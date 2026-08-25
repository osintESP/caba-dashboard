import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bac_aperturas_scraper as bas  # noqa: E402


class SplitUnidadEjecutoraTest(unittest.TestCase):
    def test_splits_code_and_name(self):
        codigo, nombre = bas.split_unidad_ejecutora('9625 - DIRECCIÓN GENERAL MUSEO DE ARTE MODERO DE BUENOS AIRES')
        self.assertEqual(codigo, '9625')
        self.assertEqual(nombre, 'DIRECCIÓN GENERAL MUSEO DE ARTE MODERO DE BUENOS AIRES')

    def test_handles_extra_whitespace(self):
        codigo, nombre = bas.split_unidad_ejecutora('  416   -   HTAL. CARLOS G. DURAND  ')
        self.assertEqual(codigo, '416')
        self.assertEqual(nombre, 'HTAL. CARLOS G. DURAND')

    def test_no_code_prefix_returns_none_code(self):
        codigo, nombre = bas.split_unidad_ejecutora('Organismo sin código')
        self.assertIsNone(codigo)
        self.assertEqual(nombre, 'Organismo sin código')

    def test_empty_or_none_returns_none_both(self):
        self.assertEqual(bas.split_unidad_ejecutora(None), (None, None))
        self.assertEqual(bas.split_unidad_ejecutora(''), (None, None))


class ParseFechaAperturaTest(unittest.TestCase):
    def test_parses_real_format(self):
        self.assertEqual(bas.parse_fecha_apertura('25/08/2026 15:00 Hrs.'), '2026-08-25T15:00:00-03:00')

    def test_unparseable_returns_none(self):
        self.assertIsNone(bas.parse_fecha_apertura('fecha inválida'))
        self.assertIsNone(bas.parse_fecha_apertura(None))


class ParseRowTest(unittest.TestCase):
    def test_parses_full_real_row(self):
        cells = [
            '416-2754-CME26',
            'CONECTOR,JERINGA HIPODERMICA,PEDIDO N° 42071-HEMODINAMIA',
            'Contratación Menor',
            '25/08/2026 15:00 Hrs.',
            'En Apertura',
            '416 - HTAL. CARLOS G. DURAND',
        ]
        row = bas.parse_row(cells)
        self.assertEqual(row['numero_proceso'], '416-2754-CME26')
        self.assertEqual(row['tipo_proceso'], 'Contratación Menor')
        self.assertEqual(row['estado'], 'En Apertura')
        self.assertEqual(row['unidad_ejecutora_codigo'], '416')
        self.assertEqual(row['organismo'], 'HTAL. CARLOS G. DURAND')
        self.assertEqual(row['fecha_apertura'], '2026-08-25T15:00:00-03:00')
        # los campos crudos intermedios no deben filtrarse al resultado final
        self.assertNotIn('unidad_ejecutora_raw', row)
        self.assertNotIn('fecha_apertura_raw', row)

    def test_non_technology_purchase_is_flagged_false(self):
        # El dashboard audita contrataciones de TECNOLOGÍA, no todas las compras de la
        # Ciudad — la mayoría de las aperturas reales son insumos médicos/obra/etc.
        cells = ['420-0998-CDI26', 'ADQUISICIÓN DE CATETER DOBLE J', 'Contratación Directa',
                 '25/08/2026 10:30 Hrs.', 'En Apertura', '420 - HTAL. RICARDO GUTIERREZ']
        row = bas.parse_row(cells)
        self.assertFalse(row['is_technology'])

    def test_technology_purchase_is_flagged_true(self):
        cells = ['2624-0843-CDI26', 'Adquisición de licencias de software y notebooks',
                 'Contratación Directa', '25/08/2026 11:00 Hrs.', 'En Apertura',
                 '2624 - INSTITUTO DE ESTADISTICAS Y CENSOS']
        row = bas.parse_row(cells)
        self.assertTrue(row['is_technology'])


if __name__ == '__main__':
    unittest.main()
