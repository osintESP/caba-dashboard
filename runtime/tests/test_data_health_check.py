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


class AperturasTest(unittest.TestCase):
    NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)

    def data(self, rows, totales=None, obtenidos=None):
        totales = totales or {'Publicado': 179}
        return {'schema_version': 2, 'status': 'ok', 'licitaciones_abiertas': rows,
                'totales_reportados_por_bac': totales, 'obtenidos': obtenidos or dict(totales)}

    def row(self, numero, estado='Publicado', fecha='2026-11-03T13:00:00-03:00'):
        return {'estado_buscado': estado, 'numero_proceso': numero, 'fecha_apertura': fecha}

    def test_clean_dataset_passes(self):
        rows = [self.row('2051-1480-LPU26'), self.row('2051-1196-LPU26', 'En Apertura', '2026-09-10T13:00:00-03:00')]
        self.assertEqual(dhc.check_aperturas(self.data(rows), self.NOW), [])

    def test_flags_truncation(self):
        d = self.data([self.row('2051-1480-LPU26')], {'Publicado': 179}, {'Publicado': 100})
        self.assertEqual(len(dhc.check_aperturas(d, self.NOW)), 1)

    def test_flags_junk_duplicates_and_stale_publicado(self):
        rows = [self.row('1'), self.row('2051-1480-LPU26'), self.row('2051-1480-LPU26'),
                self.row('8056-1507-LPU26', fecha='2026-10-01T11:00:00-03:00')]
        self.assertEqual(len(dhc.check_aperturas(self.data(rows), self.NOW)), 3)

    def test_old_en_apertura_is_not_flagged(self):
        # Procesos trabados "En Apertura" desde hace años existen en BAC; no son un error del scraper.
        rows = [self.row('8503-0481-LPU14', 'En Apertura', '2014-10-10T10:00:00-03:00')]
        self.assertEqual(dhc.check_aperturas(self.data(rows), self.NOW), [])

    def test_old_schema_flagged(self):
        self.assertEqual(len(dhc.check_aperturas({'status': 'ok', 'aperturas_proximas': []}, self.NOW)), 1)


class BacFxTest(unittest.TestCase):
    def test_converted_ok(self):
        c = {'status': 'ok', 'summary': {'fx': {'usd_awards_converted': 221, 'excluded_awards_by_currency': {}}}}
        self.assertEqual(dhc.check_bac_fx(c), [])

    def test_excluded_usd_flagged(self):
        c = {'status': 'ok', 'summary': {'fx': {'excluded_awards_by_currency': {'USD': 3}}}}
        self.assertEqual(len(dhc.check_bac_fx(c)), 1)

    def test_missing_fx_block_flagged(self):
        self.assertEqual(len(dhc.check_bac_fx({'status': 'ok', 'summary': {}})), 1)


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
