import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from test_batch_reflection import DataFixture
import batch_reflection
from batch_reflection import prepare_groups


class DataRootMatchingTests(DataFixture):
    def nested_pair(self):
        self.subroot = self.root / "proposal" / "2026"
        self.subroot.mkdir(parents=True)
        runs = ["RUN0000963", "RUN0000964"]
        for run, rz in zip(runs, (0.77, 0.)):
            folder = self.subroot / run
            folder.mkdir()
            doc = {"summary": {"json": {"lamda_min": 3., "lamda_max": 8.4,
                   "devices": {"Rz": rz, "S1H": .8, "S1W": 32., "S3H": .6, "S3W": 32.}}}}
            (folder / run).write_text(json.dumps(doc), encoding="utf-8")
        return {"runs": runs, "data_root": str(self.subroot)}

    def test_nested_directory_reaches_legacy_preflight_and_plan(self):
        group = self.nested_pair()
        with patch.object(batch_reflection, "_prepare_explicit_groups", wraps=batch_reflection._prepare_explicit_groups) as legacy:
            plans, questions = prepare_groups([group], self.paths)
        self.assertFalse(questions)
        self.assertEqual(plans[0]["data_root"], str(self.subroot.resolve()))
        self.assertEqual(plans[0]["direct_run"], "RUN0000964")
        self.assertEqual(legacy.call_args.args[0][0]["data_root"], str(self.subroot.resolve()))

    def test_outside_root_rejected_before_file_read(self):
        group = self.nested_pair()
        group["data_root"] = str(self.root.parent)
        with self.assertRaisesRegex(ValueError, "超出"):
            prepare_groups([group], self.paths)

    def test_relative_or_nonstring_directory_rejected(self):
        group = self.nested_pair()
        for value in ("proposal/2026", "../escape", None, 42):
            group["data_root"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "绝对路径"):
                prepare_groups([group], self.paths)

    def test_missing_nested_directory_rejected(self):
        group = self.nested_pair()
        group["data_root"] = str(self.root / "missing")
        with self.assertRaisesRegex(ValueError, "目录不存在"):
            prepare_groups([group], self.paths)

    @unittest.skipUnless(os.name == "posix", "目录符号链接逃逸检查在服务器Linux运行")
    def test_symlink_directory_cannot_escape_selected_source(self):
        group = self.nested_pair()
        link = self.root / "escape"
        link.symlink_to(self.root.parent, target_is_directory=True)
        group["data_root"] = str(link)
        with self.assertRaisesRegex(ValueError, "超出"):
            prepare_groups([group], self.paths)

    def test_source_alias_boundary_is_enforced(self):
        group = self.nested_pair()
        other = self.root / "other-source"
        other.mkdir()
        self.paths["data_sources"]["other"] = str(other)
        group["data_source"] = "other"
        with self.assertRaisesRegex(ValueError, "超出"):
            prepare_groups([group], self.paths)


if __name__ == "__main__":
    unittest.main()
