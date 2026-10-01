"""Command line front end: parse arguments, call the model, report errors."""
import argparse
import sys
from pathlib import Path

from inventory import OutOfStock
from storage import load, save

DEFAULT_FILE = Path("inventory.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inventory")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("add", "remove"):
        p = sub.add_parser(name)
        p.add_argument("item")
        p.add_argument("quantity", type=int)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    inventory = load(DEFAULT_FILE)
    try:
        getattr(inventory, args.command)(args.item, args.quantity)
    except (OutOfStock, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    save(inventory, DEFAULT_FILE)
    print(f"{args.item}: {inventory.stock.get(args.item, 0)} (total {inventory.total()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
