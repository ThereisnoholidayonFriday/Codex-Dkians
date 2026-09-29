"""V6 local project controller. Model/tool work is performed by the root Agent."""
from argparse import ArgumentParser
from copy import deepcopy
import importlib.util
import html
import json
from pathlib import Path
from types import SimpleNamespace
import re
import shutil
import sys
import uuid

from v6_storage import atomic_json, digest, file_lock, object_digest, read_json, utc_now

VERSION = "6.0"
ATTEMPT_LIMIT = 2
SKILLS = Path(__file__).resolve().parents[2]
ROLES = {"brief", "product_master", "requirements", "policy", "research", "aesthetics", "directions", "copy_master", "typography", "plan", "prompt_package", "cast_presence", "scene_truth", "physical_logic", "prompt_source", "revision_ledger", "layout", "preflight"}
PLAN_ROLES = {"product_master", "requirements", "policy", "research", "aesthetics", "directions", "copy_master", "typography", "plan"}
FACT_ROLES = {"product_master", "requirements", "policy", "copy_master", "cast_presence", "scene_truth", "physical_logic"}
PLAN_BINDING_ROLES = PLAN_ROLES | {"brief", "cast_presence", "scene_truth", "physical_logic", "layout"}
STATES = {"intake", "strategy_active", "awaiting_plan_approval", "sample_active", "awaiting_production_approval", "production_active", "qa_active", "blocked", "complete"}
FAILURES = {"input_evidence", "requirement_conflict", "creative_failure", "prompt_conflict", "model_execution", "size_adaptation", "detection_error"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", value), f"invalid identifier: {value}")
    return value


def within(root, path):
    path = Path(path).resolve()
    require(path.is_relative_to(root.resolve()), f"path escapes project: {path}")
    return path


def state_path(root):
    return root / "control" / "workflow_state.json"


def binding(root, path):
    path = Path(path).resolve()
    require(path.is_file(), f"missing file: {path}")
    return {"path": str(path.relative_to(root)) if path.is_relative_to(root) else str(path), "sha256": digest(path)}


def verify_binding(root, item):
    require(isinstance(item, dict) and item.get("path") and item.get("sha256"), "missing file binding")
    path = (root / item["path"]).resolve()
    require(path.is_file() and digest(path) == item["sha256"], f"file binding changed or unavailable: {path}")
    return path


def load_state(root):
    state = read_json(state_path(root))
    require(state.get("schema_version") == VERSION, "V6 requires schema_version 6.0; legacy projects use legacy commands")
    require(state.get("state") in STATES, "invalid V6 state")
    require(type(state.get("state_revision")) is int and state["state_revision"] >= 0, "invalid state revision")
    for key, kind in (("artifacts", dict), ("approvals", dict), ("attempts", list), ("reviews", dict), ("packets", dict), ("revisions", list), ("events", list)):
        require(isinstance(state.get(key), kind), f"invalid state field: {key}")
    return state


def controls(root, state):
    result = {role: read_json(verify_binding(root, item)) for role, item in state["artifacts"].items()}
    approved = state["approvals"].get("plan", {})
    ids = approved.get("approved_requirement_ids", []) if approved.get("valid") else []
    result["_approved_requirement_ids"] = ids
    result["_stage"] = "production" if state["state"] in {"production_active", "qa_active", "complete"} else "sample" if state["state"] in {"sample_active", "awaiting_production_approval"} else "strategy"
    # User-supplied content is an execution instruction, not an external-verification task.
    for role, field in (("requirements", "requirements"), ("copy_master", "entries")):
        for row in result.get(role, {}).get(field, []):
            if row.get("origin") in {"user_document", "user_message"} and row.get("text") and row.get("status") != "confirmed":
                row["status"] = "confirmed"
                row["acceptance_basis"] = "user_supplied_content"
                row.pop("blocker", None)
                row.pop("blockers", None)
    for row in result.get("requirements", {}).get("requirements", []):
        if row.get("requirement_id") in ids and row.get("status") == "proposed":
            row["status"] = "confirmed"
            row["approval_reference"] = approved["source"]
    for row in result.get("copy_master", {}).get("entries", []):
        if row.get("origin") == "agent_proposal" and row.get("requirement_id") in ids and row.get("status") == "proposed":
            row["status"] = "confirmed"
            row["approval_reference"] = approved["source"]
    return result


def scope_hash(state, production=False):
    roles = PLAN_BINDING_ROLES | ({"prompt_package"} if production else set())
    return object_digest({role: state["artifacts"][role]["sha256"] for role in sorted(roles) if role in state["artifacts"]})


def visual_validator():
    path = SKILLS / "visual-qa-delivery" / "scripts" / "validate_v6.py"
    spec = importlib.util.spec_from_file_location("twin_stars_v6_visual", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assets(data):
    rows = data.get("plan", {}).get("assets", [])
    require(rows and all(isinstance(row, dict) for row in rows), "asset plan is empty")
    ids = [identifier(row.get("asset_id")) for row in rows]
    require(len(ids) == len(set(ids)), "duplicate asset IDs")
    return dict(zip(ids, rows))


def streamlined(data):
    return data.get("policy", {}).get("workflow_profile") == "streamlined"


def effective_execution_rules(state, policy):
    """Expose enforced limits separately from immutable historical policy snapshots."""
    validator = visual_validator()
    return {"authorized_logical_asset_ceilings": {lid: row["ceiling"] for lid, row in state.get("budget_extensions", {}).items()}, "attempt_limit": ATTEMPT_LIMIT, "attempt_limit_source": "v6_runtime.ATTEMPT_LIMIT", "historical_policy_attempt_limit": policy.get("generation_policy", {}).get("attempt_limit"), "maximum_upscale_factor": validator.qa_option(policy, "maximum_upscale_factor", 1.05), "maximum_resample_count": validator.qa_option(policy, "maximum_resample_count", 1), "final_delivery_authorized": state["approvals"].get("production", {}).get("valid") is True and state["approvals"]["production"].get("final_delivery_authorized") is True}


def research_bindings(state):
    return {role: state["artifacts"][role]["sha256"] for role in ("research", "aesthetics") if role in state["artifacts"]}


def validate_plan(root, state, need_prompts=False):
    require(PLAN_ROLES <= state["artifacts"].keys(), f"missing plan artifacts: {sorted(PLAN_ROLES - state['artifacts'].keys())}")
    data = controls(root, state)
    if streamlined(data):
        require(state.get("strategy_research_bindings") == research_bindings(state), "research changed: refresh directions before preparing the plan")
    require(not need_prompts or "prompt_package" in data, "bind compiled prompt_package first")
    errors = visual_validator().validate_v6_project(data["plan"], root, data, prompt_package=data.get("prompt_package") if need_prompts else None)
    require(not errors, f"project preflight failed: {json.dumps(errors, ensure_ascii=False)}")
    return data


def source_confirmation(payload):
    source = payload.get("source", {})
    require(source.get("type") == "user_message" and str(source.get("reference", "")).strip() and str(source.get("quote", "")).strip(), "confirmation needs original user_message reference and quote")
    return source


def snapshot(root, role, path):
    original = Path(path).resolve()
    value = read_json(original)
    require(isinstance(value, dict), "artifact must be a JSON object")
    require(value.get("schema_version") == VERSION, f"{role} must explicitly use schema_version 6.0")
    target = root / "control" / "bindings" / f"{role}-{digest(original)}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(original, target)
    item = binding(root, target)
    item.update({"source_path": str(original), "source_sha256": digest(original)})
    return item, value


def commit(root, old, new, command, details):
    new["state_revision"] = old["state_revision"] + 1
    new["updated_at"] = utc_now()
    new["events"].append({"revision": new["state_revision"], "command": command, "at": new["updated_at"], "details": details})
    history = root / "history" / "state" / f"workflow_state.r{old['state_revision']:06d}.json"
    if history.exists():
        require(read_json(history) == old, "conflicting state history")
    else:
        atomic_json(history, old)
    atomic_json(state_path(root), new)
    return {"schema_version": VERSION, "state_revision": new["state_revision"], "state": new["state"], **details}


def init(root, args):
    require(not state_path(root).exists() and not (root / "workflow_state.json").exists(), "refusing to overwrite an existing or legacy project")
    for directory in ("inputs", "control", "research", "strategy", "prompts", "concepts", "production", "qa", "review", "history", "workstreams"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    state = {"schema_version": VERSION, "project_id": identifier(args.project_id), "brand_id": identifier(args.brand_id), "state_revision": 0, "state": "intake", "artifacts": {}, "approvals": {}, "attempts": [], "reviews": {}, "set_reviews": {}, "packets": {}, "revisions": [], "events": [], "asset_lineage": {}, "review_epochs": {}, "selected_candidates": {}, "blockers": [], "updated_at": utc_now()}
    policy = {"schema_version": VERSION, "project_id": args.project_id, "generation_policy": {"mode": "integrated_full_canvas", "model_recording_id": "codex_builtin_imagegen", "attempt_limit": ATTEMPT_LIMIT, "content_authority": "user_requirements", "external_claim_verification_required": False, "portable_input_contract": "one_product_image_plus_complete_prompt"}, "research_policy": {"acceptance": "evidence_coverage", "stop_when": "critical_questions_resolved_and_new_search_no_longer_changes_decisions"}, "approval_policy": "plan_then_samples_and_production", "output_contracts": [], "qa_policy": {"independent_review_required": True, "maximum_upscale_factor": 1.05, "maximum_resample_count": 1}}
    policy["workflow_profile"] = args.workflow_profile
    if args.workflow_profile == "streamlined":
        policy.update(direction_mode=args.direction_mode, approval_policy="authorized_samples_then_direction_selection")
        policy["qa_policy"]["ocr_mode"] = "risk_based"
    atomic_json(root / "control" / "project_policy.json", policy)
    atomic_json(root / "control" / "project_brief.json", {"schema_version": VERSION, "project_id": args.project_id, "brand_id": args.brand_id, "product_category": getattr(args, "category", "auto_detect"), "category_policy": "open_vocabulary_no_allowlist", "source_files": [], "brand_goal": "", "campaign_goal": "", "document_language": "zh-CN", "source_read_only": True})
    atomic_json(state_path(root), state)
    return {"status": "initialized", "schema_version": VERSION, "state_revision": 0, "project_root": str(root)}


def affected_ids(role, previous, value, rows):
    if role in {"requirements", "copy_master"}:
        field, key, asset_field = ("requirements", "requirement_id", "requirement_ids") if role == "requirements" else ("entries", "copy_id", "visible_copy_ids")
        if {k: v for k, v in previous.items() if k != field} != {k: v for k, v in value.items() if k != field}:
            return set(rows)
        before = {row[key]: row for row in previous.get(field, [])}
        after = {row[key]: row for row in value.get(field, [])}
        changed = {rid for rid in before.keys() | after.keys() if before.get(rid) != after.get(rid)}
        return {aid for aid, row in rows.items() if changed.intersection(row.get(asset_field, []))}
    if role == "plan" or role == "prompt_package":
        key, field = "asset_id", "assets"
    elif role == "directions":
        key, field = "direction_id", "directions"
    elif role == "layout":
        key, field = "asset_id", "assets"
        if not previous.get(field) or not value.get(field):
            return set(rows)
    elif role in {"revision_ledger", "preflight"}:
        return set()
    elif role == "prompt_source":
        if previous.get("base_prompt_sections") != value.get("base_prompt_sections") or previous.get("direction_layers") != value.get("direction_layers"):
            return set(rows)
        key, field = "asset_id", "asset_layers"
    else:
        return set(rows)
    before = {r[key]: r for r in previous.get(field, [])}
    after = {r[key]: r for r in value.get(field, [])}
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    return {aid for aid, row in rows.items() if row.get(key) in changed or aid in changed}


def invalidate_images(state, ids):
    for aid in ids:
        state["review_epochs"][aid] = state["review_epochs"].get(aid, 0) + 1
        if aid in state["reviews"]:
            state["reviews"][aid]["stale"] = True
    if ids:
        state["set_reviews"] = {}


def creative_plan_change(previous, value):
    fields = {"visual_description", "creative_controls", "visual_argument", "shot_signature", "prompt_source_revision_id"}
    before = {row["asset_id"]: {k: v for k, v in row.items() if k not in fields} for row in previous.get("assets", [])}
    after = {row["asset_id"]: {k: v for k, v in row.items() if k not in fields} for row in value.get("assets", [])}
    return before == after


def command_bind(root, state, args):
    item, value = snapshot(root, args.role, args.file)
    require(value.get("project_id", state["project_id"]) == state["project_id"], "artifact belongs to another project")
    previous_binding = state["artifacts"].get(args.role)
    data = controls(root, state)
    refresh_research = args.role == "directions" and streamlined(data) and state.get("strategy_research_bindings") != research_bindings(state)
    if previous_binding and previous_binding["sha256"] == item["sha256"] and not refresh_research:
        return {"status": "unchanged", "role": args.role}
    previous = read_json(verify_binding(root, previous_binding)) if previous_binding else {}
    data = controls(root, state)
    if streamlined(data) and args.role in {"directions", "plan"}:
        require({"research", "aesthetics"} <= data.keys(), "complete market and aesthetic research before directions or plan")
        validator = visual_validator()
        errors = []
        if not validator.workflow_skip(data["policy"], "research"):
            errors = validator.validate_research(data["research"], root, {r["requirement_id"]: r for r in data.get("requirements", {}).get("requirements", [])})
            validator.validate_aesthetics(data["aesthetics"], data["research"], root, errors)
        require(not errors, f"research is not ready: {errors}")
        if args.role == "directions":
            state["strategy_research_bindings"] = research_bindings(state)
        else:
            require(state.get("strategy_research_bindings") == research_bindings(state), "refresh directions against current research before binding plan")
    rows = {row["asset_id"]: row for row in data.get("plan", {}).get("assets", [])}
    changed = affected_ids(args.role, previous, value, rows)
    if args.role == "plan":
        logical = {}
        for row in value.get("assets", []):
            aid, lid = identifier(row.get("asset_id")), identifier(row.get("logical_asset_id"))
            require(lid not in logical, "duplicate logical_asset_id in current plan")
            require(aid not in state["asset_lineage"] or state["asset_lineage"][aid] == lid, "logical asset cannot be renamed to reset its attempt budget")
            logical[lid] = aid
            state["asset_lineage"][aid] = lid
        used = {a["logical_asset_id"] for a in state["attempts"]}
        old_ids = {r["logical_asset_id"] for r in previous.get("assets", [])}
        require((used & old_ids) <= logical.keys(), "used logical assets require explicit retained lineage; they cannot disappear into a new budget")
    state["artifacts"][args.role] = item
    if previous_binding or state["approvals"]:
        invalidate_images(state, changed)
        # A user revision can rebind creative-only files, without reauthorizing unchanged scope.
        revision = next((r for r in reversed(state["revisions"]) if r.get("open")), None)
        allowed = args.role in {"prompt_package", "prompt_source", "revision_ledger", "layout", "directions", "plan"} and revision and changed <= set(revision["asset_ids"])
        if allowed and args.role == "plan":
            allowed = creative_plan_change(previous, value)
        if not allowed:
            for kind, approval in state["approvals"].items():
                if approval.get("scope_hash") != scope_hash(state, kind == "production"):
                    approval["valid"] = False
            if not state["approvals"].get("plan", {}).get("valid"):
                state["state"] = "strategy_active"
            elif not state["approvals"].get("production", {}).get("valid"):
                state["state"] = "sample_active" if state["approvals"]["plan"]["asset_ids"] else "awaiting_production_approval"
        else:
            revision["rebound_roles"].append(args.role)
            for approval in state["approvals"].values():
                if approval.get("valid"):
                    approval["scope_hash"] = scope_hash(state, approval["kind"] == "production")
            state["state"] = "production_active" if state["approvals"].get("production", {}).get("valid") else "sample_active"
    elif state["state"] == "intake":
        state["state"] = "strategy_active"
    return {"status": "bound", "role": args.role, "affected_asset_ids": sorted(changed), "binding": item}


def sample_ids(rows, policy=None):
    policy = policy or {}
    if policy.get("workflow_profile") == "streamlined":
        if visual_validator().workflow_skip(policy, "samples"):
            return []
        selected = []
        directions = ("D1",) if policy.get("direction_mode") in {"existing", "automatic"} else ("D1", "D2", "D3")
        for direction in directions:
            group = [row for row in rows.values() if row["direction_id"] == direction]
            require(group, f"missing direction: {direction}")
            hero = next((row for row in group if row.get("sample_role") == "hero"), group[0])
            selected.append(hero["asset_id"])
            for row in group:
                if row.get("sample_required") is True and row != hero:
                    require(str(row.get("sample_reason", "")).strip(), "additional sample needs an uncovered production difficulty")
                    selected.append(row["asset_id"])
        return selected
    selected = []
    for direction in ("D1", "D2", "D3"):
        group = [row for row in rows.values() if row["direction_id"] == direction]
        require(group, f"missing direction: {direction}")
        chosen = []
        for role in ("hero", "key_expression", "production_risk"):
            match = next((row for row in group if row.get("sample_role") == role and row not in chosen), None)
            if match:
                chosen.append(match)
        for row in group:
            if len(chosen) >= min(3, len(group)):
                break
            if row not in chosen:
                chosen.append(row)
        selected.extend(row["asset_id"] for row in chosen)
    return selected


def command_bind_bundle(root, state, args):
    """Accept one authored control document; maintain canonical role snapshots automatically."""
    bundle = read_json(args.file)
    require(isinstance(bundle, dict) and bundle and set(bundle) <= ROLES, "bundle must map supported roles to V6 objects")
    order = ["policy", "brief", "product_master", "requirements", "copy_master", "research", "aesthetics", "directions", "typography", "cast_presence", "scene_truth", "physical_logic", "layout", "plan", "prompt_source", "revision_ledger", "prompt_package", "preflight"]
    results = []
    for role in order:
        if role not in bundle:
            continue
        path = root / "control" / "bundle-inputs" / object_digest(bundle) / f"{role}.json"
        atomic_json(path, bundle[role])
        results.append(command_bind(root, state, SimpleNamespace(role=role, file=path)))
    return {"status": "bundle_bound", "roles": results, "source": binding(root, args.file)}


def approval_valid(state, kind):
    approval = state["approvals"].get(kind, {})
    require(approval.get("valid") and approval.get("scope_hash") == scope_hash(state, kind == "production"), f"{kind} approval missing or stale")
    return approval


def command_approve_plan(root, state, args):
    require(state["state"] == "awaiting_plan_approval", "prepare-plan before approval")
    data = validate_plan(root, state)
    payload = read_json(args.file)
    source = source_confirmation(payload)
    require(payload.get("scope_hash") == scope_hash(state), "confirmation must name current scope_hash from prepare-plan")
    proposals = {row["requirement_id"] for row in data["requirements"].get("requirements", []) if row.get("status") == "proposed"}
    approved = set(payload.get("approved_requirement_ids", []))
    require(approved == proposals, "confirm all proposed requirements in this plan or revise the plan first")
    samples = sample_ids(assets(data), data["policy"])
    require(set(payload.get("sample_asset_ids", [])) == set(samples), "confirmation must match the representative sample scope")
    state["approvals"]["plan"] = {"kind": "plan", "valid": True, "scope_hash": scope_hash(state), "source": source, "approved_requirement_ids": sorted(approved), "asset_ids": samples, "max_total_attempts": ATTEMPT_LIMIT * len(samples), "approved_at": utc_now()}
    delegation = payload.get("production_delegation")
    if delegation is not None:
        require(streamlined(data) and isinstance(delegation, dict), "production delegation requires streamlined policy")
        source_confirmation(delegation)
        ids = delegation.get("approved_asset_ids", [])
        ceiling = delegation.get("max_total_attempts")
        require(ids and len(ids) == len(set(ids)) and set(ids) <= assets(data).keys(), "invalid delegated asset scope")
        require(type(ceiling) is int and 0 < ceiling <= ATTEMPT_LIMIT * len(ids), "invalid delegated budget")
        state["approvals"]["plan"]["production_delegation"] = delegation
    state["state"] = "sample_active" if samples else "awaiting_production_approval"
    return {"status": "plan_approved", "sample_asset_ids": samples}


def current_attempt(state, asset_id):
    selected = state.get("selected_candidates", {}).get(asset_id)
    if selected:
        return next((a for a in state["attempts"] if a["attempt_id"] == selected and a["status"] == "candidate"), None)
    return next((a for a in reversed(state["attempts"]) if a["asset_id"] == asset_id and a["status"] == "candidate"), None)


def require_reviews(root, state, ids):
    data = controls(root, state)
    rows = assets(data)
    validator = visual_validator()
    for aid in ids:
        review = state["reviews"].get(aid, {})
        require(review.get("passed") and not review.get("stale"), f"passing current review missing: {aid}")
        verify_binding(root, review["image"])
        report = read_json(verify_binding(root, review["report"]))
        attempt = current_attempt(state, aid)
        require(attempt and review["attempt_id"] == attempt["attempt_id"], f"review is not for selected candidate: {aid}")
        require(not any(a["asset_id"] == aid and a["status"] == "reserved" for a in state["attempts"]), f"generation still pending: {aid}")
        verify_binding(root, attempt["native"])
        errors = validator.validate_review(rows[aid], report, root, data, manifest_record(attempt))
        errors += validator.validate_output(rows[aid], manifest_record(attempt), root, data["policy"])
        errors += revision_review_errors(state, aid, report)
        require(not errors, f"current output/review no longer passes: {aid}: {errors}")


def command_approve_production(root, state, args):
    require(state["state"] == "awaiting_production_approval", "passing sample set review required before production")
    approved_plan = approval_valid(state, "plan")
    data = validate_plan(root, state, True)
    require_reviews(root, state, approved_plan["asset_ids"])
    if not streamlined(data):
        require(state["set_reviews"].get("samples", {}).get("passed"), "independent sample set review missing")
        sample_review = state["set_reviews"]["samples"]
        require(sample_review["scope_hash"] == scope_hash(state, True), "sample set review scope changed")
        verify_binding(root, sample_review["report"])
    payload = read_json(args.file)
    source = source_confirmation(payload)
    if payload.get("selection_mode", "user") == "delegated":
        delegation = approved_plan.get("production_delegation", {})
        require(streamlined(data) and delegation and source == delegation.get("source"), "missing matching user production delegation")
        require(set(payload.get("approved_asset_ids", [])) == set(delegation.get("approved_asset_ids", [])), "delegated scope cannot change")
        require(type(payload.get("max_total_attempts")) is int and payload["max_total_attempts"] <= delegation["max_total_attempts"], "delegated budget exceeded")
    else:
        require(payload.get("selection_mode", "user") == "user", "unknown selection mode")
    require(payload.get("scope_hash") == scope_hash(state, True), "production confirmation must bind current scope hash")
    if streamlined(data):
        expected = {aid: current_attempt(state, aid)["image"]["sha256"] for aid in approved_plan["asset_ids"]}
        require(payload.get("sample_image_bindings") == expected, "selection must bind the actual displayed sample images")
    rows = assets(data)
    selected = payload.get("approved_asset_ids", [])
    require(selected and len(set(selected)) == len(selected) and set(selected) <= rows.keys(), "invalid approved asset scope")
    budget = payload.get("max_total_attempts")
    used = sum(a["logical_asset_id"] in {rows[aid]["logical_asset_id"] for aid in selected} for a in state["attempts"])
    require(type(budget) is int and used <= budget <= ATTEMPT_LIMIT * len(selected), "total attempt budget must include samples and fit the two-attempt asset limit")
    state["approvals"]["production"] = {"kind": "production", "valid": True, "scope_hash": scope_hash(state, True), "source": source, "asset_ids": selected, "max_total_attempts": budget, "approved_at": utc_now(), "final_delivery_authorized": True}
    if streamlined(data):
        state["approvals"]["production"].update(selection_mode=payload.get("selection_mode", "user"), sample_image_bindings=payload["sample_image_bindings"])
    state["state"] = "production_active"
    return {"status": "production_approved", "asset_ids": selected, "remaining_attempts": budget - used}


def command_reserve(root, state, args):
    require(state["state"] in {"sample_active", "production_active", "qa_active"}, "generation is not authorized in current state")
    kind = "production" if state["approvals"].get("production", {}).get("valid") else "plan"
    approval = approval_valid(state, kind)
    require(args.asset in approval["asset_ids"], "asset is outside approved scope")
    data = validate_plan(root, state, True)
    rows = assets(data)
    row = rows[args.asset]
    data["_authorized_pilot_asset_id"] = state.get("budget_extensions", {}).get(row["logical_asset_id"], {}).get("pilot_asset_id")
    generation_preflight(root, data, row, kind)
    identifier(args.producer_id)
    history = [a for a in state["attempts"] if a["logical_asset_id"] == row["logical_asset_id"]]
    extension = state.get("budget_extensions", {}).get(row["logical_asset_id"], {})
    require(len(history) < extension.get("ceiling", ATTEMPT_LIMIT), "failed_attempt_limit: authorized attempts exhausted for this logical asset")
    pilot = extension.get("pilot_asset_id")
    if pilot and args.asset != pilot:
        require_reviews(root, state, [pilot])
    require(not any(a["status"] == "reserved" for a in history), "finish or record failure of the existing reserved attempt first")
    logical_scope = {rows[aid]["logical_asset_id"] for aid in approval["asset_ids"]}
    require(sum(a["logical_asset_id"] in logical_scope for a in state["attempts"]) < approval["max_total_attempts"], "approved total attempt budget exhausted")
    diagnosis = read_json(args.file) if args.file else {}
    if history:
        require(diagnosis.get("failure_class") in FAILURES and diagnosis.get("observations") and diagnosis.get("next_change"), "retry needs failure diagnosis, observations and a concrete next_change")
    attempt_id = "ATT-" + uuid.uuid4().hex[:16]
    attempt = {"attempt_id": attempt_id, "asset_id": args.asset, "logical_asset_id": row["logical_asset_id"], "producer_id": args.producer_id, "status": "reserved", "ordinal": len(history) + 1, "diagnosis": diagnosis, "scope_hash": scope_hash(state, True), "created_at": utc_now(), "engine": "codex_builtin_imagegen"}
    state["attempts"].append(attempt)
    invalidate_images(state, {args.asset})
    state["state"] = "production_active" if kind == "production" else "sample_active"
    return {"status": "attempt_reserved", "attempt_id": attempt_id, "ordinal": attempt["ordinal"], "asset_id": args.asset}


def generation_preflight(root, data, asset, kind):
    preflight = data.get("preflight", {})
    require(preflight.get("status") == "pass" and preflight.get("execution_engine") == "codex_builtin", "bind a passing builtin generation preflight before reserving an attempt")
    evidence = preflight.get("tool_capability_evidence", {})
    require(evidence.get("observed_at") and evidence.get("tool_name") and evidence.get("reference_inputs_verified") is True, "preflight requires observed tool and verified reference input support")
    verify_binding(root, {"path": evidence.get("evidence_path"), "sha256": evidence.get("sha256")})
    output = next((r for r in preflight.get("outputs", []) if r.get("output_contract_id") == asset["output_contract_id"]), {})
    require((output.get("target_width"), output.get("target_height"), output.get("target_format")) == (asset["width"], asset["height"], asset["format"]), "preflight target differs from the approved asset")
    require(output.get("typography_risk") in {"low", "sample_required", "verified"}, "classify typography feasibility before generation")
    native = output.get("native_size")
    if not native:
        pilot = kind == "production" and data.get("_authorized_pilot_asset_id") == asset["asset_id"]
        require((kind == "plan" or pilot) and output.get("transform") == "sample_probe" and asset["asset_id"] in output.get("sample_asset_ids", []) and output.get("unknown_size_reason"), "unknown native dimensions permit only an explicitly documented sample probe")
        return
    require(isinstance(native, list) and len(native) == 2 and all(type(n) is int and n > 0 for n in native), "invalid preflight native dimensions")
    factor = max(asset["width"] / native[0], asset["height"] / native[1])
    maximum = visual_validator().qa_option(data["policy"], "maximum_upscale_factor", 1.05)
    require(factor <= maximum, "preflight predicts unsafe upscaling; change native route or approved size before generation")
    same_ratio = asset["width"] * native[1] == asset["height"] * native[0]
    require(output.get("transform") in ({"none", "scale"} if same_ratio else {"safe_crop"}), "preflight must declare the required size adaptation")
    if not same_ratio:
        require(output.get("safe_crop_review"), "preflight safe crop needs a composition protection plan; actual pixels must be reviewed after generation")


def command_attempt(root, state, args):
    attempt = next((a for a in state["attempts"] if a["attempt_id"] == args.attempt_id), None)
    require(attempt and attempt["status"] == "reserved", "unknown or already recorded attempt")
    payload = read_json(args.file)
    if payload.get("status") == "not_started":
        require(not payload.get("tool_run_id") and payload.get("observations"), "not_started requires truthful dispatch observations and no invented tool_run_id")
        attempt.update({"status": "not_started", "observations": payload["observations"], "completed_at": utc_now()})
        return {"status": "interrupted_reservation_recorded", "attempt_id": args.attempt_id, "budget_consumed": True}
    require(payload.get("tool_run_id") and payload.get("engine") == "codex_builtin_imagegen", "record actual builtin generation tool_run_id and engine")
    if payload.get("status") == "failed":
        require(payload.get("failure_class") in FAILURES and payload.get("observations"), "failed execution needs classified observations")
        attempt.update({"status": "failed", "failure_class": payload["failure_class"], "observations": payload["observations"], "tool_run_id": payload["tool_run_id"], "completed_at": utc_now()})
        return {"status": "failure_recorded", "attempt_id": args.attempt_id}
    require(attempt["scope_hash"] == scope_hash(state, True), "inputs changed while generation was running; record failure and retry current revision")
    from PIL import Image
    path = (root / payload.get("image_path", "")).resolve()
    native = (root / payload.get("native_path", payload.get("image_path", ""))).resolve()
    for candidate in (path, native):
        with Image.open(candidate) as image:
            image.verify()
    attempt.update({"status": "candidate", "image": binding(root, path), "native": binding(root, native), "postprocess_history": payload.get("postprocess_history", []), "tool_run_id": payload["tool_run_id"], "completed_at": utc_now()})
    state["selected_candidates"][attempt["asset_id"]] = attempt["attempt_id"]
    invalidate_images(state, {attempt["asset_id"]})
    return {"status": "candidate_recorded", "attempt_id": args.attempt_id, "image": attempt["image"]}


def command_packet(root, state, args):
    require(state["state"] in {"sample_active", "awaiting_production_approval", "production_active", "qa_active"}, "review requires an active approved stage; revise a completed project first")
    data = controls(root, state)
    row = assets(data)[args.asset]
    attempt = current_attempt(state, args.asset)
    require(attempt is not None, "no image candidate available")
    require(not any(a["asset_id"] == args.asset and a["status"] == "reserved" for a in state["attempts"]), "generation still pending for review asset")
    verify_binding(root, attempt["image"])
    packet_id = "REVIEW-" + uuid.uuid4().hex[:16]
    # Whitelist actual requirements, never forward the creator's rationale, score or recommendation.
    requirement_ids = set(row["requirement_ids"])
    packet = {"schema_version": VERSION, "packet_id": packet_id, "asset_id": args.asset, "image": attempt["image"], "bindings": visual_validator().review_bindings(row, data), "review_task": "Inspect actual pixels independently against source facts and the agreed aesthetic rubric. Record observed defects and pass/fail evidence before reading any creator rationale.", "asset_contract": {k: row.get(k) for k in ("asset_id", "width", "height", "format", "purpose", "product_view_id", "visible_copy_ids", "cast_mode")}, "requirements": [r for r in data["requirements"]["requirements"] if r["requirement_id"] in requirement_ids], "product_master": data["product_master"], "aesthetics": data["aesthetics"], "copy_master": data["copy_master"], "policy": data["policy"], "physical_logic": data.get("physical_logic", {}), "creator_rationale_included": False}
    packet = sanitize_review_context(packet)
    packet["effective_execution_rules"] = effective_execution_rules(state, data["policy"])
    if streamlined(data) and data["_stage"] == "sample":
        packet["review_task"] = "Creator self-check of actual pixels and usage-size readability. Record defects and exact-copy checks; this is not independent final approval."
    revision = latest_revision(state, args.asset)
    if revision:
        packet["revision_request"] = {key: revision[key] for key in ("revision_id", "source", "observable_changes", "protected_items")}
    path = root / "review" / "packets" / f"{packet_id}.json"
    atomic_json(path, packet)
    state["packets"][packet_id] = {"binding": binding(root, path), "attempt_id": attempt["attempt_id"], "scope_hash": scope_hash(state, True), "asset_id": args.asset, "review_epoch": state["review_epochs"].get(args.asset, 0)}
    return {"status": "review_packet_created", "packet_id": packet_id, "path": str(path)}


def command_select_candidate(root, state, args):
    require(state["state"] in {"sample_active", "awaiting_production_approval", "production_active", "qa_active"}, "candidate selection requires an active approved stage")
    kind = "production" if state["approvals"].get("production", {}).get("valid") else "plan"
    approval = approval_valid(state, kind)
    attempt = next((a for a in state["attempts"] if a["attempt_id"] == args.attempt_id), None)
    require(attempt and attempt["status"] == "candidate" and attempt["asset_id"] in approval["asset_ids"], "candidate is missing or outside approved scope")
    require(not any(a["logical_asset_id"] == attempt["logical_asset_id"] and a["status"] == "reserved" for a in state["attempts"]), "generation still pending for this logical asset")
    verify_binding(root, attempt["image"])
    verify_binding(root, attempt["native"])
    state["selected_candidates"][attempt["asset_id"]] = attempt["attempt_id"]
    invalidate_images(state, {attempt["asset_id"]})
    state["state"] = "production_active" if kind == "production" else "sample_active"
    return {"status": "candidate_selected", "attempt_id": attempt["attempt_id"], "asset_id": attempt["asset_id"], "fresh_review_required": True, "attempt_budget_reset": False}


def manifest_record(attempt):
    return {"asset_id": attempt["asset_id"], "output_path": attempt["image"]["path"], "sha256": attempt["image"]["sha256"], "native_path": attempt["native"]["path"], "native_sha256": attempt["native"]["sha256"], "producer_id": attempt["producer_id"], "engine": attempt["engine"], "postprocess_history": attempt.get("postprocess_history", [])}


def sanitize_review_context(value):
    forbidden = {"creator_rationale", "creator_score", "self_score", "recommendation", "recommended_direction", "recommended_direction_id", "decision_reason", "winning_direction", "selection_reason", "candidates"}
    if isinstance(value, dict):
        return {key: sanitize_review_context(item) for key, item in value.items() if key not in forbidden}
    if isinstance(value, list):
        return [sanitize_review_context(item) for item in value]
    return value


def latest_revision(state, asset_id):
    return next((row for row in reversed(state["revisions"]) if asset_id in row["asset_ids"]), None)


def revision_review_errors(state, asset_id, report):
    revision = latest_revision(state, asset_id)
    if not revision:
        return []
    check = report.get("revision_check", {})
    errors = []
    if check.get("revision_id") != revision["revision_id"]:
        errors.append({"gate": "revision_binding", "asset_id": asset_id})
    for field, targets in (("changes", revision["observable_changes"]), ("protected_items", revision["protected_items"])):
        rows = check.get(field, [])
        for target in targets:
            if not any(row.get("target") == target and row.get("status") == "pass" and row.get("evidence") for row in rows):
                errors.append({"gate": "revision_" + field, "asset_id": asset_id, "detail": target})
    return errors


def command_review(root, state, args):
    require(state["state"] in {"sample_active", "awaiting_production_approval", "production_active", "qa_active"}, "review requires an active approved stage; revise a completed project first")
    report = read_json(args.file)
    packet = state["packets"].get(report.get("packet_id"))
    require(packet and packet["scope_hash"] == scope_hash(state, True), "unknown or stale independent review packet")
    verify_binding(root, packet["binding"])
    attempt = next(a for a in state["attempts"] if a["attempt_id"] == packet["attempt_id"])
    selected = current_attempt(state, packet["asset_id"])
    require(packet.get("review_epoch") == state["review_epochs"].get(packet["asset_id"], 0) and selected and selected["attempt_id"] == packet["attempt_id"], "review packet is superseded by a revision or candidate selection")
    require(report.get("asset_id") == packet["asset_id"], "wrong review asset")
    data = controls(root, state)
    self_check = streamlined(data) and data["_stage"] == "sample" and report.get("review_type") == "creator_self"
    kind = "production" if state["approvals"].get("production", {}).get("valid") else "plan"
    require(packet["asset_id"] in approval_valid(state, kind)["asset_ids"], "review asset is outside active approved scope")
    require(report.get("reviewer_id") and (report["reviewer_id"] == attempt["producer_id"]) == self_check and report.get("producer_id") == attempt["producer_id"], "creator cannot supply independent review")
    require(report.get("image_sha256") == attempt["image"]["sha256"], "review image hash mismatch")
    verify_binding(root, attempt["image"])
    require(report.get("reviewer_run_id") and (self_check or report.get("creator_rationale_seen") is False), "record actual independent reviewer run and first-round context isolation")
    data = controls(root, state)
    row = assets(data)[packet["asset_id"]]
    verify_binding(root, attempt["native"])
    validator = visual_validator()
    errors = validator.validate_review(row, report, root, data, manifest_record(attempt))
    errors += validator.validate_output(row, manifest_record(attempt), root, data["policy"])
    errors += revision_review_errors(state, packet["asset_id"], report)
    path = root / "qa" / "reviews" / f"{report['packet_id']}-{object_digest(report)}.json"
    atomic_json(path, report)
    state["reviews"][packet["asset_id"]] = {"attempt_id": attempt["attempt_id"], "passed": not errors, "stale": False, "image": attempt["image"], "report": binding(root, path), "reviewer_id": report["reviewer_id"], "errors": errors}
    state["set_reviews"] = {}
    state["state"] = "production_active" if state["approvals"].get("production", {}).get("valid") else "sample_active"
    if streamlined(data) and state["state"] == "sample_active":
        ids = approval_valid(state, "plan")["asset_ids"]
        try:
            require_reviews(root, state, ids)
        except ValueError:
            pass
        else:
            state["state"] = "awaiting_production_approval"
    return {"status": "review_recorded", "asset_id": packet["asset_id"], "passed": not errors, "errors": errors}


def command_final_review(root, state, args):
    """Record one independent review session, including changed images and set context."""
    approval_valid(state, "production")
    payload = read_json(args.file)
    reports = payload.get("assets", [])
    require(isinstance(reports, list), "final review assets must be a list")
    ids = [report.get("asset_id") for report in reports]
    require(len(ids) == len(set(ids)), "duplicate final review asset")
    session = payload.get("set_review", {})
    require(session.get("reviewer_run_id"), "final review requires actual reviewer session")
    results = []
    for report in reports:
        require(report.get("reviewer_run_id") == session["reviewer_run_id"] and report.get("reviewer_id") == session.get("reviewer_id"), "final review reports must belong to the same reviewer session")
        path = root / "qa" / "session-inputs" / f"{object_digest(report)}.json"
        atomic_json(path, report)
        results.append(command_review(root, state, SimpleNamespace(file=path)))
    if any(not result["passed"] for result in results):
        return {"status": "final_review_recorded", "passed": False, "assets": results}
    path = root / "qa" / "session-inputs" / f"{object_digest(session)}.json"
    atomic_json(path, session)
    return command_set_review(root, state, SimpleNamespace(file=path))


def command_packets(root, state, args):
    require(state["state"] in {"production_active", "qa_active"}, "final review packets require active production; revise completed work first")
    approval = approval_valid(state, "production")
    ids = approval["asset_ids"]
    packets = []
    for aid in ids:
        try:
            require_reviews(root, state, [aid])
        except ValueError:
            packets.append(command_packet(root, state, SimpleNamespace(asset=aid)))
    context = {"asset_ids": ids, "scope_hash": scope_hash(state, True), "image_bindings": [{"asset_id": aid, "path": current_attempt(state, aid)["image"]["path"], "sha256": current_attempt(state, aid)["image"]["sha256"]} for aid in ids]}
    return {"status": "final_review_packets_created", "packets": packets, "set_context": context, "reused_review_asset_ids": [aid for aid in ids if aid not in {state["packets"][p["packet_id"]]["asset_id"] for p in packets}]}


def command_set_review(root, state, args):
    require(state["state"] in {"sample_active", "awaiting_production_approval", "production_active", "qa_active"}, "set review requires an active approved stage; revise a completed project first")
    kind = "production" if state["approvals"].get("production", {}).get("valid") else "plan"
    approval = approval_valid(state, kind)
    ids = approval["asset_ids"]
    require_reviews(root, state, ids)
    report = read_json(args.file)
    require(set(report.get("asset_ids", [])) == set(ids), "set review must cover the exact approved scope")
    require(report.get("scope_hash") == scope_hash(state, True), "stale set review inputs")
    producers = {current_attempt(state, aid)["producer_id"] for aid in ids}
    require(report.get("reviewer_id") and report["reviewer_id"] not in producers and report.get("reviewer_run_id"), "set reviewer must be independent of all creators")
    require(report.get("creator_rationale_seen") is False, "set review first round must exclude creator rationale")
    images = {r["asset_id"]: r["sha256"] for r in report.get("image_bindings", [])}
    require(images == {aid: current_attempt(state, aid)["image"]["sha256"] for aid in ids}, "set review image coverage/hash mismatch")
    for name in ("within_direction_consistency", "within_direction_diversity", "cross_direction_diversity", "actual_use_readability"):
        result = report.get("gates", {}).get(name, {})
        require(result.get("status") in {"pass", "fail", "not_applicable"} and result.get("evidence"), f"missing observable set gate: {name}")
        if result["status"] == "not_applicable":
            rows = assets(controls(root, state))
            directions = {rows[aid]["direction_id"] for aid in ids}
            require(name == "cross_direction_diversity" and len(directions) == 1 or name in {"within_direction_consistency", "within_direction_diversity"} and len(ids) == len(directions), f"set gate cannot be skipped: {name}")
    passed = all(g["status"] != "fail" for g in report["gates"].values())
    path = root / "qa" / "set-reviews" / f"{object_digest(report)}.json"
    atomic_json(path, report)
    key = "production" if kind == "production" else "samples"
    state["set_reviews"][key] = {"passed": passed, "report": binding(root, path), "scope_hash": scope_hash(state, True)}
    state["state"] = ("qa_active" if kind == "production" else "awaiting_production_approval") if passed else ("production_active" if kind == "production" else "sample_active")
    return {"status": "set_review_recorded", "passed": passed, "production_scope_hash": scope_hash(state, True)}


def command_revise(root, state, args):
    payload = read_json(args.file)
    source = source_confirmation(payload)
    kind = "production" if state["approvals"].get("production", {}).get("valid") else "plan"
    approval = approval_valid(state, kind)
    selected = payload.get("asset_ids", [])
    require(selected and set(selected) <= set(approval["asset_ids"]), "revision is outside approved scope")
    require(payload.get("observable_changes") and payload.get("protected_items"), "revision requires observable changes and protected passing content")
    require(payload.get("changes_product_facts") is False, "fact/copy/view changes require upstream plan reapproval")
    revision = {"revision_id": "REV-" + uuid.uuid4().hex[:12], "source": source, "asset_ids": selected, "observable_changes": payload["observable_changes"], "protected_items": payload["protected_items"], "before_bindings": deepcopy(state["artifacts"]), "rebound_roles": [], "open": True, "created_at": utc_now()}
    for previous in state["revisions"]:
        previous["open"] = False
    state["revisions"].append(revision)
    invalidate_images(state, set(selected))
    state["state"] = "production_active" if kind == "production" else "sample_active"
    return {"status": "revision_opened", "revision_id": revision["revision_id"], "affected_asset_ids": selected, "attempt_budget_reset": False}


def command_extend_budget(root, state, args):
    require(state["state"] in {"production_active", "qa_active"}, "budget extension requires active production")
    approval = approval_valid(state, "production")
    payload = read_json(args.file)
    source = source_confirmation(payload)
    ids = payload.get("asset_ids", [])
    extra = payload.get("additional_attempts")
    require(ids and len(ids) == len(set(ids)) and set(ids) <= set(approval["asset_ids"]), "invalid budget extension scope")
    require(type(extra) is int and extra > 0, "additional attempts must be a positive integer")
    require(payload.get("pilot_asset_id") in ids, "identify the authorized pilot asset")
    existing = state.setdefault("budget_extensions", {})
    require(not any(v["source"] == source for v in existing.values()), "this authorization has already been recorded")
    rows = assets(controls(root, state))
    for aid in ids:
        lid = rows[aid]["logical_asset_id"]
        consumed = sum(a["logical_asset_id"] == lid for a in state["attempts"])
        previous = existing.get(lid, {}).get("ceiling", ATTEMPT_LIMIT)
        existing[lid] = {"ceiling": max(consumed, previous) + extra, "additional_attempts": extra, "source": source, "pilot_asset_id": payload["pilot_asset_id"], "recorded_at": utc_now()}
    approval["max_total_attempts"] += len(ids) * extra
    return {"status": "budget_extended", "asset_ids": ids, "additional_attempts": extra, "pilot_asset_id": payload["pilot_asset_id"], "source": source, "history_preserved": True}


def command_finalize(root, state, args):
    require(state["state"] == "qa_active", "finalize requires production QA")
    approval = approval_valid(state, "production")
    data = validate_plan(root, state, True)
    ids = approval["asset_ids"]
    require_reviews(root, state, ids)
    set_review = state["set_reviews"].get("production", {})
    require(set_review.get("passed") and set_review.get("scope_hash") == scope_hash(state, True), "current production set review missing")
    verify_binding(root, set_review["report"])
    delivered = []
    for aid in ids:
        attempt = current_attempt(state, aid)
        source = verify_binding(root, attempt["image"])
        suffix = source.suffix.lower()
        require(suffix in {".png", ".jpg", ".jpeg", ".webp"}, "unsupported image delivery format")
        destination = within(root, root / "outputs" / f"r{state['state_revision'] + 1:06d}" / f"{identifier(aid)}{suffix}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        require(not destination.exists() or digest(destination) == digest(source), "refusing conflicting delivery file")
        shutil.copyfile(source, destination)
        delivered.append({"asset_id": aid, **binding(root, destination), "review": state["reviews"][aid]["report"]})
    delivery_root = root / "outputs" / f"r{state['state_revision'] + 1:06d}"
    support_files = export_delivery_support(root, state, data, delivery_root, delivered)
    package = {"schema_version": VERSION, "project_id": state["project_id"], "status": "complete", "approval": approval, "assets": delivered, "support_files": support_files, "not_selected_asset_ids": sorted(set(assets(data)) - set(ids)), "prompt_package": state["artifacts"]["prompt_package"], "set_review": set_review["report"], "created_at": utc_now()}
    atomic_json(root / "outputs" / "delivery_manifest.json", package)
    state["delivery"] = binding(root, root / "outputs" / "delivery_manifest.json")
    state["state"] = "complete"
    for revision in state["revisions"]:
        revision["open"] = False
    return {"status": "complete", "delivered_count": len(delivered), "manifest": state["delivery"]}


def command_export_partial(root, state, args):
    """Export individually passing work without declaring the approved set complete."""
    require(state["state"] in {"production_active", "qa_active"}, "partial export requires active production")
    approval = approval_valid(state, "production")
    require(approval.get("final_delivery_authorized"), "production delivery is not authorized")
    data = validate_plan(root, state, True)
    unchecked = getattr(args, "unchecked", False)
    if unchecked:
        require(visual_validator().workflow_skip(data["policy"], "final_review"), "unreviewed export requires explicit user final_review override")
    delivered, unfinished = [], []
    status = "delivered_unreviewed" if unchecked else "partial"
    destination = root / "outputs" / f"{status}-r{state['state_revision'] + 1:06d}"
    for aid in approval["asset_ids"]:
        try:
            if unchecked:
                attempt = current_attempt(state, aid)
                require(attempt and not any(a["asset_id"] == aid and a["status"] == "reserved" for a in state["attempts"]), f"missing or pending candidate: {aid}")
                verify_binding(root, attempt["image"])
                verify_binding(root, attempt["native"])
                errors = visual_validator().validate_output(assets(data)[aid], manifest_record(attempt), root, data["policy"])
                require(not errors, f"output file validation failed: {errors}")
            else:
                require_reviews(root, state, [aid])
        except (ValueError, OSError, KeyError) as error:
            unfinished.append({"asset_id": aid, "reason": str(error), "errors": state["reviews"].get(aid, {}).get("errors", [])})
        else:
            attempt = current_attempt(state, aid)
            source = verify_binding(root, attempt["image"])
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / f"{aid}{source.suffix.lower()}"
            shutil.copyfile(source, target)
            if unchecked:
                note = destination / f"{aid}.unchecked.json"
                atomic_json(note, {"status": "skipped_by_user", "source": data["policy"]["workflow_overrides"]["source"], "image": attempt["image"], "previous_review": state["reviews"].get(aid), "quality_certified": False})
                review = binding(root, note)
            else:
                review = state["reviews"][aid]["report"]
            delivered.append({"asset_id": aid, **binding(root, target), "review": review})
    require(delivered, "no exportable assets")
    support = export_delivery_support(root, state, data, destination, delivered, partial=True)
    if unchecked:
        overview = destination / "overview.html"
        overview.write_text(overview.read_text(encoding="utf-8").replace("部分交付：单图已通过", "按用户要求未审核交付").replace("项目未完成；本导出不代表整套已通过。", "本导出未认证单图或套图质量，不代表审核通过。"), encoding="utf-8")
        support = [binding(root, root / row["path"]) if row["path"] == str(overview.relative_to(root)) else row for row in support]
    package = {"schema_version": VERSION, "project_id": state["project_id"], "status": status, "approval": approval, "assets": delivered, "support_files": support, "unfinished_assets": unfinished, "not_selected_asset_ids": sorted(set(assets(data)) - set(approval["asset_ids"])), "set_qa_status": "skipped_by_user" if unchecked else "not_certified_by_partial_export", "prompt_package": state["artifacts"]["prompt_package"], "created_at": utc_now()}
    path = destination / "delivery_manifest.json"
    atomic_json(path, package)
    key = "unreviewed_delivery" if unchecked else "partial_delivery"
    state[key] = binding(root, path)
    return {"status": status, "delivered_count": len(delivered), "unfinished_count": len(unfinished), "manifest": state[key]}


def command_export_unreviewed(root, state, args):
    return command_export_partial(root, state, SimpleNamespace(unchecked=True))


def export_delivery_support(root, state, data, destination, delivered, partial=False):
    """Copy portable prompts, their one image input, and reviewable set records."""
    files, cards = [], []
    prompts = {row["asset_id"]: row for row in data["prompt_package"]["assets"]}
    for item in delivered:
        aid = item["asset_id"]
        prompt = prompts[aid]
        prompt_path = destination / f"{aid}.prompt.txt"
        prompt_path.write_bytes(prompt["prompt_zh"].encode("utf-8"))
        require(digest(prompt_path) == prompt["prompt_sha256"], "exported prompt digest differs from compiled prompt")
        reference = prompt["attachments"][0]
        source = verify_binding(root, {"path": reference["local_path"], "sha256": reference["sha256"]})
        reference_path = destination / f"{aid}.product-reference{source.suffix.lower()}"
        shutil.copyfile(source, reference_path)
        review_path = destination / f"{aid}.review.json"
        shutil.copyfile(verify_binding(root, item["review"]), review_path)
        files.extend(binding(root, path) for path in (prompt_path, reference_path, review_path))
        image_name = Path(item["path"]).name
        cards.append(f'<article><h2>{html.escape(aid)}</h2><a href="{html.escape(image_name, quote=True)}"><img src="{html.escape(image_name, quote=True)}" alt="{html.escape(aid, quote=True)}"></a><p><a href="{prompt_path.name}">完整 Prompt</a> · <a href="{reference_path.name}">产品身份图</a> · <a href="{review_path.name}">质量记录</a></p></article>')
    overview = destination / "overview.html"
    overview.write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>套图总览</title><style>body{font:16px system-ui;margin:32px;background:#f6f5f2;color:#222}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px}article{background:white;padding:20px}img{width:100%;height:360px;object-fit:contain}a{color:#264f77}</style><h1>套图总览</h1><p>点击查看原始成品；与实际使用尺寸的检查记录一并评估。</p><main>' + ''.join(cards) + '</main></html>', encoding="utf-8")
    rows = assets(data)
    selected_directions = {rows[item["asset_id"]]["direction_id"] for item in delivered}
    styles = [row for row in data["directions"]["directions"] if row["direction_id"] in selected_directions]
    style_path = destination / "style-guide.md"
    content = ["# 已确认风格规范", "品牌规则：", json.dumps(data["product_master"].get("brand_rules", []), ensure_ascii=False, indent=2)]
    for direction in styles:
        content += ["## " + direction["direction_id"], str(direction.get("core_concept", "")), "```json\n" + json.dumps({k: direction.get(k) for k in ("style_rules", "visual_organization", "photography_or_expression", "prohibitions")}, ensure_ascii=False, indent=2) + "\n```"]
    content += ["## 文字与使用场景", "```json\n" + json.dumps(data["typography"], ensure_ascii=False, indent=2) + "\n```"]
    style_path.write_text("\n\n".join(content) + "\n", encoding="utf-8")
    version_path = destination / "version-record.json"
    atomic_json(version_path, {"schema_version": VERSION, "project_id": state["project_id"], "delivery_revision": state["state_revision"] + 1, "selected_candidates": {item["asset_id"]: current_attempt(state, item["asset_id"])["attempt_id"] for item in delivered}, "approvals": state["approvals"], "attempts": state["attempts"], "revisions": state["revisions"], "artifact_bindings": state["artifacts"]})
    if partial:
        overview.write_text(overview.read_text(encoding="utf-8").replace("<h1>套图总览</h1>", "<h1>部分交付：单图已通过</h1><p>项目未完成；本导出不代表整套已通过。未完成项见 delivery_manifest.json。</p>"), encoding="utf-8")
    else:
        set_path = destination / "set-review.json"
        shutil.copyfile(verify_binding(root, state["set_reviews"]["production"]["report"]), set_path)
        files.append(binding(root, set_path))
    files.extend(binding(root, path) for path in (overview, style_path, version_path))
    return files


def inspect(root, state):
    errors = []
    execution_rules = {}
    try:
        data = controls(root, state)
        execution_rules = effective_execution_rules(state, data.get("policy", {}))
        for item in state["attempts"]:
            if item["status"] == "candidate":
                verify_binding(root, item["image"])
                verify_binding(root, item["native"])
        for key, approval in state["approvals"].items():
            if approval.get("valid"):
                approval_valid(state, key)
        if state.get("partial_delivery"):
            partial = read_json(verify_binding(root, state["partial_delivery"]))
            require(partial.get("status") == "partial", "partial export cannot declare completion")
            for row in partial["assets"] + partial.get("support_files", []):
                verify_binding(root, row)
        if state.get("unreviewed_delivery"):
            package = read_json(verify_binding(root, state["unreviewed_delivery"]))
            require(package.get("status") == "delivered_unreviewed" and package.get("set_qa_status") == "skipped_by_user", "unreviewed delivery cannot certify quality")
            for row in package["assets"] + package.get("support_files", []):
                verify_binding(root, row)
        if state["state"] == "complete":
            validate_plan(root, state, True)
            approval = approval_valid(state, "production")
            require_reviews(root, state, approval["asset_ids"])
            set_review = state["set_reviews"].get("production", {})
            require(set_review.get("passed") and set_review.get("scope_hash") == scope_hash(state, True), "current passing production set review missing")
            verify_binding(root, set_review["report"])
            manifest = read_json(verify_binding(root, state["delivery"]))
            require({r["asset_id"] for r in manifest["assets"]} == set(approval["asset_ids"]), "incomplete delivery manifest")
            for row in manifest["assets"]:
                verify_binding(root, row)
            for row in manifest.get("support_files", []):
                verify_binding(root, row)
    except (ValueError, OSError, KeyError) as exc:
        errors.append(str(exc))
    sample_images = {aid: current_attempt(state, aid)["image"]["sha256"] for aid in state["approvals"].get("plan", {}).get("asset_ids", []) if current_attempt(state, aid)}
    return {"status": "pass" if not errors else "fail", "schema_version": VERSION, "project_id": state["project_id"], "brand_id": state["brand_id"], "state": state["state"], "state_revision": state["state_revision"], "scope_hash": scope_hash(state), "production_scope_hash": scope_hash(state, True), "sample_image_bindings": sample_images, "attempt_count": len(state["attempts"]), "effective_execution_rules": execution_rules, "failed_reviews": {aid: r["errors"] for aid, r in state["reviews"].items() if not r["passed"]}, "errors": errors}


def parser():
    top = ArgumentParser(description=__doc__)
    commands = top.add_subparsers(dest="command", required=True)
    for name in ("init", "bind", "bind-bundle", "status", "validate", "prepare-plan", "approve-plan", "approve-production", "reserve-attempt", "record-attempt", "select-candidate", "review-packet", "review-packets", "record-review", "record-set-review", "record-final-review", "revise", "finalize", "export-partial", "export-unreviewed", "extend-budget", "export-controls"):
        item = commands.add_parser(name)
        item.add_argument("--project-root", required=True, type=Path)
        if name not in {"init", "status", "validate", "export-controls"}:
            item.add_argument("--expect-revision", required=True, type=int)
        if name == "init":
            item.add_argument("--workflow-profile", choices=("streamlined", "classic"), default="streamlined")
            item.add_argument("--direction-mode", choices=("explore", "existing", "automatic"), default="explore")
            item.add_argument("--project-id", required=True)
            item.add_argument("--brand-id", required=True)
            item.add_argument("--category", default="auto_detect", help="free-text category; no predefined category whitelist")
        if name == "bind":
            item.add_argument("--role", choices=sorted(ROLES), required=True)
        if name in {"bind", "bind-bundle", "approve-plan", "approve-production", "record-attempt", "record-review", "record-set-review", "record-final-review", "revise", "extend-budget"}:
            item.add_argument("--file", required=True, type=Path)
        if name in {"review-packet", "reserve-attempt"}:
            item.add_argument("--asset", required=True)
        if name == "reserve-attempt":
            item.add_argument("--producer-id", required=True)
            item.add_argument("--file", type=Path, help="retry diagnosis JSON")
        if name in {"record-attempt", "select-candidate"}:
            item.add_argument("--attempt-id", required=True)
    memory = commands.add_parser("memory")
    memory.add_argument("action", choices=("record", "query", "invalidate"))
    memory.add_argument("--memory-root", required=True, type=Path)
    memory.add_argument("--file", type=Path)
    memory.add_argument("--brand-id")
    memory.add_argument("--project-id")
    memory.add_argument("--campaign-id")
    memory.add_argument("--asset-id")
    return top


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "memory":
            from brand_memory import invalidate_memory, query_memory, record_memory
            require(args.file or args.action == "query", "memory record/invalidate require --file")
            payload = read_json(args.file) if args.file else {}
            if args.action == "record":
                result = record_memory(args.memory_root.resolve(), payload)
            elif args.action == "query":
                require(args.brand_id, "memory query requires --brand-id")
                result = query_memory(args.memory_root.resolve(), args.brand_id, args.project_id, args.campaign_id, args.asset_id)
            else:
                result = invalidate_memory(args.memory_root.resolve(), payload["record_id"], payload["brand_id"], payload["reason"], payload["source"])
        else:
            root = args.project_root.resolve()
            if args.command in {"status", "validate"}:
                result = inspect(root, load_state(root))
            elif args.command == "export-controls":
                state = load_state(root)
                approval_valid(state, "plan")
                target = root / "control" / "authorized" / f"r{state['state_revision']:06d}"
                for role, value in controls(root, state).items():
                    if not role.startswith("_"):
                        atomic_json(target / f"{role}.json", value)
                result = {"status": "exported", "path": str(target), "state_revision": state["state_revision"]}
            else:
                with file_lock(root / ".twin-stars.lock"):
                    if args.command == "init":
                        result = init(root, args)
                    else:
                        old = load_state(root)
                        require(old["state_revision"] == args.expect_revision, f"stale state revision: expected {args.expect_revision}, found {old['state_revision']}")
                        state = deepcopy(old)
                        if args.command == "prepare-plan":
                            require(state["state"] in {"intake", "strategy_active", "awaiting_plan_approval"}, "cannot prepare a replacement plan during active production; bind changed inputs first")
                            data = validate_plan(root, state)
                            state["state"] = "awaiting_plan_approval"
                            details = {"status": "awaiting_plan_approval", "scope_hash": scope_hash(state), "sample_asset_ids": sample_ids(assets(data), data["policy"])}
                        else:
                            handlers = {"bind": command_bind, "bind-bundle": command_bind_bundle, "approve-plan": command_approve_plan, "approve-production": command_approve_production, "reserve-attempt": command_reserve, "record-attempt": command_attempt, "select-candidate": command_select_candidate, "review-packet": command_packet, "review-packets": command_packets, "record-review": command_review, "record-set-review": command_set_review, "record-final-review": command_final_review, "revise": command_revise, "finalize": command_finalize}
                            handlers["export-partial"] = command_export_partial
                            handlers["export-unreviewed"] = command_export_unreviewed
                            handlers["extend-budget"] = command_extend_budget
                            details = handlers[args.command](root, state, args)
                        result = commit(root, old, state, args.command, details)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if isinstance(result, dict) and result.get("status") == "fail" else 0
    except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        print(json.dumps({"schema_version": VERSION, "status": "fail", "errors": [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
