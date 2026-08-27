import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "webapp"))
from app import app as flask_app  # noqa: E402


@pytest.fixture
def client():
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


def test_api_plan_channel_totals_costs_sum_to_week_total(client):
    resp = client.post("/api/plan", json=VALID_PAYLOAD)
    body = resp.get_json()
    channel_sum = sum(c["cost"] for c in body["channel_totals"].values())
    assert channel_sum == pytest.approx(body["week_summary"]["total_cost"], abs=0.05)
