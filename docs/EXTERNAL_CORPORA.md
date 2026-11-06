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

* **Own-pool-only arm** (P2-1 core): rebuilt from `v19` run candidates only (4/question, our
  Qwen3.8-27B generations; zero public corpora, zero Pool D/B), same protocol (fresh official
  evaluator, pinned current gold, 30 s): **1067 = 69.56%** (75.14/61.85/58.62).
  External-evidence contribution = 1146 − 1067 = **+79 questions (+5.15 pp)**, decomposed:
  nine public corpora inside Pool P ≈ +72 (1067 → 1139), upstream-B tie-break rules D/E/F = +7.
  Interpretation: the headline is dominated by our own generator (69.56 of 74.71), but the
  external corpora demonstrably carry Dev-set tuning we inherit as candidate diversity —
  the audit's generalization caveat stands, quantified.
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

## Negative evidence — AskData-modified corroboration (measured 2026-11-06)

Our locally-modified AskData pipeline (legacy_askdata_medium1534_20261031_v1, SHA `21b5cfd3…`;
AskData code + our Qwen3.8-27B GGUF, majority-vote + execution-feedback selection,
`reference_access:false`) is ALREADY a Pool P family. Fresh official numbers (pinned gold, serial):

| arm | EX | notes |
|---|---|---|
| modified AskData standalone | **1092 = 71.19%** (76.65/64.22/58.62) | below shipped 1146 |
| cross-tab vs v16 (per-question harness) | both 1062 · ours-only 82 · theirs-only 29 · neither 361 | union-oracle 1173 = +29 ceiling |
| pool reachability | 29/29 of their-only-correct answers already have result-equivalent clusters in Pool P | their generation adds NO new candidates — pure selection problem |
| G1 weak-champion→their-strong-cluster (17 switches, a-priori) | 1141 = 74.38% → **net −5** | negative |
| G2 empty-champion via their pick | fires 0 | already covered by rule A |
| G3 strict-majority (52 switches, a-priori) | 1127 = 73.47% → **net −19** | negative |

Conclusion: their *selection* output is not independent evidence (shares families/model lineage
with Pool P), so corroboration rules harvest mostly noise — joins the negative ledger
(majority −27, unanimity −15…−28, 3-system vote ≤0, referee −24…−29). We did NOT tune gates
against the 29 dev labels (that would re-create the F6-style indirect fitting the audit flagged).
Pool composition is not the bottleneck; selection precision is, and no observable AskData-mod
proxy correlates with correctness beyond what rules D/E/F already exploit.

## Positive measured evidence — Arctic-7B local generator, D2 count-zero rescue (2026-11-06)

Second internal-lineage generator: public Arctic-Text2SQL-R1-7B-final, local
GPU inference (transformers batch, chat-template + DDL prompt), reference-free.
Applied only to the observable count-zero dead zone (champion result [[0]] with
positive pool alternatives, E5 pool = 13 Dev questions), 8 samples/question.
Pre-registered rule: >=2/8 samples agreeing on a non-empty positive-count bloc
-> elect that bloc's earliest SQL. Train-domain validation first: fired 8,
gain 3, **damage 0** (D2 pool); a sibling D1 pool (gain 1, damage 1) was
rejected on that evidence. Dev result (official evaluator, same-session
paired control at identical load): v16 = 1146 control vs v16+D2 = **1147
(74.77%)** — one deterministic wrong->right switch (qid 765), one correctness
no-op (405, bloc result-equivalent to champion), one neutral wrong->wrong
(1441). Candidate artifact only (`candidate_v1147_arcticD2_pred.json`,
sha256 d779831b...); not shipped — kit upgrade and model-allowlist extension
await user decision. Generalization to a broader singleton-champion dead zone
was attempted and **declined for insufficient Train coverage** (only 9 such
questions; fired 3, all neutral) — pre-registration discipline: no
Train-endorsed rule, no shipping.
