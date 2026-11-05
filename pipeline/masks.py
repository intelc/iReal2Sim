"""Read timestamp-checked exclusion masks shared by fusion and texturing."""
from pathlib import Path
import json

import cv2
import numpy as np

from .rgbd import read_rows


class DynamicMasks:
    def __init__(self, directory: Path, dataset: Path):
        self.directory = directory.resolve()
        self.report = json.loads((self.directory / "masks.json").read_text())
        if self.report.get("status") != "complete" or self.report.get("schema") != "iphone-rsa.dynamic-masks/1":
            raise ValueError("Masks are incomplete or have an unsupported schema")
        if Path(self.report["dataset"]).resolve() != dataset.resolve():
            raise ValueError("Masks belong to a different RGB-D export")
        self.frames = {r["frame_id"]: r for r in read_rows(self.directory / "frames.jsonl")}

    def get(self, frame_id: int, row: dict, shape: tuple[int, int]) -> np.ndarray:
        record = self.frames.get(frame_id)
        if record is None or abs(float(record["timestamp_s"]) - float(row["timestamp_s"])) > 1e-8:
            raise ValueError(f"Missing or mismatched dynamic mask at frame {frame_id}")
        mask = cv2.imread(str(self.directory / record["mask"]), cv2.IMREAD_GRAYSCALE)
        if mask is None or mask.shape != shape:
            raise ValueError(f"Dynamic mask dimensions disagree at frame {frame_id}")
        return mask > 0
