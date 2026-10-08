"""Local run history: one JSON file per run under <state_dir>/runs/.

Plain files are enough here: runs are small, written by one person, and read
back whole. A database would add setup cost without solving a real problem yet.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .models import CheckResult, Status

SCHEMA_VERSION = 1
DEFAULT_STATE_DIR = ".monitor"


def app_key(app_id: str, country: str) -> str:
    """History identity of an app. The storefront is part of it: the same app can
    legitimately be LIVE in one country and missing in another."""
    return f"{app_id}@{country}"


@dataclass
class Run:
    run_id: str
    checked_at: str
    input: str
    results: list[CheckResult]
    changes: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "run_id": self.run_id,
                "checked_at": self.checked_at, "input": self.input,
                "results": [r.to_dict() for r in self.results],
                "changes": self.changes}

    @classmethod
    def from_dict(cls, d: dict) -> "Run":
        return cls(run_id=d["run_id"], checked_at=d["checked_at"], input=d.get("input", ""),
                   results=[CheckResult.from_dict(r) for r in d["results"]],
                   changes=d.get("changes", []))


class History:
    def __init__(self, state_dir: str | os.PathLike = DEFAULT_STATE_DIR):
        self.runs_dir = Path(state_dir) / "runs"

    def load_runs(self) -> list[Run]:
        """All runs, oldest first. Run IDs are UTC timestamps, so names sort by time."""
        if not self.runs_dir.is_dir():
            return []
        runs = []
        for p in sorted(self.runs_dir.glob("*.json")):
            with p.open(encoding="utf-8") as f:
                runs.append(Run.from_dict(json.load(f)))
        return runs

    def save_run(self, results: list[CheckResult], input_name: str,
                 changes: list[dict] | None = None, now: datetime | None = None) -> Run:
        now = now or datetime.now(timezone.utc)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        base = now.strftime("%Y%m%dT%H%M%SZ")
        run_id, n = base, 1
        while (self.runs_dir / f"{run_id}.json").exists():
            n += 1
            run_id = f"{base}-{n}"
        run = Run(run_id=run_id, checked_at=now.isoformat(timespec="seconds"),
                  input=input_name, results=results, changes=changes or [])
        # Write-then-rename so an interrupted run never leaves a half-written file.
        path = self.runs_dir / f"{run_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(run.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return run

    def timeline(self, app_id: str, country: str | None = None) -> list[dict]:
        """Every recorded result for one app, oldest first."""
        out = []
        for run in self.load_runs():
            for r in run.results:
                if r.app_id == app_id and (country is None or r.country == country):
                    out.append({"run_id": run.run_id, "checked_at": run.checked_at,
                                **r.to_dict()})
        return out


@dataclass
class Baseline:
    """What history says about one app before the current run."""
    last_known: CheckResult | None = None   # newest non-ERROR result
    last_known_run: str = ""
    last_seen: CheckResult | None = None    # newest result of any status
    errors_since_known: int = 0             # ERROR results after last_known


def build_baselines(runs: list[Run]) -> dict[str, Baseline]:
    baselines: dict[str, Baseline] = {}
    for run in runs:  # oldest -> newest, so later runs overwrite
        for r in run.results:
            if not r.app_id:
                continue
            b = baselines.setdefault(app_key(r.app_id, r.country), Baseline())
            b.last_seen = r
            if r.status is Status.ERROR:
                b.errors_since_known += 1
            else:
                b.last_known, b.last_known_run, b.errors_since_known = r, run.run_id, 0
    return baselines
