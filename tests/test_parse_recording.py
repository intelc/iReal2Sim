import unittest

from pipeline.parse_recording import dataset_stamp, interval_indices, observation_rows
from pipeline.rgbd import Recording


class ParseRecordingTests(unittest.TestCase):
    def recording(self):
        recording = Recording.__new__(Recording)
        origin = 108_460_336_881_791
        recording.origin_ns = origin
        times = {"main": [origin, origin + 16_666_667, origin + 33_333_334],
                 "ultra": [origin + 3_000_000, origin + 19_666_667, origin + 36_333_334],
                 "depth": [origin, origin + 33_333_334]}
        recording.frames = {stream: [{"timestamp_ns": t, "host_ns": str(t), "segment": "segment-0000",
            "frame_index": i, "width": 1920, "height": 1080} for i, t in enumerate(stamps)] for stream, stamps in times.items()}
        recording.times = times
        return recording

    def test_nanosecond_stamp_avoids_float_precision_loss(self):
        origin = 123_456_789_012_345_678
        self.assertEqual(dataset_stamp(origin + 17, origin), "1.000000017")
        self.assertEqual(dataset_stamp(origin - 17, origin), "0.999999983")

    def test_imu_windows_do_not_duplicate_boundary_samples(self):
        times = [99, 100, 150, 200, 299, 300]
        self.assertEqual(interval_indices(times, 100, 200), [1, 3])
        self.assertEqual(interval_indices(times, 200, 300), [3, 5])

    def test_sixty_fps_rgb_does_not_duplicate_thirty_fps_depth(self):
        recording = self.recording()
        rows = list(observation_rows(recording, {"gyroscope": []}))
        self.assertEqual([r["exact_depth_id"] for r in rows], [1, None, 2])
        self.assertEqual([r["main_id"] for r in rows], [1, 2, 3])
        self.assertEqual(rows[0]["ultra_minus_main_ns"], 3_000_000)
        self.assertEqual(rows[1]["main"]["frame_index"], 1)

    def test_all_depth_mode_preserves_irregular_observations(self):
        recording = self.recording()
        rows = recording.frames["depth"]
        extra = {**rows[0], "timestamp_ns": recording.origin_ns + 10_000_000, "host_ns": str(recording.origin_ns + 10_000_000)}
        recording.frames["depth"] = [rows[0], extra, rows[1]]
        recording.times["depth"] = [r["timestamp_ns"] for r in recording.frames["depth"]]
        self.assertEqual([r["timestamp_ns"] for r in recording.sampled_depth(None)], recording.times["depth"])
        self.assertLess(len(recording.sampled_depth(30)), 3)


if __name__ == "__main__":
    unittest.main()
