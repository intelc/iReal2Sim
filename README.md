# iReal2Sim

Capture a room on iPhone, then reconstruct and inspect it in a local dashboard.
Includes a native Swift capture app and a reproducible desktop entry point.

**Install by self-signing with your own Apple Account.**
Follow [Install on your iPhone](docs/self-signing.md).

## What you need

- iPhone 17 Pro with Main + Ultra Wide + LiDAR. Short captures are tested.
  Capture requests both RGB streams at **1080p60**, depth up to
  **30 fps**, and IMU at **100 Hz**. Longer-run thermal endurance is unverified.
- A Mac, Xcode supporting your phone's iOS, and an Apple Account for self-signing.
- Python 3.12 through uv, FFmpeg, CMake and RTAB-Map for processing.
- Optional OpenAI API key for vision review and object/surface recognition.

## Use an interactive agent

Open this folder in Codex or another coding agent and ask:

> Process my iPhone recording, check the required credentials, and build the scene dashboard. Guide me through scene and hand fitting too.

`AGENTS.md` points to the repo-local `$ireal2sim` skill. It inspects setup without
printing keys, asks only for missing inputs, runs ready stages and records
progress for another chat to resume. Scan/review run automatically. Scene,
hand and object fitting use explicitly configured workers; the agent helps with
their setup and keeps unavailable stages pending rather than claiming success.

```sh
python3 -m pipeline.agent_workflow doctor --profile full
uv run scene-capture workflow plan /path/to/session --output workspaces/my-room --profile full
uv run scene-capture workflow run workspaces/my-room
```

Repeat the last command to resume without rerunning valid completed stages.
Credentials stay in the environment or gitignored `.env`. See
[the skill](.agents/skills/ireal2sim/SKILL.md) for credential routing, worker
contracts and verification. You can also use the manual path below.

## 1. Install the capture app

Download this repository and open `SceneCapture.xcodeproj`. Select your Apple
team under Signing & Capabilities, use a unique bundle identifier, connect and
unlock your iPhone, and run the SceneCapture scheme on it.

The [self-signing guide](docs/self-signing.md) covers each Xcode/iPhone step,
Developer Mode, free-account renewal, troubleshooting and the install helper.
A free Personal Team can install on your own phone; its provisioning expires
periodically and needs renewal. The simulator cannot validate camera capture.

## 2. Record and export

Prepare cameras and verify both previews plus depth rates. Begin with a
15-second test. Use a rigid chest mount; slowly sweep the floor, walls and upper
surfaces from several viewpoints before interacting with objects. Keep the app
in the foreground. Stop and wait for Complete, then export the entire session
folder using the app, Files or Finder file sharing.

## 3. Start the desktop dashboard

Install [Homebrew](https://brew.sh) and then:

```sh
brew install uv ffmpeg cmake rtabmap
./start
```

The local setup page opens at `http://127.0.0.1:8766`. Enter the exported session
folder. Optionally enter your OpenAI key and enable vision review; that uploads
the Main/Ultra storyboards and incurs API usage. Click **Build scene**.
Recordings appear first when you return. Search or rename them, open a ready
scene, or choose **New reconstruction** to process another capture. Each scene
workspace shows progress, source evidence and optional fitting setup separately.
The viewer groups navigation, scene representation and display settings, with
**Controls** for walking and keyboard help.

The key remains in the local backend's memory/child environment and is not saved
in the dashboard or iPhone app. Processing without API review works offline once
dependencies are installed. No learned model download or NVIDIA GPU is required.

## What gets reconstructed

The portable pipeline runs capture validation → both-camera indexing → calibrated
Main/LiDAR RGB-D export → RTAB-Map pose estimation → Open3D fusion → 3D map and
paired Main/Ultra replay. Optional texture baking improves captured appearance. Optional
vision review produces an object/surface inventory and capture-gap suggestions.

This baseline does **not** automatically reproduce the hand-authored furnished
demo room, fit hands or arbitrary objects, remove every moving artifact, or fuse
Ultra Wide geometry. Those remain research stages. Models, physical dimensions,
hidden surfaces and contact need further evidence and review. Original recordings
are never rewritten; all derived data stays in a separate workspace.

## Command line and development

```sh
uv sync --frozen --python 3.12
uv run scene-capture doctor
uv run scene-capture run /path/to/session --output workspaces/my-room
uv run python -m unittest discover -s tests -v
node --test tests/*.mjs
```

Add `--review` with `OPENAI_API_KEY` configured, and optionally `--model` or
`--texture`. See [processing/release workflow](docs/release-workflow.md) and
[recording format](docs/session-format.md). Build the iPhone source without signing:

```sh
xcodebuild -project SceneCapture.xcodeproj -scheme SceneCapture \
  -destination 'generic/platform=iOS' -derivedDataPath build/ci \
  CODE_SIGNING_ALLOWED=NO build
```

## Release contents

The clean release excludes private recordings, API keys, pretrained checkpoints,
licensed MANO assets, demo-room models, discarded experiments and build caches.
No IPA or signing credentials are bundled. [Third-party dependencies](THIRD_PARTY.md)
retain their upstream licenses. Project source is under [MIT](LICENSE).

Inspired by [SceneHands](https://xiaobenli00.github.io/scene-hands/). The iPhone
capture rig has different coverage and calibration needs from the reference rig.
