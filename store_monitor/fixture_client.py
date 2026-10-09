"""Replays recorded store responses instead of calling Apple. Used by agent evals.

When STORE_MONITOR_FIXTURE points to a fixture file, the `probe` command uses this
client, so the agent investigates a fixed, repeatable world. Each probe runs in its
own process, so per-storefront call counts are kept in a file next to the fixture.
That lets a scenario answer differently on a retry.

Fixture format:
    {"app": {"name": "Demo"},
     "storefronts": {"us": [{"lookup": "found", "page": 200}, ...],
                     "*":  [{"lookup": "empty", "page": 404}]}}

The nth probe of a storefront gets the nth response; the last one repeats.
"*" covers storefronts not listed. lookup is one of found / empty / error /
malformed; page is an HTTP code or "timeout".
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import LookupResult, PageResult

ENV_VAR = "STORE_MONITOR_FIXTURE"


class FixtureClient:
    def __init__(self, fixture_path: str | os.PathLike):
        self.path = Path(fixture_path)
        self.fixture = json.loads(self.path.read_text(encoding="utf-8"))
        self.counts_path = self.path.with_name(self.path.stem + ".calls.json")
        self._current: dict[str, dict] = {}

    def _response(self, country: str, kind: str) -> dict:
        # One probe = one lookup + one page for a storefront; count on lookup.
        if kind == "lookup" or country not in self._current:
            counts = (json.loads(self.counts_path.read_text())
                      if self.counts_path.exists() else {})
            n = counts.get(country, 0)
            counts[country] = n + 1
            self.counts_path.write_text(json.dumps(counts))
            seq = self.fixture["storefronts"].get(country) or self.fixture["storefronts"]["*"]
            self._current[country] = seq[min(n, len(seq) - 1)]
        return self._current[country]

    def lookup(self, app_id: str, country: str) -> LookupResult:
        kind = self._response(country, "lookup")["lookup"]
        if kind == "found":
            meta = {"name": "", "seller": "", "version": "1.0.0", "updated": "2026-01-01",
                    "bundle": "", "price": "Free"} | self.fixture.get("app", {})
            return LookupResult(found=True, meta=meta)
        if kind == "empty":
            return LookupResult(found=False)
        if kind == "malformed":
            return LookupResult(found=None, error="lookup returned malformed JSON")
        return LookupResult(found=None, error="TimeoutError: timed out")

    def page(self, app_id: str, country: str) -> PageResult:
        page = self._response(country, "page")["page"]
        if page == "timeout":
            return PageResult(http=None, present=None, error="TimeoutError: timed out")
        if 200 <= page < 400:
            return PageResult(http=page, present=True)
        if page in (404, 410):
            return PageResult(http=page, present=False)
        return PageResult(http=page, present=None, error=f"http {page}")


def client_from_env():
    path = os.environ.get(ENV_VAR)
    return FixtureClient(path) if path else None
