"""Terminal and CSV output. Presentation only; no decisions are made here."""

from __future__ import annotations

import csv
import sys

from .changes import Change, ChangeKind, describe_signals
from .models import CheckResult, Status

ORDER = [Status.REMOVED, Status.UNCERTAIN, Status.ERROR,
         Status.NO_LINK, Status.SKIPPED, Status.LIVE]
HEADERS = {
    Status.REMOVED: "REMOVED FROM STORE",
    Status.UNCERTAIN: "UNCERTAIN - needs a closer look",
    Status.ERROR: "CHECK FAILED",
    Status.NO_LINK: "NO LINK IN INPUT",
    Status.SKIPPED: "NOT AN APP STORE LINK",
    Status.LIVE: "LIVE",
}
CSV_COLUMNS = ["label", "status", "app_id", "country", "name", "seller", "version",
               "updated", "bundle", "price", "http", "detail", "url"]

_COLORS = {Status.LIVE: "\033[32m", Status.REMOVED: "\033[31m",
           Status.UNCERTAIN: "\033[33m", Status.ERROR: "\033[35m",
           Status.NO_LINK: "\033[90m", Status.SKIPPED: "\033[90m",
           "dim": "\033[2m", "bold": "\033[1m", "off": "\033[0m"}


def color(key) -> str:
    return _COLORS[key] if sys.stdout.isatty() else ""


def sort_key(r: CheckResult):
    return ORDER.index(r.status), r.label.lower()


def print_report(results: list[CheckResult], only: set[Status] | None = None) -> None:
    by_status: dict[Status, list[CheckResult]] = {}
    for r in results:
        by_status.setdefault(r.status, []).append(r)

    dim, off, bold = color("dim"), color("off"), color("bold")
    for status in ORDER:
        group = by_status.get(status)
        if not group or (only and status not in only):
            continue
        print(f"{bold}{color(status)}{HEADERS[status]} ({len(group)}){off}")
        width = min(max(len(g.label) for g in group), 34)
        for r in sorted(group, key=sort_key):
            extra = [x for x in (f"v{r.version}" if r.version else "", r.updated, r.detail) if x]
            tail = f"  {dim}{' · '.join(extra)}{off}" if extra else ""
            print(f"  {r.label[:width].ljust(width)}  {dim}{r.url or '-'}{off}{tail}")
        print()

    print("-" * 70)
    print("Summary: " + "  ".join(
        f"{color(s)}{s.value} {len(by_status[s])}{off}" for s in ORDER if s in by_status))


def write_csv(results: list[CheckResult], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as out:
        w = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(r.to_dict() for r in sorted(results, key=sort_key))


def print_changes(changes: list[Change], unchanged: int, previous_runs: int) -> None:
    dim, off, bold = color("dim"), color("off"), color("bold")
    if previous_runs == 0:
        print(f"\n{bold}History:{off} no previous runs. This run is the baseline.")
        return

    anomalies = [c for c in changes if c.kind is ChangeKind.STATUS_CHANGE]
    failed = [c for c in changes if c.kind is ChangeKind.CHECK_FAILED]
    new = [c for c in changes if c.kind is ChangeKind.NEW]
    checked = unchanged + len(changes)

    print(f"\n{bold}History:{off} compared with {previous_runs} previous run(s).")
    print(f"{checked} apps checked. {unchanged} unchanged. "
          f"{len(anomalies)} status change(s), {len(failed)} failed check(s), {len(new)} new.")

    if anomalies:
        print(f"\n{bold}Status changes:{off}")
        for c in sorted(anomalies, key=lambda c: c.label.lower()):
            print(f"  {bold}{c.label}{off}  {c.app_id}@{c.country}  "
                  f"{color(c.previous)}{c.previous.value}{off} → {color(c.current)}{c.current.value}{off}")
            print(f"    {dim}now:    {describe_signals(c.current_signals)}{off}")
            print(f"    {dim}before: {describe_signals(c.previous_signals)} (run {c.previous_run_id}){off}")
    if failed:
        print(f"\n{bold}Failed checks{off} {dim}(app state unknown; rerun before drawing conclusions){off}")
        for c in sorted(failed, key=lambda c: c.label.lower()):
            prev = c.previous.value if c.previous else "no history"
            print(f"  {c.label}  {dim}last known: {prev} · consecutive errors: "
                  f"{c.consecutive_errors} · {c.current_detail}{off}")


def print_timeline(entries: list[dict]) -> None:
    for e in entries:
        print(f"  {e['checked_at']}  {e['country']}  {e['status']:<9}  "
              f"{describe_signals(e.get('signals') or {})}")
