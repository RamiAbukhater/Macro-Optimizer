import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "pull_kroger_prices.py"
spec = importlib.util.spec_from_file_location("pull_kroger_prices", SCRIPT)
pull = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pull)

TEXT = """{
  "rice": {
    "unit": "g", "price_per_unit": 0.00109,
    "calories_per_unit": 3.65
  },
  "egg": {
    "unit": "each", "price_per_unit": 0.30,
    "calories_per_unit": 72
  }
}
"""


def test_apply_prices_changes_only_the_named_ingredient():
    updated = json.loads(pull.apply_prices(TEXT, {"rice": (0.00219, "Kroger API, 32 oz at $1.99")}))
    assert updated["rice"]["price_per_unit"] == 0.00219
    assert updated["rice"]["price_source"] == "Kroger API, 32 oz at $1.99"
    assert updated["rice"]["calories_per_unit"] == 3.65
    assert updated["egg"] == {"unit": "each", "price_per_unit": 0.30, "calories_per_unit": 72}


def test_apply_prices_is_repeatable_without_stacking_source_notes():
    once = pull.apply_prices(TEXT, {"rice": (0.00219, "first pull")})
    twice = pull.apply_prices(once, {"rice": (0.0025, "second pull")})
    assert twice.count("price_source") == 1
    assert json.loads(twice)["rice"] == {
        "unit": "g", "price_per_unit": 0.0025, "price_source": "second pull", "calories_per_unit": 3.65,
    }


def test_apply_prices_ignores_unknown_keys():
    assert pull.apply_prices(TEXT, {"saffron": (1.0, "x")}) == TEXT
