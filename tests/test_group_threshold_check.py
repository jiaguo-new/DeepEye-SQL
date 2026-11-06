"""Synthetic-only tests: static parsing, no SQL execution, no network."""
import sqlite3
import unittest
import socket
from unittest import mock

try:
    from birdfusion.group_threshold_check import classify_record, extract_threshold, reference_having
except ImportError as _e:  # sqlglot is an optional audit-only dependency
    import unittest
    raise SystemExit if False else unittest.SkipTest(str(_e))


def sql(having="COUNT(*) > 3", projection="s.team", distinct=False, wrap=False):
    body = f"SELECT {projection} FROM members s GROUP BY s.team HAVING {having}"
    return f"SELECT DISTINCT {projection} FROM members s GROUP BY s.team HAVING {having}" if distinct else body


class ExtractThresholdTests(unittest.TestCase):
    def test_exact_template_matches(self):
        cases = [
            ("List each team with at least 3 members.", "at least", 3, "team", "members"),
            ("List each big red team with more than 999 total members.", "more than", 999, "big red team", "total members"),
            ("List each a with at least 1 b.", "at least", 1, "a", "b"),
        ]
        for question, direction, n, group, child in cases:
            with self.subTest(question=question):
                self.assertEqual(extract_threshold(question),
                                 {"group": group, "direction": direction, "n": n, "child": child})

    def test_deviations_never_match(self):
        for question in (
            "list each team with at least 3 members.",          # wrong case
            "List each team with at least 3 members",           # missing period
            "List each team with atleast 3 members.",           # fused phrase
            "List each team with no less than 3 members.",      # other comparator
            "List each team with at least 007 members.",        # leading zero
            "List each team with at least 1000 members.",       # out of range
            "List each very big red team with at least 3 members.",  # 4-word group
            "List each team  with at least 3 members.",         # double space
            "List each team with at least 3 members, sorted.",  # trailing clause
            "For each team count members with at least 3.",     # different frame
            "List each team with at least 3 members and 2 staff.",   # conjunction
            None, 12,                                            # non-string
        ):
            with self.subTest(question=question):
                self.assertIsNone(extract_threshold(question))


class ReferenceHavingTests(unittest.TestCase):
    def test_ok_shapes(self):
        self.assertEqual(reference_having(sql()), {"status": "ok", "op": ">", "n": 3})
        self.assertEqual(reference_having(sql("COUNT(*) >= 3")), {"status": "ok", "op": ">=", "n": 3})
        self.assertEqual(reference_having(sql("COUNT(*) >= 12", projection="COUNT(*) AS n")),
                         {"status": "ok", "op": ">=", "n": 12})
        self.assertEqual(reference_having(sql("(COUNT(*) >= 3)", wrap=True)),
                         {"status": "ok", "op": ">=", "n": 3})
        self.assertEqual(reference_having(sql("COUNT(*) > 3", projection="s.team", distinct=True)),
                         {"status": "ok", "op": ">", "n": 3})
        self.assertEqual(reference_having(
            "SELECT s.team FROM members s GROUP BY s.team HAVING COUNT(*) >= 100 LIMIT 5"),
            {"status": "ok", "op": ">=", "n": 100})

    def test_rejections_cover_every_reason(self):
        cases = {
            "invalid_sql": [None, "", "SELECT COUNT(", "SELECT 1; SELECT 2"],
            "not_single_select": ["SELECT (SELECT MAX(x) FROM m) FROM t",
                                  "SELECT 1 UNION SELECT 2",
                                  "WITH c AS (SELECT 1) SELECT * FROM c"],
            "unsupported_node": ["SELECT CAST(COUNT(*) AS INT) FROM t GROUP BY x HAVING COUNT(*) > 3",
                                 "SELECT s.team FROM members s GROUP BY s.team "
                                 "HAVING COUNT(*) > 3 AND MAX(x) > 1"],
            "no_having": ["SELECT COUNT(*) FROM t", "SELECT team FROM t GROUP BY team"],
            "comparator_not_gt_gte": [sql("COUNT(*) < 3"), sql("COUNT(*) = 3"),
                                      sql("3 < COUNT(*)"),
                                      sql("COUNT(*) > 1 AND COUNT(*) < 5")],
            "count_not_left": [sql("s.size > 3")],
            "count_not_star": [sql("COUNT(s.id) > 3")],
            "distinct_count_present": [sql("COUNT(DISTINCT s.id) > 3"),
                                       sql("COUNT(*) > 3", projection="COUNT(DISTINCT s.id) AS n")],
            "n_invalid": [sql("COUNT(*) > 0"), sql("COUNT(*) > 1000"), sql("COUNT(*) > s.min")],
        }
        for expected, samples in cases.items():
            for sample in samples:
                with self.subTest(expected=expected, sql=sample):
                    result = reference_having(sample)
                    self.assertEqual(result["status"], expected, result)
                    if expected != "invalid_sql":
                        self.assertNotEqual(expected, "ok")

    def test_distinct_in_having_takes_precedence(self):
        # Precedence: any DISTINCT inside COUNT anywhere wins over shape reasons.
        self.assertEqual(reference_having(sql("COUNT(DISTINCT s.id) >= 3"))["status"],
                         "distinct_count_present")


class ClassifyRecordTests(unittest.TestCase):
    def record(self, question="List each team with more than 3 members.",
               evidence="", sql_text="SELECT COUNT(*) FROM m GROUP BY t HAVING COUNT(*) > 3"):
        return {"db_id": "db", "question": question, "evidence": evidence, "SQL": sql_text}

    def test_full_categories(self):
        self.assertEqual(classify_record(self.record())["category"], "applicable")
        result = classify_record(self.record())
        self.assertTrue(result["agree"])
        self.assertTrue(result["n_equal"])
        self.assertTrue(classify_record(self.record(
            question="List each team with at least 3 members.",
            sql_text="SELECT COUNT(*) FROM m GROUP BY t HAVING COUNT(*) >= 3"))["agree"])
        self.assertTrue(classify_record(self.record(
            question="List each team with at least 3 members.",
            sql_text="SELECT COUNT(*) FROM m GROUP BY t HAVING COUNT(*) > 3"))["n_equal"])
        self.assertFalse(classify_record(self.record(
            question="List each team with at least 3 members.",
            sql_text="SELECT COUNT(*) FROM m GROUP BY t HAVING COUNT(*) > 3"))["agree"])
        self.assertEqual(classify_record(self.record(
            sql_text="SELECT COUNT(*) FROM m GROUP BY t HAVING COUNT(*) > 5"))["category"], "applicable")
        self.assertFalse(classify_record(self.record(
            sql_text="SELECT COUNT(*) FROM m GROUP BY t HAVING COUNT(*) > 5"))["n_equal"])
        self.assertEqual(classify_record(self.record(evidence="staff are counted"))["category"],
                         "evidence_nonempty")
        self.assertEqual(classify_record(self.record(question="Count the members."))["category"],
                         "no_template_match")
        self.assertEqual(classify_record(self.record(
            sql_text="SELECT COUNT(*) FROM m GROUP BY t HAVING COUNT(s.id) > 3"))["reason"],
            "count_not_star")
        self.assertEqual(classify_record({"db_id": "db"})["category"], "invalid_record")
        self.assertEqual(classify_record(None)["category"], "invalid_record")
        bad = self.record(); bad["SQL"] = 7
        self.assertEqual(classify_record(bad)["category"], "invalid_record")

    def test_never_executes_sql_or_network(self):
        with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("sql execution forbidden")), \
             mock.patch.object(socket, "socket", side_effect=AssertionError("network forbidden")), \
             mock.patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
            self.assertEqual(classify_record(self.record())["category"], "applicable")
            self.assertIsNone(extract_threshold("List each team with at least x members."))


if __name__ == "__main__":
    unittest.main()
