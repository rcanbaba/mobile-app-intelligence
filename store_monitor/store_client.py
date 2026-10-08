"""The only module that talks to the network.

Everything that touches Apple's public endpoints goes through StoreClient, so
tests (and later agent evals) can swap in a fake client with recorded responses.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable

from .models import LookupResult, PageResult

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# HTTP codes worth retrying: rate limiting and server-side failures.
RETRYABLE_HTTP = {429, 500, 502, 503, 504}
# Only these codes prove the store page does not exist.
PAGE_GONE_HTTP = {404, 410}

# opener(url, timeout) -> (status, body); raises urllib errors like urlopen does.
Opener = Callable[[str, float], "tuple[int, str]"]


def urllib_opener(url: str, timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


class StoreClient:
    def __init__(self, opener: Opener = urllib_opener, timeout: float = 20,
                 retries: int = 2, sleep: Callable[[float], None] = time.sleep):
        self.opener = opener
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep

    def fetch(self, url: str) -> tuple[int | None, str, str]:
        """(http_code, body, error). http_code is None when no response arrived."""
        code, error = None, ""
        for attempt in range(self.retries + 1):
            try:
                status, body = self.opener(url, self.timeout)
                return status, body, ""
            except urllib.error.HTTPError as e:
                if e.code not in RETRYABLE_HTTP:
                    return e.code, "", ""
                code, error = e.code, f"http {e.code}"
            except Exception as e:  # timeouts, DNS, connection resets...
                code, error = None, f"{type(e).__name__}: {e}"
            if attempt < self.retries:
                self.sleep(1.5 * (attempt + 1))
        return code, "", error

    def lookup(self, app_id: str, country: str) -> LookupResult:
        code, body, err = self.fetch(
            f"https://itunes.apple.com/lookup?id={app_id}&country={country}")
        if code != 200:
            return LookupResult(found=None, error=err or f"lookup http {code}")
        try:
            data = json.loads(body)
            count = int(data.get("resultCount", 0))
            results = data.get("results") or []
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            return LookupResult(found=None, error="lookup returned malformed JSON")
        if count < 1 or not results:
            return LookupResult(found=False)
        r = results[0]
        return LookupResult(found=True, meta={
            "name": r.get("trackName", ""),
            "seller": r.get("sellerName", ""),
            "version": r.get("version", ""),
            "updated": (r.get("currentVersionReleaseDate") or "")[:10],
            "bundle": r.get("bundleId", ""),
            "price": r.get("formattedPrice", ""),
        })

    def page(self, app_id: str, country: str) -> PageResult:
        code, _, err = self.fetch(page_url(app_id, country))
        if code is None:
            return PageResult(http=None, present=None, error=err or "page unreachable")
        if 200 <= code < 400:
            return PageResult(http=code, present=True)
        if code in PAGE_GONE_HTTP:
            return PageResult(http=code, present=False)
        return PageResult(http=code, present=None, error=err or f"page http {code}")


def page_url(app_id: str, country: str) -> str:
    return f"https://apps.apple.com/{country}/app/id{app_id}"
