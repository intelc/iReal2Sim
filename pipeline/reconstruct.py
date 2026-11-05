"""Compile/run the RTAB-Map adapter, retaining tracking failures and its database."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from .rgbd import output_directory, write_json


def run(dataset: Path, output: Path, build_dir: Path, prefix: str | None = None):
    dataset = dataset.resolve()
    calibration = json.loads((dataset / "calibration.json").read_text())
    if calibration.get("status") != "complete":
        raise ValueError("RGB-D export is incomplete")
    output = output_directory(dataset, output, Path(calibration["source"]))
    source = Path(__file__).parent / "rtabmap"
    command = ["cmake", "-S", str(source), "-B", str(build_dir), "-DCMAKE_BUILD_TYPE=Release"]
    if prefix:
        command.append(f"-DCMAKE_PREFIX_PATH={prefix}")
    subprocess.run(command, check=True)
    subprocess.run(["cmake", "--build", str(build_dir), "--parallel", "4"], check=True)
    binary = (build_dir / "iphone-rsa-map").resolve()
    with (output / "run.log").open("w") as log:
        subprocess.run([str(binary), str(dataset), str(output / "mapping")], stdout=log, stderr=subprocess.STDOUT, check=True)
    result = json.loads((output / "mapping" / "backend.json").read_text())
    result.update(dataset=str(dataset), calibration=calibration, binary=str(binary),
                  tracking_fraction=result["tracked"] / result["frames"],
                  limitations=["No camera-to-IMU calibration or IMU fusion applied.",
                               "Dynamic hands/objects are not segmented in this initial mapping baseline.",
                               "Tracking success is not a ground-truth accuracy measurement."])
    write_json(output / "run.json", result)
    print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=Path("build/rtabmap-adapter"))
    parser.add_argument("--prefix", help="CMake dependency prefix, e.g. /opt/homebrew")
    args = parser.parse_args()
    run(args.dataset, args.output, args.build_dir, args.prefix)


if __name__ == "__main__":
    main()
