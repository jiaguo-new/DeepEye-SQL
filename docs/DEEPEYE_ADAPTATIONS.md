# DeepEye-SQL usage: exact adaptation record

Upstream: HKUSTDial/DeepEye-SQL @ `252ab45a7adf9f88fb93d1ff27ca019d20f0449a` (MIT).

We run the upstream repository as-is except for:

| # | Adaptation | Category |
|---|---|---|
| 1 | LLM: published default model replaced by our gateway-served model (per user directive and frozen model policy) | backend |
| 2 | Embedding: local default embedding model replaced by gateway OpenAI-compatible embeddings API | backend |
| 3 | cuda -> cpu (GPU unavailable on host) | device |
| 4 | `n>1` sampling split into `n` independent 1-sample requests (gateway constraint; prompt/temperature identical) | request shape |
| 5 | `app/llm_extractor/extractor.py`: `llm.ask(..., timeout=600, ...)` (one-line robustness) | local diff (the only code diff) |
| 6 | `pyproject.toml` / lockfile adjustments for offline install | environment |

No changes to: schema linking prompts/algorithms, value retrieval, SQL
generation templates, candidate filtering, or DeepEye's own final selection.
Their pipeline final answer scores **1143/1534 = 74.51%** on publisher Dev
20240627 (official evaluator, local rescore), which is the baseline our
shipped selector (74.71%) treats as Pool D evidence.

Audit note (compliance): `gold_sql` appears in DeepEye snapshot inputs but
is consumed only by a post-hoc diagnostic recall function and the scoring
runner — never fed forward into generation or selection. This was verified
by artifact-field auditing.
