import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backfill_boletin as bb  # noqa: E402


class ComputeEndTest(unittest.TestCase):
    def test_override_wins_when_provided(self):
        self.assertEqual(bb.compute_end([7300, 7301], override='7290'), 7290)

    def test_defaults_to_one_before_oldest_existing_edition(self):
        self.assertEqual(bb.compute_end([7433, 7434, 7435]), 7432)

    def test_no_existing_editions_and_no_override_returns_none(self):
        self.assertIsNone(bb.compute_end([]))

    def test_empty_string_override_treated_as_unset(self):
        self.assertEqual(bb.compute_end([7433, 7434], override=''), 7432)


if __name__ == '__main__':
    unittest.main()
