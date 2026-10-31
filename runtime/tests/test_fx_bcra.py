import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fx_bcra  # noqa: E402


class MakeConverterTest(unittest.TestCase):
    RATES = {'2026-02-19': 1440.0, '2026-02-20': 1450.0}

    def test_same_day(self):
        self.assertEqual(fx_bcra.make_converter(self.RATES)('2026-02-20T00:00:00-03:00'), 1450.0)

    def test_weekend_uses_last_business_day(self):
        self.assertEqual(fx_bcra.make_converter(self.RATES)('2026-02-22'), 1450.0)

    def test_gap_too_long_or_before_series_is_none(self):
        conv = fx_bcra.make_converter(self.RATES)
        self.assertIsNone(conv('2026-03-30'))
        self.assertIsNone(conv('2026-01-01'))
        self.assertIsNone(conv(None))


class LoadRatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = fx_bcra.FX_FILE
        fx_bcra.FX_FILE = Path(self.tmp.name) / 'fx.json'

    def tearDown(self):
        fx_bcra.FX_FILE = self.orig
        self.tmp.cleanup()

    def test_fetches_then_uses_cache_for_covered_range(self):
        calls = []
        def fake(a, b):
            calls.append((a, b))
            return {'2026-02-20': 1450.0}
        fx_bcra.load_rates(date(2026, 2, 20), date(2026, 2, 20), fetch=fake)
        self.assertEqual(len(calls), 1)
        rates = fx_bcra.load_rates(date(2026, 2, 20), date(2026, 2, 20), fetch=lambda a, b: self.fail('no debería pedir'))
        self.assertEqual(rates['2026-02-20'], 1450.0)

    def test_api_failure_falls_back_to_cache(self):
        import requests
        def boom(a, b):
            raise requests.ConnectionError('down')
        self.assertEqual(fx_bcra.load_rates(date(2026, 2, 20), date(2026, 2, 20), fetch=boom), {})


if __name__ == '__main__':
    unittest.main()
