import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_model  # noqa: E402


class MergeNormTest(unittest.TestCase):
    """Bug: una corrida con una respuesta parcial de la API pisaba campos
    ya conocidos con None/[] en vez de conservar el último valor bueno."""

    def test_partial_fetch_does_not_erase_known_fields(self):
        old = {
            'id_norma': 1, 'organismo': 'Ministerio de Salud', 'tipo': 'Resolución',
            'url_norma': 'http://example.com/1', 'anexos': ['a.pdf'],
            'rutas_recuperacion': ['obtenerBoletin_true'], 'first_seen_at': '2026-08-01T00:00:00',
        }
        partial = {'id_norma': 1, 'organismo': None, 'tipo': None, 'url_norma': None, 'anexos': None}
        merged = data_model.merge_norm(old, partial, num=100, b={'fecha_publicacion': '21/08/2026'}, collected='2026-08-21T00:00:00')

        self.assertEqual(merged['organismo'], 'Ministerio de Salud')
        self.assertEqual(merged['tipo'], 'Resolución')
        self.assertEqual(merged['url_norma'], 'http://example.com/1')
        self.assertEqual(merged['anexos'], ['a.pdf'])
        self.assertEqual(merged['first_seen_at'], '2026-08-01T00:00:00')

    def test_new_non_empty_value_does_update(self):
        old = {'id_norma': 1, 'url_norma': None}
        new = {'id_norma': 1, 'url_norma': 'http://example.com/2'}
        merged = data_model.merge_norm(old, new, num=100, b={}, collected='2026-08-21T00:00:00')
        self.assertEqual(merged['url_norma'], 'http://example.com/2')

    def test_rutas_recuperacion_accumulate_across_runs(self):
        old = {'id_norma': 1, 'rutas_recuperacion': ['obtenerBoletin_true']}
        new = {'id_norma': 1, 'rutas_recuperacion': ['obtenerResultado']}
        merged = data_model.merge_norm(old, new, num=100, b={}, collected='2026-08-21T00:00:00')
        self.assertEqual(merged['rutas_recuperacion'], ['obtenerBoletin_true', 'obtenerResultado'])


class CategoryTest(unittest.TestCase):
    """Bug: 'redes' como palabra suelta clasificaba como tecnología cualquier
    mención a redes eléctricas/hídricas."""

    def test_electric_networks_are_not_technology(self):
        n = {'nombre': 'Licitación Pública / Circular con consulta N° 14/IVC/26',
             'sumario': 'Servicio integral de redes eléctricas y prevención de emergencias'}
        self.assertNotEqual(data_model.category(n), 'tecnologia')

    def test_data_networks_are_still_technology(self):
        n = {'nombre': 'Licitación', 'sumario': 'Adquisición e instalación de redes de datos y switches'}
        self.assertEqual(data_model.category(n), 'tecnologia')

    def test_generic_tech_keyword_still_matches(self):
        n = {'nombre': 'Licitación', 'sumario': 'Adquisición de licencias de software y servidores'}
        self.assertEqual(data_model.category(n), 'tecnologia')


class ProcesoIdTest(unittest.TestCase):
    """Bug: cada acto (llamado, circular, prórroga) de una misma licitación se
    contaba como una contratación distinta, inflando la métrica."""

    def test_same_process_number_groups_together(self):
        acts = [
            'Licitación Pública / Llamado  N° 14/IVC/26',
            'Licitación Pública / Circular con consulta  N° 14/IVC/26',
            'Licitación Pública / Circular sin consulta  N° 14/IVC/26',
            'Licitación Pública / Prórroga  N° 14/IVC/26',
        ]
        ids = {data_model.proceso_id({'nombre': nombre, 'id_norma': i}) for i, nombre in enumerate(acts)}
        self.assertEqual(ids, {'14/IVC/26'})

    def test_falls_back_to_id_norma_when_no_process_number(self):
        self.assertEqual(data_model.proceso_id({'nombre': 'Resolución sin número de proceso', 'id_norma': 42}), '42')

    def test_stats_count_distinct_processes_not_acts(self):
        norms = [
            {'id_norma': 1, 'nombre': 'Licitación / Llamado N° 14/IVC/26', 'sumario': 'Licitación Pública'},
            {'id_norma': 2, 'nombre': 'Licitación / Circular N° 14/IVC/26', 'sumario': 'Licitación Pública'},
            {'id_norma': 3, 'nombre': 'Licitación / Llamado N° 31/DGCYC/26', 'sumario': 'Licitación Pública'},
        ]
        procs = [{'proceso_id': data_model.proceso_id(n), 'categoria': data_model.category(n)} for n in norms]
        distinct = {p['proceso_id'] for p in procs}
        self.assertEqual(len(procs), 3)
        self.assertEqual(len(distinct), 2)


if __name__ == '__main__':
    unittest.main()
