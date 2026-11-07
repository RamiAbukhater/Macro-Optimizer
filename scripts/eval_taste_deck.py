"""Offline check: do concept-card swipes rank the catalog better than product-card swipes?

Simulates a handful of synthetic personas whose true taste is a known
function of the same labels taste.py uses (cuisine, protein, flavor, meal,
format, processing). Each persona answers an adaptive survey twice:

* **SKU deck**: the old survey, cards are catalog products and recipes.
  A product swipe also reacts to things that aren't taste (brand, photo,
  package, price), modelled as extra Gaussian noise ``--sku-noise``.
* **Concept deck**: dish cards plus format cards in their slots
  (taste.concept_pool). Dish cards carry no format, so the persona's
  format/processing likes only show up on the format cards.

Each answer set is propagated over the catalog (TasteModel.propagate) and
compared with the persona's true ranking: P@20 is the share of the predicted
top 20 that are in the persona's true top 10%; Bad@20 is the share the
persona would actually reject (true utility < 0).

Caveat: the personas are defined on the model's own labels, so this tests
whether the deck *asks the right questions* under the model's view of food,
not whether the labels are right. It justifies the design; it isn't proof.

The catalog comes from ``load_market_items`` (grocery.db, priced live by
Kroger), so this needs a populated grocery.db and Kroger credentials; with
neither it has nothing to rank. The numbers in the commit that added this
(concept P@20 0.61 vs product 0.42) were run on the earlier 1,361-item
kroger_items.json catalog.

A persona swipes yes when utility + noise clears ``--like-threshold``: a
neutral food needs a reason to get a yes, as in a real survey.

Usage: PYTHONPATH=src py scripts/eval_taste_deck.py [--seeds 10] [--sku-noise 0.8]
"""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macro_optimizer.database import load_market_items  # noqa: E402
from macro_optimizer.recipes import recipe_food_items  # noqa: E402
from macro_optimizer.taste import Answer, TasteModel, concept_pool, load_concepts  # noqa: E402

MEATS = {"chicken": 1, "beef": 1, "turkey": 1, "pork": 1, "fish": 1}

# Weight per label value; anything unlisted counts 0. Keys: cuisine,
# protein, flavor, meal, format, processing.
PERSONAS = {
    "Spicy Mexican, no fish, avoids frozen": {
        "cuisine": {"mexican": 2.0}, "flavor": {"spicy": 1.0}, "protein": {"fish": -3.0},
        "processing": {"processed": -1.5},
    },
    "Vegetarian-leaning Italian/Med": {
        "cuisine": {"italian": 1.5, "mediterranean": 1.5},
        "protein": {"plant": 1.0, "dairy": 1.0, **{m: -2.0 for m in MEATS}},
    },
    "Convenience eater, American": {
        "processing": {"processed": 1.5, "fresh": -1.0}, "cuisine": {"american": 1.0},
        "format": {"produce": -2.0, "salad": -2.0}, "flavor": {"plain": -0.5},
    },
    "Asian food, fish and chicken, no sweets": {
        "cuisine": {"asian": 2.0}, "protein": {"fish": 1.0, "chicken": 1.0}, "flavor": {"sweet": -1.5},
    },
    "Breakfast and sweets, no spice": {
        "meal": {"breakfast": 1.5}, "flavor": {"sweet": 1.5, "spicy": -1.5},
    },
    "High-protein, cooks from scratch": {
        "protein": {"chicken": 1.0, "egg": 1.0, "turkey": 1.0, "fish": 0.5},
        "processing": {"fresh": 1.5, "processed": -1.5}, "flavor": {"sweet": -1.0},
    },
}
GROUPS = ("cuisine", "protein", "flavor", "meal", "format", "processing")


def utility(persona: dict, labels: dict) -> float:
    """Mean weight over each group's values (a list for multi-valued labels), summed over groups."""
    total = 0.0
    for group in GROUPS:
        values = labels.get(group)
        values = values if isinstance(values, list) else [values]
        values = [v for v in values if v is not None]
        if values:
            total += float(np.mean([persona.get(group, {}).get(v, 0.0) for v in values]))
    return total


def run_survey(model, persona, pool_fn, n_cards, noise, rng, threshold=0.25):
    answers = []
    for _ in range(n_cards):
        nxt = model.next_card(answers, pool=pool_fn(answers), rng=rng)
        if nxt is None:
            break
        name = nxt[0]
        u = utility(persona, model.labels[model.index[name]])
        answers.append(Answer(name, u + rng.normal(0, noise) > threshold))
    return answers


def score(model, persona, answers, targets, rng):
    true = np.array([utility(persona, model.labels[i]) for i in targets])
    relevant = true >= np.quantile(true, 0.9)
    bonus = model.propagate(answers, 1.0, min_bonus=0.0)
    pred = np.array([bonus.get(model.names[i], 0.0) for i in targets]) + rng.uniform(0, 1e-6, len(targets))
    top = np.argsort(-pred)[:20]
    return relevant[top].mean(), (true[top] < 0).mean()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--sku-noise", type=float, default=0.8, help="extra non-taste noise on product swipes")
    ap.add_argument("--base-noise", type=float, default=0.3, help="ordinary answer noise on every swipe")
    ap.add_argument("--like-threshold", type=float, default=0.25,
                    help="utility a card needs to get a yes; neutral foods (utility 0) get one ~20%% of the time")
    args = ap.parse_args()

    catalog = load_market_items() + [r.to_dict() for r in recipe_food_items()]
    concepts = load_concepts()
    model = TasteModel(catalog + concepts)
    targets = [i for i in range(len(model.items)) if not model.is_concept[i]]
    sku_pool = [model.names[i] for i in targets]
    concept_names = [c["name"] for c in concepts]

    decks = {
        "SKU": (lambda answers: sku_pool, np.hypot(args.base_noise, args.sku_noise)),
        "SKU (no brand noise)": (lambda answers: sku_pool, args.base_noise),
        "Concept": (lambda answers: concept_pool(concept_names, model, answers), args.base_noise),
    }
    rows = []
    for pname, persona in PERSONAS.items():
        for n_cards in (12, 20):
            for dname, (pool_fn, noise) in decks.items():
                p20, bad = [], []
                for seed in range(args.seeds):
                    rng = np.random.default_rng([seed, n_cards])
                    answers = run_survey(model, persona, pool_fn, n_cards, noise, rng, args.like_threshold)
                    p, b = score(model, persona, answers, targets, rng)
                    p20.append(p)
                    bad.append(b)
                rows.append((pname, n_cards, dname, np.mean(p20), np.mean(bad)))

    print(f"seeds={args.seeds}  base_noise={args.base_noise}  sku_noise={args.sku_noise}")
    print(f"{'persona':42} {'cards':>5} {'deck':22} {'P@20':>6} {'Bad@20':>7}")
    for r in rows:
        print(f"{r[0]:42} {r[1]:>5} {r[2]:22} {r[3]:6.2f} {r[4]:7.2f}")
    print("\nMean over personas")
    for n_cards in (12, 20):
        for dname in decks:
            sel = [r for r in rows if r[1] == n_cards and r[2] == dname]
            print(f"  {n_cards:>2} cards  {dname:22} P@20 {np.mean([r[3] for r in sel]):.2f}  Bad@20 {np.mean([r[4] for r in sel]):.2f}")


if __name__ == "__main__":
    main()
