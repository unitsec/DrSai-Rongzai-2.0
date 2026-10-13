"""Offline regression tests; standard library unittest, no pytest required."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

import benchmark as b


def write_explicit(path: Path, structure: b.Structure) -> None:
    lengths = np.linalg.norm(structure.lattice, axis=1)
    axes = structure.lattice / lengths[:, None]
    cosine = axes @ axes.T
    angles = np.rad2deg(np.arccos(np.clip([cosine[1, 2], cosine[0, 2], cosine[0, 1]], -1, 1)))
    cell = [*lengths, *angles]
    canonical = b.G2lat.cell2AB(cell)[0].T
    rotation, _ = b.orthogonal_procrustes(structure.lattice, canonical)
    cart_moments = structure.moments @ rotation * np.linalg.det(rotation)
    moment_components = cart_moments @ np.linalg.inv(canonical / lengths[:, None])
    lines = ["#\\#CIF_2.0", "data_submission"]
    for name, value in zip(
        ("length_a", "length_b", "length_c", "angle_alpha", "angle_beta", "angle_gamma"), cell
    ):
        lines.append(f"_cell_{name} {value:.12g}")
    lines += [
        "loop_", "_space_group_symop_magn_operation.xyz", "'x,y,z,+1'",
        "loop_", "_atom_site_label", "_atom_site_type_symbol",
        "_atom_site_fract_x", "_atom_site_fract_y", "_atom_site_fract_z",
    ]
    for i, (species, position) in enumerate(zip(structure.species, structure.positions)):
        lines.append(f"S{i} {species} " + " ".join(f"{v:.12g}" for v in position))
    lines += [
        "loop_", "_atom_site_moment.label", "_atom_site_moment.crystalaxis_x",
        "_atom_site_moment.crystalaxis_y", "_atom_site_moment.crystalaxis_z",
    ]
    for i, moment in enumerate(moment_components):
        lines.append(f"S{i} " + " ".join(f"{v:.12g}" for v in moment))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.reference = b.read_structure(b.SOURCE / "MnTe_最终磁结构.mcif")
        # `protocol.json` is the active protocol (v2: magnitudes reported, not scored);
        # `protocol_v1.json` is frozen so v1 semantics stay reproducible.
        cls.rules = json.loads((b.HERE / "protocol.json").read_text())
        cls.rules_v1 = json.loads((b.HERE / "protocol_v1.json").read_text())

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def check(self, candidate: b.Structure, expected: bool = True, rules: dict | None = None) -> dict:
        path = self.folder / "candidate.mcif"
        write_explicit(path, candidate)
        verdict = b.compare(self.reference, b.read_structure(path), rules or self.rules)
        self.assertEqual(verdict["passed"], expected, verdict)
        return verdict

    def test_reference_and_crystalaxis_conversion(self) -> None:
        self.assertTrue(b.compare(self.reference, self.reference, self.rules)["passed"])
        np.testing.assert_allclose(np.linalg.norm(self.reference.moments, axis=1), [2, 0, 0, 2])

    def test_explicit_full_cell_without_k_metadata(self) -> None:
        self.check(copy.deepcopy(self.reference))

    def test_global_reversal(self) -> None:
        candidate = copy.deepcopy(self.reference)
        candidate.moments *= -1
        self.check(candidate)

    def test_origin_shift_and_atom_permutation(self) -> None:
        candidate = copy.deepcopy(self.reference)
        candidate.positions = (candidate.positions + [0.17, 0.23, 0.31]) % 1
        order = [3, 1, 0, 2]
        candidate.positions = candidate.positions[order]
        candidate.moments = candidate.moments[order]
        candidate.species = [candidate.species[i] for i in order]
        self.check(candidate)

    def test_unimodular_changes_including_large_shear(self) -> None:
        transforms = [
            [[0, 1, 0], [1, 0, 0], [0, 0, 1]],
            [[1, 1, 0], [0, 1, 0], [0, 0, 1]],
            [[1, 4, 0], [0, 1, 0], [0, 0, 1]],
            [[-1, 0, 0], [0, 1, 0], [0, 0, 1]],
        ]
        for transform in transforms:
            with self.subTest(transform=transform):
                candidate = copy.deepcopy(self.reference)
                candidate.lattice = np.array(transform) @ candidate.lattice
                candidate.positions = (candidate.positions @ np.linalg.inv(transform)) % 1
                self.check(candidate)

    def test_centered_orthorhombic_bns_cell(self) -> None:
        transform = np.array([[1, 1, 0], [-1, 1, 0], [0, 0, 1]])
        candidate = copy.deepcopy(self.reference)
        candidate.lattice = transform @ candidate.lattice
        positions = self.reference.positions @ np.linalg.inv(transform)
        candidate.positions = np.vstack((positions, positions + [0.5, 0.5, 0])) % 1
        candidate.species *= 2
        candidate.moments = np.vstack((candidate.moments, candidate.moments))
        self.check(candidate)

    def test_supercell(self) -> None:
        candidate = copy.deepcopy(self.reference)
        candidate.lattice[2] *= 2
        positions = candidate.positions.copy()
        positions[:, 2] /= 2
        candidate.positions = np.vstack((positions, positions + [0, 0, 0.5]))
        candidate.species *= 2
        candidate.moments = np.vstack((candidate.moments, candidate.moments))
        self.check(candidate)

    def test_extra_magnetic_period_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.reference)
        candidate.lattice[2] *= 2
        positions = candidate.positions.copy()
        positions[:, 2] /= 2
        candidate.positions = np.vstack((positions, positions + [0, 0, 0.5]))
        candidate.species *= 2
        candidate.moments = np.vstack((candidate.moments, -candidate.moments))
        path = self.folder / "nonzero_k.mcif"
        write_explicit(path, candidate)
        with self.assertRaisesRegex(b.InvalidStructure, "not k=0"):
            b.compare(self.reference, b.read_structure(path), self.rules)

    def test_parent_symmetry_domain(self) -> None:
        candidate = copy.deepcopy(self.reference)
        angle = np.deg2rad(120)
        rotation = np.array([[np.cos(angle), np.sin(angle), 0],
                             [-np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
        candidate.moments = candidate.moments @ rotation
        self.check(candidate)

    def test_arbitrary_in_plane_rotation_is_not_a_domain(self) -> None:
        candidate = copy.deepcopy(self.reference)
        angle = np.deg2rad(15)
        rotation = np.array([[np.cos(angle), np.sin(angle), 0],
                             [-np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
        candidate.moments = candidate.moments @ rotation
        self.check(candidate, False)

    def test_ferromagnetic_and_out_of_plane_fail(self) -> None:
        candidate = copy.deepcopy(self.reference)
        candidate.moments[3] = candidate.moments[0]
        self.check(candidate, False)
        candidate.moments[0] = [0, 0, 2]
        candidate.moments[3] = [0, 0, -2]
        self.check(candidate, False)

    def test_moment_five_percent_boundary(self) -> None:
        """v1 semantics: the reference magnitude itself gates the verdict."""
        for scale, expected in [(0.95, True), (1.05, True), (0.949, False), (1.051, False)]:
            with self.subTest(scale=scale):
                candidate = copy.deepcopy(self.reference)
                candidate.moments *= scale
                self.check(candidate, expected, rules=self.rules_v1)

    def test_unverified_magnitude_reported_but_not_scored(self) -> None:
        """v2 semantics: a uniformly rescaled structure keeps its geometry verdict.

        The reference magnitude is not experimentally confirmed (see README), so scoring
        it would fail a physically correct fit for reproducing a placeholder.
        """
        candidate = copy.deepcopy(self.reference)
        candidate.moments *= 2.2
        path = self.folder / "scaled.mcif"
        write_explicit(path, candidate)
        parsed = b.read_structure(path)
        v2 = b.compare(self.reference, parsed, self.rules)
        self.assertTrue(v2["passed"], v2)
        self.assertFalse(v2["magnitude_scored"], v2)
        self.assertFalse(v2["magnitude_within_tolerance"], v2)
        self.assertAlmostEqual(v2["moment_scale_ratio"], 2.2, delta=0.05)
        v1 = b.compare(self.reference, parsed, self.rules_v1)
        self.assertFalse(v1["passed"], v1)
        self.assertIn("equivalent magnetic moments", v1["reason"])

    def test_magnitude_agreement_is_reported_when_it_holds(self) -> None:
        verdict = self.check(copy.deepcopy(self.reference))
        self.assertFalse(verdict["magnitude_scored"], verdict)
        self.assertTrue(verdict["magnitude_within_tolerance"], verdict)
        self.assertAlmostEqual(verdict["moment_scale_ratio"], 1.0, delta=1e-6)

    def test_moment_presence_is_still_scored(self) -> None:
        """A moment that vanishes or appears is a structural difference in either protocol."""
        for index in (0, 3):
            with self.subTest(index=index):
                candidate = copy.deepcopy(self.reference)
                candidate.moments[index] = 0
                path = self.folder / f"missing-moment-{index}.mcif"
                write_explicit(path, candidate)
                for rules in (self.rules, self.rules_v1):
                    verdict = b.compare(self.reference, b.read_structure(path), rules)
                    self.assertFalse(verdict["passed"], verdict)

    def test_global_reversal_knob_is_honored(self) -> None:
        candidate = copy.deepcopy(self.reference)
        candidate.moments *= -1
        path = self.folder / "reversed.mcif"
        write_explicit(path, candidate)
        parsed = b.read_structure(path)
        self.assertTrue(b.compare(self.reference, parsed, self.rules)["passed"])
        frozen = {**self.rules, "allow_global_time_reversal": False}
        verdict = b.compare(self.reference, parsed, frozen)
        self.assertFalse(verdict["passed"], verdict)

    def test_lattice_one_percent_boundary(self) -> None:
        for scale, expected in [(0.99, True), (1.01, True), (0.989, False), (1.011, False)]:
            with self.subTest(scale=scale):
                candidate = copy.deepcopy(self.reference)
                candidate.lattice *= scale
                self.check(candidate, expected)

    def test_zero_moments_and_wrong_chemistry_fail(self) -> None:
        candidate = copy.deepcopy(self.reference)
        candidate.moments[:] = 0
        self.check(candidate, False)
        candidate = copy.deepcopy(self.reference)
        candidate.species[0] = "Fe"
        self.check(candidate, False)

    def test_point_and_underscore_tags(self) -> None:
        path = self.folder / "underscore.mcif"
        write_explicit(path, self.reference)
        path.write_text(path.read_text().replace("_moment.", "_moment_")
                        .replace("_operation.", "_operation_"))
        self.assertTrue(b.compare(self.reference, b.read_structure(path), self.rules)["passed"])

    def test_conflicting_symmetry_moments(self) -> None:
        text = (b.SOURCE / "MnTe_最终磁结构.mcif").read_text()
        path = self.folder / "conflict.mcif"
        path.write_text(text.replace("Mn1 2.00000 2.00000 0.00000", "Mn1 2.00000 0.00000 0.00000"))
        with self.assertRaisesRegex(b.InvalidStructure, "Moments conflict"):
            b.read_structure(path)

    def test_safe_symmetry_parser_and_nonfinite_values(self) -> None:
        for expression in ("__import__('os').system('echo forbidden')", "x*y", "x/0"):
            with self.subTest(expression=expression), self.assertRaises(b.InvalidStructure):
                b.affine(expression)
        for value in ("nan", "inf", "?"):
            with self.subTest(value=value), self.assertRaises(b.InvalidStructure):
                b.number(value)

    def test_missing_and_multiblock_files(self) -> None:
        with self.assertRaises(b.InvalidStructure):
            b.read_structure(self.folder / "missing.mcif")
        path = self.folder / "multiple.mcif"
        text = (b.SOURCE / "MnTe_最终磁结构.mcif").read_text()
        path.write_text(text + "\n" + text.replace("data_5yOhtAoR", "data_other"))
        with self.assertRaisesRegex(b.InvalidStructure, "exactly one"):
            b.read_structure(path)

    def test_prepare_manifest_and_no_answer_in_public(self) -> None:
        root = self.folder / "run"
        b.prepare(root)
        manifest = json.loads((root / "public" / "manifest.json").read_text())
        self.assertEqual(manifest["banks"], ["BS", "HA", "LA", "MA", "SE"])
        self.assertEqual(len(list((root / "public").rglob("*.dat"))), 10)
        self.assertEqual(len(list((root / "public").rglob("*.instprm"))), 5)
        self.assertFalse(list((root / "public").rglob("*.mcif")))
        for record in manifest["files"]:
            self.assertEqual(record["sha256"], b.digest(root / "public" / record["path"]))
        self.assertEqual(b.digest(root / "private" / "reference.mcif"),
                         b.digest(b.SOURCE / "MnTe_最终磁结构.mcif"))
        with self.assertRaises(FileExistsError):
            b.prepare(root)

    def test_prepare_hides_the_phase_identity(self) -> None:
        """The starting structure must not name the compound: that name is the prior
        the case forbids, and it survives the rename to `initial.cif`."""
        root = self.folder / "named"
        b.prepare(root)
        text = (root / "public" / "input" / "initial.cif").read_text(encoding="utf-8")
        self.assertNotIn("MnTe", text)
        self.assertNotIn("Phases extracted from file", text)
        self.assertIn("'reference phase'", text)
        # Element identity stays: scattering lengths and magnetic form factors need it.
        self.assertIn("Mn", text)
        self.assertIn("Te", text)
        self.assertIn("4.15439(4)", text)

    def test_cli_exit_codes_and_json(self) -> None:
        reference = b.SOURCE / "MnTe_最终磁结构.mcif"
        for submission, expected in [(reference, 0), (self.folder / "missing.mcif", 1)]:
            result = subprocess.run(
                [sys.executable, str(b.HERE / "benchmark.py"), "evaluate",
                 "--reference", str(reference), "--submission", str(submission)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, expected, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["verdict"], "PASS" if expected == 0 else "FAIL")
        result = subprocess.run(
            [sys.executable, str(b.HERE / "benchmark.py"), "evaluate",
             "--reference", str(self.folder / "no-reference.mcif"), "--submission", str(reference)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("EVALUATOR ERROR", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
