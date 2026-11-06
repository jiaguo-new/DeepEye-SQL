"""Train-only candidate evaluation. Never import this module from inference code."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .public_input import database_path
from .selection import Candidate, select_candidate
from .sql_exec import execute_sql


def _metric(correct: int, total: int) -> dict:
    return {"correct": correct, "total": total, "ex_percent": round(100 * correct / total, 2) if total else None}


def evaluate_train_pool(raw_train_path: str | Path, work: str | Path,
                        database_root: str | Path) -> dict:
    """Use train gold only after a pool is frozen; return aggregates, not qid labels."""
    raw = json.loads(Path(raw_train_path).read_text(encoding="utf-8"))
    work = Path(work)
    selected_rows = json.loads((work / "selected.json").read_text(encoding="utf-8"))
    selected = {str(row["question_id"]): row for row in selected_rows}
    raw_by_qid = {str(row["question_id"]): row for row in raw}
    missing_raw = sorted(set(selected) - set(raw_by_qid))
    if missing_raw:
        raise ValueError(f"Frozen predictions have no train gold row: {missing_raw}")
    raw = [raw_by_qid[qid] for qid in selected]

    oracle = top1 = all_cluster_top1 = 0
    family_correct = Counter()
    template_correct = Counter()
    families = {"qwen", "deepseek"}
    templates = {"standard", "reread"}
    distinct_distribution = Counter()
    n_candidates = n_executable = no_executable_questions = 0

    for row in raw:
        qid = str(row["question_id"])
        candidates = json.loads((work / "executed" / f"{qid}.json").read_text(encoding="utf-8"))["candidates"]
        gold = execute_sql(database_path(database_root, row["db_id"]), row["SQL"],
                           timeout_s=30.0, max_result_rows=1000000)
        if not gold.ok:
            raise RuntimeError(f"Train gold execution failed for qid={qid}: {gold.error_class}")
        keys = {c.get("official_key") for c in candidates if c.get("ok") and c.get("official_key")}
        distinct_distribution[str(len(keys))] += 1
        if not keys:
            no_executable_questions += 1
        n_candidates += len(candidates)
        n_executable += sum(bool(c.get("ok")) for c in candidates)
        correct = [c for c in candidates if c.get("ok") and c.get("official_key") == gold.official_key]
        oracle += bool(correct)
        families.update(c["family"] for c in candidates)
        templates.update(c["template"] for c in candidates)
        for family in {c["family"] for c in correct}:
            family_correct[family] += 1
        for template in {c["template"] for c in correct}:
            template_correct[template] += 1
        chosen = selected.get(qid)
        if chosen is None:
            raise ValueError(f"Missing selected row for qid={qid}")
        top1 += bool(chosen.get("official_key") and chosen.get("official_key") == gold.official_key)
        public_candidates = [Candidate(sql=c["sql"], family=c["family"], template=c["template"],
                                       sample_index=int(c["sample_index"]), ok=bool(c.get("ok")),
                                       official_key=c.get("official_key"), schema_view=c.get("schema_view", "full")) for c in candidates]
        alternative = select_candidate(public_candidates, strategy="all_cluster")
        all_cluster_top1 += bool(alternative.official_key and alternative.official_key == gold.official_key)

    total = len(raw)
    return {
        "boundary": "train-only evaluation; no per-qid labels are written to inference artifacts",
        "gold_used": True,
        "n_questions": total,
        "n_candidates": n_candidates,
        "n_executable_candidates": n_executable,
        "no_executable_questions": no_executable_questions,
        "pool_oracle": _metric(oracle, total),
        "top1": _metric(top1, total),
        "all_cluster_top1": _metric(all_cluster_top1, total),
        "selection_gap_questions": oracle - top1,
        "family_oracle": {name: _metric(family_correct[name], total) for name in sorted(families)},
        "template_oracle": {name: _metric(template_correct[name], total) for name in sorted(templates)},
        "distinct_result_distribution": dict(sorted(distinct_distribution.items(), key=lambda item: int(item[0]))),
    }
