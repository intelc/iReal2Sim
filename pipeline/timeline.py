"""Build a timed, rectified RGB preview without treating encoder FPS as source time."""
import json
from pathlib import Path
import subprocess

import numpy as np

from .cross_camera_report import pose_at


def build_timeline(dataset: Path, output: Path, rows: list[dict], poses: list[dict], masks: Path | None = None):
    first, last = poses[0]["timestamp_s"], poses[-1]["timestamp_s"]
    selected = [r for r in rows if first <= float(r["timestamp_s"]) <= last]
    stamp0 = float(selected[0]["timestamp_s"])
    lines = ["ffconcat version 1.0"]
    for i, row in enumerate(selected):
        path = str((dataset / row["rgb"]).resolve()).replace("'", "'\\''")
        duration = float(selected[i + 1]["timestamp_s"]) - float(row["timestamp_s"]) if i + 1 < len(selected) else .034
        lines += [f"file '{path}'", "option framerate 1000", f"duration {duration:.9f}"]
    concat = output / "timeline.ffconcat"
    concat.write_text("\n".join(lines) + "\n")
    subprocess.run(["ffmpeg", "-v", "error", "-safe", "0", "-f", "concat", "-i", str(concat),
                    "-vf", "transpose=1", "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p",
                    "-fps_mode", "vfr", "-video_track_timescale", "1000000", "-movflags", "+faststart", str(output / "source-preview.mp4")], check=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(output / "source-preview.mp4")], capture_output=True, text=True, check=True)
    pts = [float(f["best_effort_timestamp_time"]) for f in json.loads(probe.stdout)["frames"]]
    expected = np.array([float(r["timestamp_s"]) - stamp0 for r in selected])
    if len(pts) != len(selected) or np.max(np.abs(np.array(pts) - expected)) > .002:
        raise ValueError("Preview changed frame count or timing by >2ms")
    if masks:
        (output / "masks").symlink_to(masks.resolve() / "masks", target_is_directory=True)
    return {"video": "source-preview.mp4", "mask_root": "masks/" if masks else None,
            "origin_s": stamp0 - 1, "max_preview_timestamp_error_ms": float(np.max(np.abs(np.array(pts) - expected)) * 1000),
            "frames": [{"id": r["index"] + 1, "seconds": float(r["timestamp_s"]) - 1, "pts": p,
                        "pose": matrix.tolist() if (matrix := pose_at(poses, float(r["timestamp_s"]))) is not None else None}
                       for r, p in zip(selected, pts)]}
