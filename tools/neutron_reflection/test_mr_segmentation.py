import os
from pathlib import Path
import sys
import types
import unittest

sys.path.insert(0, str(Path(__file__).parent / "interface"))
import numpy as np
from calculatemr import calculate_mr_from_dataset


def dataset(x, y, error):
    return {k: types.SimpleNamespace(values=v) for k, v in zip(("xvalue", "histogram", "error"), (x, y, error))}


class SegmentationTests(unittest.TestCase):
    def setUp(self):
        self.x = np.arange(.02, .091, .00098)
        baseline = .98 - 2 * (self.x - .022)
        self.y = baseline / (1 + np.exp((self.x - .058) / .0012))
        self.error = np.maximum(.005 * self.y, 1e-9)

    def calculate(self, y=None, error=None):
        return calculate_mr_from_dataset(dataset(self.x, self.y if y is None else y,
                                                self.error if error is None else error))

    def test_clean_edge_midpoint_and_positive_platform(self):
        result = self.calculate()
        self.assertLess(abs(result["Qm"] - .058), .002)
        self.assertGreater(result["r"], .85)
        self.assertLess(result["r"], 1.05)
        self.assertLess(result["platform_q_end"], result["Qm"])

    def test_late_extreme_spike_does_not_move_main_edge(self):
        clean = self.calculate()
        y, e = self.y.copy(), self.error.copy()
        index = np.argmin(abs(self.x - .071))
        y[index], e[index] = -29., 6000.
        result = self.calculate(y, e)
        self.assertAlmostEqual(result["Qm"], clean["Qm"])
        self.assertAlmostEqual(result["r"], clean["r"])

    def test_one_point_dip_is_not_a_sustained_edge(self):
        y = self.y.copy()
        y[14] *= .1
        result = self.calculate(y)
        self.assertLess(abs(result["Qm"] - .058), .002)
        self.assertGreater(result["r"], .85)

    def test_flat_curve_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "下降"):
            self.calculate(np.ones_like(self.y))

    def test_uncertain_main_edge_is_rejected(self):
        e = self.error.copy()
        e[(self.x > .056) & (self.x < .06)] = 100
        with self.assertRaises(ValueError):
            self.calculate(error=e)

    def test_sorting_preserves_results_and_duplicate_q_is_rejected(self):
        result = self.calculate()
        reverse = calculate_mr_from_dataset(dataset(self.x[::-1], self.y[::-1], self.error[::-1]))
        self.assertAlmostEqual(result["Qm"], reverse["Qm"])
        x = self.x.copy()
        x[9] = x[8]
        with self.assertRaises(ValueError):
            calculate_mr_from_dataset(dataset(x, self.y, self.error))

    def test_zero_padded_tail_is_not_a_fake_second_edge(self):
        y, e = self.y.copy(), self.error.copy()
        y[self.x > .072] = 0
        e[self.x > .072] = 0
        self.assertAlmostEqual(self.calculate(y, e)["Qm"], self.calculate()["Qm"])

    def test_amplitude_scaling_preserves_m(self):
        original = self.calculate()
        scaled = self.calculate(self.y * .5, self.error * .5)
        self.assertAlmostEqual(original["m"], scaled["m"])
        self.assertAlmostEqual(original["r"] * .5, scaled["r"], places=5)


@unittest.skipUnless(os.getenv("REFLECTION_REGRESSION_DATA"), "set REFLECTION_REGRESSION_DATA to the four supplied .dat files")
class ActualCurveTests(unittest.TestCase):
    def test_supplied_curves_and_smoothing_sensitivity(self):
        root = Path(os.environ["REFLECTION_REGRESSION_DATA"])
        for n in (216, 217, 218, 963):
            a = np.loadtxt(next(root.glob(f"RUN{n:07d}_*.dat")))
            ds = dataset(a[:, 0], a[:, 1], a[:, 2])
            with self.subTest(run=n):
                result = calculate_mr_from_dataset(ds)
                bound = (.043, .05) if n == 963 else (.054, .063)
                self.assertTrue(bound[0] < result["Qm"] < bound[1])
                self.assertTrue(.7 < result["r"] < 1.1)
                self.assertTrue(result["platform_q_end"] < result["Qm"])
                qm = [calculate_mr_from_dataset(ds, smoothing_window=w)["Qm"] for w in (5, 7, 9)]
                self.assertLessEqual(max(qm) - min(qm), .00101)


if __name__ == "__main__":
    unittest.main()
