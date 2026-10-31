import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bac_presupuesto_oficial as bpo  # noqa: E402

# Extracto del texto real de VistaPreviaPliegoCiudadano.aspx para 2630-1459-LPU26 (31/10/2026),
# reducido a los bloques que se parsean. La página real también lista nombres y DNI de
# supervisores/evaluadores: no se copian acá ni se guardan nunca.
DETAIL_1459 = """Moneda

USD-Dolar Estadounidense
ARS-Peso Argentino

Cronograma
Fecha y hora estimada de publicación en el portal

9/10/2026 14:30:00

Fecha y hora publicación en boletín oficial

7/10/2026 00:00:00

Monto y duración del contrato
Monto

$ 18.424.665.000,00

Moneda

ARS - Peso Argentino

Periodicidad recepción

Mensual

Fecha estimada del inicio del contrato

A partir del perfeccionamiento del documento contractual

Duración del contrato

24 Meses
"""


class ParseDetailTest(unittest.TestCase):
    def test_real_sprs_block(self):
        self.assertEqual(bpo.parse_detail(DETAIL_1459), {
            'monto': 18424665000.0, 'moneda': 'ARS', 'duracion_meses': 24, 'fecha_publicacion': '2026-10-09'})

    def test_moneda_is_the_contract_one_not_the_quotation_list(self):
        # El primer "Moneda" de la página es la lista de monedas habilitadas para cotizar (USD/ARS);
        # la del contrato es la que está dentro de "Monto y duración del contrato".
        self.assertEqual(bpo.parse_detail(DETAIL_1459)['moneda'], 'ARS')

    def test_missing_block(self):
        self.assertEqual(bpo.parse_detail('sin datos')['monto'], None)

    def test_parse_monto(self):
        self.assertEqual(bpo.parse_monto('$ 1.234,56'), 1234.56)
        self.assertEqual(bpo.parse_monto('USD 2.880.000,00'), 2880000.0)
        self.assertIsNone(bpo.parse_monto(''))


class ScoreTest(unittest.TestCase):
    def test_outlier_flagged_only_with_enough_peers(self):
        base = {f'p{i}': 100_000 * (1 + (i % 5)) for i in range(16)}
        s = bpo.score({**base, 'grande': 12_000_000})
        self.assertTrue(s['grande']['flag'])
        self.assertEqual(s['grande']['rank'], 1)
        self.assertFalse(any(v['flag'] for k, v in s.items() if k != 'grande'))
        few = bpo.score({'a': 100_000, 'b': 200_000, 'grande': 12_000_000})
        self.assertFalse(few['grande']['flag'])  # menos de OUTLIER_MIN_PEERS: sólo puesto
        self.assertEqual(few['grande']['rank'], 1)

    def test_big_but_not_outlier_in_log_scale(self):
        # Montos dispersos en varios órdenes de magnitud: el mayor no necesariamente es atípico.
        vals = {f'p{i}': 10 ** (4 + i * 0.25) for i in range(16)}
        top = max(vals, key=vals.get)
        self.assertFalse(bpo.score(vals)[top]['flag'])


class BuildTest(unittest.TestCase):
    TODAY = date(2026, 10, 31)

    def test_converts_ars_and_annualizes_only_long_contracts(self):
        aperturas = {'licitaciones_abiertas': [
            {'numero_proceso': 'A', 'estado_buscado': 'Publicado', 'fecha_apertura': '2026-11-12T13:00:00-03:00'},
            {'numero_proceso': 'B', 'estado_buscado': 'Publicado', 'fecha_apertura': '2026-11-12T13:00:00-03:00'},
            {'numero_proceso': 'VIEJO', 'estado_buscado': 'En Apertura', 'fecha_apertura': '2014-10-10T10:00:00-03:00'},
        ]}
        cache = {'A': {'monto': 15_000_000_000, 'moneda': 'ARS', 'duracion_meses': 24, 'fecha_publicacion': '2026-10-28'},
                 'B': {'monto': 300_000, 'moneda': 'USD', 'duracion_meses': 6}}
        out = bpo.build(aperturas, cache, lambda d: 1500.0, self.TODAY)
        self.assertNotIn('VIEJO', out)  # registro trabado desde 2014: no es una licitación vigente
        self.assertEqual(out['A']['monto_usd'], 10_000_000)
        self.assertEqual(out['A']['monto_usd_anual'], 5_000_000)
        self.assertEqual(out['B']['monto_usd_anual'], 300_000)
        self.assertEqual(out['A']['rank'], 1)

    def test_compares_within_procedure_family(self):
        aperturas = {'licitaciones_abiertas': [
            {'numero_proceso': 'LP', 'estado_buscado': 'Publicado', 'tipo_proceso': 'Licitación Pública'},
            {'numero_proceso': 'CM', 'estado_buscado': 'Publicado', 'tipo_proceso': 'Contratación Menor'},
        ]}
        cache = {'LP': {'monto': 10, 'moneda': 'USD'}, 'CM': {'monto': 1_000_000, 'moneda': 'USD'}}
        out = bpo.build(aperturas, cache, None, self.TODAY)
        self.assertEqual((out['LP']['rank'], out['LP']['n']), (1, 1))
        self.assertEqual(out['CM']['grupo_comparacion'], 'Contrataciones menores, directas y otras')


class NeedsVisitTest(unittest.TestCase):
    TODAY = date(2026, 10, 31)

    def test_rules(self):
        pub, ap = {'estado_buscado': 'Publicado'}, {'estado_buscado': 'En Apertura'}
        self.assertTrue(bpo.needs_visit(pub, None, self.TODAY))
        self.assertFalse(bpo.needs_visit(pub, {'monto': 1, 'scraped_at': '2026-10-30T00:00:00+00:00'}, self.TODAY))
        self.assertTrue(bpo.needs_visit(pub, {'monto': 1, 'scraped_at': '2026-10-20T00:00:00+00:00'}, self.TODAY))
        self.assertFalse(bpo.needs_visit(ap, {'monto': 1, 'scraped_at': '2026-01-01T00:00:00+00:00'}, self.TODAY))


if __name__ == '__main__':
    unittest.main()
