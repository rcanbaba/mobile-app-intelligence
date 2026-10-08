"""Turn free-form input lines ("name <TAB> url", bare IDs, pasted links) into AppInputs."""

from __future__ import annotations

import re
from typing import Iterable

from .models import AppInput, Status

ID_RE = re.compile(r"/id(\d{6,12})")
BARE_ID_RE = re.compile(r"^\d{6,12}$")
CC_RE = re.compile(r"apps\.apple\.com/([a-z]{2})/")
URLISH_RE = re.compile(r"https?://\S+")


def split_fields(line: str) -> list[str]:
    """Split on tab, 2+ spaces or comma, in that order of preference."""
    if "\t" in line:
        return [f.strip() for f in line.split("\t")]
    if re.search(r"\s{2,}", line):
        return [f.strip() for f in re.split(r"\s{2,}", line)]
    if "," in line:
        return [f.strip() for f in line.split(",")]
    return [line.strip()]


def parse_line(line: str, default_country: str) -> AppInput | None:
    line = line.rstrip()
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    fields = [f for f in split_fields(line) if f]
    if not fields:
        return None

    app_id = country = url = None
    rest = []
    for f in fields:
        m = ID_RE.search(f)
        if app_id is None and m:
            app_id, url = m.group(1), f
            cc = CC_RE.search(f)
            country = cc.group(1) if cc else default_country
        elif app_id is None and BARE_ID_RE.match(f):
            app_id, url, country = f, "", default_country
        else:
            rest.append(f)

    label = " ".join(f for f in rest if not URLISH_RE.match(f)).strip()
    label = label or (f"id{app_id}" if app_id else fields[0])

    if app_id:
        return AppInput(label=label, app_id=app_id, country=country, input_url=url)
    # No ID: either no link at all, or a non-App-Store link (TestFlight etc.)
    other = URLISH_RE.search(line)
    if other:
        return AppInput(label=label or fields[0], input_url=other.group(0),
                        pre_status=Status.SKIPPED)
    return AppInput(label=fields[0], pre_status=Status.NO_LINK)


def parse_lines(lines: Iterable[str], default_country: str) -> list[AppInput]:
    return [p for p in (parse_line(l, default_country) for l in lines) if p]
