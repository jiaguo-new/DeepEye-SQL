# scoring/ — the only sanctioned scoring path (remediation P2-2)

Verbatim official BIRD evaluator copy (`evaluation_ex.py + evaluation_utils.py + process_sql.py (official trio, byte-identical copies)`, SHA-256 `da1bbcd4530be83692d7c650c814ea9704bb710d0c953eb75d02ccb38233cf89`),
plus the pinned 2024-06-27 gold used for every published number in this repo:

| input | SHA-256 |
|---|---|
| dev.zip (publisher, current as of 2026-11-06) | `cdd6d19faeb45a23970b98d3ef6c40a87987c95459c2cf12076897a60cf5a630` |
| dev_20240627/dev.json | `630272f2b1c44d8cef2c6…` (full: see AUDIT_RESPONSE) |
| dev_20240627/dev.sql | `42087d70ed96496b27465…` |
| dev_20240627/dev_databases.zip | `8132aacfe61ecec7198a62e93cb8512dac89b1d6b9922fe3c7bb94b13f317d8f` |

Usage (the ONLY way to produce an outward-facing EX number):
```
python evaluation_ex.py --predicted_sql_path PRED.json \
  --ground_truth_path dev_20240627/dev.sql --db_root_path dev_20240627/dev_databases/ \
  --num_cpus 12 --meta_time_out 30 --diff_json_path dev_1534.jsonl
```
Predictions must be in official two-tab form (`SQL\t----- bird -----\tdb_id`). No result-cache,
no label-inheritance path exists in this directory. Historical numbers table: `scoring/HISTORICAL_NUMBERS.json`.
