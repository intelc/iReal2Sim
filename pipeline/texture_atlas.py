"""Bake visible, unmasked native RGB observations onto the measured triangle mesh."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d
from PIL import Image
import trimesh
from .gltf_material import captured_rgb_unlit_glb

from .fuse_map import read_poses
from .masks import DynamicMasks
from .rgbd import Recording, VideoReader, output_directory, read_rows, remap_for_camera, write_json


def visible_points(points, camera_from_world, K, measured, excluded, rendered):
    """Require measured depth AND mesh visibility, so foreground cannot paint a wall."""
    camera = points @ camera_from_world[:3, :3].T + camera_from_world[:3, 3]
    z = camera[:, 2]
    safe_z = np.maximum(z, .001)
    pixels = camera @ K.T
    pixels = pixels[:, :2] / safe_z[:, None]
    uv = np.rint(pixels).astype(np.int32)
    inside = (z > .2) & (uv[:, 0] >= 2) & (uv[:, 0] < measured.shape[1] - 2) & (uv[:, 1] >= 2) & (uv[:, 1] < measured.shape[0] - 2)
    u = np.clip(uv[:, 0], 0, measured.shape[1] - 1)
    v = np.clip(uv[:, 1], 0, measured.shape[0] - 1)
    sample = measured[v, u]
    first_hit = rendered[v, u]
    tolerance = .035 + .015 * z
    visible = inside & ~excluded[v, u] & (sample > .2) & np.isfinite(first_hit)
    visible &= (np.abs(sample - z) <= tolerance) & (np.abs(first_hit - z) <= tolerance)
    return visible, pixels, z


def smooth_face_views(scores, faces, vertices, iterations=2):
    """Encourage neighboring triangles to share a valid view without inventing visibility."""
    labels = scores.argmax(axis=1)
    best = scores.max(axis=1)
    labels[best <= 0] = -1
    adjacency = trimesh.Trimesh(vertices=vertices, faces=faces, process=False).face_adjacency
    neighbors = np.tile(np.arange(len(faces))[:, None], (1, 3))
    slots = np.zeros(len(faces), dtype=np.int8)
    for pairs in (adjacency, adjacency[:, ::-1]):
        source, target = pairs.T
        order = np.argsort(source, kind="stable")
        a, b = source[order], target[order]
        # A manifold triangle has at most three edge-neighbors.
        offsets = np.arange(len(a)) - np.repeat(np.flatnonzero(np.r_[True, a[1:] != a[:-1]]), np.diff(np.r_[np.flatnonzero(np.r_[True, a[1:] != a[:-1]]), len(a)]))
        column = slots[a] + offsets
        valid = column < 3
        neighbors[a[valid], column[valid]] = b[valid]
        np.add.at(slots, a, 1)
    row = np.arange(len(faces))
    for _ in range(iterations):
        nearby = labels[neighbors]
        chosen = labels.copy()
        current = scores[row, np.maximum(labels, 0)] * (1 + .35 * np.sum(nearby == labels[:, None], axis=1))
        for col in range(3):
            candidate = nearby[:, col]
            unary = scores[row, np.maximum(candidate, 0)]
            weighted = unary * (1 + .35 * np.sum(nearby == candidate[:, None], axis=1))
            change = (candidate >= 0) & (unary >= .6 * best) & (unary > 0) & (weighted > current)
            chosen[change], current[change] = candidate[change], weighted[change]
        labels = chosen
    return labels


def native_rectified_color(recording, reader, row, K, size, main_rows, depth_rows):
    main = main_rows[row["main_segment"], row["main_frame_index"]]
    depth = depth_rows[row["depth_segment"], row["depth_frame_index"]]
    color = reader.frame(main)
    mx, my = remap_for_camera(K, size, np.array(main["intrinsics"]), (main["width"], main["height"]), depth["calibration"])
    return cv2.remap(color, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


def build_atlas(dataset: Path, run: Path, mesh_path: Path, masks: Path | None, output: Path, size: int = 8192, interval: int = 10, color_balance: bool = False, view_selection: str = "uniform"):
    dataset, run, mesh_path = dataset.resolve(), run.resolve(), mesh_path.resolve()
    cfg = json.loads((dataset / "calibration.json").read_text())
    output = output_directory(dataset, output, Path(cfg["source"]), run, mesh_path.parent, *([masks] if masks else []))
    dynamic = DynamicMasks(masks, dataset) if masks else None
    def excluded_at(pose, row, shape):
        return dynamic.get(pose["id"], row, shape) if dynamic else np.zeros(shape, dtype=bool)
    rows = read_rows(dataset / "frames.jsonl")
    poses = read_poses(run / "mapping/optimized.tsv")
    recording = Recording(Path(cfg["source"]))
    native = recording.frames["main"][0]
    native_size = (native["width"], native["height"])
    scale = np.diag([native_size[0] / cfg["width"], native_size[1] / cfg["height"], 1])
    K, native_K = np.array(cfg["intrinsics"]), scale @ np.array(cfg["intrinsics"])
    mesh = o3d.io.read_triangle_mesh(str(mesh_path))
    mesh.compute_triangle_normals()
    vertices, faces = np.asarray(mesh.vertices), np.asarray(mesh.triangles)
    colors = np.clip(np.asarray(mesh.vertex_colors), 0, 1)
    centers = vertices[faces].mean(axis=1)
    normals = np.asarray(mesh.triangle_normals)
    fallback_height = int(np.ceil(len(faces) / size)) + 8
    gutter = 4
    tile_width, tile_height = native_size[0] + 2 * gutter, native_size[1] + 2 * gutter
    columns = size // tile_width
    capacity = columns * ((size - fallback_height) // tile_height)
    if capacity < 1:
        raise ValueError("Atlas is too small for a native RGB image")
    candidates = []
    for start in range(0, len(poses), interval):
        group = poses[start:start + interval]
        options = []
        for pose in group:
            row = rows[pose["id"] - 1]
            color = cv2.imread(str(dataset / row["rgb"]))
            excluded = excluded_at(pose, row, color.shape[:2])
            gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
            gradient = cv2.Laplacian(gray, cv2.CV_32F)
            sharpness = float(np.var(gradient[~excluded])) if np.mean(~excluded) > .5 else 0.
            options.append((sharpness, pose))
        sharpness, pose = max(options, key=lambda x: x[0])
        candidates.append({**pose, "sharpness": sharpness})
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(mesh))
    if len(candidates) > capacity:
        if view_selection == "coverage":
            sample = np.linspace(0,len(faces)-1,min(20000,len(faces))).round().astype(int)
            sample_centers=centers[sample];sample_normals=normals[sample]
            area=np.linalg.norm(np.cross(vertices[faces[sample,1]]-vertices[faces[sample,0]],vertices[faces[sample,2]]-vertices[faces[sample,0]]),axis=1)/2
            quality=np.zeros((len(sample),len(candidates)),np.float32)
            for col,p in enumerate(candidates):
                row=rows[p['id']-1];measured=cv2.imread(str(dataset/row['depth']),-1).astype(np.float32)/1000
                excluded=excluded_at(p,row,measured.shape);inverse=np.linalg.inv(p['world_from_camera'])
                rays=scene.create_rays_pinhole(o3d.core.Tensor(K,dtype=o3d.core.Dtype.Float64),o3d.core.Tensor(inverse,dtype=o3d.core.Dtype.Float64),cfg['width'],cfg['height'])
                predicted=scene.cast_rays(rays)['t_hit'].numpy()
                visible,_,z=visible_points(sample_centers,inverse,K,measured,excluded,predicted)
                direction=p['world_from_camera'][:3,3]-sample_centers;direction/=np.maximum(np.linalg.norm(direction,axis=1,keepdims=True),1e-8)
                incidence=np.abs(np.sum(sample_normals*direction,axis=1))
                quality[visible,col]=incidence[visible]**2/np.maximum(z[visible]**2,.1)
            quality/=np.maximum(quality.max(axis=1,keepdims=True),1e-8)
            selected=[];best=np.zeros(len(sample))
            for _ in range(capacity):
                gain=(np.maximum(quality-best[:,None],0)*area[:,None]).sum(axis=0)
                gain[selected]=-1;chosen=int(np.argmax(gain));selected.append(chosen);best=np.maximum(best,quality[:,chosen])
            candidates=[candidates[i] for i in sorted(selected)]
        else:
            candidates = [candidates[i] for i in np.linspace(0, len(candidates) - 1, capacity).round().astype(int)]
    print(f"Atlas: {len(faces)} faces, {len(candidates)} native RGB views, {size}×{size}", flush=True)
    scores = np.zeros((len(faces), len(candidates)), dtype=np.float32)
    median_sharpness = max(float(np.median([p["sharpness"] for p in candidates])), 1)
    for column, pose in enumerate(candidates):
        row = rows[pose["id"] - 1]
        measured = cv2.imread(str(dataset / row["depth"]), -1).astype(np.float32) / 1000
        excluded = excluded_at(pose, row, measured.shape)
        # Additional margin prevents thin dynamic edges entering a texture chart.
        excluded = cv2.dilate(excluded.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        inverse = np.linalg.inv(pose["world_from_camera"])
        rays = scene.create_rays_pinhole(o3d.core.Tensor(K), o3d.core.Tensor(inverse), cfg["width"], cfg["height"])
        rendered = scene.cast_rays(rays)["t_hit"].numpy()
        visible_v, _, _ = visible_points(vertices, inverse, K, measured, excluded, rendered)
        visible_c, _, z = visible_points(centers, inverse, K, measured, excluded, rendered)
        visible = visible_c & np.all(visible_v[faces], axis=1)
        direction = pose["world_from_camera"][:3, 3] - centers
        direction /= np.maximum(np.linalg.norm(direction, axis=1, keepdims=True), 1e-8)
        incidence = np.abs(np.sum(normals * direction, axis=1))
        quality = np.clip(pose["sharpness"] / median_sharpness, .4, 1.8)
        scores[visible, column] = (incidence[visible] ** 2 * quality / np.maximum(z[visible] ** 2, .1)).astype(np.float32)
        if (column + 1) % 10 == 0:
            print(f"Visibility scored {column+1}/{len(candidates)} views", flush=True)
    assignment = smooth_face_views(scores, faces, vertices)
    del scores
    np.save(output / "face-view-indices.npy", assignment)
    atlas = np.zeros((size, size, 3), dtype=np.uint8)
    reader = VideoReader(recording, "main")
    main_rows = {(r["segment"], r["frame_index"]): r for r in recording.frames["main"]}
    depth_rows = {(r["segment"], r["frame_index"]): r for r in recording.frames["depth"]}
    for i, pose in enumerate(candidates):
        color = native_rectified_color(recording, reader, rows[pose["id"] - 1], native_K, native_size, main_rows, depth_rows)
        if color_balance:
            from scipy.optimize import least_squares
            selected = np.flatnonzero(assignment == i)
            selected = selected[np.linspace(0,len(selected)-1,min(5000,len(selected))).round().astype(int)] if len(selected) else selected
            gain, bias = np.ones(3), np.zeros(3)
            if len(selected) > 100:
                transform = np.linalg.inv(pose["world_from_camera"])
                points = centers[selected] @ transform[:3,:3].T + transform[:3,3]
                pixels = points @ K.T
                pixels = np.rint(pixels[:,:2] / pixels[:,2:]).astype(int)
                source = cv2.cvtColor(cv2.imread(str(dataset / rows[pose["id"]-1]["rgb"])),cv2.COLOR_BGR2RGB)[pixels[:,1],pixels[:,0]] / 255.
                reference = colors[faces[selected]].mean(axis=1)
                keep = (source.min(axis=1)>.05)&(source.max(axis=1)<.95)&(reference.min(axis=1)>.05)&(reference.max(axis=1)<.95)
                if keep.sum()>100:
                    fit = least_squares(lambda x:(source[keep]*x[:3]+x[3:]-reference[keep]).ravel(),np.r_[gain,bias],bounds=([.7]*3+[-.12]*3,[1.3]*3+[.12]*3),loss='soft_l1',f_scale=.03)
                    gain,bias=fit.x[:3],fit.x[3:]
            pose["color_gain_rgb"],pose["color_bias_rgb"] = gain.tolist(),bias.tolist()
            # Input is BGR; balance in RGB channel order and preserve native detail.
            color=np.uint8(np.clip((color[...,::-1]/255.)*gain+bias,0,1)[...,::-1]*255)
        x, y = (i % columns) * tile_width, (i // columns) * tile_height
        atlas[y:y + tile_height, x:x + tile_width] = cv2.copyMakeBorder(cv2.cvtColor(color, cv2.COLOR_BGR2RGB), gutter, gutter, gutter, gutter, cv2.BORDER_REPLICATE)
    reader.close()
    # Each fallback face samples one fused mean color. UVs stay constant on that
    # face, avoiding interpolation through unrelated fallback texels.
    face_ids = np.arange(len(faces))
    fallback_u, fallback_v = face_ids % size, size - fallback_height + face_ids // size
    atlas[fallback_v, fallback_u] = np.rint(colors[faces].mean(axis=1) * 255).astype(np.uint8)
    # Split shared vertices only where their source-camera chart changes.
    keys = faces.astype(np.int64) * (len(candidates) + 1) + (assignment[:, None] + 1)
    cutoff = len(vertices) * (len(candidates) + 1)
    fallback_faces = assignment < 0
    keys[fallback_faces] = cutoff + face_ids[fallback_faces, None] * 3 + np.arange(3)
    unique, inverse = np.unique(keys, return_inverse=True)
    original = unique // (len(candidates) + 1)
    view_id = unique % (len(candidates) + 1) - 1
    fallback = unique >= cutoff
    original[fallback] = faces.ravel()[unique[fallback] - cutoff]
    view_id[fallback] = -1
    uv = np.empty((len(unique), 2), dtype=np.float32)
    fallback_face_ids = (unique[fallback] - cutoff) // 3
    uv[fallback, 0] = (fallback_u[fallback_face_ids] + .5) / size
    uv[fallback, 1] = 1 - (fallback_v[fallback_face_ids] + .5) / size
    for i, pose in enumerate(candidates):
        selected = view_id == i
        points = vertices[original[selected]]
        camera = points @ np.linalg.inv(pose["world_from_camera"])[:3, :3].T + np.linalg.inv(pose["world_from_camera"])[:3, 3]
        pixels = camera @ native_K.T
        pixels = pixels[:, :2] / pixels[:, 2:]
        x, y = (i % columns) * tile_width + gutter, (i // columns) * tile_height + gutter
        uv[selected, 0] = (pixels[:, 0] + x + .5) / size
        uv[selected, 1] = 1 - (pixels[:, 1] + y + .5) / size
    packed_faces = inverse.reshape(-1, 3).astype(np.uint32)
    packed_vertices = vertices[original].astype(np.float32)
    packed_colors = colors[original].astype(np.float32)
    Image.fromarray(atlas).save(output / "atlas.png")
    np.savez_compressed(output / "textured-mesh.npz", vertices=packed_vertices, faces=packed_faces, uv=uv, colors=packed_colors)
    visual = trimesh.visual.texture.TextureVisuals(uv=uv, image=Image.fromarray(atlas))
    textured = trimesh.Trimesh(vertices=packed_vertices, faces=packed_faces, visual=visual, process=False)
    textured.export(output / "room-textured.glb")
    captured_rgb_unlit_glb(output / "room-textured.glb")
    area_per_face = np.linalg.norm(np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]], vertices[faces[:, 2]] - vertices[faces[:, 0]]), axis=1) / 2
    report = {"schema": "iphone-rsa.texture-atlas/1", "status": "complete", "mesh": str(mesh_path), "dataset": str(dataset),
        "run": str(run), "masks": str(masks.resolve()) if masks else None, "atlas_size": size, "native_rgb_size": list(native_size),
        "source_views": [{"frame_id": p["id"], "timestamp_s": p["timestamp_s"], "sharpness": p["sharpness"],"color_gain_rgb":p.get("color_gain_rgb"),"color_bias_rgb":p.get("color_bias_rgb")} for p in candidates],
        "triangles": len(faces), "textured_triangle_fraction": float(np.mean(assignment >= 0)),
        "textured_observed_mesh_area_fraction": float(area_per_face[assignment >= 0].sum() / area_per_face.sum()),
        "geometry_modified": False, "method": "Visibility-gated native RGB camera charts packed into one atlas; neighboring view labels smoothed",
        "color_balance": "Robust per-view RGB gain/bias against training-only fused face colors" if color_balance else None,
        "view_selection": view_selection,
        "limitations": ["Dynamic masks applied." if dynamic else "No dynamic masks applied; use only on reviewed static segments.",
            "Texture coverage describes the existing mesh, not completeness of the room.",
            "Unknown geometry remains absent; this does not complete holes or synthesize textures.",
            "Residual camera errors, illumination changes and chart boundaries can produce seams.",
            "Faces without a valid camera observation retain mean fused face colors via fallback texels."]}
    write_json(output / "texture.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "source_views"}, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("run", type=Path)
    parser.add_argument("mesh", type=Path)
    parser.add_argument("--masks", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, choices=[4096, 8192, 16384], default=8192)
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--color-balance", action="store_true")
    parser.add_argument("--view-selection", choices=["uniform","coverage"], default="uniform")
    args = parser.parse_args()
    if args.interval < 1:
        parser.error("--interval must be positive")
    build_atlas(args.dataset, args.run, args.mesh, args.masks, args.output, args.size, args.interval, args.color_balance,args.view_selection)


if __name__ == "__main__":
    main()
