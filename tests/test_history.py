from datetime import date, timedelta

import pandas as pd
import pytest

from macro_optimizer.history import PickHistory


def test_add_pick_and_counts_in_window():
    history = PickHistory()
    today = date.today()
    history.add_pick("Tuna Packet", on=today)
    history.add_pick("Tuna Packet", on=today - timedelta(days=1))
    counts = history.counts_in_window(7, as_of=today)
    assert counts["Tuna Packet"] == 2


def test_counts_in_window_excludes_old_entries():
    history = PickHistory()
    today = date.today()
    history.add_pick("Tuna Packet", on=today - timedelta(days=30))
    counts = history.counts_in_window(7, as_of=today)
    assert "Tuna Packet" not in counts


def test_recency_penalty_scales_with_occurrences():
    history = PickHistory()
    today = date.today()
    for _ in range(3):
        history.add_pick("Banana", on=today)
    penalties = history.recency_penalty(window_days=7, penalty_per_occurrence=1.5, as_of=today)
    assert penalties["Banana"] == pytest.approx(4.5)


def test_recency_penalty_zero_for_unpicked_items():
    history = PickHistory()
    penalties = history.recency_penalty(as_of=date.today())
    assert penalties == {}


def test_rotation_cap_exclusions_triggers_at_threshold():
    history = PickHistory()
    today = date.today()
    for _ in range(3):
        history.add_pick("Banana", on=today)
    excluded = history.rotation_cap_exclusions(window_days=7, max_appearances=3, as_of=today)
    assert "Banana" in excluded


def test_rotation_cap_exclusions_below_threshold_not_excluded():
    history = PickHistory()
    today = date.today()
    history.add_pick("Banana", on=today)
    history.add_pick("Banana", on=today)
    excluded = history.rotation_cap_exclusions(window_days=7, max_appearances=3, as_of=today)
    assert "Banana" not in excluded


def test_record_plan_logs_every_row():
    history = PickHistory()
    plan_df = pd.DataFrame([
        {"name": "Banana", "servings": 2},
        {"name": "Tuna Packet", "servings": 1},
    ])
    history.record_plan(plan_df, on=date.today())
    counts = history.counts_in_window(7, as_of=date.today())
    assert counts["Banana"] == 1
    assert counts["Tuna Packet"] == 1


def test_persistence_round_trip(tmp_path):
    path = tmp_path / "history.json"
    history = PickHistory(path)
    history.add_pick("Banana", on=date.today())

    reloaded = PickHistory(path)
    counts = reloaded.counts_in_window(7, as_of=date.today())
    assert counts["Banana"] == 1
