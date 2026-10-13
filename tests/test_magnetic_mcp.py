"""Contract tests for the magnetic structure MCP surface and its mission layer.

The scientific engine has its own tests (`magnetic_analysis/test_native.py`,
`test_engine.py`); these cover what the agent-facing tool layer must guarantee:
mission isolation, bank pairing, argument validation, and the shape of the
published tool schema.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.magnetic_structure_mcp import build_server

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools" / "GSAS-II-master") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools" / "GSAS-II-master"))


def _engine():
    """The mission layer; importing it must not drag the GSAS-II stack in."""
    from magnetic_analysis import mission

    return mission


class MissionPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {"WORKDIR": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_mission_lives_under_user_session(self):
        mission = _engine()
        path = mission.mission_dir("user", "session", "mission")
        self.assertEqual(path, (self.root / "user" / "session" / "mission").resolve())
        self.assertTrue(path.is_dir())

    def test_identifiers_and_traversal_are_rejected(self):
        mission = _engine()
        for user, session, name in (("../other", "session", "mission"),
                                    ("user", "..", "mission"),
                                    ("user", "session", "../mission"),
                                    ("user/session", "session", "mission"),
                                    ("", "session", "mission")):
            with self.subTest(user=user, session=session, name=name), \
                    self.assertRaises(ValueError):
                mission.mission_dir(user, session, name)

    def test_workdir_must_be_configured(self):
        mission = _engine()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WORKDIR", None)
            with self.assertRaises(ValueError):
                mission.mission_dir("user", "session", "mission")

    def test_standard_directories(self):
        mission = _engine()
        paths = mission.mission_paths("user", "session", "mission")
        for key in ("mission", "inputs", "analysis", "figures", "submission"):
            self.assertIn(key, paths)
        self.assertEqual(paths["inputs"].name, "input")
        self.assertEqual(paths["analysis"], paths["mission"] / "analysis")


class InputStagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {"WORKDIR": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.source = self.root / "source"
        (self.source / "high").mkdir(parents=True)
        (self.source / "low").mkdir(parents=True)
        (self.source / "inst").mkdir(parents=True)
        (self.source / "initial.cif").write_text("data_x\n_cell_length_a 4.0\n", encoding="utf-8")
        for bank, token in (("a", "BS"), ("b", "HA"), ("c", "SE")):
            (self.source / "high" / f"RUN0001_group{token}.dat").write_text(
                f"1 2 0.1\n2 3 0.1\n", encoding="utf-8")
            (self.source / "low" / f"RUN0002_group{token}.dat").write_text(
                f"1 4 0.1\n2 5 0.1\n", encoding="utf-8")
            (self.source / "inst" / f"{token}.instprm").write_text("Type:PNT\n", encoding="utf-8")

    def _stage(self):
        mission = _engine()
        paths = mission.mission_paths("user", "session", "mission")
        return mission, mission.stage_inputs(
            paths, self.source / "initial.cif", self.source / "high",
            self.source / "low", self.source / "inst")

    def test_banks_are_paired_across_all_three_inputs(self):
        mission, staged = self._stage()
        self.assertEqual(staged["banks"], ["bs", "ha", "se"])
        for token in staged["banks"]:
            for kind in ("high", "low", "instruments"):
                self.assertTrue(Path(staged[kind][token]).is_file(), kind)
        self.assertEqual(staged["unpaired"], {"high": [], "low": [], "instruments": []})

    def test_an_extra_high_bank_is_reported_not_used(self):
        (self.source / "high" / "RUN0001_groupXX.dat").write_text("1 2 0.1\n", encoding="utf-8")
        _, staged = self._stage()
        self.assertNotIn("xx", staged["banks"])
        self.assertIn("xx", staged["unpaired"]["high"])

    def test_instrument_year_or_version_suffix_still_pairs(self):
        """Real instrument files carry an acquisition year the spectra do not.

        ``BS_2025.instprm`` alongside ``RUN0002218_groupBS.dat`` is what the group's
        beamline writes; before the suffix handling this failed as "no bank token
        appears in all three inputs" and the agent had nothing to pair.
        """
        (self.source / "inst" / "BS.instprm").unlink()
        (self.source / "inst" / "BS_2025.instprm").write_text("Type:PNT\n", encoding="utf-8")
        (self.source / "inst" / "HA.instprm").unlink()
        (self.source / "inst" / "HA_v2.instprm").write_text("Type:PNT\n", encoding="utf-8")
        _, staged = self._stage()
        self.assertEqual(staged["banks"], ["bs", "ha", "se"])
        self.assertTrue(Path(staged["instruments"]["bs"]).is_file())

    def test_numeric_bank_names_are_not_collapsed(self):
        """bank1/bank2 share a leading word: collapsing them would merge two banks."""
        mission = _engine()
        root = self.source / "numeric"
        for kind, suffix in (("high", ".dat"), ("low", ".dat"), ("inst", ".instprm")):
            (root / kind).mkdir(parents=True, exist_ok=True)
            for token in ("bank1", "bank2"):
                (root / kind / f"{token}{suffix}").write_text("x\n", encoding="utf-8")
        paths = mission.mission_paths("user", "session", "numeric")
        staged = mission.stage_inputs(paths, self.source / "initial.cif", root / "high",
                                      root / "low", root / "inst")
        self.assertEqual(staged["banks"], ["bank1", "bank2"])

    def test_duplicate_bank_token_is_rejected(self):
        (self.source / "low" / "BS.dat").write_text("1 2 0.1\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self._stage()

    def test_missing_inputs_are_rejected(self):
        mission = _engine()
        paths = mission.mission_paths("user", "session", "mission")
        with self.assertRaises(ValueError):
            mission.stage_inputs(paths, self.source / "nope.cif", self.source / "high",
                                 self.source / "low", self.source / "inst")

    def test_magnetic_elements_are_inferred_from_chemistry_only(self):
        mission = _engine()
        cif = self.source / "chemistry.cif"
        cif.write_text("loop_\n_atom_site_type_symbol\nMn\nTe\nO\n", encoding="utf-8")
        self.assertEqual(mission.magnetic_elements_from_cif(cif), ["Mn", "O"])
        self.assertEqual(mission.magnetic_elements_from_cif(cif, ["Fe"]), ["Fe"])


class ShortlistTests(unittest.TestCase):
    """The screen→shortlist rule decides what the full pass even looks at."""

    def _missions(self):
        return _engine()

    def test_shortlist_takes_best_then_near_degenerate_groups(self):
        mission = self._missions()
        report = {"models": [
            {"id": "a1", "bns_number": "194.270", "status": "ok",
             "quality": {"bic_known_variance": 10.0}},
            {"id": "a2", "bns_number": "194.268", "status": "ok",
             "quality": {"bic_known_variance": 20.0}},
            {"id": "b1", "bns_number": "63.457", "status": "ok",
             "quality": {"bic_known_variance": 30.0}},
            {"id": "c1", "bns_number": "12.62", "status": "ok",
             "quality": {"bic_known_variance": 5.0}},
            {"id": "bad", "bns_number": "9.9", "status": "failed"},
        ]}
        ids = mission._shortlist_ids(report, 2)
        # Best two by screen BIC; nothing else is within the near-degeneracy window.
        self.assertEqual(ids, ["c1", "a1"])
        self.assertNotIn("bad", ids)

    def test_near_degenerate_new_group_is_added_and_deduplicated_exactly(self):
        mission = self._missions()
        report = {"models": [
            {"id": "best", "bns_number": "194.270", "status": "ok",
             "quality": {"bic_known_variance": 100.0}},
            {"id": "dup", "bns_number": "194.270", "status": "ok",
             "quality": {"bic_known_variance": 101.0}},
            {"id": "rival", "bns_number": "63.462", "status": "ok",
             "quality": {"bic_known_variance": 103.0}},
            {"id": "rival-sibling", "bns_number": "63.457", "status": "ok",
             "quality": {"bic_known_variance": 104.0}},
            {"id": "far", "bns_number": "12.62", "status": "ok",
             "quality": {"bic_known_variance": 400.0}},
        ]}
        ids = mission._shortlist_ids(report, 2)
        self.assertEqual(ids[:2], ["best", "dup"])
        # A different magnetic group inside the window joins; the far one does not.
        self.assertIn("rival", ids)
        self.assertIn("rival-sibling", ids)
        self.assertNotIn("far", ids)

    def test_failed_only_screen_is_an_error(self):
        mission = self._missions()
        with self.assertRaises(ValueError):
            mission._shortlist_ids({"models": [{"id": "x", "status": "failed"}]}, 4)


class ServerSurfaceTests(unittest.IsolatedAsyncioTestCase):
    """The published tool set and its parameters are the agent-facing contract."""

    async def test_tools_and_house_parameters(self):
        server = build_server()
        tools = {tool.name: tool for tool in await server.list_tools()}
        self.assertEqual(sorted(tools), [
            "EnumerateMagneticCandidates", "ExportMagneticStructure",
            "InitializeMagneticMission", "RefineBestMagneticModel",
            "RefineMagneticCandidates", "SearchPropagationVector",
        ])
        for name, tool in tools.items():
            properties = tool.inputSchema["properties"]
            for key in ("user_id", "session_id", "mission_name", "task_id"):
                self.assertIn(key, properties, f"{name} lacks {key}")
        initialize = tools["InitializeMagneticMission"].inputSchema["properties"]
        for key in ("initial_cif", "high_temperature", "low_temperature", "instruments"):
            self.assertIn(key, initialize)
        refine = tools["RefineMagneticCandidates"].inputSchema["properties"]
        for key in ("shortlist", "screen_cycles", "final_cycles", "native_solver"):
            self.assertIn(key, refine)
        deepen = tools["RefineBestMagneticModel"].inputSchema["properties"]
        for key in ("model", "deep_cycles", "deep_rounds", "time_limit_seconds", "native_solver"):
            self.assertIn(key, deepen)
        candidates = tools["EnumerateMagneticCandidates"].inputSchema["properties"]
        self.assertIn("candidate_scope", candidates)

    async def test_tool_descriptions_explain_the_subject(self):
        server = build_server()
        tools = {tool.name: tool for tool in await server.list_tools()}
        description = tools["RefineMagneticCandidates"].description
        self.assertIn("磁", description)
        self.assertIn("磁矩约束", description)


class ProgressChannelTests(unittest.TestCase):
    """A long stage must publish progress the polling runner can relay."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {"WORKDIR": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_progress_path_follows_the_mission_layout(self):
        from tools.magnetic_structure_mcp import _progress_log_path

        resolved = _progress_log_path({"user_id": "u1", "session_id": "s1",
                                       "mission_name": "m1"})
        self.assertEqual(resolved, self.root / "u1" / "s1" / "m1" / "analysis" / "progress.log")

    def test_missing_or_unsafe_identifiers_yield_no_path(self):
        from tools.magnetic_structure_mcp import _progress_log_path

        for kwargs in ({}, {"user_id": "u1", "session_id": "s1"},
                       {"user_id": "../etc", "session_id": "s1", "mission_name": "m1"},
                       {"user_id": "u1", "session_id": "s/1", "mission_name": "m1"}):
            self.assertIsNone(_progress_log_path(kwargs))

    def test_runner_attaches_recent_progress_lines(self):
        from src.core.tools import ProcessPollingMcpToolBase

        def target(user_id, session_id, mission_name, task_id):
            return {"status": "completed"}

        log = self.root / "u1" / "s1" / "m1" / "analysis" / "progress.log"
        log.parent.mkdir(parents=True)
        log.write_text("\n".join(f"step {i}" for i in range(1, 12)) + "\n", encoding="utf-8")
        runner = ProcessPollingMcpToolBase(
            target_func=target, timeout_sec=1, poll_interval=0.1,
            progress_file=lambda kw: self.root / kw["user_id"] / kw["session_id"] /
            kw["mission_name"] / "analysis" / "progress.log")
        key = ("m1", "t1")
        runner.id_to_kwargs[key] = {"user_id": "u1", "session_id": "s1", "mission_name": "m1"}
        progress = runner._read_progress(key)
        self.assertEqual(progress["lines"][-1], "step 11")
        self.assertEqual(len(progress["lines"]), 6)
        self.assertIn("age_seconds", progress)


class FailureTriageTests(unittest.TestCase):
    """Failed candidates are triaged by cause, never lumped together."""

    def _classify(self):
        return _engine(), __import__("magnetic_analysis.native", fromlist=["classify_failure"])

    def test_categories_separate_budget_bounds_solver_and_geometry(self):
        mission, native = self._classify()
        cases = {
            "time_budget": {"error": "Native refinement wall-clock budget reached"},
            "parameter_bounds": {"error": "Atom Uiso outside allowed range"},
            "solver": {"error_type": "RuntimeError",
                       "error": "GSAS-II did not produce a fresh refinement result (solver failure)"},
            "geometry": {"error": "Selected magnetic atoms could not be mapped to the CIF orbit"},
            "other": {"error": "unexpected"},
        }
        for expected, model in cases.items():
            self.assertEqual(native.classify_failure(model), expected, model)

    def test_deepen_requires_a_comparison_first(self):
        mission = _engine()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with patch.dict(os.environ, {"WORKDIR": temporary.name}):
            result = mission.deepen("u", "s", "missing-mission", "t1")
        # Mission stages report failure instead of raising, so the agent always gets a
        # structured answer it can explain to the user.
        self.assertEqual(result["status"], "failure")
        self.assertIn("refinement stage first", result["error"])


class HandoffTests(unittest.TestCase):
    """Deepening is handed to the gsas2-refine service, not done by this engine.

    Measured reasons: the engine's own ladder released every parameter group at once, which
    drove the solver into nonfinite intensities, and its first round was a no-op (identical
    reduced chi-squared). The gsas2-refine tool refines one group at a time with rollback,
    so the mission hands it a stable project instead.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {"WORKDIR": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _comparison(self, mission):
        paths = mission.mission_paths("u", "s", "m")
        analysis = paths["analysis"]
        analysis.mkdir(parents=True, exist_ok=True)
        mcif = analysis / "subA.mcif"
        mcif.write_text("comparison-structure", encoding="utf-8")
        gpx = analysis / "native.gpx"
        gpx.write_text("project", encoding="utf-8")
        comparison = {"best_model": "subA", "scope": {"stage_label": "final"},
                      "mcif_path": str(mcif),
                      "models": [{"id": "subA", "status": "ok", "gpx_path": str(gpx),
                                  "quality": {"reduced_chisq": 40.0, "bic_known_variance": 1.0},
                                  "mcif_path": str(mcif)}]}
        (analysis / "refinement_report.json").write_text(json.dumps(comparison), encoding="utf-8")
        return paths, analysis, comparison

    def test_deepen_hands_off_the_selected_project(self):
        mission = _engine()
        paths, analysis, comparison = self._comparison(mission)

        result = mission.deepen("u", "s", "m", "t")

        self.assertEqual(result["status"], "completed")
        self.assertTrue(Path(result["handoff_gpx"]).is_file())
        note = Path(result["handoff_note"]).read_text(encoding="utf-8")
        self.assertIn("refine_moments", note)
        self.assertIn("ExportMagneticStructure", note)
        # The record of how the model was selected is not disturbed by the handoff.
        self.assertEqual(json.loads((analysis / "refinement_report.json").read_text()),
                         comparison)

    def test_deepen_needs_a_saved_project_to_hand_off(self):
        mission = _engine()
        paths, analysis, comparison = self._comparison(mission)
        comparison["models"][0].pop("gpx_path")
        (analysis / "refinement_report.json").write_text(json.dumps(comparison), encoding="utf-8")

        result = mission.deepen("u", "s", "m", "t")

        self.assertEqual(result["status"], "failure")
        self.assertIn("no saved GSAS-II project", result["error"])

    def test_export_uses_a_handed_off_project_when_given(self):
        mission = _engine()
        paths, analysis, _ = self._comparison(mission)
        refined = analysis / "handoff_refined.mcif"
        refined.write_text("refined-structure", encoding="utf-8")

        with patch("magnetic_analysis.native.export_project_mcif",
                   lambda gpx, target: {"mcif_path": str(refined),
                                        "magnetic_phase": "subA"}):
            result = mission.export("u", "s", "m", "t",
                                    gpx_path=str(analysis / "selected_model.gpx"))

        self.assertEqual(result["status"], "completed")
        self.assertEqual((paths["session_submission"] / "final.mcif").read_text(),
                         "refined-structure")

    def test_export_delivers_the_comparison_structure_by_default(self):
        mission = _engine()
        paths, analysis, _ = self._comparison(mission)

        result = mission.export("u", "s", "m", "t")

        self.assertEqual(result["status"], "completed")
        self.assertEqual((paths["session_submission"] / "final.mcif").read_text(),
                         "comparison-structure")


if __name__ == "__main__":
    unittest.main()
