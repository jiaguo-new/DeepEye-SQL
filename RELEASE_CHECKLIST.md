# Release checklist — `release/v16-shippable` (2026-11-06)

All items executed on this date; every third-party-runnable step was rehearsed on a
pristine `git clone` of this branch (`/tmp/cleanclone`), not the build workspace.

| # | gate | result |
|---|---|---|
| 1 | `data/MANIFEST.sha256` byte-match on clone (EOL-frozen via `.gitattributes`) | ✅ 6/6, 0 mismatches |
| 2 | Test suite on pristine final clone (`python -m unittest discover -s tests`) | ✅ **114 tests OK**, skipped=9 (sqlglot-optional + platform-guarded) |
| 3 | Headline score from clone's own `scoring/` + official two-tab export | ✅ **1146/1534 = 74.71%** (79.46/68.97/62.76), fresh, 30 s, pinned gold. Note: the first rehearsal of this gate **failed** (`evaluation_ex.py` imports `evaluation_utils`, which had not been bundled) — the pair is now committed byte-identically (`da1bbcd4…`/`f6943d24…`) and the gate re-passed on a pristine GitHub clone. |
| 4 | `reproduce_shipping_v16.py` from clone (`--dev-root` official zip, `--deepeye-b` upstream blob `0d073da4`) | ✅ 1534/1534 **byte-identical** to frozen `510c9104…` |
| 5 | Gold pinning (publisher `dev.zip` `cdd6d19f…`, re-verified byte-identical from live link 2026-11-06) | ✅ dev.json `630272f2`, dev.sql `42087d70`, DBs `8132aacd` |
| 6 | Audit verification of this exact number set (independent machine) | ✅ 6/6 bit-exact (`audit/20261106-compliance-recheck` @ `56c4127`) |
| 7 | Submission kit (BIRD email): official two-tab export SHA `f9f3322b…`, format-verified 1534/1534, disclosures corrected pre-send (dual-artifact DeepEye provenance) | ✅ ready, send is the human action |

## Known limits (honest, non-blocking)
* The Windows CI leg (`windows-latest` matrix) is defined but its first green run
  happens on GitHub Actions after this push — the code fixes target the audit-reported
  failure mechanisms exactly, and a regression test (`tests/test_windows_compat.py`)
  asserts skip semantics on every platform. Not yet *evidenced* on real Windows.
* P2-1 decomposition measured today: own-pool-only 1067 (69.56%) → full Pool-P 1139 →
  shipped 1146. Remaining ablation refinement (stage-2 TRAIN-override isolated re-run)
  is scheduled ≤ 2026-11-20.
* `--retry-timeouts` stays **default off** to preserve documented byte-repro; enabled
  only as a mitigation for loaded machines (SPEC §8).
