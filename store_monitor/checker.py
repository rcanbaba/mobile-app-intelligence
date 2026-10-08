"""Deterministic classification: two independent public signals, cross-validated.

1. iTunes lookup API: does this storefront return the app?
2. Public store page: does apps.apple.com/<cc>/app/id<id> exist?

Neither signal is trusted alone because the lookup API occasionally serves stale
cached answers. When the lookup is empty, one fallback storefront is tried to
tell "removed everywhere" apart from "not available in this region".
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .models import AppInput, CheckResult, Status
from .store_client import StoreClient, page_url


def fallback_storefront(country: str) -> str:
    return "us" if country != "us" else "gb"


def check_app(item: AppInput, client: StoreClient,
              country_override: str | None = None) -> CheckResult:
    if item.pre_status is Status.NO_LINK:
        return CheckResult(label=item.label, status=Status.NO_LINK,
                           detail="no App Store URL or ID")
    if item.pre_status is Status.SKIPPED:
        kind = "TestFlight link" if "testflight" in item.input_url else "not an App Store link"
        return CheckResult(label=item.label, status=Status.SKIPPED,
                           url=item.input_url, detail=kind)

    country = country_override or item.country
    res = CheckResult(label=item.label, status=Status.ERROR, app_id=item.app_id,
                      country=country, url=page_url(item.app_id, country))

    lk = client.lookup(item.app_id, country)
    pg = client.page(item.app_id, country)
    res.signals = {"lookup_found": lk.found, "lookup_error": lk.error,
                   "page_http": pg.http, "page_present": pg.present,
                   "page_error": pg.error}
    for k, v in lk.meta.items():
        setattr(res, k, v)
    res.http = pg.http if pg.http is not None else ""

    def done(status: Status, detail: str = "") -> CheckResult:
        res.status, res.detail = status, detail
        return res

    if lk.found is True:
        if pg.present is False:
            return done(Status.UNCERTAIN, f"lookup found the app but page returned {pg.http}")
        return done(Status.LIVE)

    if lk.found is False:
        alt = fallback_storefront(country)
        alt_lk = client.lookup(item.app_id, alt)
        res.signals.update({"alt_country": alt, "alt_lookup_found": alt_lk.found,
                            "alt_lookup_error": alt_lk.error})
        if alt_lk.found:
            for k, v in alt_lk.meta.items():
                setattr(res, k, v)
            return done(Status.UNCERTAIN,
                        f"not in {country.upper()} storefront but available in {alt.upper()}")
        if pg.present is False:
            return done(Status.REMOVED, f"empty lookup + page {pg.http}")
        if pg.present is True:
            return done(Status.UNCERTAIN, f"empty lookup in every checked storefront but page {pg.http}")
        return done(Status.ERROR, pg.error or "page could not be read")

    return done(Status.ERROR, lk.error or pg.error or "network error")


def check_all(items: list[AppInput], client: StoreClient, workers: int = 8,
              country_override: str | None = None) -> list[CheckResult]:
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda i: check_app(i, client, country_override), items))
