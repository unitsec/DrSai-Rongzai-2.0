"""Run: python -m unittest discover -s tools/neutron_reflection -p 'test_*.py' -v"""
import json
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / "interface"))
from batch_reflection import prepare_groups
from reflection_config import load_reflection_config


class DataFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = {"data_root": str(self.root), "data_sources": {"legacy_home": str(self.root)}}

    def write_run(self, n, angle=None):
        run = f"RUN{n:07d}"
        folder = self.root / run
        folder.mkdir(exist_ok=True)
        data = {} if angle is None else {"summary": {"json": {"devices": {"Rz": angle}}}}
        (folder / run).write_text(json.dumps(data))
        return run


class ConfigCompatibilityTests(DataFixture):
    def make_config(self, paths):
        base = self.root / "base.json"
        specific = self.root / "reflection.json"
        base.write_text(json.dumps({"nested": {"keep": 1, "replace": 1}}))
        specific.write_text(json.dumps({"data_path": "{data_root}", "param_path": "{param_root}/BL08A",
                                        "nested": {"replace": 2}, "runs": ["RUN0000216"]}))
        (self.root / "machine_paths.json").write_text(json.dumps(paths))
        return load_reflection_config(base, specific)

    def test_source_dictionary_does_not_break_template_resolution(self):
        config = self.make_config({"data_root": "/data", "param_root": "/params",
                                   "data_sources": {"legacy_home": "/home/user"}})
        self.assertEqual(config["data_path"], "/data")
        self.assertEqual(config["param_path"], "/params/BL08A")
        self.assertEqual(config["nested"], {"keep": 1, "replace": 2})
        self.assertEqual(config["runs"], ["RUN0000216"])

    def test_original_scalar_only_configuration_unchanged(self):
        config = self.make_config({"data_root": "/original", "param_root": "/params"})
        self.assertEqual(config["data_path"], "/original")

    def test_dictionary_cannot_itself_be_used_as_a_path(self):
        with self.assertRaisesRegex(ValueError, "data_root"):
            self.make_config({"data_root": {"invalid": "/data"}, "param_root": "/params"})


class PreflightTests(DataFixture):
    def test_original_pair_auto_classified(self):
        reflection, direct = self.write_run(963, .770115), self.write_run(964, 0)
        plans, questions = prepare_groups([{"runs": [reflection, direct]}], self.paths)
        self.assertEqual(questions, [])
        self.assertEqual(plans[0]["direct_run"], direct)

    def test_missing_devices_requests_identity_and_angles(self):
        runs = [self.write_run(n) for n in (216, 219)]
        plans, questions = prepare_groups([{"runs": runs}], self.paths)
        self.assertIsNone(plans[0]["direct_run"])
        self.assertTrue(any(x["field"] == "direct_run" for x in questions[0]["missing_or_conflicting"]))
        self.assertFalse(any(x["Rz"] == 0 for x in plans[0]["classified"]))

    def test_user_supplied_legacy_angles(self):
        runs = [self.write_run(n) for n in (216, 217, 218, 219)]
        plans, questions = prepare_groups([{"runs": runs, "data_source": "legacy_home",
            "direct_run": runs[-1], "angles_deg": dict.fromkeys(runs[:-1], 1.22)}], self.paths)
        self.assertEqual(questions, [])
        self.assertEqual([x["Rz"] for x in plans[0]["classified"]], [1.22, 1.22, 1.22, None])
        self.assertEqual(plans[0]["classified"][0]["angle_source"], "user")

    def test_missing_one_angle_is_not_silently_filled(self):
        runs = [self.write_run(n) for n in (216, 217, 219)]
        _, q = prepare_groups([{"runs": runs, "direct_run": runs[-1], "angles_deg": {runs[0]: 1.22}}], self.paths)
        self.assertEqual(q[0]["missing_or_conflicting"][0]["run"], runs[1])

    def test_conflict_requires_explicit_confirmation(self):
        r, d = self.write_run(216, 1), self.write_run(219, 0)
        group = {"runs": [r, d], "direct_run": d, "angles_deg": {r: 1.22}}
        self.assertTrue(prepare_groups([group], self.paths)[1])
        group["confirm_metadata_override"] = True
        self.assertFalse(prepare_groups([group], self.paths)[1])

    def test_duplicate_and_path_input_rejected(self):
        r = self.write_run(216)
        for group in ({"runs": [r, r]}, {"runs": [r, "../secret"]},
                      {"runs": [r, "RUN0000219"], "data_source": "/home/user"}):
            with self.subTest(group=group), self.assertRaises(ValueError):
                prepare_groups([group], self.paths)

    def test_nonfinite_and_boolean_angles_rejected(self):
        r, d = self.write_run(216), self.write_run(219)
        for angle in (True, float("nan"), float("inf"), 90, "1.22"):
            with self.subTest(angle=angle), self.assertRaises(ValueError):
                prepare_groups([{"runs": [r, d], "direct_run": d, "angles_deg": {r: angle}}], self.paths)

    def test_same_reflection_can_be_compared_with_different_directs(self):
        r, d1, d2 = self.write_run(216, 1.22), self.write_run(219, 0), self.write_run(220, 0)
        plans, q = prepare_groups([{"runs": [r, d1]}, {"runs": [r, d2]}], self.paths)
        self.assertFalse(q)
        self.assertNotEqual(plans[0]["group_id"], plans[1]["group_id"])
        self.assertEqual([p["direct_run"] for p in plans], [d1, d2])

    def test_missing_file_is_not_treated_as_missing_angle(self):
        r = self.write_run(216)
        with self.assertRaises(FileNotFoundError):
            prepare_groups([{"runs": [r, "RUN0000219"]}], self.paths)


class PublicSummaryTests(DataFixture):
    def test_only_two_columns_and_envelope_not_center(self):
        import csv
        from summary_export import write_public_summary
        path = self.root / "summary.csv"
        write_public_summary(path, [
            {"Run": "RUN0000963", "Type": "reflection", "m": 2.1, "r_envelope(%)": 84., "R_center(%)": 42., "R0": .98},
            {"Run": "RUN0000964", "Type": "direct"},
            {"Run": "RUN0000216", "Type": "reflection", "m": 2.6, "r_envelope(%)": 90.},
        ])
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        self.assertEqual(reader.fieldnames, ["m", "r_envelope(%)"])
        self.assertEqual([float(row["r_envelope(%)"]) for row in rows], [84., 90.])
        self.assertEqual([row["run"] for row in json.loads((self.root / "summary_row_order.json").read_text())],
                         ["RUN0000963", "RUN0000216"])

    def test_invalid_estimate_is_not_exported(self):
        from summary_export import write_public_summary
        for value in (-1., float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                write_public_summary(self.root / "summary.csv", [{"Type": "reflection", "m": 2., "r_envelope(%)": value}])


@unittest.skipUnless(importlib.util.find_spec("numpy") and importlib.util.find_spec("matplotlib"),
                     "需要numpy和matplotlib；可在服务器反射venv中运行")
class CombinedOutputTests(DataFixture):
    def test_two_groups_have_separate_directs_and_aggregate_outputs(self):
        import csv
        import types
        import numpy as np
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.axes import Axes
        from batch_reflection import execute_groups
        r, d1, d2 = self.write_run(216, 1.22), self.write_run(219, 0), self.write_run(220, 0)
        plans, _ = prepare_groups([{"runs": [r, d1]}, {"runs": [r, d2]}], self.paths)
        self.paths["param_root"] = str(self.root)
        calls = []

        def reduce(mods, output_dir):
            calls.append(mods)
            return {r: {"xvalue": types.SimpleNamespace(values=np.array([.01, .02, .03])),
                        "histogram": types.SimpleNamespace(values=np.array([1., .5, .1])),
                        "error": types.SimpleNamespace(values=np.array([.02, .02, .01]))}}

        interface = types.ModuleType("interface")
        interface.build_modifications = lambda rows: {"direct_run": [x["run"] for x in rows if x["type"] == "direct"]}
        interface.calculate_mr_results = lambda ds: {}
        interface.plot_results = lambda *args: None
        interface.build_summary = lambda rows, mr: [{"Run": x["run"], "Type": x["type"], "Rz": x["Rz"], "m": 2.5, "r(%)": 90} for x in rows]
        algorithm = types.ModuleType("CSNS_reflection_script_forinterface")
        algorithm.run_reflection = reduce
        with patch.dict(sys.modules, {"interface": interface, "CSNS_reflection_script_forinterface": algorithm}), \
                patch.object(Axes, "errorbar", autospec=True, side_effect=Axes.errorbar) as errorbars:
            result = execute_groups(plans, self.paths, self.root)
        self.assertEqual(errorbars.call_count, 2)
        for call in errorbars.call_args_list:
            self.assertEqual(call.kwargs["linestyle"], "none")
            np.testing.assert_array_equal(call.kwargs["yerr"], [.02, .02, .01])
        self.assertEqual([x["direct_run"] for x in calls], [[d1], [d2]])
        self.assertGreater(Path(result["combined_plot"]).stat().st_size, 1000)
        with Path(result["summary_file"]).open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(reader.fieldnames, ["m", "r_envelope(%)"])
        self.assertEqual(len(rows), 2)
        order = json.loads((self.root / "summary_row_order.json").read_text())
        self.assertEqual([row["run"] for row in order], [r, r])
        self.assertTrue((self.root / "resolved_groups.json").is_file())


if __name__ == "__main__":
    unittest.main()
