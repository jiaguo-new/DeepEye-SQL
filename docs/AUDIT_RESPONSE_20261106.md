# Audit response — recheck 2026-11-06 (P0 actions completed, findings dispositioned)

Dev-side response to `docs/AUDIT_RECHECK_20261106.md` / `docs/COMPLIANCE_REMEDIATION_20261106.md`
(branch `audit/20261106-compliance-recheck`). All recomputation below was done on the
scoring host with the **verbatim official evaluator, SHA `da1bbcd4530be83692d7c650c814ea9704bb710d0c953eb75d02ccb38233cf89`
— byte-identical to the audit's copy** — `--meta_time_out 30`, fresh executions, no
result caches, no label inheritance, `--num_cpus 12`, logs at `/tmp/p01/rescore.log`
(2026-11-06 10:56–10:58) and `final_official_rescore_v2.log` (00:22).

**Experiment freeze honored**: no new method experiments ran on 2026-11-06; everything
in this branch is measurement, governance, and disclosure correction.

## 1. P0-1 results (fresh official rescores on current publisher gold)

Gold provenance pinned first — this turned out to be the root cause of the disagreement:

| input | SHA-256 | how pinned |
|---|---|---|
| publisher `dev.zip` (live OSS link, **downloaded fresh 2026-11-06**) | `cdd6d19faeb45a23970b98d3ef6c40a87987c95459c2cf12076897a60cf5a630` | byte-identical to the 2026-11-02 download that built our entire chain |
| `dev_20240627/dev.json` | `630272f2b1c44d8cef2c3b246f623355cf0bbc1e832c81061df895530dfc2f06` | from the zip above |
| `dev_20240627/dev.sql` | `42087d70ed96496b2746a7453e530155076564df127acd1e87d5fa710b6a3805` | from the zip above |
| `dev_20240627/dev_databases.zip` | `8132aacfe61ecec7198a62e93cb8512dac89b1d6b9922fe3c7bb94b13f317d8f` | from the zip; **identical to the audit's §2 value** ✓ |
| difficulty counts | 925 / **464** / **145** | current official file |

| artifact | claimed | **fresh official rescore (this response)** | audit's number |
|---|---|---|---|
| public v1 `predicted_sql.json` (`0dede3dd…`) | 1016 (66.23%) | **1000 (65.19%)**, 72.00/55.60/52.41 | 1016 |
| v19 pool `predicted_sql.json` (`aae94afe…`, SHA **identical to audit's**) | 1063 (69.30%) | **1063 (69.30%)**, 74.49/62.07/59.31 | 992 |
| upstream DeepEye published file **used by our chain** (`0d073da4…`, == git blob @252ab45a, verified via `git show`) | 1143 (74.51%) *@240s* | **1141 (74.38%)**, 79.89/68.10/59.31 | 1061 (of a *different file*, see §2.2) |
| **shipping v16** (`510c9104…`) + two-tab export (`f9f3322b…`) | 1146 (74.71%) | **1146 (74.71%)**, 79.46/68.97/62.71→62.76 (verified 4× fresh on 2026-11-05/06) | "file not found" |

Per-question detail (v19, fresh, timing-captured): `/tmp/p01/perq_v19.json` (qid → verdict,
gold-latency, pred-latency). On this gold: 1062/1063 (one 30 s-boundary flip under the
sequential harness variant).

## 2. Root cause of the 93-question disagreement: the audit scored against a PRE-2024-06-27 dev snapshot

Evidence chain:

1. **Same evaluator** (da1bbcd4 ✓), **same databases** (8132aacd ✓, and qid-5 pred returns
   `4` on both machines), **same v19 file** (aae94afe ✓) — the only divergent input is the
   audit's `dev.json` (`9b4c38da…`), which is **not** the content of the current publisher
   `dev.zip` (`630272f2…`, re-verified by fresh download today).
2. **Fingerprint of the older annotation set**: the audit's report quotes qid 5 as
   "Math **under** 400" with gold→0; the publisher's current file reads "Math **greater
   than** 400" with gold→`4` (equal to pred → correct). The audit's control match on
   public v1 (1016) is itself evidence of the older snapshot: the October record's own
   layer counts are 925/**465**/**144** (see `evaluation_summary.json` above), while the
   current official file is 925/**464**/**145** (qid 831 was recategorized in the
   2024-06-27 correction pass — the publisher's notice "we corrected annotations and
   replaced selected instances" on bird-bench.github.io refers to it).
3. **The 82 "overcounted" qids are not overcounted on current gold**: probed individually
   with fresh read-only executions — **82/82 correct, zero failures, zero queries >3 s**
   (so neither caching nor the 30 s budget explains them; the audit's own sampled "semantics"
   examples were misread against the old gold: qid 5 pred=gold=`(4,)`; qid 92 pred=gold=`(69,)`).
   The 11 "undercounted" qids: 11/11 wrong on current gold. Both directions mirror the
   annotation revision — exactly what a gold-snapshot mismatch predicts.

We therefore **cannot reproduce 992/1061 on the current official gold, and hold that the
outward-facing numbers must be pinned to it** (pinned above + `scoring/README.md`).
Request to the audit team: re-run §2 against the current publisher zip (bytes above), and
state the provenance of `9b4c38da…` (download URL/date); if it is a deliberately newer
revision than the live zip, show the publisher source — today's live-zip download says otherwise.

## 3. Disposition of findings F1–F6

* **F1 (reproduce script non-runnable): accepted and fixed.** The `Path('~/…')` bug was
  introduced by our public-path sanitization (the internal original used absolute paths
  and ran all week). `reproduce_shipping_v16.py` now takes `--dev-root/--deepeye-b/--out`,
  SHA-pins every input, and runs from repo contents. **Verified end-to-end 2026-11-06:
  byte-identical reproduction of the frozen `510c9104…` predictions** (rule logic untouched;
  `except Exception → except BaseException` hardening only — never fires on fast caches).
* **F3 (DeepEye provenance): accepted in full.** Rule B *is* the upstream-published,
  git-tracked file (blob-identity verified via `git show HEAD:results/…` == `0d073da4`).
  README/NOTICE/SPEC/DEEPEYE_ADAPTATIONS rewritten accordingly; the "we inherit no published
  scores from any pipeline" line corrected (we inherit no *scores*, but we DO consume
  external Dev *predictions* — now disclosed, registry in `docs/EXTERNAL_CORPORA.md`).
  Corrected headline for that artifact: **1141 = 74.38%** (fresh, 30 s, current gold).
  Clarified naming inconsistency: our locally executed adapted-pipeline runs (Nov-2026,
  token-ledgered: 124,416,106 prompt / 51,528,651 completion) produced the **Pool D
  candidate draws**; the upstream **final** predictions file is a separate artifact. The
  October `BASELINE_DECISION.json` (`complete_pipeline_deployed: false`) predates those
  November runs; on the audit's machine the DE12 pool genuinely has no trace because the
  November workspace lives on the scoring host.
* **P0-2 conclusion: option 1 (如实标注) — executed in this branch**, supplemented by the
  option-2 evidence (the local candidate-run artifacts + ledgers exist here and are cited).
* **P0-3 conclusion: option 1 (找回并落地) — executed.** The file was never lost: it lives
  on the scoring host (`…/repro_baseline_20261103/shipping_v16_predicted_sql.json`,
  SHA-verified `510c9104…`, mtime 2026-11-05 01:15) — the audit's disk scan ran on a
  machine that never held it. It is now published **in-repo** under `data/` with
  `data/MANIFEST.sha256` (alongside `shipping_v15`, the gold-free pools, and the
  official two-tab export `submission_pred_v1146.json` `f9f3322b…`). Any third party can
  download and rescore to 74.71% with `scoring/`. (If GitHub release-asset distribution is
  preferred over in-repo `data/`, say the word; files are ≤1.5 MB each.)
* **F4 (cached-label harness): accepted as a governance issue, not as the cause of the v19 gap.**
  The cited `close_pool1534_cache_only68.py` does not exist on this host — request its path
  from the audit for our records. Historical numbers repinned in
  `scoring/HISTORICAL_NUMBERS.json`; all outward-facing November numbers were (and remain)
  produced only through the official evaluator path now vendored in `scoring/`. Note the
  cache theory makes a wrong prediction for v19: a labels-inflated 1063 would NOT reappear
  bit-exact (incl. all three layers) under today's *cacheless* official rescore — yet it does.
* **F2 (CLI `run` disabled): honest wording adopted** (README + COMPLIANCE known-limitations).
  Full live-generation enablement is P2-schedule; prerequisites (private gateway, weights)
  are stated.
* **F5 (Windows): scheduled** — this host is Linux; fix list accepted (sqlite handle close,
  illegal filename chars, symlink skips) + dual-OS CI matrix within the two-week window.
  No logic failures observed here: 111 passed on Linux with optional-dep skips.
* **F6/§2.2 (external Dev predictions ≈ inherited Dev tuning): accepted as a real risk,
  not a rule violation of BIRD** (Dev is the public tuning split; using public predictions
  is standard ensemble practice — but it does bound independent-Test generalization).
  Registry + ablation commitment in `docs/EXTERNAL_CORPORA.md`; COMPLIANCE.md boundary
  reworded to match reality. Ablation runs once the P0-1 freeze lifts.

## 4. Schedule against the remediation plan

| item | status | date |
|---|---|---|
| P0-1 full official rescore + per-question detail | **done** | 2026-11-06 |
| P0-2 disclosure correction | **done (option 1 + option-2 evidence)** | 2026-11-06 |
| P0-3 headline artifact published + rescored | **done (option 1)** | 2026-11-06 |
| P1-1 reproduce script | **done** | 2026-11-06 |
| P1-2 artifacts distribution | **done in-repo** (release option open) | 2026-11-06 |
| P1-3 CLI honesty | **done** | 2026-11-06 |
| P1-4 Windows compat + CI | scheduled | ≤ 2026-11-20 |
| P2-1 corpora registry (partial done) + ablation | registry done; ablation post-freeze | ≤ 2026-11-20 |
| P2-2 scoring governance | **done** (`scoring/`) | 2026-11-06 |
| P2-3 verified/unverified/retracted markers | **done** (README table) | 2026-11-06 |

## 5. Open questions back to the audit

1. Provenance of `9b4c38da…` (URL/date). Current live `dev.zip` = `cdd6d19f…` → `630272f2`.
2. Provenance of the audit's `qwen3.6-27b.json` (`6f5bb278…`): not the git blob at `252ab45a`
   (`0d073da4…`). Plausibly the Oct-10 baseline working tree where a deepseek-backed run
   overwrote the tracked output path (`only_deepseek_LLM_requests: true`) — please verify
   with `git status/diff` in that checkout before it informs any conclusion about our chain.
3. Re-run of §2/§3 under the pinned current gold; we expect 1000 (public v1), 1063 (v19),
   1141 (B), 1146 (shipping) bit-exact, and will reconcile any residual per-question deltas.
4. The path of `close_pool1534_cache_only68.py` for our archive.
5. Whether the P0-1 freeze may lift after the re-run agreement (ablation P2-1 is gated on it).
