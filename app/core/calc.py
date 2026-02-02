from __future__ import annotations

import ast
from decimal import Decimal

MAX_EXPRESSION_LENGTH = 200


class CalcError(ValueError):
    """Raised when the expression is invalid or unsafe."""


_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
)

_ALLOWED_BIN_OPS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
)

_ALLOWED_UNARY_OPS = (
    ast.UAdd,
    ast.USub,
)


def parse_and_eval(expression: str) -> Decimal | int:
    if not expression or not expression.strip():
        raise CalcError("Выражение пустое.")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise CalcError("Слишком длинное выражение.")
    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalcError("Некорректное выражение.") from exc
    _validate_ast(parsed)
    result = _eval_node(parsed)
    if isinstance(result, Decimal) and result == result.to_integral_value():
        return int(result)
    return result


def _validate_ast(node: ast.AST) -> None:
    for child in ast.walk(node):
        if isinstance(child, _ALLOWED_BIN_OPS + _ALLOWED_UNARY_OPS):
            continue
        if not isinstance(child, _ALLOWED_NODES):
            raise CalcError("Недопустимые элементы выражения.")
        if isinstance(child, ast.BinOp) and not isinstance(child.op, _ALLOWED_BIN_OPS):
            raise CalcError("Недопустимая операция.")
        if isinstance(child, ast.UnaryOp) and not isinstance(child.op, _ALLOWED_UNARY_OPS):
            raise CalcError("Недопустимая операция.")


def _eval_node(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CalcError("Разрешены только числа.")
        return Decimal(str(value))
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise CalcError("Недопустимая операция.")
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise CalcError("Деление на ноль.")
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            if right == 0:
                raise CalcError("Деление на ноль.")
            return left // right
        if isinstance(node.op, ast.Mod):
            if right == 0:
                raise CalcError("Деление на ноль.")
            return left % right
        if isinstance(node.op, ast.Pow):
            return left ** right
        raise CalcError("Недопустимая операция.")
    raise CalcError("Недопустимое выражение.")


def _selftest() -> None:
    assert parse_and_eval("2+2") == 4
    assert parse_and_eval("10/4") == Decimal("2.5")
    assert parse_and_eval("5//2") == 2
    assert parse_and_eval("2**3") == 8
    assert parse_and_eval("-3 + 1") == -2


if __name__ == "__main__":
    _selftest()
    print("calc selftest ok")
