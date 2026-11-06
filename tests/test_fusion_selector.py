"""Unit tests for the gold-free fusion selector (synthetic fixtures only)."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from birdfusion.fusion import (
    build_pool, clusters_for, ekey, exec_sql, export_official,
    normalize_sql, run_selection, select,
)


def make_pool_data(pool_spec, db='toy'):
    """pool_spec: {qid: [(sql, [fams])]} -> pool_data (no exec cache needed)."""
    pool = {qid: [{'sql': s, 'fams': sorted(f)} for s, f in cands]
            for qid, cands in pool_spec.items()}
    return {'db_of': {qid: db for qid in pool}, 'pool': pool}


def fake_cache(pool_data, results):
    """results: {qid: {sql: result_dict}}"""
    cache = {}
    for qid, per in results.items():
        for sql, r in per.items():
            cache[ekey(pool_data['db_of'][qid], sql)] = r
    return cache


def res(rh, n=1, nc=1):
    return {'status': 'OK', 'rh': rh, 'n': n, 'nc': nc, 'sample': []}


class NormalizeTests(unittest.TestCase):
    def test_strips_suffix_comments_and_semicolon(self):
        raw = "SELECT a -- tail\nFROM t ;  \t----- bird -----\t financial"
        self.assertEqual(normalize_sql(raw), 'SELECT a FROM t')


class SelectorTests(unittest.TestCase):
    """Frozen rule: anchor kept unless an alternative has >= K_SWITCH
    non-anchor families AND >= LEAD more families than the anchor cluster."""

    def setUp(self):
        self.pool_data = make_pool_data({
            # alt has only 2 non-anchor families -> below k_switch, anchor kept
            '0': [('SELECT 1', ['deepeye']),
                  ('SELECT 2', ['qwen', 'omnisql'])],
            # alt has 4 non-anchor families, anchor is alone -> override
            '1': [('SELECT 3', ['deepeye']),
                  ('SELECT 4', ['qwen', 'omnisql', 'deepseek', 'sqlplus'])],
            # alt has 4 non-anchor families but anchor cluster itself has 3
            # families -> +1 only, below lead=2 -> anchor kept
            '2': [('SELECT 5', ['deepeye', 'qwen', 'omnisql']),
                  ('SELECT 6', ['qwen', 'omnisql', 'deepseek', 'sqlplus'])],
            # anchor executes empty -> excluded from universe, family vote wins
            '3': [('SELECT 7', ['deepeye']),
                  ('SELECT 8', ['qwen', 'omnisql', 'deepseek', 'sqlplus'])],
        })
        self.cache = fake_cache(self.pool_data, {
            '0': {'SELECT 1': res('A'), 'SELECT 2': res('B')},
            '1': {'SELECT 3': res('C'), 'SELECT 4': res('D')},
            '2': {'SELECT 5': res('E'), 'SELECT 6': res('F')},
            '3': {'SELECT 7': res('G', n=0), 'SELECT 8': res('H')},
        })

    def test_anchor_kept_below_k_switch(self):
        sql, meta = select('0', self.pool_data, self.cache, k_switch=4, lead=2)
        self.assertEqual(meta['reason'], 'anchor_kept')
        self.assertEqual(sql, 'SELECT 1')

    def test_anchor_overridden_with_strong_cross_family_support(self):
        sql, meta = select('1', self.pool_data, self.cache, k_switch=4, lead=2)
        self.assertEqual(meta['reason'], 'anchor_overridden')
        self.assertEqual(sql, 'SELECT 4')

    def test_anchor_kept_when_lead_insufficient(self):
        sql, meta = select('2', self.pool_data, self.cache, k_switch=4, lead=2)
        self.assertEqual(meta['reason'], 'anchor_kept')
        self.assertEqual(sql, 'SELECT 5')

    def test_empty_anchor_cluster_excluded_from_universe(self):
        sql, meta = select('3', self.pool_data, self.cache, k_switch=4, lead=2)
        self.assertEqual(meta['reason'], 'family_vote')
        self.assertEqual(sql, 'SELECT 8')

    def test_no_executable_returns_none(self):
        cache = fake_cache(self.pool_data, {'0': {'SELECT 1': {'status': 'ERROR'},
                                                 'SELECT 2': {'status': 'TIMEOUT'}}})
        sql, meta = select('0', self.pool_data, cache, k_switch=4, lead=2)
        self.assertIsNone(sql)
        self.assertEqual(meta['reason'], 'no_executable_cluster')

    def test_run_selection_covers_all_questions(self):
        out, meta = run_selection(self.pool_data, self.cache, k_switch=4, lead=2)
        self.assertEqual(set(out), {'0', '1', '2', '3'})

    def test_set_semantics_collapses_duplicate_rows(self):
        # two SQLs whose row multisets differ but sets agree -> one cluster
        pool_data = make_pool_data({
            '0': [('SELECT a FROM t', ['f1']), ('SELECT DISTINCT a FROM t', ['f2'])]})
        cache = fake_cache(pool_data, {
            '0': {'SELECT a FROM t': res('X', n=2),   # multiset {(1,),(1,),(2,)}
                  'SELECT DISTINCT a FROM t': res('X', n=2)}})
        cl = clusters_for('0', pool_data, cache)
        self.assertEqual(len(cl), 1)
        self.assertEqual(cl[0]['fams'], {'f1', 'f2'})


class ExecutorTests(unittest.TestCase):
    def test_exec_sql_official_semantics(self):
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / 'toy' / 'toy.sqlite')
            Path(db).parent.mkdir()
            con = sqlite3.connect(db)
            con.execute('CREATE TABLE t (a INT)')
            con.executemany('INSERT INTO t VALUES (?)', [(1,), (1,), (2,)])
            con.commit()
            con.close()
            r = exec_sql(db, 'SELECT a FROM t')
            self.assertEqual(r['status'], 'OK')
            self.assertEqual(r['n'], 2)          # set semantics: duplicates collapse
            r2 = exec_sql(db, 'SELECT nope FROM t')
            self.assertEqual(r2['status'], 'ERROR')
            r3 = exec_sql(db, 'DELETE FROM t')
            self.assertEqual(r3['status'], 'ERROR')  # read-only guard


class PoolAndExportTests(unittest.TestCase):
    def test_build_pool_dedups_and_merges_families(self):
        with tempfile.TemporaryDirectory() as td:
            arms = {'0': {'_db': 'toy',
                          'famA': ['SELECT 1', 'SELECT 1'],   # dup within family
                          'famB': ['SELECT 1;']}}               # same after normalize
            arms_p = Path(td) / 'arms.json'
            arms_p.write_text(json.dumps(arms), encoding='utf-8')
            pred_p = Path(td) / 'pred.json'
            pred_p.write_text(json.dumps({'0': 'SELECT 1\t----- bird -----\ttoy'}),
                              encoding='utf-8')
            manifest = {'arms_file': str(arms_p),
                        'prediction_files': {'deepeye': str(pred_p)}}
            data = build_pool(manifest, Path(td))
            self.assertEqual(len(data['pool']['0']), 1)
            self.assertEqual(data['pool']['0'][0]['fams'], ['deepeye', 'famA', 'famB'])

    def test_official_format_roundtrip(self):
        pool_data = make_pool_data({'0': [('SELECT 1', ['deepeye'])]})
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'pred.json'
            sha = export_official({'0': 'SELECT 1'}, pool_data, p)
            data = json.load(open(p, encoding='utf-8'))
            self.assertEqual(data['0'], 'SELECT 1\t----- bird -----\ttoy')
            self.assertEqual(len(sha), 64)


if __name__ == '__main__':
    unittest.main()
