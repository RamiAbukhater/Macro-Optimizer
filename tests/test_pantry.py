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
