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


class UnchangedDetectionTest(unittest.TestCase):
    def test_matches_on_etag(self):
        prev = {'url': 'https://cdn/x.json', 'etag': 'abc'}
        fp = {'etag': 'abc', 'last_modified': None, 'content_length': None}
        self.assertTrue(bcc.unchanged(prev, 'https://cdn/x.json', fp))

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
    def test_matches_bac_anual_json_among_decoys(self):
        # El nombre visible en CKAN no identifica el archivo de forma confiable
        # (el JSON real se llama "Buenos Aires Compras" en 'name'); el match real
        # depende de la URL, no del 'name'.
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
        self.assertEqual(res['id'], '3')

    def test_no_match_returns_none(self):
        result = {'resources': [{'id': '1', 'name': 'bac.csv', 'format': 'CSV', 'url': 'https://cdn/.../bac.csv'}]}
        self.assertIsNone(bcc.find_resource(result))


if __name__ == '__main__':
    unittest.main()
