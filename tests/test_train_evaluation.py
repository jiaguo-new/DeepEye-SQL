import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from birdfusion.sql_exec import execute_sql
from birdfusion.train_evaluation import evaluate_train_pool


class TrainEvaluationTests(unittest.TestCase):
    def test_oracle_top1_and_family_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "db" / "school"
            db_dir.mkdir(parents=True)
            db_path = db_dir / "school.sqlite"
            con = sqlite3.connect(db_path)
            con.executescript("CREATE TABLE students(id INTEGER, name TEXT); INSERT INTO students VALUES (1,'A'),(2,'B');")
            con.commit(); con.close()
            train = [
                {"question_id": 0, "db_id": "school", "question": "Count", "evidence": "", "SQL": "SELECT COUNT(*) FROM students"},
                {"question_id": 1, "db_id": "school", "question": "Names", "evidence": "", "SQL": "SELECT name FROM students ORDER BY id"},
            ]
            source = root / "train.json"
            source.write_text(json.dumps(train), encoding="utf-8")
            work = root / "run"
            (work / "executed").mkdir(parents=True)
            sqls = {
                0: [("qwen", "SELECT COUNT(*) FROM students WHERE id=1"),
                    ("deepseek", "SELECT COUNT(*) FROM students")],
                1: [("qwen", "SELECT name FROM students ORDER BY id"),
                    ("deepseek", "SELECT name FROM students ORDER BY id")],
            }
            for qid, rows in sqls.items():
                candidates = []
                for index, (family, sql) in enumerate(rows):
                    result = execute_sql(db_path, sql)
                    candidates.append({"family": family, "template": "standard", "sample_index": index,
                                       "sql": sql, "ok": result.ok, "official_key": result.official_key})
                (work / "executed" / f"{qid}.json").write_text(json.dumps({"candidates": candidates}), encoding="utf-8")
            selected = []
            for qid in (0, 1):
                candidate = sqls[qid][0]
                result = execute_sql(db_path, candidate[1])
                selected.append({"question_id": qid, "sql": candidate[1], "official_key": result.official_key})
            (work / "selected.json").write_text(json.dumps(selected), encoding="utf-8")

            report = evaluate_train_pool(source, work, root / "db")
            self.assertEqual(report["n_questions"], 2)
            self.assertEqual(report["pool_oracle"]["correct"], 2)
            self.assertEqual(report["top1"]["correct"], 1)
            self.assertEqual(report["all_cluster_top1"]["correct"], 2)
            self.assertEqual(report["family_oracle"]["qwen"]["correct"], 1)
            self.assertEqual(report["family_oracle"]["deepseek"]["correct"], 2)
            self.assertEqual(report["distinct_result_distribution"]["1"], 1)
            self.assertEqual(report["distinct_result_distribution"]["2"], 1)

            from contextlib import redirect_stdout
            from io import StringIO
            from birdfusion.cli import main
            output = root / "metrics.json"
            with redirect_stdout(StringIO()):
                rc = main(["train-evaluate", "--input", str(source), "--work", str(work),
                           "--database-root", str(root / "db"), "--output", str(output)])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(output.read_text())["pool_oracle"]["correct"], 2)

    def test_evaluation_follows_frozen_prediction_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "db" / "school"; db_dir.mkdir(parents=True)
            con = sqlite3.connect(db_dir / "school.sqlite")
            con.executescript("CREATE TABLE students(id INTEGER); INSERT INTO students VALUES (1),(2);")
            con.commit(); con.close()
            train = [
                {"question_id": 0, "db_id": "school", "question": "Count", "evidence": "", "SQL": "SELECT COUNT(*) FROM students"},
                {"question_id": 1, "db_id": "school", "question": "Extra", "evidence": "", "SQL": "SELECT COUNT(*) FROM students"},
            ]
            source = root / "train.json"; source.write_text(json.dumps(train), encoding="utf-8")
            work = root / "run"; (work / "executed").mkdir(parents=True)
            result = execute_sql(db_dir / "school.sqlite", "SELECT COUNT(*) FROM students")
            (work / "executed" / "0.json").write_text(json.dumps({"candidates":[{
                "family":"qwen","template":"standard","sample_index":0,"sql":"SELECT COUNT(*) FROM students",
                "ok":result.ok,"official_key":result.official_key}]}), encoding="utf-8")
            selected = [{"question_id":0,"sql":"SELECT COUNT(*) FROM students","official_key":result.official_key}]
            (work / "selected.json").write_text(json.dumps(selected), encoding="utf-8")
            report = evaluate_train_pool(source, work, root / "db")
            self.assertEqual(report["n_questions"], 1)
            self.assertEqual(report["top1"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
