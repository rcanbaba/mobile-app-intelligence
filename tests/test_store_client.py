import json
import unittest

from store_monitor.store_client import StoreClient
from tests.fakes import ScriptedOpener, http_error


def client(*responses):
    opener = ScriptedOpener(*responses)
    return StoreClient(opener=opener, retries=2, sleep=lambda s: None), opener


class FetchTest(unittest.TestCase):
    def test_retries_network_errors_then_succeeds(self):
        c, opener = client(TimeoutError("slow"), (200, "ok"))
        self.assertEqual(c.fetch("u"), (200, "ok", ""))
        self.assertEqual(len(opener.urls), 2)

    def test_gives_up_after_retries(self):
        c, _ = client(*[TimeoutError("slow")] * 3)
        code, _, err = c.fetch("u")
        self.assertIsNone(code)
        self.assertIn("TimeoutError", err)

    def test_retries_server_errors(self):
        c, opener = client(http_error(503), (200, "ok"))
        self.assertEqual(c.fetch("u")[0], 200)
        self.assertEqual(len(opener.urls), 2)

    def test_404_is_final(self):
        c, opener = client(http_error(404))
        self.assertEqual(c.fetch("u")[0], 404)
        self.assertEqual(len(opener.urls), 1)


class SignalTest(unittest.TestCase):
    def test_lookup_found(self):
        body = json.dumps({"resultCount": 1, "results": [
            {"trackName": "Demo", "version": "2.0", "currentVersionReleaseDate": "2026-02-03T00:00:00Z"}]})
        c, _ = client((200, body))
        lk = c.lookup("1", "us")
        self.assertTrue(lk.found)
        self.assertEqual((lk.meta["name"], lk.meta["updated"]), ("Demo", "2026-02-03"))

    def test_lookup_empty(self):
        c, _ = client((200, '{"resultCount": 0, "results": []}'))
        self.assertIs(c.lookup("1", "us").found, False)

    def test_lookup_malformed(self):
        for body in ("<html>", "[]", '{"resultCount": "x"}'):
            c, _ = client((200, body))
            lk = c.lookup("1", "us")
            self.assertIsNone(lk.found, body)

    def test_page_codes(self):
        cases = [((200, ""), True), (http_error(404), False), (http_error(410), False),
                 (http_error(403), None)]
        for resp, expected in cases:
            c, _ = client(resp)
            self.assertIs(c.page("1", "us").present, expected)

    def test_page_persistent_429_is_unknown(self):
        c, _ = client(*[http_error(429)] * 3)
        pg = c.page("1", "us")
        self.assertEqual((pg.http, pg.present), (429, None))


if __name__ == "__main__":
    unittest.main()
