import unittest

from store_monitor.cli import DEFAULT_CSV, build_parser


class CheckArgsTest(unittest.TestCase):
    def test_csv_is_written_by_default(self):
        args = build_parser().parse_args(["check"])
        self.assertEqual((args.csv, args.no_csv), (DEFAULT_CSV, False))

    def test_csv_can_be_redirected_or_disabled(self):
        self.assertEqual(build_parser().parse_args(["check", "--csv", "x.csv"]).csv, "x.csv")
        self.assertTrue(build_parser().parse_args(["check", "--no-csv"]).no_csv)


if __name__ == "__main__":
    unittest.main()
