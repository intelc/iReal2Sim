"""Package a dependency-free local WebGL mesh viewer with recorded camera views."""
from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d
from PIL import Image

from .fuse_map import read_poses
from .rgbd import output_directory, read_rows, write_json
from .timeline import build_timeline


def encode_array(array: np.ndarray, dtype: str):
    return base64.b64encode(np.asarray(array, dtype=dtype).tobytes()).decode("ascii")


def build_viewer(dataset: Path, run: Path, mesh_path: Path, output: Path, inspection: Path | None = None, texture_bundle: Path | None = None, timeline: bool = False, masks: Path | None = None, tracks: Path | None = None):
    dataset, run, mesh_path = dataset.resolve(), run.resolve(), mesh_path.resolve()
    cfg = json.loads((dataset / "calibration.json").read_text())
    output = output_directory(dataset, output, Path(cfg["source"]), run, mesh_path.parent)
    rows = read_rows(dataset / "frames.jsonl")
    poses = read_poses(run / "mapping/optimized.tsv")
    metrics = {}
    if inspection:
        metrics = {r["frame_id"]: r for r in json.loads(inspection.read_text())["samples"] if r["status"] == "rendered"}
    if metrics:
        by_id = {p["id"]:p for p in poses}
        chosen = [by_id.get(i) or {"id": i, "timestamp_s": v["timestamp_s"], "world_from_camera": np.array(v["world_from_camera"])} for i,v in metrics.items()]
    else:
        chosen = [poses[i] for i in np.linspace(0, len(poses) - 1, 18).round().astype(int)]
    views = []
    for pose in chosen:
        row = rows[pose["id"] - 1]
        if abs(float(row["timestamp_s"]) - pose["timestamp_s"]) > 1e-5:
            raise ValueError("Source image and mesh camera timestamp disagree")
        rgb = cv2.imread(str(dataset / row["rgb"]))
        rotated = cv2.rotate(cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB), cv2.ROTATE_90_CLOCKWISE)
        image = Image.fromarray(rotated)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88)
        depth = cv2.imread(str(dataset / row["depth"]), -1) / 1000
        h, w = depth.shape
        patch = depth[h // 2 - 10:h // 2 + 10, w // 2 - 10:w // 2 + 10]
        focus = float(np.median(patch[patch > 0])) if np.any(patch > 0) else 2.
        views.append({"id": pose["id"], "seconds": pose["timestamp_s"] - 1,
                      "pose": pose["world_from_camera"].tolist(), "focus": focus,
                      "rgb": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
                      "median_depth_error_m": metrics.get(pose["id"], {}).get("median_abs_depth_error_m")})
    if not views:
        raise ValueError("No tracked camera views available")
    atlas = None
    if texture_bundle:
        texture_report = json.loads((texture_bundle / "texture.json").read_text())
        if Path(texture_report["mesh"]).resolve() != mesh_path:
            raise ValueError("Texture bundle belongs to a different mesh")
        packed = np.load(texture_bundle / "textured-mesh.npz")
        vertices, triangles, colors, uv = packed["vertices"], packed["faces"], packed["colors"], packed["uv"]
        atlas = "data:image/png;base64," + base64.b64encode((texture_bundle / "atlas.png").read_bytes()).decode("ascii")
    else:
        mesh = o3d.io.read_triangle_mesh(str(mesh_path))
        vertices, triangles = np.asarray(mesh.vertices), np.asarray(mesh.triangles)
        colors = np.clip(np.asarray(mesh.vertex_colors), 0, 1)
        uv = np.zeros((len(vertices), 2), dtype=np.float32)
    if len(colors) != len(vertices):
        colors = np.full_like(vertices, .7)
    payload = {"positions": encode_array(vertices, "<f4"), "colors": encode_array(np.rint(colors * 255), "u1"),
               "indices": encode_array(triangles, "<u4"), "indexCount": int(triangles.size),
               "uv": encode_array(uv, "<f4"), "atlas": atlas,
               "K": cfg["intrinsics"], "width": cfg["width"], "height": cfg["height"], "views": views,
               "masking_applied": bool(texture_report.get("masks")) if texture_bundle else False,
               "timeline": build_timeline(dataset, output, rows, poses, masks) if timeline else None}
    if tracks:
        track_report=json.loads((tracks/'tracks-report.json').read_text())
        if Path(track_report['dataset']).resolve()!=dataset or Path(track_report['run']).resolve()!=run:
            raise ValueError('Interaction tracks belong to a different dataset or trajectory')
        shutil.copyfile(tracks/'tracks.json',output/'interaction-tracks.json')
        payload['tracks']='interaction-tracks.json'
    template = Path(__file__).with_name("viewer.html").read_text()
    if timeline:
        for name in ("positions", "colors", "indices", "uv"):
            (output / f"{name}.bin").write_bytes(base64.b64decode(payload[name]))
            payload[name] = {"url": f"{name}.bin"}
        if atlas:
            (output / "atlas.png").symlink_to((texture_bundle / "atlas.png").resolve())
            payload["atlas"] = "atlas.png"
        write_json(output / "scene.json", payload)
        encoded = "await (await fetch('scene.json')).json()"
    else:
        encoded = json.dumps(payload, allow_nan=False)
    (output / "viewer.html").write_text(template.replace("__PAYLOAD__", encoded))
    write_json(output / "viewer.json", {"schema": "iphone-rsa.mesh-viewer/1", "mesh": str(mesh_path),
               "run": str(run), "dataset": str(dataset), "vertices": len(vertices), "triangles": len(triangles),
               "camera_views": len(views), "rendering": "native RGB texture atlas with fused-color toggle" if atlas else "fused vertex colors; original estimated camera poses",
               "geometry_modified": False, "network_required": False, "local_http_required": timeline,
               "timeline_frames": len(payload["timeline"]["frames"]) if timeline else 0})
    print(output / "viewer.html")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("run", type=Path)
    parser.add_argument("mesh", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inspection", type=Path)
    parser.add_argument("--texture-bundle", type=Path)
    parser.add_argument("--timeline", action="store_true")
    parser.add_argument("--masks", type=Path)
    parser.add_argument("--tracks", type=Path)
    args = parser.parse_args()
    build_viewer(args.dataset, args.run, args.mesh, args.output, args.inspection, args.texture_bundle, args.timeline, args.masks,args.tracks)


if __name__ == "__main__":
    main()
