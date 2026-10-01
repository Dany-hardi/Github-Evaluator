"""Inventory model: pure logic, no I/O."""
from dataclasses import dataclass, field


class OutOfStock(Exception):
    """Raised when more items are removed than are in stock."""


@dataclass
class Inventory:
    stock: dict[str, int] = field(default_factory=dict)

    def add(self, item: str, quantity: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self.stock[item] = self.stock.get(item, 0) + quantity

    def remove(self, item: str, quantity: int) -> None:
        available = self.stock.get(item, 0)
        if quantity > available:
            raise OutOfStock(f"{item}: wanted {quantity}, have {available}")
        self.stock[item] = available - quantity

    def total(self) -> int:
        return sum(self.stock.values())
