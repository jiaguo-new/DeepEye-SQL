"""Synthetic-only tests; run with python -m unittest discover -s tests."""
import copy
import itertools
import unittest

try:
    from birdfusion.count_distinct_selector import cue_statistics, select_count_distinct
except ImportError as _e:  # sqlglot is an optional audit-only dependency
    import unittest
    raise SystemExit if False else unittest.SkipTest(str(_e))


PLAIN = "SELECT COUNT(t.x) FROM t WHERE t.y > 0"
DISTINCT = "SELECT COUNT(DISTINCT t.x) FROM t WHERE t.y > 0"


def records(sizes=(7, 7, 6), distinct_counts=None):
    distinct_counts = distinct_counts or sizes
    return [dict(db_id=f"db{db}", question=f"How many items {db} {i}?", evidence="",
                 sql=DISTINCT if i < distinct_counts[db] else PLAIN)
            for db, n in enumerate(sizes) for i in range(n)]


def candidates():
    return [dict(slot=0, sql=PLAIN, success=True, result_key="plain"),
            dict(slot=1, sql=DISTINCT, success=True, result_key="distinct")]


def decide(cs=None, train=None, **kwargs):
    args = dict(train_records=records() if train is None else train, target_db="target",
                target_question="How many items are there?", candidates=candidates() if cs is None else cs,
                baseline_slot=0, expected_slot_count=2)
    args.update(kwargs)
    return select_count_distinct(**args)


class CueStatisticsTests(unittest.TestCase):
    def test_thresholds_and_exclusions(self):
        train = records((5, 5, 10), (4, 4, 8))
        stats = cue_statistics(train, "target", "How many items are there?")
        self.assertEqual((stats["database_count"], stats["pooled_eligible"], stats["pooled_distinct"]), (3, 20, 16))
        self.assertTrue(stats["sufficient"])
        train += records((5,), (5,))
        train += [dict(db_id="target", question="count targets", sql=DISTINCT)] * 50
        train += [dict(db_id="another", question=" HOW   MANY ITEMS ARE THERE? ", sql=DISTINCT)] * 50
        stats = cue_statistics(train, "TaRGeT", "how many items are there?")
        self.assertEqual(stats["database_count"], 3)
        self.assertNotIn("another", stats["contributing_databases"])
        self.assertNotIn("target", stats["contributing_databases"])

    def test_individual_and_pooled_thresholds(self):
        for train in [records((5, 5, 9)), records((4, 8, 8)),
                      records((5, 5, 10), (3, 5, 10)), records((10, 10))]:
            self.assertEqual(decide(train=train)["reason"], "insufficient_training_support")
        self.assertTrue(cue_statistics(records((5, 5, 10), (4, 4, 8)), "x", "different count")["sufficient"])

    def test_cue_evidence_and_eligibility(self):
        train = records()
        train[0]["question"] = "items?"
        train[0]["evidence"] = "NUMBER OF items"
        self.assertTrue(cue_statistics(train, "x", "count target")["sufficient"])
        train[0]["evidence"] = "discounted"
        self.assertFalse(cue_statistics(train, "x", "count target")["sufficient"])
        self.assertEqual(decide(target_question="list items")["reason"], "target_cue_missing")
        self.assertEqual(decide(target_question="list items", target_evidence="number of items")["decision"], "switch")
        self.assertEqual(decide(target_evidence=None)["reason"], "invalid_target_evidence")

    def test_exclusion_happens_before_thresholds(self):
        train = records((5, 5, 10))
        train[0]["question"] = "count TARGET"
        self.assertFalse(cue_statistics(train, "x", " Count   target ")["sufficient"])
        self.assertFalse(cue_statistics(records((5, 5, 10)), "DB0", "count target")["sufficient"])

    def test_untrusted_inputs_fail_closed(self):
        self.assertEqual(decide(train=None, target_db="")["reason"], "invalid_training_input")
        train = records()
        train[0]["evidence"] = {"untrusted": True}
        self.assertFalse(cue_statistics(train, "x", "count target")["sufficient"])
        with self.assertRaises(ValueError):
            cue_statistics({}, "x", "count target")


class SelectorTests(unittest.TestCase):
    def test_switch_and_candidate_order_invariance(self):
        cs = candidates() + [dict(slot=2, sql=DISTINCT, success=True, result_key="distinct")]
        for order in itertools.permutations(cs):
            result = decide(list(order), expected_slot_count=3)
            self.assertEqual((result["decision"], result["selected_slot"]), ("switch", 1))
            self.assertEqual(result["provenance"]["pooled_eligible"], 20)

    def test_slot_coverage_and_success_fail_closed(self):
        for kw in [dict(expected_slot_count=None), dict(expected_slot_count=True),
                   dict(expected_slot_count=3), dict(baseline_slot=True), dict(baseline_slot=2)]:
            self.assertEqual(decide(**kw)["decision"], "abstain")
        for change in [dict(success=None), dict(success=1), dict(success=False),
                       dict(result_key=None), dict(result_key=" "), dict(error="timeout"),
                       dict(slot=0), dict(slot=2)]:
            cs = candidates(); cs[1].update(change)
            self.assertEqual(decide(cs)["decision"], "abstain", change)
        for key in ("success", "result_key", "slot"):
            cs = candidates(); del cs[1][key]
            self.assertEqual(decide(cs)["decision"], "abstain", key)

    def test_clusters_baseline_and_operator(self):
        cs = candidates(); cs[1]["result_key"] = "plain"
        self.assertEqual(decide(cs)["reason"], "expected_two_result_clusters")
        cs = candidates() + [dict(slot=2, sql=DISTINCT, success=True, result_key="third")]
        self.assertEqual(decide(cs, expected_slot_count=3)["reason"], "expected_two_result_clusters")
        self.assertEqual(decide(baseline_slot=1)["reason"], "baseline_not_plain")
        cs = candidates(); cs[1]["sql"] = PLAIN
        self.assertEqual(decide(cs)["reason"], "expected_opposite_count_operators")
        cs = candidates(); cs[1]["sql"] = DISTINCT.replace(" > 0", " > 1")
        self.assertEqual(decide(cs)["reason"], "candidate_ast_mismatch")
        for sql in (DISTINCT.replace("t.x", "t.X"), DISTINCT.replace("t.x", 't."x"')):
            cs = candidates(); cs[1]["sql"] = sql
            self.assertEqual(decide(cs)["reason"], "candidate_ast_mismatch")

    def test_cluster_internal_consistency_and_unique_alternate(self):
        cs = candidates() + [dict(slot=2, sql=DISTINCT.replace(" > 0", " > 1"), success=True, result_key="distinct")]
        self.assertEqual(decide(cs, expected_slot_count=3)["reason"], "inconsistent_cluster_sql")
        cs[2]["sql"] = DISTINCT.lower()
        self.assertEqual(decide(cs, expected_slot_count=3)["reason"], "alternate_sql_not_unique")
        cs[2]["sql"] = PLAIN
        self.assertEqual(decide(cs, expected_slot_count=3)["reason"], "inconsistent_cluster_sql")

    def test_unsupported_queries_rejected_in_training_and_candidates(self):
        bad_sql = [
            "SELECT COUNT(*) FROM t", "SELECT COUNT(DISTINCT *) FROM t",
            "SELECT COUNT(DISTINCT x, y) FROM t", "SELECT COUNT(x + 1) FROM t",
            "SELECT COUNT(x), COUNT(y) FROM t", "SELECT COUNT(x) + 1 FROM t",
            "SELECT COUNT(x) FROM (SELECT x FROM t)",
            "WITH q AS (SELECT x FROM t) SELECT COUNT(x) FROM q",
            "SELECT COUNT(x) FROM t UNION SELECT COUNT(x) FROM s",
            "SELECT COUNT(x) FILTER (WHERE x > 0) FROM t",
            "SELECT COUNT(x) OVER () FROM t",
            "SELECT COUNT(x) FROM t WHERE EXISTS (SELECT 1 FROM s WHERE s.x=t.x)",
            "SELECT COUNT(x) FROM t; SELECT 1", "DELETE FROM t", "SELECT COUNT(x) FROM t garbage nonsense",
            "SELECT DISTINCT COUNT(x) FROM t", "SELECT COUNT(x) FROM t WHERE random() > 0",
            "SELECT COUNT(x) FROM t WHERE x IN (SELECT x FROM s)",
        ]
        for sql in bad_sql:
            with self.subTest(sql=sql):
                cs = candidates(); cs[1]["sql"] = sql
                self.assertEqual(decide(cs)["reason"], "unsupported_candidate_sql")
                train = records(); train[0]["sql"] = sql
                self.assertFalse(cue_statistics(train, "x", "count target")["sufficient"])

    def test_supported_flat_join_and_alias(self):
        cs = candidates()
        cs[0]["sql"] = "SELECT COUNT(t.x) AS n FROM t JOIN s ON t.y = s.y WHERE s.z IS NOT NULL"
        cs[1]["sql"] = cs[0]["sql"].replace("COUNT(t.x)", "COUNT(DISTINCT t.x)")
        self.assertEqual(decide(cs)["decision"], "switch")

    def test_no_mutation_or_sql_in_output(self):
        train, cs = records(), candidates()
        originals = copy.deepcopy((train, cs))
        result = decide(cs, train)
        self.assertEqual((train, cs), originals)
        self.assertNotIn("SELECT", str(result))
        self.assertNotIn("result_key", str(result))


if __name__ == "__main__":
    unittest.main()
