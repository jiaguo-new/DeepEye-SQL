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
import json, hashlib, os, sqlite3, sys
from pathlib import Path
from func_timeout import func_timeout
from concurrent.futures import ThreadPoolExecutor
BASE = Path(__file__).resolve().parent
DEV = Path(os.environ.get('BIRDFUSION_DEV_ROOT', '~/bird_dev_check/dev_20240627')).expanduser()
QWEN = Path(os.environ.get('BIRDFUSION_DEEPEYE_PRED', '~/bird_dev_check/qwen3.6-27b.json')).expanduser()
DEL = '\t----- bird -----'
REQUIRED_ARTIFACTS = [
    'shipping_v15_predicted_sql.json',
    'unified_pool.json',
    'DE12_POOL.json',
    'shipping_v16_predicted_sql.json',
]
_missing = [a for a in REQUIRED_ARTIFACTS if not (BASE / a).exists()]
if _missing:
    sys.exit(
        'missing frozen artifacts (see docs/COMPLIANCE_REMEDIATION_20261106.md P1-2):\n  '
        + '\n  '.join(_missing)
        + '\nThese pools are not distributed in this repository; obtain them from the '
          'release attachments and place them next to this script before re-running.'
    )
if not (DEV / 'dev.json').exists():
    sys.exit(
        f'BIRD dev data not found at {DEV}.\n'
        'Set BIRDFUSION_DEV_ROOT to a directory containing dev.json and '
        'dev_databases/<db_id>/<db_id>.sqlite from the official BIRD release.'
    )
if not QWEN.exists():
    sys.exit(
        f'DeepEye final SQL not found at {QWEN}.\n'
        'Set BIRDFUSION_DEEPEYE_PRED to the DeepEye-SQL pipeline final predictions JSON.'
    )
DEL = '\t----- bird -----'
def n(s): return ' '.join(s.split(DEL)[0].split())
dev = {str(d['question_id']): d for d in json.load(open(DEV/'dev.json'))}
def pth(q): d = dev[q]; return str(DEV/'dev_databases'/d['db_id']/(d['db_id']+'.sqlite'))
def rs(p, sql):
    def run():
        con = sqlite3.connect(f'file:{p}?mode=ro', uri=True, timeout=20); con.execute('PRAGMA query_only=ON')
        r = con.execute(sql).fetchall(); con.close(); return frozenset(tuple(x) for x in r)
    try: return func_timeout(25, run)
    except Exception: return None

def main():
    V15 = json.load(open(BASE/'shipping_v15_predicted_sql.json'))
    U = json.load(open(BASE/'unified_pool.json'))
    DE12 = json.load(open(BASE/'DE12_POOL.json'))
    DE = json.load(open(QWEN))
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
    p = BASE/'shipping_v16_predicted_sql.json'
    ref = json.load(open(p))
    same = all(n(out[q]) == n(ref[q]) for q in qs)
    print(f'reproduced {len(out)} predictions; identical to frozen v16: {same}')
    print('frozen SHA :', hashlib.sha256(open(p, 'rb').read()).hexdigest())
    return 0 if same else 1

if __name__ == '__main__':
    sys.exit(main())
