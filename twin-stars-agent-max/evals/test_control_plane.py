import json
import hashlib
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from migrate_project import migrate_project, migrate_state  # noqa: E402
from validate_workflow_state import HARD_GATE_KEYS, SCHEMA_VERSION, validate_market_research, validate_state  # noqa: E402
from twin_stars import production_runs  # noqa: E402


RUNNER = SCRIPTS / "twin_stars.py"


def run(*args):
    return subprocess.run([sys.executable, str(RUNNER), *map(str, args)], capture_output=True, text=True)


def read_state(root):
    return json.loads((root / "control" / "workflow_state.json").read_text(encoding="utf-8"))


class ControlPlaneEvals(unittest.TestCase):
    def init_project(self, root):
        result = run("init", "--project-root", root, "--project-id", "P-EVAL", "--profile", "auto_concept_fast")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return read_state(root)

    def test_init_is_valid_and_preserves_integrated_generation_policy(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            state = self.init_project(root)
            self.assertEqual(validate_state(state, root), [])
            policy = json.loads((root / "control" / "project_policy.json").read_text(encoding="utf-8"))
            self.assertEqual(policy["generation_policy"]["mode"], "integrated_full_canvas")
            self.assertTrue(policy["generation_policy"]["separate_layer_delivery_forbidden"])
            self.assertEqual([run["agent_name"] for run in state["support_agent_runs"]], ["market_intelligence", "visual_reference_research"])
            self.assertTrue(policy["market_research_policy"]["target_selling_platform_required"])
            self.assertEqual(policy["multi_agent_policy"]["max_concurrent_production_agents"], 3)

    def test_missing_or_wrong_schema_is_rejected(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            state = self.init_project(root)
            state.pop("schema_version")
            errors = validate_state(state, root)
            self.assertTrue(any(error.startswith("missing:schema_version") for error in errors))
            self.assertTrue(any(error.startswith("schema_version:") for error in errors))
            state["schema_version"] = "5.0"
            self.assertTrue(any(error.startswith("schema_version:") for error in validate_state(state, root)))

    def test_gate_values_not_key_presence_control_advancement(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            state = self.init_project(root)
            state.update({"state": "strategy_active", "previous_state": "awaiting_evidence_approval"})
            errors = validate_state(state, root)
            self.assertIn("hard_gate_not_passed:product_master", errors)
            self.assertIn("hard_gate_not_passed:requirements_execution", errors)
            self.assertTrue(HARD_GATE_KEYS.issubset(state["hard_gate_status"]))

    def test_blockers_force_advance_false(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            state = self.init_project(root)
            state.update({"blockers": ["missing evidence"], "advance_allowed": True})
            self.assertIn("blocked_advance", validate_state(state, root))

    def test_runner_rejects_stale_revision(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self.init_project(root)
            result = run("transition", "--project-root", root, "--expect-revision", "1", "--to", "awaiting_evidence_approval", "--next-action", "review evidence")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("stale state revision", result.stdout)

    def test_runner_requires_explicit_confirmation_to_leave_gate(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self.init_project(root)
            first = run("transition", "--project-root", root, "--expect-revision", "0", "--to", "awaiting_evidence_approval", "--next-action", "await user")
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            second = run("transition", "--project-root", root, "--expect-revision", "1", "--to", "strategy_active", "--next-action", "build strategy")
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("requires --user-confirmation", second.stdout)

    def test_resumed_state_cannot_jump_to_an_unrelated_stage(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            state = self.init_project(root)
            state.update({"state": "resumed", "previous_state": "blocked", "resume_state": "strategy_active"})
            errors = validate_state(state, root)
            self.assertEqual(errors, [])
            state.update({"state": "production_active", "previous_state": "resumed"})
            self.assertIn("resume_target", validate_state(state, root))

    def test_runner_records_gate_failure_and_resumes_exact_state(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self.init_project(root)
            failed = run("gate", "--project-root", root, "--expect-revision", "0", "--name", "product_master", "--status", "fail_missing_view", "--reason", "missing product view")
            self.assertEqual(failed.returncode, 0, failed.stdout + failed.stderr)
            resumed = run("transition", "--project-root", root, "--expect-revision", "1", "--to", "resumed", "--next-action", "continue intake", "--resolve-blocker", "missing product view")
            self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
            returned = run("transition", "--project-root", root, "--expect-revision", "2", "--to", "intake_active", "--next-action", "inventory supplied files")
            self.assertEqual(returned.returncode, 0, returned.stdout + returned.stderr)
            self.assertEqual(read_state(root)["state_revision"], 3)
            self.assertEqual(len(list((root / "history" / "state").glob("*.json"))), 3)

    def test_direction_provenance_requires_real_hash_bound_files(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            state = self.init_project(root)
            state.update({"state": "awaiting_direction_approval", "previous_state": "strategy_active", "confirmation_required": True, "confirmation_status": "pending"})
            state["execution_authorization"] = {"granted_by": "user", "status": "active", "scope": {"through_state": "sample_active", "allow_research": True, "allow_three_directions": True, "allow_prompt_compilation": True, "allow_comparison_samples": True, "allow_concept_presentations": True, "formal_production": False, "final_delivery": False}}
            for gate in ("product_master", "requirements_execution", "reference_visual_utility", "direction_divergence", "style_branch_divergence"):
                state["hard_gate_status"][gate] = "pass"
            state["barrier_status"]["round_a_direction_references"] = "pass"
            state["agent_execution"]["parallel_window"] = {"started_at": "2026-09-04T10:00:00+08:00", "all_agents_active_at": "2026-09-04T10:00:00+08:00", "first_agent_completed_at": "2026-09-04T10:20:00+08:00"}
            for item in state["active_agent_runs"]:
                item.update({"status": "completed", "started_at": "2026-09-04T10:00:00+08:00", "completed_at": "2026-09-04T10:20:00+08:00", "last_handoff_at": "2026-09-04T10:21:00+08:00", "input_lock_sha256": "a" * 64, "handoff_sha256": "b" * 64})
                item["round_status"]["round_a"] = "pass"
                item["round_timing"]["round_a"] = {"started_at": "2026-09-04T10:00:00+08:00", "completed_at": "2026-09-04T10:20:00+08:00", "handoff_sha256": "b" * 64}
            errors = validate_state(state, root)
            self.assertTrue(any("input_lock_path_binding" in error for error in errors))
            self.assertTrue(any("handoff_path_binding" in error for error in errors))

    def test_unversioned_intake_migrates_without_inventing_semantics(self):
        legacy = {"project_id": "P", "run_profile": "auto_concept", "state": "intake_active", "previous_state": None}
        migrated, report = migrate_state(legacy, "2026-09-04T00:00:00+00:00")
        self.assertEqual(migrated["schema_version"], SCHEMA_VERSION)
        self.assertEqual(migrated["run_profile"], "auto_concept_fast")
        self.assertEqual(report["manual_review"], [])
        with TemporaryDirectory() as folder:
            self.assertEqual(validate_state(migrated, Path(folder)), [])

    def test_post_intake_migration_blocks_and_reports_old_asset_plans(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "control").mkdir()
            legacy = {"project_id": "P", "run_profile": "production_selected", "state": "production_active", "previous_state": "awaiting_production_approval"}
            (root / "workflow_state.json").write_text(json.dumps(legacy), encoding="utf-8")
            (root / "asset_plan.json").write_text(json.dumps({"schema_version": "5.0", "assets": []}), encoding="utf-8")
            report = migrate_project(root)
            self.assertEqual(report["status"], "manual_review_required")
            self.assertEqual(report["artifacts"][0]["action"], "manual_semantic_migration_required")
            self.assertFalse(report["applied"])

    def test_production_shards_are_maximally_parallel_without_overlap(self):
        one_direction = {f"D1-A{index}": "D1" for index in range(1, 6)}
        runs = production_runs(one_direction, "PROD-1")
        self.assertEqual(len(runs), 3)
        assigned = [asset_id for run in runs for asset_id in run["asset_ids"]]
        self.assertEqual(set(assigned), set(one_direction))
        self.assertEqual(len(assigned), len(set(assigned)))

        three_directions = {"D1-A": "D1", "D2-A": "D2", "D3-A": "D3"}
        runs = production_runs(three_directions, "PROD-2")
        self.assertEqual({run["direction_id"] for run in runs}, {"D1", "D2", "D3"})

    def test_market_research_rejects_cross_platform_core_competitor(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            record = {
                "schema_version": SCHEMA_VERSION,
                "scope": {"target_selling_platform": "Amazon", "target_country_or_region": "US", "category": "Office Chairs"},
                "competitors": [{
                    "competitor_id": f"C-{index}", "platform": "Walmart" if index == 10 else "Amazon", "country_or_region": "US", "category": "Office Chairs",
                    "source_url": f"https://example.com/{index}", "retrieved_at": "2026-09-04T00:00:00+00:00",
                    "observable_performance": [{"signal_type": "dated_category_or_bestseller_rank", "value": f"#{index}", "source_url": f"https://example.com/rank/{index}", "observed_at": "2026-09-04T00:00:00+00:00"}],
                } for index in range(1, 11)],
                "negative_feedback_records": [{"competitor_id": f"C-{index % 6 + 1}", "source_url": f"https://example.com/review/{index}", "retrieved_at": "2026-09-04T00:00:00+00:00", "problem_summary": "observable problem"} for index in range(30)],
                "issue_themes": [{"theme_id": "ISSUE-1"}],
                "product_fact_authority": False,
            }
            path = root / "market.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            state = {"market_research_binding": {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
            errors = []
            validate_market_research(state, root, errors)
            self.assertIn("market_competitor:C-10:target_platform", errors)

    def test_authorize_three_direction_production_creates_three_workers(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            state = self.init_project(root)
            state.update({
                "state": "awaiting_production_approval", "previous_state": "sample_active",
                "confirmation_required": True, "confirmation_status": "pending",
                "confirmation_history": [{"gate": "awaiting_direction_approval", "status": "approved", "source": "explicit_user_confirmation"}],
                "execution_authorization": {"granted_by": "user", "status": "active", "scope": {"through_state": "sample_active", "allow_research": True, "allow_three_directions": True, "allow_prompt_compilation": True, "allow_comparison_samples": True, "allow_concept_presentations": True, "formal_production": False, "final_delivery": False}},
                "barrier_status": {"market_intelligence": "pass", "visual_reference_research": "pass", "round_a_direction_references": "pass", "round_b_set_prompts": "pass", "round_c_concept_images": "pass", "formal_production": "pending"},
                "updated_at": "2026-09-04T10:30:00+08:00",
            })
            for gate in ("product_master", "requirements_execution", "market_intelligence", "reference_visual_utility", "direction_divergence", "style_branch_divergence", "within_direction_set_diversity", "cross_direction_selling_point_diversity", "portable_prompt_self_containment"):
                state["hard_gate_status"][gate] = "pass"
            state["support_agent_execution"]["parallel_window"] = {"started_at": "2026-09-04T09:00:00+08:00", "all_agents_active_at": "2026-09-04T09:00:00+08:00", "first_agent_completed_at": "2026-09-04T09:20:00+08:00"}
            for support in state["support_agent_runs"]:
                support.update({"status": "completed", "started_at": "2026-09-04T09:00:00+08:00", "completed_at": "2026-09-04T09:20:00+08:00", "last_handoff_at": "2026-09-04T09:21:00+08:00"})
            state["agent_execution"]["parallel_window"] = {"started_at": "2026-09-04T10:00:00+08:00", "all_agents_active_at": "2026-09-04T10:00:00+08:00", "first_agent_completed_at": "2026-09-04T10:20:00+08:00"}
            for direction_run in state["active_agent_runs"]:
                direction_run.update({"status": "completed", "started_at": "2026-09-04T10:00:00+08:00", "completed_at": "2026-09-04T10:20:00+08:00", "last_handoff_at": "2026-09-04T10:21:00+08:00"})
                for round_id in ("round_a", "round_b", "round_c"):
                    direction_run["round_status"][round_id] = "pass"
                    direction_run["round_timing"][round_id] = {"started_at": "2026-09-04T10:00:00+08:00", "completed_at": "2026-09-04T10:20:00+08:00", "handoff_sha256": ""}
            for agent_run in [*state["support_agent_runs"], *state["active_agent_runs"]]:
                for path_field, hash_field in (("input_lock_path", "input_lock_sha256"), ("handoff_path", "handoff_sha256")):
                    path = root / agent_run[path_field]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(path_field, encoding="utf-8")
                    agent_run[hash_field] = hashlib.sha256(path.read_bytes()).hexdigest()
                for timing in agent_run.get("round_timing", {}).values():
                    timing["handoff_sha256"] = agent_run["handoff_sha256"]

            market = {
                "schema_version": SCHEMA_VERSION, "scope": {"target_selling_platform": "Amazon", "target_country_or_region": "US", "category": "Office Chairs"},
                "competitors": [{"competitor_id": f"C-{index}", "platform": "Amazon", "country_or_region": "US", "category": "Office Chairs", "source_url": f"https://amazon.com/C{index}", "retrieved_at": "2026-09-04T09:00:00+08:00", "observable_performance": [{"signal_type": "dated_category_or_bestseller_rank", "value": f"#{index}", "source_url": f"https://amazon.com/rank/C{index}", "observed_at": "2026-09-04T09:00:00+08:00"}]} for index in range(1, 11)],
                "negative_feedback_records": [{"competitor_id": f"C-{index % 6 + 1}", "source_url": f"https://amazon.com/review/{index}", "retrieved_at": "2026-09-04T09:00:00+08:00", "problem_summary": "sample problem"} for index in range(30)],
                "issue_themes": [{"theme_id": "T-1"}], "product_fact_authority": False,
            }
            market_path = root / "strategy" / "market_research_index.json"
            market_path.write_text(json.dumps(market), encoding="utf-8")
            state["market_research_binding"] = {"path": str(market_path.relative_to(root)), "sha256": hashlib.sha256(market_path.read_bytes()).hexdigest()}
            reference_path = root / "research" / "reference_library.json"
            reference_path.write_text(json.dumps({"schema_version": SCHEMA_VERSION}), encoding="utf-8")
            state["reference_library_binding"] = {"path": str(reference_path.relative_to(root)), "sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest()}
            plan_path = root / "strategy" / "asset_plan.json"
            plan_path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "assets": [{"asset_id": f"{direction}-A", "direction_id": direction} for direction in ("D1", "D2", "D3")]}), encoding="utf-8")
            state["asset_plan_binding"] = {"path": str(plan_path.relative_to(root)), "sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest()}
            (root / "control" / "workflow_state.json").write_text(json.dumps(state), encoding="utf-8")

            result = run("authorize-production", "--project-root", root, "--expect-revision", "0", "--direction", "D1", "--direction", "D2", "--direction", "D3", "--asset", "D1-A", "--asset", "D2-A", "--asset", "D3-A", "--user-confirmation", "批准三个方向正式生产")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            produced = read_state(root)
            self.assertEqual(produced["active_direction_ids"], ["D1", "D2", "D3"])
            self.assertEqual({run["agent_name"] for run in produced["active_agent_runs"]}, {"production_d1_01", "production_d2_01", "production_d3_01"})


if __name__ == "__main__":
    unittest.main()
