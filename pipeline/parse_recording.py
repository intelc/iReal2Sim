"""Index every measured stream and audit a native recording without resampling it."""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .rgbd import Recording, VideoReader, output_directory, read_rows, write_json


def dataset_stamp(host_ns: int, origin_ns: int) -> str:
    seconds, nanos = divmod(host_ns - origin_ns + 1_000_000_000, 1_000_000_000)
    return f"{seconds}.{nanos:09d}"


def interval_indices(times: list[int], start_ns: int, end_ns: int) -> list[int]:
    """Half-open [start,end) sensor intervals partition samples without duplication."""
    return [bisect.bisect_left(times, start_ns), bisect.bisect_left(times, end_ns)]


def source_reference(row: dict, stream: str) -> dict:
    return {"segment": row["segment"], "frame_index": row["frame_index"],
            "host_ns": row["host_ns"], "movie": f"{row['segment']}/{stream}.mov",
            "width": row["width"], "height": row["height"], "intrinsics": row.get("intrinsics"),
            "camera": row.get("camera")}


def observation_rows(recording: Recording, imu_times: dict[str, list[int]]):
    depths = {r["timestamp_ns"]: i + 1 for i, r in enumerate(recording.frames["depth"])}
    main = recording.frames["main"]
    for index, row in enumerate(main):
        stamp = row["timestamp_ns"]
        ultra = recording.nearest("ultra", stamp)
        next_ns = main[index + 1]["timestamp_ns"] if index + 1 < len(main) else stamp + 1
        yield {"main_id": index + 1, "host_ns": row["host_ns"],
               "timestamp_s": dataset_stamp(stamp, recording.origin_ns),
               "main": source_reference(row, "main"), "ultra_nearest": source_reference(ultra, "ultra"),
               "ultra_minus_main_ns": ultra["timestamp_ns"] - stamp,
               "exact_depth_id": depths.get(stamp),
               "imu_ranges": {sensor: interval_indices(times, stamp, next_ns) for sensor, times in imu_times.items()}}


def write_jsonl(path: Path, rows):
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, allow_nan=False) + "\n")


def depth_audit(recording: Recording) -> dict:
    pixels = finite = positive = retained = 0
    band_counts = np.zeros((5, 4), dtype=np.int64)
    time_bins = {}
    for row in recording.frames["depth"]:
        depth = recording.depth(row)
        valid = np.isfinite(depth) & (depth > 0)
        keep = valid & (depth >= .2) & (depth <= 5)
        pixels += depth.size
        finite += int(np.isfinite(depth).sum())
        positive += int(valid.sum())
        retained += int(keep.sum())
        for i, band in enumerate(np.array_split(depth, 5, axis=1)):
            good = np.isfinite(band) & (band > 0)
            band_counts[i] += [band.size, int(good.sum()), int(np.sum(good & (band < .2))), int(np.sum(good & (band > 5)))]
        second = (row["timestamp_ns"] - recording.origin_ns) // 1_000_000_000
        bucket = time_bins.setdefault(int(second), {"second": int(second), "frames": 0, "pixels": 0, "valid_pixels": 0, "retained_pixels": 0})
        bucket["frames"] += 1; bucket["pixels"] += depth.size
        bucket["valid_pixels"] += int(valid.sum()); bucket["retained_pixels"] += int(keep.sum())
    for bucket in time_bins.values():
        bucket["finite_positive_fraction"] = bucket["valid_pixels"] / bucket["pixels"]
        bucket["retained_0_2_to_5m_fraction"] = bucket["retained_pixels"] / bucket["pixels"]
    return {"frames_audited": len(recording.frames["depth"]), "finite_fraction": finite / pixels,
            "finite_positive_fraction": positive / pixels, "retained_0_2_to_5m_fraction": retained / pixels,
            "portrait_bands": [{"band": name, "finite_positive_fraction": int(c[1]) / int(c[0]),
                                "nearer_than_0_2m_fraction": int(c[2]) / int(c[0]), "beyond_5m_fraction": int(c[3]) / int(c[0])}
                               for name, c in zip(["top", "upper", "middle", "lower", "bottom"], band_counts)],
            "per_second": list(time_bins.values()),
            "limitations": "Native distorted depth pixels; this measures observed depth availability, not complete-room coverage."}


def storyboard(recording: Recording, output: Path, times_s: list[float]):
    references = []
    for time in times_s:
        row = recording.nearest("main", recording.origin_ns + round(time * 1e9))
        if not references or references[-1]["timestamp_ns"] != row["timestamp_ns"]:
            references.append(row)
    width, height, columns = 225, 400, 7
    for stream in ("main", "ultra"):
        sheet = Image.new("RGB", (width * columns, (height + 35) * ((len(references) + columns - 1) // columns)), "#f4f3ef")
        draw = ImageDraw.Draw(sheet)
        reader = VideoReader(recording, stream)
        try:
            for i, main in enumerate(references):
                row = recording.nearest(stream, main["timestamp_ns"])
                image = cv2.cvtColor(cv2.rotate(reader.frame(row), cv2.ROTATE_90_CLOCKWISE), cv2.COLOR_BGR2RGB)
                x, y = (i % columns) * width, (i // columns) * (height + 35)
                sheet.paste(Image.fromarray(image).resize((width, height)), (x, y + 35))
                draw.text((x + 8, y + 8), f"{stream} {(main['timestamp_ns']-recording.origin_ns)/1e9:.2f}s", fill="#222")
            sheet.save(output / f"storyboard-{stream}.jpg", quality=94)
        finally:
            reader.close()


def parse(source: Path, output: Path):
    recording = Recording(source)
    output = output_directory(recording.path, output)
    imu = read_rows(recording.path / "imu.jsonl")
    imu_by_sensor = {sensor: [r for r in imu if r["sensor"] == sensor] for sensor in ("accelerometer", "gyroscope")}
    imu_times = {sensor: [int(r["host_ns"]) for r in rows] for sensor, rows in imu_by_sensor.items()}
    for sensor, times in imu_times.items():
        if not times or any(a >= b for a, b in zip(times, times[1:])):
            raise ValueError(f"Missing or non-monotonic {sensor} data")
        write_jsonl(output / f"imu-{sensor}.jsonl", ({**r, "index": i, "timestamp_s": dataset_stamp(int(r["host_ns"]), recording.origin_ns)} for i, r in enumerate(imu_by_sensor[sensor])))
    observations = list(observation_rows(recording, imu_times))
    write_jsonl(output / "observations.jsonl", observations)
    write_jsonl(output / "depth-frames.jsonl", ({**r, "timestamp_ns": str(r["timestamp_ns"]), "id": i + 1, "timestamp_s": dataset_stamp(r["timestamp_ns"], recording.origin_ns),
                "payload": f"{r['segment']}/depth.f32"} for i, r in enumerate(recording.frames["depth"])))
    for stream in ("main", "ultra"):
        write_jsonl(output / f"rgb-{stream}.jsonl", ({**r, "timestamp_ns": str(r["timestamp_ns"]), "id": i + 1, "timestamp_s": dataset_stamp(r["timestamp_ns"], recording.origin_ns),
                    "movie": f"{r['segment']}/{stream}.mov"} for i, r in enumerate(recording.frames[stream])))
    write_json(output / "capture-configuration.json", recording.manifest["configuration"])
    quality = depth_audit(recording)
    write_json(output / "depth-quality.json", quality)
    gaps = {s: {"samples": len(rows), "mean_hz": (len(rows) - 1) / ((rows[-1]["timestamp_ns"] - rows[0]["timestamp_ns"]) / 1e9)} for s, rows in recording.frames.items()}
    paired = sum(r["exact_depth_id"] is not None for r in observations)
    report = {"schema": "iphone-rsa.parsed-recording/1", "status": "generating_storyboards", "source": str(recording.path),
        "source_manifest_sha256": hashlib.sha256((recording.path / "manifest.json").read_bytes()).hexdigest(),
        "origin_host_ns": str(recording.origin_ns), "timestamp_convention": "1 + (original host_ns - origin_host_ns)/1e9; no time correction",
        "streams": gaps, "imu_samples": {s: len(rows) for s, rows in imu_by_sensor.items()},
        "main_frames_with_exact_depth": paired, "main_frames_without_exact_depth": len(observations) - paired,
        "ultra_matching": "nearest recorded observation; signed timestamp difference retained, no exposure-time correction",
        "imu_ranges": "half-open index ranges into corresponding imu-SENSOR.jsonl, covering [main timestamp, next main timestamp); final frame covers exact timestamp only",
        "unassigned_imu_samples": {s: len(times) - sum(r["imu_ranges"][s][1] - r["imu_ranges"][s][0] for r in observations) for s, times in imu_times.items()},
        "depth_finite_positive_fraction": quality["finite_positive_fraction"],
        "depth_retained_0_2_to_5m_fraction": quality["retained_0_2_to_5m_fraction"],
        "source_payloads_copied": False, "movie_and_payload_paths": "relative to source; frame_index is local to stream/segment",
        "depth_interpolated_or_duplicated": False,
        "limitations": ["No world pose, hand/object tracks or semantic masks are inferred by parsing.",
                        "Ultra-wide distortion, camera/IMU extrinsics and physical sensor latency remain uncalibrated."]}
    write_json(output / "parse.json", report)
    duration = (recording.times["main"][-1] - recording.origin_ns) / 1e9
    times = sorted(set([float(t) for t in np.arange(0, duration, 4)] + [float(t) for t in np.arange(28, duration, 2)] + [duration]))
    storyboard(recording, output, times)
    report["status"] = "complete"
    write_json(output / "parse.json", report)
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parse(args.source, args.output)


if __name__ == "__main__":
    main()
