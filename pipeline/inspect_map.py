"""Render a measured mesh from recorded cameras and quantify depth consistency."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw

from .fuse_map import read_poses
from .rgbd import output_directory, read_rows, write_json
from .masks import DynamicMasks
from .cross_camera_report import pose_at


def evaluation_poses(frames: list[dict], poses: list[dict], split: dict) -> list[dict]:
    """Withheld RGB-D samples use interpolated training poses, never a fitted pose."""
    train, held = set(split["train_frame_ids"]), split["evaluation_frame_ids"]
    if train.intersection(held) or any(p["id"] not in train for p in poses):
        raise ValueError("Evaluation samples leaked into the training trajectory")
    poses = sorted(poses, key=lambda p: p["timestamp_s"])
    result = []
    for frame_id in held:
        if not 1 <= frame_id <= len(frames):
            raise ValueError("Evaluation frame ID is outside the dataset")
        time = float(frames[frame_id - 1]["timestamp_s"])
        result.append({"id": frame_id, "timestamp_s": time, "world_from_camera": pose_at(poses, time, .15)})
    return result


class MeshRenderer:
    """CPU ray casting; no display server, lighting, or generated scene content."""
    def __init__(self, mesh: o3d.geometry.TriangleMesh, uv: np.ndarray | None = None, texture: np.ndarray | None = None):
        self.scene = o3d.t.geometry.RaycastingScene()
        self.scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(mesh))
        self.triangles = np.asarray(mesh.triangles)
        self.colors = np.clip(np.asarray(mesh.vertex_colors), 0, 1)
        if not len(self.colors):
            self.colors = np.full((len(mesh.vertices), 3), .7)
        self.uv, self.texture = uv, texture

    def render(self, K: np.ndarray, world_from_camera: np.ndarray, size: tuple[int, int]):
        width, height = size
        rays = self.scene.create_rays_pinhole(
            o3d.core.Tensor(K, dtype=o3d.core.Dtype.Float64),
            o3d.core.Tensor(np.linalg.inv(world_from_camera), dtype=o3d.core.Dtype.Float64), width, height)
        result = self.scene.cast_rays(rays)
        # Pinhole rays have camera Z=1. t_hit is axial depth, not Euclidean range.
        depth = result["t_hit"].numpy()
        hit = np.isfinite(depth)
        ids = result["primitive_ids"].numpy()[hit]
        barycentric = result["primitive_uvs"].numpy()[hit]
        weights = np.column_stack([1 - barycentric.sum(axis=1), barycentric])
        colors = (self.colors[self.triangles[ids]] * weights[..., None]).sum(axis=1)
        if self.texture is not None:
            uv = (self.uv[self.triangles[ids]] * weights[..., None]).sum(axis=1)
            colors = sample_texture(self.texture, uv) / 255.
        image = np.full((height, width, 3), 238, dtype=np.uint8)
        image[hit] = np.rint(np.clip(colors, 0, 1) * 255).astype(np.uint8)
        return image, depth


def sample_texture(texture: np.ndarray, uv: np.ndarray):
    """Bilinear sampling of OpenGL UVs (V up) from an RGB image (rows down)."""
    x = np.clip(uv[:, 0] * texture.shape[1] - .5, 0, texture.shape[1] - 1)
    y = np.clip((1 - uv[:, 1]) * texture.shape[0] - .5, 0, texture.shape[0] - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, texture.shape[1] - 1), np.minimum(y0 + 1, texture.shape[0] - 1)
    a, b = (x - x0)[:, None], (y - y0)[:, None]
    return (1-b)*((1-a)*texture[y0,x0]+a*texture[y0,x1])+b*((1-a)*texture[y1,x0]+a*texture[y1,x1])


def depth_metrics(measured: np.ndarray, rendered: np.ndarray, mask: np.ndarray | None = None):
    valid = np.isfinite(measured) & (measured > 0)
    if mask is not None:
        valid &= mask
    hit = valid & np.isfinite(rendered) & (rendered > 0)
    error = np.abs(measured[hit] - rendered[hit])
    count = int(valid.sum())
    return {"measured_pixels": count, "mesh_hit_pixels": int(hit.sum()),
            "mesh_coverage": float(hit.sum() / count) if count else None,
            "median_abs_depth_error_m": float(np.median(error)) if len(error) else None,
            "p90_abs_depth_error_m": float(np.percentile(error, 90)) if len(error) else None,
            # Missing mesh pixels count as failures, so deleting surfaces cannot improve this score.
            "fraction_measured_within_5cm": float(np.sum(error <= .05) / count) if count else None}


def labelled_comparison(rgb: np.ndarray, rendered: np.ndarray, measured: np.ndarray,
                        predicted: np.ndarray, label: str) -> Image.Image:
    error = np.abs(measured - predicted)
    heat = cv2.applyColorMap(np.uint8(np.clip(error / .3, 0, 1) * 255), cv2.COLORMAP_INFERNO)[..., ::-1]
    heat[~np.isfinite(predicted) | (measured <= 0)] = [220, 220, 220]
    views = [cv2.rotate(a, cv2.ROTATE_90_CLOCKWISE) for a in (rgb, rendered, heat)]
    width, height = views[0].shape[1], views[0].shape[0]
    panel = Image.new("RGB", (width * 3, height + 72), "#f4f3ef")
    draw = ImageDraw.Draw(panel)
    draw.text((10, 8), label, fill="#222222")
    for index, (title, view) in enumerate(zip(["Recorded RGB", "Fused mesh · same camera", "Depth error · black 0 / yellow ≥30 cm"], views)):
        draw.text((index * width + 10, 37), title, fill="#222222")
        panel.paste(Image.fromarray(view), (index * width, 72))
    return panel


def inspect(dataset: Path, run: Path, mesh_path: Path, output: Path, samples: int = 18, texture_bundle: Path | None = None, masks: Path | None = None, evaluation_split: Path | None = None):
    dataset, run, mesh_path = dataset.resolve(), run.resolve(), mesh_path.resolve()
    cfg = json.loads((dataset / "calibration.json").read_text())
    output = output_directory(dataset, output, Path(cfg["source"]), run, mesh_path.parent)
    frames = read_rows(dataset / "frames.jsonl")
    dynamic = DynamicMasks(masks, dataset) if masks else None
    poses = read_poses(run / "mapping/optimized.tsv")
    split = json.loads(evaluation_split.read_text()) if evaluation_split else None
    if split:
        if Path(split["dataset"]).resolve() != dataset:
            raise ValueError("Evaluation split belongs to a different dataset")
        selected = evaluation_poses(frames, poses, split)
    else:
        indices = np.unique(np.linspace(0, len(poses) - 1, min(samples, len(poses))).round().astype(int))
        selected = [poses[i] for i in indices]
    if texture_bundle:
        packed = np.load(texture_bundle / "textured-mesh.npz")
        mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(packed["vertices"]), o3d.utility.Vector3iVector(packed["faces"]))
        mesh.vertex_colors = o3d.utility.Vector3dVector(packed["colors"])
        renderer = MeshRenderer(mesh, packed["uv"], np.array(Image.open(texture_bundle / "atlas.png").convert("RGB")))
    else:
        renderer = MeshRenderer(o3d.io.read_triangle_mesh(str(mesh_path)))
    K = np.array(cfg["intrinsics"])
    records, panels = [], []
    for pose in selected:
        frame_id = pose["id"]
        frame = frames[frame_id - 1]
        if pose["world_from_camera"] is None:
            records.append({"frame_id": frame_id, "status": "trajectory_gap"})
            continue
        if abs(float(frame["timestamp_s"]) - pose["timestamp_s"]) > 1e-5:
            raise ValueError("Camera pose and source frame timestamp disagree")
        rgb = cv2.cvtColor(cv2.imread(str(dataset / frame["rgb"])), cv2.COLOR_BGR2RGB)
        measured = cv2.imread(str(dataset / frame["depth"]), -1).astype(np.float32) / 1000
        rendered, predicted = renderer.render(K, pose["world_from_camera"], (cfg["width"], cfg["height"]))
        metrics = depth_metrics(measured, predicted)
        record = {"frame_id": pose["id"], "timestamp_s": pose["timestamp_s"], "status": "rendered", **metrics}
        width = measured.shape[1]
        record["portrait_depth_bands"] = {name: depth_metrics(measured[:, a:b], predicted[:, a:b])
                for name, a, b in [("top", 0, width // 5), ("middle", width // 5, width * 4 // 5), ("bottom", width * 4 // 5, width)]}
        valid_rgb = np.any(rgb > 0, axis=2)
        mse = np.mean((rgb.astype(float)[valid_rgb] - rendered.astype(float)[valid_rgb]) ** 2)
        record["rgb_psnr_db"] = float(10 * np.log10(255. ** 2 / max(mse, 1e-12)))
        record["world_from_camera"] = pose["world_from_camera"].tolist()
        record["pose_source"] = "interpolated adjacent training poses; <=0.15s gap" if split else "training pose"
        if dynamic:
            record["static_depth_metrics"] = depth_metrics(measured, predicted, ~dynamic.get(pose["id"], frame, measured.shape))
        records.append(record)
        error_text = f"{metrics['median_abs_depth_error_m'] * 100:.1f} cm" if metrics['median_abs_depth_error_m'] is not None else "no mesh overlap"
        label = f"{pose['timestamp_s'] - 1:.2f}s | median depth disagreement {error_text}"
        panel = labelled_comparison(rgb, rendered, measured, predicted, label)
        filename = f"view-{pose['id']:04d}.jpg"
        panel.save(output / filename, quality=92)
        record["image"] = filename
        panels.append(panel)
    if panels:
        chosen = [panels[i] for i in np.unique(np.linspace(0, len(panels) - 1, min(6, len(panels))).round().astype(int))]
        thumb_width = 810
        thumbs = [p.resize((thumb_width, round(p.height * thumb_width / p.width))) for p in chosen]
        sheet = Image.new("RGB", (thumb_width * 2, max(p.height for p in thumbs) * ((len(thumbs) + 1) // 2)), "#f4f3ef")
        for i, panel in enumerate(thumbs):
            sheet.paste(panel, ((i % 2) * thumb_width, (i // 2) * thumbs[0].height))
        sheet.save(output / "comparison.jpg", quality=92)
    successful = [r for r in records if r["status"] == "rendered"]
    report = {"schema": "iphone-rsa.mesh-inspection/1", "dataset": str(dataset), "run": str(run),
              "mesh": str(mesh_path), "texture_bundle": str(texture_bundle.resolve()) if texture_bundle else None, "samples": records,
              "median_of_view_median_depth_error_m": float(np.median([r["median_abs_depth_error_m"] for r in successful if r["median_abs_depth_error_m"] is not None])) if any(r["median_abs_depth_error_m"] is not None for r in successful) else None,
              "mean_view_fraction_within_5cm": float(np.mean([r["fraction_measured_within_5cm"] for r in successful])) if successful else None,
              "evaluation_split": str(evaluation_split.resolve()) if evaluation_split else None,
              "mean_view_rgb_psnr_db": float(np.mean([r["rgb_psnr_db"] for r in successful])) if successful else None,
              "limitations": ["RGB-D views were withheld from fitting, but their camera poses are interpolated from the training trajectory; this is not independent physical ground truth." if split else "This is internal consistency against the input depth, not held-out or ground-truth accuracy.",
                              "RGB PSNR includes missing geometry (gray background); exposure differences and low-resolution depth limit interpretation.",
                              "Moving objects, occlusion, missing depth and TSDF interpolation affect the residuals.",
                              "Texture is sampled from visible recorded RGB." if texture_bundle else "RGB is shown for comparison; the reconstruction uses fused vertex colors, not a texture atlas."]}
    if dynamic:
        report["masks"] = str(masks.resolve())
        report["static_median_of_view_median_depth_error_m"] = float(np.median([r["static_depth_metrics"]["median_abs_depth_error_m"] for r in successful]))
    write_json(output / "inspection.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("run", type=Path)
    parser.add_argument("mesh", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=18)
    parser.add_argument("--texture-bundle", type=Path)
    parser.add_argument("--masks", type=Path)
    parser.add_argument("--evaluation-split", type=Path)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    inspect(args.dataset, args.run, args.mesh, args.output, args.samples, args.texture_bundle, args.masks, args.evaluation_split)


if __name__ == "__main__":
    main()
