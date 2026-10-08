"""Command-line entry point: python3 -m store_monitor <command> ..."""

from __future__ import annotations

import argparse
import json
import re
import sys

from .changes import detect_changes
from .checker import check_all
from pathlib import Path

from . import investigation as inv
from .history import DEFAULT_STATE_DIR, History, build_baselines
from .models import Status
from .parsing import BARE_ID_RE, parse_lines
from .probe import probe
from .report import (print_changes, print_conclusion, print_report, print_timeline,
                     print_tool_call, write_csv)
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

    anomalies = inv.anomalies_of(run)
    if not anomalies:
        return 0
    # Human in the loop: detection is automatic, investigation needs a yes.
    if args.input == "-" or not sys.stdin.isatty():
        print(f"\nTo investigate: python3 -m store_monitor investigate {run.run_id}")
        return 0
    answer = input(f"\nInvestigate {len(anomalies)} status change(s) with Claude Code? [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        print(f"Skipped. Later: python3 -m store_monitor investigate {run.run_id}")
        return 0
    return investigate_run(run, history, Path(args.state_dir), args.model, args.max_budget_usd)


def investigate_run(run, history, state_dir: Path, model, budget) -> int:
    case = inv.build_case(run, history, state_dir)
    case_path = inv.write_case(case, state_dir)
    if not inv.claude_available():
        print(f"Claude Code CLI ('claude') not found. Case written to {case_path}.\n"
              f"Open Claude Code in this repository and run:\n"
              f"  /investigate-store-anomaly {case_path}")
        return 1
    print(f"\nInvestigating {len(case['anomalies'])} status change(s)... "
          f"(case: {case_path})")
    outcome = inv.run_investigation(case, state_dir, model, budget,
                                    on_tool_call=print_tool_call)
    print_conclusion(outcome)
    print(f"\nSaved: {inv.case_dir(state_dir, run.run_id)}/conclusion.json")
    return 0 if outcome["valid"] else 1


def cmd_investigate(args) -> int:
    history = History(args.state_dir)
    runs = history.load_runs()
    if args.run_id:
        run = next((r for r in runs if r.run_id == args.run_id), None)
        if run is None:
            print(f"No run {args.run_id} in {history.runs_dir}.", file=sys.stderr)
            return 2
    else:
        run = next((r for r in reversed(runs) if inv.anomalies_of(r)), None)
        if run is None:
            print("No run with status changes to investigate.")
            return 0
    if not inv.anomalies_of(run):
        print(f"Run {run.run_id} has no status changes to investigate.")
        return 0
    return investigate_run(run, history, Path(args.state_dir), args.model, args.max_budget_usd)


def cmd_probe(args) -> int:
    # Arguments may come from the agent: accept only well-formed IDs and country codes.
    countries = [c.strip().lower() for c in args.countries.split(",") if c.strip()]
    if not BARE_ID_RE.match(args.app_id) or not countries or \
            not all(re.fullmatch(r"[a-z]{2}", c) for c in countries):
        print("probe needs a numeric app ID and 2-letter country codes (e.g. us,gb).",
              file=sys.stderr)
        return 2
    print(json.dumps(probe(args.app_id, countries, StoreClient()), indent=2, ensure_ascii=False))
    return 0


def add_agent_args(p) -> None:
    p.add_argument("--model", help="Claude model for the investigation (default: Claude Code's)")
    p.add_argument("--max-budget-usd", type=float, default=1.0,
                   help="Spending cap for one investigation (default: 1.0)")


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
    add_agent_args(c)
    c.set_defaults(func=cmd_check)

    i = sub.add_parser("investigate",
                       help="Investigate a run's status changes with Claude Code (read-only agent)")
    i.add_argument("run_id", nargs="?", help="Run to investigate (default: latest with changes)")
    i.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    add_agent_args(i)
    i.set_defaults(func=cmd_investigate)

    p = sub.add_parser("probe", help="Fresh read-only lookup + page signals for one app (JSON)")
    p.add_argument("app_id")
    p.add_argument("--countries", default="us", help="Comma-separated storefronts (default: us)")
    p.set_defaults(func=cmd_probe)

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
