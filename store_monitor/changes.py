"""Compare the current run with history and decide what is worth a human's attention.

Rules (all deterministic):
- No history for an app            -> NEW (this run is its baseline, not an anomaly)
- Current result is ERROR          -> CHECK_FAILED (the check broke, the app may be fine)
- Same status as last known status -> unchanged (not reported)
- Different status                 -> STATUS_CHANGE (an anomaly, offered for investigation)

"Last known" skips ERROR results, so LIVE -> ERROR -> LIVE is not a change, and
LIVE -> ERROR -> REMOVED is reported as LIVE -> REMOVED.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

from .history import Baseline, app_key
from .models import CheckResult, Status


class ChangeKind(str, Enum):
    NEW = "NEW"
    CHECK_FAILED = "CHECK_FAILED"
    STATUS_CHANGE = "STATUS_CHANGE"


@dataclass
class Change:
    kind: ChangeKind
    key: str
    label: str
    app_id: str
    country: str
    previous: Status | None
    current: Status
    previous_run_id: str = ""
    current_detail: str = ""
    current_signals: dict = field(default_factory=dict)
    previous_signals: dict = field(default_factory=dict)
    consecutive_errors: int = 0

    @property
    def is_anomaly(self) -> bool:
        return self.kind is ChangeKind.STATUS_CHANGE

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(kind=self.kind.value, current=self.current.value,
                 previous=self.previous.value if self.previous else None,
                 is_anomaly=self.is_anomaly)
        return d


def detect_changes(results: list[CheckResult],
                   baselines: dict[str, Baseline]) -> tuple[list[Change], int]:
    """Returns (changes, unchanged_count). Inputs without an app ID are ignored."""
    changes, unchanged = [], 0
    for r in results:
        if not r.app_id:
            continue
        key = app_key(r.app_id, r.country)
        b = baselines.get(key)
        prev = b.last_known if b else None

        def change(kind: ChangeKind, **extra) -> Change:
            return Change(kind=kind, key=key, label=r.label, app_id=r.app_id,
                          country=r.country, previous=prev.status if prev else None,
                          current=r.status, previous_run_id=b.last_known_run if b else "",
                          current_detail=r.detail, current_signals=r.signals,
                          previous_signals=prev.signals if prev else {}, **extra)

        if r.status is Status.ERROR:
            errors = (b.errors_since_known if b else 0) + 1
            changes.append(change(ChangeKind.CHECK_FAILED, consecutive_errors=errors))
        elif prev is None:
            changes.append(change(ChangeKind.NEW))
        elif prev.status is r.status:
            unchanged += 1
        else:
            changes.append(change(ChangeKind.STATUS_CHANGE))
    return changes, unchanged


def describe_signals(signals: dict) -> str:
    """One-line human summary of the raw evidence behind a result."""
    if not signals:
        return "no signals recorded"

    def lk(found, err):
        return {True: "found", False: "empty"}.get(found, f"failed ({err or 'unknown'})")

    parts = [f"lookup {lk(signals.get('lookup_found'), signals.get('lookup_error'))}"]
    if "alt_country" in signals:
        parts.append(f"{signals['alt_country'].upper()} lookup "
                     f"{lk(signals.get('alt_lookup_found'), signals.get('alt_lookup_error'))}")
    http = signals.get("page_http")
    parts.append(f"page {http}" if http is not None
                 else f"page failed ({signals.get('page_error') or 'unknown'})")
    return ", ".join(parts)
