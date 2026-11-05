"""Audit factory main/ultra geometry with timestamp-aware static-feature reprojection.

Ultra distortion is NOT calibrated by this diagnostic. Do not interpret its
pixel residuals as measured extrinsic or timing error in isolation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from .fuse_map import read_poses
from .rgbd import Recording, VideoReader, output_directory, read_rows, write_json


def pose_at(poses: list[dict], time: float, max_gap: float = .15) -> np.ndarray | None:
    if not poses:
        return None
    times = np.array([p["timestamp_s"] for p in poses])
    if time < times[0] or time > times[-1]:
        return None
    exact = np.flatnonzero(np.abs(times - time) < 1e-9)
    if len(exact):
        return poses[int(exact[0])]["world_from_camera"].copy()
    right = int(np.searchsorted(times, time))
    left = right - 1
    if times[right] - times[left] > max_gap:
        return None
    fraction = (time - times[left]) / (times[right] - times[left])
    result = np.eye(4)
    matrices = [poses[i]["world_from_camera"] for i in (left, right)]
    result[:3, :3] = Slerp(times[[left, right]], Rotation.from_matrix([m[:3, :3] for m in matrices]))([time]).as_matrix()[0]
    result[:3, 3] = (1 - fraction) * matrices[0][:3, 3] + fraction * matrices[1][:3, 3]
    return result


def project(points: np.ndarray, transform: np.ndarray, K: np.ndarray):
    camera = points @ transform[:3, :3].T + transform[:3, 3]
    pixels = camera @ K.T
    return pixels[:, :2] / pixels[:, 2:], camera[:, 2] > 0


def audit(dataset: Path, run: Path, output: Path) -> dict:
    dataset = dataset.resolve()
    config = json.loads((dataset / "calibration.json").read_text())
    source = Recording(Path(config["source"]))
    output = output_directory(source.path, output)
    poses = read_poses(run / "mapping" / "optimized.tsv")
    frames = read_rows(dataset / "frames.jsonl")
    factory = source.manifest["configuration"].get("ultra_from_main_m")
    if factory is None:
        raise ValueError("Factory main-to-ultra extrinsics missing")
    ultra_from_main = np.eye(4)
    ultra_from_main[:3] = factory
    K = np.asarray(config["intrinsics"])
    sift = cv2.SIFT_create(nfeatures=2500)
    reader = VideoReader(source, "ultra")
    records = []
    try:
        for sample, index in enumerate(np.unique(np.linspace(3, len(poses) - 4, min(8, len(poses) - 6)).astype(int))):
            pose = poses[index]
            frame = frames[pose["id"] - 1]
            ultra = source.nearest("ultra", int(frame["host_ns"]))
            ultra_time = (ultra["timestamp_ns"] - source.origin_ns) / 1e9 + 1
            main_at_ultra_time = pose_at(poses, ultra_time)
            if main_at_ultra_time is None:
                records.append({"timestamp_s": frame["timestamp_s"], "status": "trajectory_gap"})
                continue
            main_image = cv2.imread(str(dataset / frame["rgb"]))
            ultra_image = reader.frame(ultra)
            depth = cv2.imread(str(dataset / frame["depth"]), cv2.IMREAD_UNCHANGED).astype(float) / 1000
            kp1, desc1 = sift.detectAndCompute(cv2.cvtColor(main_image, cv2.COLOR_BGR2GRAY), None)
            kp2, desc2 = sift.detectAndCompute(cv2.cvtColor(ultra_image, cv2.COLOR_BGR2GRAY), None)
            record = {"timestamp_s": frame["timestamp_s"], "ultra_minus_main_timestamp_ms": (ultra["timestamp_ns"] - int(frame["host_ns"])) / 1e6}
            if desc1 is None or desc2 is None:
                records.append(dict(record, status="no_features"))
                continue
            matches = cv2.BFMatcher().knnMatch(desc1, desc2, k=2)
            matches = [pair[0] for pair in matches if len(pair) == 2 and pair[0].distance < .7 * pair[1].distance]
            if len(matches) < 12:
                records.append(dict(record, status="too_few_matches", matches=len(matches)))
                continue
            a = np.array([kp1[m.queryIdx].pt for m in matches])
            b = np.array([kp2[m.trainIdx].pt for m in matches])
            _, inliers = cv2.findFundamentalMat(a, b, cv2.FM_RANSAC, 2, .999)
            if inliers is None:
                records.append(dict(record, status="match_verification_failed"))
                continue
            a, b = a[inliers.ravel() > 0], b[inliers.ravel() > 0]
            pixels = np.rint(a).astype(int)
            z = depth[np.clip(pixels[:, 1], 0, depth.shape[0] - 1), np.clip(pixels[:, 0], 0, depth.shape[1] - 1)]
            KU = np.asarray(ultra["intrinsics"])
            rays_ultra = (np.column_stack([b, np.ones(len(b))]) @ np.linalg.inv(KU).T)[:, :2]
            # Exclude close moving hands/object regions and outer ultra-wide rays.
            keep = (z > .6) & (z < config["depth_max_m"]) & (np.linalg.norm(rays_ultra, axis=1) < .7)
            a, b, z = a[keep], b[keep], z[keep]
            if len(a) < 8:
                records.append(dict(record, status="too_few_depth_supported_central_matches", matches=len(a)))
                continue
            xyz = (np.column_stack([a, np.ones(len(a))]) @ np.linalg.inv(K).T) * z[:, None]
            # Main frame at its exposure time -> world -> ultra at the ultra frame's own time.
            time_aware = ultra_from_main @ np.linalg.inv(main_at_ultra_time) @ pose["world_from_camera"]
            predicted, front = project(xyz, time_aware, KU)
            uncorrected, _ = project(xyz, ultra_from_main, KU)
            residual = np.linalg.norm(predicted - b, axis=1)[front]
            before = np.linalg.norm(uncorrected - b, axis=1)[front]
            record.update(status="diagnostic", matches=int(front.sum()),
                factory_same_time_median_px=float(np.median(before)),
                timestamp_aware_median_px=float(np.median(residual)),
                timestamp_aware_p90_px=float(np.percentile(residual, 90)))
            records.append(record)
            overlay = ultra_image.copy()
            for expected, observed in zip(predicted[front][:100], b[front][:100]):
                p, q = tuple(np.rint(expected).astype(int)), tuple(np.rint(observed).astype(int))
                cv2.line(overlay, p, q, (0, 0, 230), 1)
                cv2.circle(overlay, p, 3, (240, 100, 10), 1)
                cv2.circle(overlay, q, 3, (0, 220, 0), 1)
            cv2.imwrite(str(output / f"reprojection-{sample:02d}.jpg"), cv2.rotate(overlay, cv2.ROTATE_90_CLOCKWISE))
    finally:
        reader.close()
    usable = [r for r in records if r["status"] == "diagnostic"]
    report = {"schema": "iphone-rsa.cross-camera-audit/1", "status": "diagnostics_only", "samples": records,
        "median_of_sample_medians_px": float(np.median([r["timestamp_aware_median_px"] for r in usable])) if usable else None,
        "applied_calibration_correction": False,
        "limitations": ["Ultra-wide distortion is not calibrated; projection uses its delivered pinhole intrinsics as an approximation.",
            "Residuals combine distortion, depth error, feature error, optical stabilization, extrinsic error and trajectory/timing error.",
            "The >0.6m depth filter reduces hand contamination but is not a semantic static-scene mask.",
            "Motion compensation uses interpolated estimated poses; these are not measured ground truth.",
            "No original timestamps, factory extrinsics or source imagery are changed."]}
    write_json(output / "cross-camera.json", report)
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.dataset, args.run, args.output)


if __name__ == "__main__":
    main()
