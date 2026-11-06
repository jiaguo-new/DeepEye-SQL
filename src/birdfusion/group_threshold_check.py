"""Strict static checks for the group-count threshold template (research only).

Normative implementation for the preregistered TRAIN-only feasibility study:
the question template and the reference-SQL HAVING shape defined here are the
frozen specification, not approximations of a prose description. Pure static
parsing only: no SQL execution, no database, no network, no model.

Applicability never implies correctness: agreement of a reference comparator
direction is structural evidence only, and the template does not prove what the
counted entity or the group is.
"""
from __future__ import annotations

from collections.abc import Mapping
import re

import sqlglot
from sqlglot import ErrorLevel, exp

# Exact, case-sensitive, single-space ASCII sentence. Group and child are 1-3
# alphabetic words; N has no leading zeros and is 1..999. Nothing else matches.
_TEMPLATE = re.compile(
    r"^List each ([A-Za-z]+(?: [A-Za-z]+){0,2}) with (at least|more than) "
    r"([1-9][0-9]{0,2}) ([A-Za-z]+(?: [A-Za-z]+){0,2})\.$"
)

# Frozen SQLite AST allowlist. Note: ILIKE is deliberately absent (SQLite has
# no ILIKE), and Star is required for COUNT(*).
_ALLOWED = frozenset(
    getattr(exp, name)
    for name in (
        "Select", "Count", "Distinct", "Column", "Identifier", "Table",
        "TableAlias", "From", "Join", "Where", "Group", "Having", "Order",
        "Ordered", "Limit", "Offset", "Literal", "Null", "Boolean", "Star",
        "EQ", "NEQ", "GT", "GTE", "LT", "LTE", "And", "Or", "Not", "Is",
        "In", "Between", "Like", "Paren", "Add", "Sub", "Mul", "Div", "Mod",
        "Neg", "Alias", "Collate", "Escape",
    )
)

_FIELDS = ("db_id", "question", "evidence", "SQL")


def extract_threshold(question):
    """Return {"group", "direction", "n", "child"} for an exact template match.

    Evidence is intentionally not consulted here; callers must reject nonempty
    evidence themselves. Any deviation from the frozen template returns None.
    """
    if not isinstance(question, str):
        return None
    match = _TEMPLATE.match(question)
    if match is None:
        return None
    group, direction, n, child = match.groups()
    return {"group": group, "direction": direction, "n": int(n), "child": child}


def reference_having(sql):
    """Classify the HAVING threshold shape of one flat SELECT reference.

    Returns {"status": "ok", "op": ">" or ">=", "n": int} exactly when: the SQL
    is one single flat SELECT whose every node is allowlisted; there is exactly
    one HAVING; after unwrapping parentheses its predicate is a binary GT/GTE
    comparison whose left operand is COUNT(*) with a plain integer literal
    right operand n in 1..999; and no COUNT anywhere in the query is DISTINCT.
    A SELECT DISTINCT projection does not block applicability; a DISTINCT
    inside any COUNT does. Any other shape returns {"status": reason} with one
    of: invalid_sql, not_single_select, unsupported_node, no_having,
    multiple_having, distinct_count_present, comparator_not_gt_gte,
    count_not_left, count_not_star, n_invalid. Reasons are mutually exclusive
    with the precedence order listed here.
    """
    if not isinstance(sql, str) or not sql.strip():
        return {"status": "invalid_sql"}
    try:
        parsed = sqlglot.parse(sql, read="sqlite", error_level=ErrorLevel.RAISE)
    except (sqlglot.errors.SqlglotError, ValueError, TypeError, RecursionError):
        return {"status": "invalid_sql"}
    if len(parsed) != 1:
        return {"status": "invalid_sql"}
    selects = [node for node in parsed[0].walk() if isinstance(node, exp.Select)]
    if len(selects) != 1:
        return {"status": "not_single_select"}
    tree = selects[0]
    if any(type(node) not in _ALLOWED for node in tree.walk()):
        return {"status": "unsupported_node"}
    havings = list(tree.find_all(exp.Having))
    if not havings:
        return {"status": "no_having"}
    if len(havings) > 1:
        return {"status": "multiple_having"}
    if any(isinstance(count.this, exp.Distinct)
           for count in tree.find_all(exp.Count)):
        return {"status": "distinct_count_present"}
    predicate = havings[0].this
    while isinstance(predicate, exp.Paren):
        predicate = predicate.this
    if not isinstance(predicate, (exp.GT, exp.GTE)):
        return {"status": "comparator_not_gt_gte"}
    left, right = predicate.this, predicate.expression
    if not isinstance(left, exp.Count):
        return {"status": "count_not_left"}
    if not isinstance(left.this, exp.Star) or left.args.get("distinct") is not None:
        return {"status": "count_not_star"}
    if not isinstance(right, exp.Literal) or not right.is_number:
        return {"status": "n_invalid"}
    try:
        n = int(right.this)
    except (TypeError, ValueError):
        return {"status": "n_invalid"}
    if not 1 <= n <= 999:
        return {"status": "n_invalid"}
    return {"status": "ok", "op": ">" if isinstance(predicate, exp.GT) else ">=", "n": n}


def classify_record(record):
    """Classify one public TRAIN record for the feasibility denominator.

    The record mapping must carry exactly db_id, question, evidence and SQL.
    Invalid records return category invalid_record instead of raising; the
    caller decides fail-closed policy. No SQL text, question text, or result
    data is included in the returned summary fields.
    """
    if not isinstance(record, Mapping) or set(record) != set(_FIELDS):
        return {"category": "invalid_record"}
    db_id, question, evidence, sql = (record[key] for key in _FIELDS)
    if not all(isinstance(value, str) for value in (db_id, question, evidence, sql)):
        return {"category": "invalid_record"}
    if evidence != "":
        return {"category": "evidence_nonempty", "db_id": db_id}
    template = extract_threshold(question)
    if template is None:
        return {"category": "no_template_match", "db_id": db_id}
    reference = reference_having(sql)
    if reference["status"] != "ok":
        return {"category": "unsupported_reference", "db_id": db_id,
                "reason": reference["status"]}
    direction, n = template["direction"], template["n"]
    n_equal = reference["n"] == n
    agree = n_equal and ((direction == "at least" and reference["op"] == ">=")
                         or (direction == "more than" and reference["op"] == ">"))
    return {"category": "applicable", "db_id": db_id, "direction": direction,
            "n_equal": n_equal, "agree": agree}
