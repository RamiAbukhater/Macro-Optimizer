"""Turns free-text feedback/deviation messages into structured hints.

Users won't file a "recency penalty override" ticket, they'll type "I'm
sick of beef, we had it again Thursday" or "skipped the chicken bowl,
went out for a burger instead." This module bridges that gap: keyword
rules classify the message as feedback vs. a deviation report, then the
day/tag/item extractors pull out which day, which food category, and
which specific plan item the message is actually about. Every function
here is pure, callers own loading plan dates and the food/tag data and
pass it in, so this stays trivial to unit test without a running planner.

Deliberately conservative throughout: each extractor returns ``None``
rather than a low-confidence guess, since a wrong guess (crediting the
wrong day, matching the wrong item) is worse than surfacing nothing and
asking the user to clarify.
"""

import json
import re
import string
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional

DATA_DIR = Path(__file__).parent / "data"


# Deliberately long and still not exhaustive, this is a keyword-substring
# classifier, not a language model, its recall is only ever as good as the
# phrasings someone thought to add. Grown 2026-08-28 after "I am not feeling
# apples" produced no intent at all, see NLU_ADJUSTMENTS.md's "Known
# limitation" section for the tradeoffs of going further than this.
FEEDBACK_TRIGGERS = [
    "didn't like", "don't want", "avoid", "sick of", "tired of", "too much",
    "don't feel like", "not feeling", "not in the mood for", "not really into",
    "not into", "not a fan of", "not interested in", "not craving",
    "rather not have", "rather not eat", "can't do", "skip the",
    "remove the", "take out the", "get rid of", "cut out", "no more",
    "hate", "dislike", "over it", "done with", "not a big fan",
]
DEVIATION_TRIGGERS = [
    "didn't have", "instead", "ended up", "went out", "skipped",
    "wound up", "actually had", "actually ate", "grabbed a", "got takeout",
    "ordered", "picked up", "ate something else", "swapped",
]

WEEKDAY_NAMES = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
]

_PUNCTUATION_TABLE = str.maketrans("", "", string.punctuation)


def normalize(text: str) -> str:
    """Lowercase and strip punctuation, collapsing whitespace."""
    stripped = text.lower().translate(_PUNCTUATION_TABLE)
    return re.sub(r"\s+", " ", stripped).strip()


def classify_intent(text: str) -> Optional[str]:
    """Classify a message as "feedback" (a preference/complaint) or "deviation"
    (a report of eating something other than the plan), via substring rules.

    Feedback is checked first since a message can plausibly mention both
    (e.g. "sick of chicken, ended up ordering pizza instead"), and a
    complaint about the plan itself is the more actionable signal.
    """
    norm = normalize(text)
    if any(normalize(trigger) in norm for trigger in FEEDBACK_TRIGGERS):
        return "feedback"
    if any(normalize(trigger) in norm for trigger in DEVIATION_TRIGGERS):
        return "deviation"
    return None


def resolve_day(text: str, plan_dates: List[date]) -> Optional[date]:
    """Resolve a weekday name or relative term in ``text`` to one of ``plan_dates``.

    ``plan_dates`` is a 7-day rolling window starting today (see
    menu_planner.DayPlan), so its earliest date stands in for "today"
    rather than reaching for the system clock, keeping this pure and
    testable. Returns None if no day reference is found, or if the
    referenced day isn't actually in the window.
    """
    if not plan_dates:
        return None
    norm = normalize(text)
    today = min(plan_dates)

    relative = {
        "today": today,
        "tomorrow": today + timedelta(days=1),
        "yesterday": today - timedelta(days=1),
    }
    for word, candidate in relative.items():
        if word in norm:
            return candidate if candidate in plan_dates else None

    matches = [d for d in plan_dates if d.strftime("%A").lower() in norm]
    if len(matches) == 1:
        return matches[0]
    return None


def load_food_tags() -> Dict[str, List[str]]:
    with open(DATA_DIR / "food_tags.json", encoding="utf-8") as f:
        return json.load(f)


def extract_tag(text: str, tags: Dict[str, List[str]]) -> Optional[str]:
    """Match ``text`` against a tag -> synonyms dict, returning the first tag hit."""
    norm = normalize(text)
    for tag, synonyms in tags.items():
        if any(normalize(synonym) in norm for synonym in synonyms):
            return tag
    return None


def extract_item_match(
    text: str,
    candidate_names: List[str],
    threshold: float = 0.6,
) -> Optional[str]:
    """Fuzzy-match free text against plan/food item names.

    A whole-string comparison would penalize a short item name against a
    long sentence, so each candidate is instead compared against a
    same-length sliding window of words from ``text`` and the best window
    wins. Returns None when even the best candidate falls short of
    ``threshold``, rather than returning a weak guess.
    """
    norm_text = normalize(text)
    text_words = norm_text.split()

    best_name = None
    best_ratio = 0.0
    for name in candidate_names:
        norm_name = normalize(name)
        name_words = norm_name.split()
        window = len(name_words)
        if not window:
            continue

        if len(text_words) >= window:
            ratio = max(
                SequenceMatcher(None, " ".join(text_words[i:i + window]), norm_name).ratio()
                for i in range(len(text_words) - window + 1)
            )
        else:
            ratio = SequenceMatcher(None, norm_text, norm_name).ratio()

        if ratio > best_ratio:
            best_ratio = ratio
            best_name = name

    return best_name if best_ratio >= threshold else None
