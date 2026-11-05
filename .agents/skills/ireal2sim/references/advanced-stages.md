# Advanced fitting workers

The source preview provides orchestration and output validation, not a bundled
furniture/HaWoR service. `models`, `hands`, `objects` require configured workers.
Development experiments need portable inputs/runtime setup and a worker adapter;
copying their old outputs is not fitting a new recording.

User-owned runner config is gitignored `ireal2sim.local.json`:

```json
{"backends": {
  "models": {"argv": ["python3", "/path/to/model_worker.py"], "requires_env": ["FAL_KEY"], "timeout_seconds": 1800},
  "hands": {"argv": ["python3", "/path/to/gpu_worker_client.py"], "requires_env": [], "requires_paths": [], "timeout_seconds": 1800}
}}
```

These are interface examples, not included executors. Dependencies follow the
actual worker: procedural models need no fal key, and remote hand workers may
already have MANO/weights. Do not put secrets in JSON or argv.

The runner appends `--request REQUEST_JSON --output ATTEMPT_DIRECTORY` without a
local shell. A trusted SSH/client adapter can use an existing GPU host. Transport,
GPU/asset checks and cleanup belong to that adapter. Follow existing authorization
for source uploads; resolve a missing host/spend limit before new provisioning.

The request supplies dataset/mapping/viewer paths, source/calibration/trajectory
hashes, `world_frame: initial_main_optical`, and `context_sha256`. Use these exact
intrinsics/poses/basis. Write `result.json` inside the attempt directory:

```json
{
  "schema": "ireal2sim.worker-result/1", "stage": "hands", "status": "complete",
  "source_manifest_sha256": "copy from request.context",
  "context_sha256": "copy from request", "world_frame": "initial_main_optical",
  "artifacts": [{"path": "tracks.json", "role": "interaction_tracks", "sha256": "actual file hash"}],
  "limitations": ["Hidden shape inferred; physical contact not verified."]
}
```

Models provide an embedded, metric, source-world GLB with role `scene_glb`; no
external buffers/textures or escaping artifact paths. The viewer offers a scan/
model selector. Walking obstacles remain based on the scan. Models are unreviewed
until the agent performs source/render review; completion cannot create approval.

Hands/objects use `iphone-rsa.viewer-tracks/1`: `tracks` is an array of actors
with unique `name`, base64 little-endian uint32 triangle `indices`, `indexCount`,
RGB `color` in 0..1 and `frames` keyed by original numeric source IDs. Frames
supply base64 little-endian float32 world-space `positions` and `quality`.
Hand names are `left_hand`/`right_hand`; object names are distinct. Hands need
`quality.display_support_gate.passed: true` for default display; objects need
`quality.passes_observation_gate: true`. These are heuristics, not confidence
calibration. Weak/unknown samples are opt-in; missing frames remain absent.

Hashes, world context, valid geometry/index ranges and frame identities are
checked before composition. Attempts are preserved and workers have bounded
deadlines. Provide model-only renders/source overlays as additional artifact
roles for agent/Astra review. Geometry/contact accuracy remains unverified.
