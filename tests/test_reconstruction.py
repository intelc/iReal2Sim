import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from scipy.spatial.transform import Rotation

from pipeline.fuse_map import gyro_lag_diagnostic, read_poses
from pipeline.cross_camera_report import pose_at, project


class ReconstructionDiagnosticsTests(unittest.TestCase):
    def test_pose_interpolation_does_not_bridge_tracking_loss_or_extrapolate(self):
        a, b = np.eye(4), np.eye(4)
        b[:3, :3] = Rotation.from_euler("z", 90, degrees=True).as_matrix()
        b[0, 3] = 1
        poses = [{"timestamp_s": 1., "world_from_camera": a}, {"timestamp_s": 1.1, "world_from_camera": b}]
        midpoint = pose_at(poses, 1.05)
        np.testing.assert_allclose(midpoint[:3, 3], [.5, 0, 0])
        np.testing.assert_allclose(midpoint[:3, :3], Rotation.from_euler("z", 45, degrees=True).as_matrix(), atol=1e-10)
        self.assertIsNone(pose_at(poses, .9))
        self.assertIsNone(pose_at(poses, 1.2))
        poses[-1]["timestamp_s"] = 1.3
        self.assertIsNone(pose_at(poses, 1.1))

    def test_camera_pair_motion_compensation_transform_direction(self):
        ultra_from_main = np.eye(4)
        ultra_from_main[0, 3] = .02
        world_from_main_at_ultra_time = np.eye(4)
        world_from_main_at_ultra_time[0, 3] = .1
        transform = ultra_from_main @ np.linalg.inv(world_from_main_at_ultra_time)
        pixels, front = project(np.array([[0, 0, 2.]]), transform, np.diag([100., 100., 1.]))
        self.assertTrue(front[0])
        np.testing.assert_allclose(pixels, [[-4, 0]])

    def test_pose_reader_rejects_invalid_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "poses.tsv"
            pose = np.eye(4)
            pose[0, 0] = 2
            path.write_text("1 1.0 " + " ".join(map(str, pose.ravel())) + "\n")
            with self.assertRaisesRegex(ValueError, "orthogonal"):
                read_poses(path)

    def test_camera_imu_lag_diagnostic_sign_and_no_timestamp_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = 1_000_000_000
            camera_times = np.arange(1, 8, 1 / 15)
            angle = lambda t: 1.2 * t - .7 / 5 * np.cos(5 * t) - .3 / 12 * np.cos(12 * t)
            velocity = lambda t: 1.2 + .7 * np.sin(5 * t) + .3 * np.sin(12 * t)
            poses = []
            for i, time in enumerate(camera_times):
                pose = np.eye(4)
                pose[:3, :3] = Rotation.from_rotvec([0, 0, angle(time)]).as_matrix()
                poses.append({"id": i + 1, "timestamp_s": float(time), "world_from_camera": pose})
            rows = [{"sensor": "gyroscope", "host_ns": str(origin + round((t - 1) * 1e9)),
                     "xyz": [0, 0, float(velocity(t - .04))]} for t in np.arange(.8, 8.2, .01)]
            original = "".join(json.dumps(r) + "\n" for r in rows)
            (root / "imu.jsonl").write_text(original)
            report = gyro_lag_diagnostic(poses, root, origin)
            self.assertFalse(report["applied"])
            self.assertAlmostEqual(report["candidate_shift_ms"], 40, delta=3)
            self.assertGreater(report["peak_correlation"], .99)
            self.assertEqual((root / "imu.jsonl").read_text(), original)


if __name__ == "__main__":
    unittest.main()
