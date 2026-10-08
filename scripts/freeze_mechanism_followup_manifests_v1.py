import hashlib
import json
import subprocess
import sys
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
            "docs/mechanism_gate_e1_v1_prompt_diff.md",
        ]
    else:
        shared += [
            config["presentation_file"], config["mcp_server_file"],
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


def main():
    head = git(["rev-parse", "HEAD"]).stdout.strip()
    commit_time = git(["show", "-s", "--format=%cI", head]).stdout.strip()
    for mode in ("e1", "d1b"):
        config_path = ROOT / "configs" / f"mechanism_gate_{mode}_v1.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
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
        else:
            manifest["presentation_schema_sha256"] = canonical_hash(list(selective.PRESENTATION_KEYS))
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
