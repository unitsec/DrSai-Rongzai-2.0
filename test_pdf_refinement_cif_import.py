#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import importlib.util
import math
import unittest
from pathlib import Path

from diffpy.structure.parsers import getParser

from test_si_refine_loop import CIF_FILES, MISSION_NAME, PDF_FILE, SESSION_ID, USER_ID, _detect_r_bounds


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools/pdf_refinement/0pdf_refine.py"


def load_module():
    spec = importlib.util.spec_from_file_location("pdf_refine_mod", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


class PdfRefinementCifImportRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_si_cif_origin_shift_search_selects_eight_atom_expansion(self):
        parser = getParser("cif")
        parser.parseFile(CIF_FILES[0])

        best_expanded, best_offset, best_score = self.mod._select_best_cif_expansion(parser)

        self.assertEqual(best_score[0], 8)
        self.assertEqual(len(best_expanded.expandedpos), 1)
        self.assertEqual(len(best_expanded.expandedpos[0]), 8)
        self.assertIn(best_offset, {(0.125, 0.125, 0.125), (0.375, 0.375, 0.375), (0.625, 0.625, 0.625), (0.875, 0.875, 0.875)})

    def test_si_demo_refine_stays_in_expected_rw_range(self):
        rmin, rmax = _detect_r_bounds(PDF_FILE)
        fit_rmin = max(1.0, rmin)
        safe_rmax = rmax - max(0.01, (rmax - rmin) * 0.001)
        fit_rmax = min(20.0, safe_rmax)

        init = self.mod.initialize_pdf_project(
            data_file=PDF_FILE,
            structure_files=CIF_FILES,
            qmax=25.0,
            rmin=fit_rmin,
            rmax=fit_rmax,
            pdf_source="N",
            user_id=USER_ID,
            session_id=SESSION_ID,
            mission_name=MISSION_NAME,
            task_id="cif_import_regression_init",
        )
        self.assertEqual(init.get("status"), "success")

        res = self.mod.refine(
            project_path=init["project_path"],
            refine_config={
                "run_control": {"cycles": 2},
                "parameter_controls": [
                    {
                        "selector": "phase[1].lat.a",
                        "bind": "lat1",
                        "refine": True,
                    },
                    {
                        "selector": "phase[1].lat.b",
                        "bind": "lat1",
                    },
                    {
                        "selector": "phase[1].lat.c",
                        "bind": "lat1",
                    },
                    {
                        "selector": "phase[1].pscale",
                        "bind": "ps1",
                        "refine": True,
                    },
                ],
            },
            user_id=USER_ID,
            session_id=SESSION_ID,
            mission_name=MISSION_NAME,
            task_id="cif_import_regression_refine",
        )

        self.assertEqual(res.get("status"), "success")
        rp = res.get("refined_params", {})
        self.assertTrue(math.isfinite(float(rp["rw"])))
        self.assertLess(float(rp["rw"]), 0.3)
        self.assertGreater(float(rp["scale_1"]), 0.6)
        self.assertLess(float(rp["scale_1"]), 1.0)

    def test_parameter_controls_can_bind_set_and_fix_scale(self):
        rmin, rmax = _detect_r_bounds(PDF_FILE)
        fit_rmin = max(1.0, rmin)
        safe_rmax = rmax - max(0.01, (rmax - rmin) * 0.001)
        fit_rmax = min(20.0, safe_rmax)

        init = self.mod.initialize_pdf_project(
            data_file=PDF_FILE,
            structure_files=CIF_FILES,
            qmax=25.0,
            rmin=fit_rmin,
            rmax=fit_rmax,
            pdf_source="N",
            user_id=USER_ID,
            session_id=SESSION_ID,
            mission_name=MISSION_NAME,
            task_id="cif_import_parameter_controls_init",
        )
        self.assertEqual(init.get("status"), "success")

        res = self.mod.refine(
            project_path=init["project_path"],
            refine_config={
                "run_control": {"cycles": 1},
                "parameter_controls": [
                    {
                        "selector": "phase[1].pscale",
                        "bind": "lock_scale_group",
                        "initial": 0.9,
                        "fixed": True,
                    }
                ],
            },
            user_id=USER_ID,
            session_id=SESSION_ID,
            mission_name=MISSION_NAME,
            task_id="cif_import_parameter_controls_refine",
        )

        self.assertEqual(res.get("status"), "success")
        rp = res.get("refined_params", {})
        self.assertAlmostEqual(float(rp["scale_1"]), 0.9, places=6)


if __name__ == "__main__":
    unittest.main()