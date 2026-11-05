# Processing and release workflow

The supported portable release is macOS + Xcode for capture installation and
macOS + Python 3.12/Homebrew for reconstruction. No NVIDIA GPU or learned-model
download is needed by the default path.

```sh
brew install uv ffmpeg cmake rtabmap
./start
```

The launcher installs pinned Python dependencies and opens the local setup page
at `http://127.0.0.1:8766`. Enter the complete exported session folder. For vision
review, enter your OpenAI key, select a model available to your account, and
enable the upload checkbox. The key remains in memory/the child environment;
it is not placed in an HTML page, command argument, source archive or iPhone app.

The pipeline validates every stream/video, indexes both cameras, exports
calibrated Main/LiDAR RGB-D, estimates poses with RTAB-Map, fuses an Open3D mesh,
and builds the upright 3D map/timed replay. Optionally it bakes a native RGB atlas
and sends the two camera storyboards to the OpenAI Responses API for capture
review and an evidence-based object/surface inventory. Source recordings are
read-only; failures and partial derived outputs are retained for diagnosis.

Processing defaults to 15 RGB-D observations/sec at 640 pixels wide. This does
not alter the original 1080p60 movies or native depth cadence. Ultra Wide provides
source images and review evidence, not automatic additional geometry. A new
capture is not assumed to share any calibration or room parameters with the demo.

## Command line

```sh
uv sync --frozen --python 3.12
uv run scene-capture doctor
uv run scene-capture run /path/to/session --output workspaces/my-room
```

To enable review, set `OPENAI_API_KEY` in your shell (or copy `.env.example` to
`.env` and use `uv run --env-file .env ...`), then add `--review`. `--model` or
`OPENAI_MODEL` selects the vision model; the default is `gpt-6-astra`. There is
no silent model substitution. `--texture` adds atlas generation. `--prefix`
passes a custom CMake dependency prefix. The source and output must be separate,
non-nested folders, and every run requires a fresh output directory.

API review uploads the sampled storyboards and incurs usage. It does not create
verified 3D objects or certify physical dimensions. The local geometry path works
without an API key. See [official image input documentation](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses).

## Output

- `index.html`: dashboard with scene, downloads and status.
- `map/viewer.html`: reconstructed room, recorded views, upright orbit, WASD/jump
  and timed Main/Ultra replay.
- `parsed/`: all-stream indexes, depth audit and Main/Ultra storyboards.
- `validation.json`, `run.json`: capture checks and processing provenance.
- `mapping/`: retained RTAB-Map database, poses and logs.
- `mesh/`: measured-depth mesh/cloud and diagnostics.
- `review/`: optional exact review request, response and inventory proposals.

The local setup server exposes only its generated workspace, binds to loopback
and checks Origin/session tokens before launching a job. Do not expose it publicly.
Stop the server after all jobs complete. Long reconstructions can take time and
significant disk/memory; progress is shown in the job list.

## Scope of 0.3.0

This is a reproducible capture-to-reconstruction preview. The earlier furnished
room, camera self-calibration, learned hand/object fitting, dynamic masks and
multi-stage model approval loop remain research workflows. Their room-specific
parameters and outputs are excluded from the portable release. The baseline may
still contain moving-object artifacts and incomplete surfaces. No pretrained
weights, licensed MANO files or private demo recording are redistributed.

## Maintainer packaging

```sh
python3 scripts/prepare_release.py --output .release/iReal2Sim
```

This allowlists the portable Python import closure, required viewer assets,
iPhone source/project, portable tests, documentation and CI. It checks for local
paths, secrets, weights and symlinks, then writes a hash manifest and source ZIP.
It refuses to overwrite an existing staging directory. Research files remain in
the development workspace and are not silently removed. Run the staged checkout's
tests/build before publishing. No signing credentials or prebuilt IPA are emitted.
