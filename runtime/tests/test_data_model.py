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


class CleanOrganismoTest(unittest.TestCase):
    """Bug real visto en producción: 'Ministerio de Salud' y 'Ministerio de Salud-'
    contaban como organismos distintos en el ranking por venir de la API del Boletín
    con basura de formato (guion/espacio colgante)."""

    def test_strips_trailing_hyphen(self):
        self.assertEqual(data_model.clean_organismo('Ministerio de Salud-'), 'Ministerio de Salud')

    def test_leaves_clean_value_unchanged(self):
        self.assertEqual(data_model.clean_organismo('Ministerio de Salud'), 'Ministerio de Salud')

    def test_does_not_touch_legitimate_internal_hyphen(self):
        # Un organismo compuesto real (dos ministerios listados juntos) no debe fusionarse.
        value = 'Ministerio de Hacienda y Finanzas - Ministerio de Salud'
        self.assertEqual(data_model.clean_organismo(value), value)

    def test_collapses_internal_whitespace_and_trailing_space(self):
        self.assertEqual(data_model.clean_organismo('Ministerio  de   Salud  '), 'Ministerio de Salud')

    def test_none_and_empty_passthrough(self):
        self.assertIsNone(data_model.clean_organismo(None))
        self.assertIsNone(data_model.clean_organismo(''))


class MergeNormAppliesCleanOrganismoTest(unittest.TestCase):
    def test_merge_norm_normalizes_organismo(self):
        merged = data_model.merge_norm({}, {'id_norma': 1, 'organismo': 'Ministerio de Salud-'},
                                        num=100, b={}, collected='2026-08-21T00:00:00')
        self.assertEqual(merged['organismo'], 'Ministerio de Salud')

    def test_garbage_organismo_falls_back_to_old_instead_of_erasing(self):
        # pick() ve "-" como no-vacío y lo elige sobre el valor viejo; sin el fix, clean_organismo
        # lo reduce a None DESPUÉS de que pick() ya decidió, y el organismo válido se pierde.
        old = {'id_norma': 1, 'organismo': 'Ministerio de Salud'}
        new = {'id_norma': 1, 'organismo': '-'}
        merged = data_model.merge_norm(old, new, num=100, b={}, collected='2026-08-21T00:00:00')
        self.assertEqual(merged['organismo'], 'Ministerio de Salud')


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

    def test_datos_inside_mandatos_is_not_a_false_positive(self):
        # Bug real: 'datos' en category() era un substring check sin borde de palabra,
        # así que "mandatos" (delegación de facultades, común en el Boletín) clasificaba
        # falsamente como tecnología.
        n = {'nombre': 'Decreto', 'sumario': 'Delega facultades y mandatos conferidos por la ley vigente'}
        self.assertNotEqual(data_model.category(n), 'tecnologia')

    def test_datos_as_real_word_still_matches(self):
        n = {'nombre': 'Licitación', 'sumario': 'Servicio de procesamiento de datos y analítica'}
        self.assertEqual(data_model.category(n), 'tecnologia')

    def test_bare_sistema_excludes_known_non_it_collocations(self):
        # Hallazgos reales (Ministerio de Salud y otros organismos, ~12% del bucket
        # 'tecnologia' antes del fix): 'sistema' solo, sin exigir calificación IT,
        # clasificaba como tecnología compras que no lo son.
        cases = [
            'Adquisición de sistema de Catéteres percutáneos y Cartucho hemoabsorbedor de citoquinas',
            'Mantenimiento del Sistema de Monitoreo, Registro y Alarma de Temperatura',
            'Sistemas de Alarmas de Línea Residencial/Comercial',
            'Adquisición de sistemas de arcos detectores de metales, scanner',
            'Servicio de sistema de detección y extinción de incendios',
        ]
        for sumario in cases:
            with self.subTest(sumario=sumario):
                n = {'nombre': 'Licitación', 'sumario': sumario}
                self.assertNotEqual(data_model.category(n), 'tecnologia')

    def test_bare_sistema_still_matches_when_not_excluded(self):
        n = {'nombre': 'Licitación', 'sumario': 'Actualización del Sistema de Liquidación de Haberes'}
        self.assertEqual(data_model.category(n), 'tecnologia')

    def test_tecnolog_stem_still_matches_without_right_boundary(self):
        # El fix sólo exige borde a la izquierda: 'tecnolog' debe seguir matcheando
        # 'tecnología' (raíz + sufijo, sin borde de palabra a la derecha).
        n = {'nombre': 'Resolución', 'sumario': 'Servicio integral de tecnología para el organismo'}
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


class IdSortKeyTest(unittest.TestCase):
    """Bug: sorted(key=str(id_norma)) ordena lexicográficamente, así que '999999' termina
    antes que '1000000' — con id_norma acercándose a 7 dígitos esto ya no es hipotético."""

    def test_numeric_ids_sort_numerically_not_lexicographically(self):
        ids = ['1000000', '999999', '2']
        ordered = sorted(ids, key=data_model._id_sort_key)
        self.assertEqual(ordered, ['2', '999999', '1000000'])

    def test_non_numeric_id_does_not_crash_and_sorts_after_numeric(self):
        keys = [data_model._id_sort_key(v) for v in (5, None, 'abc')]
        ordered = sorted(keys)
        self.assertEqual(ordered[0], (0, 5))


if __name__ == '__main__':
    unittest.main()
