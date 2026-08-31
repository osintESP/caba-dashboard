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

    def test_includes_price_redetermination_field(self):
        norms = [self._norm(5, 'Jefatura de Gabinete de Ministros', 'Disposición N° 56/DGIASINF/26',
                             sumario='Aprueba la Novena Actualización de Precios de la Orden de Compra N° 8056-0106-OCA25')]
        flagged = ni.build_flagged_norms(norms)
        self.assertEqual(flagged[0]['price_redetermination'],
                          {'orden_de_compra': '8056-0106-OCA25', 'ordinal': 9, 'ordinal_word': 'Novena'})

    def test_price_redetermination_is_none_when_not_mentioned(self):
        norms = [self._norm(6, 'Jefatura de Gabinete de Ministros', 'Disposición N° 1/ASINF/26',
                             sumario='Dar de baja bienes inventariados')]
        flagged = ni.build_flagged_norms(norms)
        self.assertIsNone(flagged[0]['price_redetermination'])


class ExtractPriceRedeterminationTest(unittest.TestCase):
    """Las disposiciones de actualización de precios de Orden de Compra declaran el ordinal
    en texto plano (verificado contra normative_impact.json real) -eso es lo que hace viable
    esta señal sin reconstruir un historial de montos, que no existe en ningún dataset."""

    def test_parses_real_example(self):
        result = ni.extract_price_redetermination(
            'Aprueba la Cuarta Actualización de Precios de la Orden de Compra N° 8056-0311-OCA25')
        self.assertEqual(result, {'orden_de_compra': '8056-0311-OCA25', 'ordinal': 4, 'ordinal_word': 'Cuarta'})

    def test_first_ordinal_parses(self):
        result = ni.extract_price_redetermination(
            'Aprueba la Primera Actualización de Precios de la Orden de Compra N° 8056-0524-OCA25')
        self.assertEqual(result['ordinal'], 1)

    def test_returns_none_when_no_match(self):
        self.assertIsNone(ni.extract_price_redetermination('Dar de baja bienes inventariados'))
        self.assertIsNone(ni.extract_price_redetermination(None))
        self.assertIsNone(ni.extract_price_redetermination(''))

    def test_returns_none_for_unrecognized_ordinal_word(self):
        # ordinales compuestos (ej. "Décimo Primera") no se parsean -documentado como
        # limitación conocida, no un bug-.
        self.assertIsNone(ni.extract_price_redetermination(
            'Aprueba la Décimo Primera Actualización de Precios de la Orden de Compra N° 8056-0001-OCA25'))


class BuildPriceRedeterminationFlagsTest(unittest.TestCase):
    def _flagged(self, id_norma, ordinal, ordinal_word, orden='8056-0001-OCA25'):
        return {'id_norma': id_norma, 'numero_boletin': 7000, 'fecha_publicacion': '01/01/2026',
                'nombre': f'Disposición N° {id_norma}/DGISIS/26', 'sigla_unidad': 'DGISIS',
                'url_norma': f'http://x/{id_norma}',
                'price_redetermination': {'orden_de_compra': orden, 'ordinal': ordinal, 'ordinal_word': ordinal_word}}

    def test_flags_at_or_above_threshold(self):
        flagged = [self._flagged(1, 3, 'Tercera')]
        flags = ni.build_price_redetermination_flags(flagged)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]['ordinal'], 3)
        self.assertEqual(flags[0]['orden_de_compra'], '8056-0001-OCA25')

    def test_below_threshold_excluded(self):
        flagged = [self._flagged(1, 2, 'Segunda')]
        self.assertEqual(ni.build_price_redetermination_flags(flagged), [])

    def test_norms_without_price_redetermination_excluded(self):
        flagged = [{**self._flagged(1, 3, 'Tercera'), 'price_redetermination': None}]
        self.assertEqual(ni.build_price_redetermination_flags(flagged), [])

    def test_sorted_by_ordinal_descending(self):
        flagged = [self._flagged(1, 3, 'Tercera'), self._flagged(2, 9, 'Novena')]
        flags = ni.build_price_redetermination_flags(flagged)
        self.assertEqual([f['ordinal'] for f in flags], [9, 3])


if __name__ == '__main__':
    unittest.main()
