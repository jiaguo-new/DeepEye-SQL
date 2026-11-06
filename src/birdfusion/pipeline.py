"""Resumable public-only generation, candidate execution and deterministic selection."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any

from . import __version__
from .corrections import execution_correction_prompt, literal_correction_prompt
from .public_input import database_path, extract_sql, load_public_questions, render_prompt, schema_from_sqlite
from .schema_linking import linked_schema_from_candidates
from .lexical_linking import focused_schema
from .selection import Candidate, SELECTOR_VERSION, select_candidate
from .sql_exec import execute_sql
from .train_examples import examples_for_row, load_public_example_bank
from .values import build_value_index, literal_bindings, value_hints, value_lookup

_WRITE_LOCK = threading.Lock()


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _atomic_json(path: Path, value) -> None:
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temp.replace(path)


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _qid_seed(qid) -> int:
    try:
        return int(qid)
    except (TypeError, ValueError):
        return int(hashlib.sha256(str(qid).encode()).hexdigest()[:8], 16)


def _new_family_seed(family: str, qid, template: str, schema_view: str, sample: int) -> int:
    family_code = {"nanbeige": 1}.get(family, 255)
    template_code = {"standard": 0, "reread": 1, "fkjoin": 2, "decompose": 3}.get(template, 15)
    view_code = 1 if schema_view == "linked" else 0
    slot_code = family_code * 512 + template_code * 32 + view_code * 16 + (int(sample) % 16)
    return (_qid_seed(qid) * 1024 + slot_code) & 0x7FFFFFFF


def _response_sql(choice: dict) -> str | None:
    message = choice.get("message") or {}
    text = str(message.get("content") or message.get("reasoning_content") or message.get("reasoning") or "")
    turn_end = "<|" + "im_end" + "|>"
    if turn_end not in text and str(choice.get("finish_reason") or "") == "length":
        return None
    if turn_end in text:
        text = text.rsplit(turn_end, 1)[-1]
    return extract_sql(text)


def _candidate_completion(client, prompt: str, family_model: str, cfg: dict,
                          base_seed: int, correction_stage: str):
    request = {"model": family_model, "temperature": 0.0}
    if "top_p" in cfg:
        request["top_p"] = float(cfg["top_p"])
    request.update({
        "max_tokens": int(cfg["max_tokens"]),
        "reasoning_effort": cfg.get("correction_reasoning_effort", cfg["reasoning_effort"]),
        "thinking": {"type": "enabled"},
        "seed": base_seed * 1000 + {"literal": 1, "execution": 2}.get(correction_stage, 0),
        "messages": [{"role": "user", "content": prompt}],
    })
    response = client.complete(request)
    return _response_sql(response["choices"][0]), response


def _revise_candidate(row, schema, sql, *, train_examples, value_hints, value_index, value_lookup,
                      client, family_model, cfg, db_path, base_seed):
    initial_sql = sql
    literal_count = 0
    literal_seen: list[dict] = []
    for _ in range(int(cfg["literal_corrections"])):
        if not sql:
            break
        bindings = literal_bindings(sql, value_index, int(cfg["literal_correction_max_targets"]),
                                   lookup=value_lookup)
        if not bindings:
            break
        literal_seen = bindings
        prompt = literal_correction_prompt(row, schema, sql, bindings,
                                         train_examples=train_examples, value_hints=value_hints)
        revised, _ = _candidate_completion(client, prompt, family_model, cfg, base_seed, "literal")
        if not revised or revised == sql:
            break
        sql = revised
        literal_count += 1

    execution_count = 0
    last_error = None
    for _ in range(int(cfg["execution_corrections"])):
        result = execute_sql(db_path, sql or "", float(cfg["exec_timeout_s"]), int(cfg["max_result_rows"]))
        last_error = result.error_message
        if result.ok:
            last_error = None
            break
        prompt = execution_correction_prompt(row, schema, sql or "", last_error or "SQL is empty",
                                             train_examples=train_examples, value_hints=value_hints)
        revised, _ = _candidate_completion(client, prompt, family_model, cfg, base_seed, "execution")
        if not revised or revised == sql:
            break
        sql = revised
        execution_count += 1

    return {
        "sql": sql or "",
        "initial_sql": initial_sql or "",
        "literal_corrections": literal_count,
        "execution_corrections": execution_count,
        "literal_bindings": literal_seen if literal_count else [],
        "last_execution_error_before_freeze": last_error,
    }


def run_pipeline(input_path: str | Path, database_root: str | Path, work_dir: str | Path,
                 config: dict[str, Any], client=None) -> dict[str, Any]:
    """Run public inference. The input row projection never accesses gold fields."""
    started = time.time()
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    rows = load_public_questions(input_path)
    if not rows:
        raise ValueError("No public questions")
    cfg = dict(config)
    cfg.setdefault("templates", ["standard", "reread"])
    cfg.setdefault("samples_per_template", 2)
    cfg.setdefault("models", {"qwen": "qwen3.8-27b"})
    cfg.setdefault("temperature", 0.7)
    cfg.setdefault("max_tokens", 2048)
    cfg.setdefault("reasoning_effort", "high")
    cfg.setdefault("workers", 16)
    cfg.setdefault("exec_workers", 8)
    cfg.setdefault("exec_timeout_s", 30.0)
    cfg.setdefault("max_result_rows", 10000)
    cfg.setdefault("sample_values", 3)
    cfg.setdefault("database_descriptions", False)
    cfg.setdefault("evidence_formula", False)
    cfg.setdefault("train_example_count", 3)
    cfg.setdefault("train_example_scope", "same_db")
    cfg.setdefault("value_hints", False)
    cfg.setdefault("value_hint_max_values", 400)
    cfg.setdefault("value_hint_max_length", 60)
    cfg.setdefault("value_hint_topk", 14)
    cfg.setdefault("literal_corrections", 0)
    cfg.setdefault("execution_corrections", 0)
    cfg.setdefault("literal_correction_max_targets", 8)
    cfg.setdefault("focused_schema_followup", False)
    cfg.setdefault("focused_max_columns", 24)
    cfg.setdefault("linked_schema_followup", False)
    cfg.setdefault("linked_temperatures", [0.2, 0.7])
    cfg.setdefault("context_length_fallback", False)
    families = tuple(f for f in ("qwen", "deepseek", "nanbeige") if f in cfg["models"])
    if not families or set(cfg["models"]) - {"qwen", "deepseek", "nanbeige"}:
        raise ValueError("Model families must be a nonempty subset of qwen, deepseek and nanbeige")
    if client is None:
        # The bundled generic gateway is not yet safe for live benchmark use:
        # it lacks a durable UNKNOWN/no-resend ledger and execution cache identities
        # do not bind database snapshots. Offline injected test clients still work.
        raise RuntimeError("Live pipeline disabled until request ledger and database-bound cache are implemented")
    if not set(cfg["templates"]).issubset({"standard", "reread", "fkjoin", "decompose"}):
        raise ValueError("Unsupported prompt template")
    if cfg.get("schema_format", "json") not in {"json", "ddl"}:
        raise ValueError("Unsupported public schema format")
    if int(cfg["samples_per_template"]) < 1:
        raise ValueError("samples_per_template must be positive")
    if cfg["train_example_scope"] not in {"same_db", "cross_domain_masked"}:
        raise ValueError("Unsupported train_example_scope")
    if "temperatures" in cfg and len(cfg["temperatures"]) != int(cfg["samples_per_template"]):
        raise ValueError("temperatures must contain one value per sample slot")
    for name in ("literal_corrections", "execution_corrections"):
        if int(cfg[name]) < 0:
            raise ValueError(f"{name} must be nonnegative")
    if cfg["linked_schema_followup"] and len(cfg["linked_temperatures"]) != int(cfg["samples_per_template"]):
        raise ValueError("linked_temperatures must contain one value per sample slot")

    schemas = {}
    for db_id in sorted({r["db_id"] for r in rows}):
        schemas[db_id] = schema_from_sqlite(
            database_path(database_root, db_id), int(cfg["sample_values"]),
            description_root=database_root if cfg["database_descriptions"] else None,
        )
    need_value_index = bool(cfg["value_hints"] or int(cfg["literal_corrections"]))
    value_indexes = {}
    if need_value_index:
        value_indexes = {db_id: build_value_index(database_path(database_root, db_id),
                                                  int(cfg["value_hint_max_values"]),
                                                  int(cfg["value_hint_max_length"]))
                         for db_id in schemas}
    hints_by_qid = {}
    if cfg["value_hints"]:
        hints_by_qid = {str(r["question_id"]): value_hints(r["question"], r["evidence"],
                                                            value_indexes[r["db_id"]], int(cfg["value_hint_topk"]))
                        for r in rows}
    formulas_by_qid = {}
    if cfg["evidence_formula"]:
        from .evidence_formula import compile_evidence
        formulas_by_qid = {str(r["question_id"]): compile_evidence(r["question"], r["evidence"]).get("compiled_formulas", "")
                           for r in rows}
    value_lookups = {db: value_lookup(index) for db, index in value_indexes.items()}
    selected_examples = {}
    if cfg.get("train_examples_file"):
        example_bank = load_public_example_bank(cfg["train_examples_file"], cfg.get("train_example_exclude_files"))
        selected_examples = {str(r["question_id"]): examples_for_row(
                                example_bank, r, int(cfg["train_example_count"]),
                                scope=cfg["train_example_scope"])
                             for r in rows}
    scientific_cfg = {k: v for k, v in cfg.items() if k not in {"workers", "exec_workers"}}
    signature_payload = {
        "version": __version__, "selector": SELECTOR_VERSION,
        "rows": rows, "schemas": schemas, "value_hints": hints_by_qid, "evidence_formulas": formulas_by_qid,
        "value_index_sha": {db: _sha(value_indexes.get(db)) for db in schemas},
        "train_examples": selected_examples, "config": scientific_cfg,
    }
    signature = _sha(signature_payload)
    protocol = {
        "protocol": "birdfusion-public-v1", "package_version": __version__,
        "selector_version": SELECTOR_VERSION, "run_signature": signature,
        "n_questions": len(rows), "config": cfg,
        "public_fields": ["question_id", "db_id", "question", "evidence", "difficulty"],
        "schema_inputs": ["sqlite_master", "PRAGMA table_info", "PRAGMA foreign_key_list", "public stored values",
                          "public BIRD database_description CSVs",
                          "public DISTINCT value index", "candidate's own read-only execution error"],
        "gold_fields_used": [], "derived_labels_used": [],
        "selection_inputs": ["candidate SQL", "model family", "prompt template", "schema view", "sample index", "read-only execution result key"],
        "selection_rule": "standard champion; cross-family-or-cross-schema-view+cross-template alternative requires >=3 support, single-source champion, and support margin >=2",
        "scoring": "separate evaluate command only; never called by run_pipeline",
    }
    protocol_path = work / "protocol.json"
    if protocol_path.exists():
        old = _load_json(protocol_path)
        old_cfg = old.get("config") or {}
        old_scientific_cfg = {k: v for k, v in old_cfg.items() if k not in {"workers", "exec_workers"}}
        old_scientific_signature = _sha({**signature_payload, "config": old_scientific_cfg})
        if old_scientific_signature != signature:
            raise ValueError("Resume protocol mismatch; use a new work directory")
        old_signature = old.get("run_signature")
        if old_signature != signature:
            # Legacy protocol files hashed operational worker fields too. Accept only a
            # worker-only difference by retaining the already written legacy signature.
            legacy_signature = _sha({**signature_payload, "config": old_cfg})
            if old_signature != legacy_signature:
                raise ValueError("Resume protocol mismatch; use a new work directory")
            signature = old_signature
    else:
        _atomic_json(protocol_path, protocol)
    _atomic_json(work / "public_inputs.json", rows)
    _atomic_json(work / "public_schemas.json", schemas)
    if cfg["value_hints"]:
        _atomic_json(work / "public_value_hints.json", hints_by_qid)
    if cfg["evidence_formula"]:
        _atomic_json(work / "public_evidence_formulas.json", formulas_by_qid)
    if selected_examples:
        safe_examples = {
            qid: [{**({"source_db_id": ex["db_id"]} if "db_id" in ex else {}),
                   "question": ex["question"], "evidence": ex["evidence"],
                   "public_train_sql": ex["SQL"]} for ex in examples]
            for qid, examples in selected_examples.items()
        }
        _atomic_json(work / "public_train_examples.json", safe_examples)

    if client is None:
        from .gateway import GatewayClient
        client = GatewayClient(cache_dir=work / "http_cache", routes=cfg.get("routes"), channels=cfg.get("channels"))
    had_prior_artifacts = any((work / "candidates").glob("*.json")) if (work / "candidates").exists() else False
    linked_by_qid: dict[str, dict] = {}
    focused_by_qid: dict[str, dict] = {}

    def generate_one(row):
        qid = str(row["question_id"])
        path = work / "candidates" / f"{qid}.json"
        existing = []
        if path.exists():
            payload = _load_json(path)
            if payload.get("run_signature") != signature:
                raise ValueError(f"Candidate resume mismatch for qid={qid}")
            existing = payload["candidates"]
        by_slot = {(c["family"], c["template"], int(c["sample_index"]), c.get("schema_view", "full")): c
                   for c in existing}

        def generate_view(schema_view, prompt_schema, temperatures):
            for family in families:
                for template in cfg["templates"]:
                    for sample in range(int(cfg["samples_per_template"])):
                        slot = (family, template, sample, schema_view)
                        if slot in by_slot:
                            continue
                        prompt = render_prompt(row, prompt_schema, template,
                                               train_examples=selected_examples.get(qid, []),
                                               value_hints=hints_by_qid.get(qid, ""),
                                               schema_format=cfg.get("schema_format", "json"),
                                               evidence_formula=bool(formulas_by_qid.get(qid)))
                        template_offset = 0 if template == "standard" else 10000
                        if schema_view == "linked":
                            template_offset += 20000
                        elif schema_view == "focused":
                            template_offset += 30000
                        if family in {"qwen", "deepseek"}:
                            family_offset = {"qwen": 0, "deepseek": 50000}[family]
                            base_seed = _qid_seed(row["question_id"]) * 100000 + family_offset + template_offset + sample
                        else:
                            base_seed = _new_family_seed(
                                family, row["question_id"], template, schema_view, sample
                            )
                        sample_temperature = temperatures[sample]
                        request = {"model": cfg["models"][family], "temperature": float(sample_temperature)}
                        if "top_p" in cfg:
                            request["top_p"] = float(cfg["top_p"])
                        request.update({
                            "max_tokens": int(cfg["max_tokens"]), "reasoning_effort": cfg["reasoning_effort"],
                            "thinking": {"type": "enabled"},
                            "seed": base_seed,
                            "messages": [{"role": "user", "content": prompt}],
                        })
                        response = client.complete(request)
                        raw_sql = _response_sql(response["choices"][0])
                        context_fallback = False
                        # A shorter-prompt retry is a separate paid request. Require
                        # explicit experimental authorization in the frozen config.
                        if (cfg.get("context_length_fallback", False) and not raw_sql
                                and selected_examples.get(qid)
                                and str(response["choices"][0].get("finish_reason") or "") == "length"):
                            prompt = render_prompt(row, prompt_schema, template,
                                                   train_examples=[], value_hints=hints_by_qid.get(qid, ""),
                                                   schema_format=cfg.get("schema_format", "json"),
                                                   evidence_formula=bool(formulas_by_qid.get(qid)))
                            request["messages"] = [{"role": "user", "content": prompt}]
                            response = client.complete(request)
                            raw_sql = _response_sql(response["choices"][0])
                            context_fallback = True
                        revised = _revise_candidate(
                            row, prompt_schema, raw_sql,
                            train_examples=selected_examples.get(qid, []),
                            value_hints=hints_by_qid.get(qid, ""),
                            value_index=value_indexes.get(row["db_id"], {}),
                            value_lookup=value_lookups.get(row["db_id"], {}),
                            client=client, family_model=cfg["models"][family], cfg=cfg,
                            db_path=database_path(database_root, row["db_id"]), base_seed=base_seed,
                        )
                        by_slot[slot] = {
                            "question_id": row["question_id"], "db_id": row["db_id"],
                            "family": family, "template": template, "sample_index": sample,
                            "schema_view": schema_view,
                            "model": response.get("model", cfg["models"][family]),
                            "sql": revised["sql"], "initial_sql": revised["initial_sql"],
                            "literal_corrections": revised["literal_corrections"],
                            "execution_corrections": revised["execution_corrections"],
                            "literal_bindings": revised["literal_bindings"],
                            "last_execution_error_before_freeze": revised["last_execution_error_before_freeze"],
                            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                            "request_sha256": response.get("_request_sha256"),
                            "replay": bool(response.get("_replay", False)),
                            "context_fallback": context_fallback,
                            "usage": response.get("usage") or {},
                        }
                        ordered = sorted(by_slot.values(), key=lambda c: (
                            c["family"], c["template"], c["sample_index"], c.get("schema_view", "full")
                        ))
                        _atomic_json(path, {"run_signature": signature, "candidates": ordered})

        generate_view("full", schemas[row["db_id"]], cfg.get(
            "temperatures", [cfg["temperature"]] * int(cfg["samples_per_template"])
        ))
        if cfg["focused_schema_followup"]:
            focused = focused_schema(schemas[row["db_id"]], row["question"], row["evidence"],
                                      hints_by_qid.get(qid, ""), int(cfg["focused_max_columns"]))
            with _WRITE_LOCK:
                focused_by_qid[qid] = focused
            generate_view("focused", focused, cfg.get(
                "focused_temperatures", [cfg["temperature"]] * int(cfg["samples_per_template"])
            ))
        if cfg["linked_schema_followup"]:
            derived = linked_schema_from_candidates(
                schemas[row["db_id"]],
                [c["sql"] for c in by_slot.values() if c.get("schema_view", "full") == "full" and c.get("sql")],
            )
            if derived is not None:
                with _WRITE_LOCK:
                    linked_by_qid[qid] = derived
                generate_view("linked", derived, cfg["linked_temperatures"])
        return qid, len(by_slot)

    generation_errors = []
    with ThreadPoolExecutor(max_workers=int(cfg["workers"])) as pool:
        futures = {pool.submit(generate_one, row): row for row in rows}
        done = 0
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                generation_errors.append({"question_id": futures[future]["question_id"],
                                          "error": type(exc).__name__, "detail": str(exc)[:500]})
            done += 1
            if done % 25 == 0 or done == len(rows):
                print(json.dumps({"stage": "generate", "done": done, "total": len(rows),
                                  "errors": len(generation_errors)}), flush=True)
    if generation_errors:
        _atomic_json(work / "generation_errors.json", generation_errors)
        raise RuntimeError(f"Generation incomplete for {len(generation_errors)} questions; "
                           "do not resend uncertain requests; inspect the request ledger first")
    if cfg["focused_schema_followup"]:
        _atomic_json(work / "public_focused_schemas.json", focused_by_qid)
    if cfg["linked_schema_followup"]:
        _atomic_json(work / "public_linked_schemas.json", linked_by_qid)

    def execute_one(row):
        qid = str(row["question_id"])
        source = work / "candidates" / f"{qid}.json"
        path = work / "executed" / f"{qid}.json"
        candidates = _load_json(source)["candidates"]
        if path.exists():
            payload = _load_json(path)
            if payload.get("run_signature") == signature and len(payload.get("candidates", [])) == len(candidates):
                return qid
        db_path = database_path(database_root, row["db_id"])
        result_cache = {}
        executed = []
        for candidate in candidates:
            sql = candidate["sql"]
            if sql not in result_cache:
                result_cache[sql] = execute_sql(db_path, sql, float(cfg["exec_timeout_s"]), int(cfg["max_result_rows"]))
            result = result_cache[sql]
            executed.append({**candidate, "ok": result.ok, "official_key": result.official_key,
                             "execution": result.to_dict()})
        _atomic_json(path, {"run_signature": signature, "candidates": executed})
        return qid

    with ThreadPoolExecutor(max_workers=int(cfg["exec_workers"])) as pool:
        futures = [pool.submit(execute_one, row) for row in rows]
        done = 0
        for future in as_completed(futures):
            future.result()
            done += 1
            if done % 50 == 0 or done == len(rows):
                print(json.dumps({"stage": "execute", "done": done, "total": len(rows)}), flush=True)

    selected = []
    for row in rows:
        qid = str(row["question_id"])
        executed = _load_json(work / "executed" / f"{qid}.json")["candidates"]
        candidates = [Candidate(sql=c["sql"], family=c["family"], template=c["template"],
                                sample_index=int(c["sample_index"]), ok=bool(c["ok"]),
                                official_key=c.get("official_key"), schema_view=c.get("schema_view", "full")) for c in executed]
        decision = select_candidate(candidates)
        selected.append({
            "question_id": row["question_id"], "db_id": row["db_id"], "sql": decision.sql,
            "reason": decision.reason, "official_key": decision.official_key,
            "support": decision.support, "families": decision.families, "templates": decision.templates,
            "n_candidates": len(candidates), "n_executable": sum(c.ok for c in candidates),
        })
        if not selected[-1]["sql"]:
            raise RuntimeError(f"No SQL available for qid={qid}; incomplete generation is not a completed prediction")
    selected.sort(key=lambda r: str(r["question_id"]))
    _atomic_json(work / "selected.json", selected)
    summary = {
        "protocol": "birdfusion-public-v1", "run_signature": signature,
        "n_questions": len(rows), "n_predictions": len(selected),
        "resumed": had_prior_artifacts, "elapsed_s": round(time.time() - started, 3),
        "n_candidates": sum(r["n_candidates"] for r in selected),
        "n_executable": sum(r["n_executable"] for r in selected),
        "selection_reasons": {reason: sum(r["reason"] == reason for r in selected)
                              for reason in sorted({r["reason"] for r in selected})},
        "gold_used": False, "evaluation_executed": False,
    }
    _atomic_json(work / "run_summary.json", summary)
    return summary
