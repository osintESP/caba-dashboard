import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_health_check as dhc  # noqa: E402


def pliego(numero, estado='Desierto', fecha='2026-10-01T10:00:00-03:00'):
    return {'estado_buscado': estado, 'numero_proceso': numero, 'fecha_apertura': fecha}


class EditionsTest(unittest.TestCase):
    def test_detects_gap_in_recent_editions(self):
        eds = [{'numero_boletin': n, 'total_normas': 10} for n in (7455, 7456, 7458)]
        issues = dhc.check_editions(eds)
        self.assertEqual(len(issues), 1)
        self.assertIn('7457', issues[0])

    def test_ignores_gaps_older_than_window(self):
        eds = [{'numero_boletin': 7000, 'total_normas': 10}]
        eds += [{'numero_boletin': n, 'total_normas': 10}
                for n in range(7500, 7500 + dhc.RECENT_EDITIONS_WINDOW)]
        self.assertEqual(dhc.check_editions(eds), [])


class PliegosCaidosTest(unittest.TestCase):
    def test_clean_dataset_passes(self):
        data = {'status': 'ok', 'totales_reportados_por_bac': {'Desierto': 2},
                'pliegos': [pliego('541-2119-CME26'), pliego('412-3637-CME25')]}
        self.assertEqual(dhc.check_pliegos_caidos(data), [])

    def test_flags_pager_junk_row(self):
        # Fila real que se coló por el <tr> anidado del paginador antes del fix.
        data = {'status': 'ok', 'pliegos': [pliego('541-2119-CME26'), pliego('1', fecha=None)]}
        self.assertTrue(any('número de proceso' in i for i in dhc.check_pliegos_caidos(data)))

    def test_flags_truncation_against_reported_total(self):
        data = {'status': 'ok', 'totales_reportados_por_bac': {'Dejado Sin Efecto': 537},
                'pliegos': [pliego('541-2119-CME26', estado='Dejado Sin Efecto')]}
        self.assertTrue(any('BAC reporta 537' in i for i in dhc.check_pliegos_caidos(data)))

    def test_flags_duplicates_and_non_ok_status(self):
        data = {'status': 'incompleto', 'pliegos': [pliego('541-2119-CME26')] * 2}
        issues = dhc.check_pliegos_caidos(data)
        self.assertTrue(any('duplicadas' in i for i in issues))
        self.assertTrue(any("'incompleto'" in i for i in issues))


class BacCoverageTest(unittest.TestCase):
    NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)

    def test_recent_coverage_passes(self):
        catalog = {'coverage': {'date_to': '2026-08-31T15:45:00-03:00'}}
        self.assertEqual(dhc.check_bac_coverage(catalog, self.NOW), [])

    def test_stale_coverage_flagged(self):
        catalog = {'coverage': {'date_to': '2026-03-31T15:45:00-03:00'}}
        self.assertEqual(len(dhc.check_bac_coverage(catalog, self.NOW)), 1)


if __name__ == '__main__':
    unittest.main()
