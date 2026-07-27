from __future__ import annotations

import sys
import unittest
from unittest import mock
from urllib.error import URLError

from engine import mark_prospective_outcomes


class MarkProspectiveOutcomesTests(unittest.TestCase):
    def test_quote_fetch_failure_is_nonfatal_when_allowed(self) -> None:
        with (
            mock.patch.object(sys, "argv", ["mark_prospective_outcomes.py", "--allow-quote-fetch-failure"]),
            mock.patch.object(mark_prospective_outcomes, "mark_prospective_ledger_file", side_effect=URLError("timed out")),
        ):
            self.assertEqual(mark_prospective_outcomes.main(), 0)

    def test_quote_fetch_failure_remains_fatal_by_default(self) -> None:
        with (
            mock.patch.object(sys, "argv", ["mark_prospective_outcomes.py"]),
            mock.patch.object(mark_prospective_outcomes, "mark_prospective_ledger_file", side_effect=URLError("timed out")),
        ):
            with self.assertRaises(URLError):
                mark_prospective_outcomes.main()


if __name__ == "__main__":
    unittest.main()
