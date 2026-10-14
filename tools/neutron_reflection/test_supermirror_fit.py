import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import importlib.util

import numpy as np
sys.path.insert(0, str(Path(__file__).parent / "interface"))
from supermirror_fit import fit_supermirror_from_dataset, phits_reflectivity


def dataset(q, y, error):
    return {k: types.SimpleNamespace(values=v) for k, v in zip(("xvalue", "histogram", "error"), (q, y, error))}


class ReferenceModelTests(unittest.TestCase):
    def setUp(self):
        self.q = np.arange(.022, .075, .0005)
        self.parameters = (.98, 2., .058, .0015)
        self.y = phits_reflectivity(self.q, *self.parameters)
        self.error = np.maximum(.005 * self.y, 1e-6)

    def test_model_low_q_and_center_match_reference_expression(self):
        r0, alpha, center, width = self.parameters
        self.assertEqual(float(phits_reflectivity(.01, *self.parameters)), r0)
        expected = .5 * r0 * (1 - alpha * (center - .0217))
        self.assertAlmostEqual(float(phits_reflectivity(center, *self.parameters)), expected)

    def test_known_parameters_recovered_with_explicit_window(self):
        result = fit_supermirror_from_dataset(dataset(self.q, self.y, self.error), fit_q_range=(.022, .07))
        for key, expected in zip(("R0", "alpha", "Qm", "W"), self.parameters):
            self.assertAlmostEqual(result[key], expected, places=6)
        self.assertAlmostEqual(result["r_envelope"], 2 * result["r_center"])
        self.assertEqual(result["r"], result["r_envelope"])

    def test_auto_window_also_recovers_clean_cutoff(self):
        result = fit_supermirror_from_dataset(dataset(self.q, self.y, self.error))
        self.assertAlmostEqual(result["Qm"], .058, places=6)

    def test_width_boundaries_are_cutoff_factor_not_absolute_reflectivity(self):
        result = fit_supermirror_from_dataset(dataset(self.q, self.y, self.error), fit_q_range=(.022, .07))
        for key, fraction in (("Q90", .9), ("Q10", .1), ("Q99", .99), ("Q01", .01)):
            q = result[key]
            envelope = result["R0"] * (1 - result["alpha"] * (q - .0217))
            self.assertAlmostEqual(float(phits_reflectivity(q, *self.parameters)) / envelope, fraction, places=6)
        self.assertAlmostEqual(result["width_90_10"], 2 * np.arctanh(.8) * result["W"])

    def test_late_high_error_outlier_does_not_shift_auto_fit(self):
        baseline = fit_supermirror_from_dataset(dataset(self.q, self.y, self.error))
        y, e = self.y.copy(), self.error.copy()
        i = np.argmin(abs(self.q - .072))
        y[i], e[i] = -29, 6000
        changed = fit_supermirror_from_dataset(dataset(self.q, y, e))
        self.assertAlmostEqual(changed["m"], baseline["m"], places=6)

    def test_incomplete_transition_and_missing_errors_rejected(self):
        with self.assertRaises(ValueError):
            fit_supermirror_from_dataset(dataset(self.q, self.y, self.error), fit_q_range=(.022, .054))
        with self.assertRaises(ValueError):
            fit_supermirror_from_dataset({"xvalue": types.SimpleNamespace(values=self.q),
                                         "histogram": types.SimpleNamespace(values=self.y)})

    def test_zero_error_does_not_produce_infinite_weights(self):
        result = fit_supermirror_from_dataset(dataset(self.q, self.y, np.zeros_like(self.y)), fit_q_range=(.022, .07))
        self.assertAlmostEqual(result["Qm"], .058, places=6)
        self.assertGreater(result["effective_error_floor"], 0)

    def test_normalization_above_one_is_flagged_not_clipped(self):
        y = phits_reflectivity(self.q, 1.08, 2., .058, .0015)
        result = fit_supermirror_from_dataset(dataset(self.q, y, self.error), fit_q_range=(.022, .07))
        self.assertAlmostEqual(result["R0"], 1.08, places=6)
        self.assertEqual(result["status"], "needs_review")

    def test_real_interface_default_routing_and_explicit_summary_columns(self):
        stub = types.ModuleType("CSNS_reflection_script_forinterface")
        stub.run_reflection = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not reduce raw data"))
        spec = importlib.util.spec_from_file_location("fit_interface_test", Path(__file__).parent / "interface/interface.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {stub.__name__: stub}), patch.dict(os.environ, {"NEUTRON_REFLECTION_MR_METHOD": "phits"}):
            spec.loader.exec_module(module)
            estimates = module.calculate_mr_results({"RUN0000001": dataset(self.q, self.y, self.error)})
            rows = module.build_summary([{"run": "RUN0000001", "type": "reflection", "Rz": 1.22},
                                         {"run": "RUN0000002", "type": "direct", "Rz": None}], estimates)
        self.assertEqual(rows[0]["mr_method"], "phits_3_37_tanh_fit_v1")
        self.assertAlmostEqual(rows[0]["r_envelope(%)"], 2 * rows[0]["R_center(%)"])
        self.assertEqual(set(rows[0]), set(rows[1]))
        self.assertIsNone(rows[1]["R_center(%)"])


@unittest.skipUnless(os.getenv("REFLECTION_REGRESSION_DATA"), "requires supplied .dat directory")
class ActualReferenceFitTests(unittest.TestCase):
    def test_four_supplied_profiles(self):
        root = Path(os.environ["REFLECTION_REGRESSION_DATA"])
        for n in (216, 217, 218, 963):
            with self.subTest(run=n):
                a = np.loadtxt(next(root.glob(f"RUN{n:07d}_*.dat")))
                result = fit_supermirror_from_dataset(dataset(a[:, 0], a[:, 1], a[:, 2]))
                self.assertEqual(result["method"], "phits_3_37_tanh_fit_v1")
                self.assertAlmostEqual(result["r_envelope"], 2 * result["r_center"])
                self.assertGreater(result["width_99_01"], result["width_90_10"])
                self.assertTrue(2 < result["m"] < 3)
                self.assertTrue(.7 < result["r_envelope"] < 1.1)
                # Numerical stability check on the analysis weighting convention.
                alternatives = [fit_supermirror_from_dataset(dataset(a[:, 0], a[:, 1], a[:, 2]),
                                error_floor_fraction=f)["m"] for f in (.005, .02)]
                self.assertLess(max(alternatives + [result["m"]]) - min(alternatives + [result["m"]]), .08)


if __name__ == "__main__":
    unittest.main()
