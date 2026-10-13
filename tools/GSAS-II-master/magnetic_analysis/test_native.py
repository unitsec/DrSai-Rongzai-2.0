"""Generic native subgroup integration controls; no remote/sample examples."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from . import test_engine as fixtures
from . import workflow as w
from .native import assemble, enumerate_native, export_full, refine_native


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LocalSymmetryTests()
        self.fixture.setUp()
        self.directory = self.fixture.directory
        self.sc, self.lat, self.spc, _, self.wrapper = w._modules()

    def tearDown(self):
        self.fixture.tearDown()

    def candidate(self, parent, origin=None):
        sg = self.spc.SpcGroup("P 1")[1]
        self.spc.SetMagnetic(sg)
        descriptor = {"Name": "native", "SGData": sg, "Trans": np.eye(3),
                      "Uvec": np.asarray(origin if origin is not None else [0., 0., 0.]),
                      "Cell": parent["General"]["Cell"][1:]}
        phase, sites = self.wrapper._build_magnetic_phase(parent, descriptor, {0, 1}, 1.)
        candidate = {"id": "subgroup_000", "transformation_matrix": np.eye(3).tolist(),
                     "origin_shift": descriptor["Uvec"].tolist(), "magnetic_sites": sites}
        return candidate, phase

    def test_native_phase_origin_scales_independent_orbits_and_export(self):
        project = self.sc.G2Project(newgpx=str(self.directory / "native.gpx"))
        hist = project.add_simulated_powder_histogram(
            "native", str(self.fixture.input / "instruments" / "bank_a.instprm"),
            1, 9, Npoints=800, scale=100000)
        parent = project.add_phase(str(self.fixture.cif), phasename="nuclear", histograms=[hist])
        candidate, phase = self.candidate(parent.data, [.125, .05, -.1])
        nuclear, magnetic = assemble(project, copy.deepcopy(parent.data), candidate, phase,
                                     refine_cell=False)
        self.assertEqual(len(nuclear.data["Atoms"]), 3)
        self.assertEqual(len(magnetic.data["Atoms"]), 2)
        mx = magnetic.data["General"]["AtomPtrs"][0]
        nx = nuclear.data["General"]["AtomPtrs"][0]
        np.testing.assert_allclose(magnetic.data["Atoms"][0][mx:mx+3],
                                   nuclear.data["Atoms"][0][nx:nx+3])
        constraints = str(project.data["Constraints"])
        self.assertNotIn("AM", constraints)  # no inter-orbit amplitude coupling
        for atom, magnitude in zip(magnetic.data["Atoms"], (2.3, -1.7)):
            atom[mx+4:mx+7] = [0., 0., magnitude]
            atom[2] = ""
        nuclear.set_refinements({"Atoms": {"all": " "}})
        nuclear.set_HAP_refinements({"Scale": False})
        magnetic.set_HAP_refinements({"Scale": False})
        for h in project.histograms():
            h.data["Background"][0][1] = False
        project.set_Controls("cycles", 0)
        project.refine()
        full = np.asarray(hist.getdata("ycalc")).copy()
        magnetic.data["Histograms"][hist.name]["Use"] = False
        project.refine()
        nuclear_profile = np.asarray(hist.getdata("ycalc")).copy()
        self.assertGreater(float(np.max(full - nuclear_profile)), 0.)
        magnetic.data["Histograms"][hist.name]["Use"] = True
        physical = export_full(self.directory / "native.mcif", nuclear.data, magnetic.data)
        np.testing.assert_allclose(physical["moment_norms"][:2], [2.3, 1.7])
        self.assertIn("O", (self.directory / "native.mcif").read_text())

    def test_native_wrapper_unrestricted_and_persisted(self):
        inputs = w.discover_inputs(self.directory)
        w._write(self.directory / "search_report.json",
                 {"inputs": inputs, "k_candidates": [{"k": [0., 0., 0.]}]})
        parent = self.wrapper.load_parent_phase(inputs["cif"])
        candidate, phase = self.candidate(parent)
        response = {**candidate, "phase": phase, "bns_number": "1.1",
                    "magnetic_space_group": "P 1", "cell": phase["General"]["Cell"][1:]}
        with patch.object(self.wrapper, "search_magnetic_structure_candidates", return_value=[response]) as search:
            result = enumerate_native(self.directory, self.directory)
        self.assertEqual(result["candidate_count"], 1)
        self.assertFalse(search.call_args.kwargs["exact_wyckoff_orbit"])
        self.assertFalse(search.call_args.kwargs["maximal_only"])
        persisted = w._read(result["report_path"])
        self.assertNotIn("phase", persisted["modes"][0])
        self.assertTrue(Path(persisted["modes"][0]["phase_path"]).is_file())

    def test_supercell_nuclear_intensity_normalization_and_origin(self):
        project = self.sc.G2Project(newgpx=str(self.directory / "supercell.gpx"))
        hist = project.add_simulated_powder_histogram(
            "supercell", str(self.fixture.input / "instruments" / "bank_a.instprm"),
            1, 9, Npoints=800, scale=100000)
        parent = project.add_phase(str(self.fixture.cif), phasename="nuclear", histograms=[hist])
        parent.set_HAP_refinements({"Scale": False})
        hist.data["Background"][0][1] = False
        project.set_Controls("cycles", 0)
        project.refine()
        original = np.asarray(project.histogram(hist.name).getdata("ycalc")).copy()
        parent_data = copy.deepcopy(parent.data)
        transform = np.diag([2., 1., 1.])
        sg = self.spc.SpcGroup("P 1")[1]
        self.spc.SetMagnetic(sg)
        descriptor = {"Name": "supercell", "SGData": sg, "Trans": transform,
                      "Uvec": np.array([.125, .05, -.1]),
                      "Cell": self.lat.TransformCell(parent_data["General"]["Cell"][1:7], transform)}
        phase, _ = self.wrapper._build_magnetic_phase(parent_data, descriptor, {0, 1}, 1.)
        candidate = {"id": "subgroup_000", "transformation_matrix": transform.tolist(),
                     "origin_shift": descriptor["Uvec"].tolist()}
        nuclear, magnetic = assemble(project, parent_data, candidate, phase, refine_cell=False)
        nuclear.set_refinements({"Atoms": {"all": " "}})
        nuclear.set_HAP_refinements({"Scale": False})
        magnetic.set_HAP_refinements({"Scale": False})
        magnetic.data["Histograms"][hist.name]["Use"] = False
        project.refine()
        transformed = np.asarray(project.histogram(hist.name).getdata("ycalc"))
        np.testing.assert_allclose(transformed, original, rtol=2e-5, atol=1e-6)
        self.assertEqual(len(nuclear.data["Atoms"]), 6)
        self.assertEqual(len(magnetic.data["Atoms"]), 4)

    def test_network_error_never_falls_back_to_local_modes(self):
        inputs = w.discover_inputs(self.directory)
        w._write(self.directory / "search_report.json",
                 {"inputs": inputs, "k_candidates": [{"k": [0., 0., 0.]}]})
        with patch.object(self.wrapper, "search_magnetic_structure_candidates",
                          side_effect=ConnectionError("generic network unavailable")):
            with self.assertRaisesRegex(ConnectionError, "generic network unavailable"):
                enumerate_native(self.directory, self.directory)

    def test_generic_transport_allowlist_cache_and_private_payload_rejection(self):
        inputs = w.discover_inputs(self.directory)
        w._write(self.directory / "search_report.json",
                 {"inputs": inputs, "k_candidates": [{"k": [0., 0., 0.]}]})
        parent = self.wrapper.load_parent_phase(inputs["cif"])
        candidate, phase = self.candidate(parent)
        response = {**candidate, "phase": phase, "bns_number": "1.1",
                    "magnetic_space_group": "P 1"}
        pathmod = __import__("GSASIIpath")
        query = {"generators": "x,y,z", "km1x": "0", "km1y": "0", "km1z": "0"}
        endpoint = "https://www.cryst.ehu.es/cgi-bin/cryst/programs/subgrmag1_general_GSAS.pl?"
        def search(*args, **kwargs):
            self.assertEqual(pathmod.postURL(endpoint, query), "generic symmetry response")
            return [response]
        with patch.object(pathmod, "postURL", return_value="generic symmetry response") as transport:
            with patch.object(self.wrapper, "search_magnetic_structure_candidates", side_effect=search):
                enumerate_native(self.directory, self.directory)
                enumerate_native(self.directory, self.directory)
        transport.assert_called_once()
        self.assertEqual(transport.call_args.kwargs["timeout"], 30)
        def private_query(*args, **kwargs):
            pathmod.postURL(endpoint, {**query, "atoms": "private atomic data"})
        with patch.object(self.wrapper, "search_magnetic_structure_candidates", side_effect=private_query):
            with self.assertRaises(PermissionError):
                enumerate_native(self.directory, self.directory)

    def test_native_real_profile_refinement(self):
        project = self.sc.G2Project(newgpx=str(self.directory / "generate.gpx"))
        hist = project.add_simulated_powder_histogram(
            "native", str(self.fixture.input / "instruments" / "bank_a.instprm"),
            1, 9, Npoints=1000, scale=100000)
        parent = project.add_phase(str(self.fixture.cif), phasename="nuclear", histograms=[hist])
        project.set_Controls("cycles", 0)
        project.refine()
        x = np.asarray(hist.getdata("x")).copy()
        high = np.asarray(hist.getdata("ycalc")).copy()
        candidate, phase = self.candidate(parent.data)
        nuclear, magnetic = assemble(project, copy.deepcopy(parent.data), candidate, phase,
                                     refine_cell=False)
        mx = magnetic.data["General"]["AtomPtrs"][0]
        for atom in magnetic.data["Atoms"]:
            atom[mx+4:mx+7] = [0., 0., 2.3]
            atom[2] = ""
        nuclear.set_refinements({"Atoms": {"all": " "}})
        nuclear.set_HAP_refinements({"Scale": False})
        magnetic.set_HAP_refinements({"Scale": False})
        project.refine()
        low = np.asarray(hist.getdata("ycalc")).copy()
        for name, intensity in [("high_temperature", high), ("low_temperature", low)]:
            np.savetxt(self.fixture.input / name / "bank_a.dat",
                       np.column_stack([x, intensity, np.sqrt(np.maximum(intensity, 1))]))
        inputs = w.discover_inputs(self.directory)
        w._write(self.directory / "search_report.json",
                 {"inputs": inputs, "k_candidates": [{"k": [0., 0., 0.]}]})
        response = {**candidate, "phase": phase, "bns_number": "1.1",
                    "magnetic_space_group": "P 1", "cell": phase["General"]["Cell"][1:]}
        with patch.object(self.wrapper, "search_magnetic_structure_candidates", return_value=[response]):
            enumerate_native(self.directory, self.directory)
        result = refine_native(self.directory, self.directory, cycles=12,
                               refine_cell=False, refine_profile=False,
                               time_limit_seconds=300, background_coeffs=3)
        report = w._read(result["report_path"])
        self.assertEqual(result["failed_models"], 0)
        self.assertTrue(report["complete"])
        evidence = report["models"][0]["fit_evidence_paths"]
        self.assertEqual(len(evidence), 1)
        import xml.etree.ElementTree as ET
        root = ET.parse(evidence[0]["plot_path"]).getroot()
        self.assertEqual(len(root.findall("{http://www.w3.org/2000/svg}polyline")), 4)
        self.assertEqual(evidence[0]["full_resolution_points"],
                         len(np.loadtxt(evidence[0]["csv_path"], delimiter=",")))
        np.testing.assert_allclose(report["models"][0]["physical"]["moment_norms"][:2],
                                   [2.3, 2.3], atol=.15)

    def test_native_positive_profile_boundary_is_recomputed(self):
        from .native import _fit_native
        inst = {"alpha": [1., 1., True], "beta-0": [.02, .02, True],
                "sig-1": [1., -1., True], "X": [0., 0., False]}
        hist = unittest.mock.Mock()
        hist.id = 0
        inst.update({"Type": ["PNT", "PNT", False], "difC": [1000., 1000., False],
                     "difA": [0., 0., False], "difB": [0., 0., False],
                     "Zero": [0., 0., False]})
        hist.data = {"Instrument Parameters": [inst, {}], "Limits": [None, [1000., 4000.]]}
        hist.getdata.return_value = np.linspace(1000., 4000., 50)
        project = unittest.mock.Mock()
        project.data = {"Covariance": {"data": {"Rvals": {}}}}
        project.histograms.return_value = [hist]
        with patch.object(w, "_refine", return_value={"converged": True}) as refine:
            quality = _fit_native(project, 12)
        self.assertEqual(inst["sig-1"][1], 1e-8)
        self.assertEqual(quality["active_profile_boundaries"], [":0:sig-1"])
        self.assertEqual(refine.call_args.kwargs["relative_tolerance"], .001)
        project.data["Covariance"]["data"]["Rvals"] = {"DelChi2": -1.2}
        with patch.object(w, "_refine", return_value={"converged": True}):
            with self.assertRaisesRegex(RuntimeError, "increasing objective"):
                _fit_native(project, 12)
        inst["beta-1"] = [-.1, -.1, False]
        inst["beta-0"][1] = .02
        project.data["Covariance"]["data"]["Rvals"] = {}
        with patch.object(w, "_refine", return_value={"converged": True}):
            quality = _fit_native(project, 12)
        self.assertGreater(inst["beta-0"][1], .1)
        self.assertIn(":0:beta-0", quality["active_profile_boundaries"])
        reflection = np.zeros((1, 18))
        reflection[0, [4, 5, 6, 7, 12, 13]] = [2., 2000., 1., 0., 1., -.1]
        hist.data["Reflection Lists"] = {"generic": {"RefList": reflection}}
        with patch.object(w, "_refine", return_value={"converged": True}):
            with self.assertRaisesRegex(RuntimeError, "Invalid final fitted TOF"):
                _fit_native(project, 12)

    def test_two_site_transformed_native_residual_jacobian(self):
        """Compare the actual constrained residual Jacobian, not SF² alone."""
        import GSASIIstrMain as main
        import GSASIIstrMath as sm
        import GSASIImapvars as mv
        from threadpoolctl import threadpool_limits
        project = self.sc.G2Project(newgpx=str(self.directory / "jacobian.gpx"))
        hist = project.add_simulated_powder_histogram(
            "jacobian", str(self.fixture.input / "instruments" / "bank_a.instprm"),
            4, 9, Npoints=500, scale=100000)
        hist.data["Sample Parameters"]["Scale"][1] = False
        parent = project.add_phase(str(self.fixture.cif), phasename="nuclear", histograms=[hist])
        cell = [4., 4., 6., 90., 90., 120.]
        parent.data["General"]["Cell"][1:7] = cell
        parent.data["General"]["Cell"][7] = self.lat.calc_V(self.lat.cell2A(cell))
        transform = np.array([[1., 1., 0.], [-1., 1., 0.], [0., 0., 1.]])
        sg = self.spc.SpcGroup("P 1")[1]
        self.spc.SetMagnetic(sg)
        desc = {"Name": "native", "SGData": sg, "Trans": transform,
                "Uvec": np.array([.12, .05, .03]),
                "Cell": self.lat.TransformCell(cell, transform)}
        phase, _ = self.wrapper._build_magnetic_phase(parent.data, desc, {0, 1}, 1.)
        candidate = {"id": "subgroup_000", "transformation_matrix": transform.tolist(),
                     "origin_shift": desc["Uvec"].tolist()}
        nuclear, magnetic = assemble(project, copy.deepcopy(parent.data), candidate, phase)
        mx = magnetic.data["General"]["AtomPtrs"][0]
        for index, atom in enumerate(magnetic.data["Atoms"]):
            atom[mx+4:mx+7] = (np.array([1.3, .6, .9]) *
                               (1 if index % 2 == 0 else -1)).tolist()
            atom[2] = "MU"
        hist.set_refinements({"Instrument Parameters": ["alpha", "beta-0", "sig-1", "X"]})
        hist.data["Instrument Parameters"][0]["sig-1"][1] = 3.
        hist.data["Instrument Parameters"][0]["X"][1] = .4
        class Captured(Exception):
            pass
        evidence = []
        def inspect(controls, histograms, phases, restraint, rigid, parms, varying,
                    calc, pawley, seq, stream, dlg, **kwargs):
            mv.Map2Dict(parms, varying)
            values = np.asarray(sm.Dict2Values(parms, varying), float)
            args = ([histograms, phases, restraint, rigid], parms, varying, calc, pawley, dlg)
            original = copy.deepcopy(parms)
            residual = np.asarray(sm.errRefine(values, *args)).copy()
            analytic = np.asarray(sm.dervRefine(values, *args))
            vector, hessian = sm.HessRefine(values, *args)
            evidence.append(("Hessian-gradient", np.linalg.norm(vector + analytic @ residual) /
                             max(np.linalg.norm(vector), 1e-12), 1e-8))
            evidence.append(("Hessian-normal-matrix",
                             np.linalg.norm(hessian - analytic @ analytic.T) /
                             max(np.linalg.norm(hessian), 1e-12), 1e-8))
            for index, name in enumerate(varying):
                if "Back" in name:
                    continue
                step = max(abs(values[index]) * 1e-3, 1e-4)
                if "::A" in name and "::AM" not in name and "::AU" not in name:
                    step = 1e-6
                for suffix, size in [(":X", .01), (":sig-1", .1),
                                     (":alpha", .01), (":beta-0", 1e-4)]:
                    if name.endswith(suffix):
                        step = size
                if "::AU" in name:
                    step = 1e-4
                upper, lower = values.copy(), values.copy()
                upper[index] += step
                lower[index] -= step
                parms.clear()
                parms.update(copy.deepcopy(original))
                up = np.asarray(sm.errRefine(upper, *args)).copy()
                parms.clear()
                parms.update(copy.deepcopy(original))
                down = np.asarray(sm.errRefine(lower, *args)).copy()
                numerical = (up - down) / (2 * step)
                relative = np.linalg.norm(analytic[index] - numerical) / max(
                    np.linalg.norm(numerical), 1e-12)
                evidence.append((name, relative))
                # The forward Fortran profiles are float32: resolved steps are
                # essential, and moving finite peak windows limit cell accuracy.
                bound = .08 if "::A" in name and "::AM" not in name and "::AU" not in name else .02
                evidence[-1] = (name, relative, bound)
            raise Captured()
        with threadpool_limits(1), patch.object(main, "RefineCore", side_effect=inspect):
            try:
                project.refine()
            except Captured:
                pass
        for name, relative, bound in evidence:
            self.assertLess(relative, bound, f"{name}: {relative}")
        self.assertEqual(sum("::AM" in name for name, _, _ in evidence), 6)
        self.assertTrue(any(name.endswith(":Scale") for name, _, _ in evidence))

    def test_native_antiferromagnetic_orbit_refines_real_profile(self):
        """One time-reversed inversion orbit; native symmetry generates both sites."""
        self.fixture.cif.write_text(
            "data_generic\n_cell_length_a 4\n_cell_length_b 5\n_cell_length_c 6\n"
            "_cell_angle_alpha 90\n_cell_angle_beta 90\n_cell_angle_gamma 90\n"
            "_symmetry_space_group_name_H-M 'P -1'\n"
            "loop_\n_space_group_symop_operation_xyz\n'x,y,z'\n'-x,-y,-z'\n"
            "loop_\n_atom_site_label\n_atom_site_type_symbol\n_atom_site_fract_x\n"
            "_atom_site_fract_y\n_atom_site_fract_z\n_atom_site_occupancy\n_atom_site_U_iso_or_equiv\n"
            "Fe1 Fe .17 .23 .31 1 .01\nO1 O .35 .1 .45 1 .01\n")
        project = self.sc.G2Project(newgpx=str(self.directory / "af_generated.gpx"))
        hist = project.add_simulated_powder_histogram(
            "af", str(self.fixture.input / "instruments" / "bank_a.instprm"),
            2, 9, Npoints=800, scale=100000)
        hist.data["Sample Parameters"]["Scale"][1] = False
        parent = project.add_phase(str(self.fixture.cif), phasename="nuclear", histograms=[hist])
        parent.set_refinements({"Atoms": {"all": " "}})
        parent.set_HAP_refinements({"Scale": False})
        project.set_Controls("cycles", 0)
        project.refine()
        x = np.asarray(hist.getdata("x")).copy()
        high = np.asarray(hist.getdata("ycalc")).copy()
        sg = copy.deepcopy(parent.data["General"]["SGData"])
        self.spc.SetMagnetic(sg)
        sg["SpnFlp"] = np.array([1, -1])
        sg["MagSpGrp"] = "P -1'"
        desc = {"Name": "nativeAF", "SGData": sg, "Trans": np.eye(3),
                "Uvec": np.zeros(3), "Cell": parent.data["General"]["Cell"][1:]}
        phase, sites = self.wrapper._build_magnetic_phase(parent.data, desc, {0}, 1.)
        candidate = {"id": "subgroup_000", "transformation_matrix": np.eye(3).tolist(),
                     "origin_shift": [0., 0., 0.], "magnetic_sites": sites}
        nuclear, magnetic = assemble(project, copy.deepcopy(parent.data), candidate, phase,
                                     refine_cell=False)
        mx = magnetic.data["General"]["AtomPtrs"][0]
        magnetic.data["Atoms"][0][mx+4:mx+7] = [0., 0., 2.3]
        magnetic.data["Atoms"][0][2] = ""
        nuclear.set_refinements({"Atoms": {"all": " "}})
        nuclear.set_HAP_refinements({"Scale": False})
        magnetic.set_HAP_refinements({"Scale": False})
        project.refine()
        low = np.asarray(hist.getdata("ycalc")).copy()
        # MINPACK convergence is carried in ier, unlike GSAS's Hessian result.
        from .native import _fit_native
        hist.data["data"][1][1] = low.copy()
        hist.data["data"][1][2] = 1 / np.maximum(low, 1)
        magnetic.data["Atoms"][0][mx+4:mx+7] = [0., 0., 1.]
        magnetic.data["Atoms"][0][2] = "M"
        project.data["Controls"]["data"]["deriv type"] = "analytic Jacobian"
        project.index_ids()
        for component in ("x", "y"):
            project.set_Frozen(variable=f"1::AM{component}:0", mode="add")
        native_quality = _fit_native(project, 24)
        rv = project.data["Covariance"]["data"]["Rvals"]
        self.assertTrue(native_quality["converged"])
        self.assertIn(rv["solver_status"], (1, 2, 3, 4))
        self.assertGreater(rv["DelChi2"], 0)
        np.testing.assert_allclose(project.phase("subgroup_000").data["Atoms"][0][mx+4:mx+7],
                                   [0., 0., 2.3], atol=.01)
        for name, intensity in [("high_temperature", high), ("low_temperature", low)]:
            np.savetxt(self.fixture.input / name / "bank_a.dat",
                       np.column_stack([x, intensity, np.sqrt(np.maximum(intensity, 1))]))
        inputs = w.discover_inputs(self.directory)
        w._write(self.directory / "search_report.json",
                 {"inputs": inputs, "k_candidates": [{"k": [0., 0., 0.]}]})
        response = {**candidate, "phase": phase, "bns_number": "2.6",
                    "magnetic_space_group": "P -1'", "cell": phase["General"]["Cell"][1:]}
        with patch.object(self.wrapper, "search_magnetic_structure_candidates", return_value=[response]):
            enumerate_native(self.directory, self.directory)
        result = refine_native(self.directory, self.directory, cycles=24,
                               refine_cell=False, refine_profile=False,
                               time_limit_seconds=300, background_coeffs=3)
        report = w._read(result["report_path"])
        self.assertEqual(result["failed_models"], 0)
        fitted = report["models"][0]
        self.assertLess(fitted["quality"]["weighted_residual_sum"],
                        report["baseline"]["low_nuclear_control"]["weighted_residual_sum"])
        np.testing.assert_allclose(fitted["physical"]["moment_norms"][:2], [2.3, 2.3], atol=.05)
        cart = np.asarray(fitted["physical"]["moments_cartesian"])
        np.testing.assert_allclose(cart[0], -cart[1], atol=1e-6)
        print("NATIVE_AF_VERIFIED", {
            "nuclear_only_chisq": report["baseline"]["low_nuclear_control"]["weighted_residual_sum"],
            "magnetic_chisq": fitted["quality"]["weighted_residual_sum"],
            "moment_norms": fitted["physical"]["moment_norms"][:2],
            "converged": fitted["quality"]["converged"]})


if __name__ == "__main__":
    unittest.main()
