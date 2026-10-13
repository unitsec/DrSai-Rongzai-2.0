"""Synthetic controls; no external case, historical project, or reference files."""

from pathlib import Path
import shutil
import unittest
import uuid
from types import SimpleNamespace

import numpy as np

from magnetic_analysis.science import (
    collinear_modes, difference_peaks, moment_to_cartesian, moment_to_crystalaxis,
)
from magnetic_analysis.workflow import (
    _integer_indexing, _modules, _symmetry_mcif, analyze, discover_inputs,
    enumerate_local_candidates as enumerate_candidates,
)


class NumericalTests(unittest.TestCase):
    def test_profile_cache_exactness_bounds_and_restoration(self):
        from magnetic_analysis.profiles import cached_tof_profiles
        calls = []
        def calculate(*args):
            calls.append(args[:-1])
            return (np.asarray(args[-1]) * args[0], 1.0)
        module = SimpleNamespace(getEpsVoigt=calculate, getdEpsVoigt=calculate)
        x = np.arange(30, dtype=float)
        args = (1, 2, 3, 4, 5, x)
        with cached_tof_profiles(module, max_bytes=2000) as stats:
            first = module.getEpsVoigt(*args)
            first[0][:] = -999
            np.testing.assert_array_equal(module.getEpsVoigt(*args)[0], x)
            module.getEpsVoigt(1 + 1e-14, *args[1:])
            self.assertEqual(stats["hits"], 1)
            self.assertEqual(stats["misses"], 2)
            self.assertLessEqual(stats["bytes"], 2000)
        self.assertIs(module.getEpsVoigt, calculate)
        self.assertEqual(len(calls), 2)

    def test_nonorthogonal_moment_units(self):
        lattice = np.array([[4, 0, 0], [-2, 3.464101615, 0], [0.3, 0.4, 7]])
        m = np.array([1, 1, 0.4])
        cart = moment_to_cartesian(m, lattice)
        np.testing.assert_allclose(moment_to_crystalaxis(cart, lattice), m)
        for mode in collinear_modes(2, lattice):
            np.testing.assert_allclose(np.linalg.norm(moment_to_cartesian(mode, lattice), axis=1), 1)
        self.assertFalse(np.isclose(np.linalg.norm(m), np.linalg.norm(cart)))

    def test_work_bound(self):
        with self.assertRaisesRegex(ValueError, "bound"):
            list(collinear_modes(8, np.eye(3), max_candidates=100))
        with self.assertRaises(ValueError):
            list(collinear_modes(0, np.eye(3)))

    @staticmethod
    def spectra(extra=True):
        d = np.linspace(0.5, 5, 4501)
        def nuclear(x):
            return sum(300 * np.exp(-((x - c) / 0.012)**2) for c in (1, 1.7, 2.3, 3.1, 4.1))
        high = 10 + nuclear(d)
        low = 18 + 1.8 * nuclear((d - 0.001) / 1.003)
        if extra:
            low += 100 * np.exp(-((d - 2.8) / 0.014)**2)
        return (d, high, np.ones(len(d))), (d, low, np.ones(len(d)))

    def test_alignment_and_extra_peak(self):
        diag, _ = difference_peaks(*self.spectra(), significance=8)
        self.assertAlmostEqual(diag["scale_high_to_low"], 1.8, places=2)
        self.assertAlmostEqual(diag["relative_d_strain"], 0.003, places=4)
        self.assertTrue(any(abs(p["d"] - 2.8) < 0.01 for p in diag["peaks"]))

    def test_nonmagnetic_control(self):
        diag, _ = difference_peaks(*self.spectra(False), significance=8)
        self.assertEqual(diag["peaks"], [])

    def test_no_nuclear_anchors(self):
        d = np.linspace(1, 3, 100)
        with self.assertRaisesRegex(ValueError, "anchors"):
            difference_peaks((d, np.ones(100), np.ones(100)),
                             (d, np.ones(100), np.ones(100)))

    def test_integer_hkl_without_nuclear_absence_filter(self):
        result = _integer_indexing([4, 5, 6, 90, 90, 90], [4.0], tolerance=1e-5)
        self.assertTrue(result[0]["indexed"])

    def test_indexing_allows_small_initial_lattice_mismatch(self):
        result = _integer_indexing([4, 5, 6, 90, 90, 90],
                                   [3.96, 4.95, 5.94], tolerance=0.001,
                                   fit_dilation=True)
        self.assertTrue(all(m["indexed"] for m in result))
        self.assertAlmostEqual(result[0]["fitted_lattice_dilation"], -0.01, places=3)

    def test_cross_bank_peak_evidence_rejects_overlap_and_single_bank(self):
        from magnetic_analysis.workflow import _propagation_peaks
        peak = {"d": 2, "significance": 40, "width_d": 0.01, "nuclear_overlap": False}
        banks = [{"name": "a", "peaks": [peak, {**peak, "d": 3}]},
                 {"name": "b", "peaks": [{**peak, "d": 2.001},
                                       {**peak, "d": 3, "nuclear_overlap": True}]}]
        found = _propagation_peaks(banks)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["banks"], ["a", "b"])

    def test_magnetic_stabilizer_refines_free_plane_not_seed_ratio(self):
        from magnetic_analysis.workflow import _direction_space
        lattice = np.diag([4.0, 5.0, 6.0])
        positions = np.array([[0.0, 0.0, 0.0]])
        space = _direction_space(lattice, positions, [1], np.array([[1.0, 1.0, 0.0]]))
        self.assertEqual(space["direction_dimension"], 2)
        constraints = np.array(space["direction_constraints_crystalaxis"])
        np.testing.assert_allclose(constraints @ [1.0, 2.0, 0.0], 0, atol=1e-8)
        self.assertGreater(np.linalg.norm(constraints @ [0.0, 0.0, 1.0]), 0.5)
        axial = _direction_space(lattice, positions, [1], np.array([[0.0, 0.0, 1.0]]))
        self.assertEqual(axial["direction_dimension"], 1)


class LocalSymmetryTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parent / f".synthetic_test_{uuid.uuid4().hex}"
        self.directory.mkdir(exist_ok=True)
        self.input = self.directory / "input"
        self.input.mkdir(exist_ok=True)
        self.cif = self.input / "initial.cif"
        self.cif.write_text(
            "data_synthetic\n_cell_length_a 4\n_cell_length_b 5\n_cell_length_c 6\n"
            "_cell_angle_alpha 90\n_cell_angle_beta 90\n_cell_angle_gamma 90\n"
            "_symmetry_space_group_name_H-M 'P 1'\n"
            "loop_\n_space_group_symop_operation_xyz\n'x,y,z'\n"
            "loop_\n_atom_site_label\n_atom_site_type_symbol\n_atom_site_fract_x\n"
            "_atom_site_fract_y\n_atom_site_fract_z\n_atom_site_occupancy\n_atom_site_U_iso_or_equiv\n"
            "Fe1 Fe 0 0 0 1 0.01\nFe2 Fe 0.25 0.3 0.4 1 0.01\nO1 O 0.1 0.2 0.3 1 0.01\n")
        for temp in ("high_temperature", "low_temperature", "instruments"):
            (self.input / temp).mkdir(exist_ok=True)
        for temp in ("high_temperature", "low_temperature"):
            (self.input / temp / "bank_a.dat").write_text("1 2 1\n")
        (self.input / "instruments" / "bank_a.instprm").write_text(
            "#GSAS-II instrument parameter file\nType:PNT\nBank:1.0\n"
            "fltPath:20.0\n2-theta:90.0\ndifC:2000.0\ndifA:0.0\ndifB:0.0\nZero:0.0\n"
            "alpha:1.0\nbeta-0:0.02\nbeta-1:0.0\nbeta-q:0.0\n"
            "sig-0:20.0\nsig-1:0.0\nsig-2:0.0\nsig-q:0.0\n"
            "X:0.0\nY:0.0\nZ:0.0\n")

    def tearDown(self):
        shutil.rmtree(self.directory)

    def test_discovery_and_ambiguous_instrument(self):
        self.assertEqual(discover_inputs(self.directory)["banks"][0]["name"], "bank_a")
        (self.input / "instruments" / "bank_a.prm").write_text("# duplicate")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            discover_inputs(self.directory)

    def test_generic_tof_header_normalization(self):
        from magnetic_analysis.workflow import _histogram
        sc, _, _, _, _ = _modules()
        data = self.input / "high_temperature" / "bank_a.dat"
        data.write_text("Generic TOF profile\nIntensity normalization description\nTOF INT ERR\n"
                        + "\n".join(f"{1000 + 10 * i} 3 1" for i in range(100)))
        bank = discover_inputs(self.directory)["banks"][0]
        project = sc.G2Project(newgpx=str(self.directory / "header.gpx"))
        hist = _histogram(project, bank, "high")
        np.testing.assert_array_equal(hist.getdata("x"), np.arange(1000, 2000, 10))
        np.testing.assert_array_equal(hist.getdata("yobs"), np.full(100, 3))
        self.assertTrue(any(str(data) in comment for comment in hist.data["Comments"]))

    def test_input_symlink_escape_rejected(self):
        outside = self.directory / "outside.dat"
        outside.write_text("1 1 1\n")
        data = self.input / "high_temperature" / "bank_a.dat"
        data.unlink()
        data.symlink_to(outside.resolve())
        with self.assertRaisesRegex(ValueError, "symlink escapes"):
            discover_inputs(self.directory)

    def test_local_candidates_and_nonzero_rejection(self):
        import json
        inputs = discover_inputs(self.directory)
        (self.directory / "search_report.json").write_text(json.dumps({
            "inputs": inputs, "k_candidates": [{"k": [0, 0, 0]}]}))
        result = enumerate_candidates(self.directory, self.directory, magnetic_elements=["Fe"])
        self.assertGreater(result["candidate_count"], 1)
        report = json.loads(Path(result["report_path"]).read_text())
        self.assertTrue(any(np.dot(m["moment_crystalaxis"][0],
                                  m["moment_crystalaxis"][1]) < 0 for m in report["modes"]))
        with self.assertRaises(NotImplementedError):
            enumerate_candidates(self.directory, self.directory, k_vectors=[[0.5, 0, 0]])

    def test_phase_and_mcif_preserve_antiparallel_moments(self):
        from magnetic_analysis.workflow import _insert_magnetic, _expanded_parent
        sc, _, _, _, wrapper = _modules()
        parent = wrapper.load_parent_phase(self.cif)
        atoms, _, vectors, positions, _ = _expanded_parent(parent)
        mode = {"id": "synthetic", "moment_crystalaxis": [[0, 0, 1], [0, 0, -1], [0, 0, 0]]}
        project = sc.G2Project(newgpx=str(self.directory / "synthetic.gpx"))
        hist = project.add_simulated_powder_histogram(
            "synthetic", str(self.input / "instruments" / "bank_a.instprm"),
            1, 8, Npoints=500)
        nuclear = project.add_phase(str(self.cif), phasename="nuclear", histograms=[hist])
        candidate = {"selected_parent_indices": [0, 1],
                     "expanded_positions": positions.tolist(),
                     "expanded_types": ["Fe", "Fe", "O"]}
        mag = _insert_magnetic(project, nuclear.data, candidate, mode)
        self.assertEqual(len(mag.data["Atoms"]), 2)
        physical = _symmetry_mcif(self.directory / "synthetic.mcif", parent, mag.data)
        self.assertTrue(physical["symmetry_validated"])
        np.testing.assert_allclose(physical["moment_norms"], [1, 1, 0])
        self.assertIn("_space_group_symop_magn_operation.xyz",
                      (self.directory / "synthetic.mcif").read_text())

    def test_higher_symmetry_hstrain_not_copied_to_p1(self):
        from magnetic_analysis.workflow import _insert_magnetic, _expanded_parent
        sc, _, _, _, _ = _modules()
        self.cif.write_text(self.cif.read_text().replace("'P 1'", "'P 2/m'"))
        project = sc.G2Project(newgpx=str(self.directory / "higher_symmetry.gpx"))
        hist = project.add_simulated_powder_histogram(
            "synthetic", str(self.input / "instruments" / "bank_a.instprm"),
            1, 8, Npoints=100)
        nuclear = project.add_phase(str(self.cif), phasename="nuclear", histograms=[hist])
        atoms, codes, _, positions, _ = _expanded_parent(nuclear.data)
        ct = nuclear.data["General"]["AtomPtrs"][1]
        selected = [i for i, a in enumerate(nuclear.data["Atoms"]) if a[ct] == "Fe"]
        moments = [[0, 0, 1] if a[ct] == "Fe" else [0, 0, 0] for a in atoms]
        candidate = {"selected_parent_indices": selected,
                     "expanded_positions": positions.tolist(),
                     "expanded_types": [a[ct] for a in atoms]}
        mag = _insert_magnetic(project, nuclear.data, candidate,
                               {"id": "higher", "moment_crystalaxis": moments})
        self.assertEqual(mag.data["General"]["MagDmin"], 0.0)
        self.assertEqual(len(mag.data["Histograms"][hist.name]["HStrain"][0]), 6)
        self.assertLess(len(nuclear.data["Histograms"][hist.name]["HStrain"][0]), 6)
        project.set_Controls("cycles", 0)
        project.refine()

    def test_anisotropic_orbit_codes_and_coupled_adps(self):
        from magnetic_analysis.workflow import _insert_magnetic, _expanded_parent
        sc, lat, _, _, _ = _modules()
        self.cif.write_text(self.cif.read_text().replace("'P 1'", "'P 2/m'"))
        project = sc.G2Project(newgpx=str(self.directory / "anisotropic.gpx"))
        hist = project.add_simulated_powder_histogram(
            "synthetic", str(self.input / "instruments" / "bank_a.instprm"),
            1, 8, Npoints=100)
        nuclear = project.add_phase(str(self.cif), phasename="nuclear", histograms=[hist])
        cia = nuclear.data["General"]["AtomPtrs"][3]
        for atom in nuclear.data["Atoms"]:
            atom[cia] = "A"
            atom[cia + 2:cia + 8] = [0.01, 0.01, 0.01, 0, 0, 0]
        atoms, codes, vectors, positions, _ = _expanded_parent(nuclear.data)
        self.assertTrue(all(":[" not in code for code in codes))
        ct = nuclear.data["General"]["AtomPtrs"][1]
        selected = [i for i, a in enumerate(nuclear.data["Atoms"]) if a[ct] == "Fe"]
        candidate = {"selected_parent_indices": selected, "expanded_atom_codes": codes,
                     "cell_vectors": vectors.tolist(), "expanded_positions": positions.tolist(),
                     "expanded_types": [a[ct] for a in atoms]}
        mode = {"id": "anisotropic", "moment_crystalaxis":
                [[0, 0, 1] if a[ct] == "Fe" else [0, 0, 0] for a in atoms]}
        mag = _insert_magnetic(project, nuclear.data, candidate, mode)
        nuclear.set_refinements({"Atoms": {"all": "U"}})
        for atom in mag.data["Atoms"]:
            atom[mag.data["General"]["AtomPtrs"][1] + 1] = "MU"
        project.set_Controls("cycles", 0)
        project.refine()
        physical = _symmetry_mcif(self.directory / "anisotropic.mcif", nuclear.data, mag.data)
        self.assertTrue(physical["symmetry_validated"])
        self.assertIn("_atom_site_aniso_U_11", (self.directory / "anisotropic.mcif").read_text())

    def test_explicit_unknown_stage(self):
        with self.assertRaises(ValueError):
            analyze(self.directory, self.directory, stage="guess")

    def test_nonorthogonal_magnetic_polarization_and_derivatives(self):
        from unittest.mock import patch
        _, lat, spc, _, _ = _modules()
        import GSASIIstrMath as sm
        _, sg = spc.SpcGroup("P 1")
        spc.SetMagnetic(sg)
        sg["MagMom"] = np.ones(1)
        metric, _ = lat.A2Gmat(lat.cell2A([4, 4, 6, 90, 90, 120]))
        refs = np.zeros((1, 14))
        refs[0, :3], refs[0, 4] = [0, 0, 1], 6
        reflection = {"RefList": refs, "FF": {}}
        parameters = {"0::AMx:0": 1., "0::AMy:0": 0., "0::AMz:0": 1.}
        def atoms(pfx, controls, parms):
            moment = np.array([[parms[f"0::AM{axis}:0"]] for axis in "xyz"])
            return (["Fe"], np.ones(1), np.ones(1), np.zeros((3, 1)),
                    np.zeros((3, 1)), np.zeros(1), np.zeros(1), np.zeros((6, 1)), moment)
        controls = {"MFtables": {"Fe": {}}}
        with patch.object(sm, "GetAtomFXU", side_effect=atoms), patch.object(
                sm.G2el, "MagScatFac", side_effect=lambda table, sq: np.ones_like(sq)):
            sm.MagStructureFactor2(reflection, metric, "0:", "0::", sg, controls, parameters)
            derivatives = sm.MagStructureFactorDerv2(
                reflection, metric, "0:", "0::", sg, controls, parameters)
        # With Q along c only the unit-a moment contributes to polarization.
        constant = (0.539 / 2) ** 2
        self.assertAlmostEqual(reflection["RefList"][0, 9], constant, places=10)
        for axis, expected in zip("xyz", [2 * constant, -constant, 0]):
            self.assertAlmostEqual(derivatives[f"0::AM{axis}:0"][0], expected, places=7)

    def test_absolute_two_site_magnetic_and_nuclear_structure_factors(self):
        from unittest.mock import patch
        _, lat, spc, _, _ = _modules()
        import GSASIIstrMath as sm
        from magnetic_analysis.workflow import _magnetic_dataset
        for cell in ([4, 5, 6, 90, 90, 90], [4, 4, 6, 90, 90, 120]):
            metric, _ = lat.A2Gmat(lat.cell2A(cell))
            _, p1 = spc.SpcGroup("P 1")
            spc.SetMagnetic(p1)
            p1["MagMom"] = np.ones(1)
            centered = dict(p1, SGFixed=True, SGOps=[
                (np.eye(3), np.zeros(3)), (np.eye(3), np.array([0, 0, .5]))],
                MagMom=np.array([1, -1]))
            factors = []
            dataset, _ = _magnetic_dataset(
                lat.cell2AB(cell)[0].T, np.array([[0, 0, 0], [0, 0, .5]]),
                [1, 1], np.array([[2, 0, 0], [-2, 0, 0]]))
            stabilizer = dict(p1, SGFixed=True,
                SGOps=list(zip(dataset.rotations, dataset.translations)),
                MagMom=np.linalg.det(dataset.rotations) * np.where(dataset.time_reversals, -1, 1))
            for symmetry, positions, multiplicities, moment in (
                    (p1, np.array([[0, 0, 0], [0, 0, .5]]), [1, 1], [[2, 0, 0], [-2, 0, 0]]),
                    (centered, np.array([[0, 0, 0]]), [2], [[2, 0, 0]]),
                    (stabilizer, np.array([[0, 0, 0]]), [2], [[2, 0, 0]])):
                n = len(positions)
                atoms = (["Fe"] * n, np.array(multiplicities), np.ones(n),
                         positions.T, np.zeros((3, n)), np.zeros(n), np.zeros(n),
                         np.zeros((6, n)), np.array(moment).T)
                refs = np.zeros((3, 15))
                refs[:, :3] = [[0, 0, 1], [0, 0, 2], [1, 0, 1]]
                refs[:, 4] = [1 / np.sqrt(h @ metric @ h) for h in refs[:, :3]]
                reflections = {"RefList": refs, "FF": {}}
                controls = {"MFtables": {"Fe": {}}, "histType": "PNC", "0:histType": "PNC",
                            "FFtables": {}, "EFtables": {}, "BLtables": {}, "ORBtables": {}}
                with patch.object(sm, "GetAtomFXU", return_value=atoms), patch.object(
                        sm.G2el, "MagScatFac", side_effect=lambda table, sq: np.ones_like(sq)):
                    sm.MagStructureFactor2(reflections, metric, "0:", "0::",
                                          symmetry, controls, {})
                factors.append(reflections["RefList"][:, 9].copy())
            basis, reciprocal = lat.cell2AB(cell)
            unit = basis / np.linalg.norm(basis, axis=0)
            cart = unit @ np.array([2., 0, 0])
            expected = []
            for hkl in [[0, 0, 1], [0, 0, 2], [1, 0, 1]]:
                q = reciprocal.T @ hkl
                q /= np.linalg.norm(q)
                vector = (cart - q * np.dot(q, cart)) * (1 - np.exp(1j * np.pi * hkl[2]))
                expected.append(.2695**2 * np.vdot(vector, vector).real)
            np.testing.assert_allclose(factors[0], expected, atol=1e-10)
            np.testing.assert_allclose(factors[1], expected, atol=1e-10)
            np.testing.assert_allclose(factors[2], expected, atol=1e-10)
            # Nuclear b in GSAS is in 10^-12 cm: Fe coherent b=0.945,
            # magnetic 2.695 fm=0.2695. No additional powder 1/4 factor.
            atoms = (["Fe", "Fe"], np.ones(2), np.ones(2),
                     np.array([[0, 0, 0], [0, 0, .5]]).T, np.zeros((3, 2)),
                     np.zeros(2), np.zeros(2), np.zeros((6, 2)), np.zeros((3, 2)))
            controls["0:histType"] = "PNC"
            refs = np.zeros((3, 15)); refs[:, :3] = [[0, 0, 1], [0, 0, 2], [1, 0, 1]]
            refs[:, 4] = [1 / np.sqrt(h @ metric @ h) for h in refs[:, :3]]
            reflections = {"RefList": refs, "FF": {}}
            with patch.object(sm, "GetAtomFXU", return_value=atoms), patch.object(
                    sm.G2el, "getBLvalues", return_value={"Fe": .945}), patch.object(
                    sm.G2el, "BlenResCW", return_value=(np.zeros(2), np.zeros(2))):
                sm.StructureFactor2(reflections, metric, "0:", "0::", p1, controls,
                                    {"0:Lam": 1., "00:BabA": 0., "00:BabU": 0.})
            np.testing.assert_allclose(reflections["RefList"][:, 9],
                                       [0, (2 * .945)**2, 0], atol=1e-10)

    def test_bounded_positive_profile_uses_fresh_zero_cycle_calculations(self):
        from magnetic_analysis.workflow import _bounded_profile_fit
        sc, _, _, _, _ = _modules()
        project = sc.G2Project(newgpx=str(self.directory / "profile.gpx"))
        hist = project.add_simulated_powder_histogram(
            "synthetic", str(self.input / "instruments" / "bank_a.instprm"),
            1, 8, Npoints=600, scale=10000)
        project.add_phase(str(self.cif), phasename="nuclear", histograms=[hist])
        project.set_Controls("cycles", 0)
        project.refine()
        hist = project.histograms()[0]
        hist.data["data"][1][1] = np.asarray(hist.getdata("ycalc")).copy()
        hist.data["data"][1][2] = np.ones(len(hist.getdata("x")))
        hist.data["Instrument Parameters"][0]["alpha"][1] *= 1.2
        quality = _bounded_profile_fit(project, 12)
        self.assertLess(quality["weighted_residual_sum"] / quality["points"], 1e-4)
        self.assertAlmostEqual(project.histograms()[0].data[
            "Instrument Parameters"][0]["alpha"][1], 1., delta=.02)
        self.assertTrue(quality["bounded_profile_fits"])

    def test_hexagonal_powder_equivalent_basal_directions_have_equal_intensity(self):
        from unittest.mock import patch
        _, lat, spc, _, _ = _modules()
        import GSASIIstrMath as sm
        cell = [4, 4, 6, 90, 90, 120]
        metric, _ = lat.A2Gmat(lat.cell2A(cell))
        vectors = lat.cell2AB(cell)[0].T
        _, sg = spc.SpcGroup("P 1")
        spc.SetMagnetic(sg); sg["MagMom"] = np.ones(1)
        hkls = np.array([[1, 0, 1], [0, 1, 1], [-1, 1, 1],
                         [-1, 0, 1], [0, -1, 1], [1, -1, 1]])
        totals = []
        for cartesian in ([2, 0, 0], [0, 2, 0], [np.sqrt(2), np.sqrt(2), 0]):
            moment = moment_to_crystalaxis(cartesian, vectors)
            atoms = (["Fe", "Fe"], np.ones(2), np.ones(2),
                     np.array([[0, 0, 0], [0, 0, .5]]).T, np.zeros((3, 2)),
                     np.zeros(2), np.zeros(2), np.zeros((6, 2)), np.array([moment, -moment]).T)
            refs = np.zeros((6, 15)); refs[:, :3] = hkls
            refs[:, 4] = [1 / np.sqrt(h @ metric @ h) for h in hkls]
            reflections = {"RefList": refs, "FF": {}}
            with patch.object(sm, "GetAtomFXU", return_value=atoms), patch.object(
                    sm.G2el, "MagScatFac", side_effect=lambda table, sq: np.ones_like(sq)):
                sm.MagStructureFactor2(reflections, metric, "0:", "0::",
                                      sg, {"MFtables": {"Fe": {}}}, {})
            totals.append(float(sum(reflections["RefList"][:, 9])))
        np.testing.assert_allclose(totals, totals[0], atol=1e-10)

    def test_gsas_post_update_adp_boundary_is_recomputed(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from magnetic_analysis.workflow import _refine
        _modules()
        atom = ["I", 0.01]
        phase = SimpleNamespace(name="nuclear", data={
            "pId": 0, "General": {"AtomPtrs": [0, 0, 0, 0]}, "Atoms": [atom]})
        project = SimpleNamespace(data={"Controls": {"data": {}}},
                                  set_Controls=lambda *a, **k: None,
                                  set_Frozen=lambda *a, **k: None,
                                  phases=lambda: [phase], save=lambda: None)
        calls = []
        def refine():
            calls.append(atom[1])
            if len(calls) == 1:
                atom[1] = -0.01
        project.refine = refine
        with patch("magnetic_analysis.workflow._quality", return_value={
                "converged": True, "points": 100, "weighted_residual_sum": 1.0}):
            quality = _refine(project, 2)
        self.assertEqual(calls, [0.01, 0.0])
        self.assertEqual(quality["refinement_passes"], 2)
        self.assertEqual(quality["active_adp_boundaries"], ["nuclear:atom0:ADP1"])

    def test_scriptable_solver_failure_cannot_reuse_old_convergence(self):
        from types import SimpleNamespace
        from magnetic_analysis.workflow import _refine
        _modules()
        project = SimpleNamespace(
            data={"Controls": {"data": {}},
                  "Covariance": {"data": {"Rvals": {"converged": True}}}},
            set_Controls=lambda *a, **k: None, refine=lambda: None,
            phases=lambda: [])
        with self.assertRaisesRegex(RuntimeError, "fresh refinement result"):
            _refine(project, 2)

    def test_conditional_model_change_releases_only_adp_bounds(self):
        from magnetic_analysis.workflow import _release_adp_boundaries
        from unittest.mock import patch
        frozen = ["0::AUiso:0", "1::AU11:1", "0:2:Scale", "0::dAx:0"]
        project = SimpleNamespace(
            data={"Controls": {"data": {"parmFrozen": {"FrozenList": frozen}}}},
            phases=lambda: [SimpleNamespace(name="nuclear")])
        with patch("magnetic_analysis.workflow._physical_limits") as limits:
            released = _release_adp_boundaries(project)
        self.assertEqual(released, ["0::AUiso:0", "1::AU11:1"])
        self.assertEqual(frozen, ["0:2:Scale", "0::dAx:0"])
        limits.assert_called_once()

    def test_conditional_cell_metric_sync(self):
        from magnetic_analysis.workflow import _conditional_cell_fit
        from unittest.mock import patch
        _, lat, spc, _, _ = _modules()
        sg = spc.SpcGroup("P 1")[1]
        phase = SimpleNamespace(data={"General": {
            "SGData": sg, "Cell": [False, 4., 5., 6., 90., 90., 90., 120.]},
            "Histograms": {"bank": {"Scale": [2., False]}}})
        magnetic = SimpleNamespace(data={"General": {
            "Cell": [False, 4., 5., 6., 90., 90., 90., 120.]},
            "Histograms": {"bank": {"Scale": [2., False]}}})
        x = np.linspace(1., 10., 200)
        peak = np.exp(-((x - 5.) / .3)**2)
        arrays = {"x": x, "yobs": 2. * peak + 1., "yweight": np.ones(200),
                  "ycalc": np.ma.array(peak, mask=np.zeros(200, dtype=bool))}
        hist = SimpleNamespace(name="bank", getdata=lambda key: arrays[key],
            data={"Limits": [None, [1., 10.]], "Background": [[None, False, 1, 0.]]})
        project = SimpleNamespace(
            phase=lambda name: phase, phases=lambda: [phase, magnetic],
            histograms=lambda: [hist], histogram=lambda name: hist,
            set_Controls=lambda *a: None, refine=lambda: None)
        diagnostic = _conditional_cell_fit(project)
        self.assertEqual(diagnostic["parameter_count"], 6)
        np.testing.assert_allclose(phase.data["General"]["Cell"],
                                   magnetic.data["General"]["Cell"])
        self.assertAlmostEqual(phase.data["Histograms"]["bank"]["Scale"][0], 2.)
        self.assertAlmostEqual(hist.data["Background"][0][3], 1.)

    def test_zero_boundary_seed_uses_measured_resolution_not_tiny_moment(self):
        from types import SimpleNamespace
        from magnetic_analysis.workflow import _seed_moment_from_profile
        _modules()
        atom = [0., 0., 0., 0., 0., 0., 1.]
        magnetic = SimpleNamespace(data={
            "General": {"AtomPtrs": [0, 0, 0, 0]}, "Atoms": [atom]})
        values = {"x": np.arange(4.), "yobs": np.full(4, 5.),
                  "yweight": np.ones(4), "ycalc": np.full(4, 15.)}
        hist = SimpleNamespace(data={"Limits": [(0, 3), [0, 3]]},
                               getdata=lambda key: values[key])
        def refine():
            values["ycalc"] = np.full(4, 5 + 10 * atom[6]**2)
        project = SimpleNamespace(set_Controls=lambda *a: None,
                                  refine=refine, histograms=lambda: [hist])
        seed = _seed_moment_from_profile(project, magnetic)
        self.assertTrue(seed["positive_boundary"])
        self.assertAlmostEqual(seed["amplitude_squared_resolution"], .05)
        self.assertAlmostEqual(seed["amplitude"], np.sqrt(.05))

    def test_zero_moment_quadratic_fit_is_stable(self):
        from magnetic_analysis.workflow import _insert_magnetic, _expanded_parent, _quadratic_moment_fit
        sc, _, _, _, _ = _modules()
        project = sc.G2Project(newgpx=str(self.directory / "zero.gpx"))
        hist = project.add_simulated_powder_histogram(
            "synthetic", str(self.input / "instruments" / "bank_a.instprm"),
            1, 8, Npoints=400, scale=10000)
        nuclear = project.add_phase(str(self.cif), phasename="nuclear", histograms=[hist])
        _, codes, vectors, positions, _ = _expanded_parent(nuclear.data)
        candidate = {"selected_parent_indices": [0, 1], "expanded_atom_codes": codes,
                     "expanded_positions": positions.tolist(), "cell_vectors": vectors.tolist(),
                     "expanded_types": ["Fe", "Fe", "O"]}
        project.set_Controls("cycles", 0); project.refine()
        hist = project.histograms()[0]
        observed = np.asarray(hist.getdata("ycalc")).copy()
        hist.data["data"][1][1] = observed
        hist.data["data"][1][2] = np.ones(len(observed))
        mode = {"id": "parallel", "moment_crystalaxis": [[0, 0, 1], [0, 0, 1], [0, 0, 0]],
                "direction_basis_cartesian": [[0], [0], [1]]}
        magnetic = _insert_magnetic(project, nuclear.data, candidate, mode)
        result = _quadratic_moment_fit(project, magnetic, mode, vectors)
        self.assertTrue(result["zero_boundary"])
        np.testing.assert_allclose(result["moment_cartesian"], 0)
        self.assertLess(result["weighted_residual_sum"], 1e-6)
        repeated = _quadratic_moment_fit(project, project.phase(mode["id"]), mode, vectors)
        self.assertTrue(repeated["zero_boundary"])
        physical = _symmetry_mcif(self.directory / "zero.mcif",
                                  project.phase("nuclear").data, project.phase(mode["id"]).data)
        np.testing.assert_allclose(physical["moment_norms"], 0)

    def test_antiparallel_histogram_and_moment_representation_invariance(self):
        import copy
        from magnetic_analysis.workflow import _insert_magnetic, _expanded_parent
        sc, _, spc, elem, _ = _modules()
        for angle in (90, 120):
            self.cif.write_text(self.cif.read_text().split("loop_\n_atom_site_label")[0]
                .replace("'P 1'", "'P -1'")
                .replace("_cell_length_b 5", "_cell_length_b 4")
                .replace("_cell_angle_gamma 90", f"_cell_angle_gamma {angle}") +
                "loop_\n_atom_site_label\n_atom_site_type_symbol\n_atom_site_fract_x\n"
                "_atom_site_fract_y\n_atom_site_fract_z\n_atom_site_occupancy\n_atom_site_U_iso_or_equiv\n"
                "Fe1 Fe .1 .2 .25 1 .01\nO1 O .2 .3 .17 1 .01\n")
            project = sc.G2Project(newgpx=str(self.directory / f"invariant_{angle}.gpx"))
            hist = project.add_simulated_powder_histogram(
                "synthetic", str(self.input / "instruments" / "bank_a.instprm"),
                1, 8, Npoints=400, scale=10000)
            nuclear = project.add_phase(str(self.cif), phasename="nuclear", histograms=[hist])
            atoms, codes, vectors, positions, _ = _expanded_parent(nuclear.data)
            ct = nuclear.data["General"]["AtomPtrs"][1]
            types = [a[ct] for a in atoms]
            moment = [[0, 0, 1 if ":1" in code else -1] if typ == "Fe" else [0, 0, 0]
                      for code, typ in zip(codes, types)]
            candidate = {"selected_parent_indices": [0], "expanded_atom_codes": codes,
                         "expanded_positions": positions.tolist(), "cell_vectors": vectors.tolist(),
                         "expanded_types": types}
            mode = {"id": "invariant", "moment_crystalaxis": moment}
            magnetic = _insert_magnetic(project, nuclear.data, candidate, mode)
            project.data["Constraints"]["phase"] = []
            project.data["Constraints"]["HAP"] = []
            for phase in project.phases():
                phase.set_HAP_refinements({"Scale": False})
                phase.set_refinements({"Atoms": {"all": " "}})
            cx, _, cs, _ = magnetic.data["General"]["AtomPtrs"]
            for atom in magnetic.data["Atoms"]:
                atom[cx + 4:cx + 7] = (np.asarray(atom[cx + 4:cx + 7]) * 2.3).tolist()
            project.set_Controls("cycles", 0); project.refine()
            full = np.asarray(project.histograms()[0].getdata("ycalc")).copy()
            # Expand the nuclear inversion orbit too, without changing cell or
            # histogram scale. Both phases must remain absolute equivalents.
            nuclear = project.phase("nuclear")
            expanded_nuclear, _, _, _, _ = _expanded_parent(nuclear.data)
            nuclear.data["Atoms"] = expanded_nuclear
            nuclear.data["General"]["SGData"] = spc.SpcGroup("P 1")[1]
            ncs = nuclear.data["General"]["AtomPtrs"][2]
            for atom in nuclear.data["Atoms"]:
                atom[ncs:ncs + 2] = ["1", 1]
            elem.SetupGeneral(nuclear.data, None)
            project.refine()
            np.testing.assert_allclose(project.histograms()[0].getdata("ycalc"),
                                       full, atol=1e-7, rtol=1e-8)
            # Same cell and full nuclear phase, compress only magnetic inversion
            # orbit (axial inversion + time reversal gives opposite vectors).
            magnetic = project.phase("invariant")
            magnetic.data["Atoms"] = [copy.deepcopy(magnetic.data["Atoms"][0])]
            magnetic.data["Atoms"][0][cs + 1] = 2
            magnetic.data["General"]["SGData"].update(
                SGFixed=True, SGInv=False, SGOps=[(np.eye(3), np.zeros(3)),
                (-np.eye(3), np.zeros(3))], MagMom=np.ones(2), SpnFlp=[1, -1])
            elem.SetupGeneral(magnetic.data, None)
            project.refine()
            compressed = np.asarray(project.histograms()[0].getdata("ycalc")).copy()
            np.testing.assert_allclose(compressed, full, atol=1e-7, rtol=1e-8)
            # Nuclear subtraction lets the identical histogram determine the
            # same amplitude independent of representation and HAP scale.
            magnetic.data["Histograms"][project.histograms()[0].name]["Use"] = False
            project.refine()
            baseline = np.asarray(project.histograms()[0].getdata("ycalc")).copy()
            magnetic.data["Histograms"][project.histograms()[0].name]["Use"] = True
            magnetic.data["Atoms"][0][cx + 4:cx + 7] = (
                np.asarray(magnetic.data["Atoms"][0][cx + 4:cx + 7]) / 2.3).tolist()
            project.refine()
            unit = np.asarray(project.histograms()[0].getdata("ycalc")) - baseline
            amplitude = np.sqrt(np.dot(unit, full - baseline) / np.dot(unit, unit))
            self.assertAlmostEqual(amplitude, 2.3, places=7)

    def test_real_gsas_profile_refinement(self):
        """Generate independent nuclear/magnetic TOF profiles then fit amplitude."""
        import json
        from magnetic_analysis.workflow import _insert_magnetic, _expanded_parent, refine_local_candidates as refine_candidates
        sc, _, _, _, _ = _modules()
        project = sc.G2Project(newgpx=str(self.directory / "generated.gpx"))
        instrument_file = self.input / "instruments" / "bank_a.instprm"
        instrument_file.write_text(instrument_file.read_text().replace("sig-1:0.0", "sig-1:5.0"))
        hist = project.add_simulated_powder_histogram(
            "synthetic", str(self.input / "instruments" / "bank_a.instprm"),
            1, 9, Npoints=1800, scale=100000)
        nuclear = project.add_phase(str(self.cif), phasename="nuclear", histograms=[hist])
        _, codes, vectors, positions, _ = _expanded_parent(nuclear.data)
        candidate = {"selected_parent_indices": [0, 1],
                     "expanded_atom_codes": codes,
                     "expanded_positions": positions.tolist(), "cell_vectors": vectors.tolist(),
                     "expanded_types": ["Fe", "Fe", "O"],
                     "diagnostics": {"limitations": "Synthetic single-mode test"}}
        mode = {"id": "synthetic", "moment_crystalaxis": [[0, 0, 1], [0, 0, -1], [0, 0, 0]]}
        project.set_Controls("cycles", 0)
        project.refine()
        x = np.asarray(hist.getdata("x"))
        high = np.asarray(hist.getdata("ycalc")).copy()
        # Independent low-temperature sample broadening must not be mistaken
        # for a changed ordered moment or held to the high-T profile.
        inst = hist.data["Instrument Parameters"][0]
        inst["sig-1"][1] *= 2.5
        low_sig1 = inst["sig-1"][1]
        magnetic = _insert_magnetic(project, nuclear.data, candidate, mode)
        mx = magnetic.data["General"]["AtomPtrs"][0]
        for atom in magnetic.data["Atoms"]:
            atom[mx + 4:mx + 7] = (np.array(atom[mx + 4:mx + 7]) * 2.3).tolist()
            atom[2] = ""
        nuclear.set_HAP_refinements({"Scale": False})
        magnetic.set_HAP_refinements({"Scale": False})
        project.refine()
        low = np.asarray(hist.getdata("ycalc")).copy()
        self.assertGreater(float(np.max(low - high)), 0)
        for temp, intensity in (("high_temperature", high), ("low_temperature", low)):
            # Deterministic synthetic counts; explicit errors supplied.
            np.savetxt(self.input / temp / "bank_a.dat",
                       np.column_stack((x, intensity, np.sqrt(np.maximum(intensity, 1)))))
        candidate["inputs"] = discover_inputs(self.directory)
        candidate["modes"] = [mode]
        (self.directory / "candidates_report.json").write_text(json.dumps(candidate))
        result = refine_candidates(self.directory, self.directory, cycles=20,
                                   refine_cell=False, background_coeffs=3)
        report = json.loads(Path(result["report_path"]).read_text())
        self.assertTrue(report["complete"])
        baseline = json.loads(Path(report["baseline_report_path"]).read_text())
        self.assertEqual(baseline["low_temperature_profile_control"]["added_parameter_count"], 4)
        physical = report["models"][0]["physical"]
        self.assertAlmostEqual(physical["moment_norms"][0], 2.3, delta=0.3)
        self.assertTrue(Path(result["mcif_path"]).is_file())
        self.assertIn("scientific_status", result)
        self.assertIn("fit_quality", result)
        self.assertIn("warning", result)
        self.assertEqual(result["failed_models"], 0)
        self.assertTrue(result["scientific_status"]["comparison_complete"])
        profile_fit = report["models"][0]["low_temperature_profile_fit"]
        self.assertEqual(profile_fit["added_parameter_count"], 4)
        final_profile = profile_fit["steps"][-1]["profile"]["bounded_profile_fits"][0]
        self.assertAlmostEqual(final_profile["profile_parameters"]["sig-1"],
                               low_sig1, delta=.15 * low_sig1)
        initialization = report["models"][0]["nuclear_scale_initialization"][0]
        self.assertGreaterEqual(initialization["selected_points"], 20)
        self.assertGreater(initialization["scale_correction"], 0)
        self.assertLess(initialization["scale_correction"], 1.0)


if __name__ == "__main__":
    unittest.main()
