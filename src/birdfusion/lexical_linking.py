"""Deterministic lexical and value-anchored schema linking.

No candidate SQL answer is required, so this view does not inherit the self-reinforcing
bias of the candidate-derived linked schema. It uses only question/evidence text, public
column names, primary keys and value-anchor lines produced from SQLite contents.
"""
from __future__ import annotations

import re
from typing import Any

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "from", "with",
    "what", "which", "who", "how", "many", "much", "list", "give", "show", "find",
    "name", "names", "all", "each", "their", "there", "does", "did", "are", "was",
    "were", "is", "be", "been", "that", "this", "these", "those", "state",
}


def _tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9_]+", (text or "").casefold()) if token not in _STOPWORDS]


def _identifier_tokens(text: str) -> set[str]:
    lowered = (text or "").casefold()
    raw = {token for token in re.findall(r"[a-z0-9]+", lowered) if token not in _STOPWORDS}
    stemmed: set[str] = set()
    for token in raw:
        stemmed.add(token)
        if len(token) > 3:
            if token.endswith("ies"):
                stemmed.add(token[:-3] + "y")
            elif token.endswith("ses") or token.endswith("xes") or token.endswith("zes"):
                stemmed.add(token[:-2])
            elif token.endswith("s") and not token.endswith("ss"):
                stemmed.add(token[:-1])
    return stemmed


def _column_identity_tokens(table_name: str, column: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    tokens.update(_identifier_tokens(str(column.get("name") or "")))
    tokens.update(_identifier_tokens(str(table_name)))
    tokens.update(_identifier_tokens(str(column.get("description") or "")))
    tokens.update(_identifier_tokens(str(column.get("value_description") or "")))
    return tokens


def focused_schema(schema: dict[str, Any], question: str, evidence: str,
                   value_hints: str = "", max_columns: int = 24) -> dict[str, Any]:
    """Select public tables/columns from question/evidence text and exact value anchors."""
    terms = _identifier_tokens(f"{question} {evidence or ''}")
    anchor_scores: dict[str, float] = {}
    for match in re.finditer(r"-\s*([A-Za-z_][\w.]*?)[ .]contains", value_hints or ""):
        anchor_scores[match.group(1)] = anchor_scores.get(match.group(1), 0) + 3.0

    # Public evidence often names an exact column, e.g. "A11 pertains to average salary".
    evidence_ref_scores: dict[str, float] = {}
    for match in re.finditer(r"(?i)\b(?:refers to|means|pertains to|contains information about)\s+([A-Za-z_][\w.]*)", evidence or ""):
        identity = match.group(1)
        evidence_ref_scores[identity] = evidence_ref_scores.get(identity, 0.0) + 4.0

    table_index = {str(table["name"]): table for table in schema.get("tables", [])}
    scored: list[tuple[float, str, str, dict[str, Any]]] = []
    for table_name, table in table_index.items():
        for column in table.get("columns", []):
            name = str(column.get("name", ""))
            identity = f"{table_name}.{name}"
            lexical = len(terms & _column_identity_tokens(table_name, column))
            score = float(lexical) + anchor_scores.get(identity, 0.0) + evidence_ref_scores.get(identity, 0.0)
            if bool(column.get("primary_key")):
                score += 0.5
            if bool(column.get("foreign_key")):
                score += 0.5
            if score > 0:
                scored.append((score, identity, name, column))

    scored.sort(key=lambda item: (-item[0], item[1]))
    by_table: dict[str, list[tuple[float, str, str, dict[str, Any]]]] = {}
    table_scores: dict[str, float] = {}
    for score, identity, _, _ in scored:
        table, _ = identity.split(".", 1)
        by_table.setdefault(table, []).append((score, identity, "", {}))
        table_scores[table] = max(table_scores.get(table, 0.0), score)

    max_tables = min(5, len(table_scores))
    top_tables = sorted(table_scores, key=lambda name: (-table_scores[name], name))[:max_tables]
    per_table = max(6, (int(max_columns) + len(top_tables) - 1) // max(1, len(top_tables)))
    selected_columns: dict[str, set[str]] = {}
    for table_name in top_tables:
        for _, identity, _, _ in by_table[table_name][:per_table]:
            selected_columns.setdefault(table_name, set()).add(identity.split(".", 1)[1])

    # A high-scoring column outside a top table still needs its join key.
    scored_by_identity = {identity: (score, column) for score, _, _, column in scored}
    for fk in schema.get("foreign_keys", []):
        source = str(fk.get("from_table") or fk.get("table") or "")
        target = str(fk.get("to_table") or fk.get("references_table") or "")
        source_column = str(fk.get("from_column") or fk.get("column") or "")
        target_column = str(fk.get("to_column") or fk.get("references_column") or "")
        source_identity = f"{source}.{source_column}"
        target_identity = f"{target}.{target_column}"
        if (source_identity in scored_by_identity or target_identity in scored_by_identity) and (source in top_tables or target in top_tables):
            selected_columns.setdefault(source, set()).add(source_column)
            selected_columns.setdefault(target, set()).add(target_column)

    # Preserve every PK/FK needed to connect selected tables.
    adjacency: dict[str, set[str]] = {}
    fk_edges: dict[frozenset[str], list[tuple[str, str, str, str]]] = {}
    for fk in schema.get("foreign_keys", []):
        source = str(fk.get("from_table") or fk.get("table") or "")
        target = str(fk.get("to_table") or fk.get("references_table") or "")
        source_column = str(fk.get("from_column") or fk.get("column") or "")
        target_column = str(fk.get("to_column") or fk.get("references_column") or "")
        if source and target:
            adjacency.setdefault(source, set()).add(target)
            adjacency.setdefault(target, set()).add(source)
            fk_edges.setdefault(frozenset((source, target)), []).append((source, source_column, target, target_column))
        if source in selected_columns and target in selected_columns:
            selected_columns.setdefault(source, set()).add(source_column)
            selected_columns.setdefault(target, set()).add(target_column)

    # Join paths can require an intermediate table. Add shortest FK paths between
    # already-linked tables (up to distance 3) rather than guessing from SQL.
    def shortest_path(start: str, end: str) -> list[str]:
        queue = [(start, [start])]
        seen = {start}
        while queue:
            node, path = queue.pop(0)
            if len(path) > 4:
                continue
            for neighbor in sorted(adjacency.get(node, set())):
                if neighbor == end:
                    return path + [neighbor]
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))
        return []

    linked_tables = sorted(selected_columns)
    for index, left in enumerate(linked_tables):
        for right in linked_tables[index + 1:]:
            path = shortest_path(left, right)
            if not path:
                continue
            for table in path:
                selected_columns.setdefault(table, set())
            for a, b in zip(path, path[1:]):
                for source, source_column, target, target_column in fk_edges.get(frozenset((a, b)), []):
                    selected_columns.setdefault(source, set()).add(source_column)
                    selected_columns.setdefault(target, set()).add(target_column)

    result_tables = []
    for table_name in sorted(selected_columns):
        table = table_index.get(table_name)
        if table is None:
            continue
        wanted = selected_columns[table_name]
        columns = [dict(column) for column in table.get("columns", []) if str(column.get("name")) in wanted]
        if not columns:
            continue
        result_tables.append({**table, "columns": columns})
    if not result_tables:
        return schema
    return {"tables": result_tables, "foreign_keys": schema.get("foreign_keys", [])}
