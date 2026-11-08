from datetime import date, timedelta

import pytest

from macro_optimizer.pantry import Pantry


def test_add_and_have():
    pantry = Pantry()
    pantry.add("rice", 500)
    assert pantry.have("rice") == 500


def test_add_accumulates():
    pantry = Pantry()
    pantry.add("rice", 500)
    pantry.add("rice", 200)
    assert pantry.have("rice") == 700


def test_have_unknown_ingredient_is_zero():
    pantry = Pantry()
    assert pantry.have("unobtainium") == 0.0


def test_offset_partial_coverage():
    pantry = Pantry()
    pantry.add("rice", 150)
    assert pantry.offset("rice", 200) == pytest.approx(50)


def test_offset_full_coverage():
    pantry = Pantry()
    pantry.add("rice", 500)
    assert pantry.offset("rice", 200) == 0.0


def test_offset_no_pantry_entry_needs_full_amount():
    pantry = Pantry()
    assert pantry.offset("rice", 200) == 200


def test_consume_decrements_quantity():
    pantry = Pantry()
    pantry.add("rice", 500)
    pantry.consume("rice", 200)
    assert pantry.have("rice") == 300


def test_consume_does_not_go_negative():
    pantry = Pantry()
    pantry.add("rice", 100)
    pantry.consume("rice", 500)
    assert pantry.have("rice") == 0.0


def test_expiring_within_window_sorted_soonest_first():
    pantry = Pantry()
    today = date.today()
    pantry.add("spinach", 100, expires_on=(today + timedelta(days=5)).isoformat())
    pantry.add("chicken", 200, expires_on=(today + timedelta(days=1)).isoformat())
    pantry.add("rice", 500, expires_on=(today + timedelta(days=90)).isoformat())

    due = pantry.expiring_within(7, as_of=today)
    assert due == ["chicken", "spinach"]


def test_expiring_within_ignores_no_expiry():
    pantry = Pantry()
    pantry.add("rice", 500)
    assert pantry.expiring_within(7) == []


def test_persistence_round_trip(tmp_path):
    path = tmp_path / "pantry.json"
    pantry = Pantry(path)
    pantry.add("rice", 300, expires_on="2026-12-31")

    reloaded = Pantry(path)
    assert reloaded.have("rice") == 300


def test_set_quantity_overwrites_rather_than_accumulates():
    pantry = Pantry()
    pantry.add("rice", 500)
    pantry.set_quantity("rice", 200)
    assert pantry.have("rice") == 200


def test_set_quantity_creates_new_entry():
    pantry = Pantry()
    pantry.set_quantity("rice", 150, expires_on="2026-01-01")
    assert pantry.have("rice") == 150
    assert pantry.list_items()["rice"].expires_on == "2026-01-01"


def test_set_quantity_rejects_negative():
    pantry = Pantry()
    with pytest.raises(ValueError):
        pantry.set_quantity("rice", -5)


def test_remove_drops_ingredient():
    pantry = Pantry()
    pantry.add("rice", 500)
    pantry.remove("rice")
    assert pantry.have("rice") == 0.0
    assert "rice" not in pantry.list_items()


def test_remove_unknown_ingredient_is_noop():
    pantry = Pantry()
    pantry.remove("unobtainium")  # should not raise


def test_list_items_returns_all_entries():
    pantry = Pantry()
    pantry.add("rice", 500)
    pantry.add("chicken", 200, expires_on="2026-01-01")
    items = pantry.list_items()
    assert set(items) == {"rice", "chicken"}
    assert items["chicken"].expires_on == "2026-01-01"


def test_list_items_is_a_copy():
    pantry = Pantry()
    pantry.add("rice", 500)
    items = pantry.list_items()
    items["rice"].quantity = 999
    assert pantry.have("rice") == 500


def test_set_quantity_stores_custom_ingredient_metadata():
    pantry = Pantry()
    pantry.set_quantity("salmon_fillet", 400, unit="g", display_name="Salmon Fillet", source="USDA FoodData Central")
    entry = pantry.list_items()["salmon_fillet"]
    assert entry.unit == "g"
    assert entry.display_name == "Salmon Fillet"
    assert entry.source == "USDA FoodData Central"


def test_set_quantity_defaults_custom_metadata_to_none():
    pantry = Pantry()
    pantry.set_quantity("rice", 200)
    entry = pantry.list_items()["rice"]
    assert entry.unit is None
    assert entry.display_name is None
    assert entry.source is None


def test_custom_ingredient_metadata_round_trips_through_persistence(tmp_path):
    path = tmp_path / "pantry.json"
    pantry = Pantry(path)
    pantry.set_quantity("salmon_fillet", 400, unit="g", display_name="Salmon Fillet", source="Open Food Facts")

    reloaded = Pantry(path)
    entry = reloaded.list_items()["salmon_fillet"]
    assert entry.unit == "g"
    assert entry.display_name == "Salmon Fillet"
    assert entry.source == "Open Food Facts"


# --- purchase lots ---

def test_add_lot_is_idempotent_by_id():
    pantry = Pantry()
    pantry.add_lot("upc:1", "buy:w:product:1", 6, purchased_on="2026-11-08", use_by="2026-11-14")
    pantry.add_lot("upc:1", "buy:w:product:1", 6, purchased_on="2026-11-08", use_by="2026-11-14")
    assert pantry.have("upc:1") == 6


def test_remove_lot_undoes_only_that_purchase():
    pantry = Pantry()
    pantry.add_lot("upc:1", "a", 6)
    pantry.add_lot("upc:1", "b", 2)
    pantry.remove_lot("upc:1", "a")
    assert pantry.have("upc:1") == 2
    pantry.remove_lot("upc:1", "b")
    assert "upc:1" not in pantry.list_items()


def test_consume_eats_soonest_expiring_lot_first():
    pantry = Pantry()
    pantry.add_lot("milk", "late", 1000, use_by="2026-11-20")
    pantry.add_lot("milk", "soon", 500, use_by="2026-11-10")
    pantry.consume("milk", 600)
    lots = {l["id"]: l["quantity"] for l in pantry.lots("milk")}
    assert lots == {"late": 900}
    assert pantry.list_items()["milk"].expires_on == "2026-11-20"


def test_legacy_entry_without_lots_round_trips():
    pantry = Pantry(entries={"rice": {"quantity": 500, "expires_on": "2026-12-01"}})
    assert pantry.have("rice") == 500
    again = Pantry(entries=pantry.to_dict())
    assert again.have("rice") == 500 and again.list_items()["rice"].expires_on == "2026-12-01"


def test_have_can_exclude_lots_by_prefix():
    pantry = Pantry()
    pantry.add_lot("upc:1", "old", 2)
    pantry.add_lot("upc:1", "buy:2026-11-08:x", 6)
    assert pantry.have("upc:1", exclude_prefix="buy:2026-11-08:") == 2
