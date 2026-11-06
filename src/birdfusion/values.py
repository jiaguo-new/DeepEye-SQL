"""Question-to-database value binding from public SQLite contents only."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from .public_input import _quote

STOP_WORDS = set(
    "the a an and or of in on at to for from with by is are was were be been what which who whom how many much "
    "that this these those it its as if then than between over under above below not no all any each their his her "
    "them they we you your our there here does do did list give show find name names highest lowest most least count "
    "number total average sum maximum minimum".split()
)

_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def _normalize_value(value) -> str:
    text = str(value).strip()
    folded = text.casefold()
    date = _DATE_RE.match(folded)
    if date:
        year, month, day = (int(part) for part in date.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"
    if re.fullmatch(r"-?\d+\.0+", folded):
        return folded[:-2]
    if re.fullmatch(r"-?\d+\.\d+0+", folded):
        return folded.rstrip("0").rstrip(".")
    return folded


def build_value_index(path: str | Path, max_values: int = 10000, max_length: int = 255) -> dict[str, list[str]]:
    """Index distinct public values across all SQLite storage types."""
    path = Path(path).resolve()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        con.execute("PRAGMA query_only = ON")
        index: dict[str, list[str]] = {}
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        for table in tables:
            for _, column, *_ in con.execute(f"PRAGMA table_info({_quote(table)})"):
                try:
                    rows = con.execute(
                        f"SELECT DISTINCT {_quote(column)} FROM {_quote(table)} "
                        f"WHERE {_quote(column)} IS NOT NULL ORDER BY 1 LIMIT ?",
                        (int(max_values),),
                    ).fetchall()
                except sqlite3.Error:
                    continue
                values: list[str] = []
                seen: set[str] = set()
                for (value,) in rows:
                    text = str(value).strip()
                    if text and len(text) <= int(max_length) and text not in seen:
                        values.append(text)
                        seen.add(text)
                if values:
                    index[f"{table}.{column}"] = sorted(values, key=lambda v: (v.casefold(), v))
        return index
    finally:
        con.close()


def value_lookup(index: dict[str, list[str]]) -> dict[str, list[str]]:
    """Return a normalized stored-value to public-column lookup."""
    lookup: dict[str, set[str]] = {}
    for column, values in index.items():
        for value in values:
            lookup.setdefault(_normalize_value(value), set()).add(column)
    return {key: sorted(columns) for key, columns in lookup.items()}


def _terms(question: str, evidence: str) -> list[str]:
    text = f"{question or ''} {evidence or ''}"
    terms: list[str] = []
    terms.extend(re.findall(r"'([^']{2,120})'", text))
    terms.extend(re.findall(r'"([^"]{2,120})"', text))
    terms.extend(re.findall(r"\b\d{4}-\d{1,2}-\d{1,2}\b", text))
    terms.extend(re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", text))
    terms.extend(re.findall(r"\b[A-Z][a-z0-9]+(?:[ -][A-Z][a-z0-9]+){1,5}\b", text))
    normalized: list[str] = []
    seen: set[str] = set()
    for term in terms:
        value = _normalize_value(term)
        if len(value) >= 2 and value not in seen:
            seen.add(value)
            normalized.append(value)
    return normalized


def value_hints(question: str, evidence: str, index: dict[str, list[str]], topk: int = 14) -> str:
    """Anchor exact public terms to stored values; never invent substring candidates."""
    terms = _terms(question, evidence)
    lookup = value_lookup(index)
    hits: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for term in terms:
        for column in lookup.get(term, []):
            for value in index[column]:
                if _normalize_value(value) == term and (column, value) not in seen:
                    seen.add((column, value))
                    hits.append((column, value))
        if len(hits) >= topk:
            break

    if not hits:
        return ""
    lines = [f'- {column} contains "{value}"' for column, value in hits]
    return (
        "RELEVANT DATABASE VALUES (matched from public question/evidence to stored values; "
        "use exact spellings when relevant):\n" + "\n".join(lines)
    )


def literal_bindings(sql: str, index: dict[str, list[str]], max_targets: int = 8,
                     lookup: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """Find public columns whose indexed normalized values match SQL string literals."""
    bindings: list[dict[str, Any]] = []
    seen_literals: set[str] = set()
    resolved_lookup = lookup if lookup is not None else value_lookup(index)
    for match in re.finditer(r"'((?:''|[^'])*)'", sql or ""):
        literal = match.group(1).replace("''", "'").strip()
        normalized = _normalize_value(literal)
        if len(normalized) < 2 or normalized in seen_literals:
            continue
        seen_literals.add(normalized)
        targets = resolved_lookup.get(normalized, [])[:int(max_targets)]
        if targets:
            bindings.append({"literal": literal, "columns": list(targets)})
    return bindings

