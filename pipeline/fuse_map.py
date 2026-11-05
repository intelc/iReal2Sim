"""Fuse measured RGB-D frames at RTAB-Map poses; export a mesh and inspection plots."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.spatial.transform import Rotation

from .rgbd import output_directory, read_rows, write_json
from .masks import DynamicMasks


def read_poses(path: Path) -> list[dict]:
    poses = []
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        values = line.split()
        matrix = np.array([float(v) for v in values[2:]]).reshape(4, 4)
        if not np.isfinite(matrix).all() or not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-5):
            raise ValueError("Invalid pose matrix")
        if not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-3):
            raise ValueError("Pose rotation is not orthogonal")
        poses.append({"id": int(values[0]), "timestamp_s": float(values[1]), "world_from_camera": matrix})
    return poses


def gyro_lag_diagnostic(poses: list[dict], source: Path, origin_ns: int) -> dict:
    """Coarse angular-speed correlation only, not a calibrated camera/IMU time shift."""
    imu = [r for r in read_rows(source / "imu.jsonl") if r["sensor"] == "gyroscope"]
    gt = np.array([(int(r["host_ns"]) - origin_ns) / 1e9 + 1 for r in imu])
    speed = np.linalg.norm([r["xyz"] for r in imu], axis=1)
    times, angular = [], []
    for a, b in zip(poses, poses[1:]):
        dt = b["timestamp_s"] - a["timestamp_s"]
        if 0 < dt <= .15:
            relative = a["world_from_camera"][:3, :3].T @ b["world_from_camera"][:3, :3]
            times.append((a["timestamp_s"] + b["timestamp_s"]) / 2)
            angular.append(np.linalg.norm(Rotation.from_matrix(relative).as_rotvec()) / dt)
    times, angular = np.asarray(times), np.asarray(angular)
    if len(times) < 30 or np.std(angular) < .1 or np.std(speed) < .1:
        return {"status": "insufficient_excitation", "applied": False}
    overlap = (times > gt[0] + .15) & (times < gt[-1] - .15)
    times, angular = times[overlap], angular[overlap]
    if len(times) < 30:
        return {"status": "insufficient_overlap", "applied": False}
    shifts = np.linspace(-.15, .15, 301)
    correlations = np.array([np.corrcoef(angular, np.interp(times + shift, gt, speed))[0, 1] for shift in shifts])
    best = int(np.nanargmax(correlations))
    near_peak = shifts[correlations >= correlations[best] - .01]
    return {"status": "coarse_diagnostic_only", "applied": False,
        "comparison": "camera angular speed at t compared with gyro magnitude at t + candidate_shift",
        "candidate_shift_ms": float(shifts[best] * 1000), "peak_correlation": float(correlations[best]),
        "zero_shift_correlation": float(correlations[150]),
        "within_0_01_of_peak_ms": [float(near_peak.min() * 1000), float(near_peak.max() * 1000)],
        "limitations": ["Visual derivative at the exported observation cadence, exposure duration, rolling shutter and tracking errors affect this estimate.",
                        "Magnitude correlation does not calibrate camera-to-IMU rotation, bias or physical sensor latency.",
                        "Original timestamps are unchanged; dedicated target-based calibration is still required."]}


def render_map_preview(cloud, poses: list[dict], path: Path, masked: bool = False):
    points = np.asarray(cloud.points)
    colors = np.clip(np.asarray(cloud.colors), 0, 1)
    centers = np.array([p["world_from_camera"][:3, 3] for p in poses])
    # Same 90-degree portrait display convention as the capture UI, plus a depth axis.
    # This is only a display basis. It is not a calibrated gravity alignment.
    display_rotation = np.array([[0, -1, 0], [0, 0, 1], [-1, 0, 0]])
    step = max(1, len(points) // 55000)
    points_view = points[::step] @ display_rotation.T
    centers_view = centers @ display_rotation.T
    fig = plt.figure(figsize=(13, 6), layout="constrained")
    ax = fig.add_subplot(121, projection="3d")
    ax.scatter(*points_view.T, c=colors[::step], s=.4, alpha=.8, rasterized=True)
    ax.plot(*centers_view.T, color="#e54035", linewidth=2)
    if len(points_view):
        low, high = np.percentile(points_view, [1, 99], axis=0)
        center = (low + high) / 2
        radius = max(high - low) / 2
        ax.set_xlim(center[0] - radius, center[0] + radius)
        ax.set_ylim(center[1] - radius, center[1] + radius)
        ax.set_zlim(center[2] - radius, center[2] + radius)
    ax.set_box_aspect((1, 1, 1))
    ax.set_title("RGB-D map + estimated camera path\nPortrait display basis; " + ("dynamic observation masks applied" if masked else "moving objects not removed"))
    ax.set_xlabel("−Y (m)"); ax.set_ylabel("Z (m)"); ax.set_zlabel("−X (m)")
    ax.view_init(elev=25, azim=-60)
    plot = fig.add_subplot(122)
    plot.plot(centers_view[:, 0], centers_view[:, 1], color="#e54035")
    plot.scatter(centers_view[0, 0], centers_view[0, 1], color="green", label="start")
    plot.scatter(centers_view[-1, 0], centers_view[-1, 1], color="black", label="end")
    plot.set_aspect("equal", adjustable="datalim")
    plot.set_xlabel("−Y (m)"); plot.set_ylabel("Z (m)")
    plot.set_title("Trajectory projection; initial camera basis"); plot.legend()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def fuse(dataset: Path, run: Path, output: Path, stride: int = 2, voxel: float = .02, masks: Path | None = None) -> dict:
    dataset, run = dataset.resolve(), run.resolve()
    if stride < 1 or not .005 <= voxel <= .1:
        raise ValueError("Require stride >=1 and voxel size in [.005,.1] metres")
    calibration = json.loads((dataset / "calibration.json").read_text())
    output = output_directory(dataset, output, Path(calibration["source"]), run)
    backend = json.loads((run / "run.json").read_text())
    frames = read_rows(dataset / "frames.jsonl")
    dynamic = DynamicMasks(masks, dataset) if masks else None
    poses = read_poses(run / "mapping" / "optimized.tsv")
    if not poses:
        raise ValueError("RTAB-Map produced no optimized camera poses")
    K = np.asarray(calibration["intrinsics"])
    intrinsic = o3d.camera.PinholeCameraIntrinsic(calibration["width"], calibration["height"], K[0, 0], K[1, 1], K[0, 2], K[1, 2])
    volume = o3d.pipelines.integration.ScalableTSDFVolume(voxel, 4 * voxel, o3d.pipelines.integration.TSDFVolumeColorType.RGB8)
    integrated = 0
    masked_pixels, valid_pixels = 0, 0
    for pose in poses[::stride]:
        frame = frames[pose["id"] - 1]
        if abs(float(frame["timestamp_s"]) - pose["timestamp_s"]) > 1e-5:
            raise ValueError("Mapper node id/timestamp does not match exported source index")
        color = cv2.cvtColor(cv2.imread(str(dataset / frame["rgb"])), cv2.COLOR_BGR2RGB)
        depth = cv2.imread(str(dataset / frame["depth"]), cv2.IMREAD_UNCHANGED)
        valid_pixels += int(np.sum(depth > 0))
        if dynamic:
            excluded = dynamic.get(pose["id"], frame, depth.shape)
            masked_pixels += int(np.sum(excluded & (depth > 0)))
            depth[excluded] = 0
        rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(o3d.geometry.Image(color), o3d.geometry.Image(depth),
            depth_scale=1000, depth_trunc=calibration["depth_max_m"], convert_rgb_to_intensity=False)
        # Open3D expects camera_from_world; the mapper explicitly writes world_from_camera.
        volume.integrate(rgbd, intrinsic, np.linalg.inv(pose["world_from_camera"]))
        integrated += 1
        if integrated % 50 == 0:
            print(f"Integrated {integrated} frames", flush=True)
    mesh = volume.extract_triangle_mesh()
    mesh.compute_vertex_normals()
    cloud = volume.extract_point_cloud()
    # TSDF color interpolation can exceed [0,1] by floating-point roundoff.
    mesh.vertex_colors = o3d.utility.Vector3dVector(np.clip(np.asarray(mesh.vertex_colors), 0, 1))
    cloud.colors = o3d.utility.Vector3dVector(np.clip(np.asarray(cloud.colors), 0, 1))
    o3d.io.write_triangle_mesh(str(output / "room-mesh.ply"), mesh)
    o3d.io.write_point_cloud(str(output / "room-cloud.ply"), cloud)
    pose_json = [{**p, "world_from_camera": p["world_from_camera"].tolist()} for p in poses]
    write_json(output / "trajectory.json", {"schema": "iphone-rsa.trajectory/1", "world_frame": "initial main-camera optical frame",
        "matrix_layout": "row-major world_from_camera", "length_unit": "metre", "pose_source": backend["backend"], "poses": pose_json})
    centers = np.array([p["world_from_camera"][:3, 3] for p in poses])
    distances = np.linalg.norm(np.diff(centers, axis=0), axis=1)
    times = np.array([p["timestamp_s"] for p in poses])
    valid_speed = distances / np.diff(times)
    render_map_preview(cloud, poses, output / "map-preview.png", masked=dynamic is not None)
    timing = gyro_lag_diagnostic(read_poses(run / "mapping" / "odometry.tsv"), Path(calibration["source"]), int(calibration["origin_host_ns"]))
    write_json(output / "camera-imu-timing.json", timing)
    report = {"schema": "iphone-rsa.reconstruction/1", "status": "initial_baseline", "backend": backend["backend"] + " + Open3D TSDF",
        "source": calibration["source"], "dataset": str(dataset), "tracking_fraction": backend["tracking_fraction"],
        "optimized_poses": len(poses), "loop_closures": backend["loop_closures"], "integrated_frames": integrated,
        "mesh_vertices": len(mesh.vertices), "mesh_triangles": len(mesh.triangles), "point_count": len(cloud.points),
        "voxel_size_m": voxel, "path_length_m": float(distances.sum()), "trajectory_extent_m": np.ptp(centers, axis=0).tolist(),
        "max_adjacent_speed_m_s": float(valid_speed.max()) if len(valid_speed) else None,
        "camera_imu_timing_diagnostic": timing,
        "dynamic_masks": str(masks.resolve()) if masks else None, "masked_valid_pixels": masked_pixels,
        "fraction_valid_depth_excluded": masked_pixels / max(valid_pixels, 1),
        "limitations": ["No ground-truth trajectory or surveyed dimensions have been used.",
            "Dynamic masks are estimated and can miss or over-mask objects; residual ghost geometry is possible." if dynamic else "Dynamic hands and objects are not removed; ghost geometry is possible.", "World axes follow the initial camera, not calibrated gravity.",
            "Camera-to-IMU calibration and ultra-wide distortion calibration remain unresolved."]}
    write_json(output / "reconstruction.json", report)
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stride", type=int, default=2)
    parser.add_argument("--voxel", type=float, default=.02)
    parser.add_argument("--masks", type=Path)
    args = parser.parse_args()
    fuse(args.dataset, args.run, args.output, args.stride, args.voxel, args.masks)


if __name__ == "__main__":
    main()
