"""Public train-example bank for prompt construction. Never read from Dev/Test gold."""
from __future__ import annotations

import json
from pathlib import Path
import re


STOPWORDS = {
    "a", "an", "are", "how", "in", "is", "many", "of", "please", "the", "there",
    "what", "which", "who", "why",
}


def _mask_public_sql(sql: str) -> str:
    masked = re.sub(r"'(?:''|[^'])*'", "'masked_value'", sql)
    return re.sub(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", "0", masked)


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if token not in STOPWORDS}


def load_public_example_bank(path: str | Path, exclude_paths=None) -> dict[str, list[dict[str, str]]]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    excluded_ids = set()
    for exclude_path in exclude_paths or []:
        excluded_rows = json.loads(Path(exclude_path).read_text(encoding="utf-8"))
        if not isinstance(excluded_rows, list):
            raise ValueError("Excluded example file must be a JSON array")
        excluded_ids.update(str(row.get("question_id", index)) for index, row in enumerate(excluded_rows))
    if not isinstance(rows, list):
        raise ValueError("Public train-example file must be a JSON array")
    bank: dict[str, list[dict[str, str]]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"Train example row {index} is not an object")
        question = str(row.get("question") or "")
        sql = str(row.get("SQL") or row.get("sql") or "")
        if not question or not sql:
            continue
        qid = str(row.get("question_id", index))
        if qid in excluded_ids:
            continue
        db_id = str(row.get("db_id") or "")
        bank.setdefault(db_id, []).append({
            "question_id": qid,
            "question": question,
            "evidence": str(row.get("evidence") or ""),
            "SQL": sql,
        })
    for examples in bank.values():
        examples.sort(key=lambda row: (int(row["question_id"]) if row["question_id"].isdigit() else 10**18,
                                       row["question_id"], row["question"]))
    return bank


def examples_for_row(bank: dict[str, list[dict[str, str]]], row: dict, count: int,
                     scope: str = "same_db") -> list[dict[str, str]]:
    if count <= 0:
        return []
    current_qid = str(row.get("question_id", ""))
    if scope == "same_db":
        return [example for example in bank.get(str(row["db_id"]), []) if example["question_id"] != current_qid][:count]
    if scope != "cross_domain_masked":
        raise ValueError(f"Unsupported train example scope: {scope}")
    current_db = str(row["db_id"])
    pool = [
        {**example, "db_id": db_id}
        for db_id, items in bank.items()
        for example in items
        if db_id != current_db and example["question_id"] != current_qid
    ]
    query_tokens = _tokens(f"{row.get('question', '')} {row.get('evidence', '')}")
    pool.sort(key=lambda example: (
        -len(query_tokens & _tokens(f"{example['question']} {example['evidence']}")),
        int(example["question_id"]) if example["question_id"].isdigit() else 10**18,
        example["question_id"], example["SQL"],
    ))
    selected: list[dict[str, str]] = []
    used_db = set()
    for example in pool:
        if example["db_id"] in used_db:
            continue
        selected.append({**example, "SQL": _mask_public_sql(example["SQL"])})
        used_db.add(example["db_id"])
        if len(selected) == count:
            break
    return selected
