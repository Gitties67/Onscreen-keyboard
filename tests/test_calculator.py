"""Tests for calculator.py (calculator panel logic). No GTK needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calculator import DIV_ZERO, evaluate, press  # noqa: E402


def keys(seq: str) -> str:
    """Press each button in seq (one character per button)."""
    expr = ""
    for k in seq:
        expr = press(expr, k)
    return expr


class PressTests(unittest.TestCase):
    def test_digits_and_operators(self):
        self.assertEqual(keys("12+3"), "12+3")

    def test_no_leading_zero(self):
        self.assertEqual(keys("07"), "7")
        self.assertEqual(keys("1+07"), "1+7")
        self.assertEqual(keys("10"), "10")

    def test_one_point_per_number(self):
        self.assertEqual(keys("1.2.3"), "1.23")
        self.assertEqual(keys("."), "0.")
        self.assertEqual(keys("1+."), "1+0.")

    def test_operator_replaces_operator(self):
        self.assertEqual(keys("5+×2"), "5×2")

    def test_only_minus_can_start(self):
        self.assertEqual(keys("×"), "")
        self.assertEqual(keys("−5"), "−5")
        self.assertEqual(keys("−+"), "−")

    def test_brackets(self):
        self.assertEqual(keys("2(3)"), "2×(3)")
        self.assertEqual(keys("(1+2)3"), "(1+2)×3")
        self.assertEqual(keys(")"), "")
        self.assertEqual(keys("(1+)"), "(1+")

    def test_backspace_and_clear(self):
        self.assertEqual(keys("12⌫"), "1")
        self.assertEqual(keys("12+3C"), "")


class EvaluateTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(evaluate("2+3×4"), "14")
        self.assertEqual(evaluate("(2+3)×4"), "20")
        self.assertEqual(evaluate("7÷2"), "3.5")
        self.assertEqual(evaluate("8÷2"), "4")
        self.assertEqual(evaluate("−5+2"), "-3")

    def test_float_noise_hidden(self):
        self.assertEqual(evaluate("0.1+0.2"), "0.3")

    def test_open_bracket_closed_for_preview(self):
        self.assertEqual(evaluate("2×(3+4"), "14")

    def test_incomplete(self):
        self.assertIsNone(evaluate(""))
        self.assertIsNone(evaluate("5+"))
        self.assertIsNone(evaluate("("))

    def test_divide_by_zero(self):
        self.assertEqual(evaluate("5÷0"), DIV_ZERO)

    def test_rejects_anything_else(self):
        self.assertIsNone(evaluate("2**8"))
        self.assertIsNone(evaluate("__import__('os')"))


if __name__ == "__main__":
    unittest.main()
