# Innovation Search Ledger — post-1146 (2026-11-06, rounds 27–41)

Complete accounting of every measured route tried after the shipped **v16 = 1146 / 74.71%**.
All selection decisions are gold-free; Dev gold appears only in post-hoc measurement blocks.
Official numbers below come from the official BIRD evaluator (serial runs, one instance).

## Scoreboard of record
| system | official | note |
|---|---|---|
| shipping v16 (kit `submission_v1146`, sha f9f3322b) | **1146 / 74.71%** | shipped, byte-reproducible, audit-confirmed 4x |
| v16 + Arctic **D2** count-zero rescue | **1147 / 74.77%** | sole positive delta; candidate pending user decisions ①② |
| v1148 (N1 corrected) | 1147 (no gain over D2) | line retired |

## Positive axis (1)
- **D2 / Arctic count-zero rescue** — pre-registered, TRAIN-validated (fired 8 / gain 3 / damage 0);
  Dev fired 3: q405 no-op, q765 gain, q1441 neutral. Officially +1 deterministic. Artifact:
  `candidate_v1147_arcticD2_pred.json` (sha d779831b…). Requires user ① (Arctic model allowlist)
  and ② (kit upgrade 1146→1147) before shipping.

## Closed negative axes (measured, each with its own pre-registered gate)
1. **deterministic mutation** — oracle ceiling +4, unselectable without gold.
2. **literal-repair** — 0/248 gate targets reachable.
3. **literal grounding** — damage-bound (13 > rescue 4).
4. **external corpora gates M1–M3** — −10 / −9 / −9.
5. **coverage rules (incl. ext-corpora AskData-modified)** — historically ≤0; re-measured −5/−19.
6. **structural templates fkjoin/decompose pilot** — 0/208 candidates hit gold; Clopper–Pearson
   95% upper 1.44% makes the ≥12/248 gate unreachable; stopped early per protocol.
7. **champion-latency swap** — 0 slow reps (p95 = 0.12 s).
8. **div10 semantic zone** — only 4 golds inside.
9. **NULL-mirror N1** — corrected build officially 1147 = no incremental gain (line retired).
10. **O1 collapse-twin gate** — 0 targets.
11. **train feature-discriminability audit** — 21 pairs max, nothing significant; plus the
    **structural non-transferability finding**: Train mirror pool stage-1 champ already 94 %
    correct (359/380) vs Dev losing 131/141 headroom pairs — Train-validated selection rules
    cannot transfer, explaining the selector/meta-rule/ext-corpora family of failures.
12. **evidence-formula compile-compliance** (new 2026-11-06): only 2/45 Train formula evidences
    compile to independently executable scalar queries (BIRD formulas are column expressions
    without FROM context); degraded token-coverage variant already closed at ≤0. Zero Dev exposure.
13. **Arctic CPU-localized generation probe (T-line)** (new 2026-11-06): Arctic-7B Q4_K_S on a
    zero-GPU llama-server, exact D2 prompt shape, 20 deterministic Train pool-miss questions ×
    8 samples = 160/160 generated. **1/16 measured hit vs frozen gate ≥2/20** (4 gold-exec failures
    excluded and disclosed: 6318/6413/6525/6665). 13/16 questions show ≥2-sample confident blocs
    with 0 gold blocs — the third model family is also *confidently wrong* exactly where our pool
    misses. Dev expansion was never proposed (gate not met), so no GPU spend occurred.
14. **semantic-perturbation (paraphrase) axis** (new 2026-11-06): STEP1 gates frozen before any
    result read passed (HEAD 9/29 FLIP_CORRECT ≥6; CTRL 22/25 all-AGREE ≥20/30 strict form) —
    the first selection-side evidence axis to survive a pre-registered gate. STEP2 (form frozen
    before measuring): v16 D/E/F chain replayed verbatim with the para 2/2-consensus rep in the
    auxiliary slot; 32/58 consensus coverage (9 jobs lost to a gateway 503 burst, disclosed);
    the chain fired on exactly one question (q1511 False→True), **net +1 < +3 official-rescore
    gate → closed with numbers**. Diagnosis: DeepEye-shaped thresholds (v_nfam≤1, dm≥6…) catch
    1/9 of raw flips — gate *shape*, not evidence, is the binding constraint. A para-native gate
    (11b) requires TRAIN-first validation, which needs the paraphraser model (GPU-locked) and
    ~900 gateway calls (7.5× the standing cost cap) → blocked on user decisions.

15. **answer-shape assertion** (new): flippable col-mismatch ceiling 19/139; regex fire 2 wrong / 1
    correct (net +1); LLM-asserted variant EV-negative by census arithmetic. Closed offline, ~4 min.
16. **result-set strict inclusion** (new): full-Dev census (10-way parallel, decision side
    gold-free): 113 fires; damage-side 65 exceeds rescue ceiling 21; direction near-symmetric
    (29/19, 11/10) with no free discriminator. Closed by census, zero submissions.
17. **D3 all-NULL rescue** (new): TRAIN mirror footprint 8 (7 wrong + 1 gold-is-NULL); pre-registered
    rule (bloc>=2 non-NULL, all-NULL>=3 protection clause) measured gains=0 / damage=1 -> TRAIN gate
    FAIL -> closed WITHOUT Dev application. The frozen gate demonstrably intercepted a rule that
    would have been net-negative on Dev.

## Test-time procedure accounting (round 46, measured, gold never in selection)
The shipped 74.71% figure is a Dev corpus-augmented measurement: five Pool-P families
(hermes/t75v4/ds/t75/bird_dev_check) and the D/E/F reference SQL (upstream published Dev file)
do not exist for hidden Test questions. Rebuilding the procedure from generatable components only
and measuring on Dev as a Test-time proxy: generatable-pool oracle 1233 (80.38%); max-support
champion 1091 (71.12%); champion + frozen D/E/F **1109 (72.29%)** - the frozen rule chain nets
**+18 on the degraded pool**, first measured transferability evidence for the rules-only
leaderboard route. A test-FAITHFUL re-measurement (own DeepEye-12 majority rep instead of the
upstream published file, which cannot exist at Test time) gives **1096/71.45%** - the band is now
anchored by both bounds: faithful lower 71.45, published-rep upper 72.29 (unavailable by
construction at Test), with v10 override (~+13, test-generatable) still omitted. D2 transfer check:
all three fired questions (q405/765/1441) keep the count-zero trigger under the generatable pool,
so the +1 from decision (2) transfers unchanged. Two disclosed two-sided approximations: the proxy stage-1 omits the v10 TRAIN
override (~+13), and the D/E/F rep slot uses the published Dev file rather than our own pipeline
consensus (the generatable variant at Test time). Leaderboard expectation therefore anchors the
70-73% band; the D2 rule (+1, pure generatable) transfers unchanged if adopted.

## Headroom accounting (unchanged, gold-free)
- Pool oracle 1284; **131/141 headroom pairs already lost at stage-1 scoring**, only 10 lost in
  the rule chain (every shipped rule net-positive overall). 132 wrong questions have a pool
  alternative == gold (official-semantics flippable scan) — the selector, not the comparator,
  is the bottleneck; and per axis 11 no transferable selection signal was found.

## Adjudication state (user-side, as of 19:2x)
- ① Arctic model allowlist; ② kit 1146→1147 (D2) decision; ③ GPU release decision —
  still pending. The shipped kit is untouched (`submission_v1146`, f9f3322b).
- No experiment is currently running from this search; all affordable in-house axes are exhausted
  with measured numbers above.

## Methods discipline (this ledger's own guarantee)
Every claim here is written *after* the run finished, with the run directory and gate frozen
before the first number was seen; two gate-form ambiguities (paraphrase gate unit, v1148 exec-NULL
pick) were corrected by re-running, never by re-tuning against labels. Where a measurement was
partially shown while running, it is marked "partial" and never used as a verdict.
