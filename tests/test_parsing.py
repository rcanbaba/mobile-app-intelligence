import unittest

from store_monitor.models import Status
from store_monitor.parsing import parse_line, parse_lines


class ParseLineTest(unittest.TestCase):
    def test_label_tab_url_reads_country_from_url(self):
        item = parse_line("Demo\thttps://apps.apple.com/gb/app/demo/id123456789?l=en", "us")
        self.assertEqual((item.label, item.app_id, item.country), ("Demo", "123456789", "gb"))

    def test_bare_id_uses_default_country_and_id_label(self):
        item = parse_line("123456789", "tr")
        self.assertEqual((item.label, item.app_id, item.country), ("id123456789", "123456789", "tr"))

    def test_two_spaces_and_comma_separators(self):
        for line in ("Demo  https://apps.apple.com/us/app/id123456789",
                     "Demo,https://apps.apple.com/us/app/id123456789"):
            self.assertEqual(parse_line(line, "us").label, "Demo")

    def test_testflight_link_is_skipped(self):
        item = parse_line("Beta\thttps://testflight.apple.com/join/abc", "us")
        self.assertIs(item.pre_status, Status.SKIPPED)

    def test_line_without_link(self):
        self.assertIs(parse_line("Just a name", "us").pre_status, Status.NO_LINK)

    def test_comments_and_blank_lines_are_ignored(self):
        self.assertEqual(parse_lines(["# comment", "", "   "], "us"), [])

    def test_example_file_parses(self):
        with open("examples/apps.example.txt", encoding="utf-8") as f:
            items = parse_lines(f, "us")
        checkable = [i for i in items if i.pre_status is None]
        self.assertEqual(len(checkable), 10)


if __name__ == "__main__":
    unittest.main()
