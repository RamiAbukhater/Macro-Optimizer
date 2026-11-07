import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "webapp"))
from app import app as flask_app  # noqa: E402
from macro_optimizer.database import load_market_items  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    import db as db_module

    # Point every test at a throwaway database so tests don't read/write the
    # developer's real webapp/data/app.db or leak state between tests.
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "app.db")
    db_module.init_db()

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        # Every existing test in this file hits routes that now require
        # login, so the fixture itself signs up (and, via signup, logs in) a
        # fixed test user, keeping `client` a drop-in authenticated client.
        signup = c.post("/api/auth/signup", json={
            "email": "test@example.com", "username": "testuser", "password": "testpass123",
        })
        assert signup.status_code == 201, signup.get_json()
        yield c


@pytest.fixture
def logged_out_client(tmp_path, monkeypatch):
    """A client against the same throwaway database as `client`, but never
    signed up/logged in — for asserting protected routes actually protect."""
    import db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "app.db")
    db_module.init_db()

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


VALID_PAYLOAD = {
    "sex": "male",
    "age": 25,
    "height_ft": 5,
    "height_in": 10,
    "weight_lbs": 170,
    "activity": "moderate",
    "goal": "lose_fat",
    "budget": 25,
    "cook_time": "medium",
    "filters": {},
}


def test_onboarding_page_serves_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<html" in resp.data.lower() or b"<!doctype" in resp.data.lower()


def test_dashboard_page_serves_html(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 200


def test_api_plan_valid_payload_returns_full_shape(client):
    resp = client.post("/api/plan", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    body = resp.get_json()
    for key in ("profile", "goals", "budget", "today", "week", "week_summary", "channel_totals"):
        assert key in body
    assert len(body["week"]) == 7
    assert body["goals"]["calories"] > 0
    assert body["profile"]["goal_label"] == "Lose Fat"


def test_api_plan_today_matches_first_week_entry(client):
    resp = client.post("/api/plan", json=VALID_PAYLOAD)
    body = resp.get_json()
    assert body["today"]["date"] == body["week"][0]["date"]


def test_api_plan_week_days_are_sequential(client):
    resp = client.post("/api/plan", json=VALID_PAYLOAD)
    body = resp.get_json()
    days_out = [d["days_out"] for d in body["week"]]
    assert days_out == list(range(7))


def test_api_plan_missing_field_returns_400(client):
    bad_payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "sex"}
    resp = client.post("/api/plan", json=bad_payload)
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_api_plan_invalid_activity_returns_400(client):
    payload = {**VALID_PAYLOAD, "activity": "superhuman"}
    resp = client.post("/api/plan", json=payload)
    assert resp.status_code == 400


def test_api_plan_invalid_goal_returns_400(client):
    payload = {**VALID_PAYLOAD, "goal": "bulk"}
    resp = client.post("/api/plan", json=payload)
    assert resp.status_code == 400


def test_api_plan_negative_budget_returns_400(client):
    payload = {**VALID_PAYLOAD, "budget": -5}
    resp = client.post("/api/plan", json=payload)
    assert resp.status_code == 400


def test_api_plan_respects_dietary_filter(client):
    payload = {**VALID_PAYLOAD, "budget": 40, "filters": {"vegan": True}}
    resp = client.post("/api/plan", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    for day in body["week"]:
        for item in day["items"]:
            # Cook-at-home vegan items are already validated by the optimizer's
            # own filter tests; this just checks nothing from an illustrative
            # meat-forward restaurant/prepared-meal item leaked into a vegan plan.
            assert "Beef" not in item["name"]
            assert "Chicken" not in item["name"] or "Home Cooking" not in item["source"]


def test_api_plan_default_settings_produce_meaningfully_distinct_days(client):
    """Regression test for the "every day should be unique" bug report at the
    real HTTP layer. The exact zero-repeat guarantee is already covered
    precisely at the orchestrator level (test_orchestrator.py) with a
    deterministic item pool; the real curated database's size and pricing
    can make a *little* overlap the correct trade for feasibility on a tight
    day (the rotation backoff relaxes on purpose rather than force a day
    into best-effort, see run_optimizer_with_rotation_backoff), so this test
    checks what should hold regardless: no two days come back as an exact
    duplicate of each other, and no pair of days shares more than half of
    either day's items, both of which the old rotation_cap=3 default (three
    real day-for-day repeats before any exclusion kicked in) regularly broke."""
    payload = {**VALID_PAYLOAD, "budget": 35, "goal": "maintain"}
    resp = client.post("/api/plan", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()

    day_sets = [{item["name"] for item in day["items"]} for day in body["week"]]
    for i in range(len(day_sets)):
        for j in range(i + 1, len(day_sets)):
            assert day_sets[i] != day_sets[j], f"days {i} and {j} are exact duplicates"
            overlap = day_sets[i] & day_sets[j]
            smaller = min(len(day_sets[i]), len(day_sets[j])) or 1
            assert len(overlap) <= smaller / 2, (
                f"days {i} and {j} share {len(overlap)} of {smaller} items: {overlap}"
            )


def test_api_plan_channel_totals_costs_sum_to_week_total(client):
    resp = client.post("/api/plan", json=VALID_PAYLOAD)
    body = resp.get_json()
    channel_sum = sum(c["cost"] for c in body["channel_totals"].values())
    assert channel_sum == pytest.approx(body["week_summary"]["total_cost"], abs=0.05)


def test_api_plan_echoes_cook_time_and_filters_for_later_reuse(client):
    payload = {**VALID_PAYLOAD, "filters": {"vegan": True}}
    resp = client.post("/api/plan", json=payload)
    body = resp.get_json()
    assert body["cook_time"] == "medium"
    assert body["filters"] == {"vegan": True}


# --- new pages ---

@pytest.mark.parametrize("path", ["/menu", "/cost", "/fridge", "/recipe/Chicken%20%26%20Rice%20Bowl"])
def test_new_pages_serve_html(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert b"<html" in resp.data.lower() or b"<!doctype" in resp.data.lower()


# --- reshuffle ---

def test_reshuffle_returns_full_week_shape(client):
    resp = client.post("/api/plan/reshuffle", json={**VALID_PAYLOAD, "week_items": []})
    assert resp.status_code == 200
    body = resp.get_json()
    for key in ("profile", "goals", "budget", "today", "week", "week_summary", "channel_totals"):
        assert key in body
    assert len(body["week"]) == 7


def test_reshuffle_avoids_items_seeded_as_current(client):
    plan = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    week_items = [
        {"date": d["date"], "name": item["name"], "servings": item["servings"]}
        for d in plan["week"] for item in d["items"]
    ]
    resp = client.post("/api/plan/reshuffle", json={**VALID_PAYLOAD, "week_items": week_items})
    assert resp.status_code == 200
    # Best-effort: with a rotation cap of 1 against everything just picked, at
    # least some days should differ from the original selection.
    new_names = {item["name"] for d in resp.get_json()["week"] for item in d["items"]}
    old_names = {i["name"] for i in week_items}
    assert new_names != old_names or not old_names


# --- replace-item ---

def test_replace_item_swaps_and_keeps_other_items(client):
    plan = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    today = plan["today"]
    assert today["items"], "expected a feasible plan with at least one item to replace"
    target = today["items"][0]["name"]

    resp = client.post("/api/plan/replace-item", json={
        **VALID_PAYLOAD,
        "date": today["date"],
        "days_out": today["days_out"],
        "current_items": [{"name": i["name"], "source": i["source"], "servings": i["servings"]} for i in today["items"]],
        "replace_name": target,
    })
    assert resp.status_code == 200
    new_names = {i["name"] for i in resp.get_json()["day"]["items"]}
    assert target not in new_names


def test_replace_item_unknown_name_returns_400(client):
    plan = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    today = plan["today"]
    resp = client.post("/api/plan/replace-item", json={
        **VALID_PAYLOAD,
        "date": today["date"],
        "days_out": today["days_out"],
        "current_items": [{"name": i["name"], "source": i["source"], "servings": i["servings"]} for i in today["items"]],
        "replace_name": "Not A Real Item",
    })
    assert resp.status_code == 400


# --- recipe / product breakdown ---

def test_recipe_lookup_returns_ingredient_breakdown(client):
    resp = client.get("/api/recipe/Chicken & Rice Bowl")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["type"] == "recipe"
    assert body["ingredients"]
    assert body["per_serving"]["calories"] > 0


def test_product_lookup_returns_product_payload(client):
    resp = client.get(f"/api/recipe/{load_market_items()[0]['name']}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["type"] == "product"
    assert body["channel"] == "Market"


def test_recipe_lookup_unknown_name_returns_404(client):
    resp = client.get("/api/recipe/Not A Real Thing At All")
    assert resp.status_code == 404


# --- shopping list ---

def test_shopping_list_from_week_items(client):
    plan = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    items = [i for d in plan["week"] for i in d["items"]]
    resp = client.post("/api/shopping-list", json={"items": items})
    assert resp.status_code == 200
    body = resp.get_json()
    for key in ("ingredients", "packaged_items", "no_shopping_needed", "total_estimated_cost"):
        assert key in body


def test_shopping_list_empty_items(client):
    resp = client.post("/api/shopping-list", json={"items": []})
    assert resp.status_code == 200
    assert resp.get_json()["total_estimated_cost"] == 0.0


# --- ingredients ---

def test_ingredients_endpoint_lists_known_ingredients(client):
    resp = client.get("/api/ingredients")
    assert resp.status_code == 200
    keys = {i["key"] for i in resp.get_json()["ingredients"]}
    assert "chicken_breast_raw" in keys


# --- pantry ---

def test_pantry_starts_empty(client):
    resp = client.get("/api/pantry")
    assert resp.status_code == 200
    assert resp.get_json()["items"] == []


def test_pantry_add_then_list(client):
    resp = client.post("/api/pantry", json={"ingredient": "chicken_breast_raw", "quantity": 500, "expires_on": "2099-01-01"})
    assert resp.status_code == 200
    items = client.get("/api/pantry").get_json()["items"]
    assert len(items) == 1
    assert items[0]["ingredient"] == "chicken_breast_raw"
    assert items[0]["quantity"] == 500
    assert items[0]["days_until_expiry"] > 0


def test_pantry_add_unknown_ingredient_returns_400(client):
    resp = client.post("/api/pantry", json={"ingredient": "unobtainium", "quantity": 5})
    assert resp.status_code == 400


def test_pantry_add_negative_quantity_returns_400(client):
    resp = client.post("/api/pantry", json={"ingredient": "chicken_breast_raw", "quantity": -5})
    assert resp.status_code == 400


def test_pantry_set_quantity_overwrites(client):
    client.post("/api/pantry", json={"ingredient": "chicken_breast_raw", "quantity": 500})
    client.post("/api/pantry", json={"ingredient": "chicken_breast_raw", "quantity": 200})
    items = client.get("/api/pantry").get_json()["items"]
    assert items[0]["quantity"] == 200


def test_pantry_delete_removes_item(client):
    client.post("/api/pantry", json={"ingredient": "chicken_breast_raw", "quantity": 500})
    resp = client.delete("/api/pantry/chicken_breast_raw")
    assert resp.status_code == 200
    assert client.get("/api/pantry").get_json()["items"] == []


def test_pantry_add_custom_ingredient_with_unit_succeeds(client):
    resp = client.post("/api/pantry", json={
        "ingredient": "salmon_fillet", "quantity": 400, "unit": "g",
        "display_name": "Salmon Fillet", "source": "USDA FoodData Central",
    })
    assert resp.status_code == 200
    items = client.get("/api/pantry").get_json()["items"]
    assert items[0]["ingredient"] == "salmon_fillet"
    assert items[0]["unit"] == "g"
    assert items[0]["display_name"] == "Salmon Fillet"
    assert items[0]["custom"] is True
    assert items[0]["custom_source"] == "USDA FoodData Central"


def test_pantry_add_unknown_ingredient_without_unit_still_400(client):
    # Preserves existing behavior: a truly unknown ingredient with no unit
    # supplied has nothing to display, so it's still rejected.
    resp = client.post("/api/pantry", json={"ingredient": "unobtainium", "quantity": 5})
    assert resp.status_code == 400


def test_pantry_is_wired_into_recipe_cost_via_plan_endpoint(client):
    # Full integration smoke test: /api/plan builds its CookAtHomeAdapter off
    # the same persisted pantry the /api/pantry endpoints write to.
    client.post("/api/pantry", json={"ingredient": "chicken_breast_raw", "quantity": 5000})
    client.post("/api/pantry", json={"ingredient": "white_rice_dry", "quantity": 5000})
    client.post("/api/pantry", json={"ingredient": "broccoli_fresh", "quantity": 5000})
    client.post("/api/pantry", json={"ingredient": "olive_oil", "quantity": 5000})
    resp = client.post("/api/plan", json={**VALID_PAYLOAD, "budget": 60})
    assert resp.status_code == 200

    recipe = client.get("/api/recipe/Chicken & Rice Bowl").get_json()
    chicken = next(i for i in recipe["ingredients"] if i["ingredient"] == "chicken_breast_raw")
    assert chicken["have_quantity"] == 5000
    assert chicken["to_buy_quantity"] == 0
    assert chicken["price_if_bought"] == 0


# --- recipe instructions / source_url ---

def test_recipe_lookup_includes_instructions_and_null_source_url(client):
    resp = client.get("/api/recipe/Chicken & Rice Bowl")
    body = resp.get_json()
    assert len(body["instructions"]) > 0
    assert all(isinstance(s, str) for s in body["instructions"])
    # Hand-authored recipe, no real external source, never fabricated.
    assert body["source_url"] is None


def test_product_lookup_links_to_the_real_product_page(client):
    resp = client.get(f"/api/recipe/{load_market_items()[0]['name']}")
    assert resp.get_json()["source_url"].startswith("https://www.kroger.com/p/")


# --- plan persistence ---

def test_get_plan_before_any_plan_returns_404(client):
    resp = client.get("/api/plan")
    assert resp.status_code == 404


def test_get_plan_after_post_returns_saved_plan(client):
    posted = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    fetched = client.get("/api/plan").get_json()
    assert fetched["week"] == posted["week"]
    assert fetched["profile_input"]["sex"] == VALID_PAYLOAD["sex"]


def test_reshuffle_updates_the_persisted_plan(client):
    client.post("/api/plan", json=VALID_PAYLOAD)
    reshuffled = client.post("/api/plan/reshuffle", json={**VALID_PAYLOAD, "week_items": []}).get_json()
    fetched = client.get("/api/plan").get_json()
    assert fetched["week"] == reshuffled["week"]


def test_replace_item_updates_the_persisted_plan(client):
    plan = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    today = plan["today"]
    target = today["items"][0]["name"]
    client.post("/api/plan/replace-item", json={
        **VALID_PAYLOAD,
        "date": today["date"],
        "days_out": today["days_out"],
        "current_items": [{"name": i["name"], "source": i["source"], "servings": i["servings"]} for i in today["items"]],
        "replace_name": target,
    })
    fetched = client.get("/api/plan").get_json()
    fetched_today = next(d for d in fetched["week"] if d["date"] == today["date"])
    assert target not in {i["name"] for i in fetched_today["items"]}


def test_plan_undo_with_nothing_to_undo_returns_404(client):
    client.post("/api/plan", json=VALID_PAYLOAD)  # only one save, no prior snapshot yet
    resp = client.post("/api/plan/undo")
    assert resp.status_code == 404


def test_plan_undo_restores_previous_plan(client):
    first = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    client.post("/api/plan/reshuffle", json={**VALID_PAYLOAD, "week_items": []})
    restored = client.post("/api/plan/undo").get_json()
    assert restored["week"] == first["week"]
    assert client.get("/api/plan").get_json()["week"] == first["week"]


def test_feasible_day_has_zero_gaps(client):
    resp = client.post("/api/plan", json=VALID_PAYLOAD)
    body = resp.get_json()
    for day in body["week"]:
        assert "gaps" in day
        if day["feasible"]:
            assert all(v == 0 for v in day["gaps"].values())


# --- pick history persistence across requests ---

def _test_user_id():
    import db as db_module
    return db_module.get_user_by_identifier("testuser")["id"]


def test_plan_generation_persists_pick_history(client, tmp_path):
    import db as db_module

    client.post("/api/plan", json=VALID_PAYLOAD)
    history = db_module.get_blob(_test_user_id(), "history", default=[])
    assert len(history) > 0


def test_reshuffle_commits_new_picks_to_persisted_history(client):
    import db as db_module

    client.post("/api/plan", json=VALID_PAYLOAD)
    before = len(db_module.get_blob(_test_user_id(), "history", default=[]))
    client.post("/api/plan/reshuffle", json={**VALID_PAYLOAD, "week_items": []})
    after = len(db_module.get_blob(_test_user_id(), "history", default=[]))
    assert after > before


# --- ingredient search (external lookups mocked, no real network calls) ---

def test_ingredients_search_requires_query(client):
    resp = client.get("/api/ingredients/search")
    assert resp.status_code == 400


def test_ingredients_search_normalizes_and_merges_both_sources(client, monkeypatch):
    import app as app_module

    off_product = {
        "product_name": "Wild Salmon Fillet",
        "nutriments": {
            "energy-kcal_100g": 208, "proteins_100g": 20.0,
            "carbohydrates_100g": 0.0, "fat_100g": 13.0,
        },
    }
    usda_food = {
        "fdcId": 999, "description": "Salmon, raw", "dataType": "Foundation",
        "foodNutrients": [
            {"nutrientId": 1008, "value": 142.0}, {"nutrientId": 1003, "value": 19.8},
            {"nutrientId": 1005, "value": 0.0}, {"nutrientId": 1004, "value": 6.3},
        ],
    }
    monkeypatch.setattr(app_module, "search_products", lambda query, page_size=10: [off_product])
    monkeypatch.setattr(app_module, "usda_search_foods", lambda query, page_size=10: [usda_food])

    resp = client.get("/api/ingredients/search?q=salmon")
    assert resp.status_code == 200
    results = resp.get_json()["results"]
    sources = {r["source"] for r in results}
    assert "Open Food Facts" in sources
    assert any(s.startswith("USDA FoodData Central") for s in sources)
    salmon = next(r for r in results if r["source"] == "Open Food Facts")
    assert salmon["key"] == "wild_salmon_fillet"
    assert salmon["calories"] == 208


def test_ingredients_search_skips_bad_results_from_one_source(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "search_products", lambda query, page_size=10: [{"product_name": ""}])
    monkeypatch.setattr(app_module, "usda_search_foods", lambda query, page_size=10: [])

    resp = client.get("/api/ingredients/search?q=nothing")
    assert resp.status_code == 200
    assert resp.get_json()["results"] == []


# --- Instacart checkout ---

INSTACART_PAYLOAD = {
    "title": "AutoEat Weekly Shopping List",
    "image_url": "https://example.com/plate.jpg",
    "ingredients": [{"name": "rice", "display_text": "300 g rice", "quantity": 300, "unit": "g"}],
}


def test_instacart_without_api_key_returns_501(client, monkeypatch):
    monkeypatch.delenv("INSTACART_API_KEY", raising=False)
    resp = client.post("/api/shopping-list/instacart", json=INSTACART_PAYLOAD)
    assert resp.status_code == 501
    assert "INSTACART_API_KEY" in resp.get_json()["error"]


def test_instacart_missing_image_url_returns_400(client, monkeypatch):
    monkeypatch.setenv("INSTACART_API_KEY", "test-key")
    payload = {k: v for k, v in INSTACART_PAYLOAD.items() if k != "image_url"}
    resp = client.post("/api/shopping-list/instacart", json=payload)
    assert resp.status_code == 400


def test_instacart_empty_ingredients_returns_400(client, monkeypatch):
    monkeypatch.setenv("INSTACART_API_KEY", "test-key")
    payload = {**INSTACART_PAYLOAD, "ingredients": []}
    resp = client.post("/api/shopping-list/instacart", json=payload)
    assert resp.status_code == 400


def test_instacart_success_returns_shoppable_url(client, monkeypatch):
    import app as app_module

    monkeypatch.setenv("INSTACART_API_KEY", "test-key")

    class FakeResponse:
        ok = True
        status_code = 200

        def json(self):
            return {"products_link_url": "https://www.instacart.com/store/recipes/123"}

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr(app_module.requests, "post", fake_post)

    resp = client.post("/api/shopping-list/instacart", json=INSTACART_PAYLOAD)
    assert resp.status_code == 200
    assert resp.get_json()["shoppable_url"] == "https://www.instacart.com/store/recipes/123"
    assert captured["url"] == "https://connect.instacart.com/idp/v1/products/recipe"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["ingredients"][0]["measurements"] == [{"quantity": 300.0, "unit": "g"}]


def test_instacart_upstream_error_returns_502(client, monkeypatch):
    import app as app_module

    monkeypatch.setenv("INSTACART_API_KEY", "test-key")

    class FakeErrorResponse:
        ok = False
        status_code = 422
        text = "invalid recipe payload"

    monkeypatch.setattr(app_module.requests, "post", lambda *a, **kw: FakeErrorResponse())

    resp = client.post("/api/shopping-list/instacart", json=INSTACART_PAYLOAD)
    assert resp.status_code == 502


# --- /api/plan/adjust ---

FOOD_TAGS_PATH = Path(__file__).parent.parent / "src" / "macro_optimizer" / "data" / "food_tags.json"


def _find_feedback_target(plan):
    """First (day, tag, synonym-hit, item-name) in ``plan`` a food_tags.json
    synonym matches, so the test doesn't hardcode a specific curated item."""
    tags = json.loads(FOOD_TAGS_PATH.read_text(encoding="utf-8"))
    for day in plan["week"]:
        for tag, synonyms in tags.items():
            for item in day["items"]:
                lowered = item["name"].lower()
                hit = next((syn for syn in synonyms if syn in lowered), None)
                if hit:
                    return day, tag, hit, item["name"]
    return None, None, None, None


def test_adjust_feedback_message_returns_confirm_step(client):
    plan = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    day, tag, synonym, item_name = _find_feedback_target(plan)
    assert day is not None, "expected at least one plan item to match a food_tags.json synonym"
    weekday = date.fromisoformat(day["date"]).strftime("%A").lower()

    resp = client.post("/api/plan/adjust", json={"message": f"I'm sick of {synonym} on {weekday}"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "confirm"
    assert body["directive"]["type"] == "feedback"
    assert body["directive"]["date"] == day["date"]
    assert body["directive"]["tag"] == tag
    assert item_name in body["directive"]["matched_names"]
    assert "preview" in body and body["preview"]

    # Nothing mutated yet.
    fetched = client.get("/api/plan").get_json()
    assert fetched["week"] == plan["week"]


def test_adjust_feedback_confirmed_removes_disliked_item(client):
    plan = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    day, tag, synonym, item_name = _find_feedback_target(plan)
    assert day is not None, "expected at least one plan item to match a food_tags.json synonym"
    weekday = date.fromisoformat(day["date"]).strftime("%A").lower()
    message = f"I'm sick of {synonym} on {weekday}"

    client.post("/api/plan/adjust", json={"message": message})  # confirm step, discarded
    resp = client.post("/api/plan/adjust", json={"message": message, "confirmed": True})
    assert resp.status_code == 200
    body = resp.get_json()
    new_names = {i["name"] for i in body["day"]["items"]}
    assert item_name not in new_names

    fetched = client.get("/api/plan").get_json()
    fetched_day = next(d for d in fetched["week"] if d["date"] == day["date"])
    assert item_name not in {i["name"] for i in fetched_day["items"]}


def _find_untagged_item(plan):
    """First (day, item_name) in ``plan`` whose name matches no food_tags.json
    synonym at all, so a test can exercise the feedback path's item-match
    fallback (see app.py's _parse_adjust_directive) rather than its tag path."""
    tags = json.loads(FOOD_TAGS_PATH.read_text(encoding="utf-8"))
    all_synonyms = [syn.lower() for synonyms in tags.values() for syn in synonyms]
    for day in plan["week"]:
        for item in day["items"]:
            lowered = item["name"].lower()
            if not any(syn in lowered for syn in all_synonyms):
                return day, item["name"]
    return None, None


def test_adjust_feedback_falls_back_to_item_match_for_an_untagged_food(client):
    # Regression: "I am not feeling apples" used to hit food_tags.json's tiny
    # protein-only lexicon, find no match, and ask the user to pick from a
    # list of five proteins. Naming any real food should now resolve
    # directly against the food database instead. See NLU_ADJUSTMENTS.md's
    # "Known limitation" section.
    plan = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    day, item_name = _find_untagged_item(plan)
    assert day is not None, "expected at least one plan item with no food_tags.json synonym hit"
    weekday = date.fromisoformat(day["date"]).strftime("%A").lower()
    message = f"I am not feeling {item_name} on {weekday}"

    resp = client.post("/api/plan/adjust", json={"message": message})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "confirm"
    assert body["directive"]["type"] == "feedback"
    assert body["directive"]["match_kind"] == "item"
    assert body["directive"]["matched_names"] == [item_name]

    apply_resp = client.post("/api/plan/adjust", json={"message": message, "confirmed": True})
    assert apply_resp.status_code == 200
    new_names = {i["name"] for i in apply_resp.get_json()["day"]["items"]}
    assert item_name not in new_names


def test_adjust_deviation_round_trip_updates_achieved_and_gaps(client):
    plan = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    today = plan["today"]
    assert len(today["items"]) >= 2, "expected today's plan to have at least two items"
    planned_name, actual_name = today["items"][0]["name"], today["items"][1]["name"]
    message = f"I skipped the {planned_name} today and instead had the {actual_name}"

    confirm_resp = client.post("/api/plan/adjust", json={"message": message})
    assert confirm_resp.status_code == 200
    confirm_body = confirm_resp.get_json()
    assert confirm_body["status"] == "confirm"
    assert confirm_body["directive"]["type"] == "deviation"
    assert confirm_body["directive"]["planned_item"] == planned_name
    assert confirm_body["directive"]["actual_item"] == actual_name

    apply_resp = client.post("/api/plan/adjust", json={"message": message, "confirmed": True})
    assert apply_resp.status_code == 200
    body = apply_resp.get_json()

    day_names = {i["name"] for i in body["day"]["items"]}
    assert planned_name not in day_names
    assert actual_name in day_names
    # Achieved/gaps for the deviation day must reflect the swap, not just repeat the original plan.
    assert body["day"]["achieved"] != today["achieved"]
    assert "gaps" in body["day"]

    fetched = client.get("/api/plan").get_json()
    fetched_today = next(d for d in fetched["week"] if d["date"] == today["date"])
    assert fetched_today["achieved"] == body["day"]["achieved"]

    import db as db_module
    deviations = db_module.get_blob(_test_user_id(), "deviations", default=[])
    assert any(e["planned_item"]["name"] == planned_name and e["actual_item"]["name"] == actual_name
               for e in deviations)


def test_adjust_unparseable_message_returns_needs_clarification(client):
    client.post("/api/plan", json=VALID_PAYLOAD)
    resp = client.post("/api/plan/adjust", json={"message": "asdlkfj qwoeiru zxcv nonsense"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "needs_clarification"
    assert "question" in body and body["question"]

    # Confirming a message that never resolved a directive must not mutate anything.
    resp2 = client.post("/api/plan/adjust", json={"message": "asdlkfj qwoeiru zxcv nonsense", "confirmed": True})
    assert resp2.status_code == 200
    assert resp2.get_json()["status"] == "needs_clarification"


def test_adjust_without_a_saved_plan_returns_404(client):
    resp = client.post("/api/plan/adjust", json={"message": "I didn't like today's plan, too much chicken"})
    assert resp.status_code == 404


def test_adjust_missing_message_returns_400(client):
    client.post("/api/plan", json=VALID_PAYLOAD)
    resp = client.post("/api/plan/adjust", json={})
    assert resp.status_code == 400


# --- swipe taste survey ---


def test_api_swipe_cards_returns_a_sample_of_unique_items(client):
    resp = client.get("/api/swipe-cards")
    assert resp.status_code == 200
    cards = resp.get_json()["cards"]
    assert len(cards) > 0
    names = [c["name"] for c in cards]
    assert len(names) == len(set(names))
    for card in cards:
        for key in ("name", "source", "price", "calories", "protein", "carbs", "fat"):
            assert key in card


def test_api_swipe_cards_show_clean_display_names_but_keep_raw_names(client):
    cards = client.get("/api/swipe-cards").get_json()["cards"]
    kroger = [c for c in cards if c["source"] == "Kroger API"]
    assert kroger, "expected some Kroger catalog cards"
    assert all(c["display_name"] and "®" not in c["display_name"] and "™" not in c["display_name"] for c in kroger)
    # The raw title is still the key taste picks are recorded under.
    assert any(c["display_name"] != c["name"] for c in kroger)


def test_plan_items_carry_display_names(client):
    plan = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    items = [i for d in plan["week"] for i in d["items"]]
    assert items and all(i["display_name"] for i in items)


def test_adjust_matches_and_previews_by_display_name(client):
    plan = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    today = plan["today"]
    shown = [i for i in today["items"] if i["display_name"] != i["name"]]
    if not shown:
        pytest.skip("today's plan has no item whose display name differs from its raw title")
    item = shown[0]
    body = client.post("/api/plan/adjust", json={"message": f"I didn't like the {item['display_name']} today"}).get_json()
    assert body["status"] == "confirm"
    assert body["directive"]["matched_names"] == [item["name"]]
    assert item["display_name"] in body["preview"]
    assert item["name"] not in body["preview"]


def test_api_swipe_survey_maps_liked_and_disliked_picks_to_signed_bonus(client):
    picks = {"picks": [{"name": "Apple", "liked": True}, {"name": "Banana", "liked": False}]}
    resp = client.post("/api/swipe-survey", json=picks)
    assert resp.status_code == 200
    bonus = resp.get_json()["preference_bonus"]
    assert bonus["Apple"] > 0
    assert bonus["Banana"] < 0


def test_api_swipe_survey_rejects_non_list_picks(client):
    resp = client.post("/api/swipe-survey", json={"picks": "not-a-list"})
    assert resp.status_code == 400


def test_api_plan_accepts_preference_bonus_and_favors_the_bumped_item(client):
    # Boost something that has no pick history yet: a plan's own picks are
    # committed to history, and the rotation guard outranks a soft preference.
    candidate = next(row["name"] for row in load_market_items() if "Bagel" in row["name"])

    boosted_payload = {**VALID_PAYLOAD, "preference_bonus": {candidate: 50.0}}
    boosted = client.post("/api/plan", json=boosted_payload).get_json()
    boosted_names = {item["name"] for item in boosted["today"]["items"]}
    assert candidate in boosted_names


def test_swipe_survey_taste_profile_persists_across_requests(client):
    first = client.post("/api/swipe-survey", json={"picks": [{"name": "Apple", "liked": True}]})
    assert first.get_json()["preference_bonus"]["Apple"] > 0

    second = client.post("/api/swipe-survey", json={"picks": [{"name": "Banana", "liked": False}]})
    bonus = second.get_json()["preference_bonus"]
    # The second call's response includes both rounds' bonuses, not just this
    # call's picks, proving the profile is merged/persisted, not stateless.
    assert bonus["Apple"] > 0
    assert bonus["Banana"] < 0


# --- accounts ---


def test_signup_duplicate_email_returns_409(client):
    resp = client.post("/api/auth/signup", json={
        "email": "test@example.com", "username": "someoneelse", "password": "testpass123",
    })
    assert resp.status_code == 409


def test_signup_duplicate_username_returns_409(client):
    resp = client.post("/api/auth/signup", json={
        "email": "someoneelse@example.com", "username": "testuser", "password": "testpass123",
    })
    assert resp.status_code == 409


def test_signup_short_password_returns_400(client):
    resp = client.post("/api/auth/signup", json={
        "email": "new@example.com", "username": "newperson", "password": "short",
    })
    assert resp.status_code == 400


def test_login_wrong_password_returns_401(logged_out_client):
    logged_out_client.post("/api/auth/signup", json={
        "email": "a@example.com", "username": "auser", "password": "correctpass",
    })
    logged_out_client.post("/api/auth/logout", json={})
    resp = logged_out_client.post("/api/auth/login", json={"identifier": "auser", "password": "wrongpass"})
    assert resp.status_code == 401


def test_login_correct_password_returns_200(logged_out_client):
    logged_out_client.post("/api/auth/signup", json={
        "email": "b@example.com", "username": "buser", "password": "correctpass",
    })
    logged_out_client.post("/api/auth/logout", json={})
    resp = logged_out_client.post("/api/auth/login", json={"identifier": "buser", "password": "correctpass"})
    assert resp.status_code == 200


def test_protected_page_redirects_to_login_when_logged_out(logged_out_client):
    resp = logged_out_client.get("/dashboard")
    assert resp.status_code in (301, 302)
    assert "/login" in resp.headers["Location"]


def test_protected_api_returns_401_when_logged_out(logged_out_client):
    resp = logged_out_client.get("/api/pantry")
    assert resp.status_code == 401


def test_pantry_and_plan_isolated_between_users(client):
    # `client` is already signed up/logged in as testuser. A second client
    # against the same throwaway database, signed up as a different user,
    # must not see testuser's pantry or plan.
    client.post("/api/pantry", json={"ingredient": "chicken_breast", "quantity": 500})
    client.post("/api/plan", json=VALID_PAYLOAD)

    other = flask_app.test_client()
    other.post("/api/auth/signup", json={
        "email": "other@example.com", "username": "otheruser", "password": "otherpass123",
    })

    other_pantry = other.get("/api/pantry").get_json()
    assert other_pantry["items"] == []

    other_plan = other.get("/api/plan")
    assert other_plan.status_code == 404
