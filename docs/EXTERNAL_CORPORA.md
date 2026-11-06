# External Dev-prediction corpora registry (remediation P2-1, partial)

Every externally-published Dev prediction corpus consumed by the shipped selector,
with provenance. These are *public research artifacts* used as candidate evidence;
none carries gold labels into our selection (the selection rules read only
observable execution structure — `SELECTOR_V16_SPEC.md` §7 "never uses ground truth"
is about *our* labels; external files may encode their authors' own Dev tuning).

| corpus | where used | provenance / tuning risk |
|---|---|---|
| nine public/historical BIRD Dev prediction corpora (leaderboard submissions, released model outputs) | Pool P clusters (`data/unified_pool.goldfree.json`; gold-derived fields stripped) | each predates this work; several are known to be Dev-tuned; treated as candidate evidence only |
| HKUSTDial/DeepEye-SQL `results/bird-dev/qwen3.6-27b.json` (SHA `0d073da4…`, git-tracked @ `252ab45a`) | rule B (final SQL) for D/E/F tie-breaks | upstream's official Dev release; upstream reports 74.5; locally rescored 74.38 on current official gold |

**Known tension** (audited 2026-11-06, F6/§2.2-§4): using others' Dev predictions is
*equivalent to inheriting their Dev-set tuning*. Our earlier COMPLIANCE.md line
banning "historical prediction ledgers" was inconsistent with the implementation.
Resolution adopted: honest disclosure (this file + README), and a measured ablation
(Pool-P-own-only vs full pools) scheduled once the audit's P0-1 freeze is lifted;
its result will be posted here. Risk to independent-Test generalization is stated in
README "Honest limits".

## Measured slices (2026-11-06, post-freeze-lift)

* **Pool-D / upstream-B rule-chain marginal value** — fresh official rescore of the
  immediate pre-D/E/F baseline `shipping_v15` (d4d14758): **1139 = 74.32%**
  (78.92/68.75/62.76) vs v16 1146 = 74.71%. Net contribution of the three
  DeepEye-evidence rules (91 switches, D+E+F): **+7 questions** — i.e. the external
  artifacts move ≤0.46 pp on Dev; the bulk of the headline is our own pools +
  gold-free consensus/repair rules. Rescored 2026-11-06 13:4x, current pinned gold.

## Ablation plan (remaining)
1. Regenerate selector decisions with Pool P restricted to our own v19 generations
   (drop the 9 public corpora + B): expected Dev drop, unmeasured.
2. Same on the Test-holdout flow (question IDs unknown to us; gold never used).
3. Report both deltas here + in the BIRD submission email's method summary.
