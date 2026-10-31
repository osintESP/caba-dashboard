import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import impact_report as ir  # noqa: E402


def cat(total, tech, sate):
    return {'summary': {'total_awarded_ars': total, 'tech_awarded_ars': tech, 'awards_counted': 100},
            'audit_signals': {'direct_or_limited_share_pct': 14.3, 'vendor_concentration_by_organismo': []},
            'tender_index': {'3202-2041-LPU25': {'amount_ars': sate, 'monto_reconstruido_ars': 6_624_000_000}}}


class ImpactReportTest(unittest.TestCase):
    def test_flags_the_real_dedupe_change(self):
        # El fix de #22: tecnología 46.879M -> 26.020M. Tiene que exigir explicación.
        text, flagged = ir.report(cat(287_867e6, 46_879e6, 19_253_760_000), cat(209_281e6, 26_020e6, 6_624_000_000))
        self.assertTrue(flagged)
        self.assertIn('REVISAR', text)
        self.assertIn('3202-2041-LPU25', text)

    def test_no_change(self):
        c = cat(1e9, 1e8, 6_624_000_000)
        text, flagged = ir.report(c, c)
        self.assertFalse(flagged)
        self.assertIn('Sin cambios en montos', text)

    def test_small_change_not_flagged(self):
        _, flagged = ir.report(cat(1e9, 1e8, 1), cat(1.05e9, 1.04e8, 1))
        self.assertFalse(flagged)


if __name__ == '__main__':
    unittest.main()
