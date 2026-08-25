import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bac_catalog_collector as bcc  # noqa: E402


def _award(amount=None, currency='ARS', suppliers=None, status='active'):
    return {'status': status, 'suppliers': suppliers or [],
            'value': ({'amount': amount, 'currency': currency} if amount is not None else None)}


RELEASES = [
    {  # A: tecnología, proveedor con name directo en suppliers
        'date': '2026-02-01T00:00:00-03:00',
        'tender': {'title': 'Adquisición de licencias de software y notebooks para oficinas'},
        'parties': [],
        'awards': [_award(1_000_000, suppliers=[{'id': 'S1', 'name': 'Microsoft SRL'}])],
    },
    {  # B: NO debe clasificar como tecnología pese a contener "redes" (falso positivo ya conocido)
        'date': '2026-02-05T00:00:00-03:00',
        'tender': {'title': 'Ampliación de redes eléctricas en Parque Centenario'},
        'parties': [{'id': 'S2', 'name': 'Constructora Sur SA', 'roles': ['supplier']}],
        'awards': [_award(500_000, suppliers=[{'id': 'S2'}])],  # sin 'name' -> fallback a parties
    },
    {  # C: tecnología ("redes de datos" calificado); un award inválido (amount None) y otro válido
        'date': '2026-02-10T00:00:00-03:00',
        'tender': {'title': 'Servicio de conectividad y redes de datos para escuelas'},
        'parties': [],
        'awards': [
            _award(None, suppliers=[{'id': 'S1', 'name': 'Microsoft SRL'}]),
            _award(250_000, suppliers=[{'id': 'S1', 'name': 'Microsoft SRL'}]),
        ],
    },
]


class ClassifyTechnologyTest(unittest.TestCase):
    def test_true_positive_software(self):
        self.assertTrue(bcc.classify_technology(RELEASES[0]['tender']))

    def test_false_positive_redes_electricas(self):
        self.assertFalse(bcc.classify_technology(RELEASES[1]['tender']))

    def test_true_positive_redes_de_datos_calificado(self):
        self.assertTrue(bcc.classify_technology(RELEASES[2]['tender']))


class ResolveSupplierNamesTest(unittest.TestCase):
    def test_fallback_to_parties_when_name_missing(self):
        parties = bcc.index_parties(RELEASES[1])
        names = bcc.resolve_supplier_names(RELEASES[1]['awards'][0], parties)
        self.assertEqual(names, ['Constructora Sur SA'])

    def test_uses_direct_name_when_present(self):
        parties = bcc.index_parties(RELEASES[0])
        names = bcc.resolve_supplier_names(RELEASES[0]['awards'][0], parties)
        self.assertEqual(names, ['Microsoft SRL'])


class ProcessReleasesTest(unittest.TestCase):
    def test_aggregates_and_skips_invalid_amounts(self):
        stats = bcc.process_releases(RELEASES)
        self.assertEqual(stats['vendor_totals']['Microsoft SRL'], 1_250_000)
        self.assertEqual(stats['vendor_totals']['Constructora Sur SA'], 500_000)
        self.assertEqual(stats['awards_counted'], 3)  # el award con amount=None no cuenta
        self.assertAlmostEqual(stats['total_ars'], 1_750_000)

    def test_tech_amount_only_counts_tech_releases(self):
        stats = bcc.process_releases(RELEASES)
        self.assertEqual(stats['vendor_tech_totals']['Microsoft SRL'], 1_250_000)
        self.assertNotIn('Constructora Sur SA', stats['vendor_tech_totals'])

    def test_splits_amount_across_cosuppliers(self):
        release = {
            'date': '2026-02-15T00:00:00-03:00',
            'tender': {'title': 'Servicio de nube y ciberseguridad'},
            'parties': [],
            'awards': [_award(300_000, suppliers=[{'id': 'X', 'name': 'Vendor X'},
                                                   {'id': 'Y', 'name': 'Vendor Y'}])],
        }
        stats = bcc.process_releases([release])
        self.assertEqual(stats['vendor_totals']['Vendor X'], 150_000)
        self.assertEqual(stats['vendor_totals']['Vendor Y'], 150_000)

    def test_non_ars_currency_excluded_from_totals(self):
        release = {
            'date': '2026-02-20T00:00:00-03:00',
            'tender': {'title': 'Compra de equipamiento'},
            'parties': [],
            'awards': [_award(1000, currency='USD', suppliers=[{'id': 'Z', 'name': 'Vendor Z'}])],
        }
        stats = bcc.process_releases([release])
        self.assertNotIn('Vendor Z', stats['vendor_totals'])
        self.assertEqual(stats['currencies'], {'USD': 1})
        self.assertEqual(stats['total_ars'], 0)

    def test_cancelled_award_excluded(self):
        release = {
            'date': '2026-02-25T00:00:00-03:00',
            'tender': {'title': 'Compra de software'},
            'parties': [],
            'awards': [_award(1000, status='cancelled', suppliers=[{'id': 'W', 'name': 'Vendor W'}])],
        }
        stats = bcc.process_releases([release])
        self.assertEqual(stats['awards_counted'], 0)
        self.assertNotIn('Vendor W', stats['vendor_totals'])


def _tech_release(date, organismo, method, competitive, amount, vendor):
    return {
        'date': date,
        'tender': {'title': 'Adquisición de licencias de software',
                   'procuringEntity': {'name': organismo},
                   'procurementMethod': method, 'competitive': competitive},
        'parties': [],
        'awards': [_award(amount, suppliers=[{'id': vendor, 'name': vendor}])],
    }


class AuditSignalsTest(unittest.TestCase):
    """BAC no publica numberOfTenderers/tenderers (confirmado contra el dataset real:
    0 ocurrencias en 23.298 releases) -> 'competitive' es la única señal de competencia
    real disponible, y la mayoría de los procesos 'open' igual vienen en false."""

    def test_direct_or_limited_share_and_noncompetitive_open(self):
        releases = [
            _tech_release('2026-01-01T00:00:00-03:00', 'Ministerio X', 'direct', False, 100, 'Vendor A'),
            _tech_release('2026-01-02T00:00:00-03:00', 'Ministerio X', 'open', False, 300, 'Vendor A'),
            _tech_release('2026-01-03T00:00:00-03:00', 'Ministerio X', 'open', True, 600, 'Vendor B'),
        ]
        stats = bcc.process_releases(releases)
        signals = bcc.build_audit_signals(stats)
        # tech_ars total = 1000; direct=100 -> 10%
        self.assertAlmostEqual(signals['direct_or_limited_share_pct'], 10.0)
        # el release 'open' + competitive=False (monto 300) es el caso de alerta
        self.assertEqual(signals['non_competitive_open_tenders']['count'], 1)
        self.assertAlmostEqual(signals['non_competitive_open_tenders']['amount_ars'], 300)

    def test_vendor_concentration_flags_high_share_above_floor(self):
        releases = [
            _tech_release('2026-01-01T00:00:00-03:00', 'Ministerio Y', 'open', True, 2_000_000, 'Vendor Dominante'),
            _tech_release('2026-01-02T00:00:00-03:00', 'Ministerio Y', 'open', True, 500_000, 'Vendor Chico'),
        ]
        stats = bcc.process_releases(releases)
        signals = bcc.build_audit_signals(stats)
        row = next(c for c in signals['vendor_concentration_by_organismo'] if c['organismo'] == 'Ministerio Y')
        self.assertEqual(row['top_vendor'], 'Vendor Dominante')
        self.assertAlmostEqual(row['top_vendor_share_pct'], 80.0)
        self.assertTrue(row['high_concentration'])

    def test_low_volume_organismo_excluded_from_concentration(self):
        releases = [_tech_release('2026-01-01T00:00:00-03:00', 'Organismo Chico', 'direct', False, 5_000, 'Vendor Unico')]
        stats = bcc.process_releases(releases)
        signals = bcc.build_audit_signals(stats)
        organismos = [c['organismo'] for c in signals['vendor_concentration_by_organismo']]
        self.assertNotIn('Organismo Chico', organismos)

    def test_no_cap_on_qualifying_organismos(self):
        # El frontend necesita poder buscar cualquier organismo del ranking del Boletín
        # acá (cruce por nombre), no sólo los primeros N por concentración.
        releases = [
            _tech_release(f'2026-01-{i:02d}T00:00:00-03:00', f'Organismo {i}', 'direct', False, 2_000_000, f'Vendor {i}')
            for i in range(1, 26)
        ]
        stats = bcc.process_releases(releases)
        signals = bcc.build_audit_signals(stats)
        self.assertEqual(len(signals['vendor_concentration_by_organismo']), 25)


class FractionationTest(unittest.TestCase):
    """Señal nueva: organismo+proveedor con varias adjudicaciones directas/limitadas
    repetidas en una ventana corta, como disparador de revisión de fraccionamiento."""

    def test_flags_repeated_direct_awards_within_window(self):
        releases = [
            _tech_release('2026-01-01T00:00:00-03:00', 'Ministerio Z', 'direct', False, 300_000, 'Vendor Repetido'),
            _tech_release('2026-01-20T00:00:00-03:00', 'Ministerio Z', 'direct', False, 300_000, 'Vendor Repetido'),
            _tech_release('2026-02-10T00:00:00-03:00', 'Ministerio Z', 'limited', False, 300_000, 'Vendor Repetido'),
        ]
        stats = bcc.process_releases(releases)
        signals = bcc.build_audit_signals(stats)
        flags = signals['possible_fractionation']['awards']
        row = next(f for f in flags if f['organismo'] == 'Ministerio Z' and f['vendor'] == 'Vendor Repetido')
        self.assertEqual(row['awards_count'], 3)
        self.assertAlmostEqual(row['total_amount_ars'], 900_000)

    def test_does_not_flag_below_minimum_award_count(self):
        releases = [
            _tech_release('2026-01-01T00:00:00-03:00', 'Ministerio W', 'direct', False, 300_000, 'Vendor Unico'),
            _tech_release('2026-01-10T00:00:00-03:00', 'Ministerio W', 'direct', False, 300_000, 'Vendor Unico'),
        ]
        stats = bcc.process_releases(releases)
        signals = bcc.build_audit_signals(stats)
        organismos = [f['organismo'] for f in signals['possible_fractionation']['awards']]
        self.assertNotIn('Ministerio W', organismos)

    def test_does_not_flag_awards_spread_beyond_window(self):
        releases = [
            _tech_release('2026-01-01T00:00:00-03:00', 'Ministerio V', 'direct', False, 300_000, 'Vendor Lento'),
            _tech_release('2026-04-01T00:00:00-03:00', 'Ministerio V', 'direct', False, 300_000, 'Vendor Lento'),
            _tech_release('2026-08-01T00:00:00-03:00', 'Ministerio V', 'direct', False, 300_000, 'Vendor Lento'),
        ]
        stats = bcc.process_releases(releases)
        signals = bcc.build_audit_signals(stats)
        organismos = [f['organismo'] for f in signals['possible_fractionation']['awards']]
        self.assertNotIn('Ministerio V', organismos)

    def test_open_method_awards_are_not_counted(self):
        releases = [
            _tech_release('2026-01-01T00:00:00-03:00', 'Ministerio U', 'open', True, 300_000, 'Vendor Abierto')
            for _ in range(3)
        ]
        stats = bcc.process_releases(releases)
        signals = bcc.build_audit_signals(stats)
        organismos = [f['organismo'] for f in signals['possible_fractionation']['awards']]
        self.assertNotIn('Ministerio U', organismos)


class UnchangedDetectionTest(unittest.TestCase):
    def test_matches_on_etag(self):
        prev = {'url': 'https://cdn/x.json', 'etag': 'abc', 'collector_version': bcc.COLLECTOR_VERSION}
        fp = {'etag': 'abc', 'last_modified': None, 'content_length': None}
        self.assertTrue(bcc.unchanged(prev, 'https://cdn/x.json', fp))

    def test_stale_collector_version_forces_reprocess_even_with_matching_etag(self):
        # Bug: la fuente (bac_anual.csv) sólo cambia cada 2-3 meses -> sin este chequeo,
        # un fix al collector quedaba "mudo" hasta que la fuente externa decidiera cambiar,
        # en vez de aplicarse en la próxima corrida tras el deploy del fix.
        prev = {'url': 'https://cdn/x.json', 'etag': 'abc', 'collector_version': bcc.COLLECTOR_VERSION - 1}
        fp = {'etag': 'abc', 'last_modified': None, 'content_length': None}
        self.assertFalse(bcc.unchanged(prev, 'https://cdn/x.json', fp))

    def test_missing_collector_version_in_prev_state_forces_reprocess(self):
        prev = {'url': 'https://cdn/x.json', 'etag': 'abc'}
        fp = {'etag': 'abc', 'last_modified': None, 'content_length': None}
        self.assertFalse(bcc.unchanged(prev, 'https://cdn/x.json', fp))

    def test_different_etag_means_changed(self):
        prev = {'url': 'https://cdn/x.json', 'etag': 'abc'}
        fp = {'etag': 'def', 'last_modified': None, 'content_length': None}
        self.assertFalse(bcc.unchanged(prev, 'https://cdn/x.json', fp))

    def test_missing_signals_assumes_changed(self):
        prev = {'url': 'https://cdn/x.json'}
        fp = {'etag': None, 'last_modified': None, 'content_length': None}
        self.assertFalse(bcc.unchanged(prev, 'https://cdn/x.json', fp))

    def test_head_failure_assumes_changed(self):
        prev = {'url': 'https://cdn/x.json', 'etag': 'abc'}
        self.assertFalse(bcc.unchanged(prev, 'https://cdn/x.json', None))

    def test_different_url_assumes_changed(self):
        prev = {'url': 'https://cdn/old.json', 'etag': 'abc'}
        fp = {'etag': 'abc', 'last_modified': None, 'content_length': None}
        self.assertFalse(bcc.unchanged(prev, 'https://cdn/new.json', fp))


class FindResourceTest(unittest.TestCase):
    def test_matches_bac_anual_csv_among_decoys(self):
        # El nombre visible en CKAN no identifica el archivo de forma confiable
        # (el CSV real se llama "Buenos Aires Compras - Anual" en 'name'); el match real
        # depende de la URL, no del 'name'. Se prefiere el CSV al JSON homónimo: verificado
        # contra el CDN real que bac_anual.json sólo tiene datos de 2022 (obsoleto).
        result = {'resources': [
            {'id': '1', 'name': 'Buenos Aires Compras General', 'format': 'CSV',
             'url': 'https://cdn/.../bac.csv'},
            {'id': '2', 'name': 'Buenos Aires Compras - Anual', 'format': 'CSV',
             'url': 'https://cdn/.../bac_anual.csv'},
            {'id': '3', 'name': 'Buenos Aires Compras', 'format': 'JSON',
             'url': 'https://cdn/.../bac_anual.json'},
            {'id': '4', 'name': 'Metadatos de Open Contracting Partnership (XLSX)', 'format': 'XLSX',
             'url': 'https://cdn/.../Metadata_OCDS.xlsx'},
        ]}
        res = bcc.find_resource(result)
        self.assertEqual(res['id'], '2')

    def test_no_match_returns_none(self):
        result = {'resources': [{'id': '1', 'name': 'bac.csv', 'format': 'CSV', 'url': 'https://cdn/.../bac.csv'}]}
        self.assertIsNone(bcc.find_resource(result))


class ToFloatToBoolTest(unittest.TestCase):
    def test_to_float_valid(self):
        self.assertEqual(bcc._to_float('1234.5'), 1234.5)

    def test_to_float_empty_or_none(self):
        self.assertIsNone(bcc._to_float(''))
        self.assertIsNone(bcc._to_float(None))

    def test_to_float_invalid(self):
        self.assertIsNone(bcc._to_float('no-es-un-numero'))

    def test_to_bool(self):
        self.assertTrue(bcc._to_bool('True'))
        self.assertFalse(bcc._to_bool('False'))
        self.assertIsNone(bcc._to_bool(''))
        self.assertIsNone(bcc._to_bool(None))

    def test_to_bool_recognizes_other_common_casings(self):
        # Bug: sólo reconocía las cadenas exactas 'True'/'False'; cualquier otra
        # convención de casing/valor (ej. si BAC cambia a lowercase o a '1'/'0') se
        # perdía en silencio como None sin ningún error visible.
        self.assertTrue(bcc._to_bool('true'))
        self.assertTrue(bcc._to_bool('TRUE'))
        self.assertTrue(bcc._to_bool('1'))
        self.assertFalse(bcc._to_bool('false'))
        self.assertFalse(bcc._to_bool('0'))
        self.assertIsNone(bcc._to_bool('unknown'))


class CsvRowToReleaseTest(unittest.TestCase):
    def _row(self, **overrides):
        row = {
            'date': '2026-05-07T16:00:00-03:00',
            'tender/title': 'Adquisición de licencias de software',
            'tender/description': '',
            'tender/mainProcurementCategory': 'goods',
            'tender/procuringEntity/name': 'Ministerio de Salud',
            'tender/procurementMethod': 'open',
            'tender/competitive': 'False',
            'parties/0/id': 'CABA-UE-416',
            'parties/0/name': 'Ministerio de Salud',
            'awards/0/status': 'active',
            'awards/0/value/amount': '150000.5',
            'awards/0/value/currency': 'ARS',
            'awards/0/suppliers/0/id': '30-1-2',
            'awards/0/suppliers/0/name': 'Vendor SRL',
        }
        row.update(overrides)
        return row

    def test_full_row_maps_correctly(self):
        rel = bcc.csv_row_to_release(self._row())
        self.assertEqual(rel['date'], '2026-05-07T16:00:00-03:00')
        self.assertEqual(rel['tender']['title'], 'Adquisición de licencias de software')
        self.assertEqual(rel['tender']['procuringEntity']['name'], 'Ministerio de Salud')
        self.assertIs(rel['tender']['competitive'], False)
        self.assertEqual(rel['awards'][0]['value'], {'amount': 150000.5, 'currency': 'ARS'})
        self.assertEqual(rel['awards'][0]['suppliers'], [{'id': '30-1-2', 'name': 'Vendor SRL'}])
        self.assertEqual(rel['parties'], [{'id': 'CABA-UE-416', 'name': 'Ministerio de Salud'}])

    def test_row_without_award_amount(self):
        rel = bcc.csv_row_to_release(self._row(**{'awards/0/value/amount': ''}))
        self.assertIsNone(rel['awards'][0]['value'])

    def test_row_with_unrecognized_competitive_value(self):
        rel = bcc.csv_row_to_release(self._row(**{'tender/competitive': ''}))
        self.assertIsNone(rel['tender']['competitive'])

    def test_row_feeds_process_releases_without_crashing(self):
        rel = bcc.csv_row_to_release(self._row())
        stats = bcc.process_releases([rel])
        self.assertEqual(stats['awards_counted'], 1)
        self.assertIn('Vendor SRL', stats['vendor_totals'])


def _bac_row(id_, award_id, item_id, amount, roles, name):
    return {
        'id': id_, 'date': '2026-02-04T13:00:00-03:00',
        'tender/title': 'Compra de equipamiento', 'tender/procuringEntity/name': 'Organismo X',
        'awards/0/id': award_id, 'awards/0/items/0/id': item_id, 'awards/0/status': 'active',
        'awards/0/value/amount': amount, 'awards/0/value/currency': 'ARS',
        'awards/0/suppliers/0/name': 'Proveedor Y', 'awards/0/suppliers/0/id': 'S1',
        'parties/0/name': name, 'parties/0/id': 'P1', 'parties/0/roles': roles,
    }


class DedupeCsvRowsTest(unittest.TestCase):
    """bac_anual.csv NO es una fila = una adjudicación: BAC emite una fila por cada
    combinación (renglón/ítem real, parte asociada). Confirmado contra el CSV real
    completo (24.911 filas -> 14.937 grupos reales, 0 inconsistencias de monto dentro
    de cada grupo con la clave usada acá) — estos fixtures replican los 3 patrones
    reales encontrados: duplicado simple comprador/proveedor, sub-ítems agrupados bajo
    un mismo renglón, y una orden de convenio marco con muchos organismos habilitados."""

    def test_simple_buyer_supplier_duplicate_collapses_to_one(self):
        # Caso real: Museo de Arte Moderno + AP Supplier Group SA, ítem único
        # duplicado en 2 filas (comprador + proveedor), mismo monto en ambas.
        rows = [
            _bac_row('9625-0248-CME26-1-0', '9625-0248-CME26', '13.01.002.0001.28-0',
                     '247000.0', 'buyer;procuringEntity', 'Museo de Arte Moderno'),
            _bac_row('9625-0248-CME26-1-1', '9625-0248-CME26', '13.01.002.0001.28-0',
                     '247000.0', 'supplier', 'Proveedor Y'),
        ]
        deduped = bcc.dedupe_csv_rows(rows)
        self.assertEqual(len(deduped), 1)

    def test_multiple_real_items_under_one_award_are_not_merged(self):
        # Caso real: mismo award/renglón nominal, pero 2 ítems reales distintos
        # (item_id difiere), cada uno con sus propias filas de comprador/proveedor/
        # ente contratante -> deben quedar como 2 grupos, no colapsar a 1.
        rows = [
            _bac_row('101-0066-LPU26-16-0', '101-0066-LPU26', '03.01.009.0001.1-0',
                     '104025600.0', 'supplier', 'Proveedor Y'),
            _bac_row('101-0066-LPU26-16-1', '101-0066-LPU26', '03.01.009.0001.1-0',
                     '104025600.0', 'procuringEntity', 'Organismo X'),
            _bac_row('101-0066-LPU26-16-2', '101-0066-LPU26', '03.01.009.0001.1-0',
                     '104025600.0', 'buyer', 'Organismo X'),
            _bac_row('101-0066-LPU26-16-3', '101-0066-LPU26', '03.01.009.0001.1-1',
                     '104025600.0', 'supplier', 'Proveedor Y'),
            _bac_row('101-0066-LPU26-16-4', '101-0066-LPU26', '03.01.009.0001.1-1',
                     '104025600.0', 'procuringEntity', 'Organismo X'),
            _bac_row('101-0066-LPU26-16-5', '101-0066-LPU26', '03.01.009.0001.1-1',
                     '104025600.0', 'buyer', 'Organismo X'),
        ]
        deduped = bcc.dedupe_csv_rows(rows)
        self.assertEqual(len(deduped), 2)

    def test_framework_agreement_many_eligible_buyers_collapses_to_one(self):
        # Caso real: convenio marco con ~250 organismos habilitados listados como
        # 'parties/0' individuales, mismo award/ítem/monto en todas las filas.
        rows = [
            _bac_row(f'623-2113-LPU25-17-{i}', '623-2113-LPU25', '03.02.001.0082.6-0',
                     '162000.0', 'buyer', f'Organismo {i}')
            for i in range(20)
        ]
        deduped = bcc.dedupe_csv_rows(rows)
        self.assertEqual(len(deduped), 1)

    def test_rows_without_a_parseable_id_are_never_merged_together(self):
        rows = [
            _bac_row('', 'AwardA', 'ItemA', '1000', 'supplier', 'X'),
            _bac_row('', 'AwardA', 'ItemA', '1000', 'buyer', 'Y'),
        ]
        deduped = bcc.dedupe_csv_rows(rows)
        self.assertEqual(len(deduped), 2)

    def test_end_to_end_total_amount_not_doubled_after_parse(self):
        csv_text = (
            'id,date,tender/title,tender/procuringEntity/name,awards/0/id,'
            'awards/0/items/0/id,awards/0/status,awards/0/value/amount,'
            'awards/0/value/currency,awards/0/suppliers/0/name,awards/0/suppliers/0/id\r\n'
            '9625-1-0,2026-02-04T13:00:00-03:00,Compra de PCs,Museo X,9625,ITEM1,active,'
            '247000.0,ARS,Vendor Y,S1\r\n'
            '9625-1-1,2026-02-04T13:00:00-03:00,Compra de PCs,Museo X,9625,ITEM1,active,'
            '247000.0,ARS,Vendor Y,S1\r\n'
        )
        releases = bcc.parse_csv_releases(csv_text)
        self.assertEqual(len(releases), 1)
        stats = bcc.process_releases(releases)
        self.assertAlmostEqual(stats['total_ars'], 247000.0)


class ParseCsvReleasesTest(unittest.TestCase):
    def test_parses_valid_csv(self):
        csv_text = (
            'date,tender/title,tender/procuringEntity/name,awards/0/value/amount,'
            'awards/0/value/currency,awards/0/status,awards/0/suppliers/0/name\r\n'
            '2026-05-07T16:00:00-03:00,Compra de notebooks,Org A,100000,ARS,active,Vendor X\r\n'
        )
        releases = bcc.parse_csv_releases(csv_text)
        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]['tender']['title'], 'Compra de notebooks')

    def test_missing_expected_columns_raises(self):
        with self.assertRaises(RuntimeError):
            bcc.parse_csv_releases('col_a,col_b\r\n1,2\r\n')


class MarkStaleTest(unittest.TestCase):
    """Bug: si ya existía un bac_catalog.json de una corrida OK previa, una falla
    posterior (resource_not_found/download_error) nunca actualizaba su 'status' -
    quedaba congelado en 'ok' para siempre aunque el pipeline siguiera fallando."""

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_out = bcc.OUT
        bcc.OUT = Path(self._tmpdir.name) / 'bac_catalog.json'

    def tearDown(self):
        bcc.OUT = self._orig_out
        self._tmpdir.cleanup()

    def test_existing_ok_output_status_flips_on_later_failure(self):
        bcc.write(bcc.OUT, {'status': 'ok', 'vendor_ranking': [{'name': 'Vendor A'}], 'audit_signals': {}})
        bcc._mark_stale(None, None, 'download_error', 'boom')
        updated = bcc.read(bcc.OUT)
        self.assertEqual(updated['status'], 'download_error')
        self.assertEqual(updated['last_error'], 'boom')
        # el último dato bueno conocido se conserva, no se pisa con datos vacíos
        self.assertEqual(updated['vendor_ranking'], [{'name': 'Vendor A'}])

    def test_creates_placeholder_when_no_prior_output_exists(self):
        bcc._mark_stale(None, None, 'resource_not_found')
        created = bcc.read(bcc.OUT)
        self.assertEqual(created['status'], 'resource_not_found')


if __name__ == '__main__':
    unittest.main()
