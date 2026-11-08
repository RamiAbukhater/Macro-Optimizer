"""Shopping list -> fridge -> planning, through the web app."""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "webapp"))
import app as app_module  # noqa: E402

PAYLOAD = {
    "sex": "male", "age": 25, "height_ft": 5, "height_in": 10, "weight_lbs": 170,
    "activity": "moderate", "goal": "lose_fat", "weekly_budget": 175, "cook_time": "medium", "filters": {},
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    import db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "app.db")
    db_module.init_db()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        assert c.post("/api/auth/signup", json={
            "email": "fridge@example.com", "username": "fridge", "password": "testpass123",
        }).status_code == 201
        yield c


def _pantry(client):
    return {i["ingredient"]: i for i in client.get("/api/pantry").get_json()["items"]}


def _product_line(shopping):
    lines = [l for l in shopping["packaged_items"] if l["packages"]]
    assert lines, "the plan should use at least one store product"
    return lines[0]


def test_shopping_list_is_whole_packages(client):
    client.post("/api/plan", json=PAYLOAD)
    shopping = client.get("/api/shopping-list").get_json()
    for line in shopping["packaged_items"]:
        if line["packages"]:
            assert line["packages"] * line["servings_per_package"] >= line["to_buy_servings"] - 1e-6
            assert line["leftover_servings"] < line["servings_per_package"] + 1e-6
            assert line["estimated_price"] == pytest.approx(line["packages"] * line["package_price"], abs=0.01)
    assert shopping["total_estimated_cost"] >= shopping["total_eaten_cost"] - 0.05
    assert shopping["week_start"].startswith(date.today().isoformat())


def test_checking_a_line_adds_the_package_once(client):
    client.post("/api/plan", json=PAYLOAD)
    line = _product_line(client.get("/api/shopping-list").get_json())
    key, stock_key = line["key"], f"upc:{line['upc']}"
    full = line["packages"] * line["servings_per_package"]

    first = client.post("/api/shopping-list/bought", json={"key": key, "bought": True}).get_json()
    assert first["changed"] == 1
    assert _pantry(client)[stock_key]["quantity"] == pytest.approx(full)

    again = client.post("/api/shopping-list/bought", json={"key": key, "bought": True}).get_json()
    assert again["changed"] == 0  # a refresh or double click adds nothing
    assert _pantry(client)[stock_key]["quantity"] == pytest.approx(full)
    shopping = client.get("/api/shopping-list").get_json()
    still = next(l for l in shopping["packaged_items"] if l["key"] == key)
    assert still["bought"] is True and still["packages"] == line["packages"]

    client.post("/api/shopping-list/bought", json={"key": key, "bought": False})
    assert stock_key not in _pantry(client)


def test_bought_item_gets_a_use_by_and_shows_as_from_shopping(client):
    client.post("/api/plan", json=PAYLOAD)
    line = _product_line(client.get("/api/shopping-list").get_json())
    client.post("/api/shopping-list/bought", json={"key": line["key"], "bought": True})
    item = _pantry(client)[f"upc:{line['upc']}"]
    assert item["from_shopping"] and item["purchased_on"] == date.today().isoformat()
    assert item["days_until_expiry"] > 0 and item["unit"] == "servings"
    assert "Store product" not in item["display_name"]  # named live from the catalog


def test_mark_all_bought(client):
    client.post("/api/plan", json=PAYLOAD)
    out = client.post("/api/shopping-list/bought", json={"all": True, "bought": True}).get_json()
    lines = out["ingredients"] + out["packaged_items"]
    assert out["changed"] >= 1
    assert all(l["bought"] for l in lines if l["packages"] or l["estimated_price"])
    assert client.post("/api/shopping-list/bought", json={"all": True}).get_json()["changed"] == 0


def test_stock_counts_down_as_days_pass(client, monkeypatch):
    plan = client.post("/api/plan", json=PAYLOAD).get_json()
    line = _product_line(client.get("/api/shopping-list").get_json())
    client.post("/api/shopping-list/bought", json={"key": line["key"], "bought": True})
    stock_key = f"upc:{line['upc']}"
    before = _pantry(client)[stock_key]["quantity"]
    eaten = sum(i["servings"] for d in plan["week"][:2] for i in d["items"] if i.get("upc") == line["upc"])

    monkeypatch.setattr(app_module, "today", lambda: date.today() + timedelta(days=2))
    after = _pantry(client).get(stock_key, {"quantity": 0})["quantity"]
    assert after == pytest.approx(max(0, before - eaten))
    # Settling is once per day: reloading doesn't eat the same days again.
    assert _pantry(client).get(stock_key, {"quantity": 0})["quantity"] == pytest.approx(after)


def test_regenerating_uses_what_is_in_the_fridge(client):
    client.post("/api/plan", json=PAYLOAD)
    client.post("/api/shopping-list/bought", json={"all": True, "bought": True})
    plan = client.post("/api/plan", json=PAYLOAD).get_json()
    from_fridge = [i for d in plan["week"] for i in d["items"] if i["from_stock"] > 0]
    assert from_fridge, "a re-plan right after shopping should eat what was bought"
    # And with the week's food already bought, the new week needs little shopping.
    shopping = client.get("/api/shopping-list").get_json()
    assert sum(l["have_servings"] for l in shopping["packaged_items"]) > 0
