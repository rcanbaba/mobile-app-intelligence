"""Deterministic classification scenarios. No network: the client is scripted."""

import unittest

from store_monitor.checker import check_app
from store_monitor.models import AppInput, LookupResult, PageResult, Status
from tests.fakes import FakeClient, found

APP = AppInput(label="Demo", app_id="123456789", country="tr")
PAGE_OK = PageResult(http=200, present=True)
PAGE_404 = PageResult(http=404, present=False)


class ClassificationTest(unittest.TestCase):
    def check(self, **kw):
        return check_app(APP, FakeClient(**kw))

    def test_normal_live_app(self):
        r = self.check(lookups={"tr": found()}, pages={"tr": PAGE_OK})
        self.assertIs(r.status, Status.LIVE)
        self.assertEqual(r.version, "1.2.0")

    def test_globally_removed_app(self):
        r = self.check(pages={"tr": PAGE_404})
        self.assertIs(r.status, Status.REMOVED)
        self.assertEqual(r.signals["alt_country"], "us")

    def test_region_specific_availability(self):
        r = self.check(lookups={"us": found()}, pages={"tr": PAGE_404})
        self.assertIs(r.status, Status.UNCERTAIN)
        self.assertIn("US", r.detail)

    def test_lookup_live_but_page_gone_is_conflict(self):
        r = self.check(lookups={"tr": found()}, pages={"tr": PAGE_404})
        self.assertIs(r.status, Status.UNCERTAIN)

    def test_empty_lookup_but_page_exists_is_conflict(self):
        r = self.check(pages={"tr": PAGE_OK})
        self.assertIs(r.status, Status.UNCERTAIN)

    def test_network_timeout_is_error_not_removed(self):
        r = self.check(lookups={"tr": LookupResult(found=None, error="TimeoutError")},
                       pages={"tr": PageResult(http=None, present=None, error="TimeoutError")})
        self.assertIs(r.status, Status.ERROR)

    def test_server_error_on_page_is_never_removed(self):
        r = self.check(pages={"tr": PageResult(http=503, present=None, error="http 503")})
        self.assertIs(r.status, Status.ERROR)

    def test_malformed_lookup_is_error(self):
        r = self.check(lookups={"tr": LookupResult(found=None, error="lookup returned malformed JSON")},
                       pages={"tr": PAGE_OK})
        self.assertIs(r.status, Status.ERROR)
        self.assertIn("malformed", r.detail)

    def test_live_lookup_with_unreadable_page_stays_live(self):
        r = self.check(lookups={"tr": found()},
                       pages={"tr": PageResult(http=None, present=None, error="timeout")})
        self.assertIs(r.status, Status.LIVE)

    def test_country_override(self):
        client = FakeClient(lookups={"de": found()}, pages={"de": PAGE_OK})
        r = check_app(APP, client, country_override="de")
        self.assertEqual((r.status, r.country), (Status.LIVE, "de"))

    def test_unlinked_inputs_make_no_requests(self):
        client = FakeClient()
        r = check_app(AppInput(label="x", pre_status=Status.NO_LINK), client)
        self.assertIs(r.status, Status.NO_LINK)
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
