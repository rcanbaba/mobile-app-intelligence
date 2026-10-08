"""Data contracts shared by every layer of the monitor.

Keeping these explicit (instead of passing loose dicts around) is what lets the
history, comparison and investigation layers agree on what a "result" is.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class Status(str, Enum):
    LIVE = "LIVE"            # lookup returns the app and the store page is reachable
    REMOVED = "REMOVED"      # no lookup in any checked storefront and the page is gone
    UNCERTAIN = "UNCERTAIN"  # signals disagree or the app exists only elsewhere
    ERROR = "ERROR"          # the check itself failed (network, server, bad response)
    NO_LINK = "NO_LINK"      # input line has no App Store URL or ID
    SKIPPED = "SKIPPED"      # input line links somewhere else (e.g. TestFlight)


@dataclass(frozen=True)
class AppInput:
    label: str
    app_id: str = ""
    country: str = ""
    input_url: str = ""
    # Set when parsing already knows the line cannot be checked.
    pre_status: Status | None = None


@dataclass(frozen=True)
class LookupResult:
    """iTunes lookup signal. `found` is None when the request itself failed."""
    found: bool | None
    meta: dict = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class PageResult:
    """Public store page signal. `present` is None when the HTTP code proves nothing
    (network failure, 429, 5xx...)."""
    http: int | None
    present: bool | None
    error: str = ""


@dataclass
class CheckResult:
    label: str
    status: Status
    app_id: str = ""
    country: str = ""
    name: str = ""
    seller: str = ""
    version: str = ""
    updated: str = ""
    bundle: str = ""
    price: str = ""
    http: int | str = ""
    detail: str = ""
    url: str = ""
    # Raw signals behind the classification; kept for history and investigations.
    signals: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CheckResult":
        return cls(**{**d, "status": Status(d["status"])})
