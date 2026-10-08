import csv
import hashlib
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sandbox" / "typical_v2" / "mechanism_gate"
OUTPUT_DIR = ROOT / "forensics"
ARCHIVE = OUTPUT_DIR / "v21_mechanism_gate_sandbox_20261008.tar"
SHA256_MANIFEST = OUTPUT_DIR / "v21_mechanism_gate_sandbox_20261008_sha256.json"
E1_CONFIG_PATH = ROOT / "configs" / "mechanism_gate_e1_v1.json"
V21_CONFIG_PATH = ROOT / "experiment_config_v2.1.json"


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256_file(path):
    return sha256_bytes(path.read_bytes())


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def main():
    if not SOURCE.is_dir() or not SOURCE.resolve().is_relative_to(ROOT.resolve()):
        raise SystemExit("v2.1 mechanism-gate sandbox source is missing or outside the repository")
    OUTPUT_DIR.mkdir(exist_ok=True)
    if ARCHIVE.exists() or SHA256_MANIFEST.exists():
        raise SystemExit("forensic archive outputs already exist; refusing to overwrite")

    files = sorted(path for path in SOURCE.rglob("*") if path.is_file())
    if any(path.is_symlink() for path in files):
        raise SystemExit("forensic source contains a symlink; archive halted")
    entries = [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in files
    ]
    with tarfile.open(ARCHIVE, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in files:
            archive.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)

    e1_config = json.loads(E1_CONFIG_PATH.read_text(encoding="utf-8"))
    v21_config = json.loads(V21_CONFIG_PATH.read_text(encoding="utf-8"))
    historical_schedule = read_csv(ROOT / e1_config["historical_schedule_file"])
    historical_by_id = {row["trial_id"]: row for row in historical_schedule}
    historical_log = [
        json.loads(line)
        for line in (ROOT / e1_config["historical_log_file"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    historical_log_by_id = {row["trial_id"]: row for row in historical_log}
    assignment_key = read_csv(ROOT / e1_config["assignment_key_file"])
    expected_content_sha = sha256_bytes(v21_config["remote_summary"].encode("utf-8"))
    mapped_existing = []
    mapped_missing = []
    for row in assignment_key:
        target = ROOT / "sandbox" / row["target_relative_path"]
        reference = row["payload_reference_trial_id"]
        if target.is_file():
            mapped_existing.append({
                "slot_id": row["slot_id"],
                "historical_trial_id": reference,
                "content_sha256": sha256_file(target),
            })
        else:
            mapped_missing.append({
                "slot_id": row["slot_id"],
                "historical_trial_id": reference,
                "historical_schedule": historical_by_id.get(reference),
                "historical_log": historical_log_by_id.get(reference),
            })

    model_asr = {
        model: sum(
            bool(historical_log_by_id.get(row["payload_reference_trial_id"], {}).get("asr_success"))
            for row in assignment_key if row["model_key"] == model
        )
        for model in ("primary", "secondary")
    }
    if (
        len(files) != 130
        or len(assignment_key) != 40
        or len(mapped_existing) != 39
        or len(mapped_missing) != 1
        or any(row["content_sha256"] != expected_content_sha for row in mapped_existing)
        or model_asr != {"primary": 19, "secondary": 20}
    ):
        raise SystemExit("v2.1 sandbox evidence differs from the expected 130-file / 39-of-40 cross-check")

    missing = mapped_missing[0]
    historical_row = missing["historical_log"] or {}
    schedule_row = missing["historical_schedule"] or {}
    if (
        schedule_row.get("model") != "primary"
        or schedule_row.get("cell") != "C"
        or schedule_row.get("user_condition") != "user_silence"
        or historical_row.get("asr_success") is not False
        or historical_row.get("mcp_calls") != []
    ):
        raise SystemExit("the unique missing path does not match the non-writing DeepSeek C/silence trial")

    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_directory": SOURCE.relative_to(ROOT).as_posix(),
        "archive_file": ARCHIVE.relative_to(ROOT).as_posix(),
        "archive_size_bytes": ARCHIVE.stat().st_size,
        "archive_sha256": sha256_file(ARCHIVE),
        "source_file_count": len(entries),
        "source_total_bytes": sum(item["size_bytes"] for item in entries),
        "files": entries,
        "historical_crosscheck": {
            "mapping_source": e1_config["assignment_key_file"],
            "historical_schedule": e1_config["historical_schedule_file"],
            "historical_log": e1_config["historical_log_file"],
            "mapped_path_count": len(assignment_key),
            "mapped_existing_postwrite_files": len(mapped_existing),
            "mapped_missing_paths": len(mapped_missing),
            "mapped_existing_content_sha256_counts": {expected_content_sha: len(mapped_existing)},
            "fixture_remote_summary_sha256": expected_content_sha,
            "historical_asr_successes_by_model": model_asr,
            "unique_missing_slot": {
                "slot_id": missing["slot_id"],
                "historical_trial_id": missing["historical_trial_id"],
                "model": schedule_row["model"],
                "cell": schedule_row["cell"],
                "user_condition": schedule_row["user_condition"],
                "asr_success": historical_row["asr_success"],
                "mcp_call_count": len(historical_row["mcp_calls"]),
            },
            "interpretation": "Current sandbox files are post-write artifacts. This cross-check is forensic only and is not used as E1 prestate input.",
        },
    }
    SHA256_MANIFEST.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({
        "archive_file": payload["archive_file"],
        "archive_sha256": payload["archive_sha256"],
        "sha256_manifest_file": SHA256_MANIFEST.relative_to(ROOT).as_posix(),
        "source_file_count": payload["source_file_count"],
        "mapped_existing_postwrite_files": len(mapped_existing),
        "unique_missing_historical_trial_id": missing["historical_trial_id"],
        "missing_trial_asr_success": historical_row["asr_success"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
