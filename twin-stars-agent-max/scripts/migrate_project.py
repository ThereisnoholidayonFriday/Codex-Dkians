"""Conservatively migrate Twin Stars workflow state to V5.1."""

from __future__ import annotations

from argparse import ArgumentParser
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil

from validate_workflow_state import (
    BARRIER_KEYS,
    DIRECTION_AGENTS,
    DIRECTION_PROFILES,
    HARD_GATE_KEYS,
    PATH,
    SCHEMA_VERSION,
    SUPPORT_AGENTS,
)


PROFILE_ALIASES = {"auto_concept": "auto_concept_fast"}
SAFE_INITIAL_STATE = "intake_active"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def planned_run(direction, execution_id):
    rounds = {name: {"started_at": None, "completed_at": None, "handoff_sha256": ""} for name in ("round_a", "round_b", "round_c")}
    return {
        "execution_id": execution_id,
        "agent_name": DIRECTION_AGENTS[direction],
        "direction_id": direction,
        "workstream_path": f"workstreams/{direction}",
        "status": "planned",
        "started_at": None,
        "completed_at": None,
        "last_handoff_at": None,
        "input_lock_path": f"workstreams/{direction}/input-lock.json",
        "input_lock_sha256": "",
        "canonical_write_paths": [],
        "handoff_path": f"workstreams/{direction}/handoff.json",
        "handoff_sha256": "",
        "round_status": {name: "pending" for name in rounds},
        "round_timing": rounds,
    }


def planned_support_run(name, execution_id):
    role, workstream = SUPPORT_AGENTS[name]
    return {
        "execution_id": execution_id,
        "agent_name": name,
        "role": role,
        "workstream_path": workstream,
        "status": "planned",
        "started_at": None,
        "completed_at": None,
        "last_handoff_at": None,
        "input_lock_path": f"{workstream}/input-lock.json",
        "input_lock_sha256": "",
        "canonical_write_paths": [],
        "handoff_path": f"{workstream}/handoff.json",
        "handoff_sha256": "",
    }


def migrate_state(data, timestamp=None):
    timestamp = timestamp or utc_now()
    migrated = deepcopy(data)
    source_version = migrated.get("schema_version") or "unversioned"
    changes = []
    manual = []

    profile = PROFILE_ALIASES.get(migrated.get("run_profile"), migrated.get("run_profile", "auto_concept_fast"))
    if profile != migrated.get("run_profile"):
        changes.append(f"run_profile:{migrated.get('run_profile')}->{profile}")
    migrated["run_profile"] = profile
    migrated["schema_version"] = SCHEMA_VERSION
    migrated["state_revision"] = migrated.get("state_revision", 0) if isinstance(migrated.get("state_revision", 0), int) else 0
    migrated.setdefault("migration_history", [])
    if not isinstance(migrated["migration_history"], list):
        migrated["migration_history"] = []
        manual.append("invalid migration_history replaced")

    list_defaults = {
        "completed_artifacts": [], "pending_items": [], "blockers": [], "confirmation_history": [],
        "open_questions": [], "decision_history": [], "active_asset_ids": [], "agent_run_history": [],
    }
    for key, default in list_defaults.items():
        if key not in migrated:
            migrated[key] = deepcopy(default)
            changes.append(f"added:{key}")
    migrated.setdefault("execution_authorization", None)
    migrated.setdefault("active_direction_id", None)
    migrated.setdefault("active_direction_ids", [migrated["active_direction_id"]] if migrated.get("active_direction_id") else [])
    migrated.setdefault("active_asset_direction_map", {})
    migrated.setdefault("asset_plan_binding", {"path": "strategy/asset_plan.json", "sha256": ""})
    migrated.setdefault("market_research_binding", {"path": "strategy/market_research_index.json", "sha256": ""})
    migrated.setdefault("reference_library_binding", {"path": "research/reference_library.json", "sha256": ""})
    migrated.setdefault("revision_context", None)
    migrated.setdefault("resume_state", None)

    gates = migrated.get("hard_gate_status")
    gates = gates if isinstance(gates, dict) else {}
    for gate in HARD_GATE_KEYS:
        gates.setdefault(gate, "not_applicable" if gate == "cast_fidelity" else "pending")
    migrated["hard_gate_status"] = gates

    execution_id = migrated.get("current_execution_id") or f"MIG-{timestamp.replace(':', '').replace('-', '').replace('+', '')[:15]}"
    migrated["current_execution_id"] = execution_id
    barriers = migrated.get("barrier_status")
    barriers = barriers if isinstance(barriers, dict) else {}
    for key in BARRIER_KEYS:
        barriers.setdefault(key, "pending")
    migrated["barrier_status"] = barriers
    migrated.setdefault("support_agent_execution", {"mode": "parallel_agents", "fallback_reason": None, "fallback_evidence": None, "parallel_window": {"started_at": None, "all_agents_active_at": None, "first_agent_completed_at": None}})
    migrated.setdefault("support_agent_runs", [planned_support_run(name, execution_id) for name in SUPPORT_AGENTS])
    migrated.setdefault("agent_run_history", [])
    if profile in DIRECTION_PROFILES:
        migrated.setdefault("agent_execution", {"mode": "parallel_agents", "fallback_reason": None, "fallback_evidence": None, "parallel_window": {"started_at": None, "all_agents_active_at": None, "first_agent_completed_at": None}})
        migrated.setdefault("active_agent_runs", [planned_run(direction, execution_id) for direction in sorted(DIRECTION_AGENTS)])
    else:
        migrated.setdefault("agent_execution", {"mode": "single_direction_agent", "fallback_reason": None, "fallback_evidence": None, "parallel_window": {}})
        migrated.setdefault("active_agent_runs", [])
        if not migrated.get("active_direction_id") or not migrated.get("active_asset_ids"):
            manual.append("single-direction scope cannot be inferred")

    original_state = migrated.get("state", SAFE_INITIAL_STATE)
    if original_state != SAFE_INITIAL_STATE:
        manual.append("reconstruct agent provenance, barriers, and file hashes from project evidence")
        if original_state in PATH[:-1]:
            migrated["state"] = "blocked"
            migrated["previous_state"] = original_state
            migrated["resume_state"] = original_state
            migrated["confirmation_required"] = False
            migrated["confirmation_status"] = "not_required"
        elif original_state in {"blocked", "failed_attempt_limit"}:
            migrated["resume_state"] = migrated.get("previous_state")
        migrated["advance_allowed"] = False
        blocker = "migration_review_required: provenance and V5.1 semantic bindings must be reconstructed"
        if blocker not in migrated["blockers"]:
            migrated["blockers"].append(blocker)
        migrated["next_action"] = "Review migration report and reconstruct missing V5.1 bindings; do not advance automatically."
    else:
        migrated["state"] = SAFE_INITIAL_STATE
        migrated["previous_state"] = None
        migrated["resume_state"] = None
        migrated["advance_allowed"] = False
        migrated["confirmation_required"] = False
        migrated["confirmation_status"] = "not_required"
        migrated.setdefault("next_action", "inventory supplied files")

    migrated["updated_at"] = timestamp
    migrated["migration_history"].append({
        "from_schema_version": source_version,
        "to_schema_version": SCHEMA_VERSION,
        "migrated_at": timestamp,
        "tool": "migrate_project.py",
        "status": "manual_review_required" if manual else "complete",
    })
    changes.append(f"schema_version:{source_version}->{SCHEMA_VERSION}")
    return migrated, {"source_version": source_version, "target_version": SCHEMA_VERSION, "changes": changes, "manual_review": manual}


def find_state(project_root, explicit=None):
    if explicit:
        return Path(explicit).resolve()
    candidates = [project_root / "control" / "workflow_state.json", project_root / "workflow_state.json"]
    found = [path for path in candidates if path.is_file()]
    if len(found) != 1:
        raise ValueError(f"expected one workflow_state.json, found {len(found)}")
    return found[0]


def inspect_artifact_versions(project_root):
    rows = []
    for path in project_root.rglob("asset_plan.json"):
        try:
            version = json.loads(path.read_text(encoding="utf-8")).get("schema_version") or "unversioned"
        except (OSError, json.JSONDecodeError):
            version = "unreadable"
        if version != SCHEMA_VERSION:
            rows.append({"path": str(path.relative_to(project_root)), "schema_version": version, "action": "manual_semantic_migration_required"})
    return rows


def atomic_write(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def migrate_project(project_root, state_path=None, apply=False):
    project_root = Path(project_root).resolve()
    state_path = find_state(project_root, state_path)
    data = json.loads(state_path.read_text(encoding="utf-8"))
    timestamp = utc_now()
    migrated, state_report = migrate_state(data, timestamp)
    artifact_report = inspect_artifact_versions(project_root)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "manual_review_required" if state_report["manual_review"] or artifact_report else "ready",
        "applied": bool(apply),
        "state_path": str(state_path),
        "state": state_report,
        "artifacts": artifact_report,
    }
    if apply:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup_dir = project_root / "history" / "migrations" / stamp
        backup_dir.mkdir(parents=True, exist_ok=False)
        shutil.copy2(state_path, backup_dir / "workflow_state.pre-migration.json")
        atomic_write(state_path, migrated)
        report_path = project_root / "control" / "migration_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(report_path, report)
    return report


def main():
    parser = ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = migrate_project(args.project_root, args.state, args.apply)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(args.report, report)
        print(json.dumps(report, ensure_ascii=False))
        return 2 if report["status"] == "manual_review_required" else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "status": "fail", "errors": [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
