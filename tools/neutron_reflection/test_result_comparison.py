import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parent / "interface"))
from result_comparison import compare_measurements


def row(run, m, r, status="estimated", group="g1"):
    return {"Run": run, "Type": "reflection", "m": m, "r_envelope(%)": r,
            "mr_status": status, "group_id": group}


class ComparisonTests(unittest.TestCase):
    def test_relative_percentage_is_not_percentage_points(self):
        result = compare_measurements([row("A", 2., 90), row("B", 2.1, 80)])
        self.assertAlmostEqual(result["comparisons"][0]["relative_improvement_percent"], 12.5)
        self.assertIn("高12.50%", result["public"]["conclusion"])

    def test_exact_ten_percent_included_but_above_excluded(self):
        self.assertEqual(len(compare_measurements([row("A", 2, 80), row("B", 2.2, 90)])["comparisons"]), 1)
        self.assertFalse(compare_measurements([row("A", 2, 80), row("B", 2.20001, 90)])["comparisons"])

    def test_neighbour_chain_does_not_widen_group(self):
        result = compare_measurements([row("A", 2, 80), row("B", 2.18, 90), row("C", 2.36, 99)])
        self.assertEqual([item["label"] for item in result["comparisons"][0]["ranking"]], ["B", "A"])

    def test_review_results_cannot_win(self):
        result = compare_measurements([row("A", 2, 99, "needs_review"), row("B", 2.05, 80), row("C", 2.1, 70)])
        self.assertEqual(result["comparisons"][0]["best"], ["B"])
        self.assertEqual(result["excluded"][0]["label"], "A")
        self.assertNotIn("警告", result["public"]["conclusion"])

    def test_all_review_results_do_not_produce_ranking(self):
        result = compare_measurements([row("A", 2, 99, "needs_review"), row("B", 2, 90, "needs_review")])
        self.assertFalse(result["comparisons"])
        self.assertIn("暂不排名", result["public"]["conclusion"])

    def test_tied_results_are_not_given_arbitrary_winner(self):
        result = compare_measurements([row("B", 2, 90), row("A", 2, 90)])
        self.assertEqual(result["comparisons"][0]["best"], ["A", "B"])
        self.assertEqual(result["comparisons"][0]["relative_improvement_percent"], 0)
        self.assertIn("相同", result["public"]["conclusion"])

    def test_invalid_and_direct_rows_excluded(self):
        result = compare_measurements([row("A", 2, 0), row("B", float("nan"), 90),
                                       {"Run": "direct", "Type": "direct"}])
        self.assertFalse(result["comparisons"])
        self.assertEqual(len(result["excluded"]), 2)

    def test_repeated_run_labels_keep_group_identity(self):
        result = compare_measurements([row("RUN1", 2, 90, group="g1"), row("RUN1", 2, 80, group="g2")])
        self.assertEqual(result["comparisons"][0]["best"], ["g1/RUN1"])

    def test_table_preserves_input_order(self):
        result = compare_measurements([row("B", 2.1, 80), row("A", 2, 90)])
        table = result["public"]["table_markdown"]
        self.assertLess(table.index("| B |"), table.index("| A |"))


if __name__ == "__main__":
    unittest.main()
