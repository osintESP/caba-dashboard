import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import collector  # noqa: E402


class BelongsTest(unittest.TestCase):
    """Bug: belongs() ignoraba boletines representados como lista de valores
    simples (no list/tuple ni dict), descartando normas válidas en silencio."""

    def test_no_boletines_field_defaults_to_belongs(self):
        self.assertTrue(collector.belongs({}, 100))

    def test_list_of_pairs_shape(self):
        self.assertTrue(collector.belongs({'boletines': [[100, '21/08/2026']]}, 100))
        self.assertFalse(collector.belongs({'boletines': [[99, '20/08/2026']]}, 100))

    def test_list_of_dicts_shape(self):
        self.assertTrue(collector.belongs({'boletines': [{'numero': 100}]}, 100))
        self.assertFalse(collector.belongs({'boletines': [{'numero': 99}]}, 100))

    def test_list_of_scalars_shape(self):
        self.assertTrue(collector.belongs({'boletines': [100, 101]}, 100))
        self.assertFalse(collector.belongs({'boletines': [98, 99]}, 100))


if __name__ == '__main__':
    unittest.main()
