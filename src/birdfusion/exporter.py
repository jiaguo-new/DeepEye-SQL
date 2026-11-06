"""BIRD prediction export and submission-shape validation."""
from __future__ import annotations

import json
from pathlib import Path

SEPARATOR = "\t----- bird -----\t"


def export_bird(selected_path: str | Path, output_path: str | Path) -> Path:
    rows = json.loads(Path(selected_path).read_text(encoding="utf-8"))
    def qid_order(row):
        qid = str(row["question_id"])
        return (0, int(qid)) if qid.isdigit() else (1, qid)
    rows = sorted(rows, key=qid_order)
    output = {}
    for row in rows:
        qid = str(row["question_id"])
        if qid in output:
            raise ValueError(f"Duplicate question_id: {qid}")
        sql = str(row["sql"]).strip().rstrip(";")
        if not sql:
            raise ValueError(f"Empty SQL for question_id={qid}")
        output[qid] = sql + SEPARATOR + str(row["db_id"])
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return path


def validate_bird_predictions(path: str | Path, expected_count: int | None = None,
                              expected_qids: list[str] | None = None) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("BIRD predictions must be a JSON object keyed by question_id")
    if expected_count is not None and len(payload) != expected_count:
        raise ValueError(f"Expected {expected_count} predictions, found {len(payload)}")
    if expected_qids is not None:
        def qid_order(qid):
            text = str(qid)
            return (0, int(text)) if text.isdigit() else (1, text)
        if list(payload) != sorted((str(qid) for qid in expected_qids), key=qid_order):
            raise ValueError("Prediction question_id order/set differs from canonical public-input order")
    for qid, value in payload.items():
        if not isinstance(value, str) or value.count(SEPARATOR) != 1:
            raise ValueError(f"Invalid BIRD value for question_id={qid}")
        sql, db_id = value.split(SEPARATOR)
        if not sql.strip() or not db_id.strip():
            raise ValueError(f"Empty SQL or db_id for question_id={qid}")
    return {"ok": True, "n": len(payload), "sha256": __import__("hashlib").sha256(Path(path).read_bytes()).hexdigest()}
