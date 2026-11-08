"""Pantry / inventory awareness, per the IDEAS.md recipe layer.

A recipe's real cost to a given user is not its full ingredient price, it's
whatever they still need to buy. This module is the "first version is just
a manual list" IDEAS.md describes: what's in the kitchen right now and how
much, treated as free up to the quantity owned. recipes.py calls into this
to turn a recipe's full ingredient cost into a marginal cost.

Quantities are tracked in each ingredient's base unit (see
ingredients.json / recipes.py, typically grams or milliliters), no unit
conversion is done here, callers are expected to already agree on units.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Union


@dataclass
class PantryEntry:
    quantity: float
    expires_on: Optional[str] = None  # ISO date string, None means no known expiry
    # Set only for ingredients not in the curated ingredients.json database
    # (e.g. added via the Open Food Facts / USDA FoodData Central search),
    # so the fridge page has something to display without a recipe-costing
    # entry to fall back on. None for curated ingredients, whose unit and
    # display name already come from ingredients.json.
    unit: Optional[str] = None
    display_name: Optional[str] = None
    source: Optional[str] = None
    # Purchase batches making up ``quantity``: [{id, quantity, purchased_on,
    # use_by}]. A shopping-list check-off upserts the lot "buy:<week>:<line>",
    # so re-checking never adds stock twice and unchecking removes exactly
    # that purchase. Entries saved before lots existed get one "manual" lot.
    lots: List[dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.lots and self.quantity > 0:
            self.lots = [{"id": "manual", "quantity": self.quantity, "purchased_on": None,
                          "use_by": self.expires_on}]
        self._sync()

    def _sync(self) -> None:
        self.lots = [l for l in self.lots if l["quantity"] > 1e-9]
        self.quantity = round(sum(l["quantity"] for l in self.lots), 4)
        dates = [l["use_by"] for l in self.lots if l.get("use_by")]
        if dates:
            self.expires_on = min(dates)
        elif self.lots:
            self.expires_on = None


class Pantry:
    """Tracks owned ingredient quantities, optionally persisted to a JSON file."""

    def __init__(
        self,
        path: Optional[Union[str, Path]] = None,
        entries: Optional[Dict[str, dict]] = None,
    ):
        """``entries``, when given, seeds an in-memory-only copy (e.g. a blob
        loaded from a database) without writing anything back to ``path``,
        mirroring PickHistory's ``entries=`` constructor. Callers that want a
        real on-disk log should pass ``path`` alone."""
        self.path = Path(path) if path else None
        self._items: Dict[str, PantryEntry] = {}
        if entries is not None:
            self._items = {name: PantryEntry(**entry) for name, entry in entries.items()}
        elif self.path and self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            self._items = {name: PantryEntry(**entry) for name, entry in raw.items()}

    def add(self, ingredient: str, quantity: float, expires_on: Optional[str] = None) -> None:
        if quantity < 0:
            raise ValueError("quantity cannot be negative")
        existing = self._items.get(ingredient)
        if existing:
            existing.lots.append({"id": f"manual-{len(existing.lots)}", "quantity": quantity,
                                  "purchased_on": None, "use_by": expires_on})
            existing._sync()
            if expires_on:
                existing.expires_on = expires_on
        else:
            self._items[ingredient] = PantryEntry(quantity=quantity, expires_on=expires_on)
        self._save()

    def add_lot(self, key: str, lot_id: str, quantity: float, purchased_on: Optional[str] = None,
                use_by: Optional[str] = None, unit: Optional[str] = None) -> None:
        """Upsert one purchase batch by id. Idempotent: the same id twice leaves one lot."""
        if quantity < 0:
            raise ValueError("quantity cannot be negative")
        entry = self._items.get(key)
        if entry is None:
            entry = self._items[key] = PantryEntry(quantity=0.0, unit=unit)
        entry.lots = [l for l in entry.lots if l["id"] != lot_id]
        entry.lots.append({"id": lot_id, "quantity": quantity, "purchased_on": purchased_on, "use_by": use_by})
        entry._sync()
        self._save()

    def remove_lot(self, key: str, lot_id: str) -> None:
        """Drop one purchase batch (whatever of it is left). No-op if it isn't there."""
        entry = self._items.get(key)
        if entry is None:
            return
        entry.lots = [l for l in entry.lots if l["id"] != lot_id]
        entry._sync()
        if not entry.lots:
            del self._items[key]
        self._save()

    def lots(self, key: str) -> List[dict]:
        entry = self._items.get(key)
        return [dict(l) for l in entry.lots] if entry else []

    def set_quantity(
        self,
        ingredient: str,
        quantity: float,
        expires_on: Optional[str] = None,
        unit: Optional[str] = None,
        display_name: Optional[str] = None,
        source: Optional[str] = None,
    ) -> None:
        """Upsert an ingredient to an exact quantity, replacing whatever was tracked before.

        Unlike ``add``, this doesn't accumulate, it's the "I just counted what's
        actually in the fridge" operation an inventory UI needs alongside the
        "I bought more of this" one ``add`` already covers. ``unit``/
        ``display_name``/``source`` are for ingredients tracked outside the
        curated ingredients.json database (see PantryEntry); pass None (the
        default) for a curated ingredient, whose display info already comes
        from there.
        """
        if quantity < 0:
            raise ValueError("quantity cannot be negative")
        self._items[ingredient] = PantryEntry(
            quantity=quantity, expires_on=expires_on,
            unit=unit, display_name=display_name, source=source,
        )
        self._save()

    def remove(self, ingredient: str) -> None:
        """Drop an ingredient from the pantry entirely. No-op if it isn't tracked."""
        if ingredient in self._items:
            del self._items[ingredient]
            self._save()

    def list_items(self) -> Dict[str, PantryEntry]:
        """Every tracked ingredient, name to its entry. Empty dict if nothing is tracked."""
        return {
            name: PantryEntry(entry.quantity, entry.expires_on, entry.unit, entry.display_name, entry.source,
                              [dict(l) for l in entry.lots])
            for name, entry in self._items.items()
        }

    def have(self, ingredient: str, exclude_prefix: Optional[str] = None) -> float:
        """Owned quantity; ``exclude_prefix`` leaves out lots whose id starts with it
        (the shopping list ignores this week's own purchases when netting stock)."""
        entry = self._items.get(ingredient)
        if not entry:
            return 0.0
        if exclude_prefix is None:
            return entry.quantity
        return sum(l["quantity"] for l in entry.lots if not str(l["id"]).startswith(exclude_prefix))

    def offset(self, ingredient: str, needed_qty: float) -> float:
        """How much of ``needed_qty`` still has to be bought, after using what's owned."""
        return max(0.0, needed_qty - self.have(ingredient))

    def consume(self, ingredient: str, quantity: float) -> None:
        """Decrement owned quantity after a plan using this ingredient is committed to."""
        entry = self._items.get(ingredient)
        if not entry:
            return
        # Soonest use-by first, so what spoils first is what gets eaten first.
        remaining = quantity
        for lot in sorted(entry.lots, key=lambda l: (l.get("use_by") is None, l.get("use_by") or "")):
            take = min(lot["quantity"], remaining)
            lot["quantity"] -= take
            remaining -= take
            if remaining <= 1e-9:
                break
        entry._sync()
        self._save()

    def expiring_within(self, days: int, as_of: Optional[date] = None) -> List[str]:
        """Ingredient names with a known expiry inside the given window, soonest first."""
        as_of = as_of or date.today()
        due = []
        for name, entry in self._items.items():
            if not entry.expires_on or entry.quantity <= 0:
                continue
            expires = date.fromisoformat(entry.expires_on)
            delta = (expires - as_of).days
            if 0 <= delta <= days:
                due.append((delta, name))
        due.sort()
        return [name for _, name in due]

    def to_dict(self) -> Dict[str, dict]:
        """Raw JSON-serializable shape, same as what ``_save`` writes to disk.

        Lets a caller that built this Pantry from ``entries=`` (no file path,
        e.g. a per-user blob loaded from a database) pull the mutated state
        back out to persist elsewhere.
        """
        return {
            name: {
                "quantity": e.quantity, "expires_on": e.expires_on,
                "unit": e.unit, "display_name": e.display_name, "source": e.source,
                "lots": [dict(l) for l in e.lots],
            }
            for name, e in self._items.items()
        }

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2)
