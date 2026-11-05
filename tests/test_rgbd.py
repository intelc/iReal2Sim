import base64
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from pipeline.rgbd import (Recording, apple_radial_map, backproject, depth_u16_mm,
                           output_directory, rectify_rgbd, scale_intrinsics)


def calibration(table=(0., 0.), inverse=(0., 0.)):
    encode = lambda x: base64.b64encode(np.asarray(x, dtype="<f4").tobytes()).decode()
    return {"reference_width": 8, "reference_height": 6, "distortion_center": [3.5, 2.5],
            "distortion_lookup_f32le_base64": encode(table),
            "inverse_distortion_lookup_f32le_base64": encode(inverse),
            "intrinsics": [[4, 0, 3.5], [0, 4, 2.5], [0, 0, 1]]}


class RGBDGeometryTests(unittest.TestCase):
    def test_apple_lut_direction_and_roundtrip(self):
        cal = calibration((.1, .1), (-1/11, -1/11))
        point = np.array([[5.5, 2.5]])
        mapped = apple_radial_map(point, cal)
        np.testing.assert_allclose(mapped, [[5.7, 2.5]], atol=1e-7)
        np.testing.assert_allclose(apple_radial_map(mapped, cal, inverse=True), point, atol=1e-7)

    def test_missing_distortion_is_not_silently_zero(self):
        cal = calibration()
        cal["distortion_lookup_f32le_base64"] = None
        with self.assertRaisesRegex(ValueError, "Missing Apple"):
            apple_radial_map(np.array([[1, 1]]), cal)

    def test_identity_rectification_preserves_depth_holes(self):
        cal = calibration()
        K = np.asarray(cal["intrinsics"])
        rgb = np.arange(6 * 8 * 3, dtype=np.uint8).reshape(6, 8, 3)
        depth = np.full((6, 8), 1.5, dtype=np.float32)
        depth[2, 2] = np.nan
        depth[3, 4] = 0
        color, z = rectify_rgbd(rgb, depth, {"intrinsics": K, "width": 8, "height": 6},
                                {"calibration": cal, "accuracy": "absolute", "width": 8, "height": 6},
                                K, (8, 6), .2, 5)
        np.testing.assert_array_equal(color, rgb)
        self.assertEqual(z[2, 2], 0)
        self.assertEqual(z[3, 4], 0)
        self.assertEqual(z[1, 1], 1.5)

    def test_backprojection_known_plane_and_scaled_intrinsics(self):
        K = np.array([[2., 0, 1], [0, 2, 1], [0, 0, 1]])
        xyz = backproject(np.full((3, 3), 2.), K)
        np.testing.assert_allclose(xyz[1, 1], [0, 0, 2])
        np.testing.assert_allclose(xyz[1, 2], [1, 0, 2])
        np.testing.assert_allclose(scale_intrinsics(K, .5, .5), [[1, 0, .5], [0, 1, .5], [0, 0, 1]])

    def test_depth_mm_conversion_masks_nonfinite_and_out_of_range(self):
        values = np.array([[np.nan, np.inf, -1, 0, 1.234, 80]])
        np.testing.assert_array_equal(depth_u16_mm(values), [[0, 0, 0, 0, 1234, 0]])

    def test_output_must_not_modify_source_or_overwrite_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            with self.assertRaises(ValueError):
                output_directory(source, source / "derived")
            output = Path(temporary) / "output"
            output_directory(source, output)
            (output / "keep.txt").write_text("keep")
            with self.assertRaises(ValueError):
                output_directory(source, output)

    def test_sampling_and_nearest_matching_cross_segment_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "manifest.json").write_text(json.dumps({"schema": "iphone-rsa.session/1", "status": "complete", "first_sample_host_ns": "1000000000"}))
            for segment_index in range(2):
                path = root / f"segment-{segment_index:04d}"
                path.mkdir()
                (path / "segment.json").write_text('{"status":"complete"}')
                frames, depths = [], []
                for i in range(3):
                    ns = str(1_000_000_000 + (segment_index * 3 + i) * 33_333_000)
                    frames.extend({"stream": stream, "host_ns": ns, "frame_index": i} for stream in ("main", "ultra"))
                    depths.append({"host_ns": ns, "frame_index": i, "width": 1, "height": 1, "offset_bytes": i * 4, "length_bytes": 4})
                (path / "frames.jsonl").write_text("".join(json.dumps(r) + "\n" for r in frames))
                (path / "depth.jsonl").write_text("".join(json.dumps(r) + "\n" for r in depths))
                (path / "depth.f32").write_bytes(np.array([1, 2, 3], dtype="<f4").tobytes())
            data = Recording(root)
            sampled = data.sampled_depth(15)
            self.assertEqual([r["timestamp_ns"] for r in sampled], [1_000_000_000, 1_066_666_000, 1_133_332_000])
            self.assertEqual(sampled[-1]["segment"], "segment-0001")
            self.assertEqual(data.depth(sampled[-1])[0, 0], 2)


if __name__ == "__main__":
    unittest.main()
