"""Unified deterministic control-plane runner for Twin Stars."""

from __future__ import annotations

from argparse import ArgumentParser
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import subprocess
import sys

from migrate_project import atomic_write, find_state, migrate_project
from validate_workflow_state import ALL_STATES, DIRECTION_PROFILES, GATE_STATUS, SCHEMA_VERSION, validate_state


SKILL_ROOT = Path(__file__).resolve().parents[1]
REFERENCES = SKILL_ROOT / "references"
PROJECT_DIRECTORIES = (
    "inputs", "control", "research", "strategy", "prompts", "concepts", "production",
    "qa", "review", "outputs", "temp", "history", "workstreams/D1", "workstreams/D2", "workstreams/D3",
    "workstreams/research/market", "workstreams/research/references", "workstreams/production",
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def print_json(value):
    print(json.dumps(value, ensure_ascii=False))


def write_state(path, state, project_root):
    revision = max(state["state_revision"] - 1, 0)
    history = project_root / "history" / "state" / f"workflow_state.r{revision:06d}.json"
    history.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if history.exists():
            raise ValueError(f"state history already exists: {history}")
        history.write_bytes(path.read_bytes())
    atomic_write(path, state)


def assert_revision(state, expected):
    if state.get("state_revision") != expected:
        raise ValueError(f"stale state revision: expected {expected}, found {state.get('state_revision')}")


def command_init(args):
    root = args.project_root.resolve()
    targets = {
        root / "control" / "project_brief.json": "project-brief.template.json",
        root / "control" / "project_policy.json": "project-policy.template.json",
        root / "control" / "workflow_state.json": "workflow-state.template.json",
    }
    conflicts = [str(path) for path in targets if path.exists()]
    if conflicts:
        raise ValueError(f"refusing to overwrite existing control files: {conflicts}")
    for name in PROJECT_DIRECTORIES:
        (root / name).mkdir(parents=True, exist_ok=True)

    now = utc_now()
    brief = load_json(REFERENCES / targets[root / "control" / "project_brief.json"])
    policy = load_json(REFERENCES / targets[root / "control" / "project_policy.json"])
    state = load_json(REFERENCES / targets[root / "control" / "workflow_state.json"])
    for record in (brief, policy, state):
        record["project_id"] = args.project_id
    brief.update({"project_name": args.project_name or args.project_id, "run_profile": args.profile, "input_root": str((root / "inputs").resolve()), "workspace_root": str(root), "updated_at": now})
    policy["updated_at"] = now
    state.update({"run_profile": args.profile, "updated_at": now, "current_execution_id": "EXEC-001"})
    inventory = {"schema_version": SCHEMA_VERSION, "project_id": args.project_id, "source_read_only": True, "source_files": [], "updated_at": now}

    atomic_write(root / "control" / "project_brief.json", brief)
    atomic_write(root / "control" / "project_policy.json", policy)
    atomic_write(root / "control" / "workflow_state.json", state)
    atomic_write(root / "inputs" / "input_inventory.json", inventory)
    errors = validate_state(state, root)
    if errors:
        raise ValueError(f"generated state failed validation: {errors}")
    print_json({"status": "initialized", "schema_version": SCHEMA_VERSION, "project_root": str(root), "state_revision": 0})
    return 0


def command_validate(args):
    root = args.project_root.resolve()
    state_path = find_state(root, args.state)
    errors = validate_state(load_json(state_path), root)
    report = {"schema_version": SCHEMA_VERSION, "status": "pass" if not errors else "fail", "state_path": str(state_path), "errors": errors}
    if args.report:
        atomic_write(args.report.resolve(), report)
    print_json(report)
    return 1 if errors else 0


def command_status(args):
    root = args.project_root.resolve()
    state_path = find_state(root, args.state)
    state = load_json(state_path)
    errors = validate_state(state, root)
    print_json({
        "schema_version": state.get("schema_version"),
        "status": "valid" if not errors else "invalid",
        "project_id": state.get("project_id"),
        "run_profile": state.get("run_profile"),
        "state": state.get("state"),
        "state_revision": state.get("state_revision"),
        "active_direction_ids": state.get("active_direction_ids"),
        "support_agents": [{"agent_name": run.get("agent_name"), "status": run.get("status")} for run in state.get("support_agent_runs", []) if isinstance(run, dict)],
        "active_agents": [{"agent_name": run.get("agent_name"), "direction_id": run.get("direction_id"), "asset_ids": run.get("asset_ids", []), "status": run.get("status")} for run in state.get("active_agent_runs", []) if isinstance(run, dict)],
        "barrier_status": state.get("barrier_status"),
        "blockers": state.get("blockers"),
        "next_action": state.get("next_action"),
        "errors": errors,
    })
    return 1 if errors else 0


def command_transition(args):
    root = args.project_root.resolve()
    state_path = find_state(root, args.state)
    state = load_json(state_path)
    assert_revision(state, args.expect_revision)
    current_errors = validate_state(state, root)
    if current_errors:
        raise ValueError(f"current state is invalid; migrate or repair first: {current_errors}")

    candidate = deepcopy(state)
    current = candidate["state"]
    if current.startswith("awaiting_"):
        if not args.user_confirmation:
            raise ValueError(f"leaving {current} requires --user-confirmation")
        candidate["confirmation_history"].append({
            "gate": current,
            "status": "approved",
            "source": "explicit_user_confirmation",
            "raw_text": args.user_confirmation,
            "recorded_at": utc_now(),
        })
    if args.to in {"blocked", "failed_attempt_limit"} and not args.reason:
        raise ValueError(f"entering {args.to} requires --reason")
    for blocker in args.resolve_blocker:
        if blocker not in candidate["blockers"]:
            raise ValueError(f"cannot resolve unknown blocker: {blocker}")
        candidate["blockers"].remove(blocker)
    if current in {"blocked", "failed_attempt_limit"} and args.to == "resumed" and candidate["blockers"]:
        raise ValueError("all blockers must be resolved before resuming")
    if current == "resumed" and args.to != candidate.get("resume_state"):
        raise ValueError(f"resumed state must return to {candidate.get('resume_state')}")

    candidate.update({
        "previous_state": current,
        "state": args.to,
        "state_revision": candidate["state_revision"] + 1,
        "updated_at": utc_now(),
        "next_action": args.next_action,
        "advance_allowed": False,
    })
    if args.to.startswith("awaiting_"):
        candidate.update({"confirmation_required": True, "confirmation_status": "pending"})
    elif args.to == "complete":
        candidate.update({"confirmation_required": False, "confirmation_status": "approved"})
    else:
        candidate.update({"confirmation_required": False, "confirmation_status": "not_required"})
    if args.reason and args.reason not in candidate["blockers"]:
        candidate["blockers"].append(args.reason)
    if args.to in {"blocked", "failed_attempt_limit"}:
        candidate["resume_state"] = current
    elif current not in {"blocked", "failed_attempt_limit", "resumed"} and candidate.get("previous_state") != "resumed":
        candidate["resume_state"] = None

    errors = validate_state(candidate, root)
    if errors:
        raise ValueError(f"transition rejected: {errors}")
    write_state(state_path, candidate, root)
    print_json({"status": "transitioned", "from": current, "to": args.to, "state_revision": candidate["state_revision"]})
    return 0


def command_gate(args):
    root = args.project_root.resolve()
    state_path = find_state(root, args.state)
    state = load_json(state_path)
    assert_revision(state, args.expect_revision)
    if not GATE_STATUS.fullmatch(args.status):
        raise ValueError(f"invalid gate status: {args.status}")
    if args.name not in state.get("hard_gate_status", {}):
        raise ValueError(f"unknown hard gate: {args.name}")
    candidate = deepcopy(state)
    candidate["hard_gate_status"][args.name] = args.status
    candidate["state_revision"] += 1
    candidate["updated_at"] = utc_now()
    if args.status.startswith(("fail", "blocked")):
        if not args.reason:
            raise ValueError("failing or blocking a gate requires --reason")
        previous = candidate["state"]
        if previous in ALL_STATES and previous not in {"blocked", "complete"}:
            candidate.update({"state": "blocked", "previous_state": previous, "resume_state": previous, "confirmation_required": False, "confirmation_status": "not_required", "advance_allowed": False})
        if args.reason not in candidate["blockers"]:
            candidate["blockers"].append(args.reason)
    errors = validate_state(candidate, root)
    if errors:
        raise ValueError(f"gate update rejected: {errors}")
    write_state(state_path, candidate, root)
    print_json({"status": "gate_recorded", "gate": args.name, "value": args.status, "state_revision": candidate["state_revision"]})
    return 0


def production_runs(asset_direction_map, execution_id):
    by_direction = {}
    for asset_id, direction in sorted(asset_direction_map.items()):
        by_direction.setdefault(direction, []).append(asset_id)
    worker_count = min(3, len(asset_direction_map))
    shard_counts = {direction: 1 for direction in by_direction}
    while sum(shard_counts.values()) < worker_count:
        eligible = [direction for direction, assets in by_direction.items() if shard_counts[direction] < len(assets)]
        direction = max(eligible, key=lambda item: (len(by_direction[item]) / shard_counts[item], item))
        shard_counts[direction] += 1

    runs = []
    for direction in sorted(by_direction):
        buckets = [[] for _ in range(shard_counts[direction])]
        for index, asset_id in enumerate(by_direction[direction]):
            buckets[index % len(buckets)].append(asset_id)
        for index, asset_ids in enumerate(buckets, 1):
            name = f"production_{direction.lower()}_{index:02d}"
            workstream = f"workstreams/production/{name}"
            runs.append({
                "execution_id": execution_id,
                "agent_name": name,
                "direction_id": direction,
                "asset_ids": asset_ids,
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
            })
    return runs


def command_authorize_production(args):
    root = args.project_root.resolve()
    state_path = find_state(root, args.state)
    state = load_json(state_path)
    assert_revision(state, args.expect_revision)
    errors = validate_state(state, root)
    if errors:
        raise ValueError(f"current state is invalid; migrate or repair first: {errors}")
    if state.get("state") != "awaiting_production_approval":
        raise ValueError("authorize-production requires awaiting_production_approval")

    directions = sorted(set(args.direction))
    asset_ids = list(dict.fromkeys(args.asset))
    binding = state.get("asset_plan_binding", {})
    plan_path = (root / str(binding.get("path") or "")).resolve()
    plan = load_json(plan_path)
    plan_map = {item.get("asset_id"): item.get("direction_id") for item in plan.get("assets", []) if isinstance(item, dict)}
    missing = [asset_id for asset_id in asset_ids if asset_id not in plan_map]
    if missing:
        raise ValueError(f"assets not found in approved plan: {missing}")
    asset_direction_map = {asset_id: plan_map[asset_id] for asset_id in asset_ids}
    if set(asset_direction_map.values()) != set(directions):
        raise ValueError("approved directions must exactly match selected asset directions")

    candidate = deepcopy(state)
    old_execution = candidate["current_execution_id"]
    candidate["agent_run_history"].extend(candidate.get("active_agent_runs", []))
    execution_id = f"PROD-r{candidate['state_revision'] + 1:06d}"
    runs = production_runs(asset_direction_map, execution_id)
    now = utc_now()
    candidate.update({
        "run_profile": "production_selected",
        "previous_state": "awaiting_production_approval",
        "state": "production_active",
        "state_revision": candidate["state_revision"] + 1,
        "updated_at": now,
        "current_execution_id": execution_id,
        "active_direction_id": directions[0] if len(directions) == 1 else None,
        "active_direction_ids": directions,
        "active_asset_ids": asset_ids,
        "active_asset_direction_map": asset_direction_map,
        "active_agent_runs": runs,
        "agent_execution": {
            "mode": "single_agent" if len(runs) == 1 else "parallel_agents",
            "fallback_reason": None,
            "fallback_evidence": None,
            "parallel_window": {"started_at": None, "all_agents_active_at": None, "first_agent_completed_at": None},
        },
        "execution_authorization": None,
        "confirmation_required": False,
        "confirmation_status": "not_required",
        "advance_allowed": False,
        "resume_state": None,
        "next_action": f"dispatch {len(runs)} production agent shard(s) concurrently",
    })
    candidate["barrier_status"]["formal_production"] = "pending"
    candidate["confirmation_history"].append({
        "gate": "awaiting_production_approval",
        "status": "approved",
        "source": "explicit_user_confirmation",
        "raw_text": args.user_confirmation,
        "recorded_at": now,
        "approved_direction_ids": directions,
        "approved_asset_ids": asset_ids,
        "superseded_execution_id": old_execution,
    })
    errors = validate_state(candidate, root)
    if errors:
        raise ValueError(f"production authorization rejected: {errors}")
    write_state(state_path, candidate, root)
    print_json({"status": "production_authorized", "directions": directions, "assets": asset_ids, "agents": [run["agent_name"] for run in runs], "state_revision": candidate["state_revision"]})
    return 0


def command_migrate(args):
    report = migrate_project(args.project_root, args.state, args.apply)
    print_json(report)
    return 2 if report["status"] == "manual_review_required" else 0


def command_eval(_args):
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(SKILL_ROOT / "evals"), "-p", "test_*.py"], check=False)
    return result.returncode


def parser():
    top = ArgumentParser(description=__doc__)
    commands = top.add_subparsers(dest="command", required=True)
    commands.add_parser("v6", help="V6 project lifecycle and brand memory; use v6 --help")

    init = commands.add_parser("init")
    init.add_argument("--project-root", required=True, type=Path)
    init.add_argument("--project-id", required=True)
    init.add_argument("--project-name")
    init.add_argument("--profile", choices=sorted(DIRECTION_PROFILES), default="auto_concept_fast")
    init.set_defaults(func=command_init)

    for name, func in (("validate", command_validate), ("status", command_status)):
        item = commands.add_parser(name)
        item.add_argument("--project-root", required=True, type=Path)
        item.add_argument("--state", type=Path)
        if name == "validate":
            item.add_argument("--report", type=Path)
        item.set_defaults(func=func)

    transition = commands.add_parser("transition")
    transition.add_argument("--project-root", required=True, type=Path)
    transition.add_argument("--state", type=Path)
    transition.add_argument("--expect-revision", required=True, type=int)
    transition.add_argument("--to", required=True, choices=sorted(ALL_STATES))
    transition.add_argument("--next-action", required=True)
    transition.add_argument("--user-confirmation")
    transition.add_argument("--reason")
    transition.add_argument("--resolve-blocker", action="append", default=[])
    transition.set_defaults(func=command_transition)

    gate = commands.add_parser("gate")
    gate.add_argument("--project-root", required=True, type=Path)
    gate.add_argument("--state", type=Path)
    gate.add_argument("--expect-revision", required=True, type=int)
    gate.add_argument("--name", required=True)
    gate.add_argument("--status", required=True)
    gate.add_argument("--reason")
    gate.set_defaults(func=command_gate)

    production = commands.add_parser("authorize-production")
    production.add_argument("--project-root", required=True, type=Path)
    production.add_argument("--state", type=Path)
    production.add_argument("--expect-revision", required=True, type=int)
    production.add_argument("--direction", action="append", required=True, choices=("D1", "D2", "D3"))
    production.add_argument("--asset", action="append", required=True)
    production.add_argument("--user-confirmation", required=True)
    production.set_defaults(func=command_authorize_production)

    migrate = commands.add_parser("migrate")
    migrate.add_argument("--project-root", required=True, type=Path)
    migrate.add_argument("--state", type=Path)
    migrate.add_argument("--apply", action="store_true")
    migrate.set_defaults(func=command_migrate)

    evaluate = commands.add_parser("eval")
    evaluate.set_defaults(func=command_eval)
    return top


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "v6":
        from v6_runtime import main as v6_main
        return v6_main(sys.argv[2:])
    args = parser().parse_args()
    try:
        if args.command in {"init", "transition", "gate", "authorize-production"} or args.command == "migrate" and args.apply:
            from v6_storage import file_lock
            with file_lock(args.project_root.resolve() / ".twin-stars.lock"):
                return args.func(args)
        return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print_json({"schema_version": SCHEMA_VERSION, "status": "fail", "errors": [str(exc)]})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
