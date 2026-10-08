"""Scripted stand-ins for the network, so scenarios run offline and repeatably."""

import urllib.error

from store_monitor.models import LookupResult, PageResult

LIVE_META = {"name": "Demo App", "seller": "Demo Inc.", "version": "1.2.0",
             "updated": "2026-01-01", "bundle": "com.demo.app", "price": "Free"}


class FakeClient:
    """Answers lookup/page per storefront country. Unlisted countries: empty lookup."""

    def __init__(self, lookups=None, pages=None):
        self.lookups = lookups or {}
        self.pages = pages or {}
        self.calls = []

    def lookup(self, app_id, country):
        self.calls.append(("lookup", country))
        return self.lookups.get(country, LookupResult(found=False))

    def page(self, app_id, country):
        self.calls.append(("page", country))
        return self.pages.get(country, PageResult(http=404, present=False))


def found():
    return LookupResult(found=True, meta=dict(LIVE_META))


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


class ScriptedOpener:
    """Opener returning (or raising) queued responses in order."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
