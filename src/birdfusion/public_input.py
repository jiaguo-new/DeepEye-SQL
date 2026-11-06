"""Public-input projection and deterministic prompt construction. No gold access."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from .evidence_formula import compile_evidence

PUBLIC_FIELDS = {"question_id", "db_id", "question", "evidence", "difficulty"}


def _project_questions(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("BIRD input must be a JSON array")
    rows = []
    seen = set()
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Input row {index} is not an object")
        qid = item.get("question_id", index)
        if str(qid) in seen:
            raise ValueError(f"Duplicate question_id: {qid}")
        if not item.get("question") or not item.get("db_id"):
            raise ValueError(f"Input row {index} lacks question or db_id")
        seen.add(str(qid))
        rows.append({
            "question_id": qid,
            "db_id": str(item["db_id"]),
            "question": str(item["question"]),
            "evidence": str(item.get("evidence") or ""),
            "difficulty": str(item.get("difficulty") or "simple"),
        })
    return rows


def load_public_questions(path: str | Path) -> list[dict[str, Any]]:
    """Load a physically public-only file; reject gold and every nonpublic field."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("BIRD input must be a JSON array")
    for index, item in enumerate(payload):
        if isinstance(item, dict):
            extra = set(item) - PUBLIC_FIELDS
            if extra:
                raise ValueError(f"Non-public fields in inference input row {index}: {sorted(extra)}")
    return _project_questions(payload)


def prepare_public_input(source: str | Path, destination: str | Path) -> list[dict[str, Any]]:
    """One-way preprocessing boundary; the output is the only valid inference input."""
    rows = _project_questions(json.loads(Path(source).read_text(encoding="utf-8")))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".tmp")
    temp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(destination)
    return rows


def load_evaluation_questions(path: str | Path) -> list[dict[str, Any]]:
    """Evaluation-only reader; never call this from generation/execution/selection."""
    return _project_questions(json.loads(Path(path).read_text(encoding="utf-8")))


def database_path(root: str | Path, db_id: str) -> Path:
    if Path(db_id).name != db_id or db_id in {"", ".", ".."}:
        raise ValueError(f"Unsafe db_id: {db_id!r}")
    path = Path(root) / db_id / f"{db_id}.sqlite"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def schema_from_sqlite(path: str | Path, sample_values: int = 3,
                       description_root: str | Path | None = None) -> dict[str, Any]:
    """Read physical schema, foreign keys and public stored values only."""
    path = Path(path).resolve()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        con.execute("PRAGMA query_only = ON")
        tables = []
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        foreign_keys = []
        for table in names:
            columns = []
            for row in con.execute(f"PRAGMA table_info({_quote(table)})"):
                _, name, data_type, not_null, _, primary_key = row
                samples = []
                if sample_values:
                    try:
                        samples = [r[0] for r in con.execute(
                            f"SELECT DISTINCT {_quote(name)} FROM {_quote(table)} "
                            f"WHERE {_quote(name)} IS NOT NULL LIMIT ?", (sample_values,)
                        )]
                    except sqlite3.Error:
                        samples = []
                columns.append({
                    "name": str(name), "type": str(data_type or ""),
                    "primary_key": bool(primary_key), "primary_key_ordinal": primary_key,
                    "not_null": bool(not_null), "samples": samples,
                })
            for fk in con.execute(f"PRAGMA foreign_key_list({_quote(table)})"):
                foreign_keys.append({
                    "from_table": table, "from_column": fk[3],
                    "to_table": fk[2], "to_column": fk[4],
                })
            tables.append({"name": table, "columns": columns})
        schema = {"tables": tables, "foreign_keys": foreign_keys}
    finally:
        con.close()
    if description_root is not None:
        _attach_public_descriptions(schema, Path(description_root) / path.stem / "database_description")
    return schema


def _attach_public_descriptions(schema: dict[str, Any], description_dir: Path) -> None:
    """Attach only BIRD's public table CSV semantics to existing physical columns."""
    if not description_dir.is_dir():
        return
    files = {p.stem.casefold(): p for p in sorted(description_dir.glob("*.csv"))}
    for table in schema["tables"]:
        csv_path = files.get(str(table["name"]).casefold())
        if csv_path is None:
            continue
        columns = {str(c["name"]).casefold(): c for c in table["columns"]}
        with csv_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle):
                original = (row.get("original_column_name") or "").strip()
                display = (row.get("column_name") or "").strip()
                column = columns.get(original.casefold()) or columns.get(display.casefold())
                if column is None:
                    continue
                description = (row.get("column_description") or "").strip()
                value_description = (row.get("value_description") or "").strip()
                if description:
                    column["description"] = description[:1000]
                if value_description:
                    column["value_description"] = value_description[:4000]


def render_schema_ddl(schema: dict[str, Any]) -> str:
    """Deterministic public DDL view; preserves columns, constraints, FKs and samples."""
    chunks = []
    for table in schema.get("tables", []):
        columns = []
        table_columns = table.get("columns", [])
        primary_key_columns = [column for column in table_columns if column.get("primary_key")]
        for column in table_columns:
            line = f"  {_quote(str(column['name']))} {column.get('type') or 'TEXT'}"
            if column.get("not_null"):
                line += " NOT NULL"
            if column.get("primary_key") and len(primary_key_columns) == 1:
                line += " PRIMARY KEY"
            columns.append(line)
        if len(primary_key_columns) > 1:
            # Legacy schemas lack ordinals; retain their column order as the best available fallback.
            if all(column.get("primary_key_ordinal") for column in primary_key_columns):
                primary_key_columns.sort(key=lambda column: column["primary_key_ordinal"])
            names = ", ".join(_quote(str(column["name"])) for column in primary_key_columns)
            columns.append(f"  PRIMARY KEY ({names})")
        chunks.append(f"CREATE TABLE {_quote(str(table['name']))} (\n" + ",\n".join(columns) + "\n);")
        for column in table.get("columns", []):
            if column.get("samples"):
                chunks.append(
                    f"-- {_quote(str(table['name']))}.{_quote(str(column['name']))} samples: "
                    + json.dumps(column["samples"], ensure_ascii=False, default=str)
                )
    for fk in schema.get("foreign_keys", []):
        target = _quote(str(fk["to_table"]))
        if fk.get("to_column") is None:
            target += " (implicit primary key)"
        else:
            target += "." + _quote(str(fk["to_column"]))
        chunks.append(
            f"-- FK {_quote(str(fk['from_table']))}.{_quote(str(fk['from_column']))} -> "
            f"{target}"
        )
    return "\n".join(chunks)


DIRECTIVES = {
    "standard": (
        "Generate exactly one SQLite SQL query answering the question with the given schema, "
        "stored values, and evidence. Use public train examples only as SQL pattern references. "
        "Output only the SQL."
    ),
    "reread": (
        "Before writing SQL, re-read the requested result unit, entity key, aggregation, range, "
        "NULL handling, ordering and limit. Then generate exactly one SQLite SQL query consistent "
        "with that reading. Output one short reasoning line, then the SQL on the final line."
    ),
    "fkjoin": (
        "Generate exactly one SQLite SQL query answering the question. When joins are needed, "
        "use the declared foreign-key relationships and choose the shortest valid join path; "
        "do not invent table relationships. Preserve the requested grain, aggregation, NULL "
        "handling, ordering and limit. Output only the SQL."
    ),
    "decompose": (
        "Decompose the requested result into its required entities, filters, aggregations and "
        "final output grain. Express those steps as nested CTEs or subqueries so each step is "
        "independently correct, then generate exactly one SQLite SQL query. Output only the SQL."
    ),
}

EVIDENCE_FORMULA_ADDENDUM = (
    " PUBLIC EVIDENCE CALCULATION is supplied for this question: implement its arithmetic, "
    "CASE WHEN conditions, denominator and percentage scaling exactly."
)


def render_prompt(row: dict[str, Any], schema: dict[str, Any], template: str,
                  train_examples: list[dict[str, str]] | None = None, value_hints: str = "",
                  schema_format: str = "json", evidence_formula: bool = False) -> str:
    if template not in DIRECTIVES:
        raise ValueError(f"Unknown public prompt template: {template}")
    if schema_format not in {"json", "ddl"}:
        raise ValueError(f"Unknown public schema format: {schema_format}")
    example_text = ""
    if train_examples:
        example_text = "\n\nPublic train examples:\n" + "\n\n".join(
            f"Question: {ex['question']}\nEvidence: {ex.get('evidence', '')}\nSQL: {ex['SQL']}"
            for ex in train_examples
        )
    public = {
        "question": row["question"], "evidence": row["evidence"],
    }
    if value_hints:
        public["relevant_database_values"] = value_hints
    formula_text = ""
    if evidence_formula:
        formula_text = compile_evidence(row["question"], row["evidence"]).get("compiled_formulas", "")
        if formula_text:
            public["compiled_evidence_calculation"] = formula_text.split("\n", 1)[-1]
    public_text = json.dumps(public, ensure_ascii=False, sort_keys=True, default=str)
    if formula_text:
        public_text += "\n\n" + formula_text
    if schema_format == "ddl":
        public_text += "\n\nSchema DDL and public stored-value samples:\n" + render_schema_ddl(schema)
    else:
        public["schema"] = schema
        public_text = json.dumps(public, ensure_ascii=False, sort_keys=True, default=str)
    directive = DIRECTIVES[template]
    if formula_text:
        directive += EVIDENCE_FORMULA_ADDENDUM
    return directive + example_text + "\n\n" + public_text


def extract_sql(content: str) -> str | None:
    import re
    text = (content or "").strip()
    fenced = re.findall(r"```(?:sql)?\s*(.+?)```", text, flags=re.S | re.I)
    if fenced:
        text = fenced[-1].strip()
    tagged = re.findall(r"<sql>(.*?)</sql>", text, flags=re.S | re.I)
    if tagged:
        text = tagged[-1].strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if re.match(r"^(SELECT|WITH)\b", line, flags=re.I):
            candidate = "\n".join(lines[index:]).strip().rstrip(";").strip()
            return candidate or None
    match = re.search(r"\b(SELECT|WITH)\b", text, flags=re.I)
    if match:
        return text[match.start():].strip().rstrip(";").strip() or None
    return None
