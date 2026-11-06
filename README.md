# BirdFusion — BIRD Text-to-SQL, publisher Dev 20240627: **1146/1534 = 74.71%** (official EX)

End-to-end, gold-free text-to-SQL system: two independent candidate pools, exact
result-set clustering over read-only SQLite execution, and a frozen evidence-based
selector chain. Every number is measured locally with the official BIRD evaluator
(set-equality, 30 s meta-timeout, UNKNOWN kept in the denominator) against the
current publisher gold (pinned SHAs in `scoring/`). We do not reuse any paper's
score; note however that candidate evidence *includes* other teams' published Dev
predictions (nine public corpora in Pool P; the upstream-published DeepEye file as
rule B) — see `docs/EXTERNAL_CORPORA.md` and `docs/AUDIT_RESPONSE_20261106.md`.

| Configuration | Dev 1534 official EX |
|---|---|
| Pool P generation alone (Qwen3.8-27B, 4 candidates/question, zero-shot; v19 final) | 1063 = 69.30% — **verified** (fresh official rescore 2026-11-06; audit's 992 traced to a pre-2024-06-27 gold, see response doc) |
| Upstream **DeepEye-SQL published predictions** (git-tracked artifact @252ab45a, SHA `0d073da4`; *not* a local rerun of their selector — see docs/DEEPEYE_ADAPTATIONS.md) | 1141 = 74.38% — **verified** (fresh rescore on current official gold; paper claims 74.5) |
| Pool-D evidence rules (D+E+F) marginal value over v15 (fresh, current gold) | 1139 → 1146: **+7 net** (74.32% → 74.71%) |
| **Shipped: BirdFusion pools + frozen gold-free selector v16** | **1146 = 74.71%** (simple 79.46 / moderate 68.97 / challenging 62.76) — **verified 4x fresh + byte-reproducible from `data/`** |

## How it works

```
BIRD public inputs ──► Pool P: our generator (4 templates/candidates)
(question, db,       + nine public/historical prediction corpora
 evidence, schema)         │ read-only execute, cluster by exact result set
                           │ (timeouts/errors = UNKNOWN, never vote)
                    Pool D: upstream DeepEye-SQL pipeline (12 samples/question,
                            final SQL + observable cluster evidence)
                           │
             frozen selector chain v16 (SELECTOR_V16_SPEC.md):
               stage-1 consensus + TRAIN-only preference override (266)
               → empty-result repair (10) → fragmentation/split-vote (32)
               → DeepEye corroboration & tie-breaks D+E+F (91)
                           ▼
                 one final SQL per question
```

Selection never uses ground truth: each rule switches an answer only to a member of a
corroborated, gold-free corpus cluster, using observable execution evidence (support
counts, family spread, row/column/cluster shapes). `reproduce_shipping_v16.py`
re-derives the shipped predictions byte-for-byte (SHA-256 `510c9104…2061c3`).

## Repository layout

```
src/birdfusion/          inference pipeline (pure stdlib, Python >= 3.11)
configs/                 48 frozen experiment configs (sanitized)
tests/                   111 unit tests (unittest; sqlglot-dependent audit tests auto-skip)
SELECTOR_V16_SPEC.md     exact rule chain, thresholds, gates, self-checks
reproduce_shipping_v16.py byte-reproducible selector over frozen inputs
COMPLIANCE.md            gold-free audit gates G1-G3 (0 violations)
docs/DEEPEYE_ADAPTATIONS.md  exact upstream commit + the 6 plumbing-only adaptations
docs/README_SUBMISSION_NOTES.md  internal submission notes (archived)
LICENSE / NOTICE         MIT; full third-party provenance
```

## Reproduction

1. **Selector chain** (deterministic, no model calls): place the frozen pools described
   in `SELECTOR_V16_SPEC.md` §1 and run `reproduce_shipping_v16.py`.
2. **Full pipeline from scratch**: BIRD databases/gold are not redistributed (see NOTICE) —
   obtain them from the official BIRD release; then run generation with any
   `configs/public_v19_*` style config (gateway base_url/host_header are placeholders
   for your own OpenAI-compatible endpoint), execute+cluster with `birdfusion.sql_exec`,
   and apply the v16 chain. Pool D requires cloning upstream DeepEye-SQL at the pinned
   commit and applying the documented adaptations.
3. **Scoring**: official BIRD `evaluation_ex.py` (see `requirements.txt`).

## Compliance statements

* No Dev/Test gold at generation or selection time; gold is read only by the official
  evaluator after predictions freeze (splits and audits in `COMPLIANCE.md`).
* The stage-2 preference selector is trained exclusively on BIRD TRAIN (disjoint DBs).
* DeepEye-SQL is used as published (algorithm untouched; provenance in NOTICE); the
  submission category is "Combined Models".
* Honest limits: 75%+ is unreachable within this frozen model set and rule chain
  (extensive negative-result search archived in the playbook of the submission; the
  remaining headroom is a model-capability wall).

Submit questions: open an issue.
