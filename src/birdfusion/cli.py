"""Unified CLI for public inference, export, audit and separate gold evaluation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from .audit import audit_public_run
from .exporter import validate_bird_predictions
from .public_input import load_evaluation_questions, load_public_questions, prepare_public_input


def main(argv=None):
    parser = argparse.ArgumentParser(prog="birdfusion", description="BIRD public-only dual-model fusion inference")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="write a physically public-only inference input")
    prepare.add_argument("--input", required=True)
    prepare.add_argument("--output", required=True)

    run = sub.add_parser("run", help="generate, execute, select and export without gold")
    run.add_argument("--input", required=True)
    run.add_argument("--database-root", required=True)
    run.add_argument("--work", required=True)
    run.add_argument("--config", required=True)
    run.add_argument("--credential-config", default=None)
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--qids", default=None, help="comma-separated question_ids for smoke tests")

    train_eval = sub.add_parser("train-evaluate", help="train-only pool oracle and top1 evaluation")
    train_eval.add_argument("--input", required=True)
    train_eval.add_argument("--work", required=True)
    train_eval.add_argument("--database-root", required=True)
    train_eval.add_argument("--output", required=True)

    validate = sub.add_parser("validate", help="validate official prediction shape")
    validate.add_argument("--predictions", required=True)
    validate.add_argument("--input", required=True)
    validate.add_argument("--expected-count", type=int, default=None)

    audit = sub.add_parser("audit", help="audit persisted inference artifacts")
    audit.add_argument("--work", required=True)

    evaluate = sub.add_parser("evaluate", help="separate upstream BIRD gold evaluation")
    evaluate.add_argument("--predictions", required=True)
    evaluate.add_argument("--gold-sql", required=True)
    evaluate.add_argument("--input", required=True, help="BIRD dev.json, used only for difficulty metadata")
    evaluate.add_argument("--database-root", required=True)
    evaluate.add_argument("--evaluator", required=True, help="upstream evaluation_ex.py")
    evaluate.add_argument("--work", required=True)
    evaluate.add_argument("--workers", type=int, default=4)
    evaluate.add_argument("--meta-time-out", type=float, default=30.0)

    args = parser.parse_args(argv)
    if args.command == "prepare":
        rows = prepare_public_input(args.input, args.output)
        print(json.dumps({"n": len(rows), "output": str(args.output),
                          "fields": ["question_id", "db_id", "question", "evidence", "difficulty"]}), flush=True)
    elif args.command == "run":
        # Fail before touching data; no live transport is implemented here.
        # A complete safety integration must replace, not bypass, this gate.
        raise RuntimeError("Live CLI disabled until durable request/budget and immutable database integration is implemented")
    elif args.command == "train-evaluate":
        from .train_evaluation import evaluate_train_pool
        report = evaluate_train_pool(args.input, args.work, args.database_root)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
    elif args.command == "validate":
        rows = load_public_questions(args.input)
        report = validate_bird_predictions(args.predictions, args.expected_count or len(rows),
                                           [str(r["question_id"]) for r in rows])
        print(json.dumps(report), flush=True)
    elif args.command == "audit":
        print(json.dumps(audit_public_run(args.work), ensure_ascii=False), flush=True)
    else:
        work = Path(args.work); work.mkdir(parents=True, exist_ok=True)
        rows = load_evaluation_questions(args.input)
        difficulty = work / "difficulty.jsonl"
        difficulty.write_text("".join(json.dumps({"question": r["question"], "difficulty": r["difficulty"]},
                                                  ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        command = [sys.executable, str(Path(args.evaluator).resolve()),
                   "--predicted_sql_path", str(Path(args.predictions).resolve()),
                   "--ground_truth_path", str(Path(args.gold_sql).resolve()),
                   "--db_root_path", str(Path(args.database_root).resolve()) + "/",
                   "--num_cpus", str(args.workers), "--meta_time_out", str(args.meta_time_out),
                   "--sql_dialect", "SQLite", "--diff_json_path", str(difficulty.resolve()),
                   "--output_log_path", str((work / "evaluation.log").resolve())]
        print(json.dumps({"evaluation_command": command, "note": "gold is first accessed by this separate command"}), flush=True)
        return subprocess.run(command).returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
