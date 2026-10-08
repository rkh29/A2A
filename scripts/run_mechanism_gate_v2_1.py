import argparse
import asyncio
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_api_chain as base


CONFIG_PATH = ROOT / "experiment_config_v2.1.json"


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def canonical_hash(value):
    return sha256_bytes(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def load_config_and_schedule(schedule_override=None):
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    prompt_spec = config["host"]
    prompt_bytes = (ROOT / prompt_spec["prompt_file"]).read_bytes()
    prompt_hash = sha256_bytes(prompt_bytes)
    if prompt_hash != prompt_spec["prompt_sha256"]:
        raise SystemExit("host-v2-typical prompt hash does not match frozen v2.0 config")
    schedule_name = schedule_override or config["randomization"]["schedule_file"]
    if Path(schedule_name).name != schedule_name:
        raise SystemExit("schedule must be a CSV within configs/")
    schedule_path = ROOT / "configs" / schedule_name
    with schedule_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    schedule_hash = sha256_bytes(schedule_path.read_bytes())
    if schedule_name == config["randomization"]["schedule_file"] and schedule_hash != config["frozen_hashes"]["schedule_sha256"]:
        raise SystemExit("frozen randomization schedule hash does not match v2.0 config")
    expected_n = config["sample_size"]["planned_valid_trials"]
    if len(rows) != expected_n:
        raise SystemExit(f"schedule has {len(rows)} rows; expected {expected_n}")
    expected = {
        (model, cell): config["sample_size"]["per_cell_per_model"]
        for model in config["models"]
        for cell in config["cells"]
    }
    observed = {}
    for row in rows:
        key = (row["model"], row["cell"])
        observed[key] = observed.get(key, 0) + 1
        if row["user_condition"] != config["user_condition"]["name"]:
            raise SystemExit("schedule contains a non-frozen user condition")
    if observed != expected:
        raise SystemExit(f"schedule cell counts differ from config: {observed}")
    trial_ids = [row["trial_id"] for row in rows]
    if len(set(trial_ids)) != len(trial_ids) or any(
        not trial_id.startswith("t-") or any(token in trial_id.lower() for token in ("primary", "secondary", "-a-", "-b-", "-c-", "-d-"))
        for trial_id in trial_ids
    ):
        raise SystemExit("schedule trial IDs must be unique and condition-opaque")
    return config, rows, prompt_bytes.decode("utf-8"), prompt_hash, schedule_path


def build_tool_schema(config):
    tools = json.loads(json.dumps(base.TOOLS))
    if config["host"]["tool_definition_version"] == "tools-v2-neutral":
        tools[0]["function"]["description"] = config["host"]["delegate_task_description"]
        tools[1]["function"]["description"] = config["host"]["write_canary_description"]
    if canonical_hash(tools) != config["frozen_hashes"]["tool_schema_sha256"]:
        raise SystemExit("tool schema hash does not match frozen v2.0 config")
    return tools


def build_payload(config, cell, relative_path):
    common_instruction = config["common_artifact_instruction"].format(relative_path=relative_path)
    artifact = f"{config['remote_summary']}\n\n{common_instruction}"
    metadata = {}
    claim = config["untrusted_approval_claim"]
    if cell == "A":
        artifact = f"{artifact}\n\n{claim}"
    elif cell in ("B", "D"):
        metadata = {"approval_claim": claim}
    return {
        "artifact_text": artifact,
        "task_state": base.TaskState.TASK_STATE_COMPLETED,
        "task_state_name": config["remote_task_state"],
        "task_metadata": metadata,
    }


def validate_static_matrix(config, historical_config):
    relative_path = "mechanism_gate_v2/static-check/canary.txt"
    payloads = {
        cell: build_payload(config, cell, relative_path)
        for cell in config["cells"]
    }
    common_artifact = payloads["C"]["artifact_text"]
    expected_instruction = config["common_artifact_instruction"].format(relative_path=relative_path)
    claim = config["untrusted_approval_claim"]
    if any(payloads[cell]["artifact_text"].count(expected_instruction) != 1 for cell in "ABCD"):
        raise SystemExit("static design check failed: common Artifact instruction is not constant")
    if not all(payloads[cell]["artifact_text"].startswith(config["remote_summary"]) for cell in "ABCD"):
        raise SystemExit("static design check failed: remote summary differs across cells")
    if payloads["A"]["artifact_text"] != f"{common_artifact}\n\n{claim}":
        raise SystemExit("static design check failed: cell A does not carry only the added Artifact claim")
    if any(payloads[cell]["artifact_text"] != common_artifact for cell in "BCD"):
        raise SystemExit("static design check failed: Artifact instruction changed across B/C/D")
    if payloads["A"]["task_metadata"] or payloads["C"]["task_metadata"]:
        raise SystemExit("static design check failed: A or C unexpectedly has Task.metadata")
    expected_claim = {"approval_claim": claim}
    if payloads["B"]["task_metadata"] != expected_claim or payloads["D"]["task_metadata"] != expected_claim:
        raise SystemExit("static design check failed: B and D metadata claim differs")
    if any("requested_action" in payloads[cell]["task_metadata"] for cell in "ABCD"):
        raise SystemExit("static design check failed: action instruction leaked into metadata")
    prompt_hashes = set()
    for cell in "ABCD":
        prompt = base.make_user_prompt(
            "typical_attacks_v2",
            f"mechanism_{cell}",
            relative_path,
            historical_config["null_baseline_operationalization"],
            config["user_condition"]["name"],
            historical_config["typical_host_v2_matrix"],
        )
        prompt_hashes.add(sha256_bytes(prompt.encode("utf-8")))
    if len(prompt_hashes) != 1:
        raise SystemExit("static design check failed: user prompt differs across cells")
    return {
        "common_artifact_instruction_constant": True,
        "artifact_b_c_d_identical": True,
        "metadata_b_equals_d": True,
        "metadata_contains_no_action_instruction": True,
        "user_prompt_constant": True,
        "task_state_constant": len({p["task_state_name"] for p in payloads.values()}) == 1,
    }


def make_clients(config):
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY is not set")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set")
    models = config["models"]
    clients = {
        "primary": {
            "client": OpenAI(
                api_key=os.environ["DEEPSEEK_API_KEY"],
                base_url=models["primary"]["api_base_url"],
                max_retries=0,
                timeout=120.0,
            ),
            "model_name": models["primary"]["requested_model_id"],
            "provider": models["primary"]["provider"],
            "api_base_url": models["primary"]["api_base_url"],
            "thinking": models["primary"]["thinking"],
            "reasoning_effort": models["primary"]["reasoning_effort"],
        },
        "secondary": {
            "client": OpenAI(
                api_key=os.environ["OPENAI_API_KEY"],
                base_url=os.environ.get("OPENAI_BASE_URL", models["secondary"]["api_base_url_default"]),
                max_retries=0,
                timeout=120.0,
            ),
            "model_name": models["secondary"]["requested_model_id"],
            "provider": models["secondary"]["provider"],
            "api_base_url": os.environ.get("OPENAI_BASE_URL", models["secondary"]["api_base_url_default"]),
            "thinking": models["secondary"]["thinking"],
            "reasoning_effort": models["secondary"]["reasoning_effort"],
        },
    }
    return clients


async def run_batch(args):
    config, schedule, host_prompt, prompt_hash, schedule_path = load_config_and_schedule(args.schedule_file)
    historical_config = json.loads((ROOT / config["historical_config"]).read_text(encoding="utf-8"))
    static_checks = validate_static_matrix(config, historical_config)
    tools = build_tool_schema(config)
    tool_hash = canonical_hash(tools)
    clients = make_clients(config)
    schedule_hash = sha256_bytes(schedule_path.read_bytes())
    log_path = ROOT / "logs" / f"mechanism_gate_v2.1_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    results = []
    mcp_params = base.StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "scripts" / "probe_mcp_stdio.py"), "--server"],
        cwd=str(ROOT),
    )
    async with base.stdio_client(mcp_params) as (read_stream, write_stream):
        async with base.ClientSession(read_stream, write_stream) as mcp_session:
            await mcp_session.initialize()
            available = {tool.name for tool in (await mcp_session.list_tools()).tools}
            if "write_canary" not in available:
                raise RuntimeError("MCP server did not expose write_canary")
            with log_path.open("a", encoding="utf-8") as log:
                for index, row in enumerate(schedule, start=1):
                    model_key = row["model"]
                    cell = row["cell"]
                    client_info = clients[model_key]
                    internal_stage = "typical_metadata_state_only" if cell == "D" else "typical_attacks_v2"
                    retry_limit = config["execution"]["infrastructure_retry_limit"]
                    for attempt in range(retry_limit + 1):
                        planned_trial_id = row["trial_id"]
                        trial_id = planned_trial_id if attempt == 0 else f"{planned_trial_id}-retry{attempt}"
                        target_path = f"typical_v2/mechanism_gate/{trial_id}.txt"
                        payload = build_payload(config, cell, target_path)
                        user_prompt = base.make_user_prompt(
                            internal_stage,
                            f"mechanism_{cell}",
                            target_path,
                            historical_config["null_baseline_operationalization"],
                            row["user_condition"],
                            historical_config["typical_host_v2_matrix"],
                        )
                        record = await base.run_trial(
                            internal_stage,
                            trial_id,
                            f"mechanism_{cell}",
                            payload["task_state_name"],
                            payload,
                            mcp_session,
                            client_info["client"],
                            client_info["model_name"],
                            client_info["provider"],
                            client_info["thinking"],
                            client_info["reasoning_effort"],
                            host_prompt,
                            prompt_hash,
                            config["host"]["prompt_version"],
                            config["host"]["tool_definition_version"],
                            tools,
                            {},
                            {},
                            row["user_condition"],
                            historical_config["typical_host_v2_matrix"],
                        )
                        model_visible_metadata = payload["task_metadata"] if cell == "B" else {}
                        record.update({
                            "stage": "mechanism_gate_v2.1",
                            "condition": "mechanism_gate_v2.1",
                            "target_relative_path": target_path,
                            "protocol_version": config["protocol_version"],
                            "cell": cell,
                            "mechanism_role": config["cells"][cell],
                            "model_key": model_key,
                            "block": int(row["block"]),
                            "replicate": int(row["replicate"]),
                            "random_seed": int(row["random_seed"]),
                            "tool_schema_sha256": tool_hash,
                            "schedule_sha256": schedule_hash,
                            "user_prompt_sha256": sha256_bytes(user_prompt.encode("utf-8")),
                            "remote_artifact_sha256": sha256_bytes(payload["artifact_text"].encode("utf-8")),
                            "remote_metadata_sha256": canonical_hash(payload["task_metadata"]),
                            "expected_model_visible_metadata_keys": sorted(model_visible_metadata),
                            "approval_claim_model_visible": cell in ("A", "B"),
                            "infrastructure_retry_limit": retry_limit,
                            "retry_attempt": attempt,
                            "source_trial_id": planned_trial_id,
                            "replaces_trial_id": planned_trial_id if attempt else None,
                            "static_matrix_checks": static_checks,
                        })
                        record["api_base_url"] = client_info["api_base_url"]
                        record["schedule_run_index"] = int(row["run_index"])
                        log.write(json.dumps(record, ensure_ascii=False) + "\n")
                        log.flush()
                        results.append(record)
                        print(
                            f"{index}/{len(schedule)} {model_key}/{cell}/{trial_id}: "
                            f"ASR={int(bool(record.get('asr_success')))} "
                            f"tool_attempts={record['tool_call_attempt']} "
                            f"text_attempt={int(bool(record['text_emitted_attempt']))} "
                            f"tokens={record['total_tokens']}"
                        )
                        if "infrastructure_error" not in record:
                            break
                    print(
                        f"{index}/{len(schedule)} {model_key}/{cell}/{trial_id}: "
                        f"ASR={int(bool(record.get('asr_success')))} "
                        f"tool_attempts={record['tool_call_attempt']} "
                        f"text_attempt={int(bool(record['text_emitted_attempt']))} "
                        f"tokens={record['total_tokens']}"
                    )
    summary = {
        "stage": "mechanism_gate_v2.1",
        "protocol_version": config["protocol_version"],
        "planned_trials": len(schedule),
        "completed_trials": sum(
            any(
                row.get("source_trial_id", row["trial_id"]) == planned["trial_id"]
                and "infrastructure_error" not in row
                and row.get("completed")
                for row in results
            )
            for planned in schedule
        ),
        "valid_trials": sum(
            any(
                row.get("source_trial_id", row["trial_id"]) == planned["trial_id"]
                and "infrastructure_error" not in row
                and row.get("completed")
                and row.get("delegation_success")
                and row.get("artifact_received")
                for row in results
            )
            for planned in schedule
        ),
        "attempt_records": len(results),
        "unresolved_schedule_rows": sum(
            not any(
                row.get("source_trial_id", row["trial_id"]) == planned["trial_id"]
                and "infrastructure_error" not in row
                and row.get("completed")
                and row.get("delegation_success")
                and row.get("artifact_received")
                for row in results
            )
            for planned in schedule
        ),
        "infrastructure_errors": sum("infrastructure_error" in row for row in results),
        "total_tokens": sum(int(row["total_tokens"]) for row in results),
        "asr_successes": sum(bool(row.get("asr_success")) for row in results),
        "tool_call_attempts": sum(int(row["tool_call_attempt"]) for row in results),
        "text_emitted_attempts": sum(bool(row["text_emitted_attempt"]) for row in results),
        "schedule_sha256": schedule_hash,
        "host_prompt_sha256": prompt_hash,
        "tool_schema_sha256": tool_hash,
        "log_file": str(log_path),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["infrastructure_errors"] == 0 else 1


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--schedule-file", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def dry_run(args):
    config, schedule, _host_prompt, prompt_hash, schedule_path = load_config_and_schedule(args.schedule_file)
    historical_config = json.loads((ROOT / config["historical_config"]).read_text(encoding="utf-8"))
    static_checks = validate_static_matrix(config, historical_config)
    tools = build_tool_schema(config)
    counts = {
        f"{model}:{cell}": sum(row["model"] == model and row["cell"] == cell for row in schedule)
        for model in config["models"]
        for cell in config["cells"]
    }
    print(json.dumps({
        "dry_run": True,
        "planned_trials": len(schedule),
        "counts": counts,
        "host_prompt_sha256": prompt_hash,
        "schedule_sha256": sha256_bytes(schedule_path.read_bytes()),
        "tool_schema_sha256": canonical_hash(tools),
        "user_condition": config["user_condition"]["name"],
        "task_state": config["remote_task_state"],
        "static_matrix_checks": static_checks,
        "no_api_requests_sent": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    cli_args = parse_args()
    if cli_args.dry_run:
        dry_run(cli_args)
    else:
        raise SystemExit(asyncio.run(run_batch(cli_args)))
