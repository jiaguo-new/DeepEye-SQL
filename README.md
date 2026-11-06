# BirdFusion — BIRD Text-to-SQL, publisher Dev 20240627

> **Result status (2026-11-06, per docs/AUDIT_RECHECK_20261106.md):** the
> historical table below could not be reproduced with the official evaluator on
> the official Dev gold; the v16 shipping artifacts are not distributed and its
> 74.71% is **unverified**. The current **verified** results on this branch are:
>
> * **Fusion v2 (gold-free anchor+corroboration selector): 1086/1534 = 70.80%**
>   (official evaluator; beats the strongest single input corpus at 1061/69.17%
>   and the prior fusion layer at 1028/67.01%) — `FUSION_V2_SPEC.md`,
>   `docs/FUSION_V2_RESULTS.md`, `fusion_v2_predicted_sql.json`.
> * public v1 pipeline: 1016/1534 = 66.23% (reproduces exactly).

End-to-end, gold-free text-to-SQL system: two independent candidate pools, exact
result-set clustering over read-only SQLite execution, and a frozen evidence-based
selector chain. Every number is measured locally with the official BIRD evaluator
(set-equality, 30 s meta-timeout, UNKNOWN kept in the denominator); we inherit no
published scores from any pipeline.

| Configuration | Dev 1534 official EX | status |
|---|---|---|
| Pool P generation alone (Qwen3.8-27B, 4 candidates/question, zero-shot) | 1063 = 69.30% | **unverified** (official rescore: 992 = 64.67%) |
| Upstream **DeepEye-SQL run** (adapted backends only; see docs/DEEPEYE_ADAPTATIONS.md) | 1143 = 74.51% | **unverified** (the artifact is the upstream published file; official rescore: 1061 = 69.17%) |
| **Shipped: BirdFusion pools + frozen gold-free selector v16** | **1146 = 74.71%** (simple 79.46 / moderate 68.97 / challenging 62.76) | **unverified** (artifacts not distributed) |
| **Fusion v2 (this branch)** | **1086 = 70.80%** (simple 76.32 / moderate 64.30 / challenging 56.25) | **verified** (official evaluator, reproducible) |

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
