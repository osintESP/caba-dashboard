import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bac_recurring_vendor_watch as brw  # noqa: E402


class NormalizeNameTest(unittest.TestCase):
    """Lógica de mayor riesgo de falso positivo/negativo del enriquecimiento: decide si un
    organismo/proveedor de bac_anual.csv es "el mismo" que uno devuelto por la búsqueda en
    vivo de BAC, con formato de texto potencialmente distinto (acentos, puntuación, mayúsculas,
    prefijo de código en el organismo -ya separado antes de llegar acá por
    split_unidad_ejecutora-)."""

    def test_strips_accents(self):
        self.assertEqual(brw.normalize_name('Ministerio de Educación'), 'MINISTERIO DE EDUCACION')

    def test_strips_periods(self):
        self.assertEqual(brw.normalize_name('Htal. Bernardino Rivadavia'), 'HTAL BERNARDINO RIVADAVIA')

    def test_collapses_whitespace(self):
        self.assertEqual(brw.normalize_name('  Dirección   General   Museo  '), 'DIRECCION GENERAL MUSEO')

    def test_uppercases(self):
        self.assertEqual(brw.normalize_name('exo sa'), 'EXO SA')

    def test_real_candidate_matches_real_live_grid_value(self):
        # Valor real de bac_anual.csv (tender/procuringEntity/name) vs. cómo aparece el mismo
        # organismo en la grilla en vivo de BAC (código + nombre, ya separado por
        # split_unidad_ejecutora antes de llegar a normalize_name -este test recibe sólo la
        # parte del nombre, no el "9625 - " completo-). Nota: "MODERO" (no "MODERNO") es el
        # valor real tal como lo publica BAC, no un typo nuestro.
        csv_value = 'DIRECCIÓN GENERAL MUSEO DE ARTE MODERO DE BUENOS AIRES'
        live_grid_value = 'DIRECCIÓN GENERAL MUSEO DE ARTE MODERO DE BUENOS AIRES'
        self.assertEqual(brw.normalize_name(csv_value), brw.normalize_name(live_grid_value))

    def test_different_organismos_do_not_match(self):
        self.assertNotEqual(brw.normalize_name('Ministerio de Educación'), brw.normalize_name('Ministerio de Salud'))

    def test_none_and_empty_input(self):
        self.assertEqual(brw.normalize_name(None), '')
        self.assertEqual(brw.normalize_name(''), '')

    def test_periods_are_removed_not_replaced_with_space(self):
        # "S.A." -> "SA" (los puntos se eliminan, no se reemplazan por espacio) -comportamiento
        # esperado y deseable acá: es la abreviatura más común de razón social en Argentina.
        self.assertEqual(brw.normalize_name('S.A.'), 'SA')

    def test_does_not_merge_words_separated_by_a_real_space(self):
        # Chequeo explícito del criterio "preferir falso negativo a falso positivo": un
        # espacio real entre letras (no producto de sacar puntos) no se colapsa -"S A" y "SA"
        # siguen siendo distintos-.
        self.assertNotEqual(brw.normalize_name('S A'), brw.normalize_name('SA'))


class IsAfterCutoffTest(unittest.TestCase):
    def test_date_after_cutoff_is_true(self):
        self.assertTrue(brw.is_after_cutoff('2026-08-04T14:00:00-03:00', '2026-05-29T16:00:00-03:00'))

    def test_date_before_cutoff_is_false(self):
        self.assertFalse(brw.is_after_cutoff('2026-02-04T10:00:00-03:00', '2026-05-29T16:00:00-03:00'))

    def test_date_equal_to_cutoff_is_false(self):
        self.assertFalse(brw.is_after_cutoff('2026-05-29T16:00:00-03:00', '2026-05-29T16:00:00-03:00'))

    def test_missing_fecha_or_cutoff_is_false(self):
        self.assertFalse(brw.is_after_cutoff(None, '2026-05-29T16:00:00-03:00'))
        self.assertFalse(brw.is_after_cutoff('2026-08-04T14:00:00-03:00', None))
        self.assertFalse(brw.is_after_cutoff(None, None))


def _candidate(organismo, vendor, awards_count=2):
    return {'organismo': organismo, 'vendor': vendor, 'awards_count': awards_count,
            'total_amount_ars': 100_000, 'date_from': '2026-01-01', 'date_to': '2026-02-01'}


def _row(organismo, fecha_apertura, numero_proceso='999-0001-CDI26'):
    return {'organismo': organismo, 'fecha_apertura': fecha_apertura, 'numero_proceso': numero_proceso}


class FindCandidateMatchesTest(unittest.TestCase):
    CUTOFF = '2026-05-29T16:00:00-03:00'

    def test_matches_same_organismo_after_cutoff(self):
        candidates = [_candidate('Ministerio de Educación', 'EXO SA')]
        rows = [_row('Ministerio de Educación', '2026-08-01T10:00:00-03:00')]
        matches = brw.find_candidate_matches(rows, candidates, self.CUTOFF)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][0], candidates[0])

    def test_no_match_when_row_before_cutoff(self):
        candidates = [_candidate('Ministerio de Educación', 'EXO SA')]
        rows = [_row('Ministerio de Educación', '2026-03-01T10:00:00-03:00')]
        matches = brw.find_candidate_matches(rows, candidates, self.CUTOFF)
        self.assertEqual(matches, [])

    def test_no_match_for_different_organismo(self):
        candidates = [_candidate('Ministerio de Educación', 'EXO SA')]
        rows = [_row('Ministerio de Salud', '2026-08-01T10:00:00-03:00')]
        matches = brw.find_candidate_matches(rows, candidates, self.CUTOFF)
        self.assertEqual(matches, [])

    def test_matches_despite_accent_and_case_differences(self):
        candidates = [_candidate('Htal. Bernardino Rivadavia', 'Facundo Adrian Ortiz Guerreiro')]
        rows = [_row('HTAL BERNARDINO RIVADAVIA', '2026-08-01T10:00:00-03:00')]
        matches = brw.find_candidate_matches(rows, candidates, self.CUTOFF)
        self.assertEqual(len(matches), 1)


class VendorConfirmedTest(unittest.TestCase):
    """El paso final antes de flaggear algo -confirmar que el proveedor del detalle del
    proceso es el mismo que el candidato-. Mismo organismo no alcanza: podría ser un
    proveedor distinto ganando ese organismo por primera vez."""

    def test_matching_vendor_confirmed(self):
        self.assertTrue(brw.vendor_confirmed('EXO SA', 'EXO SA'))

    def test_matching_vendor_with_format_differences_confirmed(self):
        self.assertTrue(brw.vendor_confirmed('  exo   sa  ', 'EXO SA'))

    def test_different_vendor_not_confirmed(self):
        self.assertFalse(brw.vendor_confirmed('Otro Proveedor SRL', 'EXO SA'))

    def test_missing_detail_not_confirmed(self):
        self.assertFalse(brw.vendor_confirmed(None, 'EXO SA'))
        self.assertFalse(brw.vendor_confirmed('EXO SA', None))


if __name__ == '__main__':
    unittest.main()
