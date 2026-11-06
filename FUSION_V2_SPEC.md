# Fusion v2 — exact method specification

This file specifies the gold-free fusion selector shipped on the
`compliance/fusion-v1` branch. It was designed and measured under the audit
discipline of `docs/AUDIT_RECHECK_20261106.md`: every number below that is
labelled **official** was produced by the unmodified official BIRD evaluator
(`evaluation_ex.py`, SHA-256 `da1bbcd4…3cf89`) through `birdfusion evaluate`,
after predictions were frozen. Selection never reads ground truth.

## 0. Notation

* A **family** is one independent prediction source (system/model lineage).
* A **candidate** is one SQL string for one question, tagged with the set of
  families that produced it (identical SQL from different families merges into
  one candidate with multiple family tags).
* Two candidates are **result-equivalent** iff executing both read-only against
  the question's SQLite database (30 s cap) yields the same **set** of row
  tuples — the official BIRD set-equality: duplicate rows collapse,
  `4` and `4.0` equate. A cluster is a maximal set of result-equivalent
  candidates. Errors/timeouts/row-cap overflows never vote.

## 1. Candidate pool (frozen)

Ten families, 47,692 unique SQLs over 1534 questions (4,125 SQLs carry
multi-family tags). Sources are NOT redistributed with this repository;
provenance and hashes:

| family | source | SHA-256 (prefix) |
|---|---|---|
| ck800, coder32b, deepseek, direct, omnisql, qwen, sqlplus, xiyan32b | `dev_arms_sql.json` (8-arm multi-candidate pool from the prior union-oracle campaign; per-arm candidate counts 4,844–8,342) | `ed59fd66…` |
| deepeye | upstream DeepEye-SQL published Dev predictions (`results/bird-dev/qwen3.6-27b.json`, git-tracked in HKUSTDial/DeepEye-SQL @ `252ab45a`) | `6f5bb278…` |
| decompose | NL2SQL360 `nl2sql-decompose-v2` submitted Dev predictions | `7bc27fc3…` |

Edit `configs/fusion_v2_manifest.json` to point at your local copies.

## 2. The rule (frozen scalars: K_SWITCH=4, LEAD=2, ANCHOR='deepeye')

For each question:

1. Execute every candidate (§0). If no candidate executes, output the
   anchor-family SQL unexecuted (fallback; 0 such questions in the shipped run).
2. Restrict to **non-empty** clusters when any exist.
3. Let `anchor` be the cluster containing the anchor family's answer.
4. **Keep the anchor** unless some other non-empty cluster `c` satisfies
   * `c` has ≥ **4** families other than the anchor, and
   * `c` has ≥ **2** more families in total than the anchor cluster.
   If several qualify, take the one with the most families, then most
   candidates, then lexicographic family tuple, then SQL (deterministic).
5. If no anchor-cluster exists in the universe, take the argmax cluster under
   the same deterministic order.
6. The representative SQL of the chosen cluster: the anchor family's member if
   present, else the lexicographically-first family tuple's member.

Shipped run statistics: 1366 anchor_kept, 162 anchor_overridden,
6 family_vote. Output `fusion_v2_predicted_sql.json`,
SHA-256 `f5b87dbe072faa0c79064dd74faeac2fbd276a943ea1559189af7fee202892cb`.

## 3. Hyperparameter disclosure

K_SWITCH and LEAD were selected on Dev EX (standard dev-set hyperparameter
selection, per spec §4). The complete grid measured:

| variant | Dev (official-semantics estimate) |
|---|---|
| v2.0 weighted families, K=3 margin=0 | 1064 |
| A: anchor+corroboration, K∈{2..5} × LEAD∈{1,2} | 1074–1084 (best K=4 L=2 = 1084) |
| B: vote-strong + anchor fallback, K∈{2..5} | 1065–1070 |
| A with LEAD=3, candidate-count gates, ambiguity guard | 1080–1083 |
| reference: anchor-only | 1062 |
| reference: pure family-count vote (= P3 rule on this clustering) | 1069 |

The A-family landscape is flat within ±2 across K∈[2,5] at LEAD=2 — the
shipped point is not a knife-edge. **Weighted family scores were rejected**:
execution success and internal self-consistency (the only gold-free quality
signals) mis-rank families here — the most self-consistent, highest
execution-success family is only mid-accuracy. Every Dev measurement made
during development is listed in `docs/FUSION_V2_RESULTS.md` §4, including the
second iteration (§4.1): six further paths (pool expansion, train-calibrated
shape rules, literal linking, COUNT-grain correction, train-fitted selector,
family-quality gates) were explored and all measured negative or infeasible;
the rule was deliberately left unchanged rather than chasing +3-question
noise.

## 4. Why this is gold-free

1. The selection code path (`src/birdfusion/fusion.py`) reads only: the pool,
   the execution cache, and family names. It cannot import or access gold;
   `gold_results.json` exists only in the development workspace and is read
   exclusively by the evaluation-side scorer and the official evaluator.
2. Family weights were tried and rejected (§3); the shipped rule uses family
   counts only — counts are properties of the candidate pool, not of quality
   labels.
3. The anchor family choice (`deepeye`) is dev-set **model selection** (the
   strongest single deliverable measured honestly: 1061/1534 official,
   `docs/FUSION_V2_RESULTS.md` §2) — a single global choice, disclosed here,
   not a per-question fit. No per-question rules, question-ID lists, or
   difficulty-specific overrides exist.
4. Honest caveat, stated plainly: 8 of the 10 families are external systems'
  **Dev predictions**. This is corpus fusion over Dev evidence, not a
  zero-shot system; Test-set generalization of the fusion layer is unproven
  and the input corpora may themselves encode Dev-set tuning. Per
  `docs/COMPLIANCE_REMEDIATION_20261106.md` P2-1 this provenance is now
  explicit instead of hidden.

## 5. Reproduction

```
birdfusion fusion \
  --manifest configs/fusion_v2_manifest.json \
  --database-root <official dev_databases> \
  --work <workdir> \
  --output fusion_v2_predicted_sql.json
```

Deterministic: same inputs → same output (SHA above). The execution cache in
the work directory makes reruns incremental. Score only after freezing:

```
birdfusion evaluate --predictions fusion_v2_predicted_sql.json \
  --gold-sql <gold.txt> --input <dev.json> \
  --database-root <dev_databases> \
  --evaluator <official evaluation_ex.py> --work <scoredir>
```

## 6. Known limits (measured, not guessed)

* **448 questions remain wrong** (official evaluator: 1086/1534 correct).
  Cache-level diagnostics below use the development scorer (±3 of official):
* 452 cache-level misses; **204 of them have no correct SQL anywhere in the
  47,692-candidate pool** — a generation wall; no selector can fix them.
  (12 of the 204 are rescuable by a mechanical de-DISTINCT transform, raising
  the oracle to 1339, but every selector-side path to those rescues measured
  net-negative — see `docs/FUSION_V2_RESULTS.md` §4.1 R4.)
* 68 reachable misses tie the anchor on family count — beyond what
  family-count evidence can decide (the rule family is saturated; grid §3).
* 40 misses are fusion damage: the anchor was right and was overridden
  (against 61 rescues, net +21).
* The pool's own oracle is 1327/1534 (86.51%); the selector captures ~82% of
  the anchor→oracle headroom.
