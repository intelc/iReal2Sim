"""Export a calibrated fixed-intrinsics RGB-D sequence without changing raw data."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import cv2
import numpy as np

from .rgbd import Recording, VideoReader, output_directory, rectify_rgbd, scale_intrinsics, depth_u16_mm, write_json
from .validate_session import Validator


def export(recording_path: Path, output: Path, fps: float | None = 15, width: int = 640,
           depth_min: float = .2, depth_max: float = 5.0) -> dict:
    recording = Recording(recording_path)
    if not 0 < depth_min < depth_max < 65.535:
        raise ValueError("Depth limits must satisfy 0 < min < max < 65.535 metres")
    if width < 160 or width > recording.frames["main"][0]["width"]:
        raise ValueError("Export width must be 160..native width")
    selected = recording.sampled_depth(fps)
    if any(row.get("intrinsics") is None for row in recording.frames["main"]):
        raise ValueError("Main-camera intrinsics are missing")
    output = output_directory(recording.path, output)
    validation = Validator(recording.path).run()
    write_json(output / "source-validation.json", validation)
    if validation["errors"]:
        raise ValueError("Source validation failed; see source-validation.json")
    (output / "rgb").mkdir()
    (output / "depth").mkdir()
    native = recording.frames["main"][0]
    height = round(native["height"] * width / native["width"])
    native_K = np.median(np.asarray([row["intrinsics"] for row in recording.frames["main"]]), axis=0)
    K = scale_intrinsics(native_K, width / native["width"], height / native["height"])
    config = {"schema": "iphone-rsa.rgbd-export/1", "status": "exporting", "source": str(recording.path),
              "source_manifest_sha256": hashlib.sha256((recording.path / "manifest.json").read_bytes()).hexdigest(),
              "origin_host_ns": str(recording.origin_ns), "timestamp_convention": "dataset seconds = 1 + (source host_ns - origin_host_ns)/1e9",
              "intrinsics": K.tolist(), "width": width, "height": height, "depth_scale": 1000.0,
              "depth_min_m": depth_min, "depth_max_m": depth_max, "requested_fps": fps,
              "frame_selection": "all_recorded_depth_samples" if fps is None else "nearest_samples_on_requested_time_grid",
              "coordinate_system": "OpenCV optical: +X right, +Y down, +Z forward; no image rotation",
              "rectification": "Apple forward radial LUT output-to-input; per-frame RGB/depth K normalized to one virtual camera",
              "depth_sampling": "nearest; invalid/out-of-range samples become zero; no inpainting",
              "depth_encoding": "uint16 millimetres; source float32 recording remains unchanged",
              "reference_to_buffer_coordinates": "calibration reference coordinates multiplied by buffer/reference dimensions",
              "dynamic_object_masking": "not yet applied; mapping output is an initial baseline",
              "ultra_distortion_model": "not calibrated; ultra is excluded from RGB-D odometry"}
    write_json(output / "calibration.json", config)
    reader = VideoReader(recording, "main")
    rows = []
    try:
        for depth_row in selected:
            main = recording.nearest("main", depth_row["timestamp_ns"])
            delta = main["timestamp_ns"] - depth_row["timestamp_ns"]
            if abs(delta) > 1_000_000:
                raise ValueError(f"Main/depth timestamps differ by {delta / 1e6:.3f} ms; motion compensation is required")
            color, depth = rectify_rgbd(reader.frame(main), recording.depth(depth_row), main, depth_row,
                                       K, (width, height), depth_min, depth_max)
            stamp_ns = main["timestamp_ns"] - recording.origin_ns + 1_000_000_000
            stamp = f"{stamp_ns // 1_000_000_000}.{stamp_ns % 1_000_000_000:09d}"
            rgb_file, depth_file = f"rgb/{stamp}.png", f"depth/{stamp}.png"
            if not cv2.imwrite(str(output / rgb_file), color) or not cv2.imwrite(str(output / depth_file), depth_u16_mm(depth)):
                raise OSError("Cannot write RGB-D PNG payload")
            rows.append({"index": len(rows), "timestamp_s": stamp, "host_ns": main["host_ns"],
                         "rgb": rgb_file, "depth": depth_file, "rgb_depth_delta_ns": delta,
                         "main_segment": main["segment"], "main_frame_index": main["frame_index"],
                         "depth_segment": depth_row["segment"], "depth_frame_index": depth_row["frame_index"],
                         "depth_valid_fraction": float(np.mean(depth > 0))})
            if len(rows) % 50 == 0:
                print(f"Exported {len(rows)}/{len(selected)} RGB-D frames", flush=True)
    finally:
        reader.close()
    import json
    (output / "frames.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (output / "frames.tsv").write_text("".join(f"{r['index']+1}\t{r['timestamp_s']}\t{r['rgb']}\t{r['depth']}\n" for r in rows))
    for stream in ("rgb", "depth"):
        (output / f"{stream}.txt").write_text("# timestamp path\n" + "".join(f"{r['timestamp_s']} {r[stream]}\n" for r in rows))
    (output / "associations.txt").write_text("".join(f"{r['timestamp_s']} {r['rgb']} {r['timestamp_s']} {r['depth']}\n" for r in rows))
    (output / "camera.txt").write_text(f"{K[0,0]:.12f} {K[1,1]:.12f} {K[0,2]:.12f} {K[1,2]:.12f} {width} {height}\n")
    config.update(status="complete", frame_count=len(rows), duration_s=float(rows[-1]["timestamp_s"]) - float(rows[0]["timestamp_s"]))
    write_json(output / "calibration.json", config)
    print(f"Export complete: {len(rows)} frames, {width}×{height}, {output}", flush=True)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fps", type=float, default=15)
    parser.add_argument("--all-depth", action="store_true", help="Preserve every recorded depth observation at its original time; overrides --fps")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--depth-min", type=float, default=.2)
    parser.add_argument("--depth-max", type=float, default=5)
    args = parser.parse_args()
    export(args.session, args.output, None if args.all_depth else args.fps, args.width, args.depth_min, args.depth_max)


if __name__ == "__main__":
    main()
