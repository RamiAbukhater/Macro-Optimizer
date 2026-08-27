"""Turns a person into a macro goals dict.

Every other module in this package takes goals as a plain
{calories, protein, carbs, fat} dict typed in by hand (see GOALS in the
notebook's Configuration section). This module is what produces that dict
from a person instead: height, weight, age, sex, an activity level picked
from a short qualitative list, and a goal (lose fat / maintain / gain
muscle).

BMR uses the Mifflin-St Jeor equation, the current standard, more accurate
across body types than the older Harris-Benedict formula it replaced.
Activity level scales BMR to maintenance calories (TDEE) using the
standard Harris-Benedict activity multipliers, still the conventional
choice for this step even though Mifflin-St Jeor replaced Harris-Benedict
for the BMR calculation itself. The goal then shifts calories from
maintenance and sets a protein target; fat and carbs split the remainder.

This is a standard estimate, not a medical calculation, individual
metabolism varies. The calorie floor exists to keep a plugged-in profile
from producing an unsafely low target, not to make the number precise.
"""

from dataclasses import dataclass
from typing import Optional

ACTIVITY_MULTIPLIERS = {
    "sedentary": 1.2,  # little or no exercise
    "light": 1.375,  # light exercise/sports 1-3 days/week
    "moderate": 1.55,  # moderate exercise/sports 3-5 days/week
    "active": 1.725,  # hard exercise/sports 6-7 days/week
    "very_active": 1.9,  # very hard exercise, physical job, or training twice a day
}

GOAL_CALORIE_ADJUSTMENT = {
    "lose_fat": -500.0,  # ~1 lb/week deficit
    "maintain": 0.0,
    "gain_muscle": 300.0,  # lean surplus
}

GOAL_PROTEIN_G_PER_LB = {
    "lose_fat": 1.0,  # higher protein preserves lean mass in a deficit
    "maintain": 0.8,
    "gain_muscle": 0.9,
}

FAT_PERCENT_OF_CALORIES = 0.25
MIN_CALORIES = 1200.0  # safety floor regardless of computed deficit
LB_TO_KG = 0.45359237
INCH_TO_CM = 2.54


@dataclass
class Profile:
    sex: str  # "male" | "female"
    age_years: float
    height_inches: float
    weight_lbs: float
    activity: str  # key into ACTIVITY_MULTIPLIERS
    goal: str  # key into GOAL_CALORIE_ADJUSTMENT

    def __post_init__(self):
        if self.sex not in ("male", "female"):
            raise ValueError(f"sex must be 'male' or 'female', got {self.sex!r}")
        if self.activity not in ACTIVITY_MULTIPLIERS:
            raise ValueError(f"activity must be one of {list(ACTIVITY_MULTIPLIERS)}, got {self.activity!r}")
        if self.goal not in GOAL_CALORIE_ADJUSTMENT:
            raise ValueError(f"goal must be one of {list(GOAL_CALORIE_ADJUSTMENT)}, got {self.goal!r}")
        if self.age_years <= 0 or self.age_years > 120:
            raise ValueError(f"age_years must be a plausible age, got {self.age_years!r}")
        if self.height_inches <= 0:
            raise ValueError(f"height_inches must be positive, got {self.height_inches!r}")
        if self.weight_lbs <= 0:
            raise ValueError(f"weight_lbs must be positive, got {self.weight_lbs!r}")


def bmr(profile: Profile) -> float:
    """Basal metabolic rate in kcal/day, Mifflin-St Jeor."""
    weight_kg = profile.weight_lbs * LB_TO_KG
    height_cm = profile.height_inches * INCH_TO_CM
    base = 10 * weight_kg + 6.25 * height_cm - 5 * profile.age_years
    return base + 5 if profile.sex == "male" else base - 161


def tdee(profile: Profile) -> float:
    """Maintenance calories: BMR scaled by activity level."""
    return bmr(profile) * ACTIVITY_MULTIPLIERS[profile.activity]


def compute_goals(profile: Profile) -> dict:
    """The macro goals dict every optimizer/planner function in this package expects.

    Also carries 'bmr' and 'tdee' for display purposes; run_optimizer only
    reads calories/protein/carbs/fat, so the extra keys are harmless there.
    """
    maintenance = tdee(profile)
    calories = max(MIN_CALORIES, maintenance + GOAL_CALORIE_ADJUSTMENT[profile.goal])

    protein = GOAL_PROTEIN_G_PER_LB[profile.goal] * profile.weight_lbs
    fat = (calories * FAT_PERCENT_OF_CALORIES) / 9
    macro_calories_before_carbs = protein * 4 + fat * 9
    carbs = max(0.0, (calories - macro_calories_before_carbs) / 4)

    return {
        "calories": round(calories),
        "protein": round(protein, 1),
        "carbs": round(carbs, 1),
        "fat": round(fat, 1),
        "bmr": round(bmr(profile)),
        "tdee": round(maintenance),
    }
