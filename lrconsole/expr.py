"""極小的安全表達式求值器。

規則檔（config/rules.json）裡的 expr 只允許算術、比較、and/or/not 與少數
內建函式。任何名稱在變數表中不存在或值為 None，一律回傳 None（unknown），
讓上層可以把「沒資料」和「條件不成立」分開處理——這兩者在風險監測裡的
意義完全不同。
"""

import ast
import operator

__all__ = ["evaluate", "referenced_names", "comparison_terms", "ExprError"]


class ExprError(ValueError):
    """表達式本身寫錯（語法或用了不允許的語法節點）。"""


class _Unknown(Exception):
    """求值過程中碰到沒有資料的變數。"""


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_CMP_OPS = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}

_FUNCS = {"abs": abs, "min": min, "max": max, "round": round}

_CONSTS = {"true": True, "false": False, "True": True, "False": False, "None": None}


def _eval(node, variables):
    if isinstance(node, ast.Expression):
        return _eval(node.body, variables)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool)) or node.value is None:
            return node.value
        raise ExprError("只允許數值與布林常數：%r" % (node.value,))

    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        if node.id not in variables:
            raise _Unknown(node.id)
        value = variables[node.id]
        if value is None:
            raise _Unknown(node.id)
        return value

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not _eval(node.operand, variables)
        if isinstance(node.op, ast.USub):
            return -_eval(node.operand, variables)
        if isinstance(node.op, ast.UAdd):
            return +_eval(node.operand, variables)
        raise ExprError("不支援的一元運算子")

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ExprError("不支援的二元運算子")
        return op(_eval(node.left, variables), _eval(node.right, variables))

    if isinstance(node, ast.BoolOp):
        # and/or 不做短路：任一分支 unknown 就整條 unknown，避免
        # 「另一半沒資料」被靜靜當成 False。
        values = [_eval(v, variables) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        return any(values)

    if isinstance(node, ast.Compare):
        left = _eval(node.left, variables)
        for op_node, right_node in zip(node.ops, node.comparators):
            op = _CMP_OPS.get(type(op_node))
            if op is None:
                raise ExprError("不支援的比較運算子")
            right = _eval(right_node, variables)
            if not op(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.IfExp):
        return _eval(node.body, variables) if _eval(node.test, variables) else _eval(node.orelse, variables)

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ExprError("只允許呼叫 abs／min／max／round")
        if node.keywords:
            raise ExprError("函式呼叫不接受關鍵字引數")
        return _FUNCS[node.func.id](*[_eval(a, variables) for a in node.args])

    raise ExprError("不支援的語法：%s" % type(node).__name__)


def evaluate(expr, variables):
    """求值。回傳 True／False／None(unknown)。"""
    if expr is None:
        return None
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExprError("表達式語法錯誤：%s（%s）" % (expr, exc)) from exc
    try:
        result = _eval(tree, variables)
    except _Unknown:
        return None
    except ZeroDivisionError:
        return None
    if isinstance(result, bool) or result is None:
        return result
    return bool(result)


def evaluate_value(expr, variables):
    """求值並回傳數值（給 derived 指標用）。無資料時回傳 None。"""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExprError("表達式語法錯誤：%s（%s）" % (expr, exc)) from exc
    try:
        return _eval(tree, variables)
    except (_Unknown, ZeroDivisionError):
        return None


def referenced_names(expr):
    """列出表達式引用到的變數名稱（用於錯誤訊息與 self-test）。"""
    if not expr:
        return set()
    tree = ast.parse(expr, mode="eval")
    return {
        n.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id not in _CONSTS and n.id not in _FUNCS
    }


_FLIP = {">": "<", ">=": "<=", "<": ">", "<=": ">="}
_OP_TEXT = {ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<="}


def _number(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _number(node.operand)
        if inner is not None:
            return -inner if isinstance(node.op, ast.USub) else inner
    return None


def _term(node):
    """`變數 比較 常數`（或反過來寫）→ (變數, 方向, 門檻)；其他形狀回傳 None。"""
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return None
    op = _OP_TEXT.get(type(node.ops[0]))
    if op is None:
        return None
    left, right = node.left, node.comparators[0]
    if isinstance(left, ast.Name) and left.id not in _CONSTS and _number(right) is not None:
        return (left.id, op, _number(right))
    if isinstance(right, ast.Name) and right.id not in _CONSTS and _number(left) is not None:
        return (right.id, _FLIP[op], _number(left))
    return None


def comparison_terms(expr):
    """把簡單的門檻規則拆成 ("any"|"all", [(變數, 方向, 門檻), ...])。

    給「距下一階還有多遠」用：門檻直接從規則本身讀，不在別處再寫一份——
    兩份遲早會不一致，改了規則、刻度卻還指著舊門檻。只認得一層 and／or
    串起來的 `變數 > 常數` 這類比較；算術、函式、and／or 混用一律回傳
    None，由上層顯示成「無法換算」，而不是硬猜一個意思。
    """
    if not expr:
        return None
    try:
        tree = ast.parse(expr, mode="eval").body
    except SyntaxError:
        return None
    if isinstance(tree, ast.BoolOp):
        mode = "all" if isinstance(tree.op, ast.And) else "any"
        terms = [_term(v) for v in tree.values]
    else:
        mode, terms = "any", [_term(tree)]
    if not terms or any(t is None for t in terms):
        return None
    return mode, terms
