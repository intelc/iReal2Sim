"""Read immutable recordings and rectify Apple's calibrated, distorted RGB-D data."""
from __future__ import annotations

import base64
import bisect
import json
from pathlib import Path

import cv2
import numpy as np


def read_rows(path: Path) -> list[dict]:
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def output_directory(source: Path, output: Path, *protected_sources: Path) -> Path:
    source, output = source.resolve(), output.resolve()
    for protected in (source, *protected_sources):
        protected = protected.resolve()
        if output == protected or protected in output.parents:
            raise ValueError("Derived output must be outside all source recordings and datasets")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output is not empty: {output}. Use a new output directory.")
    output.mkdir(parents=True, exist_ok=True)
    return output


class Recording:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.manifest = json.loads((self.path / "manifest.json").read_text())
        if self.manifest.get("schema") != "iphone-rsa.session/1" or self.manifest.get("status") != "complete":
            raise ValueError("Only complete v1 source recordings can be exported")
        self.origin_ns = int(self.manifest["first_sample_host_ns"])
        self.frames = {"main": [], "ultra": [], "depth": []}
        self.depth_buffers: dict[str, np.memmap] = {}
        for segment in sorted(self.path.glob("segment-*")):
            if json.loads((segment / "segment.json").read_text()).get("status") != "complete":
                raise ValueError(f"Incomplete segment: {segment.name}")
            for name, stream in [("frames.jsonl", None), ("depth.jsonl", "depth")]:
                for row in read_rows(segment / name):
                    row = dict(row, segment=segment.name)
                    row["timestamp_ns"] = int(row["host_ns"])
                    self.frames[stream or row["stream"]].append(row)
            self.depth_buffers[segment.name] = np.memmap(segment / "depth.f32", dtype="<f4", mode="r")
        for stream, rows in self.frames.items():
            if not rows or any(a["timestamp_ns"] >= b["timestamp_ns"] for a, b in zip(rows, rows[1:])):
                raise ValueError(f"Missing or non-monotonic {stream} stream")
        self.times = {stream: [r["timestamp_ns"] for r in rows] for stream, rows in self.frames.items()}

    def nearest(self, stream: str, timestamp_ns: int) -> dict:
        times = self.times[stream]
        index = bisect.bisect_left(times, timestamp_ns)
        candidates = range(max(0, index - 1), min(len(times), index + 1))
        return self.frames[stream][min(candidates, key=lambda i: abs(times[i] - timestamp_ns))]

    def sampled_depth(self, fps: float | None) -> list[dict]:
        if fps is None:
            return list(self.frames["depth"])
        if not 0 < fps <= 30:
            raise ValueError("Export FPS must be in (0,30]")
        first, last = self.times["depth"][0], self.times["depth"][-1]
        selected = []
        previous = None
        for delta in np.arange(0, (last - first) / 1e9 + 1e-8, 1 / fps):
            row = self.nearest("depth", first + round(float(delta) * 1e9))
            if row["timestamp_ns"] != previous:
                selected.append(row)
                previous = row["timestamp_ns"]
        return selected

    def depth(self, row: dict) -> np.ndarray:
        start, size = row["offset_bytes"] // 4, row["length_bytes"] // 4
        return np.asarray(self.depth_buffers[row["segment"]][start:start + size]).reshape(row["height"], row["width"])


class VideoReader:
    """Decode forward by indexed frame number; never use approximate video seeking."""
    def __init__(self, recording: Recording, stream: str):
        self.recording, self.stream = recording, stream
        self.capture = None
        self.segment = None
        self.next_frame = 0
        self.last_frame = None

    def frame(self, row: dict) -> np.ndarray:
        index = row["frame_index"]
        if row["segment"] != self.segment or index < self.next_frame - 1:
            self.close()
            path = self.recording.path / row["segment"] / f"{self.stream}.mov"
            self.capture = cv2.VideoCapture(str(path))
            if not self.capture.isOpened():
                raise ValueError(f"Cannot decode {path}")
            self.segment, self.next_frame = row["segment"], 0
        if index == self.next_frame - 1 and self.last_frame is not None:
            return self.last_frame
        while self.next_frame <= index:
            if not self.capture.grab():
                raise ValueError(f"Video ended before indexed frame {index}")
            if self.next_frame == index:
                ok, image = self.capture.retrieve()
                if not ok:
                    raise ValueError(f"Cannot retrieve indexed frame {index}")
                if image.shape[:2] != (row["height"], row["width"]):
                    raise ValueError("Decoded frame dimensions disagree with source index")
                self.last_frame = image
            self.next_frame += 1
        return self.last_frame

    def close(self):
        if self.capture is not None:
            self.capture.release()
        self.capture = None
        self.last_frame = None


def scale_intrinsics(matrix: np.ndarray, scale_x: float, scale_y: float) -> np.ndarray:
    return np.diag([scale_x, scale_y, 1.0]) @ np.asarray(matrix, dtype=np.float64)


def apple_radial_map(points: np.ndarray, calibration: dict, inverse: bool = False) -> np.ndarray:
    """Apple LUT mapping. Forward LUT: rectified output -> distorted source.

    The inverse LUT maps distorted points -> rectified points. Apple's names
    refer to applying/removing lens distortion, not to cv2.remap's direction.
    All point coordinates here are at calibration reference resolution.
    """
    key = "inverse_distortion_lookup_f32le_base64" if inverse else "distortion_lookup_f32le_base64"
    encoded = calibration.get(key)
    points = np.asarray(points, dtype=np.float64)
    if encoded is None:
        raise ValueError(f"Missing Apple {key}; refusing to silently assume zero distortion")
    table = np.frombuffer(base64.b64decode(encoded, validate=True), dtype="<f4")
    if table.size < 2 or not np.isfinite(table).all():
        raise ValueError("Invalid Apple distortion lookup table")
    center = np.asarray(calibration["distortion_center"], dtype=np.float64)
    width, height = calibration["reference_width"], calibration["reference_height"]
    radius_max = np.hypot(max(center[0], width - center[0]), max(center[1], height - center[1]))
    vectors = points - center
    radii = np.linalg.norm(vectors, axis=-1)
    magnification = np.interp(radii / radius_max * (table.size - 1), np.arange(table.size), table)
    return center + vectors * (1 + magnification[..., None])


def remap_for_camera(K_output: np.ndarray, output_size: tuple[int, int],
                     K_source: np.ndarray, source_size: tuple[int, int], calibration: dict) -> tuple[np.ndarray, np.ndarray]:
    width, height = output_size
    u, v = np.meshgrid(np.arange(width), np.arange(height))
    rays = np.stack([u, v, np.ones_like(u)], axis=-1) @ np.linalg.inv(K_output).T
    source_rectified = rays @ np.asarray(K_source, dtype=np.float64).T
    source_rectified = source_rectified[..., :2] / source_rectified[..., 2:]
    distorted = apple_radial_map(source_rectified, calibration)
    distorted[..., 0] *= source_size[0] / calibration["reference_width"]
    distorted[..., 1] *= source_size[1] / calibration["reference_height"]
    return distorted[..., 0].astype(np.float32), distorted[..., 1].astype(np.float32)


def rectify_rgbd(rgb: np.ndarray, depth: np.ndarray, rgb_row: dict, depth_row: dict,
                 K_output: np.ndarray, output_size: tuple[int, int], depth_min: float, depth_max: float):
    calibration = depth_row.get("calibration")
    if calibration is None or rgb_row.get("intrinsics") is None:
        raise ValueError("RGB and depth calibration are required")
    if depth_row.get("accuracy") != "absolute":
        raise ValueError("Expected absolute metric depth")
    reference_size = (calibration["reference_width"], calibration["reference_height"])
    if reference_size != (rgb_row["width"], rgb_row["height"]):
        raise ValueError("This exporter requires calibration reference size to match native main RGB")
    mx, my = remap_for_camera(K_output, output_size, np.asarray(rgb_row["intrinsics"]), reference_size, calibration)
    color = cv2.remap(rgb, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    dx, dy = remap_for_camera(K_output, output_size, np.asarray(calibration["intrinsics"]),
                             (depth_row["width"], depth_row["height"]), calibration)
    valid = np.isfinite(depth) & (depth >= depth_min) & (depth <= depth_max)
    safe = np.where(valid, depth, 0).astype(np.float32)
    # Nearest sampling avoids mixing foreground/background depth or filling holes.
    rectified = cv2.remap(safe, dx, dy, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT)
    color_inside = (mx >= 0) & (mx <= rgb.shape[1] - 1) & (my >= 0) & (my <= rgb.shape[0] - 1)
    rectified[~color_inside] = 0
    return color, rectified


def depth_u16_mm(depth: np.ndarray) -> np.ndarray:
    valid = np.isfinite(depth) & (depth > 0) & (depth < 65.535)
    result = np.zeros(depth.shape, dtype=np.uint16)
    result[valid] = np.rint(depth[valid] * 1000).astype(np.uint16)
    return result


def backproject(depth: np.ndarray, intrinsics: np.ndarray) -> np.ndarray:
    v, u = np.indices(depth.shape)
    rays = np.stack([u, v, np.ones_like(u)], axis=-1) @ np.linalg.inv(intrinsics).T
    return rays * depth[..., None]
