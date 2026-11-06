"""Fusion v2: anchor-with-corroboration selection over a multi-family candidate pool.

A fully offline, gold-free selection system:

  1. POOL      candidates from independent prediction families (manifest-driven)
  2. EXECUTE   every candidate read-only against the question's SQLite database
               (30 s cap, official BIRD set-equality: duplicate rows collapse),
               cached on disk
  3. SELECT    cluster candidates by exact result set; prefer non-empty
               clusters; anchor to a designated family (the strongest single
               deliverable) and override only with strict cross-family
               corroboration — the alternative needs at least K_SWITCH
               non-anchor families and LEAD more families than the anchor
               cluster
  4. EXPORT    official BIRD submission format with SHA-256 manifest

This module never reads ground truth. Evaluation is the separate
``birdfusion evaluate`` command (see cli.py), exactly as for the rest of the
pipeline. All thresholds are global scalars disclosed in FUSION_V2_SPEC.md.

Empirical note (why family COUNT, not weighted scores): family quality
signals observable without gold — execution success rate and internal
self-consistency — do not correlate with answer accuracy on this pool (the
most execution-successful and self-consistent family in the pool is only
mid-accuracy). Equal family counts were measured strictly better than any
weighted variant we tested; the grid is recorded in FUSION_V2_SPEC.md.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from func_timeout import func_timeout

DEL = '\t----- bird -----\t'
DEFAULT_TIMEOUT_S = 30
DEFAULT_ROW_CAP = 200_000
DEFAULT_ANCHOR = 'deepeye'
DEFAULT_K_SWITCH = 4
DEFAULT_LEAD = 2


def normalize_sql(sql: str) -> str:
    """Strip BIRD suffix, comments, trailing semicolon; collapse whitespace."""
    s = sql.split(DEL)[0] if DEL in sql else sql
    s = s.strip().rstrip(';').strip()
    s = re.sub(r'--[^\n]*\n', ' ', s)
    s = re.sub(r'/\*.*?\*/', ' ', s, flags=re.S)
    return ' '.join(s.split())


def _rhash(rows) -> str:
    """Hash the result SET (official BIRD set-equality: duplicate rows collapse)."""
    row_set = set(map(tuple, rows))
    return hashlib.sha256(repr(sorted(map(repr, row_set))).encode()).hexdigest()[:16]


def exec_sql(db_path: str, sql: str, timeout_s: int = DEFAULT_TIMEOUT_S,
             row_cap: int = DEFAULT_ROW_CAP):
    """Execute one SQL read-only with a hard timeout. Official set-equality."""
    def run():
        con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=timeout_s - 2)
        try:
            con.execute('PRAGMA query_only=ON')
            cur = con.execute(sql)
            rows = cur.fetchmany(row_cap)
            more = cur.fetchone() is not None
            return rows, more
        finally:
            con.close()
    try:
        rows, more = func_timeout(timeout_s, run)
    except BaseException as e:
        return {'status': 'TIMEOUT' if 'TimedOut' in type(e).__name__ else 'ERROR'}
    if more:
        return {'status': 'ROWCAP'}
    row_set = set(map(tuple, rows))
    return {'status': 'OK', 'rh': _rhash(rows), 'n': len(row_set),
            'nc': len(rows[0]) if rows else 0, 'sample': rows[:2]}


def ekey(db_id: str, sql: str) -> str:
    return hashlib.sha256((db_id + '\x00' + sql).encode()).hexdigest()


# --------------------------------------------------------------------------- pool

def build_pool(manifest: dict, work: Path) -> dict:
    """manifest: {"arms_file": path, "prediction_files": {family: path}}.

    arms_file: {qid: {arm: [sql...], ..., "_db": db_id}}  (multi-candidate arms)
    prediction_files: {family: {qid: sql}}                 (single-prediction)
    """
    arms = json.load(open(manifest['arms_file'], encoding='utf-8')) if manifest.get('arms_file') else {}
    preds = {fam: json.load(open(p, encoding='utf-8'))
             for fam, p in (manifest.get('prediction_files') or {}).items()}
    db_of, pool = {}, {}
    for qid, arm_map in arms.items():
        db_of[str(qid)] = arm_map['_db']
        entries = {}
        for fam, cands in arm_map.items():
            if fam == '_db' or not isinstance(cands, list):
                continue
            for c in cands:
                if isinstance(c, str) and c.strip():
                    s = normalize_sql(c)
                    if s:
                        entries.setdefault(s, set()).add(fam)
        for fam, table in preds.items():
            v = table.get(str(qid))
            if isinstance(v, str) and v.strip():
                s = normalize_sql(v)
                if s:
                    entries.setdefault(s, set()).add(fam)
        pool[str(qid)] = [{'sql': s, 'fams': sorted(f)} for s, f in sorted(entries.items())]
    data = {'db_of': db_of, 'pool': pool}
    json.dump(data, open(work / 'pool.json', 'w', encoding='utf-8'), ensure_ascii=False)
    return data


def execute_pool(pool_data: dict, db_root: Path, work: Path,
                 workers: int = 14, timeout_s: int = DEFAULT_TIMEOUT_S) -> dict:
    """Execute every unique SQL; resumable via the on-disk cache."""
    db_of, pool = pool_data['db_of'], pool_data['pool']
    cache_path = work / 'exec_cache.json'
    cache = json.load(open(cache_path, encoding='utf-8')) if cache_path.exists() else {}
    jobs = []
    for qid, cands in pool.items():
        for e in cands:
            k = ekey(db_of[qid], e['sql'])
            if k not in cache:
                jobs.append((k, db_of[qid], e['sql']))
    if jobs:
        def work_one(job):
            k, db, sql = job
            return k, exec_sql(f'{db_root}/{db}/{db}.sqlite', sql, timeout_s)
        with ThreadPoolExecutor(workers) as ex:
            for i, (k, res) in enumerate(ex.map(work_one, jobs), 1):
                cache[k] = res
                if i % 2000 == 0:
                    json.dump(cache, open(cache_path, 'w', encoding='utf-8'))
    json.dump(cache, open(cache_path, 'w', encoding='utf-8'))
    return cache


# ------------------------------------------------------------------------ select

def clusters_for(qid, pool_data, cache):
    db_of, pool = pool_data['db_of'], pool_data['pool']
    cl = defaultdict(lambda: {'fams': set(), 'cands': [], 'n': None, 'nc': None, 'rh': None})
    for e in pool[qid]:
        r = cache.get(ekey(db_of[qid], e['sql']), {})
        if r.get('status') != 'OK':
            continue
        d = cl[r['rh']]
        d['fams'].update(e['fams'])
        d['cands'].append(e)
        d['n'], d['nc'], d['rh'] = r['n'], r['nc'], r['rh']
    return list(cl.values())


def _rank_key(c):
    """Deterministic order: more families, more candidates, family names, SQL."""
    return (-len(c['fams']), -len(c['cands']), tuple(sorted(c['fams'])), c['cands'][0]['sql'])


def _rep(cluster, anchor):
    for e in cluster['cands']:
        if anchor in e['fams']:
            return e['sql']
    return sorted(cluster['cands'],
                  key=lambda e: (tuple(sorted(e['fams'])), e['sql']))[0]['sql']


def select(qid, pool_data, cache, anchor=DEFAULT_ANCHOR,
           k_switch=DEFAULT_K_SWITCH, lead=DEFAULT_LEAD):
    """Return (sql, meta). See module docstring for the rule."""
    clusters = clusters_for(qid, pool_data, cache)
    if not clusters:
        return None, {'reason': 'no_executable_cluster'}
    nonempty = [c for c in clusters if c['n'] > 0]
    universe = nonempty if nonempty else clusters

    anchor_cl = next((c for c in universe if anchor in c['fams']), None)
    if anchor_cl is not None:
        alts = [c for c in universe
                if c is not anchor_cl
                and len(c['fams'] - {anchor}) >= k_switch
                and len(c['fams']) >= len(anchor_cl['fams']) + lead]
        if alts:
            best = sorted(alts, key=_rank_key)[0]
            return _rep(best, anchor), {'reason': 'anchor_overridden', 'rh': best['rh']}
        return _rep(anchor_cl, anchor), {'reason': 'anchor_kept', 'rh': anchor_cl['rh']}
    best = sorted(universe, key=_rank_key)[0]
    return _rep(best, anchor), {'reason': 'family_vote', 'rh': best['rh']}


def run_selection(pool_data: dict, cache: dict, anchor=DEFAULT_ANCHOR,
                  k_switch=DEFAULT_K_SWITCH, lead=DEFAULT_LEAD):
    out, meta = {}, {}
    for qid in pool_data['pool']:
        sql, m = select(qid, pool_data, cache, anchor, k_switch, lead)
        out[qid] = sql
        meta[qid] = m
    return out, meta


def export_official(predictions: dict, pool_data: dict, out_path: Path) -> str:
    """Write official BIRD format (SQL \t----- bird -----\t db_id); return SHA."""
    db_of = pool_data['db_of']
    lines = {str(q): (sql + DEL + db_of[str(q)] if sql else ' ' + DEL + db_of[str(q)])
             for q, sql in predictions.items()}
    payload = json.dumps(lines, ensure_ascii=False, indent=1)
    out_path.write_text(payload, encoding='utf-8', newline='\n')
    return hashlib.sha256(out_path.read_bytes()).hexdigest()
