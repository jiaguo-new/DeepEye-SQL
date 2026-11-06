# data/ — frozen artifacts (all SHA-pinned in MANIFEST.sha256)

**Format guide (read before scoring — third parties have mis-scored with the wrong file):**

| file | format | purpose |
|---|---|---|
| `submission_pred_v1146.json` (`f9f3322b…`) | **official two-tab**: `SQL\t----- bird -----\tdb_id` | **THE scoring artifact.** Feed directly to the official evaluator (`scoring/`). = 1146/1534 = 74.71%. |
| `shipping_v16_predicted_sql.json` (`510c9104…`) | internal single-tab: `SQL\t----- bird -----db_id` (matches `reproduce_shipping_v16.py` DEL constant) | reproducibility reference only — the evaluator's `package_sqls` split does not parse this form; scoring it as-is yields 0. |
| `shipping_v15_predicted_sql.json` (`d4d14758…`) | internal single-tab (same DEL) | selector chain stage before rules D/E/F — ablation baseline (see docs/EXTERNAL_CORPORA.md) |
| `unified_pool.goldfree.json` / `DE12_POOL.goldfree.json` | result-cluster pools, **all gold-derived fields stripped** (`correct`×6599, `gold`×1520; structure-verified identical otherwise) | inputs to `reproduce_shipping_v16.py`; observable evidence only (sql/fams/n/empty/support) |

`reproduce_shipping_v16.py` needs the **upstream-published** DeepEye file B (git blob
`0d073da4…` at `252ab45a`, MIT, not redistributed here) via `--deepeye-b`, plus unzipped
official dev under `--dev-root`. Note: on heavily loaded machines the 25 s per-query cap
can flip result-equivalence features (observed once, qid 584, under 14-thread concurrency);
run on a quiet machine, or pass `--retry-timeouts` (feature-phase serial retry for
timed-out executions; default off, no behavior change on idle machines).
