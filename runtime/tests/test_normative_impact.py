import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import normative_impact as ni  # noqa: E402


class ExtractSiglaTest(unittest.TestCase):
    def test_extracts_sigla_from_act_number(self):
        self.assertEqual(ni.extract_sigla('Disposición N° 24/DGISIS/26'), 'DGISIS')

    def test_extracts_sigla_with_degree_symbol_variant(self):
        self.assertEqual(ni.extract_sigla('Resolución N° 130/SECITD/26'), 'SECITD')

    def test_returns_none_when_no_act_number_pattern(self):
        self.assertIsNone(ni.extract_sigla('Comunicado sin número de acto asociado'))

    def test_returns_none_for_missing_nombre(self):
        self.assertIsNone(ni.extract_sigla(None))
        self.assertIsNone(ni.extract_sigla(''))


class IsJgmTest(unittest.TestCase):
    def test_matches_exact_jgm(self):
        self.assertTrue(ni.is_jgm('Jefatura de Gabinete de Ministros'))

    def test_matches_jgm_as_substring_of_composite_organismo(self):
        self.assertTrue(ni.is_jgm('Ministerio de Hacienda y Finanzas - Jefatura de Gabinete de Ministros'))

    def test_other_organismos_excluded(self):
        self.assertFalse(ni.is_jgm('Ministerio de Salud'))

    def test_none_organismo_excluded(self):
        self.assertFalse(ni.is_jgm(None))


class ClassifyTopicsTest(unittest.TestCase):
    def test_detects_gde_mention(self):
        self.assertIn('gestion_documental_gde', ni.classify_topics('Disposición sobre GDE', ''))

    def test_detects_ciberseguridad_mention(self):
        self.assertIn('ciberseguridad', ni.classify_topics('', 'Nuevos requisitos de ciberseguridad'))

    def test_no_tags_for_unrelated_text(self):
        self.assertEqual(ni.classify_topics('Disposición administrativa', 'Trámite interno de personal'), [])

    def test_multiple_tags_can_apply(self):
        topics = ni.classify_topics('Interoperabilidad y firma digital en el circuito de GDE', '')
        self.assertIn('interoperabilidad', topics)
        self.assertIn('firma_digital', topics)
        self.assertIn('gestion_documental_gde', topics)


class BuildFlaggedNormsTest(unittest.TestCase):
    def _norm(self, id_norma, organismo, nombre, sumario=''):
        return {'id_norma': id_norma, 'numero_boletin': 7000, 'fecha_publicacion': '01/01/2026',
                'nombre': nombre, 'sumario': sumario, 'tipo': 'Disposición', 'organismo': organismo,
                'url_norma': f'http://x/{id_norma}'}

    def test_flags_jgm_norm_from_monitored_sigla(self):
        norms = [self._norm(1, 'Jefatura de Gabinete de Ministros', 'Disposición N° 24/DGISIS/26')]
        flagged = ni.build_flagged_norms(norms)
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]['sigla_unidad'], 'DGISIS')

    def test_excludes_jgm_norm_from_unmonitored_sigla(self):
        # DGIUR es, con enorme diferencia, la sigla más frecuente de JGM en el dataset real
        # (376/566 normas) y es Interpretación Urbanística, no sistemas -caso real de por qué
        # no se puede incluir cualquier sigla de JGM sin verificar qué representa.
        norms = [self._norm(2, 'Jefatura de Gabinete de Ministros', 'Disposición N° 10/DGIUR/26')]
        self.assertEqual(ni.build_flagged_norms(norms), [])

    def test_excludes_non_jgm_organismo_even_with_monitored_sigla(self):
        norms = [self._norm(3, 'Ministerio de Salud', 'Disposición N° 24/DGISIS/26')]
        self.assertEqual(ni.build_flagged_norms(norms), [])

    def test_sorted_by_id_norma_descending(self):
        norms = [
            self._norm(10, 'Jefatura de Gabinete de Ministros', 'Disposición N° 1/ASINF/26'),
            self._norm(20, 'Jefatura de Gabinete de Ministros', 'Disposición N° 2/ASINF/26'),
        ]
        flagged = ni.build_flagged_norms(norms)
        self.assertEqual([f['id_norma'] for f in flagged], [20, 10])

    def test_includes_topics_field(self):
        norms = [self._norm(4, 'Jefatura de Gabinete de Ministros', 'Resolución N° 5/SECITD/26',
                             sumario='Nuevo requisito de ciberseguridad para plataformas')]
        flagged = ni.build_flagged_norms(norms)
        self.assertIn('ciberseguridad', flagged[0]['topics'])
        self.assertIn('plataforma_o_sistema', flagged[0]['topics'])


if __name__ == '__main__':
    unittest.main()
