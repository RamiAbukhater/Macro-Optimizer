from datetime import date, timedelta

from macro_optimizer.nlu import (
    classify_intent,
    extract_item_match,
    extract_tag,
    normalize,
    resolve_day,
)

FOOD_TAGS = {
    "beef": ["beef", "steak", "ground beef", "burger"],
    "chicken": ["chicken", "poultry"],
    "pork": ["pork", "bacon", "ham"],
    "fish": ["fish", "salmon", "tuna"],
    "dairy": ["dairy", "milk", "cheese"],
}

CANDIDATE_ITEMS = ["Grilled Chicken Bowl", "Turkey Burger", "Tuna Packet"]

# A fixed Monday anchor keeps day-resolution tests independent of wall-clock time.
MONDAY = date(2024, 1, 1)
WEEK = [MONDAY + timedelta(days=i) for i in range(7)]


def test_normalize_lowercases_and_strips_punctuation():
    assert normalize("Didn't Like It!") == "didnt like it"


def test_classify_intent_feedback_example():
    text = "I'm sick of beef, we've had it again on Thursday, can we avoid it this week?"
    assert classify_intent(text) == "feedback"


def test_classify_intent_deviation_example():
    text = "I skipped the grilled chicken bowl on Wednesday and went out for a burger instead"
    assert classify_intent(text) == "deviation"


def test_classify_intent_none_when_no_trigger_present():
    assert classify_intent("Looks good, thanks!") is None


def test_classify_intent_recognizes_not_feeling_phrasing():
    # Regression: "I am not feeling apples" used to classify as neither
    # feedback nor deviation, see NLU_ADJUSTMENTS.md's "Known limitation".
    assert classify_intent("I am not feeling apples") == "feedback"


def test_classify_intent_recognizes_not_in_the_mood_phrasing():
    assert classify_intent("not in the mood for oatmeal today") == "feedback"


def test_classify_intent_recognizes_no_more_phrasing():
    assert classify_intent("no more pasta this week") == "feedback"


def test_resolve_day_weekday_name():
    assert resolve_day("can we swap Thursday's meal", WEEK) == MONDAY + timedelta(days=3)


def test_resolve_day_today():
    assert resolve_day("I didn't have this today", WEEK) == MONDAY


def test_resolve_day_tomorrow():
    assert resolve_day("let's move it to tomorrow", WEEK) == MONDAY + timedelta(days=1)


def test_resolve_day_yesterday_outside_window_returns_none():
    # The window starts today, so "yesterday" falls outside it and can't be
    # confidently resolved to one of the plan's own dates.
    assert resolve_day("I skipped it yesterday", WEEK) is None


def test_resolve_day_returns_none_when_no_reference():
    assert resolve_day("this tastes great", WEEK) is None


def test_resolve_day_returns_none_for_empty_plan():
    assert resolve_day("thursday", []) is None


def test_extract_tag_matches_beef():
    assert extract_tag("I'm sick of beef again", FOOD_TAGS) == "beef"


def test_extract_tag_matches_via_synonym():
    assert extract_tag("too much poultry this week", FOOD_TAGS) == "chicken"


def test_extract_tag_returns_none_when_no_tag_mentioned():
    assert extract_tag("this was a great meal", FOOD_TAGS) is None


def test_extract_tag_does_not_over_generalize_a_named_fruit_to_the_whole_category():
    # "fruit" tag synonyms are deliberately generic ("fruit"/"fruits"), not a
    # list of specific fruits, naming one specific fruit should fall through
    # to item-level matching (see app.py's feedback fallback) rather than
    # matching a broad category tag and dropping every fruit on the plan.
    tags = {"fruit": ["fruit", "fruits"]}
    assert extract_tag("I dont want apples today", tags) is None
    assert extract_tag("not feeling fruit today", tags) == "fruit"


def test_extract_item_match_finds_exact_phrase_in_sentence():
    text = "I skipped the grilled chicken bowl on Wednesday and went out for a burger instead"
    assert extract_item_match(text, CANDIDATE_ITEMS) == "Grilled Chicken Bowl"


def test_extract_item_match_low_confidence_returns_none():
    assert extract_item_match("I ordered a pizza instead", CANDIDATE_ITEMS) is None


def test_extract_item_match_respects_custom_threshold():
    # A weak match on "Tuna Packet" clears a lowered threshold but not the default.
    text = "grabbed a quick snack instead of packing tuna"
    assert extract_item_match(text, CANDIDATE_ITEMS) is None
    assert extract_item_match(text, CANDIDATE_ITEMS, threshold=0.4) == "Tuna Packet"
