"""
Calculator logic for the on-screen keyboard's calculator panel (no GTK needed).

press(expr, key) -> expr   — apply one calculator button to the expression
evaluate(expr)   -> str    — the answer, DIV_ZERO, or None if not complete yet

Expressions use the display symbols × ÷ − ; evaluation goes through a small
AST whitelist (numbers, + − × ÷, brackets), never eval().
"""

import ast
import operator

DIGITS    = "0123456789"
OPERATORS = "+−×÷"
DIV_ZERO  = "Can't divide by 0"

_TO_PY  = str.maketrans({"×": "*", "÷": "/", "−": "-"})
_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub,
           ast.Mult: operator.mul, ast.Div: operator.truediv}
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
        return expr[:-1]
    if key in DIGITS:
        if _current_number(expr) == "0":
            return expr[:-1] + key            # "0" then "7" → "7", not "07"
        if last == ")":
            expr += "×"                       # "(1+2)3" means (1+2)×3
        return expr + key
    if key == ".":
        num = _current_number(expr)
        if "." in num:
            return expr                       # one point per number
        if last == ")":
            expr += "×"
        return expr + ("." if num else "0.")
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
        if last in DIGITS or last in ".)":
            expr += "×"                       # "2(3)" means 2×(3)
        return expr + "("
    if key == ")":
        if expr.count("(") > expr.count(")") and last not in OPERATORS + "(":
            return expr + ")"
        return expr
    return expr


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
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
    src = expr.translate(_TO_PY)
    src += ")" * max(0, src.count("(") - src.count(")"))   # close open brackets
    if not src or "**" in src or "//" in src:
        return None
    try:
        return format_number(_eval(ast.parse(src, mode="eval")))
    except ZeroDivisionError:
        return DIV_ZERO
    except (SyntaxError, ValueError, TypeError, OverflowError, RecursionError):
        return None
