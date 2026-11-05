import copy
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from pipeline.validate_session import Validator, nearest_skew_ms

K = [[100, 0, 1], [0, 100, 1], [0, 0, 1]]


class SessionValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.segment = self.root / "segment-0000"
        self.segment.mkdir()
        self.manifest = {
            "schema": "iphone-rsa.session/1", "status": "complete", "segment_count": 1,
            "stop_reason": "user", "counts": {name: 3 for name in ("main", "ultra", "depth", "accelerometer", "gyroscope")},
            "drops": {"main": 0, "ultra": 0, "depth": 0},
            "configuration": {"ultra_from_main_m": [[1, 0, 0, .01], [0, 1, 0, 0], [0, 0, 1, 0]]},
        }
        self.frames = []
        self.depths = []
        self.imu = []
        for index in range(3):
            time = 100_000_000_000 + index * 33_333_333
            for stream in ("main", "ultra"):
                timestamp = time + (1_000_000 if stream == "ultra" else 0)
                self.frames.append({"stream": stream, "frame_index": index, "host_ns": str(timestamp),
                    "capture_pts_value": str(timestamp), "capture_pts_timescale": 1_000_000_000,
                    "media_time_s": index * 33_333_333 / 1e9, "width": 2, "height": 2, "intrinsics": copy.deepcopy(K)})
            self.depths.append({"frame_index": index, "host_ns": str(time), "capture_pts_value": str(time),
                "capture_pts_timescale": 1_000_000_000, "offset_bytes": index * 16, "length_bytes": 16,
                "width": 2, "height": 2, "filtered": False, "accuracy": "absolute", "calibration": {"intrinsics": K}})
            for sensor in ("accelerometer", "gyroscope"):
                self.imu.append({"sensor": sensor, "host_ns": str(time), "sensor_time_s": time / 1e9,
                    "unit": "g" if sensor == "accelerometer" else "rad/s", "xyz": [0, 0, 1]})
        self.segment_meta = {"schema": "iphone-rsa.segment/1", "status": "complete", "depth_count": 3, "depth_bytes": 48,
                             "videos": {name: {"frame_count": 3} for name in ("main", "ultra")}}
        (self.segment / "depth.f32").write_bytes(struct.pack("<12f", *([1.0, 2.0, float("nan"), 0.0] * 3)))
        for stream in ("main", "ultra"):
            (self.segment / f"{stream}.mov").write_bytes(b"fixture: video verification is opt-in")
        self.save()

    def save(self):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))
        (self.segment / "segment.json").write_text(json.dumps(self.segment_meta))
        for path, rows in [(self.segment / "frames.jsonl", self.frames), (self.segment / "depth.jsonl", self.depths),
                           (self.root / "imu.jsonl", self.imu), (self.root / "events.jsonl", [])]:
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def validate(self, **kwargs):
        self.save()
        return Validator(self.root, **kwargs).run()

    def test_valid_session_keeps_native_invalid_depth(self):
        report = self.validate()
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["sampled_depth_valid_fraction"], .5)
        self.assertAlmostEqual(report["pair_skew_ms"]["ultra_to_nearest_main"]["p95"], 1)

    def test_truncated_depth_is_rejected(self):
        (self.segment / "depth.f32").write_bytes(b"\0" * 20)
        self.assertTrue(any("truncated" in e for e in self.validate()["errors"]))

    def test_missing_motion_is_rejected(self):
        self.imu = [row for row in self.imu if row["sensor"] != "gyroscope"]
        self.assertTrue(any("gyroscope has no samples" in e for e in self.validate()["errors"]))

    def test_bad_host_clock_mapping_is_rejected(self):
        self.frames[-2]["host_ns"] = str(int(self.frames[-2]["host_ns"]) + 10_000_000)
        self.assertTrue(any("clock mapping" in e for e in self.validate()["errors"]))

    def test_duplicate_timestamps_are_rejected(self):
        self.frames[2]["host_ns"] = self.frames[0]["host_ns"]
        self.assertTrue(any("strictly increasing" in e for e in self.validate()["errors"]))

    def test_missing_segment_is_rejected(self):
        self.manifest["segment_count"] = 2
        self.assertTrue(any("segment_count" in e for e in self.validate()["errors"]))

    def test_interrupted_export_is_rejected(self):
        self.manifest["status"] = "recording"
        self.assertEqual(self.validate()["status"], "failed")

    def test_malformed_manifest_field_is_reported_without_crash(self):
        self.manifest["counts"] = None
        self.assertTrue(any("manifest counts" in e for e in self.validate()["errors"]))

    def test_missing_calibration_is_visible(self):
        self.frames[0]["intrinsics"] = None
        report = self.validate()
        self.assertEqual(report["status"], "warning")
        self.assertEqual(report["calibration_missing"]["main"], 1)

    def test_video_check_rejects_invalid_container(self):
        import shutil
        if not shutil.which("ffprobe"):
            self.skipTest("ffprobe not installed")
        self.assertTrue(any("video verification failed" in e for e in self.validate(check_video=True)["errors"]))

    def test_real_encoded_video_matches_index(self):
        import shutil
        if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg/ffprobe not installed")
        movie = self.segment / "main.mov"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=size=16x16:rate=30",
                        "-frames:v", "3", "-c:v", "libx264", "-bf", "0", str(movie)], check=True, timeout=30)
        shutil.copyfile(movie, self.segment / "ultra.mov")
        for row in self.frames:
            row["width"] = row["height"] = 16
        report = self.validate(check_video=True)
        self.assertEqual(report["errors"], [])

    def test_no_overlap_is_detected(self):
        for row in self.frames:
            if row["stream"] == "ultra":
                row["host_ns"] = str(int(row["host_ns"]) + 100_000_000_000)
        self.assertTrue(any("no temporal overlap" in e for e in self.validate()["errors"]))

    def test_report_cannot_overwrite_source(self):
        result = subprocess.run(["python3", "-m", "pipeline.validate_session", str(self.root),
                                 "--output", str(self.root / "manifest.json")], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads((self.root / "manifest.json").read_text())["schema"], "iphone-rsa.session/1")

    def test_nearest_pair_handles_boundaries(self):
        self.assertEqual(nearest_skew_ms([10, 20, 30], [9, 10, 31])["samples"], 1)


if __name__ == "__main__":
    unittest.main()
