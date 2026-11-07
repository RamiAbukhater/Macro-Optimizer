import numpy as np
import pytest

from macro_optimizer.database import load_market_items
from macro_optimizer.recipes import recipe_food_items
from macro_optimizer.taste import (
    Answer,
    TasteModel,
    answers_from_picks,
    display_name,
    item_labels,
    load_feature_labels,
)


@pytest.fixture(scope="module")
def catalog():
    return load_market_items() + [r.to_dict() for r in recipe_food_items()]


@pytest.fixture(scope="module")
def model(catalog):
    return TasteModel(catalog)


def _kind(model, name):
    return model.items[model.index[name]].get("kind") or "recipe"


def _first_of_kind(model, kind, skip=0):
    return [n for n in model.names if _kind(model, n) == kind][skip]


def _mean_pairwise_similarity(model, names):
    idx = [model.index[n] for n in names]
    sub = model.similarity[np.ix_(idx, idx)]
    return (sub.sum() - np.trace(sub)) / (len(idx) * (len(idx) - 1))


# --- features ---------------------------------------------------------


def test_name_words_override_kind_defaults():
    labels = load_feature_labels()
    tuna = item_labels({"name": "Chicken of the Sea Chunk Light Tuna", "kind": "canned chicken"}, labels)
    assert tuna["protein"] == "fish"

    nuggets = item_labels({"name": "Impossible Plant-Based Chicken Nuggets", "kind": "frozen chicken strips"}, labels)
    assert nuggets["protein"] == "plant"

    jerky = item_labels({"name": "Archer Mango Habanero Beef Jerky", "kind": "beef jerky"}, labels)
    assert set(jerky["flavor"]) >= {"savory", "sweet", "spicy"}


def test_items_without_a_kind_get_default_labels_and_recipe_meal_words():
    labels = load_feature_labels()
    oats = item_labels({"name": "Peanut Butter Banana Overnight Oats", "kind": ""}, labels)
    assert oats["meal"] == "breakfast"
    assert oats["format"] == "cooked_dish"


def test_similarity_is_symmetric_bounded_and_one_on_the_diagonal(model):
    s = model.similarity
    assert np.allclose(s, s.T, atol=1e-5)
    assert s.min() >= 0 and s.max() <= 1.0 + 1e-5
    assert np.allclose(np.diag(s), 1.0, atol=1e-4)


def test_same_kind_items_are_more_similar_than_unrelated_ones(model):
    pizza_a, pizza_b = _first_of_kind(model, "frozen pizza"), _first_of_kind(model, "frozen pizza", 1)
    juice = _first_of_kind(model, "orange juice")
    i, j, k = model.index[pizza_a], model.index[pizza_b], model.index[juice]
    assert model.similarity[i, j] > 0.7
    assert model.similarity[i, k] < 0.4
    assert model.explain(pizza_a, juice)["flavor"] == 0.0


def test_duplicate_names_are_rejected():
    row = {"name": "x", "category": "c", "source": "s", "price": 1, "calories": 100, "protein": 1, "carbs": 1, "fat": 1}
    with pytest.raises(ValueError):
        TasteModel([row, dict(row)])


# --- card choice: diversity -------------------------------------------


def test_covering_set_is_spread_out_compared_to_random_draws(model):
    chosen = model.covering_set(20)
    assert len(chosen) == len(set(chosen)) == 20

    rng = np.random.default_rng(0)
    random_means = [
        _mean_pairwise_similarity(model, list(rng.choice(model.names, 20, replace=False))) for _ in range(30)
    ]
    assert _mean_pairwise_similarity(model, chosen) < min(random_means)


def test_covering_set_spans_many_kinds_and_rarely_repeats_one(model):
    # The test catalog is small (43 kinds, 3 near-identical brands each), so
    # allow one repeat; on the real ~1,600-product catalog it spans 20 kinds.
    kinds = [_kind(model, n) for n in model.covering_set(20)]
    assert len(set(kinds)) >= 19


def test_covering_set_respects_the_pool(model):
    pool = [n for n in model.names if _kind(model, n) in ("frozen pizza", "sushi", "milk")]
    assert set(model.covering_set(3, pool=pool)) <= set(pool)


# --- card choice: adaptiveness ----------------------------------------


def test_next_card_leaves_a_region_once_it_has_been_answered(model):
    fries_like = _first_of_kind(model, "frozen chicken strips")
    nxt, reason = model.next_card([Answer(fries_like, True)])
    assert _kind(model, nxt) != "frozen chicken strips"
    assert model.similarity[model.index[nxt], model.index[fries_like]] < 0.5
    assert reason == "new ground"


def test_next_card_depends_on_the_answers(model):
    first = model.covering_set(6)
    seen = {model.next_card([Answer(n, liked) for n, liked in zip(first, pattern)])[0]
            for pattern in ([True] * 6, [False] * 6, [True, False] * 3, [False, True] * 3)}
    assert not seen & set(first)
    # Same items answered, different verdicts: the model's view of where it
    # is still unsure has to change with them.
    mixed = model.uncertainty([Answer(n, liked) for n, liked in zip(first, [True, False] * 3)])
    agree = model.uncertainty([Answer(n, True) for n in first])
    assert not np.allclose(mixed, agree)


def test_a_like_and_a_dislike_close_together_mark_a_boundary_worth_probing(model):
    pizzas = [n for n in model.names if _kind(model, n) == "frozen pizza"]
    a, b, rest = pizzas[0], pizzas[1], pizzas[2:]
    rest_idx = [model.index[n] for n in rest]

    agree = model.uncertainty([Answer(a, True), Answer(b, True)])[rest_idx].mean()
    disagree = model.uncertainty([Answer(a, True), Answer(b, False)])[rest_idx].mean()
    assert disagree > agree


def test_next_card_with_a_seed_is_repeatable_and_returns_none_when_pool_is_used_up(model):
    answers = [Answer(model.names[0], True)]
    one = model.next_card(answers, rng=np.random.default_rng(5))
    two = model.next_card(answers, rng=np.random.default_rng(5))
    assert one == two
    assert model.next_card([], pool=[model.names[0]], exclude=[model.names[0]]) is None


def test_coverage_grows_with_answers(model):
    deck = model.covering_set(20)
    assert model.coverage([]) == 0
    assert model.coverage([Answer(n, True) for n in deck[:5]]) < model.coverage([Answer(n, True) for n in deck])


# --- propagation ------------------------------------------------------


def test_a_like_spreads_to_similar_items_and_not_to_unrelated_ones(model):
    pizzas = [n for n in model.names if _kind(model, n) == "frozen pizza"]
    juices = [n for n in model.names if _kind(model, n) == "orange juice"]
    bonus = model.propagate([Answer(pizzas[0], True)], amount=2.0)

    assert bonus[pizzas[0]] == 2.0
    pizza_bonus = np.mean([bonus.get(n, 0.0) for n in pizzas[1:]])
    juice_bonus = np.mean([bonus.get(n, 0.0) for n in juices])
    assert pizza_bonus > 0.5
    assert juice_bonus < 0.05


def test_a_dislike_spreads_as_a_penalty(model):
    hummus = [n for n in model.names if _kind(model, n) == "hummus"]
    bonus = model.propagate([Answer(hummus[0], False)], amount=2.0)
    assert bonus[hummus[0]] == -2.0
    assert np.mean([bonus.get(n, 0.0) for n in hummus[1:]]) < -0.5


def test_propagated_bonus_never_exceeds_the_swipe_amount(model):
    deck = model.covering_set(10)
    bonus = model.propagate([Answer(n, i % 2 == 0) for i, n in enumerate(deck)], amount=2.0)
    assert max(abs(v) for v in bonus.values()) <= 2.0
    assert len(bonus) > 5 * len(deck)  # far more than the swiped items themselves


def test_later_answers_win_and_unknown_names_pass_through(model):
    name = model.names[0]
    bonus = model.propagate([Answer(name, True), Answer(name, False), Answer("Not In Catalog", True)], amount=2.0)
    assert bonus[name] == -2.0
    assert bonus["Not In Catalog"] == 2.0


def test_answers_from_picks_skips_malformed_entries():
    answers = answers_from_picks([{"name": "a", "liked": True}, {"name": "b"}, "junk", {"liked": False}])
    assert answers == [Answer("a", True)]


def test_display_name_strips_mis_decoded_trademark_signs():
    assert display_name("Joseph'sâ„¢ Flax Pita") == "Joseph's Flax Pita"
    assert display_name("Boarâ€™s Head Turkey") == "Boar's Head Turkey"
    assert display_name("Kroger® 100% Whole Wheat Bread") == "Kroger 100% Whole Wheat Bread"
