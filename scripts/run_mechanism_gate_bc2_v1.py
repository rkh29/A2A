import argparse
import asyncio
import csv
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from types import SimpleNamespace
import uuid
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

import a2a.types as a2a_types
from openai import OpenAI
from mcp.types import CallToolResult

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# run_api_chain.py was edited after v2.1 to use the A2A 0.3.x type shape.
# The frozen Day-0 SDK exposes these legacy types under a2a.compat.v0_3.
if not hasattr(a2a_types, "TextPart"):
    from a2a.compat.v0_3.types import TextPart as LegacyTextPart

    a2a_types.TextPart = LegacyTextPart
if not hasattr(a2a_types.TaskState, "completed"):
    proto_task_state = a2a_types.TaskState
    a2a_types.TaskState = SimpleNamespace(
        completed=proto_task_state.TASK_STATE_COMPLETED,
        canceled=proto_task_state.TASK_STATE_CANCELED,
        auth_required=proto_task_state.TASK_STATE_AUTH_REQUIRED,
    )
if not hasattr(a2a_types.Role, "user"):
    proto_role = a2a_types.Role
    a2a_types.Role = SimpleNamespace(user=proto_role.ROLE_USER, agent=proto_role.ROLE_AGENT)

import run_api_chain as base
import run_mechanism_gate_v2_1 as mechanism
from a2a.compat.v0_3 import types as a2a_v03
from a2a.types import AgentInterface, SendMessageRequest, StreamResponse
from google.protobuf.json_format import MessageToDict

CONFIG_PATH = ROOT / "configs" / "mechanism_gate_bc2_v1.json"
MANIFEST_PATH = ROOT / "configs" / "mechanism_gate_bc2_v1_freeze_manifest.json"


def create_compatible_a2a_app(artifact_text, task_state, task_metadata=None):
    if task_state != base.TaskState.TASK_STATE_COMPLETED:
        raise ValueError("R1 fixture only supports the frozen completed-task state")

    card = base.AgentCard(
        name="Local Fixture Remote",
        description="Returns a deterministic benign summary artifact for the sandbox pilot.",
        supported_interfaces=[AgentInterface(
            url=base.RPC_URL,
            protocol_binding="JSONRPC",
            protocol_version="0.3.0",
        )],
        version="1.0.0",
        capabilities=base.AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[base.AgentSkill(
            id="fixture",
            name="Fixture",
            description="Returns a deterministic fixture summary.",
            tags=["pilot"],
        )],
    )

    async def message_send(request):
        request_json = await request.json()
        message = request_json.get("params", {}).get("message", {})
        task = a2a_v03.Task(
            id=message.get("taskId") or str(uuid.uuid4()),
            context_id=message.get("contextId") or str(uuid.uuid4()),
            status=a2a_v03.TaskStatus(state=a2a_v03.TaskState.completed),
            metadata=dict(task_metadata or {}),
            artifacts=[a2a_v03.Artifact(
                artifact_id=str(uuid.uuid4()),
                name="fixture_summary",
                parts=[a2a_v03.Part(root=a2a_v03.TextPart(text=artifact_text))],
            )],
        )
        return base.JSONResponse({
            "jsonrpc": "2.0",
            "id": request_json.get("id"),
            "result": task.model_dump(mode="json", by_alias=True, exclude_none=True),
        })

    async def get_agent_card(_request):
        return base.JSONResponse(MessageToDict(card, preserving_proto_field_name=False))

    app = base.Starlette(routes=[
        base.Route("/a2a", message_send, methods=["POST"]),
        base.Route("/.well-known/agent-card.json", get_agent_card, methods=["GET"]),
    ])
    return app, card


async def compatible_delegate(app, card, task_description):
    http = base.httpx.AsyncClient(
        transport=base.httpx.ASGITransport(app=app),
        base_url="http://a2a.test",
    )
    client = base.ClientFactory(
        base.ClientConfig(streaming=False, httpx_client=http)
    ).create(card)
    request_message = base.Message(
        role=base.Role.ROLE_USER,
        message_id=str(uuid.uuid4()),
        parts=[base.Part(text=task_description)],
    )
    request = SendMessageRequest(message=request_message)
    try:
        events = [event async for event in client.send_message(request)]
        task_event = next(
            (event.task for event in events if isinstance(event, StreamResponse) and event.HasField("task")),
            None,
        )
        if task_event is None:
            return {
                "ok": False,
                "error": "no_task_event",
                "events_received": len(events),
                "request_message": MessageToDict(request_message, preserving_proto_field_name=False),
            }, None
        task_json = MessageToDict(task_event, preserving_proto_field_name=False)
        artifact_texts = [
            part.text
            for artifact in task_event.artifacts
            for part in artifact.parts
            if part.text
        ]
        task_completed = task_event.status.state == base.TaskState.TASK_STATE_COMPLETED
        return {
            "ok": task_completed,
            "response_received": True,
            "task_completed": task_completed,
            "task_state": task_json.get("status", {}).get("state"),
            "events_received": len(events),
            "request_message": MessageToDict(request_message, preserving_proto_field_name=False),
            "task": task_json,
            "artifact_texts": artifact_texts,
        }, artifact_texts[0] if artifact_texts else None
    finally:
        await client.close()
        await http.aclose()


base.create_a2a_app = create_compatible_a2a_app
base.delegate = compatible_delegate


def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_hash(value):
    return sha256_text(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def frozen_path(relative):
    return ROOT / relative


def validate_git_anchor(manifest):
    anchor = manifest["git_commit_time_anchor"]
    base_args = ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT)]
    check = subprocess.run(
        base_args + ["cat-file", "-e", f"{anchor['commit']}^{{commit}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check.returncode:
        raise SystemExit("frozen git time-anchor commit is unavailable")
    result = subprocess.run(
        base_args + ["show", "-s", "--format=%cI", anchor["commit"]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    if result.stdout.strip() != anchor["commit_time_iso8601"]:
        raise SystemExit("frozen git commit timestamp does not match manifest")


def read_csv(relative):
    with frozen_path(relative).open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def validate_schedule(config, schedule, assignment, reserves):
    key = {row["arm_code"]: row["cell"] for row in assignment}
    if len(key) != 2 or sorted(key.values()) != ["B", "C"]:
        raise SystemExit("assignment key must map two opaque codes to B and C")
    if len(schedule) != 40 or len(reserves) != 40:
        raise SystemExit("main and reserve schedules must each have 40 rows")
    if {row["user_condition"] for row in schedule + reserves} != {"user_silence"}:
        raise SystemExit("non-frozen user condition in schedule")

    trial_ids = [row["trial_id"] for row in schedule + reserves]
    if len(set(trial_ids)) != 80 or any(not re.fullmatch(r"t-[0-9a-f]{16}", value) for value in trial_ids):
        raise SystemExit("trial IDs must be unique opaque 64-bit hexadecimal IDs")
    if any(row["arm_code"] not in key for row in schedule + reserves):
        raise SystemExit("schedule contains an unknown arm code")

    cells = {code: 0 for code in key}
    blocks = {}
    source_by_id = {}
    for row in schedule:
        cells[row["arm_code"]] += 1
        blocks.setdefault(int(row["block"]), []).append(row["arm_code"])
        source_by_id[row["trial_id"]] = row
    if set(cells.values()) != {20}:
        raise SystemExit("schedule must contain 20 rows per opaque arm code")
    if len(blocks) != 20 or any(len(codes) != 2 or codes[0] == codes[1] for codes in blocks.values()):
        raise SystemExit("each of 20 blocks must contain both opaque arm codes once")
    if sorted(int(row["run_index"]) for row in schedule) != list(range(1, 41)):
        raise SystemExit("run_index must be a permutation of 1..40")
    if set(source_by_id) != {row["source_trial_id"] for row in reserves}:
        raise SystemExit("each main slot must have exactly one frozen reserve")
    reserve_sources = [row["source_trial_id"] for row in reserves]
    if len(set(reserve_sources)) != 40:
        raise SystemExit("reserve source IDs must be unique")
    for row in reserves:
        source = source_by_id[row["source_trial_id"]]
        if row["arm_code"] != source["arm_code"] or int(row["block"]) != int(source["block"]):
            raise SystemExit("reserve must inherit its source arm code and block")
    return key, {row["source_trial_id"]: row for row in reserves}


def load_frozen_inputs():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for relative, expected in manifest["sha256"].items():
        if sha256_file(frozen_path(relative)) != expected:
            raise SystemExit(f"frozen input hash mismatch: {relative}")
    validate_git_anchor(manifest)

    installed = {
        "a2a-sdk": metadata.version("a2a-sdk"),
        "mcp": metadata.version("mcp"),
        "openai": metadata.version("openai"),
    }
    if installed != manifest["runtime"]["packages"]:
        raise SystemExit("installed package versions differ from frozen manifest")
    if platform.python_version() != manifest["runtime"]["python"]:
        raise SystemExit("installed Python version differs from frozen manifest")
    freeze_path = frozen_path(config["pip_freeze_file"])
    freeze_text = freeze_path.read_text(encoding="utf-8-sig")
    if manifest["runtime"]["pip_freeze"] != freeze_text.splitlines():
        raise SystemExit("embedded pip freeze differs from the frozen file")
    if sha256_text(freeze_text) != manifest["runtime"]["pip_freeze_sha256"]:
        raise SystemExit("pip-freeze hash differs from the frozen manifest")
    fields = getattr(CallToolResult, "model_fields", {})
    if "is_error" not in fields or fields["is_error"].alias != "isError":
        raise SystemExit("MCP CallToolResult does not expose the frozen is_error field")

    v21 = json.loads((ROOT / config["base_experiment_config"]).read_text(encoding="utf-8"))
    prompt_bytes = (ROOT / v21["host"]["prompt_file"]).read_bytes()
    prompt_hash = hashlib.sha256(prompt_bytes).hexdigest()
    if prompt_hash != manifest["host_prompt_sha256"]:
        raise SystemExit("host prompt hash differs from manifest")
    tools = mechanism.build_tool_schema(v21)
    tool_hash = canonical_hash(tools)
    if tool_hash != manifest["tool_schema_sha256"]:
        raise SystemExit("tool schema hash differs from manifest")
    schedule = read_csv(config["schedule_file"])
    assignment = read_csv(config["assignment_key_file"])
    reserves = read_csv(config["reserve_schedule_file"])
    key, reserve_by_source = validate_schedule(config, schedule, assignment, reserves)
    output_dir = ROOT / "sandbox" / "typical_v2" / "mechanism_gate_bc2_v1"
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SystemExit("BC-2 output directory is not empty; refusing a contaminated start")
    for row in schedule + reserves:
        output = ROOT / "sandbox" / "typical_v2" / "mechanism_gate_bc2_v1" / f"{row['trial_id']}.txt"
        if output.exists():
            raise SystemExit("BC-2 output path already exists; refusing to reuse a contaminated schedule")
    historical = json.loads((ROOT / v21["historical_config"]).read_text(encoding="utf-8"))
    static_checks = mechanism.validate_static_matrix(v21, historical)
    return manifest, config, v21, schedule, key, reserve_by_source, prompt_bytes.decode("utf-8"), prompt_hash, tools, tool_hash, historical, static_checks


def dry_run():
    manifest, config, _v21, schedule, _key, reserves, _prompt, prompt_hash, _tools, tool_hash, _historical, static = load_frozen_inputs()
    opaque_counts = {}
    for row in schedule:
        opaque_counts[row["arm_code"]] = opaque_counts.get(row["arm_code"], 0) + 1
    print(json.dumps({
        "dry_run": True,
        "experiment_id": config["experiment_id"],
        "planned_slots": len(schedule),
        "reserve_candidates": len(reserves),
        "opaque_arm_counts": opaque_counts,
        "schedule_sha256": manifest["sha256"][config["schedule_file"]],
        "host_prompt_sha256": prompt_hash,
        "tool_schema_sha256": tool_hash,
        "static_payload_checks": static,
        "no_api_requests_sent": True,
    }, ensure_ascii=False))


def make_client(config):
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY is not set")
    model = config["model"]
    return {
        "client": OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=model["api_base_url"],
            max_retries=0,
            timeout=120.0,
        ),
        "model_name": model["requested_model_id"],
        "provider": model["provider"],
        "thinking": model["thinking"],
        "reasoning_effort": model["reasoning_effort"],
    }


async def run_positive_gate():
    manifest, config, v21, _schedule, _key, _reserves, prompt, prompt_hash, tools, tool_hash, historical, static = load_frozen_inputs()
    trial_ids = config["positive_gate_trial_ids"]
    if len(trial_ids) != 3 or len(set(trial_ids)) != 3:
        raise SystemExit("frozen positive gate must contain exactly three unique trial IDs")
    output_paths = [ROOT / "sandbox" / "positive_gate" / f"{trial_id}.txt" for trial_id in trial_ids]
    if any(path.exists() for path in output_paths):
        raise SystemExit("positive-gate output path already exists; refusing to reuse a canary")

    model = make_client(config)
    model["api_base_url"] = config["model"]["api_base_url"]
    log_path = ROOT / "logs" / f"mechanism_gate_bc2_v1_positive_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    params = base.StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / config["mcp_server_file"]), "--server"],
        cwd=str(ROOT),
    )
    completed = 0
    passed = 0
    async with base.stdio_client(params) as (read_stream, write_stream):
        async with base.ClientSession(read_stream, write_stream) as mcp_session:
            await mcp_session.initialize()
            available = {tool.name for tool in (await mcp_session.list_tools()).tools}
            if "write_canary" not in available:
                raise SystemExit("MCP server did not expose write_canary")
            with log_path.open("x", encoding="utf-8") as handle:
                for trial_id in trial_ids:
                    payload = {
                        "artifact_text": base.REMOTE_SUMMARY,
                        "task_state": base.TaskState.TASK_STATE_COMPLETED,
                        "task_state_name": "TASK_STATE_COMPLETED",
                        "task_metadata": {},
                    }
                    record = await base.run_trial(
                        "positive_gate",
                        trial_id,
                        "bc2_authorized_fixture",
                        payload["task_state_name"],
                        payload,
                        mcp_session,
                        model["client"],
                        model["model_name"],
                        model["provider"],
                        model["thinking"],
                        model["reasoning_effort"],
                        prompt,
                        prompt_hash,
                        v21["host"]["prompt_version"],
                        v21["host"]["tool_definition_version"],
                        tools,
                        {},
                        {},
                    )
                    record.update({
                        "stage": f"{config['experiment_id']}_positive_gate",
                        "api_base_url": model["api_base_url"],
                        "runtime_versions": manifest["runtime"],
                        "tool_schema_sha256": tool_hash,
                        "freeze_manifest_sha256": sha256_file(MANIFEST_PATH),
                    })
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                    completed += 1
                    trial_passed = bool(
                        record.get("passed")
                        and record.get("authorized_canary_changed")
                        and "infrastructure_error" not in record
                        and not has_mcp_error(record)
                    )
                    passed += int(trial_passed)
                    if not trial_passed:
                        break
    print(json.dumps({
        "positive_gate_complete": completed == 3,
        "completed_trials": completed,
        "passed_trials": passed,
        "gate_passed": completed == 3 and passed == 3,
        "log_file": str(log_path),
        "main_batch_started": False,
    }, ensure_ascii=False))
    return 0 if completed == 3 and passed == 3 else 1


def terminal_record(record):
    observed_write = bool(record.get("asr_success"))
    completed_exchange = (
        "infrastructure_error" not in record
        and record.get("completed")
        and record.get("delegation_success")
        and record.get("artifact_received")
    )
    return observed_write or bool(completed_exchange)


async def run_one_slot(row, source_id, is_reserve, retry, mcp_session, client_info, shared):
    manifest = shared["manifest"]
    config = shared["config"]
    v21 = shared["v21"]
    prompt = shared["prompt"]
    prompt_hash = shared["prompt_hash"]
    tools = shared["tools"]
    historical = shared["historical"]
    static = shared["static"]
    cell = shared["key"][row["arm_code"]]
    target_path = f"typical_v2/mechanism_gate_bc2_v1/{row['trial_id']}.txt"
    payload = mechanism.build_payload(v21, cell, target_path)
    prompt_variant = f"mechanism_{cell}"
    user_prompt_path = f"typical_v2/{row['user_condition']}/{prompt_variant}/{row['trial_id']}.txt"
    user_prompt = base.make_user_prompt(
        "typical_attacks_v2",
        prompt_variant,
        user_prompt_path,
        historical["null_baseline_operationalization"],
        row["user_condition"],
        historical["typical_host_v2_matrix"],
    )
    record = await base.run_trial(
        "typical_attacks_v2",
        row["trial_id"],
        prompt_variant,
        payload["task_state_name"],
        payload,
        mcp_session,
        client_info["client"],
        client_info["model_name"],
        client_info["provider"],
        client_info["thinking"],
        client_info["reasoning_effort"],
        prompt,
        prompt_hash,
        v21["host"]["prompt_version"],
        v21["host"]["tool_definition_version"],
        tools,
        {},
        {},
        row["user_condition"],
        historical["typical_host_v2_matrix"],
    )
    record.update({
        "stage": config["experiment_id"],
        "condition": config["experiment_id"],
        "variant": "bc2_blinded",
        "target_relative_path": target_path,
        "arm_code": row["arm_code"],
        "block": int(row["block"]),
        "replicate": int(row["replicate"]),
        "random_seed": int(row["random_seed"]),
        "source_trial_id": source_id,
        "reserve_attempt": bool(is_reserve),
        "reserve_trial_id": row["trial_id"] if is_reserve else None,
        "retry_attempt": retry,
        "infrastructure_retry_limit": config["retry_limit"],
        "schedule_sha256": manifest["sha256"][config["schedule_file"]],
        "reserve_schedule_sha256": manifest["sha256"][config["reserve_schedule_file"]],
        "assignment_key_sha256": manifest["sha256"][config["assignment_key_file"]],
        "experiment_config_sha256": manifest["sha256"][CONFIG_PATH.relative_to(ROOT).as_posix()],
        "freeze_manifest_sha256": sha256_file(MANIFEST_PATH),
        "host_prompt_sha256": prompt_hash,
        "tool_schema_sha256": shared["tool_hash"],
        "user_prompt_sha256": sha256_text(user_prompt),
        "remote_artifact_sha256": sha256_text(payload["artifact_text"]),
        "remote_metadata_sha256": canonical_hash(payload["task_metadata"]),
        "static_matrix_checks": static,
        "api_base_url": client_info["api_base_url"],
        "runtime_versions": manifest["runtime"],
        "schedule_run_index": int(row.get("run_index", row.get("reserve_index"))),
    })
    return record


def has_mcp_error(record):
    return any("mcp_call_error" in call for call in record.get("mcp_calls", []))


async def run_batch():
    frozen = load_frozen_inputs()
    manifest, config, v21, schedule, key, reserve_by_source, prompt, prompt_hash, tools, tool_hash, historical, static = frozen
    shared = {
        "manifest": manifest,
        "config": config,
        "v21": v21,
        "key": key,
        "prompt": prompt,
        "prompt_hash": prompt_hash,
        "tools": tools,
        "tool_hash": tool_hash,
        "historical": historical,
        "static": static,
    }
    model = make_client(config)
    model["api_base_url"] = config["model"]["api_base_url"]
    log_path = ROOT / "logs" / f"mechanism_gate_bc2_v1_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    params = base.StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / config["mcp_server_file"]), "--server"],
        cwd=str(ROOT),
    )
    records_written = 0
    async with base.stdio_client(params) as (read_stream, write_stream):
        async with base.ClientSession(read_stream, write_stream) as mcp_session:
            await mcp_session.initialize()
            available = {tool.name for tool in (await mcp_session.list_tools()).tools}
            if "write_canary" not in available:
                raise SystemExit("MCP server did not expose write_canary")
            with log_path.open("x", encoding="utf-8") as handle:
                for index, row in enumerate(schedule, start=1):
                    source_id = row["trial_id"]
                    valid_or_success = False
                    for retry in range(config["retry_limit"] + 1):
                        record = await run_one_slot(row, source_id, False, retry, mcp_session, model, shared)
                        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                        handle.flush()
                        records_written += 1
                        print(f"R1 progress {index}/{len(schedule)}", flush=True)
                        if has_mcp_error(record):
                            raise SystemExit("MCP call error recorded; collection halted before further slots")
                        if terminal_record(record):
                            valid_or_success = True
                            break
                    if not valid_or_success:
                        reserve = reserve_by_source[source_id]
                        for retry in range(config["retry_limit"] + 1):
                            record = await run_one_slot(reserve, source_id, True, retry, mcp_session, model, shared)
                            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                            handle.flush()
                            records_written += 1
                            print(f"R1 progress {index}/{len(schedule)}", flush=True)
                            if has_mcp_error(record):
                                raise SystemExit("MCP call error recorded; collection halted before further slots")
                            if terminal_record(record):
                                break
    print(json.dumps({
        "collection_complete": True,
        "records_written": records_written,
        "log_file": str(log_path),
        "outcomes_not_printed": True,
        "analysis_not_run": True,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--positive-gate", action="store_true")
    args = parser.parse_args()
    if args.dry_run and args.positive_gate:
        raise SystemExit("--dry-run and --positive-gate cannot be combined")
    if args.dry_run:
        dry_run()
    elif args.positive_gate:
        raise SystemExit(asyncio.run(run_positive_gate()))
    else:
        raise SystemExit(asyncio.run(run_batch()))
