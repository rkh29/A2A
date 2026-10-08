import csv
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_mechanism_gate_bc2_v1 as r1  # installs the frozen A2A compatibility aliases
import run_mechanism_gate_v2_1 as mechanism


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def new_id(rng, used):
    while True:
        candidate = f"t-{rng.getrandbits(64):016x}"
        if candidate not in used:
            used.add(candidate)
            return candidate


def block_slots(schedule_seed, opaque_seed):
    schedule_rng = random.Random(schedule_seed)
    opaque_rng = random.Random(opaque_seed)
    used = set()
    block_order = list(range(1, 21))
    schedule_rng.shuffle(block_order)
    rows = []
    for replicate, block in enumerate(block_order, start=1):
        models = ["primary", "secondary"]
        schedule_rng.shuffle(models)
        for model_key in models:
            rows.append({
                "block": block,
                "replicate": replicate,
                "model_key": model_key,
                "slot_id": new_id(opaque_rng, used),
                "trial_id": new_id(opaque_rng, used),
                "retry_trial_id_1": new_id(opaque_rng, used),
                "retry_trial_id_2": new_id(opaque_rng, used),
            })
    return rows


def artifact_text(v21, target_relative_path):
    return mechanism.build_payload(v21, "C", target_relative_path)["artifact_text"]


def make_experiment(experiment_id, schedule_seed, opaque_seed, target_seed, v21, historical_rows, historical_log_hashes):
    rows = block_slots(schedule_seed, opaque_seed)
    run_index = 1
    model_paths = {
        "primary": [
            row["trial_id"] for row in historical_rows
            if row["cell"] == "C" and row["model"] == "primary" and row["user_condition"] == "user_silence"
        ],
        "secondary": [
            row["trial_id"] for row in historical_rows
            if row["cell"] == "C" and row["model"] == "secondary" and row["user_condition"] == "user_silence"
        ],
    }
    target_rng = random.Random(target_seed)
    for values in model_paths.values():
        target_rng.shuffle(values)
    next_path = {key: 0 for key in model_paths}

    schedule_rows, key_rows = [], []
    for row in rows:
        model_key = row["model_key"]
        if experiment_id == "mechanism-gate-e1-v1":
            ref_id = model_paths[model_key][next_path[model_key]]
            next_path[model_key] += 1
            target_path = f"typical_v2/mechanism_gate/{ref_id}.txt"
            expected_hash = historical_log_hashes[ref_id]
            actual_hash = hashlib.sha256(artifact_text(v21, target_path).encode("utf-8")).hexdigest()
            if actual_hash != expected_hash:
                raise SystemExit(f"E1 C payload is not byte-identical to historical v2.1 row {ref_id}")
        else:
            ref_id = ""
            target_path = ""
            expected_hash = ""
        schedule_rows.append({
            "run_index": run_index,
            "block": row["block"],
            "replicate": row["replicate"],
            "slot_id": row["slot_id"],
            "trial_id": row["trial_id"],
            "retry_trial_id_1": row["retry_trial_id_1"],
            "retry_trial_id_2": row["retry_trial_id_2"],
            "random_seed": schedule_seed,
        })
        key_rows.append({
            "slot_id": row["slot_id"],
            "model_key": model_key,
            "cell": "C",
            "user_condition": "user_silence",
            "target_relative_path": target_path if target_path else f"typical_v2/d1b_selective_v1/{row['trial_id']}.txt",
            "payload_reference_trial_id": ref_id,
            "expected_payload_sha256": expected_hash,
        })
        run_index += 1
    if experiment_id == "mechanism-gate-e1-v1":
        references = [row["payload_reference_trial_id"] for row in key_rows]
        paths = [row["target_relative_path"] for row in key_rows]
        if len(references) != len(set(references)) or len(paths) != len(set(paths)):
            raise SystemExit("E1 historical path map must be one-to-one across all source slots")
    return schedule_rows, key_rows


def make_positive_gate(config, used):
    gate = config["positive_gate"]
    rng = random.Random(gate["schedule_seed"])
    models = [model_key for model_key, count in gate["model_counts"].items() for _ in range(count)]
    if len(models) != gate["planned_trials"]:
        raise SystemExit(f"{config['experiment_id']} positive-gate counts do not sum to planned trials")
    rng.shuffle(models)
    rows = []
    for index, model_key in enumerate(models, start=1):
        slot_id = new_id(rng, used)
        trial_id = new_id(rng, used)
        retry_1 = new_id(rng, used)
        retry_2 = new_id(rng, used)
        rows.append({
            "run_index": index,
            "slot_id": slot_id,
            "trial_id": trial_id,
            "retry_trial_id_1": retry_1,
            "retry_trial_id_2": retry_2,
            "model_key": model_key,
            "user_condition": "explicit_authorization",
            "target_relative_path": f"{gate['target_relative_directory']}/{trial_id}.txt",
            "random_seed": gate["schedule_seed"],
        })
    return rows


def main():
    e1_config_path = ROOT / "configs/mechanism_gate_e1_v1.json"
    d1b_config_path = ROOT / "configs/mechanism_gate_d1b_v1.json"
    e1 = json.loads(e1_config_path.read_text(encoding="utf-8"))
    d1b = json.loads(d1b_config_path.read_text(encoding="utf-8"))
    v21 = json.loads((ROOT / e1["base_experiment_config"]).read_text(encoding="utf-8"))
    historical_rows = read_csv(ROOT / e1["historical_schedule_file"])
    historical_log_rows = [
        json.loads(line) for line in (ROOT / e1["historical_log_file"]).read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    historical_log_hashes = {
        row["trial_id"]: row["remote_artifact_sha256"]
        for row in historical_log_rows
        if row.get("cell") == "C" and row.get("user_condition") == "user_silence"
    }

    reference_path = e1["remote_payload"]["template_reference_path"]
    reference_text = artifact_text(v21, reference_path)
    normalized_text = reference_text.replace(reference_path, "{relative_path}")
    template_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    e1["remote_payload"]["expected_template_sha256"] = template_hash

    e1_schedule, e1_key = make_experiment(
        e1["experiment_id"], e1["randomization"]["schedule_seed"],
        e1["randomization"]["opaque_id_seed"], e1["randomization"]["historical_path_map_seed"],
        v21, historical_rows, historical_log_hashes,
    )
    d1b_schedule, d1b_key = make_experiment(
        d1b["experiment_id"], d1b["randomization"]["schedule_seed"],
        d1b["randomization"]["opaque_id_seed"], 0, v21, historical_rows, historical_log_hashes,
    )

    write_csv(ROOT / e1["schedule_file"], [
        "run_index", "block", "replicate", "slot_id", "trial_id",
        "retry_trial_id_1", "retry_trial_id_2", "random_seed",
    ], e1_schedule)
    write_csv(ROOT / e1["assignment_key_file"], [
        "slot_id", "model_key", "cell", "user_condition", "target_relative_path",
        "payload_reference_trial_id", "expected_payload_sha256",
    ], e1_key)
    write_csv(ROOT / d1b["schedule_file"], [
        "run_index", "block", "replicate", "slot_id", "trial_id",
        "retry_trial_id_1", "retry_trial_id_2", "random_seed",
    ], d1b_schedule)
    write_csv(ROOT / d1b["assignment_key_file"], [
        "slot_id", "model_key", "cell", "user_condition", "target_relative_path",
        "payload_reference_trial_id", "expected_payload_sha256",
    ], d1b_key)

    used = {
        value for row in e1_schedule + d1b_schedule
        for key in ("slot_id", "trial_id", "retry_trial_id_1", "retry_trial_id_2")
        for value in (row[key],)
    }
    e1_positive_rows = make_positive_gate(e1, used)
    d1b_positive_rows = make_positive_gate(d1b, used)
    positive_columns = [
        "run_index", "slot_id", "trial_id", "retry_trial_id_1", "retry_trial_id_2",
        "model_key", "user_condition", "target_relative_path", "random_seed",
    ]
    write_csv(ROOT / e1["positive_gate_schedule_file"], positive_columns, e1_positive_rows)
    write_csv(ROOT / d1b["positive_gate_schedule_file"], positive_columns, d1b_positive_rows)

    e1_config_path.write_text(json.dumps(e1, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "generated": True,
        "api_calls_made": 0,
        "e1_slots": len(e1_schedule),
        "d1b_slots": len(d1b_schedule),
        "e1_model_counts": {key: sum(row["model_key"] == key for row in e1_key) for key in ("primary", "secondary")},
        "d1b_model_counts": {key: sum(row["model_key"] == key for row in d1b_key) for key in ("primary", "secondary")},
        "e1_payload_template_sha256": template_hash,
        "e1_payload_slot_hashes_all_match_v21_source": all(
            row["expected_payload_sha256"] == historical_log_hashes[row["payload_reference_trial_id"]]
            and row["expected_payload_sha256"] == hashlib.sha256(
                artifact_text(v21, row["target_relative_path"]).encode("utf-8")
            ).hexdigest() for row in e1_key
        ),
        "positive_gate_slots": {"e1": len(e1_positive_rows), "d1b": len(d1b_positive_rows)},
        "positive_gate_model_counts": {
            "e1": {key: sum(row["model_key"] == key for row in e1_positive_rows) for key in ("primary", "secondary")},
            "d1b": {key: sum(row["model_key"] == key for row in d1b_positive_rows) for key in ("primary", "secondary")},
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
