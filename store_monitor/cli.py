"""Command-line entry point: python3 -m store_monitor <command> ..."""

from __future__ import annotations

import argparse
import json
import sys

from .changes import detect_changes
from .checker import check_all
from .history import DEFAULT_STATE_DIR, History, build_baselines
from .models import Status
from .parsing import parse_lines
from .report import print_changes, print_report, print_timeline, write_csv
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

    if args.no_history:
        return 0
    history = History(args.state_dir)
    runs = history.load_runs()
    changes, unchanged = detect_changes(results, build_baselines(runs))
    print_changes(changes, unchanged, len(runs))
    run = history.save_run(results, args.input, [c.to_dict() for c in changes])
    print(f"\nRun saved: {history.runs_dir / run.run_id}.json")
    return 0


def cmd_history(args) -> int:
    history = History(args.state_dir)
    if args.app_id:
        entries = history.timeline(args.app_id, args.country)
        if args.json:
            print(json.dumps(entries, indent=2, ensure_ascii=False))
        elif not entries:
            print(f"No history for {args.app_id}.")
        else:
            print(f"{entries[-1]['label']} ({args.app_id})")
            print_timeline(entries)
        return 0

    runs = history.load_runs()
    if args.json:
        print(json.dumps([{"run_id": r.run_id, "checked_at": r.checked_at, "input": r.input,
                           "apps": len(r.results), "changes": r.changes} for r in runs],
                         indent=2, ensure_ascii=False))
        return 0
    if not runs:
        print("No runs recorded yet.")
    for r in runs:
        anomalies = sum(1 for c in r.changes if c.get("is_anomaly"))
        print(f"  {r.run_id}  {r.input:<30}  {len(r.results):>3} apps  {anomalies} status change(s)")
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
    c.add_argument("--no-history", action="store_true",
                   help="Don't compare with or save to local history")
    c.add_argument("--state-dir", default=DEFAULT_STATE_DIR,
                   help=f"Local state directory (default: {DEFAULT_STATE_DIR})")
    c.set_defaults(func=cmd_check)

    h = sub.add_parser("history", help="List past runs, or one app's timeline")
    h.add_argument("app_id", nargs="?", help="Show the timeline of this app ID")
    h.add_argument("--country", help="Only this storefront")
    h.add_argument("--json", action="store_true", help="Machine-readable output")
    h.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    h.set_defaults(func=cmd_history)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
