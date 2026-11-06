"""ARCHIVED NEGATIVE-RESULT PROTOTYPE (2026-11-06) - do not wire into the pipeline.

EGS v1: Evidence-Grounded SQL Synthesizer - deterministic, gold-free.

Measured on BIRD Dev (docs/FUSION_V2_RESULTS.md section 4.2): 1,194
synthesized SQLs, 18 questions with a gold-matching variant, 0/204
generation-wall rescues, 3 correct-cluster corroborations against 70
wrong-cluster corroborations. Net effect on the frozen selector: zero.


Key mechanism: VALUE-SET GROUNDING. For every TEXT column with low
cardinality we cache its DISTINCT value set (public database content, read
once per DB) and match the question's n-grams directly against those values.
This grounds multi-word values ('Santa Clara'), lowercase status words
('active', 'closed'), and type phrases ('locally funded', 'Community Post
Office') to the exact column that contains them — no name guessing.

Secondary mechanisms: evidence "refers to" triples; description-token column
matching (name + column_description + value_description); comparative-context
numeric predicates; FK-graph join paths; bounded template enumeration.
"""
import re
import sqlite3
import json
from collections import defaultdict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from func_timeout import func_timeout

DB_ROOT = Path('D:/text2sql/birdfusion/recheck_20261106/db_extract/dev_databases')
MAX_SQLS_PER_Q = 48
VALUE_CARD_LIMIT = 400

STOP = set('''a an the of in on at to for and or with without by from as is are was were be been been
which what who whose when where how many much list state name please provide give me show find
there their its it this that these those all any each every some most least more than less fewer
number count total average mean sum ratio percentage difference between among'''.split())

CUE_COUNT = re.compile(r'\bhow\s+many\b|\bnumber\s+of\b|\bhow\s+much\b|\bno\.?\s+of\b', re.I)
CUE_AVG = re.compile(r'\baverage\b|\bmean\b', re.I)
CUE_MAX = re.compile(r'\bmost\b|\bmaximum\b|\bhighest\b|\blargest\b|\bgreatest\b|\btop\b', re.I)
CUE_MIN = re.compile(r'\bleast\b|\bminimum\b|\blowest\b|\bsmallest\b', re.I)
CUE_LIST = re.compile(r'\blist\b|\bwhich\b|\bwhat\b|\bname\b|\bstate\b|\bprovide\b|\bshow\b', re.I)
CUE_GROUP = re.compile(r'\bper\b|\beach\b|\bin\s+each\b|\bfor\s+each\b', re.I)
COMP_NUM = re.compile(
    r'\b(more\s+than|greater\s+than|above|over|exceed(?:ed|s)?|at\s+least)\s+(\d[\d,.]*)\b|'
    r'\b(less\s+than|fewer\s+than|under|below)\s+(\d[\d,.]*)\b', re.I)
REFERS = re.compile(
    r"([^,;.]{3,60}?)\s+refers\s+to\s+[`\"\[\]\w .-]{0,40}?\s*"
    r"(=|>=|<=|>|<)\s*('([^']*)'|\"([^\"]*)\"|([\w.]+))", re.I)


def toks(s):
    return [t for t in re.split(r'[^A-Za-z0-9]+', (s or '').lower()) if t and t not in STOP and len(t) > 2]


def col_key_tokens(name, desc, vdesc):
    parts = (re.split(r'[_\s]+', (name or '').lower())
             + re.split(r'[_\s]+', (desc or '').lower())
             + re.split(r'[_\s]+', (vdesc or '').lower()))
    return {p for p in parts if len(p) > 2 and not p.isdigit()}


class Schema:
    def __init__(self, db_id):
        self.db_id = db_id
        p = DB_ROOT / db_id / f'{db_id}.sqlite'
        con = sqlite3.connect(f'file:{p}?mode=ro', uri=True, timeout=25)
        self.tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        self.cols = {t: [(r[1], r[2]) for r in con.execute(f'PRAGMA table_info("{t}")')]
                     for t in self.tables}
        self.fks = []
        for t in self.tables:
            for fk in con.execute(f'PRAGMA foreign_key_list("{t}")'):
                self.fks.append((t, fk[3], fk[2], fk[4]))
        con.close()
        self.desc, self.vdesc = {}, {}
        ddir = DB_ROOT / db_id / 'database_description'
        if ddir.exists():
            for csv in ddir.glob('*.csv'):
                table = csv.stem
                try:
                    lines = csv.read_text(encoding='utf-8-sig').splitlines()
                except Exception:
                    continue
                for ln in lines[1:]:
                    parts = ln.split(',')
                    if len(parts) >= 3:
                        self.desc[(table, parts[0].strip())] = parts[2].strip()
                        if len(parts) >= 5:
                            self.vdesc[(table, parts[0].strip())] = parts[4].strip()
        self.token_index = defaultdict(list)
        for t in self.tables:
            for c, _ty in self.cols[t]:
                for tk in col_key_tokens(c, self.desc.get((t, c), ''), self.vdesc.get((t, c), '')):
                    self.token_index[tk].append((t, c))

    # ---- value-set index (built lazily per DB, shared across questions) ----
    def value_index(self):
        if hasattr(self, '_vi'):
            return self._vi
        vi = {}  # (table, col) -> {lower_value: original_value}
        p = DB_ROOT / self.db_id / f'{self.db_id}.sqlite'
        con = sqlite3.connect(f'file:{p}?mode=ro', uri=True, timeout=60)
        for t in self.tables:
            for c, ty in self.cols[t]:
                if ty is None or ty.upper() not in ('TEXT', '', 'VARCHAR', 'CHAR'):
                    continue
                try:
                    vals = [r[0] for r in con.execute(
                        f'SELECT DISTINCT "{c}" FROM "{t}" WHERE "{c}" IS NOT NULL LIMIT {VALUE_CARD_LIMIT*2}')]
                except Exception:
                    continue
                if len(vals) <= VALUE_CARD_LIMIT:
                    vi[(t, c)] = {str(v).strip().lower(): str(v).strip() for v in vals}
        con.close()
        self._vi = vi
        return vi

    def join_path(self, tables_needed):
        """BFS over the FK graph; returns the FULL edge list connecting all
        needed tables (including intermediate hops), or None."""
        if len(tables_needed) <= 1:
            return []
        adj = defaultdict(list)
        for a, ca, b, cb in self.fks:
            adj[a].append((b, ca, cb))
            adj[b].append((a, cb, ca))
        remaining = set(tables_needed)
        start = remaining.pop()
        visited = {start}
        result = []
        while remaining:
            # BFS from the visited component toward any remaining table,
            # recording a parent edge for every discovered node
            parent = {}
            queue = list(visited)
            seen = set(visited)
            found = None
            while queue and found is None:
                cur = queue.pop(0)
                for nxt, ca, cb in adj[cur]:
                    if nxt in seen:
                        continue
                    parent[nxt] = (cur, (cur, ca, nxt, cb))
                    if nxt in remaining:
                        found = nxt
                        break
                    seen.add(nxt)
                    queue.append(nxt)
            if found is None:
                return None
            chain = []
            node = found
            while node not in visited:
                prev, edge = parent[node]
                chain.append(edge)
                node = prev
            chain.reverse()
            result.extend(chain)
            for e in chain:
                visited.add(e[0])
                visited.add(e[2])
            remaining.discard(found)
        return result

    def from_clause(self, tables_needed):
        path = self.join_path(sorted(tables_needed))
        if path is None or path == []:
            main = sorted(tables_needed)[0] if tables_needed else None
            return (f'"{main}"', main) if main else (None, None)
        main = path[0][0]
        parts = [f'"{main}"']
        used = {main}
        for a, ca, b, cb in path:
            if b in used:
                continue
            parts.append(f'INNER JOIN "{b}" ON "{a}"."{ca}" = "{b}"."{cb}"')
            used.add(b)
        return ' '.join(parts), main


class Synthesizer:
    def __init__(self, db_id):
        self.schema = Schema(db_id)

    # ---------------------------------------------------------------- grounding
    def ground_via_values(self, question):
        """n-gram matching against low-cardinality column value sets."""
        s = self.schema
        vi = s.value_index()
        words = re.split(r'[^A-Za-z0-9]+', question.lower())
        words = [w for w in words if w]
        ql = ' ' + ' '.join(words) + ' '
        grams = set()
        for n in (1, 2, 3, 4):
            for i in range(len(words) - n + 1):
                grams.add(' '.join(words[i:i + n]))
        preds = []
        gram_hits = defaultdict(list)  # gram -> [(table, col, orig_value)]
        for gram in grams:
            if len(gram) < 4 or gram.replace(' ', '').isdigit():
                continue  # numbers are grounded via comparative context only
            for (t, c), vset in vi.items():
                if gram in vset:
                    gram_hits[gram].append((t, c, vset[gram]))
        # rank by column-token overlap with the words around the gram
        for gram, hits in sorted(gram_hits.items(), key=lambda kv: -len(kv[0]))[:8]:
            idx = ql.find(' ' + gram + ' ')
            ctx = set(toks(question[max(0, idx - 45):idx])) | set(toks(question[idx + len(gram):idx + len(gram) + 45]))
            def score(hit):
                ct = col_key_tokens(hit[1], s.desc.get((hit[0], hit[1]), ''),
                                    s.vdesc.get((hit[0], hit[1]), ''))
                return len(ctx & ct)
            for t, c, orig in sorted(hits, key=score, reverse=True)[:2]:
                preds.append((t, c, '=', orig))
        return preds

    def ground_via_evidence(self, evidence):
        preds = []
        for m in REFERS.finditer(evidence or ''):
            hint, op, _, sq1, sq2, raw = m.groups()
            val = (sq1 or sq2 or raw or '').strip()
            if not val:
                continue
            hint_toks = set(toks(hint))
            best, bs = None, 0
            for (t, c), vset in self.schema.value_index().items():
                if val.lower() in vset:
                    ct = col_key_tokens(c, self.schema.desc.get((t, c), ''),
                                        self.schema.vdesc.get((t, c), ''))
                    sc = len(hint_toks & ct)
                    if sc > bs:
                        best, bs = (t, c, vset[val.lower()]), sc
            if best:
                preds.append(best)
        return preds

    def numeric_predicates(self, question, evidence):
        preds = []
        text = (question or '') + ' ' + (evidence or '')
        for m in COMP_NUM.finditer(text):
            if m.group(1):
                op, val = ('>=' if 'at least' in m.group(1).lower() else '>'), m.group(2)
            else:
                op, val = '<=', m.group(4)
                op = '<'
            val = val.replace(',', '')
            ctx = toks(text[max(0, m.start() - 45):m.start()])[-4:]
            best, bs = None, 0
            for tk in ctx:
                for t, c in self.schema.token_index.get(tk, []):
                    ct = col_key_tokens(c, self.schema.desc.get((t, c), ''),
                                        self.schema.vdesc.get((t, c), ''))
                    sc = len(ctx & ct)
                    if sc > bs:
                        best, bs = (t, c, op, val), sc
            if best:
                preds.append(best)
        return preds

    def projection_column(self, question):
        s = self.schema
        q = question.lower()
        cands = []
        for m in re.finditer(r'\b(?:average|mean|sum|total|maximum|minimum|highest|lowest|'
                             r'largest|smallest|number|no\.?|amount|ratio)\s+(?:of\s+)?(?:the\s+)?'
                             r'((?:\w+\s+){0,3}\w+)', q):
            for tk in toks(m.group(1)):
                for t, c in s.token_index.get(tk, []):
                    cands.append((t, c))
            if cands:
                break
        if not cands:
            m = re.search(r'\b(?:how\s+many|what\s+is|what\s+are)\s+((?:\w+\s+){0,4}\w+)', q)
            if m:
                for tk in toks(m.group(1)):
                    for t, c in s.token_index.get(tk, []):
                        cands.append((t, c))
        # de-dup preserving order
        seen, out = set(), []
        for tc in cands:
            if tc not in seen:
                seen.add(tc)
                out.append(tc)
        return out

    # ---------------------------------------------------------------- templates
    def synthesize(self, question, evidence):
        s = self.schema
        preds = self.ground_via_values(question) + self.ground_via_evidence(evidence)
        preds += self.numeric_predicates(question, evidence)
        # dedup
        seen, pd = set(), []
        for p in preds:
            k = (p[0], p[1], p[2], str(p[3]))
            if k not in seen:
                seen.add(k)
                pd.append(p)
        if not pd:
            return []
        proj = self.projection_column(question)
        # restrict projection to predicate tables if join fails otherwise
        tables = {t for t, _, _, _ in pd} | {t for t, _ in proj[:2]}
        fc, main = s.from_clause(tables)
        if fc is None:
            tables = {t for t, _, _, _ in pd}
            fc, main = s.from_clause(tables)
            if fc is None:
                return []
            proj = [(t, c) for t, c in proj if t in tables]
        by_col = defaultdict(list)
        for t, c, op, v in pd:
            by_col[(t, c)].append((op, v))
        conds = []
        for (t, c), items in by_col.items():
            eqs = [v for op, v in items if op == '=']
            others = [(op, v) for op, v in items if op != '=']
            if len(eqs) > 1:
                conds.append('(' + ' OR '.join(f'"{t}"."{c}" = \'{v}\''.replace('"{t}"', f'"{t}"') for v in eqs) + ')')
            else:
                for op, v in items:
                    conds.append(f'"{t}"."{c}" {op} \'{v}\'')
        W = ' AND '.join(conds)
        sqls = set()
        qt = question.lower()
        proj_col = f'"{proj[0][0]}"."{proj[0][1]}"' if proj else '*'
        proj_cols = ', '.join(f'"{t}"."{c}"' for t, c in proj[:2]) if proj else '*'
        is_count = bool(CUE_COUNT.search(qt))
        is_avg = bool(CUE_AVG.search(qt))
        is_max = bool(CUE_MAX.search(qt))
        is_min = bool(CUE_MIN.search(qt))
        is_list = bool(CUE_LIST.search(qt))
        is_group = bool(CUE_GROUP.search(qt))
        fn = 'MAX' if is_max else 'MIN'
        d = 'DESC' if is_max else 'ASC'
        if is_count:
            sqls.add(f'SELECT COUNT(*) FROM {fc} WHERE {W}')
            if proj:
                sqls.add(f'SELECT COUNT({proj_col}) FROM {fc} WHERE {W}')
                sqls.add(f'SELECT COUNT(DISTINCT {proj_col}) FROM {fc} WHERE {W}')
                sqls.add(f'SELECT {proj_col} FROM {fc} WHERE {W}')
                if is_avg:
                    sqls.add(f'SELECT AVG({proj_col}) FROM {fc} WHERE {W}')
            if is_group and proj:
                sqls.add(f'SELECT {proj_col}, COUNT(*) FROM {fc} WHERE {W} GROUP BY {proj_col}')
        elif is_avg and proj:
            sqls.add(f'SELECT AVG({proj_col}) FROM {fc} WHERE {W}')
            sqls.add(f'SELECT {proj_col} FROM {fc} WHERE {W}')
        elif (is_max or is_min) and proj:
            sqls.add(f'SELECT {proj_col} FROM {fc} WHERE {W} ORDER BY {proj_col} {d} LIMIT 1')
            sqls.add(f'SELECT {fn}({proj_col}) FROM {fc} WHERE {W}')
            sqls.add(f'SELECT {proj_col}, COUNT(*) FROM {fc} WHERE {W} GROUP BY {proj_col} ORDER BY COUNT(*) {d} LIMIT 1')
        if is_list or not sqls:
            sqls.add(f'SELECT {proj_cols} FROM {fc} WHERE {W}')
            if (is_max or is_min) and proj:
                sqls.add(f'SELECT {proj_cols} FROM {fc} WHERE {W} ORDER BY {proj_col} {d} LIMIT 1')
        return list(sqls)[:MAX_SQLS_PER_Q]


_SYNTH_CACHE = {}

def synth_for_question(args):
    qid, db_id, question, evidence = args
    try:
        if db_id not in _SYNTH_CACHE:
            _SYNTH_CACHE[db_id] = Synthesizer(db_id)
        return qid, _SYNTH_CACHE[db_id].synthesize(question, evidence)
    except Exception:
        return qid, []


def main():
    dev = json.load(open('D:/text2sql/execution_20261020_official_original_dev_recheck_v510/terminal_snapshot/data/dev.json', encoding='utf-8'))
    jobs = [(str(d['question_id']), d['db_id'], d['question'], d.get('evidence') or '') for d in dev]
    out = {}
    with ThreadPoolExecutor(8) as ex:
        for qid, sqls in ex.map(synth_for_question, jobs):
            out[qid] = sqls
    n = sum(len(v) for v in out.values())
    print(f'questions with synth candidates: {sum(1 for v in out.values() if v)}/1534, total SQLs: {n}')
    json.dump(out, open('synth_candidates.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
