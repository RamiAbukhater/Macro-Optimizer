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


class Pantry:
    """Tracks owned ingredient quantities, optionally persisted to a JSON file."""

    def __init__(self, path: Optional[Union[str, Path]] = None):
        self.path = Path(path) if path else None
        self._items: Dict[str, PantryEntry] = {}
        if self.path and self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            self._items = {name: PantryEntry(**entry) for name, entry in raw.items()}

    def add(self, ingredient: str, quantity: float, expires_on: Optional[str] = None) -> None:
        if quantity < 0:
            raise ValueError("quantity cannot be negative")
        existing = self._items.get(ingredient)
        if existing:
            existing.quantity += quantity
            if expires_on:
                existing.expires_on = expires_on
        else:
            self._items[ingredient] = PantryEntry(quantity=quantity, expires_on=expires_on)
        self._save()

    def have(self, ingredient: str) -> float:
        entry = self._items.get(ingredient)
        return entry.quantity if entry else 0.0

    def offset(self, ingredient: str, needed_qty: float) -> float:
        """How much of ``needed_qty`` still has to be bought, after using what's owned."""
        return max(0.0, needed_qty - self.have(ingredient))

    def consume(self, ingredient: str, quantity: float) -> None:
        """Decrement owned quantity after a plan using this ingredient is committed to."""
        entry = self._items.get(ingredient)
        if not entry:
            return
        entry.quantity = max(0.0, entry.quantity - quantity)
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

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            raw = {name: {"quantity": e.quantity, "expires_on": e.expires_on} for name, e in self._items.items()}
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2)
