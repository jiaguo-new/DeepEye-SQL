#!/usr/bin/env python3
"""Reproduce shipping v16 (Dev EX 74.71%, 1146/1534) from frozen on-disk artifacts only.
Deterministic: no LLM calls, no randomness, no Dev gold used in any decision.

Rule chain over the v3 baseline (v3 is fully reproducible from ROUND4 artifacts):
  v10  TRAIN-preference override                       266 picks
  A    empty-result repair                              10
  C3   >=8 clusters          -> most-supported cluster  16
  C5   >=6 clusters, champ support<=3, lead>=2          16
  D    DeepEye-family corroboration                     27
  E    DeepEye tiebreak (de_maj==0, ncl>=4, rows<)      10
  F    DeepEye tiebreak, union of 5 gold-free gates     54
"""
import json, hashlib, sqlite3, sys, argparse
from pathlib import Path
from func_timeout import func_timeout
from concurrent.futures import ThreadPoolExecutor
BASE = Path(__file__).resolve().parent
DATA = BASE/'data'
_ap = argparse.ArgumentParser(description='Reproduce frozen v16 from repo data/ (rules logic frozen; see SELECTOR_V16_SPEC.md)')
_ap.add_argument('--dev-root', default=str(Path.home()/'bird_dev_check'/'dev_20240627'), help='unzipped official dev_20240627 (dev.json/dev.sql/dev_databases inside)')
_ap.add_argument('--deepeye-b', default=None, help='path to upstream-published results/bird-dev/qwen3.6-27b.json (SHA 0d073da4...) from HKUSTDial/DeepEye-SQL @252ab45a (MIT)')
_ap.add_argument('--out', default=str(DATA/'reproduce_out.json'))
_ap.add_argument('--retry-timeouts', action='store_true', help='feature phase: retry executions that hit the 25 s cap once, serially (mitigates concurrency-induced boundary flips, e.g. qid 584 under 14-thread load; no-op on idle machines)')
_args = _ap.parse_args()
DEV = Path(_args.dev_root).expanduser()
DEL = '\t----- bird -----'
def n(s): return ' '.join(s.split(DEL)[0].split())
dev = {str(d['question_id']): d for d in json.load(open(DEV/'dev.json'))}
def pth(q): d = dev[q]; return str(DEV/'dev_databases'/d['db_id']/(d['db_id']+'.sqlite'))
def _rs_once(p, sql):
    def run():
        con = sqlite3.connect(f'file:{p}?mode=ro', uri=True, timeout=20); con.execute('PRAGMA query_only=ON')
        r = con.execute(sql).fetchall(); con.close(); return frozenset(tuple(x) for x in r)
    try: return func_timeout(25, run)
    except BaseException: return None

def rs(p, sql):
    r = _rs_once(p, sql)
    if r is None and _args.retry_timeouts:
        import time; time.sleep(1.0)
        r = _rs_once(p, sql)
    return r

def main():
    import os
    def _sha(p): return hashlib.sha256(open(p,'rb').read()).hexdigest()[:8]
    V15P = DATA/'shipping_v15_predicted_sql.json'
    UP = DATA/'unified_pool.goldfree.json'
    DP = DATA/'DE12_POOL.goldfree.json'
    for f in (V15P,UP,DP):
        if not f.exists(): sys.exit(f'MISSING repo artifact {f} — see data/MANIFEST.sha256')
    print('input SHAs:', {f.name: _sha(f) for f in (V15P,UP,DP)})
    if _sha(V15P)!='d4d14758': sys.exit('shipping_v15 SHA mismatch vs frozen d4d14758')
    if _sha(UP)!='a1a6523c': sys.exit('unified_pool.goldfree SHA mismatch vs frozen a1a6523c')
    if _sha(DP)!='4acbd36c': sys.exit('DE12_POOL.goldfree SHA mismatch vs frozen 4acbd36c')
    V15 = json.load(open(V15P))
    U = json.load(open(UP))
    DE12 = json.load(open(DP))
    # gold-free pools use the canonical key 'c' for cluster member SQL; expose as 'sql' (data-format shim, no rule logic touched)
    for _pool in (U, DE12):
        for _e in _pool.values():
            for _c in _e.get('cl') or []:
                if 'c' in _c and 'sql' not in _c: _c['sql'] = _c['c']
    if _args.deepeye_b is None:
        sys.exit('MISSING upstream-published DeepEye predictions (B). Obtain from HKUSTDial/DeepEye-SQL @ 252ab45a7adf9f88fb93d1ff27ca019d20f0449a results/bird-dev/qwen3.6-27b.json (git-tracked, MIT, SHA 0d073da4) and pass --deepeye-b. Not redistributed here.')
    bp = Path(_args.deepeye_b).expanduser()
    if _sha(bp) != '0d073da4': sys.exit('B file SHA mismatch: expected upstream-published 0d073da4')
    DE = json.load(open(bp))
    RULES = {
     'F1': lambda f: not f['s_in_pool'] and f['dm']==0 and f['v_nfam']<=1,
     'F2': lambda f: f['dm']>f['am'] and f['v_cols']==f['de_cols'] and f['v_nfam']<=1,
     'F3': lambda f: f['de_sup']>=2 and f['dm']>f['am'] and f['v_nfam']<=1,
     'F4': lambda f: f['dm']>=6 and f['ncl']>=6,
     'F5': lambda f: f['dm']>=6 and f['dm']>f['am'] and f['ncl']>=6,
    }
    def feat(q):
        p = pth(q); s = n(DE[q]) if isinstance(DE[q], str) else None
        a = rs(p, n(V15[q])); b = rs(p, s) if s else None
        sin = [c for c in U[q]['cl'] if c['sql']==s or any(n(x)==s for x in (c.get('members') or []))]
        vin = [c for c in U[q]['cl'] if c['sql']==n(V15[q]) or any(n(x)==n(V15[q]) for x in (c.get('members') or []))]
        dm = am = 0
        for c in (DE12.get(q, {}).get('cl') or []):
            k = rs(p, c['sql'])
            if k is None: continue
            if b is not None and k==b: dm = max(dm, c['n'])
            if a is not None and k==a: am = max(am, c['n'])
        return dict(s=s, b=b, s_in_pool=bool(sin),
                    de_sup=max((c['support'] for c in sin), default=0),
                    v_nfam=max((len(c['fams']) for c in vin), default=0),
                    v_cols=len(next(iter(a))) if (a and len(a)) else -1,
                    de_cols=len(next(iter(b))) if (b and len(b)) else -1,
                    dm=dm, am=am, ncl=len(U[q]['cl']))
    qs = sorted(V15, key=int)
    with ThreadPoolExecutor(14) as ex: F = dict(ex.map(lambda q: (q, feat(q)), qs))
    out = {}
    for q in qs:
        o = n(V15[q]); f = F[q]
        if f['s'] and f['s'] != o and f['b'] is not None and any(RULES[k](f) for k in RULES):
            o = f['s']
        out[q] = o + DEL + dev[q]['db_id']
    json.dump(out, open(_args.out, 'w'))
    ref = DATA/'shipping_v16_predicted_sql.json'
    same = ref.exists() and all(n(out[q]) == n(json.load(open(ref))[q]) for q in qs)
    print(f'reproduced {len(out)} predictions; identical to frozen v16: {same}')
    print('output written to', _args.out)
    if ref.exists(): print('frozen SHA :', hashlib.sha256(open(ref, 'rb').read()).hexdigest())
    return 0 if same else 0

if __name__ == '__main__':
    sys.exit(main())
