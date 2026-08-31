from datetime import date

from macro_optimizer.deviation import (
    DeviationLog,
    carry_forward_goals,
    recompute_day_macros,
)


def test_recompute_day_macros_swaps_planned_for_actual():
    achieved = {"calories": 1800, "protein": 120, "carbs": 200, "fat": 60}
    planned_item = {"name": "Grilled Chicken Bowl", "calories": 550, "protein": 45, "carbs": 50, "fat": 15}
    actual_item = {"name": "Burger", "calories": 850, "protein": 35, "carbs": 55, "fat": 45}

    result = recompute_day_macros(achieved, planned_item, actual_item)

    assert result == {
        "calories": 1800 - 550 + 850,
        "protein": 120 - 45 + 35,
        "carbs": 200 - 50 + 55,
        "fat": 60 - 15 + 45,
    }


def test_carry_forward_goals_clamps_large_deviation():
    # Skipped a 1200-calorie dinner for a 400-calorie one: an 800-calorie
    # deficit. With a 2000-calorie default goal and a 20% cap, tomorrow's
    # goal should only move by 400 (20% of 2000), not the full 800.
    default_goals = {"calories": 2000, "protein": 150, "carbs": 250, "fat": 70}
    delta = {"calories": -800, "protein": 0, "carbs": 0, "fat": 0}

    next_goals = carry_forward_goals(default_goals, delta, cap_fraction=0.2)

    assert next_goals["calories"] == 2400
    assert next_goals["protein"] == 150
    assert next_goals["carbs"] == 250
    assert next_goals["fat"] == 70


def test_carry_forward_goals_does_not_clamp_small_deviation():
    default_goals = {"calories": 2000, "protein": 150, "carbs": 250, "fat": 70}
    delta = {"calories": 100, "protein": 5, "carbs": 0, "fat": 0}

    next_goals = carry_forward_goals(default_goals, delta, cap_fraction=0.2)

    assert next_goals["calories"] == 1900
    assert next_goals["protein"] == 145


def test_carry_forward_goals_only_covers_default_goals_keys():
    default_goals = {"calories": 2000}
    delta = {"calories": -50, "protein": -999}

    next_goals = carry_forward_goals(default_goals, delta)

    assert next_goals == {"calories": 2050}


def test_record_appends_entry_with_date_and_items():
    log = DeviationLog()
    planned_item = {"name": "Grilled Chicken Bowl", "calories": 550, "protein": 45, "carbs": 50, "fat": 15}
    actual_item = {"name": "Burger", "calories": 850, "protein": 35, "carbs": 55, "fat": 45}
    today = date.today()

    log.record(today, planned_item, actual_item)

    entries = log.entries()
    assert len(entries) == 1
    assert entries[0]["date"] == today.isoformat()
    assert entries[0]["planned_item"] == planned_item
    assert entries[0]["actual_item"] == actual_item


def test_persistence_round_trip(tmp_path):
    path = tmp_path / "deviations.json"
    log = DeviationLog(path)
    planned_item = {"name": "Grilled Chicken Bowl", "calories": 550, "protein": 45, "carbs": 50, "fat": 15}
    actual_item = {"name": "Burger", "calories": 850, "protein": 35, "carbs": 55, "fat": 45}
    log.record(date.today(), planned_item, actual_item)

    reloaded = DeviationLog(path)
    entries = reloaded.entries()

    assert len(entries) == 1
    assert entries[0]["planned_item"] == planned_item
    assert entries[0]["actual_item"] == actual_item
