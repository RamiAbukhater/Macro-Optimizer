"""Content-based taste model for the cold-start swipe survey.

IDEAS.md's "Cold-start taste" section names the problem: 20 swipes out of
a ~1,400 item catalog say nothing about the other 1,380 unless a like or
dislike spreads to similar items, and the cards are only worth showing if
each one tells the model something new. This module does three things,
all off one item-by-item similarity matrix:

1. **Features** (``item_features``). Each item is described by groups of
   labels: its catalog ``kind``, a format (bar, drink, handheld...), meal
   role, flavor (sweet / savory / spicy / plain), cuisine, protein source
   and processing level, all from ``data/taste_features.json`` (per-kind
   defaults, overridden by words in the product name), plus numbers read
   off the row: macro calorie shares, calorie density, price tier, sodium
   density, and vegetarian/vegan flags.

2. **Similarity** (``TasteModel.similarity``). Per group, labels compare by
   cosine and numbers by a Gaussian kernel, then the groups are averaged
   with ``GROUP_WEIGHTS``. Every similarity is in [0, 1] and decomposes
   back into "how much do these two agree on flavor, on cuisine, ...",
   which is what ``explain`` reports.

3. **Card choice and propagation** (``next_card``, ``propagate``). Answers
   so far define, for every item, how much evidence there is near it
   (``kernel = similarity ** SHARPNESS`` summed over answered items) and
   what that evidence says. The next card maximises representativeness x
   (unexplored + mixed signals): with no answers this is greedy coverage
   of the catalog, the french-fries-then-ice-cream behaviour; once a
   region has a like and a dislike near each other, items between them
   score high, probing the edge of what was liked. The same kernel turns
   the answers into a preference score for every item in the catalog.

Cheap enough to rebuild once per process and query per request: the
similarity matrix for ~1,400 items is ~8 MB of float32, and each card or
propagation query is O(items x answers).
"""

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

DATA_DIR = Path(__file__).parent / "data"
FEATURES_PATH = DATA_DIR / "taste_features.json"

FORMATS = [
    "bar", "drink", "cup", "snack", "deli_meat", "canned", "dip", "salad", "entree", "handheld",
    "pizza", "fried", "staple", "cereal", "bread", "spread", "soup", "noodles", "fruit", "produce",
    "patty", "cooked_dish",
]
MEALS = ["breakfast", "main", "snack", "drink"]
FLAVORS = ["sweet", "savory", "spicy", "plain"]
CUISINES = ["american", "mexican", "italian", "asian", "mediterranean", "none"]
PROTEINS = ["chicken", "beef", "turkey", "pork", "fish", "egg", "dairy", "plant", "nuts", "none"]
PROCESSING = ["fresh", "minimal", "processed"]

# Relative say each feature group has in overall similarity. Flavor,
# kind and protein carry the most because they're what a "no thanks" to a
# food is usually about; price and diet flags only break ties.
GROUP_WEIGHTS = {
    "kind": 1.5,
    "format": 1.0,
    "meal": 0.6,
    "flavor": 1.5,
    "cuisine": 1.0,
    "protein": 1.25,
    "processing": 0.5,
    "macros": 1.0,
    "density": 0.4,
    "price": 0.4,
    "diet": 0.4,
}
# Gaussian kernel widths for the numeric groups, in their own units.
MACRO_SIGMA = 0.18   # distance between calorie-share vectors (each in [0, 1])
DENSITY_SIGMA = 0.6  # natural-log units of calories per serving
PRICE_SIGMA = 0.6    # natural-log units of dollars per serving

# kernel = similarity ** SHARPNESS. Overall similarity between unrelated
# foods still sits around 0.3 (they share a price tier, a macro profile),
# so raising it to a power is what makes evidence local: 0.9 -> 0.66,
# 0.6 -> 0.13, 0.3 -> 0.01.
SHARPNESS = 4
# Pseudo-evidence the answers have to outweigh. Keeps a single like from
# claiming the whole catalog: an item with kernel mass e near the answers
# gets e / (e + PRIOR_STRENGTH) of their average verdict.
PRIOR_STRENGTH = 0.3
# Weight on "neighbours disagree" relative to "nobody nearby answered yet".
BOUNDARY_WEIGHT = 0.8
# How many nearest neighbours define an item's representativeness.
DENSITY_NEIGHBOURS = 15
# Items without a photo are still eligible, just a little less likely to
# be dealt, since a placeholder card is a weaker prompt than a photo.
NO_IMAGE_PENALTY = 0.85
# Bonuses smaller than this aren't worth sending to the optimizer.
MIN_BONUS = 0.05


@dataclass(frozen=True)
class Answer:
    name: str
    liked: bool


def load_feature_labels(path: Optional[Path] = None) -> dict:
    with open(path or FEATURES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _word_pattern(phrase: str) -> "re.Pattern":
    return re.compile(r"(?<![a-z])" + re.escape(phrase.lower()) + r"(?![a-z])")


@lru_cache(maxsize=None)
def _compiled(phrase: str) -> "re.Pattern":
    return _word_pattern(phrase)


def _mentions(text: str, phrases: Iterable[str]) -> bool:
    return any(_compiled(p).search(text) for p in phrases)


def _first_match(text: str, options: Dict[str, List[str]]) -> Optional[str]:
    for value, phrases in options.items():
        if _mentions(text, phrases):
            return value
    return None


def item_labels(item: dict, labels: dict) -> dict:
    """The categorical labels for one item: kind defaults, then name-word overrides.

    Returns {"format", "meal", "cuisine", "protein", "processing": str,
    "flavor": [str, ...]}. Flavor is a list because "mango habanero beef
    jerky" really is sweet, spicy and savory at once.
    """
    kinds = labels["kinds"]
    kind = item.get("kind") or ""
    base = dict(kinds.get(kind) or kinds["_default"])
    text = (item.get("name") or "").lower()
    # "Chicken of the Sea" is a tuna brand; matching it as a phrase under
    # fish first is what stops it reading as chicken, but strip it before
    # the other fields look at the name too.
    words = labels["name_words"]

    protein = _first_match(text, words["protein"])
    if protein:
        base["protein"] = protein
    cuisine = _first_match(text, words["cuisine"])
    if cuisine:
        base["cuisine"] = cuisine
    fmt = _first_match(text.replace("chicken of the sea", ""), words["format"])
    if fmt and not kind:
        base["format"] = fmt
    elif fmt and fmt in ("fried", "pizza", "dip", "soup") and base["format"] not in ("bar", "drink"):
        # Kind is a search term, so "frozen meal" can return a pizza and
        # "guacamole" can return salsa; let the clearest name words win.
        base["format"] = fmt
    if not kind:
        meal = _first_match(text, labels.get("recipe_words", {}).get("meal", {}))
        if meal:
            base["meal"] = meal

    flavors = [base["flavor"]]
    for flavor, phrases in words["add_flavor"].items():
        if flavor not in flavors and _mentions(text, phrases):
            flavors.append(flavor)
    if "spicy" in flavors and "savory" not in flavors and "sweet" not in flavors:
        flavors.append("savory")
    if len(flavors) > 1 and "plain" in flavors:
        flavors.remove("plain")
    base["flavor"] = flavors
    return base


def _one_hot(values: Sequence[str], vocabulary: List[str]) -> np.ndarray:
    vec = np.zeros(len(vocabulary), dtype=np.float32)
    for v in values:
        if v in vocabulary:
            vec[vocabulary.index(v)] = 1.0
    return vec


def _macro_shares(item: dict) -> np.ndarray:
    kcal = 4 * item["protein"] + 4 * item["carbs"] + 9 * item["fat"]
    if kcal <= 0:
        return np.array([1 / 3, 1 / 3, 1 / 3], dtype=np.float32)
    return np.array([4 * item["protein"] / kcal, 4 * item["carbs"] / kcal, 9 * item["fat"] / kcal], dtype=np.float32)


def _kind_key(item: dict) -> str:
    """The item's kind, or for a one-of-a-kind item (a recipe) its own name,
    so it matches itself fully on kind and nothing else does."""
    return item.get("kind") or f"_item:{item['name']}"


def item_features(item: dict, labels: dict, kinds: List[str]) -> Dict[str, np.ndarray]:
    """One item's feature groups, each a small vector (see GROUP_WEIGHTS for the names)."""
    tags = item_labels(item, labels)
    return {
        "kind": _one_hot([_kind_key(item)], kinds),
        "format": _one_hot([tags["format"]], FORMATS),
        "meal": _one_hot([tags["meal"]], MEALS),
        "flavor": _one_hot(tags["flavor"], FLAVORS),
        "cuisine": _one_hot([tags["cuisine"]], CUISINES),
        "protein": _one_hot([tags["protein"]], PROTEINS),
        "processing": _one_hot([tags["processing"]], PROCESSING),
        "macros": _macro_shares(item),
        "density": np.array([math.log(max(item["calories"], 1.0))], dtype=np.float32),
        "price": np.array([math.log(max(item["price"], 0.05))], dtype=np.float32),
        "diet": np.array([float(bool(item.get("vegetarian"))), float(bool(item.get("vegan")))], dtype=np.float32),
    }


_CATEGORICAL = ("kind", "format", "meal", "flavor", "cuisine", "protein", "processing", "diet")
_NUMERIC_SIGMA = {"macros": MACRO_SIGMA, "density": DENSITY_SIGMA, "price": PRICE_SIGMA}


def _cosine_matrix(rows: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    unit = np.divide(rows, norms, out=np.zeros_like(rows), where=norms > 0)
    return unit @ unit.T


def _gaussian_matrix(rows: np.ndarray, sigma: float) -> np.ndarray:
    sq = (rows * rows).sum(axis=1)
    dist2 = np.maximum(sq[:, None] + sq[None, :] - 2 * rows @ rows.T, 0)
    return np.exp(-dist2 / (2 * sigma * sigma)).astype(np.float32)


class TasteModel:
    """Similarity over a fixed list of items, plus card choice and propagation on top of it.

    ``items`` are FoodItem-shaped dicts with unique names. Build once per
    catalog and reuse; the constructor does the only O(n^2) work.
    """

    def __init__(self, items: Sequence[dict], labels: Optional[dict] = None):
        labels = labels or load_feature_labels()
        self.items = list(items)
        self.names = [it["name"] for it in self.items]
        self.index = {name: i for i, name in enumerate(self.names)}
        if len(self.index) != len(self.names):
            raise ValueError("TasteModel items must have unique names")
        self.labels = [item_labels(it, labels) for it in self.items]
        kinds = sorted({_kind_key(it) for it in self.items})
        feats = [item_features(it, labels, kinds) for it in self.items]

        total_weight = sum(GROUP_WEIGHTS.values())
        self.group_similarity: Dict[str, np.ndarray] = {}
        sim = np.zeros((len(self.items), len(self.items)), dtype=np.float32)
        for group, weight in GROUP_WEIGHTS.items():
            rows = np.stack([f[group] for f in feats]).astype(np.float32)
            if group in _CATEGORICAL:
                g = _cosine_matrix(rows)
                if group == "diet":
                    # Two items that are both "not vegetarian" agree on diet,
                    # but have zero vectors; cosine would call them unrelated.
                    both_zero = (rows.sum(axis=1)[:, None] == 0) & (rows.sum(axis=1)[None, :] == 0)
                    g = np.where(both_zero, 1.0, g)
            else:
                g = _gaussian_matrix(rows, _NUMERIC_SIGMA[group])
            g = np.clip(g, 0.0, 1.0).astype(np.float32)
            self.group_similarity[group] = g
            sim += (weight / total_weight) * g
        self.similarity = sim
        self.kernel = sim ** SHARPNESS

        # Representativeness: mean similarity to an item's nearest
        # neighbours, so the model leads with a typical frozen pizza rather
        # than an odd one. Square-rooted so sparse corners of the catalog
        # aren't starved of cards entirely.
        k = min(DENSITY_NEIGHBOURS, max(len(self.items) - 1, 1))
        off_diag = sim.copy()
        np.fill_diagonal(off_diag, 0.0)
        if len(self.items) > 1:
            top = -np.partition(-off_diag, k - 1, axis=1)[:, :k]
            density = top.mean(axis=1)
        else:
            density = np.ones(1, dtype=np.float32)
        density = density / density.max() if density.max() > 0 else np.ones_like(density)
        has_image = np.array([bool(it.get("image_url")) for it in self.items])
        self.representativeness = np.sqrt(density) * np.where(has_image, 1.0, NO_IMAGE_PENALTY)

    # --- evidence from answers -------------------------------------------

    def _answer_columns(self, answers: Sequence[Answer]) -> Tuple[List[int], np.ndarray]:
        cols, ys = [], []
        latest = {}
        for a in answers:  # last answer per name wins
            latest[a.name] = a.liked
        for name, liked in latest.items():
            if name in self.index:
                cols.append(self.index[name])
                ys.append(1.0 if liked else -1.0)
        return cols, np.array(ys, dtype=np.float32)

    def evidence(self, answers: Sequence[Answer]) -> Tuple[np.ndarray, np.ndarray]:
        """(mass, verdict) per item: kernel mass of answers near it, and their weighted mean (+1 like, -1 dislike)."""
        cols, ys = self._answer_columns(answers)
        if not cols:
            zeros = np.zeros(len(self.items), dtype=np.float32)
            return zeros, zeros
        k = self.kernel[:, cols]
        mass = k.sum(axis=1)
        verdict = np.divide(k @ ys, mass, out=np.zeros_like(mass), where=mass > 1e-9)
        return mass, verdict

    def uncertainty(self, answers: Sequence[Answer]) -> np.ndarray:
        """Per item, how much one more answer there would teach: unexplored + mixed-signal terms."""
        mass, verdict = self.evidence(answers)
        unexplored = PRIOR_STRENGTH / (PRIOR_STRENGTH + mass)
        confidence = mass / (PRIOR_STRENGTH + mass)
        mixed = (1.0 - verdict ** 2) * confidence  # 1 - mean^2 is the variance of +/-1 labels
        return unexplored + BOUNDARY_WEIGHT * mixed

    # --- card choice -----------------------------------------------------

    def next_card(
        self,
        answers: Sequence[Answer],
        exclude: Iterable[str] = (),
        pool: Optional[Iterable[str]] = None,
        rng: Optional[np.random.Generator] = None,
        tie_band: float = 0.95,
    ) -> Optional[Tuple[str, str]]:
        """(item name, reason) for the most informative card to show next, or None if the pool is exhausted.

        Picks uniformly among the items scoring within ``tie_band`` of the
        best, so two users with the same answers needn't see the identical
        deck. Pass ``rng=None`` for a deterministic argmax.
        """
        score = self.representativeness * self.uncertainty(answers)
        eligible = np.zeros(len(self.items), dtype=bool)
        if pool is None:
            eligible[:] = True
        else:
            for name in pool:
                if name in self.index:
                    eligible[self.index[name]] = True
        for name in list(exclude) + [a.name for a in answers]:
            if name in self.index:
                eligible[self.index[name]] = False
        if not eligible.any():
            return None
        masked = np.where(eligible, score, -np.inf)
        best = masked.max()
        if rng is None:
            choice = int(np.argmax(masked))
        else:
            candidates = np.flatnonzero(masked >= best * tie_band)
            choice = int(rng.choice(candidates))
        return self.names[choice], self._reason(choice, answers)

    def _reason(self, i: int, answers: Sequence[Answer]) -> str:
        mass, verdict = self.evidence(answers)
        if not answers or mass[i] < PRIOR_STRENGTH:
            return "new ground"
        if abs(verdict[i]) < 0.6:
            return "between things you liked and didn't"
        return "refining a region you've rated"

    def covering_set(self, n: int, pool: Optional[Iterable[str]] = None) -> List[str]:
        """A non-adaptive, spread-out set of ``n`` items: greedy coverage, no answers needed.

        Each pick counts as evidence (with an arbitrary verdict, it only
        affects the "unexplored" term, the mixed term is then zeroed since
        all answers agree), so the next pick lands far from everything
        chosen so far. Equivalent to farthest-point sampling weighted by
        representativeness.
        """
        chosen: List[str] = []
        for _ in range(n):
            nxt = self.next_card([Answer(c, True) for c in chosen], pool=pool)
            if nxt is None:
                break
            chosen.append(nxt[0])
        return chosen

    def coverage(self, answers: Sequence[Answer]) -> float:
        """Share of the catalog with real evidence nearby, in [0, 1]. Drives early stopping."""
        mass, _ = self.evidence(answers)
        return float((mass / (PRIOR_STRENGTH + mass)).mean())

    # --- propagation -----------------------------------------------------

    def propagate(self, answers: Sequence[Answer], amount: float, min_bonus: float = MIN_BONUS) -> Dict[str, float]:
        """{item name: preference bonus} for the whole catalog from a handful of answers.

        ``amount * mass / (mass + PRIOR_STRENGTH) * verdict``: near many
        consistent answers the bonus approaches +/- ``amount``; far from
        every answer it shrinks to 0. An answered item itself always gets
        the full +/- ``amount``. Answers for names outside the model are
        passed through at full strength so nothing a user swiped is lost.
        """
        mass, verdict = self.evidence(answers)
        scores = amount * verdict * mass / (mass + PRIOR_STRENGTH)
        bonus = {self.names[i]: round(float(s), 3) for i, s in enumerate(scores) if abs(s) >= min_bonus}
        latest = {}
        for a in answers:
            latest[a.name] = a.liked
        for name, liked in latest.items():
            bonus[name] = amount if liked else -amount
        return bonus

    def explain(self, a: str, b: str) -> Dict[str, float]:
        """Per-group similarity between two items, for debugging and "why this card" text."""
        i, j = self.index[a], self.index[b]
        return {group: round(float(m[i, j]), 3) for group, m in self.group_similarity.items()}


def answers_from_picks(picks: Iterable[dict]) -> List[Answer]:
    """[{name, liked}, ...] from a request body or stored blob, silently skipping malformed entries."""
    out = []
    for p in picks or []:
        if isinstance(p, dict) and "name" in p and "liked" in p:
            out.append(Answer(str(p["name"]), bool(p["liked"])))
    return out


# Kroger's product names arrive with a mis-decoded trademark sign
# (UTF-8 "™"/"®" read as cp1252, e.g. "Joseph'sâ„¢"). Card titles strip
# those and the real symbols too; the item's ``name`` stays the key.
_MARK_PATTERN = re.compile("(â„¢|Â®|™|®|�)")
_APOSTROPHE_PATTERN = re.compile("(â€™|’)")


def display_name(name: str) -> str:
    """A product name cleaned up for a card title."""
    cleaned = _MARK_PATTERN.sub("", _APOSTROPHE_PATTERN.sub("'", name))
    return re.sub(r"\s{2,}", " ", cleaned).strip()
