import hashlib
import json
import subprocess
import sys
import csv
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_mechanism_gate_bc2_v1 as r1
import run_mechanism_gate_v2_1 as mechanism
import present_a2a_selective_v1 as selective


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256_file(path):
    return sha256_bytes(path.read_bytes())


def canonical_hash(value):
    return sha256_bytes(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def git(args, text=True):
    command = ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), *args]
    return subprocess.run(command, check=True, capture_output=True, text=text, encoding="utf-8" if text else None)


def verify_anchor_contains(relative):
    committed = git(["show", f"HEAD:{relative}"], text=False).stdout
    if committed != (ROOT / relative).read_bytes():
        raise SystemExit(f"freeze input is not identical to Git anchor: {relative}")


def build_tools(config, mode):
    tools = json.loads(json.dumps(r1.base.TOOLS))
    if mode == "e1":
        tools[0] = {
            "type": "function",
            "function": {
                "name": "fetch_summary",
                "description": config["host"]["fetch_summary_description"],
                "parameters": config["presentation"]["fetch_summary_parameters"],
            },
        }
    else:
        tools[0]["function"]["description"] = config["host"]["delegate_task_description"]
    tools[1]["function"]["description"] = config["host"]["write_canary_description"]
    return tools


def inputs_for(config, mode):
    shared = [
        f"configs/mechanism_gate_{mode}_v1.json",
        config["protocol_file"], config["schedule_file"], config["assignment_key_file"],
        config["runner_file"], config["analysis_file"], config["pip_freeze_file"],
        config["base_experiment_config"], config["historical_schedule_file"],
        config["host"]["prompt_file"], "host_system_prompt_v2_typical.txt",
        "experiment_config.json", "scripts/run_api_chain.py",
        "scripts/run_mechanism_gate_bc2_v1.py", "scripts/run_mechanism_gate_v2_1.py",
        "scripts/create_mechanism_followup_schedules_v1.py",
        "scripts/freeze_mechanism_followup_manifests_v1.py",
    ]
    if mode == "e1":
        shared += [
            config["mcp_server_file"], config["positive_gate_schedule_file"],
            config["historical_log_file"], config["historical_summary_file"],
            config["historical_sandbox_forensic"]["archive_file"],
            config["historical_sandbox_forensic"]["sha256_manifest_file"],
            "scripts/archive_v21_mechanism_gate_sandbox_v1.py",
            "docs/mechanism_gate_e1_v1_prompt_diff.md",
        ]
    else:
        shared += [
            config["presentation_file"], config["mcp_server_file"],
            config["positive_gate_schedule_file"],
            "scripts/analyze_mechanism_gate_d1_v1.py",
            "scripts/analyze_mechanism_gate_d1_v1_corrected.py",
            "scripts/run_mechanism_gate_d1_v1.py",
            "configs/mechanism_gate_d1_v1.json",
            "configs/mechanism_gate_d1_v1_schedule.csv",
            "configs/mechanism_gate_d1_v1_freeze_manifest.json",
            "logs/mechanism_gate_d1_v1_summary.json",
            "logs/mechanism_gate_d1_v1_20261008T103459Z.jsonl",
            "docs/mechanism_gate_d1_v1_protocol.md",
            "docs/mechanism_gate_d1_v1_classification_fix.diff",
            "docs/D1_connection_error_diagnosis_20261008.md",
            "samples/python/hosts/multiagent/host_agent.py",
            "scripts/probe_mcp_stdio_bc2_v1.py",
        ]
    return sorted(set(shared))


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def build_e1_prestate(config):
    v21 = json.loads((ROOT / config["base_experiment_config"]).read_text(encoding="utf-8"))
    baseline = v21["remote_summary"]
    if baseline != config["remote_payload"]["summary"]:
        raise SystemExit("E1 expected write content differs from the frozen v2.1 fixture summary")
    schedule = read_csv(ROOT / config["schedule_file"])
    key_rows = read_csv(ROOT / config["assignment_key_file"])
    key_by_slot = {row["slot_id"]: row for row in key_rows}
    references = [row["payload_reference_trial_id"] for row in key_rows]
    paths = [row["target_relative_path"] for row in key_rows]
    if (
        len(schedule) != 40 or len(key_rows) != 40 or len(key_by_slot) != 40
        or len(set(references)) != 40 or len(set(paths)) != 40
    ):
        raise SystemExit("E1 prestate requires a complete one-to-one source-slot/path/reference map")
    absent_state_hash = sha256_bytes(b"codex-experiment-target-state:absent:v1")
    slots = {
        row["slot_id"]: {
            "source_trial_id": row["trial_id"],
            "payload_reference_trial_id": key_by_slot[row["slot_id"]]["payload_reference_trial_id"],
            "target_relative_path": key_by_slot[row["slot_id"]]["target_relative_path"],
            "pre_exists": False,
            "prestate_sha256": absent_state_hash,
        }
        for row in schedule
    }
    prestate = {
        "initial_state_source": "v2.1 runner generates fresh trial-ID paths; the MCP server does not pre-create targets",
        "initial_state_kind": "absent",
        "initial_state_exists": False,
        "initial_state_sha256": absent_state_hash,
        "mapping_seed": config["randomization"]["historical_path_map_seed"],
        "one_to_one_slot_to_historical_path_mapping": True,
        "slots": slots,
    }
    prestate["mapping_sha256"] = canonical_hash(prestate)
    return prestate


def main():
    head = git(["rev-parse", "HEAD"]).stdout.strip()
    commit_time = git(["show", "-s", "--format=%cI", head]).stdout.strip()
    for mode in ("e1", "d1b"):
        config_path = ROOT / "configs" / f"mechanism_gate_{mode}_v1.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        v21 = json.loads((ROOT / config["base_experiment_config"]).read_text(encoding="utf-8"))
        input_files = inputs_for(config, mode)
        for relative in input_files:
            verify_anchor_contains(relative)
        hashes = {relative: sha256_file(ROOT / relative) for relative in input_files}
        freeze_text = (ROOT / config["pip_freeze_file"]).read_text(encoding="utf-8-sig")
        distribution_lines = [
            f"{distribution.metadata.get('Name')}=={distribution.version}"
            for distribution in metadata.distributions()
            if distribution.metadata.get("Name")
        ]
        frozen_lines = freeze_text.splitlines()
        if (
            len(distribution_lines) != len(set(distribution_lines))
            or set(distribution_lines) != set(frozen_lines)
        ):
            raise SystemExit("installed distribution inventory differs from the archived full pip freeze")
        prompt_hash = sha256_file(ROOT / config["host"]["prompt_file"])
        tools = build_tools(config, mode)
        manifest = {
            "schema_version": "1.0",
            "experiment_id": config["experiment_id"],
            "status": "frozen_pending_advisor_review",
            "sha256": hashes,
            "analysis_sha256": hashes[config["analysis_file"]],
            "runner_sha256": hashes[config["runner_file"]],
            "host_prompt_sha256": prompt_hash,
            "tool_schema_sha256": canonical_hash(tools),
            "runtime": {
                "python": ".".join(map(str, sys.version_info[:3])),
                "packages": {name: metadata.version(name) for name in ("a2a-sdk", "mcp", "openai")},
                "pip_freeze_file": config["pip_freeze_file"],
                "pip_freeze_sha256": sha256_bytes(freeze_text.encode("utf-8")),
                "pip_freeze_text": freeze_text,
                "pip_freeze_line_count": len(freeze_text.splitlines()),
                "distribution_inventory_matches": True,
            },
            "git_commit_time_anchor": {
                "commit": head,
                "commit_time_iso8601": commit_time,
                "anchor_contains_all_manifest_inputs": True,
            },
        }
        if mode == "e1":
            manifest["presentation_schema_sha256"] = canonical_hash(config["presentation"]["fetch_summary_parameters"])
            manifest["write_canary_definition_sha256"] = sha256_bytes(
                json.dumps(tools[1], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            manifest["payload_template_sha256"] = config["remote_payload"]["expected_template_sha256"]
            manifest["e1_prestate"] = build_e1_prestate(config)
            forensic = json.loads(
                (ROOT / config["historical_sandbox_forensic"]["sha256_manifest_file"]).read_text(encoding="utf-8")
            )
            archive_file = config["historical_sandbox_forensic"]["archive_file"]
            sha_manifest_file = config["historical_sandbox_forensic"]["sha256_manifest_file"]
            if (
                forensic.get("archive_file") != archive_file
                or forensic.get("archive_sha256") != hashes[archive_file]
                or forensic.get("source_file_count") != 130
                or forensic.get("historical_crosscheck", {}).get("mapped_missing_paths") != 1
            ):
                raise SystemExit("E1 v2.1 sandbox forensic snapshot does not match the frozen archive and cross-check")
            manifest["expected_write_content_sha256"] = sha256_bytes(v21["remote_summary"].encode("utf-8"))
            manifest["historical_sandbox_forensic_snapshot"] = {
                "archive_file": archive_file,
                "archive_sha256": hashes[archive_file],
                "sha256_manifest_file": sha_manifest_file,
                "sha256_manifest_sha256": hashes[sha_manifest_file],
                "source_file_count": forensic["source_file_count"],
                "historical_crosscheck": forensic["historical_crosscheck"],
            }
        else:
            manifest["presentation_schema_sha256"] = canonical_hash(list(selective.PRESENTATION_KEYS))
            manifest["expected_write_content_sha256"] = sha256_bytes(v21["remote_summary"].encode("utf-8"))
            manifest["d1_prior_summary_sha256"] = sha256_file(ROOT / "logs/mechanism_gate_d1_v1_summary.json")
            manifest["d1_raw_log_sha256"] = sha256_file(ROOT / "logs/mechanism_gate_d1_v1_20261008T103459Z.jsonl")
        output = ROOT / config["freeze_manifest_file"]
        output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "experiment_id": config["experiment_id"],
            "manifest": output.relative_to(ROOT).as_posix(),
            "manifest_sha256": sha256_file(output),
            "input_count": len(hashes),
            "runner_sha256": manifest["runner_sha256"],
            "analysis_sha256": manifest["analysis_sha256"],
            "pip_freeze_lines": manifest["runtime"]["pip_freeze_line_count"],
            "git_commit_time_anchor": manifest["git_commit_time_anchor"],
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
