import pytest

from macro_optimizer.profile import (
    ACTIVITY_MULTIPLIERS,
    MIN_CALORIES,
    Profile,
    bmr,
    compute_goals,
    tdee,
)


def make_profile(**overrides):
    defaults = dict(
        sex="male", age_years=25, height_inches=70, weight_lbs=170,
        activity="moderate", goal="maintain",
    )
    defaults.update(overrides)
    return Profile(**defaults)


def test_bmr_matches_known_mifflin_st_jeor_value_male():
    # 25yo male, 70in (177.8cm), 170lb (77.1kg):
    # 10*77.1 + 6.25*177.8 - 5*25 + 5 = 771 + 1111.25 - 125 + 5 = 1762.25
    p = make_profile(sex="male")
    assert bmr(p) == pytest.approx(1762.25, abs=1.0)


def test_bmr_matches_known_mifflin_st_jeor_value_female():
    p = make_profile(sex="female")
    male_bmr = bmr(make_profile(sex="male"))
    female_bmr = bmr(p)
    # Female formula subtracts 161 instead of adding 5, a fixed 166 kcal gap.
    assert male_bmr - female_bmr == pytest.approx(166, abs=0.5)


def test_tdee_applies_activity_multiplier():
    p = make_profile(activity="sedentary")
    assert tdee(p) == pytest.approx(bmr(p) * ACTIVITY_MULTIPLIERS["sedentary"])


def test_tdee_increases_with_activity_level():
    base = make_profile(activity="sedentary")
    active = make_profile(activity="very_active")
    assert tdee(active) > tdee(base)


def test_compute_goals_maintain_equals_tdee():
    p = make_profile(goal="maintain")
    goals = compute_goals(p)
    assert goals["calories"] == round(tdee(p))


def test_compute_goals_lose_fat_is_below_maintenance():
    p = make_profile(goal="lose_fat")
    goals = compute_goals(p)
    maintain_goals = compute_goals(make_profile(goal="maintain"))
    assert goals["calories"] < maintain_goals["calories"]


def test_compute_goals_gain_muscle_is_above_maintenance():
    p = make_profile(goal="gain_muscle")
    goals = compute_goals(p)
    maintain_goals = compute_goals(make_profile(goal="maintain"))
    assert goals["calories"] > maintain_goals["calories"]


def test_compute_goals_respects_minimum_calorie_floor():
    # Small, older, sedentary, low weight -> low TDEE, big deficit could go unsafely low.
    p = Profile(sex="female", age_years=70, height_inches=60, weight_lbs=95, activity="sedentary", goal="lose_fat")
    goals = compute_goals(p)
    assert goals["calories"] >= MIN_CALORIES


def test_compute_goals_macros_sum_to_calorie_target():
    p = make_profile()
    goals = compute_goals(p)
    macro_calories = goals["protein"] * 4 + goals["carbs"] * 4 + goals["fat"] * 9
    assert macro_calories == pytest.approx(goals["calories"], abs=5)


def test_compute_goals_protein_scales_with_bodyweight():
    light = compute_goals(make_profile(weight_lbs=130, goal="gain_muscle"))
    heavy = compute_goals(make_profile(weight_lbs=220, goal="gain_muscle"))
    assert heavy["protein"] > light["protein"]


def test_compute_goals_includes_bmr_and_tdee_for_display():
    p = make_profile()
    goals = compute_goals(p)
    assert goals["bmr"] == round(bmr(p))
    assert goals["tdee"] == round(tdee(p))


def test_compute_goals_is_directly_usable_by_run_optimizer():
    from macro_optimizer.database import load_food_db
    from macro_optimizer.optimizer import run_optimizer

    goals = compute_goals(make_profile())
    df = load_food_db()
    plan_df, summary = run_optimizer(df, goals, budget_cap=30.0, verbose=False)
    assert "feasible" in summary


@pytest.mark.parametrize("field,value", [
    ("sex", "other"),
    ("activity", "extreme"),
    ("goal", "bulk"),
    ("age_years", -5),
    ("age_years", 200),
    ("height_inches", 0),
    ("weight_lbs", -10),
])
def test_profile_rejects_invalid_input(field, value):
    kwargs = dict(sex="male", age_years=25, height_inches=70, weight_lbs=170, activity="moderate", goal="maintain")
    kwargs[field] = value
    with pytest.raises(ValueError):
        Profile(**kwargs)
