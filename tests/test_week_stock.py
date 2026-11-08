"""The fridge drives planning: owned stock is preferred, spoiling stock first,
and a week's purchases are finished on later days."""

from datetime import date, timedelta

import pytest

from macro_optimizer.channels import ChannelAdapter
from macro_optimizer.database import load_food_db
from macro_optimizer.history import PickHistory
from macro_optimizer.menu_planner import DayPlan
from macro_optimizer.optimizer import run_optimizer
from macro_optimizer.orchestrator import plan_week_multichannel
from macro_optimizer.pantry import Pantry
from macro_optimizer.schema import FoodItem
from macro_optimizer.week_stock import WeekStock, urgency

GOALS = {"calories": 270, "protein": 0, "carbs": 0, "fat": 0}
MON = date(2026, 11, 9)


def food(name, price, upc="", spp=0.0, package_price=0.0, kind="", category="Pantry"):
    return FoodItem(name=name, category=category, source="Kroger API", price=price, calories=270,
                    protein=10, carbs=50, fat=2, max_daily=1, upc=upc, servings_per_package=spp,
                    package_price=package_price, kind=kind)


def solve(items, **kwargs):
    df = load_food_db(extra_items=items, include_market=False)
    plan_df, summary = run_optimizer(df, GOALS, budget_cap=10.0, verbose=False, **kwargs)
    return set(plan_df["name"]), summary


def test_optimizer_prefers_owned_stock_over_cheaper_food():
    items = [food("Bagels", 0.58), food("Cheap Oats", 0.30)]
    assert solve(items)[0] == {"Cheap Oats"}
    picked, summary = solve(items, stock_credit={"Bagels": {"servings": 3, "urgency": 0}})
    assert picked == {"Bagels"}
    assert summary["feasible"]


def test_owned_servings_do_not_spend_budget():
    items = [food("Bagels", 5.0)]
    df = load_food_db(extra_items=items, include_market=False)
    plan_df, summary = run_optimizer(df, GOALS, budget_cap=1.0, verbose=False,
                                     stock_credit={"Bagels": {"servings": 1, "urgency": 0}})
    assert summary["feasible"] and list(plan_df["name"]) == ["Bagels"]


def test_expiring_item_is_preferred_among_owned_stock():
    items = [food("Bread", 0.40), food("Bagels", 0.40)]
    credit = {"Bread": {"servings": 2, "urgency": 0.0},
              "Bagels": {"servings": 2, "urgency": urgency((MON + timedelta(days=1)).isoformat(), MON)}}
    assert solve(items, stock_credit=credit)[0] == {"Bagels"}


def test_owned_items_are_exempt_from_rotation_cap():
    history = PickHistory()
    history.record_plan(load_food_db(extra_items=[food("Bagels", 0.58)], include_market=False)
                        .assign(servings=1), on=MON - timedelta(days=1))
    items = [food("Bagels", 0.58), food("Cheap Oats", 0.30)]
    picked, _ = solve(items, history=history, rotation_cap=1, as_of=MON,
                      stock_credit={"Bagels": {"servings": 5, "urgency": 0}})
    assert picked == {"Bagels"}


def test_urgency_grows_as_use_by_nears():
    assert urgency(None, MON) == 0
    assert urgency((MON + timedelta(days=10)).isoformat(), MON) == 0
    assert urgency((MON + timedelta(days=1)).isoformat(), MON) > urgency((MON + timedelta(days=3)).isoformat(), MON)


class _Catalog(ChannelAdapter):
    name = "Cook at Home"
    lead_time_days = 0

    def __init__(self, items):
        self.items = items

    def candidates(self):
        return list(self.items)


def _week(items, stock, days=4, rotation_cap=1):
    plan_days = [DayPlan(on=MON + timedelta(days=d), free_minutes=60) for d in range(days)]
    return plan_week_multichannel(plan_days, [_Catalog(items)], GOALS, 10.0, today=MON,
                                  history=PickHistory(), rotation_cap=rotation_cap,
                                  recency_penalty_weight=5.0, stock=stock)


def _bagged(name, price, upc):
    return food(name, price, upc=upc, spp=6, package_price=round(price * 6, 2), kind=name.lower(), category="Bakery")


WEEK_ITEMS = [_bagged("Bagels", 0.58, "B1"), _bagged("Oats", 0.60, "O1"), _bagged("Rice", 0.61, "R1"),
              _bagged("Soup", 0.62, "S1"), _bagged("Pasta", 0.63, "P1")]


def test_week_finishes_the_package_it_buys():
    # Once a bag is bought on day one its leftovers are free, opening a new
    # bag of something else costs an opening fee, and the closer the bag gets
    # to its use-by the harder the plan leans on it, so the week eats the
    # whole bag (and buys no second one) instead of rotating onto other food.
    results = _week(WEEK_ITEMS, WeekStock(Pantry()), days=7)
    bagel_rows = [r["plan_df"][r["plan_df"]["name"] == "Bagels"] for r in results]
    eaten = sum(int(b["servings"].sum()) for b in bagel_rows)
    from_stock = sum(float(b["from_stock"].sum()) for b in bagel_rows)
    assert eaten == 6 and from_stock == 5  # one bag bought, every bagel eaten


def test_package_fee_weighs_what_opening_a_package_costs():
    items = [food("Six Pack", 0.50, upc="A", spp=6, package_price=3.00),
             food("Single", 0.55, upc="B", spp=1, package_price=0.55)]
    assert solve(items)[0] == {"Six Pack"}  # cheapest per serving
    assert solve(items, package_fee=0.5)[0] == {"Single"}  # but one serving doesn't justify a new six-pack
    # Already owned, the six-pack is free to eat again.
    assert solve(items, package_fee=0.5, stock_credit={"Six Pack": {"servings": 5, "urgency": 0}})[0] == {"Six Pack"}


def test_without_stock_the_rotation_guard_spreads_the_week():
    items = [food("Bagels", 0.58, upc="B1", spp=6, package_price=3.49), food("Oats", 0.60),
             food("Rice", 0.61), food("Soup", 0.62)]
    results = _week(items, None)
    assert len({n for r in results for n in r["plan_df"]["name"]}) == 4


def test_week_starts_from_the_real_fridge_without_changing_it():
    fridge = Pantry()
    fridge.add_lot("upc:O1", "buy:x", 2, use_by=(MON + timedelta(days=30)).isoformat())
    items = [food("Oats", 0.90, upc="O1", spp=10, package_price=9.0), food("Rice", 0.30)]
    results = _week(items, WeekStock(fridge), days=3, rotation_cap=None)
    oats = [r["plan_df"][r["plan_df"]["name"] == "Oats"] for r in results]
    assert set(results[0]["plan_df"]["name"]) == {"Oats"}  # pricier, but already owned
    assert sum(float(o["from_stock"].sum()) for o in oats) == 2
    assert fridge.have("upc:O1") == 2  # planning works on a copy


def test_spoiled_stock_is_not_credited():
    fridge = Pantry()
    fridge.add_lot("upc:B1", "old", 4, use_by=(MON - timedelta(days=1)).isoformat())
    stock = WeekStock(fridge)
    credit, _ = stock.prepare_day(MON, [food("Bagels", 0.58, upc="B1", spp=6, package_price=3.49)])
    assert credit == {}
