"""Persistence, kept apart from the model so each can change independently."""
import json
from pathlib import Path

from inventory import Inventory


def load(path: Path) -> Inventory:
    if not path.exists():
        return Inventory()
    return Inventory(json.loads(path.read_text(encoding="utf-8")))


def save(inventory: Inventory, path: Path) -> None:
    path.write_text(json.dumps(inventory.stock, indent=2), encoding="utf-8")
