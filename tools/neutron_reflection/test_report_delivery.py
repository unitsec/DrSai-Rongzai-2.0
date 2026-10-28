from pathlib import Path
import sys
import unittest
import zipfile
import numpy as np

sys.path.insert(0, str(Path(__file__).parent / "interface"))
from report_delivery import export_curve_data, zip_curve_data, curve_arrays, method_markdown
from test_batch_reflection import DataFixture
from test_mr_segmentation import dataset


class ReportDeliveryTests(DataFixture):
    def test_export_preserves_all_bins_including_invalid_for_log_plot(self):
        values = np.array([0., -.2, .5, np.nan, np.inf])
        ds = dataset(np.arange(5.), values.copy(), np.ones(5) * .01)
        path = export_curve_data(self.root, "group_001", "RUN0000001", ds)
        loaded = np.loadtxt(path)
        self.assertEqual(loaded.shape, (5, 3))
        np.testing.assert_allclose(loaded[:, 1], values, equal_nan=True)
        np.testing.assert_allclose(ds["histogram"].values, values, equal_nan=True)

    def test_zip_only_contains_explicit_curve_data_and_separates_groups(self):
        ds = dataset(np.array([.01, .02]), np.array([1., .1]), np.array([.01, .02]))
        files = [export_curve_data(self.root, group, "RUN0000001", ds) for group in ("group_001", "group_002")]
        (self.root / "private.json").write_text("{}")
        (self.root / "extra.png").write_bytes(b"not an image")
        archive = zip_curve_data(self.root, files)
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(z.namelist(), ["group_001/RUN0000001.dat", "group_002/RUN0000001.dat"])
            self.assertTrue(all("Q(A^-1)" in z.read(name).decode() for name in z.namelist()))

    def test_zip_rejects_outside_or_non_data_files(self):
        outside = self.root / "private.json"
        outside.write_text("{}")
        with self.assertRaises(ValueError):
            zip_curve_data(self.root, [outside])
        with self.assertRaises(ValueError):
            zip_curve_data(self.root, [])

    def test_export_rejects_path_traversal_and_mismatched_arrays(self):
        ds = dataset(np.array([.01, .02]), np.array([1., .1]), np.array([.01]))
        with self.assertRaises(ValueError):
            curve_arrays(ds)
        with self.assertRaises(ValueError):
            export_curve_data(self.root, "../outside", "RUN0000001", ds)

    def test_public_method_has_source_and_output_definitions_only(self):
        text = method_markdown(["phits_3_37_tanh_fit_v1"])
        for value in ("https://phits.jaea.go.jp/", "tanh", "m = Q*/Qc", "R_envelope(%)", "0.0217"):
            self.assertIn(value, text)
        for value in ("验收", "认证", "警告"):
            self.assertNotIn(value, text)


if __name__ == "__main__":
    unittest.main()
