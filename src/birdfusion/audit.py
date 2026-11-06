"""Audit persisted inference artifacts for forbidden gold-derived fields."""
from __future__ import annotations

import json
from pathlib import Path

FORBIDDEN_KEYS = {"SQL", "gold", "gold_sql", "reference_sql", "wrong_qids", "rescue", "damage", "ex", "EX", "per_qid"}


def audit_public_run(work_dir: str | Path) -> dict:
    work = Path(work_dir)
    required = ["protocol.json", "public_inputs.json", "public_schemas.json", "selected.json", "run_summary.json"]
    protocol_path = work / "protocol.json"
    if protocol_path.is_file():
        protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
        if protocol.get("config", {}).get("value_hints"):
            required.append("public_value_hints.json")
        if protocol.get("config", {}).get("evidence_formula"):
            required.append("public_evidence_formulas.json")
        if protocol.get("config", {}).get("train_examples_file"):
            required.append("public_train_examples.json")
        if protocol.get("config", {}).get("focused_schema_followup"):
            required.append("public_focused_schemas.json")
        if protocol.get("config", {}).get("linked_schema_followup"):
            required.append("public_linked_schemas.json")
    violations = []
    for name in required:
        path = work / name
        if not path.is_file():
            violations.append({"file": name, "error": "missing"})
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        def walk(value, location="$"):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in FORBIDDEN_KEYS:
                        violations.append({"file": name, "location": location + "." + key})
                    walk(child, location + "." + key)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, f"{location}[{index}]")
        walk(payload)
    for directory in ("candidates", "executed"):
        for path in (work / directory).glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for candidate in payload.get("candidates", []):
                for key in candidate:
                    if key in FORBIDDEN_KEYS:
                        violations.append({"file": str(path.relative_to(work)), "key": key})
    report = {"ok": not violations, "violations": violations,
              "boundary": "generation/execution/selection artifacts only; gold is read solely by separate evaluation"}
    (work / "public_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if violations:
        raise ValueError(f"Public inference audit failed: {len(violations)} violations")
    return report
