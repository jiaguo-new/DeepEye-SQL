"""Derive a compact linked schema from the run's own public SQL candidates."""
from __future__ import annotations

import re
from typing import Any


def linked_schema_from_candidates(schema: dict[str, Any], candidate_sqls: list[str]) -> dict[str, Any] | None:
    """Use only identifiers in public candidates; never inspect an answer or gold label."""
    clean = " ".join(re.sub(r"'(?:''|[^'])*'", "''", sql or "") for sql in candidate_sqls)
    clean = re.sub(r'"(?:[^"]|"")*"', "", clean)
    if not clean.strip():
        return None
    tables = {str(table["name"]): table for table in schema["tables"]}
    folded_tables = {name.casefold(): name for name in tables}
    used_tables: set[str] = set()
    for match in re.finditer(r"\b(?:from|join)\s+([A-Za-z_]\w*)", clean, re.I):
        actual = folded_tables.get(match.group(1).casefold())
        if actual:
            used_tables.add(actual)
    if not used_tables:
        return None

    selected_columns: dict[str, set[str]] = {name: set() for name in used_tables}
    for table_name in used_tables:
        for column in tables[table_name]["columns"]:
            if column.get("primary_key"):
                selected_columns[table_name].add(column["name"])
    # Qualified references, including aliases introduced by FROM/JOIN.
    aliases: dict[str, str] = {}
    for match in re.finditer(r"\b(?:from|join)\s+([A-Za-z_]\w*)(?:\s+(?:as\s+)?([A-Za-z_]\w*))?", clean, re.I):
        table = folded_tables.get(match.group(1).casefold())
        if table:
            aliases[match.group(1).casefold()] = table
            if match.group(2) and match.group(2).upper() not in {"ON", "WHERE", "GROUP", "ORDER", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER"}:
                aliases[match.group(2).casefold()] = table
    for match in re.finditer(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b", clean):
        table = aliases.get(match.group(1).casefold(), folded_tables.get(match.group(1).casefold()))
        if table in used_tables:
            column_names = {str(c["name"]).casefold(): str(c["name"]) for c in tables[table]["columns"]}
            column = column_names.get(match.group(2).casefold())
            if column:
                selected_columns[table].add(column)
    # Unqualified references are included only when the column belongs to a used table.
    words = set(re.findall(r"\b[A-Za-z_]\w*\b", clean))
    for table in used_tables:
        for column in tables[table]["columns"]:
            if str(column["name"]) in words:
                selected_columns[table].add(str(column["name"]))
    # A COUNT(*) candidate has no explicit column: preserve the used table's columns.
    for table in used_tables:
        if not selected_columns[table]:
            selected_columns[table].update(str(c["name"]) for c in tables[table]["columns"])
    result_tables = []
    for table in schema["tables"]:
        name = table["name"]
        if name not in used_tables:
            continue
        columns = [dict(c) for c in table["columns"] if str(c["name"]) in selected_columns[name]]
        result_tables.append({**table, "columns": columns})
    return {"tables": result_tables, "foreign_keys": schema.get("foreign_keys", [])}
