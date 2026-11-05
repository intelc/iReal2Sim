# iReal2Sim

Capture a room on iPhone, then reconstruct and inspect it in a local dashboard.
Includes a native Swift capture app and a reproducible desktop entry point.

**0.3.0 is a source release for self-signing.** A public TestFlight build is not
available yet. Follow [Install on your iPhone](docs/self-signing.md).

## What you need

- iPhone 17 Pro or 18 Pro with Main + Ultra Wide + LiDAR. 17 Pro short captures
  are tested; 18 Pro compatibility is checked at runtime and is not physically
  validated yet. Capture requests both RGB streams at **1080p60**, depth up to
  **30 fps**, and IMU at **100 Hz**. Longer-run thermal endurance is unverified.
- A Mac, Xcode supporting your phone's iOS, and an Apple Account for self-signing.
- Python 3.12 through uv, FFmpeg, CMake and RTAB-Map for processing.
- Optional OpenAI API key for vision review and object/surface recognition.

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
the Main/Ultra storyboards and incurs API usage. Click **Build dashboard**.
The job list shows progress and links to your generated scene, camera replay,
source storyboards, validation and optional recognition/review report.

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
