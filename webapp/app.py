"""Flask front end for macro_optimizer.

Two pages: an onboarding form (height, weight, activity, goal, budget) and
a dashboard that shows the computed goals, a suggested weekly menu across
every fulfillment channel built so far, and a meal analysis section, macro
achievement vs goal, cost vs budget, and which channel the week actually
leaned on.

Everything here is a thin layer over the macro_optimizer package. The
onboarding form's job is turning a person into a Profile (profile.py); the
dashboard's job is turning a solved plan_week_multichannel() result into
something worth looking at. No planning or optimization logic lives here
that doesn't already exist in the package.
"""

from datetime import date, timedelta

from flask import Flask, jsonify, request, send_from_directory

from macro_optimizer import (
    CookAtHomeAdapter,
    DayPlan,
    PreparedMealAdapter,
    Profile,
    RestaurantOrderAdapter,
    compute_goals,
    plan_week_multichannel,
)

app = Flask(__name__, static_folder="static", static_url_path="")

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


@app.route("/")
def onboarding_page():
    return send_from_directory(app.static_folder, "onboarding.html")


@app.route("/dashboard")
def dashboard_page():
    return send_from_directory(app.static_folder, "dashboard.html")


@app.route("/api/plan", methods=["POST"])
def api_plan():
    data = request.get_json(silent=True) or {}

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
        return jsonify({"error": f"invalid profile input: {e}"}), 400

    goals = compute_goals(profile)
    macro_goals = {k: goals[k] for k in ("calories", "protein", "carbs", "fat")}

    today = date.today()
    free_minutes = COOK_TIME_MINUTES[cook_time]
    days = [DayPlan(on=today + timedelta(days=i), free_minutes=free_minutes) for i in range(7)]
    channels = [CookAtHomeAdapter(), PreparedMealAdapter(), RestaurantOrderAdapter()]

    try:
        results = plan_week_multichannel(
            days, channels, macro_goals, budget, today=today,
            filters=filters or None, rotation_cap=3, recency_penalty_weight=1.0,
            verbose=False,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    week_payload = []
    channel_totals = {}
    for r in results:
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
                bucket = channel_totals.setdefault(channel, {"cost": 0.0, "items": 0})
                bucket["cost"] += float(row["total_cost"])
                bucket["items"] += 1

        achieved = r["summary"]["achieved"]
        week_payload.append({
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
        })

    for bucket in channel_totals.values():
        bucket["cost"] = round(bucket["cost"], 2)

    total_cost = sum(d["achieved"]["cost"] for d in week_payload)
    infeasible_days = [d["date"] for d in week_payload if not d["feasible"]]

    return jsonify({
        "profile": {
            "bmr": goals["bmr"],
            "tdee": goals["tdee"],
            "goal": profile.goal,
            "goal_label": GOAL_LABELS[profile.goal],
            "activity_label": ACTIVITY_LABELS[profile.activity],
        },
        "goals": macro_goals,
        "budget": budget,
        "today": week_payload[0],
        "week": week_payload,
        "week_summary": {
            "total_cost": round(total_cost, 2),
            "avg_daily_cost": round(total_cost / len(week_payload), 2),
            "days_planned": len(week_payload),
            "infeasible_days": infeasible_days,
        },
        "channel_totals": channel_totals,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
