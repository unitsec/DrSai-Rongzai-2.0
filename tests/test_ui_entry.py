import ast
import importlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class UIEntryTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        source = ast.parse((root / "run_ui.py").read_text())
        function = next(
            node for node in source.body
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_ui_entry"
        )
        namespace = {
            "importlib": importlib,
            "os": os,
            "sys": sys,
            "parent_path": str(root),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "run_ui.py", "exec"), namespace)
        self.resolve_ui_entry = namespace["resolve_ui_entry"]
        self.internal_src = str(root / "src")
        self.env_patch = patch.dict(os.environ, {}, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.path_patch = patch.object(sys, "path", list(sys.path))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def test_external_mode_remains_default(self):
        ui = object()
        with patch.object(importlib, "import_module", return_value=SimpleNamespace(ui=ui)):
            self.assertEqual(self.resolve_ui_entry(), (ui, "external", self.internal_src))

    def test_internal_mode_prioritizes_configured_source(self):
        os.environ.update(DRSAI_UI_MODE=" INTERNAL ", DRSAI_UI_INTERNAL_SRC="/custom/ui/src")
        ui = object()
        with patch.object(importlib, "import_module", return_value=SimpleNamespace(ui=ui)):
            self.assertEqual(self.resolve_ui_entry(), (ui, "internal", "/custom/ui/src"))
            self.resolve_ui_entry()
        self.assertEqual(sys.path[0], "/custom/ui/src")
        self.assertEqual(sys.path.count("/custom/ui/src"), 1)

    def test_missing_dependency_reports_install_command_in_both_modes(self):
        for mode in ("internal", "external"):
            with self.subTest(mode=mode):
                os.environ["DRSAI_UI_MODE"] = mode
                error = ModuleNotFoundError("No module named 'tldextract'", name="tldextract")
                with patch.object(importlib, "import_module", side_effect=error):
                    with self.assertRaises(RuntimeError) as raised:
                        self.resolve_ui_entry()
                self.assertIn("missing dependency 'tldextract'", str(raised.exception))
                self.assertIn("python -m pip install -r requirements.txt", str(raised.exception))
                self.assertNotIn("Check DRSAI_UI_INTERNAL_SRC", str(raised.exception))
                self.assertIs(raised.exception.__cause__, error)

    def test_missing_internal_package_reports_source_path(self):
        os.environ["DRSAI_UI_MODE"] = "internal"
        for name in ("drsai_ui", "drsai_ui.run_ui"):
            with self.subTest(name=name):
                error = ModuleNotFoundError(f"No module named '{name}'", name=name)
                with patch.object(importlib, "import_module", side_effect=error):
                    with self.assertRaisesRegex(RuntimeError, "Check DRSAI_UI_INTERNAL_SRC"):
                        self.resolve_ui_entry()

    def test_missing_external_package_reports_mode_option(self):
        error = ModuleNotFoundError("No module named 'drsai_ui'", name="drsai_ui")
        with patch.object(importlib, "import_module", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "DRSAI_UI_MODE=internal"):
                self.resolve_ui_entry()

    def test_other_import_errors_preserve_actual_reason(self):
        error = ImportError("cannot import name 'Example'")
        with patch.object(importlib, "import_module", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "ImportError: cannot import name 'Example'") as raised:
                self.resolve_ui_entry()
        self.assertIs(raised.exception.__cause__, error)


if __name__ == "__main__":
    unittest.main()
