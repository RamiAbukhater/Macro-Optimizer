"""Flask front end for macro_optimizer.

Five pages, all thin layers over the macro_optimizer package: an onboarding
form, an overview dashboard, a weekly menu (with per-item replace and a
full-week reshuffle), a cost analysis / shopping list page, a fridge
inventory page, and a recipe/product breakdown page. No planning,
optimization, or pantry logic lives here that doesn't already exist in the
package, this file's job is turning HTTP requests into calls against it and
turning the results into JSON the frontend can render.
"""

import os
import re
import sqlite3
from datetime import date, timedelta
from typing import Optional

import requests
from flask import Flask, g, jsonify, redirect, request, send_from_directory, session

import auth
import db
from macro_optimizer import (
    CookAtHomeAdapter,
    DayPlan,
    OpenFoodFactsError,
    Pantry,
    PickHistory,
    PreparedMealAdapter,
    Profile,
    RestaurantOrderAdapter,
    UsdaFdcError,
    build_shopping_list,
    compute_goals,
    load_food_db,
    load_ingredient_db,
    load_recipes,
    plan_week_multichannel,
    price_recipe,
    run_optimizer,
    search_products,
    to_food_item,
    usda_search_foods,
    usda_to_food_item,
)
from macro_optimizer.database import load_market_items
from macro_optimizer.nlu import classify_intent, extract_item_match, extract_tag, load_food_tags, resolve_day
from macro_optimizer.adjustments import exclude_and_resolve, resolve_tag_to_item_names
from macro_optimizer.deviation import MACRO_KEYS, DeviationLog, carry_forward_goals, recompute_day_macros
from macro_optimizer.tags import build_item_tags

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
auth.load_secret_key(app)
db.init_db()
auth.register_auth(app)

# Blob kinds stored per user in db.py's user_blobs table (pantry, plan,
# plan_previous, history, deviations, taste_profile all live there now,
# keyed by g.user["id"] instead of the flat per-kind JSON files this app
# used before accounts existed).

# Dollar-equivalent nudge applied per swiped item (see optimizer.py's
# preference_bonus: a discount on liked items' effective cost, a matching
# penalty on disliked ones). Small relative to typical per-item prices so it
# steers ties toward taste without overriding the macro/budget optimum.
SWIPE_BONUS_AMOUNT = 2.0
SWIPE_CARD_COUNT = 24

# Looser than rotation_cap=1's exact-name cap on purpose: a whole tag like
# "vegetable" covers many distinct items, so capping it at 1 would mean the
# entire category sits out for the rest of the week after a single pick.
# 3 still stops "beef burger" Monday, "beef brisket" Wednesday, "beef
# meatball" Friday from reading as three different choices, without
# collapsing a 7-day window down to one appearance per category.
TAG_ROTATION_CAP = 3

INSTACART_API_KEY_ENV = "INSTACART_API_KEY"
INSTACART_BASE_URL = os.environ.get("INSTACART_BASE_URL", "https://connect.instacart.com")

COOK_TIME_MINUTES = {
    "low": 20,
    "medium": 45,
    "high": 90,
}

GOAL_LABELS = {
    "lose_fat": "Lose Fat",
    "maintain": "Maintain",
    "gain_muscle": "Gain Muscle",
}

ACTIVITY_LABELS = {
    "sedentary": "Sedentary (little or no exercise)",
    "light": "Lightly active (1-3 days/week)",
    "moderate": "Moderately active (3-5 days/week)",
    "active": "Very active (6-7 days/week)",
    "very_active": "Extremely active (hard training or physical job)",
}

# Prefix match against FoodItem.source, so every item lands in one of the
# three channels regardless of which specific market/recipe/vendor it came from.
CHANNEL_SOURCE_PREFIXES = [
    ("Prepared Meal Delivery", "Prepared Meal Delivery"),
    ("Restaurant Order", "Restaurant Order"),
]


def classify_channel(source: str) -> str:
    for prefix, channel in CHANNEL_SOURCE_PREFIXES:
        if source.startswith(prefix):
            return channel
    return "Cook at Home"  # Market, Dining Hall, Home Cooking all fall here


def get_pantry() -> Pantry:
    return Pantry(entries=db.get_blob(g.user["id"], "pantry", default={}))


def save_pantry(pantry: Pantry) -> None:
    db.set_blob(g.user["id"], "pantry", pantry.to_dict())


def get_history() -> PickHistory:
    return PickHistory(entries=db.get_blob(g.user["id"], "history", default=[]))


def save_history(history: PickHistory) -> None:
    db.set_blob(g.user["id"], "history", history.entries())


def get_deviation_log() -> DeviationLog:
    return DeviationLog(entries=db.get_blob(g.user["id"], "deviations", default=[]))


def save_deviation_log(log: DeviationLog) -> None:
    db.set_blob(g.user["id"], "deviations", log.entries())


def get_taste_profile() -> dict:
    return db.get_blob(g.user["id"], "taste_profile", default={})


def build_channels():
    return [CookAtHomeAdapter(pantry=get_pantry()), PreparedMealAdapter(), RestaurantOrderAdapter()]


def build_catalog_item_tags(channels) -> dict:
    """{item_name: tag} across every channel's candidates, for the tag-aware rotation guard.

    Built from the same candidate pool plan_week_multichannel draws each
    day's food_df from, so it's a superset of whatever names any single day
    actually needs, extra entries for channels not viable that day are
    harmless, the exclusion filter only ever touches names present in that
    day's own food_df.
    """
    candidates = []
    for c in channels:
        candidates.extend(c.candidates())
    food_df = load_food_db(extra_items=candidates, include_market=False)
    return build_item_tags(food_df, load_food_tags())


PROFILE_INPUT_KEYS = ("sex", "age", "height_ft", "height_in", "weight_lbs", "activity", "goal")


def load_plan() -> Optional[dict]:
    return db.get_blob(g.user["id"], "plan")


def save_plan(payload: dict) -> None:
    """Persist the latest full week plan, keeping one prior snapshot for /api/plan/undo."""
    existing = db.get_blob(g.user["id"], "plan")
    if existing is not None:
        db.set_blob(g.user["id"], "plan_previous", existing)
    db.set_blob(g.user["id"], "plan", payload)


def recompute_week_aggregates(plan: dict) -> None:
    """Mirror of common.js's recomputeAggregates(): rebuild channel_totals/week_summary/today
    in-place after one day's items changed server-side (replace-item only touches one day)."""
    channel_totals = {}
    total_cost = 0.0
    infeasible_days = []
    for day in plan["week"]:
        if not day["feasible"]:
            infeasible_days.append(day["date"])
        total_cost += day["achieved"]["cost"]
        for item in day["items"]:
            bucket = channel_totals.setdefault(item["channel"], {"cost": 0.0, "items": 0})
            bucket["cost"] += item["total_cost"]
            bucket["items"] += 1
    for bucket in channel_totals.values():
        bucket["cost"] = round(bucket["cost"], 2)

    plan["channel_totals"] = channel_totals
    plan["week_summary"] = {
        "total_cost": round(total_cost, 2),
        "avg_daily_cost": round(total_cost / len(plan["week"]), 2),
        "days_planned": len(plan["week"]),
        "infeasible_days": infeasible_days,
    }
    plan["today"] = plan["week"][0]


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "item"


class PlanInputError(ValueError):
    pass


def parse_plan_request(data: dict):
    """Shared parsing for every endpoint that takes a profile + budget/cook_time/filters payload.

    Returns (profile, macro_goals, budget, cook_time, filters). Raises
    PlanInputError with a human-readable message on anything invalid.
    """
    try:
        profile = Profile(
            sex=data["sex"],
            age_years=float(data["age"]),
            height_inches=float(data["height_ft"]) * 12 + float(data["height_in"]),
            weight_lbs=float(data["weight_lbs"]),
            activity=data["activity"],
            goal=data["goal"],
        )
        budget = float(data.get("budget", 25.0))
        if budget <= 0:
            raise ValueError("budget must be positive")
        cook_time = data.get("cook_time", "medium")
        if cook_time not in COOK_TIME_MINUTES:
            raise ValueError(f"cook_time must be one of {list(COOK_TIME_MINUTES)}")
        filters = {
            k: bool(v) for k, v in (data.get("filters") or {}).items()
            if k in ("vegetarian", "vegan", "halal", "gluten_free", "no_pork")
        }
    except (KeyError, TypeError, ValueError) as e:
        raise PlanInputError(f"invalid profile input: {e}") from e

    goals = compute_goals(profile)
    macro_goals = {k: goals[k] for k in ("calories", "protein", "carbs", "fat")}
    return profile, goals, macro_goals, budget, cook_time, filters


def serialize_day(r: dict) -> dict:
    """One plan_week_multichannel() result dict -> the day-shaped JSON the frontend renders."""
    plan_df = r["plan_df"]
    items = []
    if not plan_df.empty:
        for _, row in plan_df.iterrows():
            channel = classify_channel(row["source"])
            items.append({
                "name": row["name"],
                "source": row["source"],
                "channel": channel,
                "servings": int(row["servings"]),
                "price": round(float(row["price"]), 2),
                "total_cost": round(float(row["total_cost"]), 2),
                "calories": round(float(row["total_cal"]), 1),
                "protein": round(float(row["total_prot"]), 1),
                "carbs": round(float(row["total_carb"]), 1),
                "fat": round(float(row["total_fat"]), 1),
            })

    achieved = r["summary"]["achieved"]
    gaps = r["summary"].get("gaps") or {}
    return {
        "date": r["day"].isoformat(),
        "days_out": r["days_out"],
        "feasible": r["summary"]["feasible"],
        "viable_channels": r["viable_channels"],
        "items": items,
        "achieved": {
            "cost": round(float(achieved["cost"]), 2),
            "calories": round(float(achieved["calories"]), 1),
            "protein": round(float(achieved["protein"]), 1),
            "carbs": round(float(achieved["carbs"]), 1),
            "fat": round(float(achieved["fat"]), 1),
        },
        # Only nonzero when infeasible (feasible=false): how far short of each
        # macro floor the best-effort soft-constraint fallback landed, so the
        # frontend can say *why* a swap or plan came back best-effort instead
        # of just flagging that it did. All zero when feasible=true.
        "gaps": {
            "calories": round(float(gaps.get("calories", 0)), 1),
            "protein": round(float(gaps.get("protein", 0)), 1),
            "carbs": round(float(gaps.get("carbs", 0)), 1),
            "fat": round(float(gaps.get("fat", 0)), 1),
        },
    }


def serialize_week(results: list, profile: Profile, goals: dict, macro_goals: dict, budget: float,
                    cook_time: str, filters: dict) -> dict:
    week_payload = [serialize_day(r) for r in results]

    # Accumulated from the raw (unrounded) plan_df rows, not the already-rounded
    # per-item costs in week_payload, so channel totals don't drift from
    # week_summary.total_cost through compounded per-item rounding.
    channel_totals = {}
    for r in results:
        plan_df = r["plan_df"]
        if plan_df.empty:
            continue
        for _, row in plan_df.iterrows():
            channel = classify_channel(row["source"])
            bucket = channel_totals.setdefault(channel, {"cost": 0.0, "items": 0})
            bucket["cost"] += float(row["total_cost"])
            bucket["items"] += 1
    for bucket in channel_totals.values():
        bucket["cost"] = round(bucket["cost"], 2)

    total_cost = sum(d["achieved"]["cost"] for d in week_payload)
    infeasible_days = [d["date"] for d in week_payload if not d["feasible"]]

    return {
        "profile": {
            "bmr": goals["bmr"],
            "tdee": goals["tdee"],
            "goal": profile.goal,
            "goal_label": GOAL_LABELS[profile.goal],
            "activity_label": ACTIVITY_LABELS[profile.activity],
        },
        "goals": macro_goals,
        "budget": budget,
        "cook_time": cook_time,
        "filters": filters,
        "today": week_payload[0],
        "week": week_payload,
        "week_summary": {
            "total_cost": round(total_cost, 2),
            "avg_daily_cost": round(total_cost / len(week_payload), 2),
            "days_planned": len(week_payload),
            "infeasible_days": infeasible_days,
        },
        "channel_totals": channel_totals,
    }


MIN_PASSWORD_LENGTH = 8


@app.route("/login")
def login_page():
    return send_from_directory(app.static_folder, "login.html")


@app.route("/signup")
def signup_page():
    return send_from_directory(app.static_folder, "signup.html")


@app.route("/api/auth/signup", methods=["POST"])
def api_auth_signup():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not email or not username:
        return jsonify({"error": "email and username are required"}), 400
    if len(password) < MIN_PASSWORD_LENGTH:
        return jsonify({"error": f"password must be at least {MIN_PASSWORD_LENGTH} characters"}), 400
    if db.get_user_by_identifier(email) is not None:
        return jsonify({"error": "an account with that email already exists"}), 409
    if db.get_user_by_identifier(username) is not None:
        return jsonify({"error": "that username is already taken"}), 409

    try:
        user_id = db.create_user(email, username, auth.hash_password(password))
    except sqlite3.IntegrityError:
        return jsonify({"error": "an account with that email or username already exists"}), 409

    session.clear()
    session["user_id"] = user_id
    session.permanent = True
    return jsonify({"ok": True}), 201


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or "").strip()
    password = data.get("password") or ""

    user = db.get_user_by_identifier(identifier) if identifier else None
    if user is None or not auth.verify_password(password, user["password_hash"]):
        return jsonify({"error": "incorrect email/username or password"}), 401

    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    return jsonify({"ok": True})


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/")
@auth.login_required
def onboarding_page():
    if db.get_blob(g.user["id"], "plan") is not None:
        return redirect("/dashboard")
    return send_from_directory(app.static_folder, "onboarding.html")


@app.route("/dashboard")
@auth.login_required
def dashboard_page():
    return send_from_directory(app.static_folder, "dashboard.html")


@app.route("/menu")
@auth.login_required
def menu_page():
    return send_from_directory(app.static_folder, "menu.html")


@app.route("/cost")
@auth.login_required
def cost_page():
    return send_from_directory(app.static_folder, "cost.html")


@app.route("/fridge")
@auth.login_required
def fridge_page():
    return send_from_directory(app.static_folder, "fridge.html")


@app.route("/recipe/<path:name>")
@auth.login_required
def recipe_page(name):
    return send_from_directory(app.static_folder, "recipe.html")


@app.route("/api/plan/reset", methods=["POST"])
@auth.login_required
def api_plan_reset():
    """Clears the saved plan (and its undo snapshot) so `/` serves onboarding
    again instead of redirecting straight to the dashboard. Backs the
    sidebar's "Start over" action."""
    db.set_blob(g.user["id"], "plan", None)
    db.set_blob(g.user["id"], "plan_previous", None)
    return jsonify({"ok": True})


@app.route("/api/swipe-cards")
@auth.login_required
def api_swipe_cards():
    """A sample of catalog items for the cold-start taste swipe survey.

    Onboarding-time only, before there's any pick history to learn from
    (see IDEAS.md's "Learning preferences from history"). Plain sampling, not
    curated, since the point is just a quick read on taste, not a full menu.
    """
    food_df = load_food_db()
    names_seen = set()
    rows = []
    for _, row in food_df.sample(frac=1).iterrows():
        if row["name"] in names_seen:
            continue
        names_seen.add(row["name"])
        rows.append(row)
        if len(rows) >= SWIPE_CARD_COUNT:
            break

    cards = [
        {
            "name": row["name"],
            "category": row["category"],
            "source": row["source"],
            "price": round(float(row["price"]), 2),
            "calories": round(float(row["calories"]), 1),
            "protein": round(float(row["protein"]), 1),
            "carbs": round(float(row["carbs"]), 1),
            "fat": round(float(row["fat"]), 1),
        }
        for row in rows
    ]
    return jsonify({"cards": cards})


@app.route("/api/swipe-survey", methods=["POST"])
@auth.login_required
def api_swipe_survey():
    data = request.get_json(silent=True) or {}
    picks = data.get("picks")
    if not isinstance(picks, list):
        return jsonify({"error": "picks must be a list of {name, liked}"}), 400

    # Merge into the persisted profile (last-write-wins per item name) rather
    # than returning a stateless computed dict, so taste carries across
    # sessions instead of only applying to the one /api/plan call the client
    # happens to pass this response into.
    persisted = get_taste_profile()
    for pick in picks:
        if not isinstance(pick, dict) or "name" not in pick or "liked" not in pick:
            continue
        name = str(pick["name"])
        persisted[name] = SWIPE_BONUS_AMOUNT if pick["liked"] else -SWIPE_BONUS_AMOUNT
    db.set_blob(g.user["id"], "taste_profile", persisted)

    return jsonify({"preference_bonus": persisted})


@app.route("/api/plan", methods=["POST"])
@auth.login_required
def api_plan():
    data = request.get_json(silent=True) or {}

    try:
        profile, goals, macro_goals, budget, cook_time, filters = parse_plan_request(data)
        preference_bonus = data.get("preference_bonus")
        if preference_bonus is not None:
            if not isinstance(preference_bonus, dict):
                raise ValueError("preference_bonus must be an object")
            preference_bonus = {str(k): float(v) for k, v in preference_bonus.items()}
    except (PlanInputError, ValueError) as e:
        return jsonify({"error": str(e)}), 400

    # The user's persisted taste profile always applies; anything explicitly
    # passed in this request (e.g. a swipe survey run this onboarding pass)
    # merges on top of it, per-item values in the request winning.
    combined_bonus = {**get_taste_profile(), **(preference_bonus or {})}

    today = date.today()
    free_minutes = COOK_TIME_MINUTES[cook_time]
    days = [DayPlan(on=today + timedelta(days=i), free_minutes=free_minutes) for i in range(7)]
    # A real, persisted PickHistory, fed every time a plan is generated, not
    # just at reshuffle time (see backend_support.md's "Plan/session
    # persistence" note), so the anti-repetition guard has real cross-request
    # memory.
    history = get_history()

    try:
        # rotation_cap=1 means no item repeats anywhere in the 7-day window,
        # every day genuinely unique, not just "not identical to yesterday."
        # Safe to ask for outright: plan_week_multichannel relaxes this cap
        # on a per-day basis (see run_optimizer_with_rotation_backoff) only
        # if the strict cap would otherwise sacrifice that day's macro goals,
        # so this never trades nutrition for variety, only asks for as much
        # variety as the database and budget can actually support.
        channels = build_channels()
        results = plan_week_multichannel(
            days, channels, macro_goals, budget, today=today,
            filters=filters or None, history=history, rotation_cap=1, recency_penalty_weight=5.0,
            tag_rotation_cap=TAG_ROTATION_CAP, item_tags=build_catalog_item_tags(channels),
            preference_bonus=combined_bonus or None, verbose=False,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    save_history(history)
    payload = serialize_week(results, profile, goals, macro_goals, budget, cook_time, filters)
    payload["profile_input"] = {k: data.get(k) for k in PROFILE_INPUT_KEYS}
    save_plan(payload)
    return jsonify(payload)


@app.route("/api/plan", methods=["GET"])
@auth.login_required
def api_plan_get():
    """The last plan any plan-producing endpoint saved, for a fresh tab or a
    reload after sessionStorage was cleared, so the frontend doesn't have to
    redirect back to onboarding just because its client-side copy is gone."""
    payload = load_plan()
    if payload is None:
        return jsonify({"error": "no plan saved yet"}), 404
    return jsonify(payload)


@app.route("/api/plan/undo", methods=["POST"])
@auth.login_required
def api_plan_undo():
    """Restore the plan snapshot saved right before the most recent reshuffle
    or replace-item. Single level, calling it twice in a row undoes the undo."""
    previous = db.get_blob(g.user["id"], "plan_previous")
    if previous is None:
        return jsonify({"error": "nothing to undo"}), 404
    current = db.get_blob(g.user["id"], "plan")
    db.set_blob(g.user["id"], "plan", previous)
    db.set_blob(g.user["id"], "plan_previous", current)
    return jsonify(previous)


@app.route("/api/plan/reshuffle", methods=["POST"])
@auth.login_required
def api_plan_reshuffle():
    """Rebuild the whole week, steering hard away from whatever's currently shown.

    Reuses the existing rotation-cap/recency-penalty machinery (history.py):
    every item in ``week_items`` is seeded into a fresh PickHistory as if
    picked today, then a rotation cap of 1 excludes anything already seen
    outright wherever a feasible alternative exists, run_optimizer's own
    soft-constraint fallback covers days where it doesn't.
    """
    data = request.get_json(silent=True) or {}

    try:
        profile, goals, macro_goals, budget, cook_time, filters = parse_plan_request(data)
    except PlanInputError as e:
        return jsonify({"error": str(e)}), 400

    week_items = data.get("week_items") or []

    today = date.today()
    free_minutes = COOK_TIME_MINUTES[cook_time]
    days = [DayPlan(on=today + timedelta(days=i), free_minutes=free_minutes) for i in range(7)]

    # Steer hard away from what's currently on screen, layered on top of the
    # real persisted history so a reshuffle also avoids genuinely-recent picks,
    # not just this week's. Seeded into an in-memory-only copy (see
    # PickHistory(entries=...)) so items excluded here, which aren't
    # necessarily eaten, don't get written into the real persisted log; the
    # persisted log is instead updated below with whatever this reshuffle
    # actually lands on.
    persisted_history = get_history()
    history = PickHistory(entries=persisted_history.entries())
    for it in week_items:
        try:
            history.add_pick(it["name"], servings=int(it.get("servings", 1)), on=date.fromisoformat(it["date"]))
        except (KeyError, ValueError):
            continue

    try:
        channels = build_channels()
        results = plan_week_multichannel(
            days, channels, macro_goals, budget, today=today,
            filters=filters or None, history=history, rotation_cap=1, recency_penalty_weight=5.0,
            tag_rotation_cap=TAG_ROTATION_CAP, item_tags=build_catalog_item_tags(channels),
            preference_bonus=get_taste_profile() or None, verbose=False,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # The reshuffled week is now the real plan; commit it to the persisted
    # history so future plans/reshuffles see it too.
    for r in results:
        persisted_history.record_plan(r["plan_df"], on=r["day"])
    save_history(persisted_history)

    payload = serialize_week(results, profile, goals, macro_goals, budget, cook_time, filters)
    payload["profile_input"] = {k: data.get(k) for k in PROFILE_INPUT_KEYS}
    save_plan(payload)
    return jsonify(payload)


@app.route("/api/plan/replace-item", methods=["POST"])
@auth.login_required
def api_plan_replace_item():
    """Swap one item out of a single day's plan for the next-best alternative.

    Every other currently-picked item on that day is pinned (forced to stay
    in, at least one serving), the item being replaced is excluded from the
    candidate pool entirely, and the optimizer re-solves just that day to
    fill the gap. One-click, no picking through alternatives.
    """
    data = request.get_json(silent=True) or {}

    try:
        profile, goals, macro_goals, budget, cook_time, filters = parse_plan_request(data)
    except PlanInputError as e:
        return jsonify({"error": str(e)}), 400

    try:
        day_on = date.fromisoformat(data["date"])
        days_out = int(data["days_out"])
        replace_name = data["replace_name"]
        current_items = data.get("current_items") or []
    except (KeyError, ValueError) as e:
        return jsonify({"error": f"invalid replace-item request: {e}"}), 400

    if replace_name not in {i.get("name") for i in current_items}:
        return jsonify({"error": f"{replace_name!r} is not one of this day's current items"}), 400

    keep_items = [i["name"] for i in current_items if i.get("name") != replace_name]

    channels = build_channels()
    viable = [c for c in channels if days_out >= c.lead_time_days]
    if not viable:
        return jsonify({"error": f"no channel is viable {days_out} days out"}), 400

    candidates = []
    for c in viable:
        candidates.extend(c.candidates())
    food_df = load_food_db(extra_items=candidates, include_market=False)
    food_df = food_df[food_df["name"] != replace_name].reset_index(drop=True)

    missing_keep = [n for n in keep_items if n not in food_df["name"].values]
    if missing_keep:
        return jsonify({"error": f"can't keep item(s) not available today: {missing_keep}"}), 400

    free_minutes = COOK_TIME_MINUTES[cook_time]
    history = get_history()
    try:
        plan_df, summary = run_optimizer(
            food_df, macro_goals, budget, filters=filters or None,
            time_budget_minutes=free_minutes, pinned_items=keep_items,
            history=history, recency_penalty_weight=2.0, as_of=day_on,
            preference_bonus=get_taste_profile() or None, verbose=False,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    history.record_plan(plan_df, on=day_on)
    save_history(history)

    day_result = {
        "day": day_on, "days_out": days_out,
        "viable_channels": [c.name for c in viable],
        "plan_df": plan_df, "summary": summary,
    }
    serialized_day = serialize_day(day_result)

    # Keep the persisted plan in sync so GET /api/plan and later
    # reshuffles/replacements see this swap too, not just this response.
    persisted_plan = load_plan()
    if persisted_plan is not None:
        for i, d in enumerate(persisted_plan["week"]):
            if d["date"] == serialized_day["date"]:
                persisted_plan["week"][i] = serialized_day
                break
        recompute_week_aggregates(persisted_plan)
        save_plan(persisted_plan)

    return jsonify({"day": serialized_day})


def _food_df_for_day(days_out: int):
    """Same candidate-pool construction /api/plan/replace-item uses: every
    viable channel's items for a day this many days out, no market/dining noise."""
    channels = build_channels()
    viable = [c for c in channels if days_out >= c.lead_time_days]
    candidates = []
    for c in viable:
        candidates.extend(c.candidates())
    food_df = load_food_db(extra_items=candidates, include_market=False)
    return food_df, viable


def _weekday_date_label(on: date) -> str:
    return f"{on.strftime('%A')} ({on.isoformat()})"


class AdjustNeedsClarification(Exception):
    def __init__(self, question: str, options: list):
        self.question = question
        self.options = options


def _parse_adjust_directive(message: str, plan: dict) -> dict:
    """Turn a free-text message into a structured directive against ``plan``.

    Raises AdjustNeedsClarification when any required slot can't be
    confidently resolved, per NLU_ADJUSTMENTS.md's no-guessing rule: a wrong
    guess (crediting the wrong day, dropping the wrong item) is worse than
    asking.
    """
    intent = classify_intent(message)
    if intent is None:
        raise AdjustNeedsClarification(
            "I couldn't tell whether that's feedback on the plan or something "
            "you ate instead of what was planned. Try starting with \"I didn't "
            "like...\" or \"I ended up having...\".",
            [],
        )

    plan_dates = [date.fromisoformat(d["date"]) for d in plan["week"]]
    on = resolve_day(message, plan_dates)
    if on is None:
        # Options are weekday-name labels, not bare ISO dates, because
        # resolve_day only ever recognizes a weekday name or a relative word
        # (today/tomorrow/yesterday) in free text, never a raw "2026-08-28".
        # The frontend resends the original message with this label appended
        # (see adjust.js), so whatever this string is has to be something
        # resolve_day can actually parse back out on the retry.
        raise AdjustNeedsClarification("Which day is this about?", [_weekday_date_label(d) for d in plan_dates])
    day_json = next(d for d in plan["week"] if d["date"] == on.isoformat())

    if intent == "feedback":
        tags = load_food_tags()
        food_df, _viable = _food_df_for_day(day_json["days_out"])
        tag = extract_tag(message, tags)
        if tag is not None:
            matched_names = resolve_tag_to_item_names(food_df, tag)
            return {
                "type": "feedback",
                "date": on.isoformat(),
                "days_out": day_json["days_out"],
                "match_kind": "tag",
                "tag": tag,
                "matched_names": matched_names,
            }

        # food_tags.json is a hand-curated lexicon, it will never cover every
        # named food (see NLU_ADJUSTMENTS.md's "Known limitation"). Before
        # asking the user to clarify, fall back to fuzzy-matching the message
        # against real item names, same technique the deviation path below
        # already relies on, first against what's actually on this day's
        # plan, then against the day's full candidate catalog so a dislike
        # can name any food, not just one already plated.
        day_item_names = [i["name"] for i in day_json["items"]]
        item_match = extract_item_match(message, day_item_names)
        if item_match is None:
            item_match = extract_item_match(message, food_df["name"].tolist())
        if item_match is None:
            raise AdjustNeedsClarification(
                "What food would you like to avoid? You can name a specific "
                "item, or a category like " + ", ".join(sorted(tags.keys())) + ".",
                sorted(tags.keys()),
            )
        return {
            "type": "feedback",
            "date": on.isoformat(),
            "days_out": day_json["days_out"],
            "match_kind": "item",
            "tag": item_match,
            "matched_names": [item_match],
        }

    # intent == "deviation"
    day_item_names = [i["name"] for i in day_json["items"]]
    planned_item = extract_item_match(message, day_item_names)
    if planned_item is None:
        raise AdjustNeedsClarification(
            f"Which item from {_weekday_date_label(on)}'s plan did you skip?", day_item_names,
        )
    # Excludes the planned item's own name from the search space so a
    # message that mentions both items doesn't have the planned item
    # winning its own fuzzy match as "what was actually eaten."
    food_df, _viable = _food_df_for_day(day_json["days_out"])
    actual_candidates = [n for n in food_df["name"].tolist() if n != planned_item]
    actual_item = extract_item_match(message, actual_candidates)
    if actual_item is None:
        raise AdjustNeedsClarification(
            "I couldn't confidently match what you actually ate against the food "
            "database. Can you name it more specifically?",
            [],
        )
    return {
        "type": "deviation",
        "date": on.isoformat(),
        "days_out": day_json["days_out"],
        "planned_item": planned_item,
        "actual_item": actual_item,
    }


def _adjust_preview(directive: dict, plan: dict) -> str:
    on = date.fromisoformat(directive["date"])
    label = _weekday_date_label(on)

    if directive["type"] == "feedback":
        matched = directive["matched_names"]
        if not matched:
            return f"Nothing on {label} matches '{directive['tag']}', so this won't change that day's plan."
        if directive.get("match_kind") == "item":
            return f"On {label}, I'll drop {', '.join(matched)} from the plan and re-solve that day."

        # matched_names is every catalog item the tag covers, not just what's
        # plated today, resolve_tag_to_item_names has to return the whole set
        # so exclude_and_resolve keeps the re-solve from picking a same-tag
        # replacement, but showing all of it as "I'll drop X, Y, Z..." reads
        # like a dozen items are being pulled off a day that only had two.
        # Say what's actually leaving the plan, then name the exclusion
        # separately.
        day_json = next(d for d in plan["week"] if d["date"] == directive["date"])
        currently_plated = [i["name"] for i in day_json["items"] if i["name"] in matched]
        tag = directive["tag"]
        if currently_plated:
            return (
                f"On {label}, I'll drop {', '.join(currently_plated)} (tagged '{tag}') "
                f"from the plan and avoid {tag} in general while re-solving that day."
            )
        return (
            f"Nothing currently on {label}'s plan is tagged '{tag}', but I'll avoid {tag} "
            "while re-solving that day in case something would otherwise be picked."
        )

    next_on = on + timedelta(days=1)
    has_next_day = any(d["date"] == next_on.isoformat() for d in plan["week"])
    preview = (
        f"I'll log that you had '{directive['actual_item']}' instead of "
        f"'{directive['planned_item']}' on {label}, and update that day's totals"
    )
    preview += f", carrying the difference into {_weekday_date_label(next_on)}'s goals." if has_next_day else "."
    return preview


def _apply_feedback_directive(directive: dict, plan: dict) -> dict:
    on = date.fromisoformat(directive["date"])
    days_out = directive["days_out"]
    day_json = next(d for d in plan["week"] if d["date"] == directive["date"])
    matched_names = directive["matched_names"]

    food_df, viable = _food_df_for_day(days_out)
    keep_items = [i["name"] for i in day_json["items"] if i["name"] not in matched_names]

    free_minutes = COOK_TIME_MINUTES[plan["cook_time"]]
    day = DayPlan(on=on, free_minutes=free_minutes, pinned_items=keep_items)
    history = get_history()
    plan_df, summary = exclude_and_resolve(
        food_df, day, matched_names, plan["goals"], plan["budget"],
        filters=plan["filters"] or None, history=history,
        recency_penalty_weight=2.0, as_of=on,
        preference_bonus=get_taste_profile() or None, verbose=False,
    )
    history.record_plan(plan_df, on=on)
    save_history(history)

    return serialize_day({
        "day": on, "days_out": days_out,
        "viable_channels": [c.name for c in viable],
        "plan_df": plan_df, "summary": summary,
    })


def _apply_deviation_directive(directive: dict, plan: dict) -> dict:
    """Log the deviation, correct that day's totals, and carry any resulting
    shortfall/overage into the next day's goals before re-solving it.

    Returns {date_str: day_json} for every day that changed, the deviation
    day always, the following day too whenever one exists in the plan.
    """
    on = date.fromisoformat(directive["date"])
    day_json = next(d for d in plan["week"] if d["date"] == directive["date"])
    planned_name = directive["planned_item"]
    actual_name = directive["actual_item"]

    planned_row = next(i for i in day_json["items"] if i["name"] == planned_name)
    food_df, _viable = _food_df_for_day(directive["days_out"])
    actual_row = food_df[food_df["name"] == actual_name].iloc[0]

    planned_macros = {k: planned_row[k] for k in MACRO_KEYS}
    actual_macros = {k: float(actual_row[k]) for k in MACRO_KEYS}

    deviation_log = get_deviation_log()
    deviation_log.record(
        on, {"name": planned_name, **planned_macros}, {"name": actual_name, **actual_macros},
    )
    save_deviation_log(deviation_log)

    new_achieved = recompute_day_macros(day_json["achieved"], planned_macros, actual_macros)
    new_achieved["cost"] = day_json["achieved"]["cost"] - planned_row["total_cost"] + float(actual_row["price"])

    new_items = [i for i in day_json["items"] if i["name"] != planned_name]
    new_items.append({
        "name": actual_name,
        "source": actual_row["source"],
        "channel": classify_channel(actual_row["source"]),
        "servings": 1,
        "price": round(float(actual_row["price"]), 2),
        "total_cost": round(float(actual_row["price"]), 2),
        "calories": round(actual_macros["calories"], 1),
        "protein": round(actual_macros["protein"], 1),
        "carbs": round(actual_macros["carbs"], 1),
        "fat": round(actual_macros["fat"], 1),
    })

    goals = plan["goals"]
    gaps = {k: max(0, goals.get(k, 0) - new_achieved.get(k, 0)) for k in MACRO_KEYS}
    feasible = all(v <= 1e-9 for v in gaps.values())

    day_json["items"] = new_items
    day_json["achieved"] = {
        "cost": round(new_achieved["cost"], 2),
        "calories": round(new_achieved["calories"], 1),
        "protein": round(new_achieved["protein"], 1),
        "carbs": round(new_achieved["carbs"], 1),
        "fat": round(new_achieved["fat"], 1),
    }
    day_json["gaps"] = {k: round(v, 1) for k, v in gaps.items()}
    day_json["feasible"] = feasible

    updated_days = {directive["date"]: day_json}

    delta = {k: actual_macros[k] - planned_macros[k] for k in MACRO_KEYS}
    next_on = on + timedelta(days=1)
    next_day_json = next((d for d in plan["week"] if d["date"] == next_on.isoformat()), None)
    if next_day_json is not None:
        next_goals = carry_forward_goals(goals, delta)
        free_minutes = COOK_TIME_MINUTES[plan["cook_time"]]
        next_food_df, next_viable = _food_df_for_day(next_day_json["days_out"])
        next_day = DayPlan(on=next_on, free_minutes=free_minutes, goals=next_goals)
        history = get_history()
        next_plan_df, next_summary = run_optimizer(
            next_food_df, next_goals, plan["budget"], filters=plan["filters"] or None,
            time_budget_minutes=next_day.free_minutes, history=history,
            recency_penalty_weight=2.0, as_of=next_on,
            preference_bonus=get_taste_profile() or None, verbose=False,
        )
        history.record_plan(next_plan_df, on=next_on)
        save_history(history)
        serialized_next = serialize_day({
            "day": next_on, "days_out": next_day_json["days_out"],
            "viable_channels": [c.name for c in next_viable],
            "plan_df": next_plan_df, "summary": next_summary,
        })
        updated_days[serialized_next["date"]] = serialized_next

    return updated_days


@app.route("/api/plan/adjust", methods=["POST"])
@auth.login_required
def api_plan_adjust():
    """Turn a free-text message about the current plan into a mutation.

    Two-step, confirm-before-mutate flow (see NLU_ADJUSTMENTS.md): a first
    call with just {"message": ...} either asks a clarifying question or
    echoes back the parsed directive for confirmation, nothing is written
    either way. A second call with {"message": ..., "confirmed": true}
    re-parses the same message, parsing is pure given the same persisted
    plan, and actually applies it: a feedback directive drops the matched
    items and re-solves that one day; a deviation directive logs the swap,
    corrects that day's totals, and carries the delta into tomorrow's goals.
    """
    data = request.get_json(silent=True) or {}
    message = data.get("message")
    if not message or not isinstance(message, str):
        return jsonify({"error": "message is required"}), 400

    plan = load_plan()
    if plan is None:
        return jsonify({"error": "no plan saved yet"}), 404

    try:
        directive = _parse_adjust_directive(message, plan)
    except AdjustNeedsClarification as e:
        return jsonify({"status": "needs_clarification", "question": e.question, "options": e.options})

    if not data.get("confirmed"):
        return jsonify({
            "status": "confirm",
            "directive": directive,
            "preview": _adjust_preview(directive, plan),
        })

    if directive["type"] == "feedback":
        serialized_day = _apply_feedback_directive(directive, plan)
        persisted_plan = load_plan()
        for i, d in enumerate(persisted_plan["week"]):
            if d["date"] == serialized_day["date"]:
                persisted_plan["week"][i] = serialized_day
                break
        recompute_week_aggregates(persisted_plan)
        save_plan(persisted_plan)
        return jsonify({"day": serialized_day})

    updated_days = _apply_deviation_directive(directive, plan)
    persisted_plan = load_plan()
    for i, d in enumerate(persisted_plan["week"]):
        if d["date"] in updated_days:
            persisted_plan["week"][i] = updated_days[d["date"]]
    recompute_week_aggregates(persisted_plan)
    save_plan(persisted_plan)

    dates_in_order = sorted(updated_days)
    response = {"day": updated_days[dates_in_order[0]]}
    if len(dates_in_order) > 1:
        response["next_day"] = updated_days[dates_in_order[1]]
    return jsonify(response)


def _item_by_name(candidates, name):
    for fi in candidates:
        if fi.name == name:
            return fi
    return None


def _product_payload(item: dict, channel_label: str) -> dict:
    return {
        "type": "product",
        "name": item["name"],
        "category": item.get("category", ""),
        "channel": channel_label,
        "location": item.get("location", ""),
        "serving_unit": item.get("serving_unit", "1 serving"),
        "notes": item.get("notes", ""),
        "per_serving": {
            "price": item["price"],
            "calories": item["calories"],
            "protein": item["protein"],
            "carbs": item["carbs"],
            "fat": item["fat"],
            "fiber": item.get("fiber", 0),
            "sodium": item.get("sodium", 0),
        },
        "allergens": item.get("allergens", []),
        "flags": {k: bool(item.get(k, False)) for k in ("vegetarian", "vegan", "gluten_free", "halal")},
        "source_url": item.get("source_url") or None,
    }


@app.route("/api/recipe/<path:name>")
@auth.login_required
def api_recipe(name):
    source_hint = request.args.get("source")
    recipes = load_recipes()
    recipes_by_name = {r["name"]: r for r in recipes}

    if name in recipes_by_name and (not source_hint or source_hint == "Home Cooking"):
        recipe = recipes_by_name[name]
        ingredient_db = load_ingredient_db()
        pantry = get_pantry()
        priced = price_recipe(recipe, ingredient_db, pantry=pantry)

        ingredients = []
        for line in recipe["ingredients"]:
            ing = ingredient_db.get(line["ingredient"], {})
            qty = line["quantity"]
            have = pantry.have(line["ingredient"])
            to_buy = max(0.0, qty - have)
            ingredients.append({
                "ingredient": line["ingredient"],
                "display_name": line["ingredient"].replace("_", " "),
                "quantity": qty,
                "unit": ing.get("unit", ""),
                "have_quantity": round(have, 1),
                "to_buy_quantity": round(to_buy, 1),
                "price_if_bought": round(to_buy * ing.get("price_per_unit", 0.0), 2),
            })

        return jsonify({
            "type": "recipe",
            "name": recipe["name"],
            "category": recipe.get("category", "Recipe"),
            "servings": recipe["servings"],
            "prep_minutes": recipe.get("prep_minutes", 0),
            "cook_minutes": recipe.get("cook_minutes", 0),
            "notes": recipe.get("notes", ""),
            "per_serving": {
                "price": priced["price"],
                "calories": priced["calories"],
                "protein": priced["protein"],
                "carbs": priced["carbs"],
                "fat": priced["fat"],
                "fiber": priced["fiber"],
                "sodium": priced["sodium"],
            },
            "ingredients": ingredients,
            "instructions": recipe.get("instructions", []),
            # Hand-authored, original recipes, not sourced from an external
            # site, so there's no real URL to attach; left None rather than
            # fabricated. Populated for market/dining/prepared/restaurant
            # items via _product_payload when a real one exists.
            "source_url": recipe.get("source_url") or None,
        })

    for raw in load_market_items():
        if raw["name"] == name:
            return jsonify(_product_payload(raw, "Market"))
    for adapter, label in ((PreparedMealAdapter(), "Prepared Meal Delivery"), (RestaurantOrderAdapter(), "Restaurant Order")):
        fi = _item_by_name(adapter.candidates(), name)
        if fi is not None:
            return jsonify(_product_payload(fi.to_dict(), label))

    return jsonify({"error": f"no recipe or product named {name!r}"}), 404


@app.route("/api/shopping-list", methods=["POST"])
@auth.login_required
def api_shopping_list():
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    result = build_shopping_list(items, pantry=get_pantry())
    return jsonify(result)


@app.route("/api/shopping-list/instacart", methods=["POST"])
@auth.login_required
def api_shopping_list_instacart():
    """Turn a shopping list into a real Instacart shoppable page, per
    backend_support.md's "Instacart / real shopping list checkout" note.

    Calls Instacart's Create Recipe Page API (POST
    https://connect.instacart.com/idp/v1/products/recipe, see
    docs.instacart.com/developer_platform_api) and returns the resulting
    ``shoppable_url``. Needs a real Instacart Developer Platform partner API
    key, set as the INSTACART_API_KEY environment variable, this project
    doesn't have one; without it, this endpoint is honest about that rather
    than pretending to place an order.
    """
    api_key = os.environ.get(INSTACART_API_KEY_ENV)
    if not api_key:
        return jsonify({
            "error": "Instacart integration needs a partner API key. Set the "
                     f"{INSTACART_API_KEY_ENV} environment variable (see "
                     "docs.instacart.com/developer_platform_api) to enable a real "
                     "shoppable checkout link; until then, use the JSON export.",
        }), 501

    data = request.get_json(silent=True) or {}
    title = data.get("title") or "AutoEat Weekly Shopping List"
    image_url = data.get("image_url")
    if not image_url:
        return jsonify({"error": "image_url is required by Instacart's Create Recipe Page API"}), 400

    raw_ingredients = data.get("ingredients") or []
    if not raw_ingredients:
        return jsonify({"error": "ingredients is required and cannot be empty"}), 400

    ingredients = []
    for i in raw_ingredients:
        try:
            ingredients.append({
                "name": i["name"],
                "display_text": i.get("display_text", i["name"]),
                "measurements": [{"quantity": float(i["quantity"]), "unit": i.get("unit", "")}],
            })
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": f"invalid ingredient entry: {i!r}"}), 400

    payload = {
        "title": title,
        "image_url": image_url,
        "link_type": "recipe",
        "instructions": data.get("instructions") or ["See AutoEat for the full weekly plan and macros."],
        "ingredients": ingredients,
    }
    landing_page = {}
    if data.get("partner_linkback_url"):
        landing_page["partner_linkback_url"] = data["partner_linkback_url"]
    if "enable_pantry_items" in data:
        landing_page["enable_pantry_items"] = bool(data["enable_pantry_items"])
    if landing_page:
        payload["landing_page_configuration"] = landing_page

    try:
        resp = requests.post(
            f"{INSTACART_BASE_URL}/idp/v1/products/recipe",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
    except requests.RequestException as e:
        return jsonify({"error": f"couldn't reach Instacart: {e}"}), 502

    if not resp.ok:
        return jsonify({"error": f"Instacart API error ({resp.status_code}): {resp.text[:500]}"}), 502

    return jsonify({"shoppable_url": resp.json().get("products_link_url")})


@app.route("/api/ingredients")
@auth.login_required
def api_ingredients():
    db = load_ingredient_db()
    return jsonify({
        "ingredients": [
            {"key": key, "display_name": key.replace("_", " "), "unit": v["unit"]}
            for key, v in sorted(db.items())
        ]
    })


@app.route("/api/ingredients/search")
@auth.login_required
def api_ingredients_search():
    """Search Open Food Facts and USDA FoodData Central for a real product to
    add to the fridge, per backend_support.md's "Ingredient database coverage"
    note: lets a user track something that isn't already a recipe ingredient
    (e.g. "2 lbs of salmon") without needing to grow ingredients.json by hand.

    Results have no price (neither source is a pricing API, see
    openfoodfacts.py/usda_fdc.py) and are per-100g; POST /api/pantry accepts
    the returned ``key``/``unit`` directly for an ingredient not already in
    the curated database.
    """
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "q is required"}), 400

    def _entry(fi):
        return {
            "key": _slugify(fi.name),
            "display_name": fi.name,
            "source": fi.source,
            "unit": fi.serving_unit,
            "calories": fi.calories,
            "protein": fi.protein,
            "carbs": fi.carbs,
            "fat": fi.fat,
            "fiber": fi.fiber,
            "sodium": fi.sodium,
        }

    results = []
    try:
        for product in search_products(query, page_size=5):
            try:
                results.append(_entry(to_food_item(product, price=0.0)))
            except OpenFoodFactsError:
                continue  # unnamed or missing macro data, skip it
    except requests.RequestException:
        pass  # Open Food Facts unreachable; fall through to whatever USDA has

    try:
        for food in usda_search_foods(query, page_size=5):
            try:
                results.append(_entry(usda_to_food_item(food, price=0.0)))
            except UsdaFdcError:
                continue
    except (requests.RequestException, UsdaFdcError):
        pass  # USDA unreachable or rate-limited; return whatever we already have

    return jsonify({"results": results})


@app.route("/api/pantry", methods=["GET"])
@auth.login_required
def api_pantry_list():
    pantry = get_pantry()
    ingredient_db = load_ingredient_db()
    today = date.today()

    items = []
    for name, entry in pantry.list_items().items():
        ing = ingredient_db.get(name, {})
        days_left = None
        if entry.expires_on:
            days_left = (date.fromisoformat(entry.expires_on) - today).days
        items.append({
            "ingredient": name,
            "display_name": entry.display_name or name.replace("_", " "),
            "quantity": entry.quantity,
            "unit": ing.get("unit") or entry.unit or "",
            "expires_on": entry.expires_on,
            "days_until_expiry": days_left,
            # True for anything added via /api/ingredients/search rather than
            # picked from the curated ingredients.json dropdown.
            "custom": ing == {},
            "custom_source": entry.source,
        })

    def sort_key(i):
        days_left = i["days_until_expiry"]
        # Items with a known expiry sort first, soonest first; no-expiry items sort last.
        return (days_left is None, days_left if days_left is not None else 0, i["display_name"])

    items.sort(key=sort_key)

    return jsonify({"items": items, "expiring_soon": pantry.expiring_within(5, as_of=today)})


@app.route("/api/pantry", methods=["POST"])
@auth.login_required
def api_pantry_upsert():
    data = request.get_json(silent=True) or {}
    ingredient = (data.get("ingredient") or "").strip()
    if not ingredient:
        return jsonify({"error": "ingredient is required"}), 400

    ingredient_db = load_ingredient_db()
    known = ingredient_db.get(ingredient)
    # Anything not in the curated database (e.g. a result from
    # /api/ingredients/search) can still be tracked, as long as the caller
    # supplies a unit, there's just nothing to look one up from.
    unit = data.get("unit") or (known["unit"] if known else None)
    if known is None and not unit:
        return jsonify({
            "error": f"unknown ingredient {ingredient!r}; include a unit to track it as a "
                     "custom item not in the curated database (see /api/ingredients/search)",
        }), 400

    try:
        quantity = float(data.get("quantity", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "quantity must be a number"}), 400
    if quantity < 0:
        return jsonify({"error": "quantity cannot be negative"}), 400

    expires_on = data.get("expires_on") or None
    if expires_on:
        try:
            date.fromisoformat(expires_on)
        except ValueError:
            return jsonify({"error": "expires_on must be an ISO date (YYYY-MM-DD)"}), 400

    pantry = get_pantry()
    pantry.set_quantity(
        ingredient, quantity, expires_on=expires_on,
        unit=None if known else unit,
        display_name=None if known else (data.get("display_name") or None),
        source=None if known else (data.get("source") or None),
    )
    save_pantry(pantry)
    return jsonify({"ok": True})


@app.route("/api/pantry/<ingredient>", methods=["DELETE"])
@auth.login_required
def api_pantry_delete(ingredient):
    pantry = get_pantry()
    pantry.remove(ingredient)
    save_pantry(pantry)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
