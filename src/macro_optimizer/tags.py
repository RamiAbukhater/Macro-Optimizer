"""Maps catalog item names to a food_tags.json category.

history.py's exact-name rotation cap treats "beef burger" repeating as a
problem but "beef burger" Monday then "beef brisket" Wednesday as two
unrelated items, even though a human would call both "beef, too often."
This module bridges that gap for the *candidate pool* side: given the
catalog and the curated tag/synonym data (owned and maintained elsewhere,
see food_tags.json), it produces a name -> tag lookup that history.py and
optimizer.py can use to reason about picks by category instead of by exact
name.
"""

from typing import Dict, List

import pandas as pd


def build_item_tags(food_df: pd.DataFrame, food_tags: Dict[str, List[str]]) -> Dict[str, str]:
    """{item_name: tag} for every row in ``food_df`` that matches a tag's synonyms.

    Case-insensitive substring match against ``name``, the same pattern
    nlu.py's ``extract_tag`` uses against free text, applied here to catalog
    names instead. An item matching no tag's synonyms is simply absent from
    the result. An item matching more than one tag's synonyms gets whichever
    tag comes first in ``food_tags`` (dict iteration order), mirroring
    ``extract_tag``'s first-match behavior.
    """
    item_tags: Dict[str, str] = {}
    for name in food_df["name"]:
        if name in item_tags:
            continue
        lowered = name.lower()
        for tag, synonyms in food_tags.items():
            if any(synonym.lower() in lowered for synonym in synonyms):
                item_tags[name] = tag
                break
    return item_tags
