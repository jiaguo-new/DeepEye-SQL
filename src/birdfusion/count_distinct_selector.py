"""Answer-free, deliberately conservative COUNT(DISTINCT) selector.

The caller must supply *only public TRAIN* records with db_id, question,
optional evidence, and sql. Source provenance cannot be verified here; never
pass evaluation gold or outcomes. Before counting, the target database and
exact case-folded, whitespace-normalized target questions are excluded.
The question/evidence cue is the regex ``how many|number of|count``. Each
contributing database needs at least five eligible SQLs and at least 80%
COUNT(DISTINCT); at least three such databases and 20 pooled SQLs are needed.
Sources below either per-database threshold do not contribute.

This is an intentionally narrow SQLite AST recognizer, not a SQL validator or
semantic equivalence proof. It rejects unsupported shapes, functions, nested
queries, windows, filters, stars, compound statements, and ambiguous candidate
metadata. It makes no database calls and uses no query results except explicit
opaque successful-result keys for grouping. Result-key equality is trusted as
provided by the caller; the keys must be stable and collision-free. A switch
only adds DISTINCT; all uncertainty abstains. SQL dialect parsing can still
accept syntactically valid constructs unsupported by a particular SQLite
version or schema, and AST equality cannot establish semantic correctness.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re

import sqlglot
from sqlglot import ErrorLevel, exp


_CUE = re.compile(r"\b(?:how\s+many|number\s+of|count)\b", re.IGNORECASE)

# Explicit allowlist: rejecting unknown nodes is safer than attempting to
# enumerate the ever-growing collection of sqlglot unsupported expressions.
_ALLOWED = frozenset(
    getattr(exp, name)
    for name in (
        "Select", "Count", "Distinct", "Column", "Identifier", "Table",
        "TableAlias", "From", "Join", "Where", "Group", "Having", "Order",
        "Ordered", "Limit", "Offset", "Literal", "Null", "Boolean", "EQ",
        "NEQ", "GT", "GTE", "LT", "LTE", "And", "Or", "Not", "Is",
        "In", "Between", "Like", "ILike", "Paren", "Add", "Sub", "Mul",
        "Div", "Mod", "Neg", "Alias", "Collate", "Escape",
    )
)


@dataclass(frozen=True)
class _SQL:
    distinct: bool
    structural: exp.Expression
    without_distinct: exp.Expression


def _question(value):
    return " ".join(value.casefold().split()) if isinstance(value, str) else ""


def _sql(sql):
    if not isinstance(sql, str) or not sql.strip():
        return None
    try:
        parsed = sqlglot.parse(sql, read="sqlite", error_level=ErrorLevel.RAISE)
        if len(parsed) != 1 or not isinstance(parsed[0], exp.Select):
            return None
        tree = parsed[0]
        if any(type(node) not in _ALLOWED for node in tree.walk()):
            return None
        if sum(isinstance(node, exp.Select) for node in tree.walk()) != 1:
            return None
        counts = list(tree.find_all(exp.Count))
        if len(counts) != 1:
            return None
        count = counts[0]
        # Require the COUNT to be a direct top-level SELECT item, optionally
        # aliased, rather than a nested argument or predicate expression.
        if not any(item is count or (isinstance(item, exp.Alias) and item.this is count)
                   for item in tree.expressions):
            return None
        if any(v not in (None, False, [], ()) for k, v in count.args.items()
               if k not in ("this", "big_int")):
            return None
        arg = count.this
        distinct = isinstance(arg, exp.Distinct)
        if distinct:
            if set(arg.args) != {"expressions"} or len(arg.expressions) != 1:
                return None
            arg = arg.expressions[0]
        if not isinstance(arg, exp.Column) or not isinstance(arg.this, exp.Identifier):
            return None
        # Reject catalog/db qualified or otherwise extended column nodes.
        if any(v is not None for k, v in arg.args.items() if k not in ("this", "table")):
            return None
        if arg.args.get("table") is not None and not isinstance(arg.args["table"], exp.Identifier):
            return None
        # A DISTINCT anywhere else is outside this recognizer.
        if len(list(tree.find_all(exp.Distinct))) != int(distinct):
            return None
        structural = tree.copy()
        for node in structural.walk():
            node.comments = None
            # sqlglot's Identifier equality folds unquoted case. Convert the
            # spelling to an exact-comparison string node while preserving the
            # quoted flag so neither spelling nor quoting differences vanish.
            if isinstance(node, exp.Identifier):
                node.set("this", exp.Literal.string(node.this))
        normalized = structural.copy()
        normalized_count = next(normalized.find_all(exp.Count))
        if distinct:
            normalized_count.set("this", normalized_count.this.expressions[0].copy())
        return _SQL(distinct, structural, normalized)
    except (sqlglot.errors.SqlglotError, ValueError, TypeError, RecursionError):
        return None


def cue_statistics(train_records, target_db, target_question):
    """Return aggregate cue provenance; input is an iterable of public TRAIN records.

    A record requires nonempty db_id, question, and sql, and optional string
    evidence. Invalid records are ignored; no per-record content is returned.
    """
    if not isinstance(target_db, str) or not target_db.strip() or not _question(target_question):
        raise ValueError("target db and question are required")
    if isinstance(train_records, (str, bytes, Mapping)):
        raise ValueError("train_records must be an iterable of records")
    target_db_key = target_db.strip().casefold()
    target_q = _question(target_question)
    by_db = defaultdict(lambda: [0, 0])
    for record in train_records:
        if not isinstance(record, Mapping):
            continue
        db, question, evidence, sql = (record.get(k) for k in ("db_id", "question", "evidence", "sql"))
        if not isinstance(db, str) or not db.strip() or not _question(question):
            continue
        if db.strip().casefold() == target_db_key or _question(question) == target_q:
            continue
        if evidence is not None and not isinstance(evidence, str):
            continue
        if not _CUE.search(question + " " + (evidence or "")):
            continue
        info = _sql(sql)
        if info is None:
            continue
        counts = by_db[db.strip().casefold()]
        counts[0] += 1
        counts[1] += int(info.distinct)
    contributing = {db: {"eligible": n, "distinct": d} for db, (n, d) in sorted(by_db.items())
                    if n >= 5 and d * 5 >= n * 4}
    pooled = sum(v["eligible"] for v in contributing.values())
    distinct = sum(v["distinct"] for v in contributing.values())
    return {"contributing_databases": contributing, "database_count": len(contributing),
            "pooled_eligible": pooled, "pooled_distinct": distinct,
            "sufficient": len(contributing) >= 3 and pooled >= 20}


def select_count_distinct(*, train_records, target_db, target_question, candidates,
                          baseline_slot, expected_slot_count, target_evidence=""):
    """Decide whether to switch baseline to the distinct SQL candidate.

    Candidate entries must each contain exactly usable fields: an integer
    ``slot`` (0..expected_slot_count-1), SQL string ``sql``, literal bool
    ``success``, and nonempty string ``result_key``. Only success=True is
    accepted; any nonempty ``error`` field rejects the entire candidate set.
    Exactly two result-key clusters are required, internally identical ASTs,
    one plain and one DISTINCT, differing only in that wrapper. The DISTINCT
    cluster must contain exactly one distinct SQL text after whitespace strip;
    the returned slot is deterministic. Baseline must be in the plain cluster.
    Returns only a decision, reason, optional selected slot, and aggregate
    training provenance. A caller must not infer truth from a switch.
    """
    empty = {"contributing_databases": {}, "database_count": 0,
             "pooled_eligible": 0, "pooled_distinct": 0, "sufficient": False}
    def result(decision, reason, provenance=empty, slot=None):
        return {"decision": decision, "reason": reason, "selected_slot": slot,
                "provenance": provenance}
    try:
        provenance = cue_statistics(train_records, target_db, target_question)
    except (TypeError, ValueError):
        return result("abstain", "invalid_training_input")
    if not isinstance(target_evidence, str):
        return result("abstain", "invalid_target_evidence", provenance)
    if not _CUE.search(target_question + " " + target_evidence):
        return result("abstain", "target_cue_missing", provenance)
    if not provenance["sufficient"]:
        return result("abstain", "insufficient_training_support", provenance)
    if (type(expected_slot_count) is not int or expected_slot_count < 2
        or type(baseline_slot) is not int or not 0 <= baseline_slot < expected_slot_count
        or not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes))
        or len(candidates) != expected_slot_count):
        return result("abstain", "invalid_candidate_slots", provenance)
    clusters = defaultdict(list)
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            return result("abstain", "invalid_candidate_slots", provenance)
        slot = candidate.get("slot")
        if type(slot) is not int or not 0 <= slot < expected_slot_count or slot in seen:
            return result("abstain", "invalid_candidate_slots", provenance)
        seen.add(slot)
        key = candidate.get("result_key")
        if (candidate.get("success") is not True or not isinstance(key, str)
            or not key.strip() or candidate.get("error") not in (None, "")):
            return result("abstain", "unsuccessful_or_unknown_result", provenance)
        info = _sql(candidate.get("sql"))
        if info is None:
            return result("abstain", "unsupported_candidate_sql", provenance)
        clusters[key].append((slot, candidate["sql"].strip(), info))
    if len(clusters) != 2:
        return result("abstain", "expected_two_result_clusters", provenance)
    groups = list(clusters.values())
    for group in groups:
        if any(item[2].structural != group[0][2].structural for item in group):
            return result("abstain", "inconsistent_cluster_sql", provenance)
    if groups[0][0][2].distinct == groups[1][0][2].distinct:
        return result("abstain", "expected_opposite_count_operators", provenance)
    if groups[0][0][2].without_distinct != groups[1][0][2].without_distinct:
        return result("abstain", "candidate_ast_mismatch", provenance)
    distinct_group = next(group for group in groups if group[0][2].distinct)
    plain_group = next(group for group in groups if not group[0][2].distinct)
    if not any(item[0] == baseline_slot for item in plain_group):
        return result("abstain", "baseline_not_plain", provenance)
    if len({item[1] for item in distinct_group}) != 1:
        return result("abstain", "alternate_sql_not_unique", provenance)
    return result("switch", "supported_distinct_alternative", provenance,
                  min(item[0] for item in distinct_group))
