import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import collector  # noqa: E402


class BelongsTest(unittest.TestCase):
    """Bug: belongs() ignoraba boletines representados como lista de valores
    simples (no list/tuple ni dict), descartando normas válidas en silencio."""

    def test_no_boletines_field_defaults_to_belongs(self):
        self.assertTrue(collector.belongs({}, 100))

    def test_list_of_pairs_shape(self):
        self.assertTrue(collector.belongs({'boletines': [[100, '21/08/2026']]}, 100))
        self.assertFalse(collector.belongs({'boletines': [[99, '20/08/2026']]}, 100))

    def test_list_of_dicts_shape(self):
        self.assertTrue(collector.belongs({'boletines': [{'numero': 100}]}, 100))
        self.assertFalse(collector.belongs({'boletines': [{'numero': 99}]}, 100))

    def test_list_of_scalars_shape(self):
        self.assertTrue(collector.belongs({'boletines': [100, 101]}, 100))
        self.assertFalse(collector.belongs({'boletines': [98, 99]}, 100))


class ResolveUrlTest(unittest.TestCase):
    """Bug: sólo la rama de link_documento_norma validaba esquema http(s); url_norma y
    link_documento_normas se pasaban tal cual, permitiendo un href no-http en el frontend."""

    def test_direct_url_norma_requires_http_scheme(self):
        self.assertIsNone(collector.resolve_url({'url_norma': 'javascript:alert(1)'}, '21/08/2026'))
        self.assertEqual(collector.resolve_url({'url_norma': 'https://example.com/a'}, '21/08/2026'),
                          'https://example.com/a')

    def test_link_documento_normas_requires_http_scheme(self):
        n = {'link_documento_normas': [['21/08/2026', 'javascript:alert(1)']]}
        self.assertIsNone(collector.resolve_url(n, '21/08/2026'))
        n_ok = {'link_documento_normas': [['21/08/2026', 'http://example.com/b']]}
        self.assertEqual(collector.resolve_url(n_ok, '21/08/2026'), 'http://example.com/b')

    def test_link_documento_norma_fallback_requires_http_scheme(self):
        self.assertIsNone(collector.resolve_url({'link_documento_norma': 'ftp://example.com/c'}, None))
        self.assertEqual(collector.resolve_url({'link_documento_norma': 'http://example.com/c'}, None),
                          'http://example.com/c')


class NormIdFallbackTest(unittest.TestCase):
    """Bug: n.get('id_norma', n.get('id')) sólo usa el default si la clave falta, no si
    está presente con valor None — perdía el identificador en ese caso específico."""

    def test_id_norma_present_but_none_falls_back_to_id(self):
        x = collector.norm((), {'id_norma': None, 'id': 77}, 100, '21/08/2026')
        self.assertEqual(x['id_norma'], 77)

    def test_id_norma_present_and_valid_is_used(self):
        x = collector.norm((), {'id_norma': 5, 'id': 77}, 100, '21/08/2026')
        self.assertEqual(x['id_norma'], 5)


class WalkPhantomAnexoTest(unittest.TestCase):
    """Bug: walk() seguía recursando dentro de un dict ya identificado como norma, y un
    anexo anidado con forma similar (sumario/nombre propio) se emitía como norma fantasma."""

    def test_does_not_descend_into_matched_norm_record(self):
        node = {
            'id_norma': 1, 'nombre': 'Norma real', 'sumario': 'Texto',
            'anexos': [{'id_norma': 999, 'nombre': 'Anexo con forma de norma', 'sumario': 'Otro texto'}],
        }
        results = list(collector.walk(node))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1]['id_norma'], 1)


if __name__ == '__main__':
    unittest.main()
