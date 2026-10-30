"""
Calculator logic for the on-screen keyboard's calculator panel (no GTK needed).

press(expr, key) -> expr   — apply one calculator button to the expression
evaluate(expr)   -> str    — the answer, DIV_ZERO, or None if not complete yet

insert_number(expr, number) -> expr — put a number (the last answer) in

Expressions use the display symbols × ÷ − ^ ² % √( π ; evaluation goes
through a small AST whitelist (numbers, + − × ÷ and powers, brackets,
square root), never eval().
"""

import ast
import math
import operator

DIGITS    = "0123456789"
OPERATORS = "+−×÷^"
DIV_ZERO  = "Can't divide by 0"
MAX_POWER = 1000                          # 2^1000 is fine; 9^99999999 would hang

# After one of these, a digit, "(", "√" or "π" means "times" ("2π" is 2×π)
_ENDS_VALUE = DIGITS + ".)²%π"

_TO_PY  = str.maketrans({"×": "*", "÷": "/", "−": "-", "^": "**", "²": "**2",
                         "%": "/100", "π": f"({math.pi!r})", "√": "sqrt"})


def _power(base, exp):
    if abs(exp) > MAX_POWER:
        raise OverflowError
    if isinstance(base, int) and isinstance(exp, int) and exp >= 0:
        return base ** exp                # exact: 2^60 shows every digit
    return float(base) ** exp             # a fractional root of a negative is complex → None


_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub,
           ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: _power}
_UNARY  = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _current_number(expr: str) -> str:
    """The number being typed at the end of expr ("12.5" in "3+12.5")."""
    i = len(expr)
    while i and (expr[i - 1] in DIGITS or expr[i - 1] == "."):
        i -= 1
    return expr[i:]


def press(expr: str, key: str) -> str:
    """Apply one calculator button to the expression and return the new one."""
    last = expr[-1:] or " "                   # not "": "" is `in` every string
    if key == "C":
        return ""
    if key == "⌫":
        return expr[:-2] if expr.endswith("√(") else expr[:-1]
    if key in DIGITS:
        if _current_number(expr) == "0":
            return expr[:-1] + key            # "0" then "7" → "7", not "07"
        if last in ")²%π":
            expr += "×"                       # "(1+2)3" means (1+2)×3
        return expr + key
    if key == ".":
        num = _current_number(expr)
        if "." in num:
            return expr                       # one point per number
        if last in ")²%π":
            expr += "×"
        return expr + ("." if num else "0.")
    if key in ("π", "√"):
        if last in _ENDS_VALUE:
            expr = expr.rstrip(".") + "×"     # "2π" means 2×π
        return expr + ("π" if key == "π" else "√(")
    if key in ("²", "%"):
        if last in DIGITS + ".)π" or (key == "%" and last == "²"):
            return expr.rstrip(".") + key
        return expr
    if key == "±":
        return _toggle_sign(expr)
    if key == "1/x":
        answer = evaluate(expr)
        if answer is None or answer == DIV_ZERO or float(answer) == 0:
            return expr
        return format_number(1 / float(answer)).replace("-", "−")
    if key in OPERATORS:
        if not expr or last == "(":
            return expr + "−" if key == "−" else expr   # only a minus can start
        if last in OPERATORS:
            if len(expr) == 1 or expr[-2] == "(":
                return expr                   # keep a lone leading minus
            return expr[:-1] + key            # change the operator
        if last == ".":
            expr = expr[:-1]
        return expr + key
    if key == "(":
        if last in _ENDS_VALUE:
            expr += "×"                       # "2(3)" means 2×(3)
        return expr + "("
    if key == ")":
        if expr.count("(") > expr.count(")") and last not in OPERATORS + "(":
            return expr + ")"
        return expr
    return expr


def _toggle_sign(expr: str) -> str:
    """± flips the sign of the number being typed: 3+5 ↔ 3−5, 3×5 ↔ 3×−5."""
    num = _current_number(expr)
    i = len(expr) - len(num)
    before = expr[i - 1:i]
    if not num:                               # nothing typed yet: start a negative
        if not expr or expr[-1] in "+×÷^(":
            return expr + "−"
        if expr[-1] == "−" and (len(expr) == 1 or expr[-2] in OPERATORS + "("):
            return expr[:-1]
        return expr
    if before == "−":
        if i == 1 or expr[i - 2] in OPERATORS + "(":
            return expr[:i - 1] + num         # −5 → 5
        return expr[:i - 1] + "+" + num       # 3−5 → 3+5
    if before == "+":
        return expr[:i - 1] + "−" + num       # 3+5 → 3−5
    if before in ("", "×", "÷", "^", "("):
        return expr[:i] + "−" + num
    return expr                               # after √( etc. – nothing to flip


def insert_number(expr: str, number: str) -> str:
    """Add number (e.g. the last answer) where the next number goes."""
    if not number or "e" in number:          # 1e+20 can't be typed back in
        return expr
    number = number.replace("-", "−")
    if number.startswith("−") and expr:
        number = f"({number})"                # 3×(−2), not 3×−2 read as 3 then −2
    if expr[-1:] and expr[-1] in _ENDS_VALUE:
        expr = expr.rstrip(".") + "×"
    return expr + number


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "sqrt" and len(node.args) == 1 and not node.keywords):
        return math.sqrt(_eval(node.args[0]))   # negative → ValueError → None
    raise ValueError("not allowed")


def format_number(value) -> str:
    """Whole numbers without ".0"; others to 12 significant digits, so
    0.1+0.2 shows 0.3."""
    if isinstance(value, int) and abs(value) < 10 ** 15:
        return str(value)
    value = float(value)
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return format(value, ".12g")


def evaluate(expr: str) -> str | None:
    """The answer for expr, DIV_ZERO, or None if it isn't a complete sum."""
    if not expr or any(ch in expr for ch in "*/_"):   # only our own symbols
        return None
    src = expr.translate(_TO_PY)
    src += ")" * max(0, src.count("(") - src.count(")"))   # close open brackets
    try:
        return format_number(_eval(ast.parse(src, mode="eval")))
    except ZeroDivisionError:
        return DIV_ZERO
    except (SyntaxError, ValueError, TypeError, OverflowError, RecursionError):
        return None
