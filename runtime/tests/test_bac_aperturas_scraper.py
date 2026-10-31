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


def raw(numero, nombre, estado='Publicado', fecha='2026-11-03T13:00:00-03:00'):
    return {'numero_proceso': numero, 'nombre_proceso': nombre, 'tipo_proceso': 'Licitación Pública',
            'estado': estado, 'unidad_ejecutora_codigo': '8056', 'organismo': 'AGENCIA SISTEMAS DE INFORMACION',
            'fecha_apertura': fecha}


class BuildLicitacionesTest(unittest.TestCase):
    def scraped(self, publicados, rubro_pub, en_apertura):
        return {'Publicado': (publicados, len(publicados)),
                'Publicado|Informática': (rubro_pub, len(rubro_pub)),
                'En Apertura|Informática': (en_apertura, len(en_apertura))}

    def test_combines_rubro_and_keyword_and_drops_non_tech(self):
        # Casos reales del 31/10/2026: Tenable-One está en Informática pero el nombre no
        # dispara palabra clave; Jira sólo por palabra clave; un insumo médico no es tecnología.
        tenable = raw('8056-1507-LPU26', 'Renovación de solución de Gestión e Inspección Continua de Vulnerabilidades (Tenable-One)')
        jira = raw('8056-1688-LPU26', 'Suscripción de Licencias JIRA Software On Cloud (Versión Premium)')
        cateter = raw('420-0998-CDI26', 'ADQUISICIÓN DE CATETER DOBLE J')
        out = bas.build_licitaciones(self.scraped([tenable, jira, cateter], [tenable], []))
        by_id = {r['numero_proceso']: r for r in out}
        self.assertEqual(set(by_id), {'8056-1507-LPU26', '8056-1688-LPU26'})
        self.assertEqual(by_id['8056-1688-LPU26']['tech_source'], 'palabra_clave')
        self.assertIn(by_id['8056-1507-LPU26']['tech_source'], ('rubro', 'rubro+palabra_clave'))
        self.assertTrue(all(r['is_technology'] for r in out))

    def test_rubro_row_missing_from_general_search_is_kept(self):
        # Publicado entre la búsqueda general y la de Informática.
        nuevo = raw('2051-9999-LPU26', 'Servicio de mesa de ayuda')
        out = bas.build_licitaciones(self.scraped([], [nuevo], []))
        self.assertEqual([r['numero_proceso'] for r in out], ['2051-9999-LPU26'])
        self.assertEqual(out[0]['estado_buscado'], 'Publicado')

    def test_no_duplicates_and_publicado_first_sorted_by_date(self):
        a = raw('1-1-LPU26', 'Licencias de software', fecha='2026-11-12T13:00:00-03:00')
        b = raw('1-2-LPU26', 'Licencias de software', fecha='2026-11-03T13:00:00-03:00')
        viejo = raw('1-3-LPU26', 'Mantenimiento de portales', estado='En Apertura', fecha='2026-09-10T13:00:00-03:00')
        out = bas.build_licitaciones(self.scraped([a, b], [a, b], [viejo, a]))
        self.assertEqual([r['numero_proceso'] for r in out], ['1-2-LPU26', '1-1-LPU26', '1-3-LPU26'])
        self.assertEqual(out[-1]['estado_buscado'], 'En Apertura')


class TechSourceTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(bas.tech_source(True, True), 'rubro+palabra_clave')
        self.assertEqual(bas.tech_source(True, False), 'rubro')
        self.assertEqual(bas.tech_source(False, True), 'palabra_clave')
        self.assertIsNone(bas.tech_source(False, False))


if __name__ == '__main__':
    unittest.main()
