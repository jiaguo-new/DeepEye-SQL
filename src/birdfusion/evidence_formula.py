"""Deterministic compiler for BIRD evidence calculation pseudo-code.

The input is public evidence only.  The compiler emits a short SQL formula
sketch; it is injected into prompts but never executed.  Ambiguous evidence is
skipped rather than guessed.
"""
from __future__ import annotations

import re


FUNCTION_RE = re.compile(r"^\s*([A-Z]+)\s*\(", re.I)
WHERE_RE = re.compile(r"(?i)\bwhere\b")
TRAILING_100_RE = re.compile(r"(?i)(\s*\*\s*100\s*%)?\s*(as\s+percent)?\s*$")
IDENT_RE = re.compile(r"\b([A-Za-z_][\w.]*)\s*=\s*([^,)]+)")


def _find_close(text: str, start: int) -> int:
    depth = 0
    quote: str | None = None
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in {"'", '"'}:
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _split_args(text: str) -> list[str]:
    return _split_top(text, ",")


def _split_top(text: str, separator: str) -> list[str]:
    args: list[str] = []
    depth = 0
    quote: str | None = None
    escaped = False
    current: list[str] = []
    for ch in text:
        if quote:
            current.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in {"'", '"'}:
            quote = ch
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == separator and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        args.append(tail)
    return args


def _split_where(expr: str) -> tuple[str, str]:
    m = WHERE_RE.search(expr)
    if not m:
        return expr.strip(), ""
    return expr[:m.start()].strip(), expr[m.end():].strip()


def _condition_case(column_expr: str, condition: str, value_literal: str = "1") -> str:
    condition = condition.strip()
    if not condition:
        return column_expr.strip()
    # `col = value` or `col = value and ...`; keep public condition syntax verbatim.
    if re.fullmatch(r"(?i)count\s*\([^)]+\)", column_expr):
        return f"COUNT(CASE WHEN {condition} THEN {column_expr.strip()[6:-1].strip()} END)"
    if re.match(r"(?i)sum\s*\(", column_expr):
        return f"SUM(CASE WHEN {condition} THEN {column_expr.strip()[4:-1].strip()} ELSE 0 END)"
    return f"{column_expr.strip()} AND ({condition})"


def compile_condition(text: str) -> str:
    parts = re.split(r"(!=|<>|>=|<=|=|>|<)", text)
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(part)
        else:
            out.append(compile_atom(part))
    return "".join(out)


def _combine_precompiled(op: str, left: str, right: str) -> str:
    right = compile_atom(right)
    if op == "/":
        return f"CAST({left} AS REAL) / NULLIF({right}, 0)"
    if op == "*":
        return f"({left}) * ({right})"
    if op == "+":
        return f"({left}) + ({right})"
    if op == "-":
        return f"({left}) - ({right})"
    return f"{left} {op} {right}"


def _combine_binary(op: str, left: str, right: str) -> str:
    return _combine_precompiled(op, compile_atom(left), right)


def compile_atom(expr: str) -> str:
    expr = expr.strip().rstrip("%")
    if not expr:
        return expr
    m = FUNCTION_RE.match(expr)
    if not m or expr.endswith(","):
        # `count(A) where B` form.
        inner_match = re.match(r"(?i)^count\s*\((.*?)\)\s+(?:when|where)\s+(.*)$", expr)
        if inner_match:
            return f"COUNT(CASE WHEN {compile_condition(inner_match.group(2).strip())} THEN {compile_atom(inner_match.group(1).strip())} END)"
        inner_match = re.match(r"(?i)^sum\s*\((.*?)\)\s+(?:when|where)\s+(.*)$", expr)
        if inner_match:
            return f"SUM(CASE WHEN {compile_condition(inner_match.group(2).strip())} THEN {compile_atom(inner_match.group(1).strip())} ELSE 0 END)"
        return expr

    func = m.group(1).upper()
    close = _find_close(expr, m.end() - 1)
    if close < 0:
        return expr
    inner = expr[m.end():close].strip()
    trailing = expr[close + 1:].strip()
    args = _split_args(inner)

    def rec(a: str) -> str:
        return compile_atom(a)

    def trailing_condition(text: str) -> str:
        mm = re.match(r"(?i)^(?:where|when)\s+(.*)$", text.strip())
        return compile_condition(mm.group(1).strip()) if mm else ""

    binary = re.match(r"^([+*/-])\s*(.+)$", trailing)
    compiled = None

    if func == "DIVIDE" and len(args) == 2:
        left, right = rec(args[0]), rec(args[1])
        compiled = f"CAST({left} AS REAL) / NULLIF({right}, 0)"
    if compiled is None and func in {"MULTIPLY", "TIMES"} and len(args) == 2:
        left, right = rec(args[0]), rec(args[1])
        compiled = f"({left}) * ({right})"
    if compiled is None and func == "SUBTRACT" and len(args) == 2:
        left, right = rec(args[0]), rec(args[1])
        compiled = f"({left}) - ({right})"
    if compiled is None and func == "ADD" and len(args) == 2:
        left, right = rec(args[0]), rec(args[1])
        compiled = f"({left}) + ({right})"
    if compiled is None and func == "COUNT":
        if len(args) == 1:
            a = args[0].strip()
            then_match = re.match(r"(?i)^(.+?)\s+then\s+(.+)$", a)
            if then_match:
                cond = compile_condition(then_match.group(1).strip())
                target = then_match.group(2).strip()
                compiled = f"COUNT(CASE WHEN {cond} THEN {rec(target)} END)"
            else:
                cond = _split_where(a)[1] or trailing_condition(trailing)
                target = _split_where(a)[0]
                if cond:
                    compiled = f"COUNT(CASE WHEN {cond} THEN {rec(target)} END)"
                elif re.search(r"(?i)[=<>!]", a):
                    compiled = f"SUM(CASE WHEN {compile_condition(a)} THEN 1 ELSE 0 END)"
                else:
                    compiled = f"COUNT({rec(a)})"
    if compiled is None and func == "SUM":
        if len(args) == 1:
            a = args[0].strip()
            cond = _split_where(a)[1] or trailing_condition(trailing)
            target = _split_where(a)[0]
            if cond:
                compiled = f"SUM(CASE WHEN {cond} THEN {rec(target)} ELSE 0 END)"
            elif re.search(r"(?i)[=<>!]", a):
                compiled = f"SUM(CASE WHEN {compile_condition(a)} THEN 1 ELSE 0 END)"
            else:
                compiled = f"SUM({rec(a)})"
    if compiled is None and func in {"AVG", "MAX", "MIN", "ABS"}:
        if len(args) == 1:
            a = args[0].strip()
            cond = _split_where(a)[1] or trailing_condition(trailing)
            target = _split_where(a)[0]
            if cond:
                compiled = f"{func}(CASE WHEN {cond} THEN {rec(target)} END)"
            else:
                compiled = f"{func}({rec(a)})"
    if compiled is None and func == "YEAR":
        compiled = f"CAST(strftime('%Y', {rec(args[0])}) AS INTEGER)"
    if compiled is None and func == "NOW":
        compiled = "CURRENT_DATE"
    if compiled is None and func in {"IIF", "CASE"}:
        compiled = f"{func}({', '.join(rec(a) for a in args)})"
    if compiled is None:
        return expr
    if binary:
        return _combine_precompiled(binary.group(1), compiled, binary.group(2))
    return compiled


def _looks_formula(lhs: str, rhs: str) -> bool:
    if len(lhs) > 130 or len(rhs) > 500:
        return False
    if not re.search(
        r"(?i)\b(rate|ratio|percentage|percent|proportion|average|avg|total|count|number|"
        r"difference|calculation|formula|most|least|fewest|highest|lowest|largest|smallest|"
        r"maximum|minimum)\b",
        lhs,
    ):
        return False
    return bool(re.search(r"(?i)\b(divide|multiply|subtract|add|count|sum|avg|max|min|year|now)\s*\(|/|\*100", rhs))


def _balanced_parentheses(text: str) -> bool:
    depth = 0
    quote: str | None = None
    escaped = False
    for ch in text:
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in {"'", '"'}:
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and quote is None


def _compiled_usable(text: str) -> bool:
    if not text or "[" in text or "]" in text or "{" in text or "}" in text:
        return False
    if not _balanced_parentheses(text):
        return False
    if re.search(r"(?i)\b(condition|placeholder|expression)\b", text):
        return False
    if re.search(r"(?i)\b(AVG|SUM|MAX|MIN)\s*\(\s*(COUNT|SUM|AVG|MAX|MIN)\s*\(", text):
        return False
    for condition in re.findall(r"(?i)CASE\s+WHEN\s+(.+?)\s+THEN", text):
        if re.search(r"(?i)\b(COUNT|SUM|AVG|MAX|MIN)\s*\(", condition):
            return False
    return bool(re.search(r"(?i)\b(COUNT|SUM|AVG|MAX|MIN|ORDER BY)\b", text) or "/" in text or "*" in text)


def _normalize_evidence_spacing(text: str) -> str:
    text = re.sub(r">\s*=", ">=", text)
    text = re.sub(r"<\s*=", "<=", text)
    text = re.sub(r"!\s*=", "!=", text)
    return re.sub(r"(?<![<>!=])=\s*=", "==", text)


def _apply_average_denominator(expr: str, lhs: str) -> str:
    """BIRD 'average/ratio = count(entity) / count(group key)' usually needs DISTINCT key."""
    if not re.search(r"(?i)\b(average|avg|ratio|number|most|least)\b", lhs):
        return expr

    def replace(match: re.Match[str]) -> str:
        numerator, denominator = match.group(1).strip(), match.group(2).strip()
        if denominator.upper().startswith("DISTINCT ") or numerator.lower() == denominator.lower():
            return match.group(0)
        return f"CAST(COUNT({numerator}) AS REAL) / NULLIF(COUNT(DISTINCT {denominator}), 0)"

    return re.sub(
        r"(?i)CAST\(COUNT\(([^()]+)\) AS REAL\) / NULLIF\(COUNT\(([^()]+)\), 0\)",
        replace,
        expr,
    )


def compile_evidence(question: str, evidence: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not evidence:
        return result
    # BIRD sometimes puts semicolon-delimited formulas inside a comma sentence.
    parts = [_normalize_evidence_spacing(p.strip()) for p in re.split(r";|\n", evidence) if p.strip()]
    formulas: list[str] = []
    for part in parts:
        # A formula clause may be preceded by ordinary text; take the last =.
        m = re.search(r"^(.{1,140}?)\s*=\s*(.+)$", part, re.S)
        if not m:
            m = re.search(r"(?i)^(.{1,140}?)\s+refers to\s+(.+)$", part, re.S)
        if not m:
            continue
        lhs, rhs = m.group(1).strip(), m.group(2).strip()
        if not _looks_formula(lhs, rhs):
            continue
        rhs = TRAILING_100_RE.sub("", rhs).strip()
        if re.search(r"(?i)percentage|percent", lhs) and not re.search(r"\*\s*100", rhs):
            rhs = rhs + " * 100"
        compiled = compile_atom(rhs)
        # BIRD evidence often writes MAX(COUNT(x)); SQLite does not accept that
        # literally. Express the requested top-1 grain instead.
        compiled = re.sub(r"(?i)\bMAX\(COUNT\((.+?)\)\)", r"ORDER BY COUNT(\1) DESC LIMIT 1", compiled)
        compiled = re.sub(r"(?i)\bMIN\(COUNT\((.+?)\)\)", r"ORDER BY COUNT(\1) ASC LIMIT 1", compiled)
        # Some BIRD formulas use `A / B` instead of DIVIDE(...).
        if "/" in compiled and "CAST" not in compiled:
            pieces = [compile_atom(p.strip()) for p in _split_top(compiled, "/")]
            if len(pieces) == 2 and all(pieces):
                compiled = f"CAST({pieces[0]} AS REAL) / NULLIF({pieces[1]}, 0)"
        compiled = _apply_average_denominator(compiled, lhs)
        if compiled and compiled != rhs and _compiled_usable(compiled):
            formulas.append(f"{lhs[:120]} = {compiled[:600]}")
    if not formulas:
        return result
    result["compiled_formulas"] = "PUBLIC EVIDENCE CALCULATION (follow this formula exactly):\n" + "\n".join(dict.fromkeys(formulas))
    return result


def formula_block(compiled: dict[str, str]) -> str:
    return compiled.get("compiled_formulas", "")
