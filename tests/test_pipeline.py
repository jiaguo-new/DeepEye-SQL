import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from birdfusion.public_input import load_public_questions, prepare_public_input, render_prompt, render_schema_ddl, schema_from_sqlite
from birdfusion.selection import Candidate, select_candidate
from birdfusion.exporter import export_bird, validate_bird_predictions
from birdfusion.sql_exec import execute_sql, result_key
from birdfusion.train_examples import examples_for_row, load_public_example_bank
from birdfusion.pipeline import run_pipeline
from birdfusion.values import build_value_index, literal_bindings, value_hints
from birdfusion.evidence_formula import compile_evidence
from birdfusion.audit import audit_public_run


class LivePipelineSafetyTests(unittest.TestCase):
    def test_default_live_client_is_disabled_before_database_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "public.json"
            source.write_text(json.dumps([{"question_id": 1, "db_id": "missing", "question": "Count rows"}]))
            with self.assertRaisesRegex(RuntimeError, "Live pipeline disabled"):
                run_pipeline(source, root / "nonexistent-db", root / "run", {})


class ValueGroundingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "school.sqlite"
        con = sqlite3.connect(self.db)
        con.executescript("""
        CREATE TABLE students(id INTEGER, currency TEXT, joined TEXT);
        INSERT INTO students VALUES (118,'EUR','2019-08-20');
        INSERT INTO students VALUES (2137,'CZK','2019-08-21');
        """)
        con.commit(); con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_value_index_includes_numeric_and_long_values(self):
        index = build_value_index(self.db, max_values=10000, max_length=255)
        self.assertIn("students.currency", index)
        self.assertIn("students.id", index)

    def test_value_hints_exact_only_avoids_numeric_substring_noise(self):
        index = build_value_index(self.db, max_values=10000, max_length=255)
        hints = value_hints("client id 3356", "", index, topk=10)
        self.assertNotIn("account_id contains \"3\"", hints)
        self.assertNotIn("contains \"33\"", hints)

    def test_value_hints_normalize_dates_and_match_exact_values(self):
        index = build_value_index(self.db, max_values=10000, max_length=255)
        hints = value_hints("Which joined on 2019-8-20?", "", index, topk=4)
        self.assertIn("students.joined", hints)
        self.assertIn("2019-08-20", hints)

    def test_literal_bindings_use_normalized_exact_lookup(self):
        index = build_value_index(self.db, max_values=10000, max_length=255)
        bindings = literal_bindings("SELECT * FROM students WHERE joined = '2019-8-20'", index)
        self.assertEqual(bindings[0]["columns"], ["students.joined"])


class FakeClient:
    def __init__(self):
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        prompt = request["messages"][0]["content"]
        self.assert_public(prompt)
        if request["model"].startswith("deepseek"):
            sql = "SELECT COUNT(*) FROM students"
        else:
            sql = "SELECT COUNT(*) FROM students"
        return {"choices": [{"message": {"content": sql}}], "model": request["model"], "usage": {"total_tokens": 3}}

    @staticmethod
    def assert_public(prompt):
        assert "GOLD_SENTINEL" not in prompt
        assert "wrong_qids" not in prompt
        assert "rescue" not in prompt


class PublicInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db_dir = self.root / "db" / "school"
        self.db_dir.mkdir(parents=True)
        self.db_path = self.db_dir / "school.sqlite"
        con = sqlite3.connect(self.db_path)
        con.executescript("""
            CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, score INTEGER);
            INSERT INTO students VALUES (1, 'A', 90), (2, 'B', 80);
        """)
        con.commit()
        con.close()
        self.input_path = self.root / "dev.json"
        self.input_path.write_text(json.dumps([{
            "question_id": 7,
            "db_id": "school",
            "question": "How many students are there?",
            "evidence": "students is the required table",
            "difficulty": "simple",
            "SQL": "SELECT 'GOLD_SENTINEL'",
            "wrong_qids": [7],
            "rescue": True,
        }]), encoding="utf-8")

    def test_public_projection_and_prompt_exclude_gold_derived_fields(self):
        with self.assertRaises(ValueError):
            load_public_questions(self.input_path)
        public_path = self.root / "public.json"
        prepare_public_input(self.input_path, public_path)
        rows = load_public_questions(public_path)
        self.assertEqual(set(rows[0]), {"question_id", "db_id", "question", "evidence", "difficulty"})
        schema = schema_from_sqlite(self.db_path)
        prompt = render_prompt(rows[0], schema, "standard")
        self.assertNotIn("GOLD_SENTINEL", prompt)
        self.assertNotIn("wrong_qids", prompt)
        self.assertNotIn("rescue", prompt)
        self.assertIn("students", prompt)

    def test_evidence_formula_compiles_divide_and_percentage(self):
        text = compile_evidence(
            "What percentage?",
            "percentage = divide(count(course_id) when level = 'A', count(course_id)) * 100%"
        )["compiled_formulas"]
        self.assertIn("CAST(COUNT(CASE WHEN level='A' THEN course_id END) AS REAL)", text)
        self.assertIn("/ NULLIF(COUNT(course_id), 0)", text)
        self.assertIn("* (100)", text)

    def test_evidence_formula_compiles_refers_to_top_count(self):
        text = compile_evidence(
            "State the name of the city with the most venues.",
            "the most venues refers to max(count(Venue_Id))",
        )["compiled_formulas"]
        self.assertIn("ORDER BY COUNT(Venue_Id) DESC LIMIT 1", text)

    def test_evidence_formula_normalizes_average_denominator(self):
        text = compile_evidence(
            "What is the average number of courses taught by each professor?",
            "average number = divide(count(taughtBy.course_id), count(taughtBy.p_id))",
        )["compiled_formulas"]
        self.assertIn("/ NULLIF(COUNT(DISTINCT taughtBy.p_id), 0)", text)

    def test_evidence_formula_compiles_conditional_count_then(self):
        text = compile_evidence(
            "What percentage of trains have at least 4 cars?",
            "calculation = MULTIPLY(DIVIDE(count(trailPosi > = 4 then id), count(id)), 100)",
        )["compiled_formulas"]
        self.assertIn("CASE WHEN trailPosi>=4 THEN id END", text)

    def test_evidence_formula_rejects_aggregate_in_when_and_nested_avg_count(self):
        self.assertEqual(compile_evidence("average?", "average = avg(count(p_id))").get("compiled_formulas", ""), "")
        self.assertEqual(
            compile_evidence(
                "calculation?",
                "calculation = count(direction = 'east' where MAX(load_shape) then id) / count(id) * 100",
            ).get("compiled_formulas", ""),
            "",
        )

    def test_prompt_injects_compiled_evidence_formula(self):
        row = {
            "question_id": 1, "db_id": "students",
            "question": "What is the ratio of A to B?",
            "evidence": "ratio = count(A where x = 1) / count(A where x = 0)",
            "difficulty": "simple",
        }
        prompt = render_prompt(row, {"tables": [], "foreign_keys": []}, "standard", evidence_formula=True)
        self.assertIn("PUBLIC EVIDENCE CALCULATION", prompt)
        self.assertIn("NULLIF", prompt)

    def test_prompt_does_not_mention_formula_when_compilation_is_empty(self):
        row = {
            "question_id": 2, "db_id": "students",
            "question": "List students.", "evidence": "student refers to student_id",
            "difficulty": "simple",
        }
        prompt = render_prompt(row, {"tables": [], "foreign_keys": []}, "standard", evidence_formula=True)
        self.assertNotIn("PUBLIC EVIDENCE CALCULATION", prompt)

    def test_fkjoin_template_requires_declared_shortest_paths(self):
        prompt = render_prompt(
            {"question": "List schools", "evidence": ""},
            {"tables": [], "foreign_keys": []}, "fkjoin",
        )
        self.assertIn("declared foreign-key relationships", prompt)
        self.assertIn("shortest valid join path", prompt)
        self.assertIn("do not invent table relationships", prompt)

    def test_decompose_template_requires_stepwise_cte_or_subquery(self):
        prompt = render_prompt(
            {"question": "List schools", "evidence": ""},
            {"tables": [], "foreign_keys": []}, "decompose",
        )
        self.assertIn("Decompose the requested result", prompt)
        self.assertIn("nested CTEs or subqueries", prompt)
        self.assertIn("Output only the SQL", prompt)

    def test_ddl_prompt_preserves_public_schema_and_samples(self):
        public_path = self.root / "public_ddl.json"
        prepare_public_input(self.input_path, public_path)
        rows = load_public_questions(public_path)
        schema = schema_from_sqlite(self.db_path)
        prompt = render_prompt(rows[0], schema, "standard", schema_format="ddl")
        self.assertIn('CREATE TABLE "students"', prompt)
        self.assertIn('"id" INTEGER PRIMARY KEY', prompt)
        self.assertIn("samples:", prompt)
        self.assertIn("Schema DDL and public stored-value samples:", prompt)
        self.assertNotIn("GOLD_SENTINEL", prompt)

    def test_schema_ddl_composite_primary_key_preserves_sqlite_ordinal(self):
        with sqlite3.connect(self.db_path) as con:
            con.execute('CREATE TABLE pairs (first TEXT, second INTEGER, payload TEXT, PRIMARY KEY (second, first))')
        schema = schema_from_sqlite(self.db_path, sample_values=0)
        pairs = next(table for table in schema["tables"] if table["name"] == "pairs")
        self.assertEqual(
            [(column["name"], column["primary_key"], column["primary_key_ordinal"])
             for column in pairs["columns"]],
            [("first", True, 2), ("second", True, 1), ("payload", False, 0)],
        )
        ddl = render_schema_ddl(schema)
        self.assertIn('CREATE TABLE "pairs" (\n  "first" TEXT,\n  "second" INTEGER,\n'
                      '  "payload" TEXT,\n  PRIMARY KEY ("second", "first")\n);', ddl)
        with sqlite3.connect(":memory:") as rebuilt:
            rebuilt.executescript(ddl)
            self.assertEqual(
                [(row[1], row[5]) for row in rebuilt.execute('PRAGMA table_info("pairs")')],
                [("first", 2), ("second", 1), ("payload", 0)],
            )
            rebuilt.execute('INSERT INTO pairs VALUES (\'a\', 1, \'x\')')
            with self.assertRaises(sqlite3.IntegrityError):
                rebuilt.execute('INSERT INTO pairs VALUES (\'a\', 1, \'y\')')
            rebuilt.execute('INSERT INTO pairs VALUES (\'b\', 1, \'z\')')

    def test_schema_ddl_single_primary_key_remains_inline(self):
        schema = schema_from_sqlite(self.db_path, sample_values=0)
        columns = schema["tables"][0]["columns"]
        self.assertEqual([column["primary_key_ordinal"] for column in columns], [1, 0, 0])
        ddl = render_schema_ddl(schema)
        self.assertEqual(ddl, 'CREATE TABLE "students" (\n  "id" INTEGER PRIMARY KEY,\n'
                         '  "name" TEXT,\n  "score" INTEGER\n);')
        with sqlite3.connect(":memory:") as rebuilt:
            rebuilt.executescript(ddl)
            self.assertEqual([row[5] for row in rebuilt.execute('PRAGMA table_info("students")')], [1, 0, 0])

    def test_schema_ddl_legacy_primary_keys_use_column_order(self):
        schema = {"tables": [{"name": "legacy", "columns": [
            {"name": "a", "type": "TEXT", "primary_key": True},
            {"name": "b", "type": "INTEGER", "primary_key": True},
            {"name": "c", "type": "TEXT", "primary_key": False},
        ]}], "foreign_keys": []}
        ddl = render_schema_ddl(schema)
        self.assertEqual(ddl, 'CREATE TABLE "legacy" (\n  "a" TEXT,\n  "b" INTEGER,\n'
                         '  "c" TEXT,\n  PRIMARY KEY ("a", "b")\n);')
        with sqlite3.connect(":memory:") as rebuilt:
            rebuilt.executescript(ddl)
            self.assertEqual([row[5] for row in rebuilt.execute('PRAGMA table_info("legacy")')], [1, 2, 0])
        schema["tables"][0]["columns"][1]["primary_key"] = False
        self.assertEqual(render_schema_ddl(schema),
                         'CREATE TABLE "legacy" (\n  "a" TEXT PRIMARY KEY,\n'
                         '  "b" INTEGER,\n  "c" TEXT\n);')

    def test_schema_ddl_implicit_foreign_key_has_no_fabricated_column(self):
        with sqlite3.connect(self.db_path) as con:
            con.executescript('CREATE TABLE child (parent_id INTEGER REFERENCES students);'
                              'CREATE TABLE explicit_child (parent_id INTEGER REFERENCES students(id));')
        schema = schema_from_sqlite(self.db_path, sample_values=0)
        foreign_keys = {fk["from_table"]: fk for fk in schema["foreign_keys"]}
        self.assertIsNone(foreign_keys["child"]["to_column"])
        self.assertEqual(foreign_keys["explicit_child"]["to_column"], "id")
        ddl = render_schema_ddl(schema)
        self.assertIn('-- FK "child"."parent_id" -> "students" (implicit primary key)', ddl)
        self.assertIn('-- FK "explicit_child"."parent_id" -> "students"."id"', ddl)
        self.assertNotIn('"None"', ddl)
        with sqlite3.connect(":memory:") as rebuilt:
            rebuilt.executescript(ddl)
            self.assertEqual([row[1] for row in rebuilt.execute('PRAGMA table_info("child")')], ["parent_id"])

    def test_public_database_descriptions_augment_matching_columns(self):
        description_dir = self.db_dir / "database_description"
        description_dir.mkdir()
        (description_dir / "students.csv").write_text(
            "\ufefforiginal_column_name,column_name,column_description,data_format,value_description\n"
            "score,Score,Final exam score,integer,90 means excellent\n",
            encoding="utf-8",
        )
        plain = schema_from_sqlite(self.db_path)
        described = schema_from_sqlite(self.db_path, description_root=self.db_dir.parent)
        plain_score = plain["tables"][0]["columns"][2]
        score = described["tables"][0]["columns"][2]
        self.assertNotIn("description", plain_score)
        self.assertEqual(score["description"], "Final exam score")
        self.assertEqual(score["value_description"], "90 means excellent")

    def test_execute_sql_read_only(self):
        result = execute_sql(self.db_path, "SELECT COUNT(*) FROM students")
        self.assertTrue(result.ok)
        self.assertEqual(result.n_rows, 1)
        self.assertTrue(result.official_key)
        failed = execute_sql(self.db_path, "DELETE FROM students")
        self.assertFalse(failed.ok)

    def test_result_key_matches_bird_set_equality_for_numeric_types(self):
        self.assertEqual(result_key([(1, 0.5), (2.0, 1)]), result_key([(1.0, 0.5), (2, 1.0)]))
        self.assertNotEqual(result_key([(1,)]), result_key([("1",)]))
        self.assertEqual(result_key([(1, "x"), (1, "x")]), result_key([(1.0, "x")]))

    def test_execution_timeout(self):
        recursive = "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT SUM(n) FROM x"
        result = execute_sql(self.db_path, recursive, timeout_s=0.05)
        self.assertFalse(result.ok)
        self.assertTrue(result.timeout)

    def test_negative_derived_label_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for name in ("protocol.json", "public_inputs.json", "public_schemas.json", "run_summary.json"):
                (work / name).write_text("{}", encoding="utf-8")
            (work / "selected.json").write_text('[{"question_id": 0, "rescue": true}]', encoding="utf-8")
            with self.assertRaises(ValueError):
                audit_public_run(work)
    def test_public_train_example_artifact_is_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "protocol.json").write_text('{"config":{"train_examples_file":"/public/fit_train.json"}}', encoding="utf-8")
            for name in ("public_inputs.json", "public_schemas.json", "run_summary.json", "selected.json"):
                (work / name).write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                audit_public_run(work)
            (work / "public_train_examples.json").write_text('{"0":{"SQL":"sentinel"}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                audit_public_run(work)
            (work / "public_train_examples.json").write_text('{"0":[{"public_train_sql":"SELECT 1"}]}', encoding="utf-8")
            self.assertTrue(audit_public_run(work)["ok"])
    def test_linked_schema_artifact_is_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "protocol.json").write_text('{"config":{"linked_schema_followup":true}}', encoding="utf-8")
            for name in ("public_inputs.json", "public_schemas.json", "run_summary.json"):
                (work / name).write_text("{}", encoding="utf-8")
            (work / "selected.json").write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                audit_public_run(work)
            (work / "public_linked_schemas.json").write_text("{}", encoding="utf-8")
            self.assertTrue(audit_public_run(work)["ok"])

    def test_value_hint_artifact_is_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "protocol.json").write_text('{"config":{"value_hints":true}}', encoding="utf-8")
            for name in ("public_inputs.json", "public_schemas.json", "run_summary.json"):
                (work / name).write_text("{}", encoding="utf-8")
            (work / "selected.json").write_text("[]", encoding="utf-8")
            (work / "public_value_hints.json").write_text('{"0":{"gold":"sentinel"}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                audit_public_run(work)


class SelectionTests(unittest.TestCase):
    def candidate(self, sql, family, template, index, key, ok=True):
        return Candidate(sql=sql, family=family, template=template, sample_index=index,
                         ok=ok, official_key=key)

    def test_selector_uses_only_candidate_features(self):
        candidates = [
            self.candidate("SELECT 1", "qwen", "standard", 0, "a"),
            self.candidate("SELECT 1", "qwen", "standard", 1, "a"),
            self.candidate("SELECT 2", "qwen", "reread", 0, "b"),
            self.candidate("SELECT 2", "deepseek", "reread", 0, "b"),
            self.candidate("SELECT 2", "deepseek", "reread", 1, "b"),
        ]
        decision = select_candidate(candidates)
        self.assertEqual(decision.sql, "SELECT 1")
        self.assertEqual(decision.reason, "standard_champion")

    def test_selector_deterministic_tie_break(self):
        candidates = [
            self.candidate("SELECT 2", "deepseek", "standard", 0, "b"),
            self.candidate("SELECT 1", "qwen", "standard", 0, "a"),
        ]
        first = select_candidate(candidates)
        second = select_candidate(list(reversed(candidates)))
        self.assertEqual(first.sql, second.sql)

    def test_no_executable_fallback_prefers_nonempty_sql(self):
        candidates = [
            self.candidate("", "nanbeige", "standard", 0, None, ok=False),
            self.candidate("SELECT broken_column FROM t", "nanbeige", "standard", 1, None, ok=False),
        ]
        decision = select_candidate(candidates)
        self.assertEqual(decision.sql, "SELECT broken_column FROM t")
        self.assertEqual(decision.reason, "no_executable_fallback")

    def test_new_family_is_not_blocked_by_legacy_family_tie_break(self):
        candidates = [
            self.candidate("SELECT Z", "qwen", "standard", 0, "z"),
            self.candidate("SELECT A", "nanbeige", "standard", 0, "a"),
        ]
        decision = select_candidate(candidates)
        self.assertEqual(decision.sql, "SELECT A")
    def test_cross_family_alternative_list_is_ranked(self):
        candidates = [
            self.candidate("SELECT 1", "qwen", "standard", 0, "a"),
            self.candidate("SELECT 1", "deepseek", "standard", 0, "a"),
            self.candidate("SELECT 1", "qwen", "reread", 0, "a"),
            self.candidate("SELECT 1", "deepseek", "reread", 0, "a"),
            self.candidate("SELECT 2", "qwen", "standard", 1, "b"),
            self.candidate("SELECT 2", "deepseek", "standard", 1, "b"),
            self.candidate("SELECT 2", "qwen", "reread", 1, "b"),
        ]
        decision = select_candidate(candidates)
        self.assertEqual(decision.sql, "SELECT 1")
        self.assertEqual(decision.support, 4)
    def test_qwen_only_cross_view_consensus_can_select_alternative(self):
        candidates = [
            Candidate("SELECT 1", "qwen", "standard", 0, True, "a", schema_view="full"),
            Candidate("SELECT 1", "qwen", "standard", 1, True, "a", schema_view="full"),
            Candidate("SELECT 1", "qwen", "reread", 0, True, "a", schema_view="full"),
            Candidate("SELECT 2", "qwen", "standard", 0, True, "b", schema_view="linked"),
            Candidate("SELECT 2", "qwen", "standard", 1, True, "b", schema_view="linked"),
            Candidate("SELECT 2", "qwen", "reread", 0, True, "b", schema_view="linked"),
            Candidate("SELECT 2", "qwen", "reread", 1, True, "b", schema_view="full"),
        ]
        decision = select_candidate(candidates, strategy="all_cluster")
        self.assertEqual(decision.sql, "SELECT 2")

    def test_all_cluster_strategy_can_use_reread_consensus(self):
        candidates = [
            self.candidate("SELECT 1", "qwen", "standard", 0, "a"),
            self.candidate("SELECT 1", "qwen", "standard", 1, "a"),
            self.candidate("SELECT 2", "qwen", "reread", 0, "b"),
            self.candidate("SELECT 2", "deepseek", "reread", 0, "b"),
            self.candidate("SELECT 2", "deepseek", "reread", 1, "b"),
        ]
        decision = select_candidate(candidates, strategy="all_cluster")
        self.assertEqual(decision.sql, "SELECT 2")
        self.assertEqual(decision.support, 3)

    def test_bird_export_uses_numeric_qid_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selected = root / "selected.json"
            selected.write_text(json.dumps([
                {"question_id": 2, "db_id": "school", "sql": "SELECT 2"},
                {"question_id": 0, "db_id": "school", "sql": "SELECT 0"},
                {"question_id": 1, "db_id": "school", "sql": "SELECT 1"},
            ]), encoding="utf-8")
            output = export_bird(selected, root / "predicted.json")
            self.assertEqual(list(json.loads(output.read_text())), ["0", "1", "2"])
            report = validate_bird_predictions(output, 3, expected_qids=["2", "0", "1"])
            self.assertTrue(report["ok"])


class EndToEndTests(unittest.TestCase):
    def test_fake_end_to_end_resume_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "db" / "school"
            db_dir.mkdir(parents=True)
            db_path = db_dir / "school.sqlite"
            con = sqlite3.connect(db_path)
            con.executescript("CREATE TABLE students(id INTEGER); INSERT INTO students VALUES (1),(2);")
            con.commit(); con.close()
            source = root / "input.json"
            source.write_text(json.dumps([
                {"question_id": 0, "db_id": "school", "question": "Count students", "evidence": "", "SQL": "GOLD_SENTINEL"},
                {"question_id": 1, "db_id": "school", "question": "Count all students", "evidence": "", "SQL": "GOLD_SENTINEL"},
            ]), encoding="utf-8")
            work = root / "run"
            public_source = root / "public.json"
            prepare_public_input(source, public_source)
            client = FakeClient()
            config = {"samples_per_template": 1, "templates": ["standard", "reread"],
                      "models": {"qwen": "qwen3.8-27b", "deepseek": "deepseek-v4-pro-aliyun"},
                      "temperature": 0.7, "max_tokens": 128, "workers": 2, "exec_workers": 2,
           "exec_timeout_s": 5, "max_result_rows": 1000, "routes": {}}
            first = run_pipeline(public_source, root / "db", work, config, client=client)
            self.assertEqual(first["n_questions"], 2)
            self.assertEqual(first["n_predictions"], 2)
            self.assertEqual(len(client.requests), 8)
            pred_path = export_bird(work / "selected.json", work / "predicted_sql.json")
            report = validate_bird_predictions(pred_path, expected_count=2)
            self.assertEqual(report["n"], 2)
            self.assertTrue(audit_public_run(work)["ok"])
            calls_before = len(client.requests)
            resumed_config = {**config, "workers": 4, "exec_workers": 1}
            second = run_pipeline(public_source, root / "db", work, resumed_config, client=client)
            self.assertEqual(len(client.requests), calls_before)
            self.assertTrue(second["resumed"])
            self.assertTrue(all("top_p" not in request for request in client.requests))


class FailingClient:
    def complete(self, request):
        raise RuntimeError("HTTP 429")


class QwenOnlyTests(unittest.TestCase):
    def test_qwen_only_pipeline_does_not_require_deepseek(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "db" / "school"
            db_dir.mkdir(parents=True)
            con = sqlite3.connect(db_dir / "school.sqlite")
            con.execute("CREATE TABLE students(id INTEGER, currency TEXT)")
            con.execute("INSERT INTO students VALUES (1, 'EUR')")
            con.commit(); con.close()
            source = root / "public.json"
            source.write_text(json.dumps([{"question_id": 0, "db_id": "school", "question": "Count EUR students"}]), encoding="utf-8")
            client = FakeClient()
            summary = run_pipeline(source, root / "db", root / "run", {
                "models": {"qwen": "qwen3.8-27b"}, "templates": ["standard"],
                "samples_per_template": 4, "workers": 1, "exec_workers": 1, "value_hints": True,
                "temperatures": [0.2, 0.4, 0.6, 0.8], "top_p": 0.95,
            }, client=client)
            self.assertEqual(summary["n_candidates"], 4)
            self.assertTrue(all(r["model"] == "qwen3.8-27b" for r in client.requests))
            prompts = [r["messages"][0]["content"] for r in client.requests]
            self.assertTrue(all("students.currency" in p and "EUR" in p for p in prompts))
            self.assertEqual([r["temperature"] for r in client.requests], [0.2, 0.4, 0.6, 0.8])
            self.assertTrue(all(r["top_p"] == 0.95 for r in client.requests))

    def test_linked_schema_followup_generates_public_candidate_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "db" / "school"; db_dir.mkdir(parents=True)
            con = sqlite3.connect(db_dir / "school.sqlite")
            con.execute("CREATE TABLE students(id INTEGER PRIMARY KEY, hidden TEXT)")
            con.execute("INSERT INTO students VALUES (1, 'x')")
            con.commit(); con.close()
            source = root / "public.json"
            source.write_text(json.dumps([{"question_id": 0, "db_id": "school", "question": "Count students", "evidence": ""}]), encoding="utf-8")
            client = FakeClient()
            summary = run_pipeline(source, root / "db", root / "run", {
                "models": {"qwen": "qwen3.8-27b"}, "templates": ["standard", "reread"],
                "samples_per_template": 2, "workers": 1, "exec_workers": 1,
                "linked_schema_followup": True, "linked_temperatures": [0.2, 0.7],
            }, client=client)
            self.assertEqual(summary["n_candidates"], 8)
            candidates = json.loads((root / "run" / "candidates" / "0.json").read_text())["candidates"]
            self.assertEqual(sum(c.get("schema_view") == "linked" for c in candidates), 4)
            linked = json.loads((root / "run" / "public_linked_schemas.json").read_text())["0"]
            self.assertEqual([t["name"] for t in linked["tables"]], ["students"])
            self.assertNotIn("GOLD_SENTINEL", json.dumps(linked))


    def test_same_db_public_train_examples_are_injected_without_current_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "db" / "school"
            db_dir.mkdir(parents=True)
            con = sqlite3.connect(db_dir / "school.sqlite")
            con.execute("CREATE TABLE students(id INTEGER)")
            con.execute("INSERT INTO students VALUES (1)")
            con.commit(); con.close()
            source = root / "public.json"
            source.write_text(json.dumps([{"question_id": 10, "db_id": "school", "question": "Count students"}]), encoding="utf-8")
            examples = root / "train_examples.json"
            examples.write_text(json.dumps([
                {"question_id": 10, "db_id": "school", "question": "CURRENT_SENTINEL", "evidence": "", "SQL": "SELECT 'CURRENT_SENTINEL'"},
                {"question_id": 1, "db_id": "school", "question": "Example one?", "evidence": "use students", "SQL": "SELECT COUNT(*) FROM students"},
                {"question_id": 2, "db_id": "school", "question": "Example two?", "evidence": "", "SQL": "SELECT id FROM students"},
                {"question_id": 3, "db_id": "other", "question": "Other?", "evidence": "", "SQL": "SELECT 1"},
            ]), encoding="utf-8")
            client = FakeClient()
            run_pipeline(source, root / "db", root / "run", {
                "models": {"qwen": "qwen3.8-27b"}, "templates": ["standard"], "samples_per_template": 1,
                "workers": 1, "exec_workers": 1, "train_examples_file": str(examples),
                "train_example_count": 2,
            }, client=client)
            prompt = client.requests[0]["messages"][0]["content"]
            self.assertIn("Example one?", prompt)
            self.assertIn("Example two?", prompt)
            self.assertNotIn("Other?", prompt)
            self.assertNotIn("CURRENT_SENTINEL", prompt)
            persisted = json.loads((root / "run" / "public_train_examples.json").read_text(encoding="utf-8"))
            self.assertEqual([x["public_train_sql"] for x in persisted["10"]],
                             ["SELECT COUNT(*) FROM students", "SELECT id FROM students"])

    def test_length_without_sql_retries_without_public_train_shots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); db_dir = root / "db" / "school"; db_dir.mkdir(parents=True)
            con = sqlite3.connect(db_dir / "school.sqlite")
            con.execute("CREATE TABLE students(id INTEGER)"); con.execute("INSERT INTO students VALUES (1)")
            con.commit(); con.close()
            source = root / "public.json"
            source.write_text(json.dumps([{"question_id": 10, "db_id": "school", "question": "Count students"}]), encoding="utf-8")
            examples = root / "train_examples.json"
            examples.write_text(json.dumps([{"question_id": 1, "db_id": "school", "question": "Example one?", "evidence": "", "SQL": "SELECT COUNT(*) FROM students"}]), encoding="utf-8")

            class LengthThenSqlClient:
                def __init__(self): self.requests = []
                def complete(self, request):
                    import copy
                    self.requests.append(copy.deepcopy(request))
                    if len(self.requests) == 1:
                        return {"model": "qwen3.8-27b", "choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "unfinished"}}]}
                    return {"model": "qwen3.8-27b", "choices": [{"finish_reason": "stop", "message": {"content": "SELECT COUNT(*) FROM students"}}]}

            client = LengthThenSqlClient()
            run_pipeline(source, root / "db", root / "run", {
                "models": {"qwen": "qwen3.8-27b"}, "templates": ["standard"], "samples_per_template": 1,
                "workers": 1, "exec_workers": 1, "train_examples_file": str(examples), "train_example_count": 1,
                "context_length_fallback": True,
            }, client=client)
            self.assertEqual(len(client.requests), 2)
            self.assertIn("Example one?", client.requests[0]["messages"][0]["content"])
            self.assertNotIn("Example one?", client.requests[1]["messages"][0]["content"])
            candidate = json.loads((root / "run" / "candidates" / "10.json").read_text())["candidates"][0]
            self.assertTrue(candidate["context_fallback"])
            self.assertEqual(candidate["sql"], "SELECT COUNT(*) FROM students")

            # No implicit second request, even when a prompt has train examples.
            client = LengthThenSqlClient()
            with self.assertRaisesRegex(RuntimeError, "No SQL available"):
                run_pipeline(source, root / "db", root / "run-no-fallback", {
                    "models": {"qwen": "qwen3.8-27b"}, "templates": ["standard"],
                    "samples_per_template": 1, "workers": 1, "exec_workers": 1,
                    "train_examples_file": str(examples), "train_example_count": 1,
                }, client=client)
            self.assertEqual(len(client.requests), 1)

    def test_focused_schema_view_generates_lexically_linked_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); db_dir = root / "db" / "school"; db_dir.mkdir(parents=True)
            con = sqlite3.connect(db_dir / "school.sqlite")
            con.executescript("""
            CREATE TABLE students(id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE grades(grade_id INTEGER PRIMARY KEY, student_id INTEGER, score REAL);
            INSERT INTO students VALUES (1,'A'); INSERT INTO grades VALUES (1,1,90);
            """)
            con.commit(); con.close()
            source = root / "public.json"
            source.write_text(json.dumps([{"question_id": 0, "db_id": "school", "question": "What are the scores?"}]), encoding="utf-8")
            client = FakeClient()
            summary = run_pipeline(source, root / "db", root / "run", {
                "models": {"qwen": "qwen3.8-27b"}, "templates": ["standard"],
                "samples_per_template": 1, "workers": 1, "exec_workers": 1,
                "focused_schema_followup": True,
            }, client=client)
            self.assertEqual(summary["n_candidates"], 2)
            candidates = json.loads((root / "run" / "candidates" / "0.json").read_text())["candidates"]
            self.assertEqual(sum(c.get("schema_view") == "focused" for c in candidates), 1)
            focused = json.loads((root / "run" / "public_focused_schemas.json").read_text())["0"]
            names = {t["name"] for t in focused["tables"]}
            self.assertIn("grades", names)

    def test_evaluation_cohort_examples_are_excluded_before_prompting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "db" / "school"; db_dir.mkdir(parents=True)
            con = sqlite3.connect(db_dir / "school.sqlite")
            con.execute("CREATE TABLE students(id INTEGER)"); con.execute("INSERT INTO students VALUES (1)")
            con.commit(); con.close()
            source = root / "public.json"
            source.write_text(json.dumps([{"question_id": 10, "db_id": "school", "question": "Count students"}]), encoding="utf-8")
            examples = root / "train_examples.json"
            examples.write_text(json.dumps([
                {"question_id": 1, "db_id": "school", "question": "EXCLUDED_SENTINEL", "evidence": "", "SQL": "SELECT 'EXCLUDED'"},
                {"question_id": 2, "db_id": "school", "question": "Allowed example?", "evidence": "", "SQL": "SELECT COUNT(*) FROM students"},
            ]), encoding="utf-8")
            excluded = root / "excluded.json"
            excluded.write_text(json.dumps([{"question_id": 1, "db_id": "school", "question": "EXCLUDED_SENTINEL"}]), encoding="utf-8")
            client = FakeClient()
            run_pipeline(source, root / "db", root / "run", {
                "models": {"qwen": "qwen3.8-27b"}, "templates": ["standard"], "samples_per_template": 1,
                "workers": 1, "exec_workers": 1, "train_examples_file": str(examples),
                "train_example_exclude_files": [str(excluded)], "train_example_count": 1,
            }, client=client)
            prompt = client.requests[0]["messages"][0]["content"]
            self.assertIn("Allowed example?", prompt)
            self.assertNotIn("EXCLUDED_SENTINEL", prompt)

    def test_cross_domain_masked_examples_exclude_current_db_and_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            examples = Path(tmp) / "train_examples.json"
            examples.write_text(json.dumps([
                {"question_id": 10, "db_id": "school", "question": "Current?", "evidence": "", "SQL": "SELECT COUNT(*) FROM students"},
                {"question_id": 1, "db_id": "school", "question": "Same db?", "evidence": "", "SQL": "SELECT COUNT(*) FROM students"},
                {"question_id": 2, "db_id": "finance", "question": "What is total score of students in France?",
                 "evidence": "", "SQL": "SELECT SUM(score) FROM students WHERE country = 'France'"},
                {"question_id": 3, "db_id": "hr", "question": "How many employees are in 2020?",
                 "evidence": "", "SQL": "SELECT COUNT(*) FROM employees WHERE year = 2020"},
            ]), encoding="utf-8")
            bank = load_public_example_bank(examples)
            picked = examples_for_row(
                bank,
                {"question_id": 10, "db_id": "school",
                 "question": "What is total score of students in France?", "evidence": ""},
                2,
                scope="cross_domain_masked",
            )
            self.assertEqual([x["db_id"] for x in picked], ["finance", "hr"])
            self.assertIn("country = 'masked_value'", picked[0]["SQL"])
            self.assertNotIn("France", picked[0]["SQL"])
            self.assertIn("year = 0", picked[1]["SQL"])

class ScriptedClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        prompt = request["messages"][0]["content"]
        FakeClient.assert_public(prompt)
        sql = self.replies.pop(0)
        return {"choices": [{"message": {"content": sql}}], "model": request["model"], "usage": {}}


class GenerationRepairTests(unittest.TestCase):
    def test_execution_error_is_fed_back_and_repaired_before_freezing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "db" / "school"; db_dir.mkdir(parents=True)
            con = sqlite3.connect(db_dir / "school.sqlite")
            con.executescript("CREATE TABLE students(id INTEGER); INSERT INTO students VALUES (1),(2);")
            con.commit(); con.close()
            source = root / "public.json"
            source.write_text(json.dumps([{"question_id": 0, "db_id": "school", "question": "Count students", "evidence": ""}]), encoding="utf-8")
            client = ScriptedClient(["SELECT COUNT(*) FROM bad_table", "SELECT COUNT(*) FROM students"])
            run_pipeline(source, root / "db", root / "run", {
                "models": {"qwen": "qwen3.8-27b"}, "templates": ["standard"], "samples_per_template": 1,
                "workers": 1, "exec_workers": 1, "execution_corrections": 1,
            }, client=client)
            candidates = json.loads((root / "run" / "candidates" / "0.json").read_text())["candidates"]
            self.assertEqual(candidates[0]["sql"], "SELECT COUNT(*) FROM students")
            self.assertEqual(candidates[0]["initial_sql"], "SELECT COUNT(*) FROM bad_table")
            self.assertEqual(candidates[0]["execution_corrections"], 1)
            self.assertIn("no such table", client.requests[1]["messages"][0]["content"].lower())

    def test_literal_value_targets_are_fed_back_without_gold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "db" / "school"; db_dir.mkdir(parents=True)
            con = sqlite3.connect(db_dir / "school.sqlite")
            con.executescript("CREATE TABLE customers(id INTEGER, country TEXT, currency TEXT); INSERT INTO customers VALUES (1,'US','EUR');")
            con.commit(); con.close()
            source = root / "public.json"
            source.write_text(json.dumps([{"question_id": 0, "db_id": "school", "question": "Ids for EUR", "evidence": ""}]), encoding="utf-8")
            client = ScriptedClient(["SELECT id FROM customers WHERE country = 'EUR'",
                                     "SELECT id FROM customers WHERE currency = 'EUR'"])
            run_pipeline(source, root / "db", root / "run", {
                "models": {"qwen": "qwen3.8-27b"}, "templates": ["standard"], "samples_per_template": 1,
                "workers": 1, "exec_workers": 1, "literal_corrections": 1,
            }, client=client)
            candidates = json.loads((root / "run" / "candidates" / "0.json").read_text())["candidates"]
            self.assertEqual(candidates[0]["sql"], "SELECT id FROM customers WHERE currency = 'EUR'")
            self.assertEqual(candidates[0]["literal_corrections"], 1)
            correction_prompt = client.requests[1]["messages"][0]["content"]
            self.assertIn("customers.currency", correction_prompt)
            self.assertNotIn("customers.country", correction_prompt.split("### Previous SQL", 1)[-1])


class QwenOnlyGenerationErrorTests(unittest.TestCase):
    def test_generation_error_persists_nonsecret_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "db" / "school"
            db_dir.mkdir(parents=True)
            con = sqlite3.connect(db_dir / "school.sqlite")
            con.execute("CREATE TABLE students(id INTEGER)")
            con.commit(); con.close()
            source = root / "public.json"
            source.write_text(json.dumps([{"question_id": 0, "db_id": "school", "question": "Count students"}]), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                run_pipeline(source, root / "db", root / "run", {
                    "models": {"qwen": "qwen3.8-27b"}, "templates": ["standard"],
                    "samples_per_template": 1, "workers": 1,
                }, client=FailingClient())
            errors = json.loads((root / "run" / "generation_errors.json").read_text())
            self.assertEqual(errors[0]["detail"], "HTTP 429")


    def test_new_model_family_is_accepted_and_seeded_uniquely(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); db_dir = root / "db" / "school"; db_dir.mkdir(parents=True)
            con = sqlite3.connect(db_dir / "school.sqlite")
            con.executescript("CREATE TABLE students(id INTEGER); INSERT INTO students VALUES (1),(2);")
            con.commit(); con.close()
            source = root / "public.json"
            source.write_text(json.dumps([
                {"question_id": i, "db_id": "school", "question": f"Count {i}"}
                for i in range(3)
            ]), encoding="utf-8")

            class SeedClient:
                def __init__(self):
                    self.seeds = []
                def complete(self, request):
                    FakeClient.assert_public(request["messages"][0]["content"])
                    self.seeds.append(request["seed"])
                    return {"choices": [{"message": {"content": "SELECT COUNT(*) FROM students"}}],
                            "model": request["model"], "usage": {}}

            client = SeedClient()
            run_pipeline(source, root / "db", root / "run", {
                "models": {"nanbeige": "nanbeige-4.3-33b-q4"},
                "templates": ["standard", "reread"], "samples_per_template": 2,
                "workers": 1, "exec_workers": 1,
            }, client=client)
            self.assertEqual(len(client.seeds), 12)
            self.assertEqual(len(set(client.seeds)), 12)

    def test_reasoning_content_is_used_when_content_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); db_dir = root / "db" / "school"; db_dir.mkdir(parents=True)
            con = sqlite3.connect(db_dir / "school.sqlite")
            con.executescript("CREATE TABLE students(id INTEGER); INSERT INTO students VALUES (1);")
            con.commit(); con.close()
            source = root / "public.json"
            source.write_text(json.dumps([
                {"question_id": 0, "db_id": "school", "question": "Count students"}
            ]), encoding="utf-8")

            class ReasoningClient:
                def complete(self, request):
                    return {"choices": [{
                        "message": {"content": "", "reasoning_content": "SELECT COUNT(*) FROM students"},
                        "finish_reason": "stop",
                    }], "model": request["model"], "usage": {}}

            run_pipeline(source, root / "db", root / "run", {
                "models": {"nanbeige": "nanbeige-4.3-33b-q4"},
                "templates": ["standard"], "samples_per_template": 1,
                "workers": 1, "exec_workers": 1,
            }, client=ReasoningClient())
            candidates = json.loads((root / "run" / "candidates" / "0.json").read_text())["candidates"]
            self.assertEqual(candidates[0]["sql"], "SELECT COUNT(*) FROM students")

    def test_legacy_qwen_seed_formula_is_unchanged(self):
        class SeedClient:
            def __init__(self): self.requests = []
            def complete(self, request):
                self.requests.append(request)
                return {"choices": [{"message": {"content": "SELECT 1"}}], "model": request["model"], "usage": {}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); db_dir = root / "db" / "school"; db_dir.mkdir(parents=True)
            con = sqlite3.connect(db_dir / "school.sqlite"); con.execute("CREATE TABLE students(id INTEGER)")
            con.commit(); con.close()
            source = root / "public.json"
            source.write_text(json.dumps([{"question_id": 123, "db_id": "school", "question": "x"}]), encoding="utf-8")
            client = SeedClient()
            run_pipeline(source, root / "db", root / "run", {
                "models": {"qwen": "qwen3.8-27b"}, "templates": ["standard"], "samples_per_template": 1,
                "workers": 1, "exec_workers": 1,
            }, client=client)
            self.assertEqual(client.requests[0]["seed"], 123 * 100000)


if __name__ == "__main__":
    unittest.main()
