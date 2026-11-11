"""Qualified host scalar primitive and strict arithmetic-profile identity."""

import math
import unittest
from unittest.mock import patch

from ow174.game import scalar_math


@unittest.skipUnless(scalar_math.supported(), 'qualified Windows x64 instruction backend required')
class ScalarMathTests(unittest.TestCase):
    def test_instruction_special_values_and_approximation_bound(self):
        self.assertEqual(scalar_math.reciprocal_sqrt(0), math.inf)
        self.assertEqual(scalar_math.reciprocal_sqrt(math.inf), 0)
        self.assertTrue(math.isnan(scalar_math.reciprocal_sqrt(-1)))
        for value in (.01, .5, 1, 2, 25, 10000):
            relative = abs(scalar_math.reciprocal_sqrt(value) * math.sqrt(value) - 1)
            self.assertLessEqual(relative, 1.5 * 2 ** -12)

    def test_profile_is_detached_and_ignores_sticky_status_flags(self):
        before = scalar_math.profile()
        scalar_math.reciprocal_sqrt(.001)
        self.assertEqual(scalar_math.profile(), before)
        self.assertEqual(before['mxcsr_controls'] & 0x3F, 0)
        self.assertEqual(before['probe_count'], 261)
        self.assertEqual(len(before['probe_sha256']), 64)
        before['probe_sha256'] = 'modified'
        self.assertNotEqual(scalar_math.profile(), before)

    def test_closed_kernel_cannot_be_called_or_freed_twice(self):
        kernel = scalar_math._Kernel()
        self.addCleanup(kernel.close)
        with patch.object(scalar_math, '_kernel', kernel):
            self.assertGreater(scalar_math.reciprocal_sqrt(25), 0)
            kernel.close()
            kernel.close()
            with self.assertRaises(scalar_math.ScalarMathUnavailable):
                scalar_math.reciprocal_sqrt(25)

    def test_unqualified_controls_fail_without_changing_process_control_state(self):
        kernel = scalar_math._get_kernel()

        def unsupported(output):
            output._obj.value = 0x9FC0  # fixture only; no LDMXCSR executes

        with patch.object(kernel, 'control', side_effect=unsupported):
            self.assertFalse(scalar_math.profile()['distance_controls_qualified'])
            with self.assertRaises(scalar_math.ScalarMathUnavailable):
                scalar_math.reciprocal_sqrt(25)


class UnsupportedScalarMathTests(unittest.TestCase):
    def test_unqualified_platform_has_no_ideal_sqrt_fallback(self):
        with patch.object(scalar_math, 'supported', return_value=False):
            self.assertEqual(scalar_math.profile()['backend'], 'unavailable')
            with self.assertRaises(scalar_math.ScalarMathUnavailable):
                scalar_math._Kernel()


if __name__ == '__main__':
    unittest.main()
