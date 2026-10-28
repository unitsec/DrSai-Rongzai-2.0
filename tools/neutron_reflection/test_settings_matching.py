import json
import unittest
from test_batch_reflection import DataFixture
from batch_reflection import prepare_groups
from settings_matching import differences, FIELDS
from result_comparison import compare_measurements


class MatchingTests(DataFixture):
    def change(self, run, key, value):
        p = self.root / run / run
        doc = json.loads(p.read_text())
        info = doc["summary"]["json"]
        container = info if key.startswith("lamda_") else info["devices"]
        if value is None:
            container.pop(key, None)
        else:
            container[key] = value
        p.write_text(json.dumps(doc))

    def test_unique_and_multiple_settings_partition_without_manual_groups(self):
        r1, d1, r2, d2 = [self.write_run(n, a) for n, a in ((1, 1), (2, 0), (3, 2), (4, 0))]
        for r in (r2, d2):
            self.change(r, "S1H", 2.)
        plans, q = prepare_groups([{"runs": [r1, d1, r2, d2]}], self.paths)
        self.assertFalse(q)
        self.assertEqual([p["direct_run"] for p in plans], [d1, d2])
        self.assertEqual([p["classified"][0]["run"] for p in plans], [r1, r2])

    def test_multiple_candidates_then_user_selects(self):
        r, a, b = [self.write_run(n, v) for n, v in ((1, 1), (2, 0), (3, 0))]
        g = {"runs": [r, a, b]}
        plans, q = prepare_groups([g], self.paths)
        self.assertEqual(plans, [])
        self.assertEqual(q[0]["missing_or_conflicting"][0]["candidates"], [a, b])
        g["direct_assignments"] = {r: b}
        plans, q = prepare_groups([g], self.paths)
        self.assertFalse(q)
        self.assertEqual(plans[0]["direct_run"], b)
        g.pop("direct_assignments")
        g["direct_run"] = a
        self.assertEqual(prepare_groups([g], self.paths)[0][0]["direct_run"], a)

    def test_each_field_mismatch_cannot_be_overridden(self):
        for key in FIELDS:
            with self.subTest(key=key):
                r, d = self.write_run(1, 1), self.write_run(2, 0)
                self.change(r, key, 5.)
                plans, q = prepare_groups([{"runs": [r, d], "direct_run": d,
                                             "confirm_metadata_override": True}], self.paths)
                self.assertFalse(plans)
                self.assertTrue(q)

    def test_missing_invalid_settings_never_assumed_equal(self):
        for bad in (None, 0, -1, True, "3", float("nan"), float("inf")):
            with self.subTest(bad=bad):
                r, d = self.write_run(1, 1), self.write_run(2, 0)
                self.change(r, "S1H", bad)
                self.assertTrue(prepare_groups([{"runs": [r, d]}], self.paths)[1])

    def test_missing_rz_and_partial_supplement(self):
        r, d = self.write_run(1), self.write_run(2)
        g = {"runs": [r, d]}
        self.assertTrue(prepare_groups([g], self.paths)[1])
        g.update({"roles": {r: "reflection", d: "direct"}})
        self.assertTrue(prepare_groups([g], self.paths)[1])
        g["angles_deg"] = {r: 1.2}
        plans, q = prepare_groups([g], self.paths)
        self.assertFalse(q)
        direct = next(x for x in plans[0]["classified"] if x["type"] == "direct")
        self.assertIsNone(direct["Rz"])

    def test_no_chain_and_exact_boundary_and_symmetry(self):
        a = dict.fromkeys(FIELDS, 1.)
        b = dict(a, S1H=1.01)
        c = dict(a, S1H=1.0201)
        self.assertFalse(differences(a, b))
        self.assertFalse(differences(b, a))
        self.assertFalse(differences(b, c))
        self.assertTrue(differences(a, c))
        r1, r2, d = self.write_run(1, 1), self.write_run(2, 1), self.write_run(3, 0)
        self.change(d, "S1H", 1.)
        self.change(r1, "S1H", 1.01)
        self.change(r2, "S1H", 1.0201)
        self.assertFalse(prepare_groups([{"runs": [r1, r2, d]}], self.paths)[0])

    def test_one_pending_item_blocks_whole_batch(self):
        r, d, unknown = self.write_run(1, 1), self.write_run(2, 0), self.write_run(3)
        self.assertFalse(prepare_groups([{"runs": [r, d]}, {"runs": [unknown, d]}], self.paths)[0])

    def test_target_m_propagates_and_invalid_rejected(self):
        r, d = self.write_run(1, 1), self.write_run(2, 0)
        self.assertEqual(prepare_groups([{"runs": [r, d], "target_m": 2.5}], self.paths)[0][0]["target_m"], 2.5)
        with self.assertRaises(ValueError):
            prepare_groups([{"runs": [r, d], "target_m": True}], self.paths)

    def test_uniform_direct_cannot_override_incompatible_subset(self):
        r1, r2, d = self.write_run(1, 1), self.write_run(2, 1), self.write_run(3, 0)
        self.change(r2, "S3W", 50.)
        self.assertTrue(prepare_groups([{"runs": [r1, r2, d], "direct_run": d}], self.paths)[1])


class AdviceTests(unittest.TestCase):
    def result(self, target=None, m=2.1, status="estimated"):
        row = {"Run": "RUN0000001", "Type": "reflection", "m": m, "r_envelope(%)": 85., "mr_status": status}
        if target is not None:
            row["target_m"] = target
        return compare_measurements([row])["public"]["conclusion"]

    def test_unknown_target_does_not_infer_thickness(self):
        text = self.result()
        self.assertNotIn("尚未提供目标", text)
        self.assertIn("如果m值比设计值高", text)
        self.assertNotIn("当前经验模型未反演", text)
        self.assertIn("界面互扩散", text)
        self.assertNotIn("分组规则仅用于", text)

    def test_above_below_and_equal_are_numeric_not_acceptance(self):
        for target, relation in ((2, "高于"), (2.5, "低于"), (2.1, "等于")):
            text = self.result(target)
            self.assertIn("数值" + relation, text)
            self.assertIn("可能与薄膜双层周期偏薄有关", text)
            self.assertIn("截止定义一致", text)

    def test_review_values_do_not_trigger_specific_advice(self):
        self.assertNotIn("目标m=", self.result(2, status="needs_review"))


if __name__ == "__main__":
    unittest.main()
