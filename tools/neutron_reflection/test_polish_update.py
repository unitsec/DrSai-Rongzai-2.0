"""Verify direct-beam Rz threshold boundaries."""
import ast
from pathlib import Path
import unittest
from test_batch_reflection import DataFixture
from batch_reflection import prepare_groups, THRESHOLD


class ThresholdTests(DataFixture):
    def test_positive_negative_near_zero_and_boundary(self):
        self.assertEqual(THRESHOLD, .001)
        for rz in (-.0001037, .0001037, -.000999, .000999, 0):
            with self.subTest(rz=rz):
                r, d = self.write_run(1, 1.2), self.write_run(2, rz)
                plans, questions = prepare_groups([{"runs": [r, d]}], self.paths)
                self.assertFalse(questions)
                self.assertEqual(plans[0]["direct_run"], d)
        for rz in (-.001, .001, -.001001, .001001):
            r, d = self.write_run(1, 1.2), self.write_run(2, rz)
            self.assertTrue(prepare_groups([{"runs": [r, d]}], self.paths)[1])

    def test_legacy_interface_classification_matches(self):
        tree = ast.parse((Path(__file__).parent / "interface/interface.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "classify_runs")
        ns = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "interface.py", "exec"), ns)
        result = ns["classify_runs"]([{"run": str(i), "Rz": v} for i, v in enumerate([-.0001037, .0009, -.001, .001])])
        self.assertEqual([r["type"] for r in result], ["direct", "direct", "reflection", "reflection"])




if __name__ == "__main__":
    unittest.main()
