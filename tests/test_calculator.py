"""Tests for calculator.py (calculator panel logic). No GTK needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calculator import DIV_ZERO, evaluate, insert_number, press  # noqa: E402


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


def seq(*buttons: str) -> str:
    """Press buttons whose labels may be longer than one character."""
    expr = ""
    for b in buttons:
        expr = press(expr, b)
    return expr


class ScientificTests(unittest.TestCase):
    def test_square_and_power(self):
        self.assertEqual(evaluate(keys("5²")), "25")
        self.assertEqual(evaluate(keys("2^10")), "1024")
        self.assertEqual(evaluate(keys("2^0.5")), "1.41421356237")
        self.assertEqual(evaluate(keys("2+^3")), "8")         # ^ replaces +
        self.assertEqual(keys("²"), "")                        # needs a number first

    def test_huge_power_refused(self):
        self.assertIsNone(evaluate("9^99999999"))

    def test_square_root(self):
        self.assertEqual(keys("√9"), "√(9")
        self.assertEqual(evaluate(keys("√9")), "3")
        self.assertEqual(evaluate(keys("2√9")), "6")           # 2×√(9)
        self.assertIsNone(evaluate(keys("√−4")))
        self.assertEqual(keys("2√⌫"), "2×")                    # ⌫ removes "√(" together

    def test_percent(self):
        self.assertEqual(evaluate(keys("50%")), "0.5")
        self.assertEqual(evaluate(keys("200×10%")), "20")
        self.assertEqual(evaluate(keys("50%2")), "1")          # 50%×2

    def test_pi(self):
        self.assertEqual(evaluate(keys("2π")), "6.28318530718")
        self.assertEqual(evaluate(keys("π²")), "9.86960440109")

    def test_plus_minus(self):
        self.assertEqual(keys("5±"), "−5")
        self.assertEqual(keys("5±±"), "5")
        self.assertEqual(keys("3+5±"), "3−5")
        self.assertEqual(keys("3−5±"), "3+5")
        self.assertEqual(keys("3×5±"), "3×−5")
        self.assertEqual(evaluate(keys("3×5±")), "-15")
        self.assertEqual(keys("±"), "−")
        self.assertEqual(keys("±±"), "")

    def test_reciprocal(self):
        self.assertEqual(seq("4", "1/x"), "0.25")
        self.assertEqual(seq("1", "+", "1", "1/x"), "0.5")
        self.assertEqual(seq("0", "1/x"), "0")
        self.assertEqual(seq("−", "2", "1/x"), "−0.5")

    def test_insert_number(self):
        self.assertEqual(insert_number("", "12"), "12")
        self.assertEqual(insert_number("3+", "12"), "3+12")
        self.assertEqual(insert_number("3", "12"), "3×12")
        self.assertEqual(insert_number("3×", "-2"), "3×(−2)")
        self.assertEqual(insert_number("", "-2"), "−2")
        self.assertEqual(insert_number("3+", "1e+20"), "3+")


if __name__ == "__main__":
    unittest.main()
