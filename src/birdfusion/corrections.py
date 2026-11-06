"""Deterministic public-input repair prompts for generated SQL candidates."""
from __future__ import annotations

import json
from typing import Any


def _context(row: dict[str, Any], schema: dict[str, Any], train_examples: list[dict[str, str]] | None,
             value_hints: str) -> str:
    parts = [json.dumps({
        "question": row["question"],
        "evidence": row["evidence"],
        "schema": schema,
    }, ensure_ascii=False, sort_keys=True, default=str)]
    if value_hints:
        parts.append("### Relevant public database values\n" + value_hints)
    if train_examples:
        parts.append("### Public train examples (patterns only)\n" + "\n\n".join(
            f"Question: {ex['question']}\nEvidence: {ex.get('evidence', '')}\nSQL: {ex['SQL']}"
            for ex in train_examples
        ))
    return "\n\n".join(parts)


def execution_correction_prompt(row: dict[str, Any], schema: dict[str, Any], sql: str,
                                error_message: str, *, train_examples=None,
                                value_hints: str = "") -> str:
    return (
        "You are a SQLite expert. The public SQLite database reports that the query below cannot "
        "be executed. Repair it using only the public schema, question, evidence and stored values. "
        "Do not use labels or gold answers. Output only SQL.\n\n"
        + _context(row, schema, train_examples, value_hints)
        + "\n\n### Previous SQL\n" + sql
        + "\n\n### Public execution error\n" + error_message
        + "\n\n### Revised SQL\n"
    )


def literal_correction_prompt(row: dict[str, Any], schema: dict[str, Any], sql: str,
                              bindings: list[dict[str, Any]], *, train_examples=None,
                              value_hints: str = "") -> str:
    lines = []
    for binding in bindings:
        targets = ", ".join(binding["columns"])
        lines.append(f"- {binding['literal']!r} is stored in: {targets}")
    return (
        "You are a SQLite expert. The literal values below were checked against the public database, "
        "but the query does not appear to reference a column that stores them. If the comparison is "
        "meant to select those literal values, revise the SQL to reference one of the listed target "
        "columns. Do not use labels or gold answers. Output only SQL.\n\n"
        + "\n".join(lines)
        + "\n\n"
        + _context(row, schema, train_examples, value_hints)
        + "\n\n### Previous SQL\n" + sql
        + "\n\n### Revised SQL\n"
    )
