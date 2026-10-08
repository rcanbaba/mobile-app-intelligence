"""Command-line entry point: python3 -m store_monitor <command> ..."""

from __future__ import annotations

import argparse
import sys

from .checker import check_all
from .models import Status
from .parsing import parse_lines
from .report import print_report, write_csv
from .store_client import StoreClient

ONLY = {
    "live": {Status.LIVE},
    "removed": {Status.REMOVED},
    "problem": {Status.REMOVED, Status.UNCERTAIN, Status.ERROR,
                Status.NO_LINK, Status.SKIPPED},
}


def cmd_check(args) -> int:
    src = sys.stdin if args.input == "-" else open(args.input, encoding="utf-8")
    with src as f:
        items = parse_lines(f, args.default_country)
    if not any(i.pre_status is None for i in items):
        print("No App Store URLs or IDs found in input.", file=sys.stderr)
        return 2

    checkable = sum(i.pre_status is None for i in items)
    print(f"Checking {checkable} apps...\n")
    results = check_all(items, StoreClient(), args.workers, args.country)
    print_report(results, ONLY.get(args.only))

    if args.csv:
        write_csv(results, args.csv)
        print(f"CSV written: {args.csv}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="store_monitor",
                                 description="App Store availability monitor")
    sub = ap.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="Check every app in an input list")
    c.add_argument("input", nargs="?", default="apps.txt",
                   help="Input list (default: apps.txt). '-' reads stdin")
    c.add_argument("--country", "-c", help="Force this storefront for every line (e.g. us)")
    c.add_argument("--default-country", default="us",
                   help="Storefront when a line has no country in its URL (default: us)")
    c.add_argument("--csv", help="Also write results to this CSV file")
    c.add_argument("--workers", "-w", type=int, default=8, help="Parallel requests")
    c.add_argument("--only", choices=sorted(ONLY), help="Only print these statuses")
    c.set_defaults(func=cmd_check)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
