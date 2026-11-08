"""Profile settings (/settings): storage independent of the plan, migration
from plan.profile_input, rebuild-on-save, and account changes."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "webapp"))
from test_webapp import VALID_PAYLOAD, client, logged_out_client  # noqa: E402,F401

import db as db_module  # noqa: E402


def _user_id():
    return db_module.get_user_by_identifier("test@example.com")["id"]


def _settings(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    return resp.get_json()


# --- reading / no-plan case ---

def test_fresh_account_has_no_settings_and_no_plan(client):
    body = _settings(client)
    assert body["settings"] is None
    assert body["goals"] is None
    assert body["has_plan"] is False
    assert body["account"] == {"email": "test@example.com", "username": "testuser"}
    assert body["taste"] == {"liked": [], "disliked": []}


def test_settings_page_requires_login(logged_out_client):
    assert logged_out_client.get("/settings").status_code == 302
    assert logged_out_client.get("/api/settings").status_code == 401


def test_settings_page_serves_html(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert b"settings" in resp.data.lower()


# --- plan builds keep settings in sync ---

def test_building_a_plan_saves_its_inputs_as_settings(client):
    client.post("/api/plan", json={**VALID_PAYLOAD, "cook_schedule": [20, 20, 20, 20, 20, 90, 0]})
    s = _settings(client)
    assert s["has_plan"] is True
    assert s["settings"]["weight_lbs"] == 170
    assert s["settings"]["goal"] == "lose_fat"
    assert s["settings"]["weekly_budget"] == 175
    assert s["settings"]["cook_schedule"] == [20, 20, 20, 20, 20, 90, 0]
    assert s["settings"]["filters"]["vegan"] is False
    assert s["goals"]["calories"] > 0


def test_migrates_settings_from_legacy_plan_profile_input(client):
    plan = client.post("/api/plan", json={**VALID_PAYLOAD, "weekly_budget": 210}).get_json()
    # Simulate an account from before profile_settings existed.
    db_module.set_blob(_user_id(), "profile_settings", None)
    plan.pop("cook_schedule")
    db_module.set_blob(_user_id(), "plan", plan)

    s = _settings(client)["settings"]
    assert s["weight_lbs"] == 170 and s["sex"] == "male"
    assert s["weekly_budget"] == 210
    assert s["cook_schedule"] == [45] * 7  # legacy cook_time "medium"
    assert db_module.get_blob(_user_id(), "profile_settings") == s  # persisted


def test_legacy_plan_without_profile_input_has_nothing_to_migrate(client):
    plan = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    db_module.set_blob(_user_id(), "profile_settings", None)
    plan.pop("profile_input")
    db_module.set_blob(_user_id(), "plan", plan)
    body = _settings(client)
    assert body["settings"] is None and body["has_plan"] is True


def test_plan_request_without_profile_fields_reads_them_from_settings(client):
    # e.g. the dashboard's "Save & rebuild week" on a plan whose
    # profile_input went missing: only the changed field is sent.
    client.post("/api/plan", json=VALID_PAYLOAD)
    resp = client.post("/api/plan", json={"cook_schedule": [0, 0, 0, 0, 0, 0, 0]})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["cook_schedule"] == [0] * 7
    assert body["profile_input"]["weight_lbs"] == 170
    assert _settings(client)["settings"]["cook_schedule"] == [0] * 7


def test_save_and_rebuild_week_flow_still_works(client):
    # Exactly what cook-schedule.js sends: planRequestBase(plan) + cook_schedule.
    plan = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    base = {**plan["profile_input"], "weekly_budget": plan["weekly_budget"],
            "cook_time": plan["cook_time"], "cook_schedule": plan["cook_schedule"],
            "filters": plan["filters"]}
    resp = client.post("/api/plan", json={**base, "cook_schedule": [90] * 7})
    assert resp.status_code == 200
    assert resp.get_json()["cook_schedule"] == [90] * 7


def test_legacy_request_fields_beat_stored_settings(client):
    client.post("/api/plan", json={**VALID_PAYLOAD, "weekly_budget": 350, "cook_schedule": [90] * 7})
    legacy = {k: v for k, v in VALID_PAYLOAD.items() if k != "weekly_budget"}
    body = client.post("/api/plan", json={**legacy, "budget": 20, "cook_time": "low"}).get_json()
    assert body["weekly_budget"] == 140
    assert body["cook_schedule"] == [20] * 7


def test_reset_keeps_settings(client):
    client.post("/api/plan", json=VALID_PAYLOAD)
    client.post("/api/plan/reset")
    body = _settings(client)
    assert body["has_plan"] is False
    assert body["settings"]["weight_lbs"] == 170


# --- saving settings ---

def test_put_without_plan_just_stores(client):
    resp = client.put("/api/settings", json={"settings": {**VALID_PAYLOAD, "weight_lbs": 160}})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["plan"] is None
    assert body["settings"]["weight_lbs"] == 160
    assert db_module.get_blob(_user_id(), "plan") is None


def test_put_weight_rebuilds_plan_with_new_targets(client):
    before = client.post("/api/plan", json=VALID_PAYLOAD).get_json()
    resp = client.put("/api/settings", json={"settings": {"weight_lbs": 220}})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["settings"]["weight_lbs"] == 220
    assert body["plan"]["goals"]["calories"] > before["goals"]["calories"]
    assert body["plan"]["profile_input"]["weight_lbs"] == 220
    assert client.get("/api/plan").get_json()["goals"] == body["plan"]["goals"]


def test_put_budget_and_filters(client):
    client.post("/api/plan", json=VALID_PAYLOAD)
    body = client.put("/api/settings", json={"settings": {
        "weekly_budget": 280, "filters": {"vegetarian": True},
    }}).get_json()
    assert body["plan"]["weekly_budget"] == 280
    assert body["plan"]["filters"] == {"vegetarian": True}
    assert body["settings"]["filters"]["vegetarian"] is True


@pytest.mark.parametrize("changes", [
    {"weight_lbs": "heavy"},
    {"weekly_budget": 0},
    {"goal": "get_huge"},
    {"cook_schedule": [10, 10]},
    {"favourite_colour": "blue"},
])
def test_put_rejects_invalid_settings_and_keeps_old_ones(client, changes):
    client.post("/api/plan", json=VALID_PAYLOAD)
    resp = client.put("/api/settings", json={"settings": changes})
    assert resp.status_code == 400
    assert _settings(client)["settings"]["weight_lbs"] == 170


def test_put_without_any_stored_settings_needs_full_profile(client):
    resp = client.put("/api/settings", json={"settings": {"weight_lbs": 160}})
    assert resp.status_code == 400


def test_undo_after_settings_rebuild_restores_old_settings(client):
    client.post("/api/plan", json=VALID_PAYLOAD)
    client.put("/api/settings", json={"settings": {"weight_lbs": 220}})
    undone = client.post("/api/plan/undo").get_json()
    assert undone["profile_input"]["weight_lbs"] == 170
    assert _settings(client)["settings"]["weight_lbs"] == 170


def test_preview_does_not_save(client):
    client.post("/api/plan", json=VALID_PAYLOAD)
    current = _settings(client)["goals"]
    resp = client.post("/api/settings/preview", json={"settings": {"weight_lbs": 220}})
    assert resp.status_code == 200
    assert resp.get_json()["goals"]["calories"] > current["calories"]
    assert _settings(client)["settings"]["weight_lbs"] == 170


# --- taste profile ---

def test_taste_remove_and_clear(client):
    client.post("/api/swipe-survey", json={"picks": [
        {"name": "Banana", "liked": True}, {"name": "Tofu", "liked": False}, {"name": "Rice", "liked": True},
    ]})
    assert _settings(client)["taste"] == {"liked": ["Banana", "Rice"], "disliked": ["Tofu"]}
    body = client.post("/api/settings/taste", json={"remove": ["Banana"]}).get_json()
    assert body["taste"] == {"liked": ["Rice"], "disliked": ["Tofu"]}
    body = client.post("/api/settings/taste", json={"clear": True}).get_json()
    assert body["taste"] == {"liked": [], "disliked": []}
    assert client.post("/api/settings/taste", json={}).status_code == 400


def test_taste_summary_lists_own_swipes_including_legacy_blob(client):
    # Pre-propagation accounts stored {name: +/-bonus} in taste_profile.
    db_module.set_blob(_user_id(), "taste_profile", {"Oatmeal": 2.0})
    client.post("/api/swipe-survey", json={"picks": [{"name": "Banana", "liked": False}]})
    # Only what the user swiped, not every catalog item the bonus propagates to.
    assert _settings(client)["taste"] == {"liked": ["Oatmeal"], "disliked": ["Banana"]}

    body = client.post("/api/settings/taste", json={"remove": ["Oatmeal"]}).get_json()
    assert body["taste"] == {"liked": [], "disliked": ["Banana"]}
    assert db_module.get_blob(_user_id(), "taste_profile") == {}


def test_later_swipe_on_same_food_wins(client):
    client.post("/api/swipe-survey", json={"picks": [{"name": "Banana", "liked": True}]})
    client.post("/api/swipe-survey", json={"picks": [{"name": "Banana", "liked": False}]})
    assert _settings(client)["taste"] == {"liked": [], "disliked": ["Banana"]}


# --- account ---

def test_password_change_requires_current_password(client):
    resp = client.post("/api/account/password", json={"current_password": "wrong", "new_password": "newpass123"})
    assert resp.status_code == 403
    resp = client.post("/api/account/password", json={"current_password": "testpass123", "new_password": "short"})
    assert resp.status_code == 400
    resp = client.post("/api/account/password", json={"current_password": "testpass123", "new_password": "newpass123"})
    assert resp.status_code == 200

    hashed = db_module.get_user_by_identifier("testuser")["password_hash"]
    assert "newpass123" not in hashed  # stored hashed, like signup
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"identifier": "testuser", "password": "testpass123"}).status_code == 401
    assert client.post("/api/auth/login", json={"identifier": "testuser", "password": "newpass123"}).status_code == 200


def test_delete_account_needs_confirmation_and_password(client):
    client.post("/api/plan", json=VALID_PAYLOAD)
    user_id = _user_id()
    assert client.delete("/api/account", json={"password": "testpass123"}).status_code == 400
    assert client.delete("/api/account", json={"password": "nope", "confirm": "DELETE"}).status_code == 403
    assert db_module.get_user_by_id(user_id) is not None

    resp = client.delete("/api/account", json={"password": "testpass123", "confirm": "DELETE"})
    assert resp.status_code == 200
    assert db_module.get_user_by_id(user_id) is None
    assert db_module.get_blob(user_id, "plan") is None
    assert db_module.get_blob(user_id, "profile_settings") is None
    assert client.get("/api/settings").status_code == 401  # session cleared
