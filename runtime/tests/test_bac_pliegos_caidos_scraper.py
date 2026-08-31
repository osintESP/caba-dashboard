import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bac_pliegos_caidos_scraper as bpc  # noqa: E402


class ParseRowTest(unittest.TestCase):
    """A diferencia de bac_aperturas_scraper.parse_row, esta versión NO reclasifica
    tecnología por regex (classify_technology): BuscarAvanzado.aspx ya filtra Rubro=
    Informática del lado del servidor, que es más preciso -"Reparación de impresora", por
    ejemplo, no matchearía ningún patrón de TECH_INCLUDE pero BAC sí lo clasifica como
    Informática- así que reclasificar acá reintroduciría falsos negativos ya resueltos
    por el propio filtro de la fuente."""

    def test_parses_full_real_row(self):
        cells = [
            '541-2119-CME26',
            'SERVICIO DE MANTENIMIENTO Y ACTUALIZACION DE PROCESOS DEL SISTEMA',
            'Contratación Menor',
            '04/08/2026 14:00 Hrs.',
            'Desierto',
            '541 - ENTE AUTARQUICO TEATRO COLON',
        ]
        row = bpc.parse_row(cells)
        self.assertEqual(row['numero_proceso'], '541-2119-CME26')
        self.assertEqual(row['tipo_proceso'], 'Contratación Menor')
        self.assertEqual(row['estado'], 'Desierto')
        self.assertEqual(row['unidad_ejecutora_codigo'], '541')
        self.assertEqual(row['organismo'], 'ENTE AUTARQUICO TEATRO COLON')
        self.assertEqual(row['fecha_apertura'], '2026-08-04T14:00:00-03:00')
        self.assertNotIn('unidad_ejecutora_raw', row)
        self.assertNotIn('fecha_apertura_raw', row)

    def test_does_not_reclassify_technology(self):
        cells = ['416-0001-CME26', 'Reparación de impresora', 'Contratación Menor',
                 '01/08/2026 10:00 Hrs.', 'Desierto', '416 - HTAL. CARLOS G. DURAND']
        row = bpc.parse_row(cells)
        self.assertNotIn('is_technology', row)


if __name__ == '__main__':
    unittest.main()
