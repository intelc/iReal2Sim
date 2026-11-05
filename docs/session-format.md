# Recording format v1

A session directory is self-contained and immutable after export. Source video
and depth are retained separately from future reconstructions. No world trajectory
is inferred by the recorder.

```text
manifest.json
imu.jsonl
events.jsonl
segment-0000/
  segment.json
  main.mov
  ultra.mov
  frames.jsonl
  depth.f32
  depth.jsonl
segment-0001/...
```

`capabilities.json` is stored at the app's Documents root and exported separately.
It contains camera device identities, supported MultiCam sets, formats, frame-rate
ranges, depth formats, IMU availability, and (on successful preparation) the selected
configuration. It is replaced on each preparation; session manifests retain their
own configuration snapshot.

`stream-health.json` is another live diagnostic at the Documents root, updated
once per second while preview is active. It contains no camera imagery and is
not a recording index. Fields include per-stream frame counts, measured FPS,
capture drops, last sample host times, preview sample PTS, and the number of
callbacks delivered without a primary-camera sample. Such callbacks are valid
and must be processed to retain independently timed ultra-wide/depth samples.

## Time

- `host_ns` is a **decimal string**, preserving integer precision in JavaScript.
  Camera samples use `CMSyncConvertTime` from the session synchronization clock
  to the host clock. They are not Unix/UTC timestamps.
- `capture_pts_value` is a decimal string, with integer `capture_pts_timescale`.
  Both preserve the original camera PTS. No fixed-frame-rate time is synthesized.
- Each camera movie starts at its own first accepted sample PTS, recorded in
  `segment.json.videos[stream].origin_capture_pts_*`. Its frame index contains
  `media_time_s = original_pts - movie_origin_pts`. Compare host timestamps across
  cameras/segments, not movie-local frame numbers.
- IMU `sensor_time_s` preserves Core Motion's seconds since boot; `host_ns` is
  its rounded nanosecond representation. Physical camera/IMU latency still needs
  validation/calibration. Accelerometer and gyro are independent timestamped streams.

## RGB frame index

`frames.jsonl` interleaves `main` and `ultra` records. Each has `stream`, a
zero-based per-stream/per-segment `frame_index`, time fields, dimensions,
`intrinsics` (3×3 row-major or null), and current camera exposure/ISO/focus/zoom/
distortion-correction settings. Only accepted encoder samples enter the index.
Encoder and capture drops are separate events. Frames are native sensor buffers;
no rotation, mirroring, stabilization or application rectification is applied.
Video uses HEVC in MOV containers. The original 720p30 build targeted 10 Mbps per
camera. The 1080p60 build targets 24 Mbps per camera, sets the expected source rate
to 60, and requests one keyframe per second. Segment video metadata stores
`expected_source_fps` and `target_bitrate_bps`; original sample PTS remain the
source of truth for measured cadence. Requested resolution/rates, depth frame
duration and active camera frame durations are saved in the session configuration.
RGB60 and depth30 are independently timestamped; there is not a new depth sample
for every RGB frame, and depth is never duplicated to claim 60 fps.

## Depth

`depth.f32` concatenates tightly packed, little-endian float32 frames in metres.
`depth.jsonl` supplies `offset_bytes`, `length_bytes`, width, height, zero-based
frame index, original PTS, host time, filtering/accuracy/quality and calibration.
There is no pixel-buffer row padding. NaN, infinity, zero or other invalid values
remain unchanged. Length is exactly `width * height * 4`.

Depth is Apple's processed RGB-assisted LiDAR output, aligned/distorted to the
main RGB view. It is not raw LiDAR returns and does not span ultra-wide edges.
`filtered` should be false and `accuracy` should be `absolute`.

Calibration includes:

- `intrinsics`, `reference_width`, `reference_height` (intrinsics are in pixels
  at these reference dimensions, which may differ from depth resolution).
- `distortion_center`, native float32 distortion/inverse-distortion tables encoded
  as base64, or null when unavailable. Per-frame RGB intrinsics alone do not
  establish a full ultra-wide distortion model.
- `extrinsics_reference_from_camera_m`: Apple's calibration reference-camera
  transform, with translation converted to metres. It is **not a world pose**.
- `pixel_size_mm`: Apple's native calibration pixel-size value in millimetres.

Session configuration separately contains `ultra_from_main_m`, a 3×4 row-major
physical-camera factory transform or null. Its equation is
`X_ultra = R * X_main + t`; translation is in metres. No camera-to-IMU transform
is supplied. Apply correct lens distortion/rectification and dimension scaling
before unprojecting depth or comparing cameras.

## Motion and health

`imu.jsonl` records `sensor` (`accelerometer` or `gyroscope`), timestamps, `xyz`,
and `unit` (`g` or `rad/s`). Acceleration includes gravity; it is not fused attitude
or gravity-subtracted acceleration.

`events.jsonl` records recording start/stop, telemetry, and dropped samples.
Drop records include stream, reason, event host time, and sample host time when
available. Telemetry includes thermal state (Apple raw enum), available bytes and
per-camera system-pressure levels. The manifest stores aggregate counts and drops.
Absence of an explicit drop event does not rule out gaps; inspect timestamps.

## Completion and interruption

Manifest states: `recording` → `finalizing` → `complete` or `failed`. Each segment
has its own state and byte/frame counts. A crash can leave the final segment and
manifest unfinished; do not interpret them as successful recordings. The app keeps
partial data. Validators reject incomplete sessions and can inspect their earlier
finalized segments without altering source files.

The validator's `passed` status establishes file/index consistency under its
listed checks. It is not a claim of 3D ground truth, simultaneous exposures,
correct distortion calibration, stable SLAM, or physical measurement accuracy.
