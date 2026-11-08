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
import uuid
from datetime import date, timedelta
from typing import Optional

import numpy as np
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
from macro_optimizer.display_names import label as item_label
from macro_optimizer.nlu import classify_intent, extract_item_match, extract_tag, load_food_tags, resolve_day
from macro_optimizer.adjustments import exclude_and_resolve, resolve_tag_to_item_names
from macro_optimizer.deviation import MACRO_KEYS, DeviationLog, carry_forward_goals, recompute_day_macros
from macro_optimizer.tags import build_item_tags
from macro_optimizer import market
from macro_optimizer.shelf_life import shelf_life_days, use_by as shelf_use_by
from macro_optimizer.shopping_list import HOME_COOKING_SOURCE, product_stock_key, purchase_quantity
from macro_optimizer.week_stock import WeekStock
from macro_optimizer.taste import (
    Answer,
    TasteModel,
    answers_from_picks,
    concept_pool,
    display_name,
    load_concepts,
)

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
# Cards per survey. Each card is chosen for what it teaches the taste model
# (see macro_optimizer/taste.py), so 20 covers more ground than 24 random
# ones did. The survey can end sooner once most of the catalog has a rated
# item nearby.
SWIPE_CARD_COUNT = 20
SWIPE_MIN_CARDS = 12
SWIPE_STOP_COVERAGE = 0.8

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

# Per-weekday cooking minutes, Monday..Sunday (date.weekday() order). 0 means
# "no cooking that day", only zero-prep items fit. Upper bound keeps a typo
# like 900 from silently removing the time constraint.
COOK_SCHEDULE_MAX_MINUTES = 240


def cook_schedule_from(data: dict) -> list:
    """The 7-value cook schedule a request or saved plan carries.

    Prefers an explicit ``cook_schedule`` (7 numbers, Mon..Sun); falls back to
    the legacy single ``cook_time`` label ("low"/"medium"/"high") spread over
    all 7 days, so plans saved before per-day schedules still load. Raises
    ValueError on anything malformed.
    """
    schedule = data.get("cook_schedule")
    if schedule is not None:
        if not isinstance(schedule, list) or len(schedule) != 7:
            raise ValueError("cook_schedule must be a list of 7 minute values, Monday to Sunday")
        out = []
        for v in schedule:
            if isinstance(v, bool):
                raise ValueError("cook_schedule values must be numbers")
            m = int(round(float(v)))
            if not 0 <= m <= COOK_SCHEDULE_MAX_MINUTES:
                raise ValueError(f"cook_schedule values must be between 0 and {COOK_SCHEDULE_MAX_MINUTES}")
            out.append(m)
        return out
    cook_time = data.get("cook_time", "medium")
    if cook_time not in COOK_TIME_MINUTES:
        raise ValueError(f"cook_time must be one of {list(COOK_TIME_MINUTES)}")
    return [COOK_TIME_MINUTES[cook_time]] * 7


def cook_time_label(schedule: list) -> str:
    """Legacy ``cook_time`` echo: the preset name when every day matches one, else "custom"."""
    for label, minutes in COOK_TIME_MINUTES.items():
        if all(m == minutes for m in schedule):
            return label
    return "custom"


def minutes_on(schedule: list, on: date) -> int:
    return schedule[on.weekday()]


def week_day_plans(schedule: list, start: date) -> list:
    return [
        DayPlan(on=d, free_minutes=minutes_on(schedule, d))
        for d in (start + timedelta(days=i) for i in range(7))
    ]

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


_taste_model: Optional[TasteModel] = None


def taste_model() -> TasteModel:
    """The process-wide taste model over every channel's candidates, built on first use.

    Covers everything a plan can pick (catalog, recipes, delivery
    placeholders) so a swipe's bonus reaches all of it. Built from the
    pantry-free recipe prices; a pantry only changes a recipe's cost, not
    what it is. The concept cards (taste_concepts.json) ride along: they are
    what the survey deals, the catalog is what their answers score.
    """
    global _taste_model
    if _taste_model is None:
        items, seen = [], set()
        for channel in (CookAtHomeAdapter(), PreparedMealAdapter(), RestaurantOrderAdapter()):
            for item in channel.candidates():
                if item.name not in seen:
                    seen.add(item.name)
                    items.append(item.to_dict())
        _taste_model = TasteModel(items + load_concepts())
    return _taste_model


def _concept_fits_diet(concept: dict, filters: dict) -> bool:
    """Hard diet filters keep a card out of the deck; a swipe is never asked to stand in for one."""
    if concept["card"] != "dish":
        return True
    if filters.get("vegan") and not concept.get("vegan"):
        return False
    if (filters.get("vegetarian") or filters.get("vegan")) and not concept.get("veg"):
        return False
    if (filters.get("no_pork") or filters.get("halal")) and "pork" in concept.get("protein", []):
        return False
    return True


def swipe_card_pool(filters: Optional[dict] = None) -> list:
    """Names that can be dealt as cards: the concept cards that fit the user's diet filters.

    Store products are no longer dealt (a swipe on one mixes brand, package
    and price in with taste), but answers about them from earlier surveys
    still count; see get_taste_answers.
    """
    filters = filters or {}
    return [
        it["name"] for it in taste_model().items
        if it.get("concept") and _concept_fits_diet(it["concept"], filters)
    ]


def _parse_filters(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    def truthy(v) -> bool:  # query strings arrive as "1"/"true"/"0"
        return v.strip().lower() in ("1", "true", "yes", "on") if isinstance(v, str) else bool(v)
    return {k: truthy(v) for k, v in raw.items() if k in ("vegetarian", "vegan", "halal", "no_pork")}


def deal_card(answers: list, filters: dict, rng=None):
    """(name, reason) of the next card after ``answers``, honoring format-card slots; None when none are left."""
    model = taste_model()
    pool = concept_pool(swipe_card_pool(filters), model, answers)
    return model.next_card(answers, pool=pool, rng=rng)


def get_taste_answers() -> list:
    """Every swipe this user has made, as Answers, oldest first.

    Raw answers are stored (``taste_picks``), not a precomputed bonus, so
    the bonus is always propagated over the catalog as it is today. A
    pre-propagation ``taste_profile`` blob ({name: +/-bonus}) is read as
    answers too, so earlier swipes aren't lost.
    """
    legacy = db.get_blob(g.user["id"], "taste_profile", default={}) or {}
    answers = [Answer(str(name), float(value) > 0) for name, value in legacy.items()]
    answers += answers_from_picks(db.get_blob(g.user["id"], "taste_picks", default=[]))
    return answers


def get_taste_profile() -> dict:
    """{item name: preference bonus} for the whole catalog, propagated from this user's swipes."""
    answers = get_taste_answers()
    if not answers:
        return {}
    return taste_model().propagate(answers, SWIPE_BONUS_AMOUNT)


def build_channels(pantry: Optional[Pantry] = None):
    pantry = pantry if pantry is not None else get_pantry()
    return [CookAtHomeAdapter(pantry=pantry), PreparedMealAdapter(), RestaurantOrderAdapter()]


def week_stock_and_channels():
    """A simulated copy of the fridge for planning a week (see week_stock.py),
    and channels whose recipes price against that copy as the week uses it up.
    Settles past plan days first, so the copy reflects what's really left."""
    settle_consumption()
    stock = WeekStock(get_pantry(), recipes=load_recipes(),
                      ingredient_packages=market.ingredient_packages(load_ingredient_db()))
    return stock, build_channels(pantry=stock.pantry)


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

# The user enters and sees a WEEKLY food budget (people plan around paychecks
# and grocery trips, not per day). The optimizer still works one day at a
# time, so the weekly figure is split evenly across the planned days at the
# boundary (see daily_budget_cap). Before this, requests and saved plans held
# a DAILY ``budget``; anything still carrying only that legacy field is read
# as budget * PLAN_DAYS so an old $25/day never turns into $25/week.
PLAN_DAYS = 7
DEFAULT_WEEKLY_BUDGET = 175.0


def daily_budget_cap(weekly_budget: float, days: int = PLAN_DAYS) -> float:
    """Weekly budget -> the per-day budget_cap run_optimizer/plan_week expect."""
    return weekly_budget / days


def weekly_budget_from(data: dict) -> float:
    """``weekly_budget`` if present, else the legacy daily ``budget`` * PLAN_DAYS,
    else the default. Works on both request payloads and saved plan blobs."""
    if data.get("weekly_budget") is not None:
        return float(data["weekly_budget"])
    if data.get("budget") is not None:
        return float(data["budget"]) * PLAN_DAYS
    return DEFAULT_WEEKLY_BUDGET


def normalize_plan_budget(plan: Optional[dict]) -> Optional[dict]:
    """Upgrade a saved plan from the legacy daily ``budget`` field to
    ``weekly_budget`` in place (no-op on plans that already have it)."""
    if plan is not None and "weekly_budget" not in plan and "budget" in plan:
        plan["weekly_budget"] = weekly_budget_from(plan)
        del plan["budget"]
    return plan


def load_plan() -> Optional[dict]:
    return normalize_plan_budget(db.get_blob(g.user["id"], "plan"))


def save_plan(payload: dict) -> None:
    """Persist the latest full week plan, keeping one prior snapshot for /api/plan/undo."""
    # Purchases are stored per plan (see plan_week_key): a fresh plan gets a
    # fresh id, even when rebuilt the same day, so what the previous plan's
    # shopping put in the fridge counts as owned stock for this one.
    if "plan_id" not in payload and payload.get("week"):
        payload["plan_id"] = f"{payload['week'][0]['date']}-{uuid.uuid4().hex[:8]}"
    existing = db.get_blob(g.user["id"], "plan")
    if existing is not None:
        db.set_blob(g.user["id"], "plan_previous", existing)
    db.set_blob(g.user["id"], "plan", payload)


# --- Profile settings: the user's planning inputs, independent of any plan ---
#
# Everything onboarding asks for (body stats, goal, weekly budget, cook
# schedule, diet filters) lives in its own per-user blob, so it survives
# "Start over", exists before a plan does, and can be edited from /settings.
# Before this, those inputs only lived inside the saved plan as
# ``plan.profile_input`` (+ the plan's weekly_budget/cook_schedule/filters);
# load_profile_settings() migrates that shape on first read.
#
# Invariant: whenever a plan exists, these settings are the inputs it was
# built from. Every full-week build (/api/plan, reshuffle) re-saves them,
# PUT /api/settings rebuilds the week, and undo restores the matching set.
PROFILE_SETTINGS_KIND = "profile_settings"
SETTINGS_KEYS = PROFILE_INPUT_KEYS + ("weekly_budget", "cook_schedule", "filters")
FILTER_KEYS = ("vegetarian", "vegan", "halal", "gluten_free", "no_pork")


def settings_from_plan(plan: Optional[dict]) -> Optional[dict]:
    """The settings a saved plan was built from, or None when it predates
    profile_input (nothing to migrate)."""
    if not plan:
        return None
    profile_input = plan.get("profile_input") or {}
    if any(profile_input.get(k) is None for k in PROFILE_INPUT_KEYS):
        return None
    try:
        schedule = cook_schedule_from(plan)
    except ValueError:
        schedule = [COOK_TIME_MINUTES["medium"]] * 7
    return {
        **{k: profile_input[k] for k in PROFILE_INPUT_KEYS},
        "weekly_budget": weekly_budget_from(plan),
        "cook_schedule": schedule,
        "filters": {k: bool(v) for k, v in (plan.get("filters") or {}).items() if k in FILTER_KEYS},
    }


def load_profile_settings() -> Optional[dict]:
    """The stored settings blob, migrating from the saved plan on first read."""
    settings = db.get_blob(g.user["id"], PROFILE_SETTINGS_KIND)
    if settings:
        return settings
    migrated = settings_from_plan(load_plan())
    if migrated is not None:
        db.set_blob(g.user["id"], PROFILE_SETTINGS_KIND, migrated)
    return migrated


def save_profile_settings(data: dict, weekly_budget: float, cook_schedule: list, filters: dict) -> dict:
    """Persist the (already validated) inputs of a plan request as settings."""
    settings = {
        **{k: data.get(k) for k in PROFILE_INPUT_KEYS},
        "weekly_budget": round(float(weekly_budget), 2),
        "cook_schedule": list(cook_schedule),
        "filters": {k: bool(filters.get(k)) for k in FILTER_KEYS},
    }
    db.set_blob(g.user["id"], PROFILE_SETTINGS_KIND, settings)
    return settings


def with_saved_settings(data: dict) -> dict:
    """A plan request with any missing inputs filled from the stored settings.

    Request values win; null/absent ones fall back to settings (which in
    turn fall back to the saved plan's profile_input, see
    load_profile_settings). Legacy request fields still win over the
    settings they replaced: a daily ``budget`` beats the stored
    weekly_budget, a lone ``cook_time`` beats the stored cook_schedule.
    """
    base = dict(load_profile_settings() or {})
    if data.get("weekly_budget") is not None or data.get("budget") is not None:
        base.pop("weekly_budget", None)
    if data.get("cook_time") is not None and data.get("cook_schedule") is None:
        base.pop("cook_schedule", None)
    return {**base, **{k: v for k, v in data.items() if v is not None}}


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
    """Shared parsing for every endpoint that takes a profile + weekly_budget/cook_time/filters payload.

    Returns (profile, goals, macro_goals, weekly_budget, cook_schedule, filters).
    ``weekly_budget`` is the user-facing weekly figure; callers convert it
    with daily_budget_cap() before handing it to the optimizer.
    ``cook_schedule`` is 7 minute values, Monday..Sunday (see
    cook_schedule_from). Raises
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
        weekly_budget = weekly_budget_from(data)
        if not weekly_budget > 0:
            raise ValueError("weekly_budget must be positive")
        cook_schedule = cook_schedule_from(data)
        filters = {
            k: bool(v) for k, v in (data.get("filters") or {}).items()
            if k in FILTER_KEYS
        }
    except (KeyError, TypeError, ValueError) as e:
        raise PlanInputError(f"invalid profile input: {e}") from e

    goals = compute_goals(profile)
    macro_goals = {k: goals[k] for k in ("calories", "protein", "carbs", "fat")}
    return profile, goals, macro_goals, weekly_budget, cook_schedule, filters


def serialize_day(r: dict) -> dict:
    """One plan_week_multichannel() result dict -> the day-shaped JSON the frontend renders."""
    plan_df = r["plan_df"]
    items = []
    if not plan_df.empty:
        for _, row in plan_df.iterrows():
            channel = classify_channel(row["source"])
            items.append({
                "name": row["name"],
                "display_name": item_label(row),
                "source": row["source"],
                "channel": channel,
                "servings": int(row["servings"]),
                "upc": row.get("upc") or "",
                "from_stock": round(float(row.get("from_stock", 0) or 0), 2),
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


def serialize_week(results: list, profile: Profile, goals: dict, macro_goals: dict, weekly_budget: float,
                    cook_schedule: list, filters: dict) -> dict:
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
        "weekly_budget": round(weekly_budget, 2),
        "cook_time": cook_time_label(cook_schedule),
        "cook_schedule": cook_schedule,
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


_RECIPE_INGREDIENT_NOISE = {"raw", "dry", "fresh", "canned", "bulk", "plain", "90"}


def _recipe_ingredient_hint(recipe_name: str) -> str:
    """A recipe's three biggest ingredients by quantity, e.g. "chicken breast · broccoli · white rice"."""
    recipe = next((r for r in load_recipes() if r["name"] == recipe_name), None)
    if recipe is None:
        return ""
    biggest = sorted(recipe["ingredients"], key=lambda i: -i["quantity"])[:3]
    words = [
        " ".join(w for w in i["ingredient"].split("_") if w not in _RECIPE_INGREDIENT_NOISE)
        for i in biggest
    ]
    return " · ".join(words)


_PROTEIN_WORDS = {"plant": "plant-based", "nuts": "nuts", "egg": "eggs", "dairy": "dairy", "fish": "fish & seafood"}


def _concept_card_payload(item: dict, reason: str) -> dict:
    """A concept card: generic dish or way of eating, asked as "Would you eat this?"."""
    concept = item["concept"]
    image = concept.get("image") or {}
    if concept["card"] == "dish":
        parts = [] if concept["meal"] == "main" else [concept["meal"].capitalize()]
        if concept["cuisine"] != "none":
            parts.append(concept["cuisine"].capitalize())
        if "spicy" in concept["flavor"]:
            parts.append("spicy")
        elif "sweet" in concept["flavor"]:
            parts.append("sweet")
        proteins = [_PROTEIN_WORDS.get(p, p) for p in concept["protein"] if p != "none"]
        if proteins:
            parts.append(" & ".join(proteins[:2]))
        context = " · ".join(parts) or "Dish"
    else:
        context = concept.get("blurb", "")
    return {
        "name": item["name"],
        "title": concept["title"],
        "display_name": concept["title"],
        "prompt": "Would you eat this?" if concept["card"] == "dish" else "Would you eat this way?",
        "card_type": concept["card"],
        "context": context,
        "image_url": image.get("url", ""),
        "image_credit": f"{image['author']}, {image['license']}" if image.get("url") else "",
        "image_credit_url": image.get("page", ""),
        # The placeholder eyebrow shows the cuisine, else this "flavor" line.
        "placeholder": {
            "cuisine": concept.get("cuisine", "none"),
            "flavor": (concept.get("flavor") or ["plain"])[0] if concept["card"] == "dish" else "way of eating",
            "hint": "",
            "emoji": concept.get("emoji", ""),
        },
        "reason": reason,
        "category": "Concept",
        "source": "Concept",
    }


def swipe_card_payload(name: str, reason: str = "") -> dict:
    """One swipe card: photo URL (or placeholder hints), a clean title, and one context line."""
    model = taste_model()
    i = model.index[name]
    item, labels = model.items[i], model.labels[i]
    if item.get("concept"):
        return _concept_card_payload(item, reason)
    is_recipe = item["category"] == "Recipe"
    kind = item.get("kind") or ("home-cooked recipe" if is_recipe else item["category"])
    total_minutes = float(item.get("prep_minutes") or 0) + float(item.get("cook_minutes") or 0)
    context = f"{kind[:1].upper()}{kind[1:]} · ${float(item['price']):.2f}/serving"
    if total_minutes:
        context += f" · {total_minutes:.0f} min"
    image_url = item.get("image_url") or ""
    return {
        "name": name,
        "title": display_name(item_label(item)),
        "display_name": item_label(item),
        "context": context,
        "image_url": image_url,
        # How the placeholder looks when there's no photo (recipes) or the
        # photo fails to load: tinted by cuisine, captioned by ingredients.
        "placeholder": {
            "cuisine": labels["cuisine"],
            "flavor": labels["flavor"][0],
            "hint": _recipe_ingredient_hint(name) if is_recipe else kind,
        },
        "image_credit": "Kroger" if image_url else "",
        "reason": reason,
        "category": item["category"],
        "source": item["source"],
        "price": round(float(item["price"]), 2),
        "calories": round(float(item["calories"]), 1),
        "protein": round(float(item["protein"]), 1),
        "carbs": round(float(item["carbs"]), 1),
        "fat": round(float(item["fat"]), 1),
    }


@app.route("/settings")
@auth.login_required
def settings_page():
    return send_from_directory(app.static_folder, "settings.html")


def _goals_preview(goals: dict) -> dict:
    return {k: round(float(goals[k])) for k in ("calories", "protein", "carbs", "fat")}


def _taste_summary() -> dict:
    """The user's own swipes (not the propagated catalog-wide bonus), latest
    answer per food winning, split into liked / disliked."""
    latest = {}
    for answer in get_taste_answers():
        latest[answer.name] = answer.liked
    return {
        "liked": sorted(name for name, liked in latest.items() if liked),
        "disliked": sorted(name for name, liked in latest.items() if not liked),
    }


@app.route("/api/settings", methods=["GET"])
@auth.login_required
def api_settings_get():
    """Everything /settings renders: the stored (or migrated) profile
    settings, the macro targets they produce, whether a plan exists to
    rebuild, the account identity, and the taste profile.

    ``settings`` is null for an account that never finished onboarding; the
    page then links back to onboarding instead of showing an empty form.
    """
    settings = load_profile_settings()
    goals = None
    if settings is not None:
        try:
            goals = _goals_preview(parse_plan_request(settings)[1])
        except PlanInputError:
            goals = None
    return jsonify({
        "settings": settings,
        "goals": goals,
        "has_plan": load_plan() is not None,
        "account": {"email": g.user["email"], "username": g.user["username"]},
        "taste": _taste_summary(),
    })


def _merged_settings_request(data: dict) -> dict:
    """A settings edit (any subset of SETTINGS_KEYS) layered on the stored settings."""
    changes = data.get("settings")
    if not isinstance(changes, dict):
        raise PlanInputError("settings must be an object")
    # cook_time is accepted as the legacy spelling of a uniform schedule.
    unknown = sorted(set(changes) - set(SETTINGS_KEYS) - {"cook_time"})
    if unknown:
        raise PlanInputError(f"unknown setting(s): {unknown}")
    return with_saved_settings(changes)


@app.route("/api/settings/preview", methods=["POST"])
@auth.login_required
def api_settings_preview():
    """Macro targets for a draft edit, without saving anything, so the page
    can show what a new weight/goal/activity would mean before committing."""
    try:
        merged = _merged_settings_request(request.get_json(silent=True) or {})
        goals = parse_plan_request(merged)[1]
    except PlanInputError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"goals": _goals_preview(goals)})


@app.route("/api/settings", methods=["PUT"])
@auth.login_required
def api_settings_put():
    """Save a settings edit. If a plan exists, the week is rebuilt from today
    with the new settings in the same call (single-level undo via
    /api/plan/undo restores both the old plan and the old settings), so the
    plan on screen always matches the saved settings. With no plan yet, the
    settings are just stored for onboarding to pick up."""
    try:
        merged = _merged_settings_request(request.get_json(silent=True) or {})
        _, goals, _, weekly_budget, cook_schedule, filters = parse_plan_request(merged)
    except PlanInputError as e:
        return jsonify({"error": str(e)}), 400

    plan = None
    if load_plan() is not None:
        plan, error = build_week_plan(merged)
        if error:
            return jsonify({"error": error}), 400
        settings = load_profile_settings()
    else:
        settings = save_profile_settings(merged, weekly_budget, cook_schedule, filters)
    return jsonify({"settings": settings, "goals": _goals_preview(goals), "plan": plan})


@app.route("/api/settings/taste", methods=["POST"])
@auth.login_required
def api_settings_taste():
    """Edit the swipe taste profile: ``{"remove": [names]}`` forgets every
    swipe on those foods, ``{"clear": true}`` forgets all swipes. Retaking
    the survey goes through the existing POST /api/swipe-survey, which
    appends new answers (a later answer for the same food wins)."""
    data = request.get_json(silent=True) or {}
    # Swipes live in taste_picks (raw answers) plus, for older accounts, the
    # pre-propagation taste_profile {name: bonus}; edit both.
    picks = db.get_blob(g.user["id"], "taste_picks", default=[]) or []
    legacy = db.get_blob(g.user["id"], "taste_profile", default={}) or {}
    if data.get("clear"):
        picks, legacy = [], {}
    else:
        remove = data.get("remove")
        if not isinstance(remove, list):
            return jsonify({"error": "send {remove: [names]} or {clear: true}"}), 400
        names = {str(n) for n in remove}
        picks = [p for p in picks if not (isinstance(p, dict) and str(p.get("name")) in names)]
        legacy = {k: v for k, v in legacy.items() if k not in names}
    db.set_blob(g.user["id"], "taste_picks", picks)
    db.set_blob(g.user["id"], "taste_profile", legacy)
    return jsonify({"taste": _taste_summary()})


@app.route("/api/account/password", methods=["POST"])
@auth.login_required
def api_account_password():
    data = request.get_json(silent=True) or {}
    current = data.get("current_password") or ""
    new = data.get("new_password") or ""
    if not auth.verify_password(current, g.user["password_hash"]):
        return jsonify({"error": "current password is incorrect"}), 403
    if len(new) < MIN_PASSWORD_LENGTH:
        return jsonify({"error": f"new password must be at least {MIN_PASSWORD_LENGTH} characters"}), 400
    db.update_password_hash(g.user["id"], auth.hash_password(new))
    return jsonify({"ok": True})


ACCOUNT_DELETE_CONFIRMATION = "DELETE"


@app.route("/api/account", methods=["DELETE"])
@auth.login_required
def api_account_delete():
    """Permanently delete the account and every per-user blob. Needs the
    current password plus the literal confirmation word, so a stray click
    or a stolen-but-unlocked session alone can't do it."""
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != ACCOUNT_DELETE_CONFIRMATION:
        return jsonify({"error": f'type {ACCOUNT_DELETE_CONFIRMATION} to confirm'}), 400
    if not auth.verify_password(data.get("password") or "", g.user["password_hash"]):
        return jsonify({"error": "password is incorrect"}), 403
    db.delete_user(g.user["id"])
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/swipe-cards")
@auth.login_required
def api_swipe_cards():
    """A fixed, spread-out deck for the cold-start swipe survey.

    Non-adaptive fallback for a client that wants the whole deck up front:
    greedy coverage of the taste feature space (TasteModel.covering_set), so
    no two cards point the same way. The onboarding survey itself uses
    /api/swipe-cards/next, which adapts to each answer.
    """
    # Greedy coverage (each pick counted as an answer so the next lands far
    # from it, as TasteModel.covering_set does), with format cards in their slots.
    filters = _parse_filters(request.args.to_dict())
    chosen: list = []
    while len(chosen) < SWIPE_CARD_COUNT:
        nxt = deal_card([Answer(n, True) for n in chosen], filters)
        if nxt is None:
            break
        chosen.append(nxt[0])
    return jsonify({"cards": [swipe_card_payload(n, "new ground") for n in chosen]})


@app.route("/api/swipe-cards/next", methods=["POST"])
@auth.login_required
def api_swipe_cards_next():
    """The most informative next card given this survey's answers so far.

    Stateless: the client sends every answer each time ({answers: [{name,
    liked}], seed, filters}), so prefetching the next card for both a like
    and a dislike is just two calls. Returns {card, done, answered, total};
    ``card`` is null once the survey has enough. ``seed`` (an int, fixed
    per survey) makes near-tie choices repeatable, so a prefetched card and
    a fresh request agree. ``filters`` ({vegetarian, vegan, halal,
    no_pork}) keeps cards the user can't eat out of the deck.
    """
    data = request.get_json(silent=True) or {}
    raw = data.get("answers", [])
    if not isinstance(raw, list):
        return jsonify({"error": "answers must be a list of {name, liked}"}), 400
    answers = answers_from_picks(raw)
    model = taste_model()
    done = len(answers) >= SWIPE_CARD_COUNT or (
        len(answers) >= SWIPE_MIN_CARDS and model.coverage(answers) >= SWIPE_STOP_COVERAGE
    )
    card = None
    if not done:
        seed = data.get("seed")
        rng = np.random.default_rng([seed, len(answers)]) if isinstance(seed, int) and seed >= 0 else np.random.default_rng()
        nxt = deal_card(answers, _parse_filters(data.get("filters")), rng=rng)
        if nxt is None:
            done = True
        else:
            card = swipe_card_payload(*nxt)
    return jsonify({"card": card, "done": done, "answered": len(answers), "total": SWIPE_CARD_COUNT})


@app.route("/api/swipe-survey", methods=["POST"])
@auth.login_required
def api_swipe_survey():
    data = request.get_json(silent=True) or {}
    picks = data.get("picks")
    if not isinstance(picks, list):
        return jsonify({"error": "picks must be a list of {name, liked}"}), 400

    # Append to the persisted answers rather than returning a stateless
    # computed dict, so taste carries across sessions instead of only
    # applying to the one /api/plan call the client passes this into. A
    # later answer for the same name wins when propagated.
    stored = db.get_blob(g.user["id"], "taste_picks", default=[]) or []
    stored += [{"name": a.name, "liked": a.liked} for a in answers_from_picks(picks)]
    db.set_blob(g.user["id"], "taste_picks", stored)

    return jsonify({"preference_bonus": get_taste_profile()})


@app.route("/api/plan", methods=["POST"])
@auth.login_required
def api_plan():
    data = with_saved_settings(request.get_json(silent=True) or {})
    payload, error = build_week_plan(data)
    if error:
        return jsonify({"error": error}), 400
    return jsonify(payload)


def build_week_plan(data: dict):
    """Build, save, and return a fresh 7-day plan from a full plan request.

    Returns (payload, None) or (None, error message). Also re-saves the
    request's inputs as the user's profile settings, keeping settings and
    plan in step. Shared by POST /api/plan and PUT /api/settings.
    """
    try:
        profile, goals, macro_goals, weekly_budget, cook_schedule, filters = parse_plan_request(data)
        preference_bonus = data.get("preference_bonus")
        if preference_bonus is not None:
            if not isinstance(preference_bonus, dict):
                raise ValueError("preference_bonus must be an object")
            preference_bonus = {str(k): float(v) for k, v in preference_bonus.items()}
    except (PlanInputError, ValueError) as e:
        return None, str(e)

    # The user's persisted taste profile always applies; anything explicitly
    # passed in this request (e.g. a swipe survey run this onboarding pass)
    # merges on top of it, per-item values in the request winning.
    combined_bonus = {**get_taste_profile(), **(preference_bonus or {})}

    today = date.today()
    days = week_day_plans(cook_schedule, today)
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
        stock, channels = week_stock_and_channels()
        results = plan_week_multichannel(
            days, channels, macro_goals, daily_budget_cap(weekly_budget, len(days)), today=today,
            filters=filters or None, history=history, rotation_cap=1, recency_penalty_weight=5.0,
            tag_rotation_cap=TAG_ROTATION_CAP, item_tags=build_catalog_item_tags(channels),
            preference_bonus=combined_bonus or None, stock=stock, verbose=False,
        )
    except ValueError as e:
        return None, str(e)

    save_history(history)
    payload = serialize_week(results, profile, goals, macro_goals, weekly_budget, cook_schedule, filters)
    payload["profile_input"] = {k: data.get(k) for k in PROFILE_INPUT_KEYS}
    save_plan(payload)
    mark_plan_start(payload)
    save_profile_settings(data, weekly_budget, cook_schedule, filters)
    return payload, None


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
    previous = normalize_plan_budget(db.get_blob(g.user["id"], "plan_previous"))
    if previous is None:
        return jsonify({"error": "nothing to undo"}), 404
    current = db.get_blob(g.user["id"], "plan")
    db.set_blob(g.user["id"], "plan", previous)
    db.set_blob(g.user["id"], "plan_previous", current)
    # Keep settings matching the plan now on screen (undoing a settings
    # rebuild also undoes the settings change).
    restored = settings_from_plan(previous)
    if restored is not None:
        db.set_blob(g.user["id"], PROFILE_SETTINGS_KIND, restored)
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
    data = with_saved_settings(request.get_json(silent=True) or {})

    try:
        profile, goals, macro_goals, weekly_budget, cook_schedule, filters = parse_plan_request(data)
    except PlanInputError as e:
        return jsonify({"error": str(e)}), 400

    week_items = data.get("week_items") or []

    today = date.today()
    days = week_day_plans(cook_schedule, today)

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
        stock, channels = week_stock_and_channels()
        results = plan_week_multichannel(
            days, channels, macro_goals, daily_budget_cap(weekly_budget, len(days)), today=today,
            filters=filters or None, history=history, rotation_cap=1, recency_penalty_weight=5.0,
            tag_rotation_cap=TAG_ROTATION_CAP, item_tags=build_catalog_item_tags(channels),
            preference_bonus=get_taste_profile() or None, stock=stock, verbose=False,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # The reshuffled week is now the real plan; commit it to the persisted
    # history so future plans/reshuffles see it too.
    for r in results:
        persisted_history.record_plan(r["plan_df"], on=r["day"])
    save_history(persisted_history)

    payload = serialize_week(results, profile, goals, macro_goals, weekly_budget, cook_schedule, filters)
    payload["profile_input"] = {k: data.get(k) for k in PROFILE_INPUT_KEYS}
    save_plan(payload)
    mark_plan_start(payload)
    save_profile_settings(data, weekly_budget, cook_schedule, filters)
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
    data = with_saved_settings(request.get_json(silent=True) or {})

    try:
        profile, goals, macro_goals, weekly_budget, cook_schedule, filters = parse_plan_request(data)
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

    free_minutes = minutes_on(cook_schedule, day_on)
    history = get_history()
    try:
        plan_df, summary = run_optimizer(
            food_df, macro_goals, daily_budget_cap(weekly_budget), filters=filters or None,
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


def _match_item_name(message: str, rows) -> Optional[str]:
    """Fuzzy-match free text against food rows, returning the matched row's raw ``name``.

    People type what they were shown ("the hawaiian bagels"), which is the
    display name, not the raw Kroger title the rest of the app keys on, so
    both are offered to the matcher and the hit is mapped back to ``name``.
    Raw names go in first so they win ties: several brands can share one
    display name, so a message quoting a full title should pick that exact
    product rather than whichever brand first claimed the shared label.
    """
    by_text = {}
    for row in rows:
        by_text.setdefault(row["name"], row["name"])
    for row in rows:
        by_text.setdefault(item_label(row), row["name"])
    match = extract_item_match(message, list(by_text))
    return by_text[match] if match is not None else None


def _labels_for(names, *row_sources) -> dict:
    """{raw name: display name} for ``names``, looked up in plan items / food_df records."""
    wanted = set(names)
    labels = {}
    for rows in row_sources:
        for row in rows:
            if row["name"] in wanted and row["name"] not in labels:
                labels[row["name"]] = item_label(row)
    return {n: labels.get(n, n) for n in names}


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
        # An item named outright on this day's plan wins over a category word
        # inside its name: "I didn't like the Nonfat Plain Greek Yogurt" means
        # that yogurt, not every dairy item.
        lowered = message.lower()
        named = next((i["name"] for i in day_json["items"]
                      if (i.get("display_name") or i["name"]).lower() in lowered or i["name"].lower() in lowered),
                     None)
        if named is not None:
            tag = None
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
        food_rows = food_df.to_dict("records")
        item_match = _match_item_name(message, day_json["items"])
        if item_match is None:
            item_match = _match_item_name(message, food_rows)
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
            "labels": _labels_for([item_match], day_json["items"], food_rows),
        }

    # intent == "deviation"
    planned_item = _match_item_name(message, day_json["items"])
    if planned_item is None:
        # Options are display names: adjust.js resends the message with the
        # picked option appended, and _match_item_name matches those too.
        raise AdjustNeedsClarification(
            f"Which item from {_weekday_date_label(on)}'s plan did you skip?",
            [item_label(i) for i in day_json["items"]],
        )
    # Excludes the planned item from the search space so a message that
    # mentions both items doesn't have the planned item winning its own fuzzy
    # match as "what was actually eaten." That means its display name too:
    # other brands of the same food share it ("Plain Bagels"), and would
    # otherwise match the planned item's words perfectly.
    food_df, _viable = _food_df_for_day(day_json["days_out"])
    food_rows = food_df.to_dict("records")
    planned_label = next((item_label(i) for i in day_json["items"] if i["name"] == planned_item), planned_item)
    actual_item = _match_item_name(message, [
        r for r in food_rows if r["name"] != planned_item and item_label(r) != planned_label
    ])
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
        "labels": _labels_for([planned_item, actual_item], day_json["items"], food_rows),
    }


def _adjust_preview(directive: dict, plan: dict) -> str:
    on = date.fromisoformat(directive["date"])
    label = _weekday_date_label(on)
    day_json = next(d for d in plan["week"] if d["date"] == directive["date"])
    shown = {**{i["name"]: item_label(i) for i in day_json["items"]}, **directive.get("labels", {})}

    def show(name: str) -> str:
        return shown.get(name, name)

    if directive["type"] == "feedback":
        matched = directive["matched_names"]
        if not matched:
            return f"Nothing on {label} matches '{directive['tag']}', so this won't change that day's plan."
        if directive.get("match_kind") == "item":
            return f"On {label}, I'll drop {', '.join(show(n) for n in matched)} from the plan and re-solve that day."

        # matched_names is every catalog item the tag covers, not just what's
        # plated today, resolve_tag_to_item_names has to return the whole set
        # so exclude_and_resolve keeps the re-solve from picking a same-tag
        # replacement, but showing all of it as "I'll drop X, Y, Z..." reads
        # like a dozen items are being pulled off a day that only had two.
        # Say what's actually leaving the plan, then name the exclusion
        # separately.
        currently_plated = [item_label(i) for i in day_json["items"] if i["name"] in matched]
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
        f"I'll log that you had '{show(directive['actual_item'])}' instead of "
        f"'{show(directive['planned_item'])}' on {label}, and update that day's totals"
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

    free_minutes = minutes_on(cook_schedule_from(plan), on)
    day = DayPlan(on=on, free_minutes=free_minutes, pinned_items=keep_items)
    history = get_history()
    plan_df, summary = exclude_and_resolve(
        food_df, day, matched_names, plan["goals"], daily_budget_cap(weekly_budget_from(plan)),
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
        "display_name": item_label(actual_row),
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
        free_minutes = minutes_on(cook_schedule_from(plan), next_on)
        next_food_df, next_viable = _food_df_for_day(next_day_json["days_out"])
        next_day = DayPlan(on=next_on, free_minutes=free_minutes, goals=next_goals)
        history = get_history()
        next_plan_df, next_summary = run_optimizer(
            next_food_df, next_goals, daily_budget_cap(weekly_budget_from(plan)), filters=plan["filters"] or None,
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
        "display_name": item_label(item),
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
    result = build_shopping_list(
        items, pantry=get_pantry(), products=market.products_by_upc(),
        ingredient_packages=market.ingredient_packages(load_ingredient_db()),
    )
    return jsonify(result)


# --- Shopping -> fridge -> planning -----------------------------------------
#
# The saved plan's week is shopped as whole packages (shopping_list.py).
# Checking a line off records the purchase for that plan week in the
# "purchases" blob and adds the full package contents to the fridge as a lot
# with id "buy:<week>:<line key>", so re-checking is a no-op and unchecking
# removes exactly that lot. Only our own data is stored: UPC / ingredient,
# package count, quantity, dates. Prices and titles are looked up live.
#
# A day passing means its plan was eaten: settle_consumption() takes every
# past, not yet settled plan day out of the fridge. The deviation flow
# already rewrites a day's saved items to what was actually eaten, so
# settling the saved plan accounts for logged deviations.

PURCHASES_KIND = "purchases"
FRIDGE_STATE_KIND = "fridge_state"


def today() -> date:
    """Today's date, one seam tests can move to make days pass."""
    return date.today()


def plan_week_key(plan: Optional[dict]) -> Optional[str]:
    """Which plan week purchases belong to: the plan's id (its start date
    plus a suffix), or just the start date for plans saved before ids."""
    if not plan or not plan.get("week"):
        return None
    return plan.get("plan_id") or plan["week"][0]["date"]


def purchase_lot_prefix(week: str) -> str:
    return f"buy:{week}:"


def _consume_plan_item(pantry: Pantry, item: dict, recipes_by_name: dict) -> None:
    servings = float(item.get("servings") or 0)
    if item.get("upc"):
        pantry.consume(product_stock_key(item["upc"]), servings)
        return
    recipe = recipes_by_name.get(item.get("name"))
    if recipe is not None and item.get("source") == HOME_COOKING_SOURCE:
        scale = servings / recipe["servings"]
        for line in recipe["ingredients"]:
            pantry.consume(line["ingredient"], line["quantity"] * scale)


def settle_consumption(as_of: Optional[date] = None) -> int:
    """Count every past, unsettled plan day as eaten. Returns days settled.

    The first call for a user only records yesterday as settled, so plans
    from before this existed don't suddenly drain the fridge.
    """
    as_of = as_of or today()
    yesterday = (as_of - timedelta(days=1)).isoformat()
    state = db.get_blob(g.user["id"], FRIDGE_STATE_KIND, default=None)
    if not state or not state.get("consumed_through"):
        db.set_blob(g.user["id"], FRIDGE_STATE_KIND, {"consumed_through": yesterday})
        return 0
    through = state["consumed_through"]
    if through >= yesterday:
        return 0
    plan = load_plan()
    days = [d for d in (plan or {}).get("week", []) if through < d["date"] <= yesterday]
    if days:
        pantry = get_pantry()
        recipes_by_name = {r["name"]: r for r in load_recipes()}
        for day in days:
            for item in day["items"]:
                _consume_plan_item(pantry, item, recipes_by_name)
        save_pantry(pantry)
    db.set_blob(g.user["id"], FRIDGE_STATE_KIND, {"consumed_through": yesterday})
    return len(days)


def mark_plan_start(plan: dict) -> None:
    """A new plan's days start today; settling continues from the day before it."""
    start = (date.fromisoformat(plan["week"][0]["date"]) - timedelta(days=1)).isoformat()
    state = db.get_blob(g.user["id"], FRIDGE_STATE_KIND, default=None) or {}
    if not state.get("consumed_through") or state["consumed_through"] < start:
        db.set_blob(g.user["id"], FRIDGE_STATE_KIND, {"consumed_through": start})


def get_purchases(week: str) -> dict:
    return (db.get_blob(g.user["id"], PURCHASES_KIND, default={}) or {}).get(week, {})


def save_purchases(week: str, week_purchases: dict) -> None:
    every_week = db.get_blob(g.user["id"], PURCHASES_KIND, default={}) or {}
    every_week[week] = week_purchases
    db.set_blob(g.user["id"], PURCHASES_KIND, every_week)


def week_shopping_list(plan: dict, products: Optional[dict] = None) -> dict:
    """The saved plan's whole week as a whole-package shopping list."""
    week = plan_week_key(plan)
    items = [i for d in plan["week"] for i in d["items"]]
    result = build_shopping_list(
        items, pantry=get_pantry(),
        products=products if products is not None else market.products_by_upc(),
        ingredient_packages=market.ingredient_packages(load_ingredient_db()),
        exclude_lot_prefix=purchase_lot_prefix(week), bought=get_purchases(week),
    )
    result["week_start"] = plan["week"][0]["date"]
    result["plan_id"] = week
    return result


def _stock_key_for(line: dict) -> str:
    if line.get("ingredient"):
        return line["ingredient"]
    if line.get("upc"):
        return product_stock_key(line["upc"])
    return f"item:{line['name']}"


def _shelf_days_for(line: dict, products: dict) -> int:
    if line.get("ingredient"):
        return shelf_life_days(ingredient=line["ingredient"])
    live = products.get(line.get("upc") or "") or products.get(line.get("name")) or {}
    return shelf_life_days(kind=live.get("kind", ""), category=live.get("category", ""), name=line.get("name", ""))


def set_line_bought(plan: dict, line: dict, bought: bool, products: dict) -> bool:
    """Check (or uncheck) one shopping line. Idempotent both ways; returns
    whether anything changed."""
    week = plan_week_key(plan)
    purchases = get_purchases(week)
    key = line["key"]
    lot_id = purchase_lot_prefix(week) + key
    pantry = get_pantry()
    if bought:
        if key in purchases:
            return False
        qty = purchase_quantity(line)
        if not qty:
            return False
        on = today()
        stock_key = _stock_key_for(line)
        pantry.add_lot(stock_key, lot_id, qty, purchased_on=on.isoformat(),
                       use_by=shelf_use_by(on, _shelf_days_for(line, products)),
                       unit=None if line.get("ingredient") else "servings")
        purchases[key] = {
            "stock_key": stock_key, "upc": line.get("upc") or None, "ingredient": line.get("ingredient"),
            "packages": line.get("packages"), "quantity_added": qty, "bought_on": on.isoformat(),
        }
    else:
        if key not in purchases:
            return False
        pantry.remove_lot(purchases[key].get("stock_key") or _stock_key_for(line), lot_id)
        del purchases[key]
    save_pantry(pantry)
    save_purchases(week, purchases)
    return True


@app.route("/api/shopping-list", methods=["GET"])
@auth.login_required
def api_shopping_list_get():
    """The saved plan's week as whole packages, with each line's bought state."""
    settle_consumption()
    plan = load_plan()
    if plan is None:
        return jsonify({"error": "no plan saved yet"}), 404
    return jsonify(week_shopping_list(plan))


@app.route("/api/shopping-list/bought", methods=["POST"])
@auth.login_required
def api_shopping_list_bought():
    """``{key, bought}``, or ``{all: true, bought}`` for every line: check lines
    off, adding their full package contents to the fridge, or uncheck to undo."""
    data = request.get_json(silent=True) or {}
    plan = load_plan()
    if plan is None:
        return jsonify({"error": "no plan saved yet"}), 404
    bought = bool(data.get("bought", True))
    products = market.products_by_upc()
    result = week_shopping_list(plan, products)
    lines = result["ingredients"] + result["packaged_items"]
    if data.get("all"):
        targets = lines
    else:
        targets = [l for l in lines if l["key"] == data.get("key")]
        if not targets:
            return jsonify({"error": f"no shopping line {data.get('key')!r}"}), 400
    changed = sum(set_line_bought(plan, line, bought, products) for line in targets)
    out = week_shopping_list(plan, products)
    out["changed"] = changed
    return jsonify(out)


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
    settle_consumption()
    pantry = get_pantry()
    ingredient_db = load_ingredient_db()
    on = today()
    products = None

    items = []
    for name, entry in pantry.list_items().items():
        if entry.quantity <= 1e-9:
            continue  # used up
        ing = ingredient_db.get(name, {})
        days_left = None
        if entry.expires_on:
            days_left = (date.fromisoformat(entry.expires_on) - on).days
        is_product = name.startswith("upc:")
        display = entry.display_name or name.replace("_", " ")
        if is_product:
            # Store products are named live from Kroger, never from a stored label.
            if products is None:
                products = market.products_by_upc()
            live = products.get(name[len("upc:"):]) or {}
            display = live.get("display_name") or live.get("name") or f"Store product {name[len('upc:'):]}"
        purchased = [l["purchased_on"] for l in entry.lots if l.get("purchased_on")]
        items.append({
            "ingredient": name,
            "display_name": display,
            "quantity": round(entry.quantity, 2),
            "unit": "servings" if is_product else (ing.get("unit") or entry.unit or ""),
            "expires_on": entry.expires_on,
            "days_until_expiry": days_left,
            "kind": "product" if is_product else "ingredient",
            "purchased_on": max(purchased) if purchased else None,
            "from_shopping": any(str(l["id"]).startswith("buy:") for l in entry.lots),
            # True for anything added via /api/ingredients/search rather than
            # picked from the curated ingredients.json dropdown.
            "custom": ing == {} and not is_product,
            "custom_source": entry.source,
        })

    def sort_key(i):
        days_left = i["days_until_expiry"]
        # Items with a known expiry sort first, soonest first; no-expiry items sort last.
        return (days_left is None, days_left if days_left is not None else 0, i["display_name"])

    items.sort(key=sort_key)

    expiring = [i["display_name"] for i in items
                if i["days_until_expiry"] is not None and 0 <= i["days_until_expiry"] <= 5]
    return jsonify({"items": items, "expiring_soon": expiring})


@app.route("/api/pantry", methods=["POST"])
@auth.login_required
def api_pantry_upsert():
    data = request.get_json(silent=True) or {}
    ingredient = (data.get("ingredient") or "").strip()
    if not ingredient:
        return jsonify({"error": "ingredient is required"}), 400

    ingredient_db = load_ingredient_db()
    known = ingredient_db.get(ingredient)
    if ingredient.startswith("upc:"):
        data = {**data, "unit": "servings", "display_name": None, "source": None}
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
