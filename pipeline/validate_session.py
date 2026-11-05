"""Validate an exported session without changing source data (Python 3.10+, stdlib).

    python3 -m pipeline.validate_session /path/to/session --check-video --output report.json
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
from pathlib import Path
import shutil
import statistics
import struct
import subprocess
import sys
from typing import Any

STREAMS = ("main", "ultra", "depth", "accelerometer", "gyroscope")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)]


def nearest_skew_ms(reference: list[int], other: list[int]) -> dict[str, Any]:
    if not reference or not other:
        return {"samples": 0, "p50": None, "p95": None, "max": None}
    skews = []
    for time in other:
        if not reference[0] <= time <= reference[-1]:
            continue
        index = bisect.bisect_left(reference, time)
        neighbors = reference[max(0, index - 1):index + 1]
        skews.append(min(abs(time - neighbor) for neighbor in neighbors) / 1e6)
    return {"samples": len(skews), "p50": percentile(skews, .5),
            "p95": percentile(skews, .95), "max": max(skews) if skews else None}


class Validator:
    def __init__(self, root: Path, check_video: bool = False):
        self.root = root.resolve()
        self.check_video = check_video
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.times: dict[str, list[int]] = {name: [] for name in STREAMS}
        self.calibration_missing = {"main": 0, "ultra": 0, "depth": 0}
        self.valid_depth = 0
        self.sampled_depth = 0
        self.segments: list[dict[str, Any]] = []
        self.drops: dict[str, int] = {name: 0 for name in ("main", "ultra", "depth")}
        self.decoded_movies = 0

    def read_json(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(), parse_constant=self.bad_constant)
            if not isinstance(value, dict):
                raise ValueError("Expected a JSON object")
            return value
        except (OSError, ValueError) as exc:
            self.errors.append(f"{path.name}: {exc}")
            return {}

    @staticmethod
    def bad_constant(value: str) -> None:
        raise ValueError(f"Non-finite JSON number {value}")

    def mapping(self, value: Any, label: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            self.errors.append(f"{label}: expected a JSON object")
            return {}
        return value

    def rows(self, path: Path):
        try:
            with path.open() as handle:
                for number, line in enumerate(handle, 1):
                    try:
                        row = json.loads(line, parse_constant=self.bad_constant)
                        if not isinstance(row, dict):
                            raise ValueError("Expected a JSON object")
                        yield row
                    except ValueError as exc:
                        self.errors.append(f"{path.parent.name}/{path.name}:{number}: {exc}")
        except OSError as exc:
            self.errors.append(f"{path.name}: {exc}")

    def timestamp(self, stream: str, row: dict[str, Any]) -> int:
        value = row["host_ns"]
        if not isinstance(value, str) or not value.isdecimal():
            raise ValueError("host_ns must be a nonnegative decimal string")
        timestamp = int(value)
        previous = self.times[stream]
        if previous and timestamp <= previous[-1]:
            raise ValueError(f"{stream}: timestamps are not strictly increasing")
        previous.append(timestamp)
        return timestamp

    @staticmethod
    def dimensions(row: dict[str, Any]) -> tuple[int, int]:
        width, height = row["width"], row["height"]
        if not all(isinstance(n, int) and not isinstance(n, bool) and 0 < n <= 16384 for n in (width, height)):
            raise ValueError("Invalid frame dimensions")
        return width, height

    @staticmethod
    def check_pts(row: dict[str, Any]) -> float:
        value, scale = row["capture_pts_value"], row["capture_pts_timescale"]
        if not isinstance(value, str) or not value.lstrip("-").isdecimal():
            raise ValueError("capture_pts_value must be a decimal string")
        if not isinstance(scale, int) or isinstance(scale, bool) or scale <= 0:
            raise ValueError("Invalid capture_pts_timescale")
        return int(value) / scale

    @staticmethod
    def check_intrinsics(matrix: Any) -> bool:
        if matrix is None:
            return False
        if not (isinstance(matrix, list) and len(matrix) == 3
                and all(isinstance(row, list) and len(row) == 3 for row in matrix)):
            raise ValueError("Intrinsics must be a 3×3 row-major matrix")
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for row in matrix for x in row):
            raise ValueError("Invalid intrinsics values")
        if matrix[0][0] <= 0 or matrix[1][1] <= 0 or abs(matrix[2][2] - 1) > .001:
            raise ValueError("Invalid focal length or homogeneous intrinsics entry")
        return True

    def validate_segment(self, path: Path) -> None:
        meta = self.read_json(path / "segment.json")
        if meta.get("schema") != "iphone-rsa.segment/1":
            self.errors.append(f"{path.name}: unsupported or missing segment schema")
        if meta.get("status") != "complete":
            self.errors.append(f"{path.name}: segment is {meta.get('status', 'missing status')}")
        frame_rows: dict[str, list[dict[str, Any]]] = {"main": [], "ultra": []}
        origins: dict[str, tuple[float, int]] = {}
        for row in self.rows(path / "frames.jsonl"):
            try:
                stream = row["stream"]
                if stream not in frame_rows:
                    raise ValueError(f"Unknown RGB stream {stream}")
                self.timestamp(stream, row)
                self.dimensions(row)
                pts = self.check_pts(row)
                if row["frame_index"] != len(frame_rows[stream]):
                    raise ValueError(f"{stream}: non-contiguous frame_index")
                if not self.check_intrinsics(row.get("intrinsics")):
                    self.calibration_missing[stream] += 1
                if not isinstance(row["media_time_s"], (int, float)) or not math.isfinite(row["media_time_s"]):
                    raise ValueError("Invalid media_time_s")
                first_pts, first_host = origins.setdefault(stream, (pts, int(row["host_ns"])))
                if abs(row["media_time_s"] - (pts - first_pts)) > .0001:
                    raise ValueError("media_time_s does not match original PTS relative to segment video origin")
                if abs((int(row["host_ns"]) - first_host) / 1e9 - (pts - first_pts)) > .002:
                    raise ValueError("Host clock mapping disagrees with capture PTS by over 2 ms")
                frame_rows[stream].append(row)
            except (KeyError, TypeError, ValueError) as exc:
                self.errors.append(f"{path.name}/frames.jsonl: {exc}")

        video_metadata = self.mapping(meta.get("videos"), f"{path.name}/videos")
        for stream, rows in frame_rows.items():
            declared = self.mapping(video_metadata.get(stream), f"{path.name}/videos/{stream}")
            if declared.get("frame_count") != len(rows):
                self.errors.append(f"{path.name}/{stream}: video manifest frame count does not match index")
            movie = path / f"{stream}.mov"
            if not movie.is_file() or movie.stat().st_size == 0:
                self.errors.append(f"{path.name}/{stream}: video payload missing or empty")
            elif self.check_video:
                self.validate_movie(movie, rows)

        depth_count = 0
        expected_offset = 0
        try:
            with (path / "depth.f32").open("rb") as payload:
                size = (path / "depth.f32").stat().st_size
                for row in self.rows(path / "depth.jsonl"):
                    try:
                        self.timestamp("depth", row)
                        self.check_pts(row)
                        width, height = self.dimensions(row)
                        offset, length = row["offset_bytes"], row["length_bytes"]
                        if row["frame_index"] != depth_count:
                            raise ValueError("Non-contiguous depth frame_index")
                        if offset != expected_offset or length != width * height * 4:
                            raise ValueError("Depth byte range is not contiguous or does not match float32 dimensions")
                        if offset + length > size:
                            raise ValueError("Depth payload is truncated")
                        calibration = row.get("calibration")
                        if calibration is not None and not isinstance(calibration, dict):
                            raise ValueError("Depth calibration must be an object or null")
                        if calibration is None or not self.check_intrinsics(calibration.get("intrinsics")):
                            self.calibration_missing["depth"] += 1
                        if row.get("accuracy") != "absolute":
                            raise ValueError("Depth is not absolute metric depth")
                        if row.get("filtered") is not False:
                            raise ValueError("Depth filtering unexpectedly enabled")
                        if depth_count % 30 == 0:
                            payload.seek(offset)
                            values = struct.iter_unpack("<f", payload.read(length))
                            for (value,) in values:
                                self.sampled_depth += 1
                                self.valid_depth += math.isfinite(value) and value > 0
                        expected_offset += length
                        depth_count += 1
                    except (KeyError, TypeError, ValueError, struct.error) as exc:
                        self.errors.append(f"{path.name}/depth.jsonl: {exc}")
                if size != expected_offset:
                    self.errors.append(f"{path.name}: depth payload byte count differs from indexed bytes")
        except OSError as exc:
            self.errors.append(f"{path.name}/depth.f32: {exc}")
        if meta.get("depth_count") != depth_count or meta.get("depth_bytes") != expected_offset:
            self.errors.append(f"{path.name}: depth manifest counts disagree with the index")
        self.segments.append({"name": path.name, "main_frames": len(frame_rows["main"]),
                              "ultra_frames": len(frame_rows["ultra"]), "depth_frames": depth_count})

    def validate_movie(self, path: Path, rows: list[dict[str, Any]]) -> None:
        if not shutil.which("ffprobe"):
            self.errors.append("--check-video requires ffprobe on PATH")
            return
        command = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
                   "-show_entries", "frame=best_effort_timestamp_time,width,height", "-of", "json", str(path)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=True)
            frames = json.loads(result.stdout)["frames"]
            if len(frames) != len(rows):
                raise ValueError(f"Decoded {len(frames)} frames; index has {len(rows)}")
            for frame, row in zip(frames, rows):
                if (frame["width"], frame["height"]) != (row["width"], row["height"]):
                    raise ValueError("Decoded resolution disagrees with index")
                if abs(float(frame["best_effort_timestamp_time"]) - row["media_time_s"]) > .002:
                    raise ValueError("Decoded media PTS disagrees with index by over 2 ms")
            self.decoded_movies += 1
        except (subprocess.SubprocessError, ValueError, KeyError, OSError) as exc:
            self.errors.append(f"{path.parent.name}/{path.name}: video verification failed: {exc}")

    def run(self) -> dict[str, Any]:
        manifest = self.read_json(self.root / "manifest.json")
        counts = self.mapping(manifest.get("counts"), "manifest counts")
        drops_declared = self.mapping(manifest.get("drops"), "manifest drops")
        configuration = self.mapping(manifest.get("configuration"), "manifest configuration")
        if manifest.get("schema") != "iphone-rsa.session/1":
            self.errors.append("Unsupported or missing session schema")
        if manifest.get("status") != "complete":
            self.errors.append(f"Session is {manifest.get('status', 'missing status')}: {manifest.get('error')}")
        paths = sorted(path for path in self.root.glob("segment-*") if path.is_dir())
        if len(paths) != manifest.get("segment_count") or not paths:
            self.errors.append("Segment directories do not match manifest segment_count, or no segments exist")
        for index, path in enumerate(paths):
            if path.name != f"segment-{index:04d}":
                self.errors.append("Segment numbering is not contiguous")
            self.validate_segment(path)
        for row in self.rows(self.root / "imu.jsonl"):
            try:
                sensor = row["sensor"]
                if sensor not in ("accelerometer", "gyroscope"):
                    raise ValueError(f"Unknown IMU sensor {sensor}")
                timestamp = self.timestamp(sensor, row)
                if abs(timestamp / 1e9 - float(row["sensor_time_s"])) > 1e-6:
                    raise ValueError("IMU host_ns does not match sensor_time_s")
                if row["unit"] != ("g" if sensor == "accelerometer" else "rad/s"):
                    raise ValueError("Wrong IMU unit")
                xyz = row["xyz"]
                if len(xyz) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in xyz):
                    raise ValueError("IMU xyz must contain three finite numbers")
            except (KeyError, TypeError, ValueError) as exc:
                self.errors.append(f"imu.jsonl: {exc}")
        for row in self.rows(self.root / "events.jsonl"):
            if row.get("event") == "sample_dropped" and row.get("stream") in self.drops:
                self.drops[row["stream"]] += 1
        metrics = {}
        for stream, times in self.times.items():
            if not times:
                self.errors.append(f"Required stream {stream} has no samples")
            if counts.get(stream) != len(times):
                self.errors.append(f"Manifest count for {stream} disagrees with sample index")
            intervals = [(b - a) / 1e9 for a, b in zip(times, times[1:]) if b > a]
            median = statistics.median(intervals) if intervals else None
            metrics[stream] = {"samples": len(times), "duration_s": (times[-1] - times[0]) / 1e9 if len(times) > 1 else 0,
                               "median_hz": 1 / median if median else None,
                               "max_gap_s": max(intervals) if intervals else None,
                               "gaps_over_2x_median": sum(delta > median * 2 for delta in intervals) if median else 0}
            if intervals and max(intervals) > .25:
                self.warnings.append(f"{stream}: gap over 250 ms")
        for stream, drops in self.drops.items():
            if drops != drops_declared.get(stream):
                self.errors.append(f"Manifest dropped count for {stream} disagrees with events")
            if drops:
                self.warnings.append(f"{stream}: {drops} explicitly dropped samples")
        for stream, count in self.calibration_missing.items():
            if count:
                self.warnings.append(f"{stream}: {count} samples lack intrinsics/calibration")
        if configuration.get("ultra_from_main_m") is None:
            self.warnings.append("Main-to-ultra factory extrinsics unavailable; calibrate the pair before multi-view fitting")
        depth_fraction = self.valid_depth / self.sampled_depth if self.sampled_depth else None
        if depth_fraction is not None and depth_fraction < .2:
            self.warnings.append("Less than 20% of sampled depth pixels are finite positive values")
        if manifest.get("stop_reason") != "user":
            self.warnings.append(f"Recording ended because: {manifest.get('stop_reason', 'unknown')}")
        pair_skews = {"ultra_to_nearest_main": nearest_skew_ms(sorted(self.times["main"]), self.times["ultra"]),
                      "depth_to_nearest_main": nearest_skew_ms(sorted(self.times["main"]), self.times["depth"])}
        for name, skew in pair_skews.items():
            if skew["p95"] is not None and skew["p95"] > 10:
                self.warnings.append(f"{name}: p95 timestamp skew over 10 ms; inspect exposure timing")
            if not skew["samples"]:
                self.errors.append(f"{name}: no temporal overlap")
        return {"schema": "iphone-rsa.validation/1", "session": str(self.root),
                "status": "failed" if self.errors else ("warning" if self.warnings else "passed"),
                "video_payload_verification_requested": self.check_video,
                "video_payloads_verified": self.decoded_movies, "metrics": metrics, "pair_skew_ms": pair_skews,
                "sampled_depth_valid_fraction": depth_fraction, "calibration_missing": self.calibration_missing,
                "segments": self.segments, "errors": self.errors, "warnings": self.warnings,
                "limitations": ["Timestamp proximity does not establish simultaneous exposure.",
                                "Geometry, sensor latency, camera-to-IMU extrinsics, and reconstruction accuracy require physical calibration tests.",
                                "Depth validity is sampled every 30 depth frames; byte ranges and indexes are checked for every frame."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--check-video", action="store_true", help="Decode every RGB frame with ffprobe and verify its PTS")
    parser.add_argument("--output", type=Path, help="Write a JSON report outside the immutable source session")
    args = parser.parse_args()
    if args.output and (args.output.resolve() == args.session.resolve() or args.session.resolve() in args.output.resolve().parents):
        parser.error("--output must be outside the source session")
    report = Validator(args.session, args.check_video).run()
    serialized = json.dumps(report, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(serialized + "\n")
    print(serialized)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
