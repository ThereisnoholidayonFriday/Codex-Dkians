"""Strictly validate the Twin Stars V5.1 control-plane state."""

from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
import hashlib
import json
import re


SCHEMA_VERSION = "5.1"
PATH = (
    "intake_active", "awaiting_evidence_approval", "strategy_active",
    "awaiting_direction_approval", "sample_active", "awaiting_production_approval",
    "production_active", "qa_active", "awaiting_delivery_approval", "complete",
)
AUXILIARY = {"blocked", "revision_requested", "prompt_revision_active", "resumed", "failed_attempt_limit"}
ALL_STATES = set(PATH) | AUXILIARY
PROFILES = {"strategy_only", "auto_concept_fast", "auto_concept_full", "production_selected", "revision", "prompt_revision"}
DIRECTION_PROFILES = {"strategy_only", "auto_concept_fast", "auto_concept_full"}
SCOPED_PROFILES = {"production_selected", "revision", "prompt_revision"}
REVISION_PROFILES = {"revision", "prompt_revision"}
CONFIRMATIONS = {"not_required", "pending", "approved", "revision_requested", "rejected"}
CONFIDENCE = {"high", "medium", "low", "unknown"}
RUN_STATUSES = {"planned", "active", "completed", "blocked", "failed"}
REQUIRED = {
    "schema_version", "state_revision", "migration_history", "project_id", "run_profile", "state", "previous_state", "resume_state",
    "completed_artifacts", "pending_items", "blockers", "advance_allowed", "confirmation_required",
    "confirmation_status", "confirmation_history", "execution_authorization", "hard_gate_status",
    "current_execution_id", "active_direction_id", "active_direction_ids", "active_asset_ids", "active_asset_direction_map",
    "asset_plan_binding", "market_research_binding", "reference_library_binding", "support_agent_execution", "support_agent_runs",
    "agent_execution", "active_agent_runs", "agent_run_history", "barrier_status",
    "revision_context", "open_questions", "decision_history", "next_action", "updated_at",
}
AUTO_SCOPE = {
    "allow_research", "allow_three_directions", "allow_prompt_compilation",
    "allow_comparison_samples", "allow_concept_presentations",
}
DIRECTIONS = {"D1", "D2", "D3"}
DIRECTION_AGENTS = {"D1": "direction_d1", "D2": "direction_d2", "D3": "direction_d3"}
SUPPORT_AGENTS = {
    "market_intelligence": ("market_research", "workstreams/research/market"),
    "visual_reference_research": ("visual_reference_research", "workstreams/research/references"),
}
BARRIER_KEYS = {
    "market_intelligence", "visual_reference_research", "round_a_direction_references",
    "round_b_set_prompts", "round_c_concept_images", "formal_production",
}
BARRIER_VALUES = {"pending", "pass", "fail", "not_applicable"}
ROUND_KEYS = {"round_a", "round_b", "round_c"}
HARD_GATE_KEYS = {
    "product_master", "requirements_execution", "copy_exactness", "cast_presence", "physical_logic", "market_intelligence",
    "reference_visual_utility", "direction_divergence", "style_branch_divergence",
    "within_direction_set_diversity", "cross_direction_selling_point_diversity",
    "portable_prompt_self_containment", "cast_fidelity", "integrated_generation", "product_fidelity",
    "layout_art_direction", "creative_value", "image_quality",
}
EVIDENCE_GATES = {"product_master", "requirements_execution"}
DIRECTION_GATES = {"market_intelligence", "reference_visual_utility", "direction_divergence", "style_branch_divergence"}
PROMPT_GATES = {
    "within_direction_set_diversity", "cross_direction_selling_point_diversity",
    "portable_prompt_self_containment",
}
NOT_APPLICABLE_GATES = {"cast_fidelity"}
GATE_STATUS = re.compile(r"^(pending|not_applicable|pass(?:_[A-Za-z0-9_.-]+)?|fail(?:_[A-Za-z0-9_.-]+)?|blocked(?:_[A-Za-z0-9_.-]+)?|complete(?:_[A-Za-z0-9_.-]+)?)$")


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def valid_sha256(value):
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", str(value or "")))


def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolved_project_file(project_root, value):
    if not str(value or "").strip():
        return None
    project_root = project_root.resolve()
    path = (project_root / str(value)).resolve()
    try:
        path.relative_to(project_root)
    except ValueError:
        return None
    return path


def lifecycle_state(data):
    state = data.get("state")
    previous = data.get("previous_state")
    return previous if state in AUXILIARY and previous in PATH else state


def reached(data, state):
    current = lifecycle_state(data)
    return current in PATH and PATH.index(current) >= PATH.index(state)


def gate_passed(key, value):
    text = str(value or "")
    return text == "pass" or text.startswith("pass_") or text == "complete" or text.startswith("complete_") or (text == "not_applicable" and key in NOT_APPLICABLE_GATES)


def explicit_approval(data, gate):
    return any(
        isinstance(item, dict) and item.get("gate") == gate
        and item.get("status") == "approved"
        and item.get("source") == "explicit_user_confirmation"
        for item in data.get("confirmation_history", [])
    )


def validate_transition(data, errors):
    state = data.get("state")
    previous = data.get("previous_state")
    if state == PATH[0]:
        if previous == "resumed" and data.get("resume_state") == state:
            return
        if previous is not None:
            errors.append(f"transition:{previous}->{state}")
        return
    if state in PATH:
        allowed = {PATH[PATH.index(state) - 1], "resumed"}
        if state == "awaiting_production_approval":
            allowed.add("prompt_revision_active")
        if previous not in allowed:
            errors.append(f"transition:{previous}->{state}")
        if previous == "resumed" and data.get("resume_state") != state:
            errors.append("resume_target")
        return
    allowed_auxiliary = {
        "blocked": set(PATH[:-1]) | {"revision_requested", "prompt_revision_active", "resumed"},
        "revision_requested": set(PATH[1:]) | {"blocked", "failed_attempt_limit"},
        "prompt_revision_active": {"revision_requested", "resumed"},
        "resumed": {"blocked", "failed_attempt_limit"},
        "failed_attempt_limit": {"sample_active", "production_active", "prompt_revision_active"},
    }
    if state in allowed_auxiliary and previous not in allowed_auxiliary[state]:
        errors.append(f"transition:{previous}->{state}")
    if state == "prompt_revision_active" and previous == "resumed" and data.get("resume_state") != state:
        errors.append("resume_target")
    if state in {"blocked", "failed_attempt_limit"} and data.get("resume_state") != previous:
        errors.append("resume_target")
    if state == "resumed" and data.get("resume_state") not in set(PATH[:-1]) | {"prompt_revision_active"}:
        errors.append("resume_target")


def validate_file_binding(project_root, record, path_field, hash_field, errors, label):
    path = resolved_project_file(project_root, record.get(path_field))
    digest = record.get(hash_field)
    if path is None or not path.is_file() or not valid_sha256(digest) or sha256_file(path).lower() != str(digest).lower():
        errors.append(f"{label}_{path_field}_binding")


def load_asset_plan(data, project_root, errors, label="asset_plan"):
    binding = data.get("asset_plan_binding")
    binding = binding if isinstance(binding, dict) else {}
    path = resolved_project_file(project_root, binding.get("path"))
    if path is None or not path.is_file() or not valid_sha256(binding.get("sha256")) or sha256_file(path).lower() != str(binding.get("sha256", "")).lower():
        errors.append(f"{label}_binding")
        return None
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        errors.append(f"{label}_binding")
        return None
    if not isinstance(plan, dict) or plan.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{label}_schema_version")
        return None
    return plan


def load_json_binding(data, project_root, field, errors, label):
    binding = data.get(field)
    binding = binding if isinstance(binding, dict) else {}
    path = resolved_project_file(project_root, binding.get("path"))
    digest = binding.get("sha256")
    if path is None or not path.is_file() or not valid_sha256(digest) or sha256_file(path).lower() != str(digest).lower():
        errors.append(f"{label}_binding")
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        errors.append(f"{label}_binding")
        return None
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{label}_schema_version")
        return None
    return record


def validate_market_research(data, project_root, errors):
    record = load_json_binding(data, project_root, "market_research_binding", errors, "market_research")
    if record is None:
        return
    scope = record.get("scope")
    scope = scope if isinstance(scope, dict) else {}
    platform = str(scope.get("target_selling_platform") or "").strip().casefold()
    region = str(scope.get("target_country_or_region") or "").strip().casefold()
    category = str(scope.get("category") or "").strip().casefold()
    if not all((platform, region, category)):
        errors.append("market_research_scope")

    competitors = record.get("competitors")
    competitors = competitors if isinstance(competitors, list) else []
    exception = record.get("sample_exception")
    exception = exception if isinstance(exception, dict) else {}
    smaller_sample_approved = (
        exception.get("status") == "approved"
        and exception.get("source") == "explicit_user_confirmation"
        and bool(str(exception.get("raw_text") or "").strip())
    )
    if len(competitors) < 10 and not smaller_sample_approved:
        errors.append("market_competitor_count")

    accepted_signals = {
        "dated_category_or_bestseller_rank", "platform_displayed_sales_or_orders",
        "rating_plus_material_review_volume", "target_platform_verifiable_retail_data",
    }
    competitor_ids = set()
    for index, competitor in enumerate(competitors):
        if not isinstance(competitor, dict):
            errors.append(f"market_competitor:{index}:shape")
            continue
        competitor_id = competitor.get("competitor_id")
        if not str(competitor_id or "").strip() or competitor_id in competitor_ids:
            errors.append(f"market_competitor:{index}:identity")
        competitor_ids.add(competitor_id)
        if str(competitor.get("platform") or "").strip().casefold() != platform:
            errors.append(f"market_competitor:{competitor_id}:target_platform")
        if str(competitor.get("country_or_region") or "").strip().casefold() != region:
            errors.append(f"market_competitor:{competitor_id}:target_region")
        if str(competitor.get("category") or "").strip().casefold() != category:
            errors.append(f"market_competitor:{competitor_id}:category")
        if not str(competitor.get("source_url") or "").startswith(("http://", "https://")) or parse_time(competitor.get("retrieved_at")) is None:
            errors.append(f"market_competitor:{competitor_id}:source")
        signals = competitor.get("observable_performance")
        signals = signals if isinstance(signals, list) else []
        if not any(
            isinstance(signal, dict)
            and signal.get("signal_type") in accepted_signals
            and str(signal.get("value") or "").strip()
            and str(signal.get("source_url") or "").startswith(("http://", "https://"))
            and parse_time(signal.get("observed_at")) is not None
            for signal in signals
        ):
            errors.append(f"market_competitor:{competitor_id}:observable_performance")

    feedback = record.get("negative_feedback_records")
    feedback = feedback if isinstance(feedback, list) else []
    valid_feedback = [
        item for item in feedback
        if isinstance(item, dict)
        and item.get("competitor_id") in competitor_ids
        and str(item.get("source_url") or "").startswith(("http://", "https://"))
        and parse_time(item.get("retrieved_at")) is not None
        and bool(str(item.get("problem_summary") or "").strip())
    ]
    covered = {item.get("competitor_id") for item in valid_feedback}
    if len(valid_feedback) < 30 or len(covered) < min(6, len(competitors)):
        errors.append("market_negative_feedback_coverage")
    if not isinstance(record.get("issue_themes"), list) or not record.get("issue_themes"):
        errors.append("market_issue_themes")
    if record.get("product_fact_authority") is not False:
        errors.append("market_product_fact_authority")


def validate_parallel_window(execution, runs, errors, label):
    window = execution.get("parallel_window", {}) if isinstance(execution, dict) else {}
    times = [parse_time(window.get(key)) for key in ("started_at", "all_agents_active_at", "first_agent_completed_at")]
    starts = [parse_time(run.get("started_at")) for run in runs]
    completions = [parse_time(run.get("completed_at")) for run in runs]
    if any(value is None for value in [*times, *starts, *completions]):
        errors.append(label)
        return
    expected = (min(starts), max(starts), min(completions))
    if tuple(times) != expected or expected[1] > expected[2]:
        errors.append(label)


def validate_support_agents(data, errors, project_root):
    runs = data.get("support_agent_runs")
    execution = data.get("support_agent_execution")
    if not isinstance(runs, list) or not isinstance(execution, dict):
        errors.append("support_agent_state")
        return
    by_name = {run.get("agent_name"): run for run in runs if isinstance(run, dict)}
    if len(runs) != 2 or set(by_name) != set(SUPPORT_AGENTS):
        errors.append("support_agent_coverage")
        return
    execution_ids = {run.get("execution_id") for run in runs}
    if len(execution_ids) != 1 or not all(str(value or "").strip() for value in execution_ids):
        errors.append("support_agent_execution_id")
    for name, (role, workstream) in SUPPORT_AGENTS.items():
        run = by_name[name]
        if run.get("role") != role or run.get("workstream_path") != workstream:
            errors.append(f"support_agent:{name}:identity")
        if run.get("input_lock_path") != f"{workstream}/input-lock.json" or run.get("handoff_path") != f"{workstream}/handoff.json":
            errors.append(f"support_agent:{name}:path")
        if run.get("canonical_write_paths") != [] or run.get("status") not in RUN_STATUSES:
            errors.append(f"support_agent:{name}:write_or_status")

    if reached(data, "awaiting_direction_approval"):
        for name, run in by_name.items():
            if data.get("barrier_status", {}).get(name) != "pass" or run.get("status") != "completed":
                errors.append(f"support_agent:{name}:barrier")
            times = [parse_time(run.get(field)) for field in ("started_at", "completed_at", "last_handoff_at")]
            if any(value is None for value in times) or not (times[0] <= times[1] <= times[2]):
                errors.append(f"support_agent:{name}:provenance")
            validate_file_binding(project_root, run, "input_lock_path", "input_lock_sha256", errors, f"support_agent:{name}")
            validate_file_binding(project_root, run, "handoff_path", "handoff_sha256", errors, f"support_agent:{name}")
        mode = execution.get("mode")
        if mode == "parallel_agents":
            validate_parallel_window(execution, runs, errors, "parallel_support_agent_window")
        elif mode != "sequential_fallback":
            errors.append("support_agent_execution_mode")
        elif execution.get("fallback_reason") != "collaboration_actions_unavailable" or not str(execution.get("fallback_evidence") or "").strip():
            errors.append("support_agent_fallback_reason")
        validate_market_research(data, project_root, errors)
        load_json_binding(data, project_root, "reference_library_binding", errors, "reference_library")


def validate_round_timing(runs, round_key, errors, require_overlap):
    timings = [run.get("round_timing", {}).get(round_key, {}) for run in runs]
    starts = [parse_time(item.get("started_at")) for item in timings]
    completions = [parse_time(item.get("completed_at")) for item in timings]
    hashes = [item.get("handoff_sha256") for item in timings]
    if any(value is None for value in [*starts, *completions]) or any(not valid_sha256(value) for value in hashes):
        errors.append(f"parallel_{round_key}_provenance")
        return
    if any(start > complete for start, complete in zip(starts, completions)):
        errors.append(f"parallel_{round_key}_chronology")
    if require_overlap and max(value.timestamp() for value in starts) > min(value.timestamp() for value in completions):
        errors.append(f"parallel_{round_key}_window")


def validate_run_shape(run, direction, execution_id, errors):
    prefix = f"direction_agent:{direction}"
    if run.get("execution_id") != execution_id or run.get("agent_name") != DIRECTION_AGENTS[direction]:
        errors.append(f"{prefix}:identity")
    if run.get("workstream_path") != f"workstreams/{direction}" or run.get("input_lock_path") != f"workstreams/{direction}/input-lock.json" or run.get("handoff_path") != f"workstreams/{direction}/handoff.json":
        errors.append(f"{prefix}:path")
    if run.get("canonical_write_paths") != []:
        errors.append(f"{prefix}:canonical_write")
    if run.get("status") not in RUN_STATUSES:
        errors.append(f"{prefix}:status")
    statuses = run.get("round_status")
    timings = run.get("round_timing")
    if not isinstance(statuses, dict) or set(statuses) != ROUND_KEYS or any(value not in BARRIER_VALUES for value in statuses.values()):
        errors.append(f"{prefix}:round_status")
    if not isinstance(timings, dict) or set(timings) != ROUND_KEYS or any(not isinstance(value, dict) for value in timings.values()):
        errors.append(f"{prefix}:round_timing")


def validate_production_run_shape(run, execution_id, asset_direction_map, errors):
    name = str(run.get("agent_name") or "")
    direction = run.get("direction_id")
    prefix = f"production_agent:{name or 'unknown'}"
    if run.get("execution_id") != execution_id or direction not in DIRECTIONS or not re.fullmatch(r"production_d[123]_\d{2}", name):
        errors.append(f"{prefix}:identity")
    workstream = f"workstreams/production/{name}"
    if run.get("workstream_path") != workstream or run.get("input_lock_path") != f"{workstream}/input-lock.json" or run.get("handoff_path") != f"{workstream}/handoff.json":
        errors.append(f"{prefix}:path")
    if run.get("canonical_write_paths") != [] or run.get("status") not in RUN_STATUSES:
        errors.append(f"{prefix}:write_or_status")
    asset_ids = run.get("asset_ids")
    if not isinstance(asset_ids, list) or not asset_ids or len(asset_ids) != len(set(asset_ids)):
        errors.append(f"{prefix}:asset_scope")
        return []
    if any(asset_direction_map.get(asset_id) != direction for asset_id in asset_ids):
        errors.append(f"{prefix}:asset_ownership")
    return asset_ids


def validate_multi_agent(data, errors, project_root):
    profile = data.get("run_profile")
    execution = data.get("agent_execution")
    runs = data.get("active_agent_runs")
    barriers = data.get("barrier_status")
    execution_id = data.get("current_execution_id")
    history = data.get("agent_run_history")
    if not str(execution_id or "").strip() or not isinstance(history, list) or not isinstance(execution, dict) or not isinstance(runs, list) or not isinstance(barriers, dict):
        errors.append("multi_agent_state")
        return
    if set(barriers) != BARRIER_KEYS or any(value not in BARRIER_VALUES for value in barriers.values()):
        errors.append("barrier_status")
    if any(isinstance(run, dict) and run.get("execution_id") == execution_id for run in history):
        errors.append("current_execution_hidden_in_history")

    if profile in DIRECTION_PROFILES:
        by_direction = {item.get("direction_id"): item for item in runs if isinstance(item, dict)}
        if len(runs) != 3 or set(by_direction) != DIRECTIONS:
            errors.append("direction_agent_coverage")
            return
        for direction, run in by_direction.items():
            validate_run_shape(run, direction, execution_id, errors)

        mode = execution.get("mode")
        if mode not in {"parallel_agents", "sequential_fallback"}:
            errors.append("agent_execution_mode")
        if mode == "sequential_fallback" and (execution.get("fallback_reason") != "collaboration_actions_unavailable" or not str(execution.get("fallback_evidence", "")).strip()):
            errors.append("sequential_fallback_reason")

        if reached(data, "awaiting_direction_approval"):
            if barriers.get("round_a_direction_references") != "pass" or any(run.get("round_status", {}).get("round_a") != "pass" for run in runs):
                errors.append("round_a_barrier")
            for direction, run in by_direction.items():
                times = [parse_time(run.get(field)) for field in ("started_at", "completed_at", "last_handoff_at")]
                if any(value is None for value in times) or not (times[0] <= times[1] <= times[2]):
                    errors.append(f"direction_agent_provenance:{direction}")
                validate_file_binding(project_root, run, "input_lock_path", "input_lock_sha256", errors, f"direction_agent:{direction}")
                validate_file_binding(project_root, run, "handoff_path", "handoff_sha256", errors, f"direction_agent:{direction}")
            if mode == "parallel_agents":
                window = execution.get("parallel_window", {})
                times = [parse_time(window.get(key)) for key in ("started_at", "all_agents_active_at", "first_agent_completed_at")]
                starts = [parse_time(run.get("started_at")) for run in runs]
                completions = [parse_time(run.get("completed_at")) for run in runs]
                if any(value is None for value in [*times, *starts, *completions]):
                    errors.append("parallel_agent_window")
                else:
                    expected = (min(starts), max(starts), min(completions))
                    if tuple(times) != expected or expected[1] > expected[2]:
                        errors.append("parallel_agent_window")
                validate_round_timing(runs, "round_a", errors, True)

        if reached(data, "awaiting_production_approval"):
            if barriers.get("round_b_set_prompts") != "pass" or any(run.get("round_status", {}).get("round_b") != "pass" for run in runs):
                errors.append("round_b_barrier")
            expected_c = "not_applicable" if profile == "strategy_only" else "pass"
            if barriers.get("round_c_concept_images") != expected_c or any(run.get("round_status", {}).get("round_c") != expected_c for run in runs):
                errors.append("round_c_barrier")
            if mode == "parallel_agents":
                validate_round_timing(runs, "round_b", errors, True)
                if profile != "strategy_only":
                    validate_round_timing(runs, "round_c", errors, True)
            plan = load_asset_plan(data, project_root, errors)
            if plan is not None:
                directions = {item.get("direction_id") for item in plan.get("assets", []) if isinstance(item, dict)}
                if not DIRECTIONS.issubset(directions):
                    errors.append("asset_plan_direction_coverage")

    if profile == "production_selected":
        selected_directions = data.get("active_direction_ids")
        selected_directions = selected_directions if isinstance(selected_directions, list) else []
        asset_ids = data.get("active_asset_ids")
        asset_direction_map = data.get("active_asset_direction_map")
        if not selected_directions or selected_directions != sorted(set(selected_directions)) or not set(selected_directions).issubset(DIRECTIONS):
            errors.append("production_direction_scope")
        expected_primary = selected_directions[0] if len(selected_directions) == 1 else None
        if data.get("active_direction_id") != expected_primary:
            errors.append("production_primary_direction")
        if not isinstance(asset_ids, list) or not asset_ids or len(asset_ids) != len(set(asset_ids)):
            errors.append("production_asset_scope")
            asset_ids = []
        if not isinstance(asset_direction_map, dict) or set(asset_direction_map) != set(asset_ids) or set(asset_direction_map.values()) != set(selected_directions):
            errors.append("production_asset_ownership")
            asset_direction_map = asset_direction_map if isinstance(asset_direction_map, dict) else {}

        mode = execution.get("mode")
        expected_workers = min(3, len(asset_ids))
        if mode == "parallel_agents" and len(runs) != expected_workers:
            errors.append("production_agent_count")
        elif mode == "single_agent" and (expected_workers != 1 or len(runs) != 1):
            errors.append("production_agent_count")
        elif mode == "sequential_fallback":
            if not 1 <= len(runs) <= max(expected_workers, 1):
                errors.append("production_agent_count")
            if execution.get("fallback_reason") != "collaboration_actions_unavailable" or not str(execution.get("fallback_evidence") or "").strip():
                errors.append("production_fallback_reason")
        elif mode not in {"parallel_agents", "single_agent", "sequential_fallback"}:
            errors.append("production_execution_mode")

        assigned = []
        for run in runs:
            if not isinstance(run, dict):
                errors.append("production_agent_shape")
                continue
            assigned.extend(validate_production_run_shape(run, execution_id, asset_direction_map, errors))
        if len(assigned) != len(set(assigned)) or set(assigned) != set(asset_ids):
            errors.append("production_asset_shard_coverage")

        plan = load_asset_plan(data, project_root, errors, "production_asset_plan")
        if plan is not None:
            plan_directions = {item.get("asset_id"): item.get("direction_id") for item in plan.get("assets", []) if isinstance(item, dict)}
            if any(plan_directions.get(asset_id) != asset_direction_map.get(asset_id) for asset_id in asset_ids):
                errors.append("production_asset_plan_ownership")

        if reached(data, "qa_active"):
            if barriers.get("formal_production") != "pass" or any(run.get("status") != "completed" for run in runs if isinstance(run, dict)):
                errors.append("formal_production_barrier")
            for run in runs:
                if not isinstance(run, dict):
                    continue
                times = [parse_time(run.get(field)) for field in ("started_at", "completed_at", "last_handoff_at")]
                if any(value is None for value in times) or not (times[0] <= times[1] <= times[2]):
                    errors.append(f"production_agent:{run.get('agent_name')}:provenance")
                validate_file_binding(project_root, run, "input_lock_path", "input_lock_sha256", errors, f"production_agent:{run.get('agent_name')}")
                validate_file_binding(project_root, run, "handoff_path", "handoff_sha256", errors, f"production_agent:{run.get('agent_name')}")
            if mode == "parallel_agents" and len(runs) > 1:
                validate_parallel_window(execution, runs, errors, "parallel_production_agent_window")

    if profile in REVISION_PROFILES:
        selected = data.get("active_direction_id")
        selected_directions = data.get("active_direction_ids")
        asset_ids = data.get("active_asset_ids")
        asset_direction_map = data.get("active_asset_direction_map")
        if selected not in DIRECTIONS or selected_directions != [selected] or not isinstance(asset_ids, list) or not asset_ids or len(asset_ids) != len(set(asset_ids)):
            errors.append("single_direction_execution_scope")
        if not isinstance(asset_direction_map, dict) or set(asset_direction_map) != set(asset_ids or []) or any(direction != selected for direction in asset_direction_map.values()):
            errors.append("single_direction_asset_ownership")
        if len(runs) != 1 or not isinstance(runs[0], dict):
            errors.append("single_direction_agent_count")
        else:
            run = runs[0]
            if selected in DIRECTIONS:
                validate_run_shape(run, selected, execution_id, errors)
            if execution.get("mode") != "single_direction_agent":
                errors.append("single_direction_execution_mode")
            active_single = lifecycle_state(data) in {"production_active", "qa_active", "awaiting_delivery_approval", "complete", "prompt_revision_active"}
            if active_single:
                times = [parse_time(run.get(field)) for field in ("started_at", "completed_at", "last_handoff_at")]
                if any(value is None for value in times) or not (times[0] <= times[1] <= times[2]):
                    errors.append("single_direction_agent_provenance")
                validate_file_binding(project_root, run, "input_lock_path", "input_lock_sha256", errors, "single_direction")
                validate_file_binding(project_root, run, "handoff_path", "handoff_sha256", errors, "single_direction")
                plan = load_asset_plan(data, project_root, errors, "single_direction_asset_plan")
                if plan is not None:
                    plan_directions = {item.get("asset_id"): item.get("direction_id") for item in plan.get("assets", []) if isinstance(item, dict)}
                    if any(plan_directions.get(asset_id) != selected for asset_id in asset_ids or []):
                        errors.append("single_direction_asset_plan_ownership")
        context = data.get("revision_context")
        context = context if isinstance(context, dict) else {}
        scope = context.get("revision_scope", {})
        if context.get("selected_direction_id") != selected or scope.get("direction_ids") != [selected] or scope.get("selected_direction_id") != selected or set(scope.get("asset_ids", [])) != set(asset_ids or []):
            errors.append("prompt_revision_single_direction")


def validate_authorization(data, errors):
    auth = data.get("execution_authorization")
    if not isinstance(auth, dict):
        errors.append("concept_authorization")
        return
    scope = auth.get("scope")
    if auth.get("granted_by") != "user" or auth.get("status") != "active":
        errors.append("concept_authorization_status")
    if not isinstance(scope, dict) or scope.get("through_state") != "sample_active":
        errors.append("concept_authorization_scope")
        return
    if any(scope.get(key) is not True for key in AUTO_SCOPE):
        errors.append("concept_authorization_permissions")
    if scope.get("formal_production") is not False or scope.get("final_delivery") is not False:
        errors.append("concept_authorization_overreach")


def validate_hard_gates(data, errors):
    gates = data.get("hard_gate_status")
    if not isinstance(gates, dict):
        errors.append("hard_gate_status")
        return
    missing = HARD_GATE_KEYS - set(gates)
    if missing:
        errors.extend(f"missing_hard_gate:{key}" for key in sorted(missing))
    for key, value in gates.items():
        if not isinstance(value, str) or not GATE_STATUS.fullmatch(value):
            errors.append(f"hard_gate_value:{key}")

    required = set()
    if data.get("state") in {"blocked", "failed_attempt_limit", "revision_requested"}:
        return
    if reached(data, "strategy_active"):
        required |= EVIDENCE_GATES
    if reached(data, "awaiting_direction_approval"):
        required |= DIRECTION_GATES
    if reached(data, "awaiting_production_approval"):
        required |= PROMPT_GATES
    if reached(data, "qa_active"):
        required.add("integrated_generation")
    if lifecycle_state(data) in {"awaiting_delivery_approval", "complete"}:
        required |= HARD_GATE_KEYS
    for key in sorted(required):
        if not gate_passed(key, gates.get(key)):
            errors.append(f"hard_gate_not_passed:{key}")


def validate_state(data, project_root):
    errors = [f"missing:{key}" for key in sorted(REQUIRED - set(data))]
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version:{data.get('schema_version')}->{SCHEMA_VERSION}")
    if not isinstance(data.get("state_revision"), int) or isinstance(data.get("state_revision"), bool) or data.get("state_revision", -1) < 0:
        errors.append("state_revision")
    if not isinstance(data.get("migration_history"), list):
        errors.append("migration_history")

    state = data.get("state")
    profile = data.get("run_profile")
    if profile not in PROFILES:
        errors.append("run_profile")
    if state not in ALL_STATES:
        errors.append("state")
    else:
        validate_transition(data, errors)

    if parse_time(data.get("updated_at")) is None:
        errors.append("updated_at")
    if not isinstance(data.get("advance_allowed"), bool):
        errors.append("advance_allowed")
    if not isinstance(data.get("confirmation_required"), bool):
        errors.append("confirmation_required")
    if data.get("confirmation_status") not in CONFIRMATIONS:
        errors.append("confirmation_status")

    awaiting = str(state).startswith("awaiting_")
    if awaiting and (data.get("confirmation_required") is not True or data.get("confirmation_status") != "pending" or data.get("advance_allowed") is not False):
        errors.append("approval_gate")
    if not awaiting and state != "complete" and data.get("confirmation_required") is True:
        errors.append("unexpected_confirmation_gate")
    if state == "complete" and (data.get("advance_allowed") is not False or data.get("confirmation_required") is not False):
        errors.append("complete_state_flags")

    list_fields = ("completed_artifacts", "pending_items", "blockers", "confirmation_history", "open_questions", "decision_history", "active_direction_ids", "active_asset_ids", "support_agent_runs", "agent_run_history")
    for key in list_fields:
        if key in data and not isinstance(data[key], list):
            errors.append(f"list:{key}")
    if (data.get("blockers") or data.get("open_questions")) and data.get("advance_allowed") is not False:
        errors.append("blocked_advance")
    if state in {"blocked", "failed_attempt_limit"} and (not data.get("blockers") or data.get("advance_allowed") is not False):
        errors.append("blocking_state_contract")
    if state == "complete" and (data.get("blockers") or data.get("open_questions") or data.get("pending_items")):
        errors.append("complete_with_open_work")

    for question in data.get("open_questions", []) if isinstance(data.get("open_questions"), list) else []:
        options = question.get("options", []) if isinstance(question, dict) else []
        if not isinstance(question, dict) or question.get("confidence") not in CONFIDENCE - {"high"}:
            errors.append("question_confidence")
        if not isinstance(options, list) or len(options) < 3 or sum(bool(option.get("recommended")) for option in options if isinstance(option, dict)) != 1:
            errors.append("question_options")
        if not isinstance(question, dict) or question.get("user_defined") is not True:
            errors.append("question_user_defined")

    validate_hard_gates(data, errors)
    if data.get("schema_version") == SCHEMA_VERSION and profile in PROFILES:
        validate_support_agents(data, errors, Path(project_root))
        validate_multi_agent(data, errors, Path(project_root))

    if profile in {"auto_concept_fast", "auto_concept_full"} and reached(data, "strategy_active"):
        validate_authorization(data, errors)
        if state == "sample_active" and (data.get("blockers") or data.get("open_questions")):
            errors.append("concept_unresolved_uncertainty")
    if reached(data, "sample_active") and profile in DIRECTION_PROFILES and not explicit_approval(data, "awaiting_direction_approval"):
        errors.append("sample_without_explicit_direction_approval")
    if reached(data, "production_active") and not explicit_approval(data, "awaiting_production_approval"):
        errors.append("formal_production_without_explicit_approval")
    if profile == "prompt_revision":
        context = data.get("revision_context")
        if not isinstance(context, dict) or any(not context.get(key) for key in ("selected_direction_id", "base_prompt_revision_id", "revision_scope")):
            errors.append("prompt_revision_context")
    if state == "complete" and not explicit_approval(data, "awaiting_delivery_approval"):
        errors.append("delivery_without_explicit_approval")

    return list(dict.fromkeys(errors))


def main():
    parser = ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--report")
    parser.add_argument("--project-root")
    args = parser.parse_args()
    state_path = Path(args.state).resolve()
    project_root = Path(args.project_root).resolve() if args.project_root else state_path.parent
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        errors = validate_state(data, project_root)
    except (OSError, json.JSONDecodeError) as exc:
        errors = [f"state_read:{exc}"]
    report = {"schema_version": SCHEMA_VERSION, "status": "pass" if not errors else "fail", "errors": errors}
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
