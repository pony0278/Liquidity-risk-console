"""極小的安全表達式求值器。

規則檔（config/rules.json）裡的 expr 只允許算術、比較、and/or/not 與少數
內建函式。任何名稱在變數表中不存在或值為 None，一律回傳 None（unknown），
讓上層可以把「沒資料」和「條件不成立」分開處理——這兩者在風險監測裡的
意義完全不同。
"""

import ast
import operator

__all__ = ["evaluate", "referenced_names", "ExprError"]


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
        # 三值邏輯：缺資料不能壓掉另一個已經足以成立／否定的條件。
        #   True  or Unknown = True
        #   False or Unknown = Unknown
        #   False and Unknown = False
        #   True  and Unknown = Unknown
        # 這對「VIX > 30 or MOVE > 150」特別重要：MOVE 缺值時，
        # 已知 VIX > 30 仍必須觸發，而不是整條變成無法判定。
        unknown = False
        if isinstance(node.op, ast.And):
            for child in node.values:
                try:
                    if not _eval(child, variables):
                        return False
                except _Unknown:
                    unknown = True
            if unknown:
                raise _Unknown("bool-and")
            return True

        for child in node.values:
            try:
                if _eval(child, variables):
                    return True
            except _Unknown:
                unknown = True
        if unknown:
            raise _Unknown("bool-or")
        return False

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
