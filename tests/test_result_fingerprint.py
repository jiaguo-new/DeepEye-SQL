import math
import unittest

from birdfusion.sql_exec import result_key


class ResultFingerprintTests(unittest.TestCase):
    def test_numeric_keys_follow_python_set_equality(self):
        cases = [
            ([(0,)], [(-0.0,)], True),
            ([(1,)], [(1.0,)], True),
            ([(2**53 + 1,)], [(float(2**53 + 1),)], False),
            ([(10**21,)], [(1e21,)], True),
            ([(float('inf'),)], [(float('-inf'),)], False),
            ([(1,), (1.0,)], [(1,)], True),
            ([(1,)], [('1',)], False),
            ([(None,)], [('',)], False),
        ]
        for left, right, expected in cases:
            with self.subTest(left=left, right=right):
                self.assertEqual(set(left) == set(right), expected)
                self.assertEqual(result_key(left) == result_key(right), expected)

    def test_nan_fails_closed(self):
        with self.assertRaises(ValueError):
            result_key([(math.nan,)])


if __name__ == '__main__':
    unittest.main()
