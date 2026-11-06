"""Offline train-only compiler for reusable, cross-database question conventions.

Gold SQL from public train rows may be used to compile this artifact. MiniDev and
Dev/Test gold must never be passed to this builder. The resulting cards are public
prompt features and candidate-contract checks; they contain no question IDs or labels.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

CARD_VERSION = "beyond-askdata-conventions-v1"

_CARD_PATTERNS = [
    {
        "name": "percentage_times_100",
        "phrase": re.compile(r"\b(percent|percentage|proportion|pct)\b", re.I),
        "sql": re.compile(r"\*\s*100(?:\.0)?\b|\b100(?:\.0)?\s*\*", re.I),
        "anti": None,
        "note": "When the requested answer is a percentage, multiply the ratio by 100.",
    },
    {
        "name": "ratio_real_division",
        "phrase": re.compile(r"\b(ratio|rate|per)\b", re.I),
        "sql": re.compile(r"CAST\s*\(|\b(?:REAL|FLOAT|NUMERIC|DECIMAL)\b|\*\s*1\.0", re.I),
        "anti": None,
        "note": "Ratios must be real-valued; cast or multiply by 1.0 before integer division.",
    },
    {
        "name": "distinct_when_unique",
        "phrase": re.compile(r"\b(different|distinct|unique|variety)\b", re.I),
        "sql": re.compile(r"\bCOUNT\s*\(\s*DISTINCT\b|\bSELECT\s+DISTINCT\b", re.I),
        "anti": None,
        "note": "Use DISTINCT only when the question explicitly asks for distinct entities.",
    },
    {
        "name": "top_order_limit",
        "phrase": re.compile(r"\b(top|highest|lowest|maximum|minimum|most|least|best)\b", re.I),
        "sql": re.compile(r"\bORDER\s+BY\b[^;\n]*\bLIMIT\s+\d+\b", re.I),
        "anti": None,
        "note": "Prefer ORDER BY + LIMIT for top/lowest requests unless the question asks for all tied records.",
    },
    {
        "name": "date_storage_format",
        "phrase": re.compile(r"\b(?:19|20)\d{2}[-/]\d{1,2}(?:[-/]\d{1,2})?\b|\b(?:19|20)\d{2}\b", re.I),
        "sql": re.compile(r"\bSTRFTIME\s*\(|\bSUBSTR(?:ING)?\s*\(|\bLIKE\s*'%\d{4}", re.I),
        "anti": None,
        "note": "Date comparisons must match the actual stored string format in the public database.",
    },
    {
        "name": "between_inclusive",
        "phrase": re.compile(r"\bbetween\b", re.I),
        "sql": re.compile(r"\bBETWEEN\b", re.I),
        "anti": None,
        "note": "A natural-language interval normally includes both endpoints.",
    },
]


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").casefold()


def _cv_precision(rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for card in _CARD_PATTERNS:
        matches = [row for row in rows if card["phrase"].search(row.get("question", ""))]
        if len(matches) < 10:
            continue
        per_db: dict[str, list[int]] = {}
        for row in matches:
            hit = 1 if card["sql"].search(row.get("SQL", "")) else 0
            per_db.setdefault(str(row.get("db_id", "")), []).append(hit)
        support = len(matches)
        precision = sum(1 for row in matches if card["sql"].search(row.get("SQL", ""))) / support
        group_scores: list[float] = []
        for db_id in sorted(per_db):
            held = [hit for other_db, hits in per_db.items() if other_db == db_id for hit in hits]
            if len(held) >= 5:
                group_scores.append(sum(held) / len(held))
        if support >= 10 and precision >= 0.70 and group_scores and min(group_scores) >= 0.55:
            stats[card["name"]] = {
                "support": support,
                "precision": round(precision, 4),
                "worst_cv_group_precision": round(min(group_scores), 4),
                "sql_pattern": card["sql"].pattern,
                "note": card["note"],
            }
    return stats


def compile_convention_cards(train_path: str | Path) -> dict[str, Any]:
    """Compile cards from a public train file containing SQL; evaluate cross-database groups."""
    rows = json.loads(Path(train_path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("Public train file must be a JSON array")
    stats = _cv_precision([row for row in rows if isinstance(row, dict) and row.get("SQL")])
    return {
        "version": CARD_VERSION,
        "source": "public train only",
        "cards": stats,
    }


def convention_hints(question: str, cards: dict[str, Any], max_cards: int = 6) -> str:
    """Render only frozen cards whose phrase matches the current public question."""
    selected: list[str] = []
    folded = _fold(question)
    for card in _CARD_PATTERNS:
        name = card["name"]
        if name not in cards:
            continue
        if card["phrase"].search(folded):
            selected.append(f"- {name}: {card['note']}")
        if len(selected) >= max_cards:
            break
    if not selected:
        return ""
    return "SQL QUESTION CONVENTIONS LEARNED FROM PUBLIC TRAIN (freeze before inference):\n" + "\n".join(selected)


def candidate_violations(sql: str, question: str, cards: dict[str, Any]) -> list[str]:
    """Return public-contract violations; no gold value or result label is used."""
    violations: list[str] = []
    folded = _fold(question)
    folded_sql = _fold(sql)
    for card in _CARD_PATTERNS:
        if card["name"] not in cards or not card["phrase"].search(folded):
            continue
        if not card["sql"].search(folded_sql):
            violations.append(card["name"])
    return violations
