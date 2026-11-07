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


def test_covering_set_spans_many_kinds_and_never_repeats_one(model):
    kinds = [_kind(model, n) for n in model.covering_set(20)]
    assert len(set(kinds)) == 20


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
    sushi = [n for n in model.names if _kind(model, n) == "sushi"]
    bonus = model.propagate([Answer(sushi[0], False)], amount=2.0)
    assert bonus[sushi[0]] == -2.0
    assert np.mean([bonus.get(n, 0.0) for n in sushi[1:]]) < -0.5


def test_propagated_bonus_never_exceeds_the_swipe_amount(model):
    deck = model.covering_set(10)
    bonus = model.propagate([Answer(n, i % 2 == 0) for i, n in enumerate(deck)], amount=2.0)
    assert max(abs(v) for v in bonus.values()) <= 2.0
    assert len(bonus) > 10 * 20  # far more than the swiped items themselves


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


# --- concept cards ------------------------------------------------------

from macro_optimizer.taste import (  # noqa: E402
    CONCEPT_PREFIX,
    CUISINES,
    FLAVORS,
    FORMAT_SLOTS,
    FORMATS,
    MEALS,
    PROCESSING,
    PROTEINS,
    concept_pool,
    load_concepts,
)


@pytest.fixture(scope="module")
def concepts():
    return load_concepts()


@pytest.fixture(scope="module")
def cmodel(catalog, concepts):
    return TasteModel(catalog + concepts)


def _c(cid):
    return CONCEPT_PREFIX + cid


def _sim(model, a, b):
    return float(model.similarity[model.index[a], model.index[b]])


def test_every_concept_tag_is_in_the_models_vocabulary(concepts):
    ids = [c["concept"]["id"] for c in concepts]
    assert len(ids) == len(set(ids))
    dishes = [c["concept"] for c in concepts if c["concept"]["card"] == "dish"]
    formats = [c["concept"] for c in concepts if c["concept"]["card"] == "format"]
    assert len(dishes) >= 60 and 3 <= len(formats) <= 6
    for d in dishes:
        assert d["meal"] in MEALS and d["cuisine"] in CUISINES, d["id"]
        assert d["flavor"] and set(d["flavor"]) <= set(FLAVORS), d["id"]
        assert d["protein"] and set(d["protein"]) <= set(PROTEINS), d["id"]
        assert d["kcal"] > 0 and min(d["p"], d["c"], d["f"]) >= 0, d["id"]
        assert not (d["vegan"] and not d["veg"]), d["id"]
        assert d["veg"] == (not set(d["protein"]) & {"chicken", "beef", "turkey", "pork", "fish"}), d["id"]
    for f in formats:
        assert f["format"] and set(f["format"]) <= set(FORMATS), f["id"]
        assert f["processing"] and set(f["processing"]) <= set(PROCESSING), f["id"]


def test_concepts_leave_catalog_to_catalog_similarity_unchanged(model, cmodel):
    a, b = model.names[0], model.names[200]
    assert _sim(cmodel, a, b) == pytest.approx(_sim(model, a, b), abs=1e-5)


def test_dish_and_format_cards_are_unrelated(cmodel):
    assert _sim(cmodel, _c("chicken_tacos"), _c("frozen_meals")) == 0.0


def test_a_dish_card_ignores_packaging_and_a_format_card_ignores_cuisine(cmodel):
    frozen_pizza = _first_of_kind(cmodel, "frozen pizza")
    frozen_burrito = _first_of_kind(cmodel, "frozen burrito")
    # The pizza card can't tell a frozen pizza from fresh, so cheese pizzas are a strong match.
    pizzas = [n for n in cmodel.names if _kind(cmodel, n) == "frozen pizza"]
    assert max(_sim(cmodel, _c("margherita_pizza"), p) for p in pizzas) > 0.85
    # Frozen meals reach frozen pizza and frozen burritos alike.
    assert _sim(cmodel, _c("frozen_meals"), frozen_pizza) == pytest.approx(1.0)
    assert _sim(cmodel, _c("frozen_meals"), frozen_burrito) == pytest.approx(1.0)
    assert _sim(cmodel, _c("frozen_meals"), _first_of_kind(cmodel, "baby carrots")) < 0.1


def test_concept_answers_never_appear_in_the_bonus(cmodel):
    bonus = cmodel.propagate([Answer(_c("pancakes"), True), Answer(_c("bars_shakes"), False)], 2.0)
    assert bonus and not any(n.startswith(CONCEPT_PREFIX) for n in bonus)
    assert bonus.get(_first_of_kind(cmodel, "protein bar"), 0) < -1.0


def test_concept_pool_reserves_format_slots_and_falls_back_to_dishes(cmodel, concepts):
    names = [c["name"] for c in concepts]
    fmt = lambda n: cmodel.items[cmodel.index[n]]["concept"]["card"] == "format"
    assert not any(fmt(n) for n in concept_pool(names, cmodel, []))
    slot = FORMAT_SLOTS[0]
    answers = [Answer(n, True) for n in names[:slot]]
    assert all(fmt(n) for n in concept_pool(names, cmodel, answers))
    all_formats = [Answer(n, True) for n in names if fmt(n)]
    padded = all_formats + [Answer(n, True) for n in names if not fmt(n)][: slot - len(all_formats)] if slot >= len(all_formats) else all_formats
    pool = concept_pool(names, cmodel, padded)
    assert pool and not any(fmt(n) for n in pool)
    assert not set(pool) & {a.name for a in padded}


def test_coverage_counts_only_catalog_items(cmodel):
    assert cmodel.coverage([]) == 0.0
    assert 0 < cmodel.coverage([Answer(_c("chicken_stir_fry"), True)]) < 1


def test_concept_photos_are_openly_licensed_attributed_and_on_disk(concepts):
    import re
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "webapp" / "static"  # served at the site root
    for item in concepts:
        image = item["concept"].get("image")
        if image is None:
            continue  # styled placeholder
        cid = item["concept"]["id"]
        assert image["url"].startswith("/img/concepts/"), cid
        assert (static / image["url"].lstrip("/")).is_file(), cid
        assert image["page"].startswith("https://commons.wikimedia.org/"), cid
        assert image["author"] and image["source"] == "Wikimedia Commons", cid
        assert re.match(r"(CC0|Public domain|PD|CC BY(-SA)? \d)", image["license"], re.I), (cid, image["license"])
        assert item["image_url"] == image["url"], cid
