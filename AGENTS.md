# iReal2Sim agent entry point

This repository captures Main/Ultra RGB, LiDAR and IMU on an iPhone 17 Pro and
builds a local reconstruction dashboard. Users self-sign the native app in Xcode.

When helping install, capture, process a recording, or fit scene/hand/object
models, read [.agents/skills/ireal2sim/SKILL.md](.agents/skills/ireal2sim/SKILL.md).
Start with its redacted doctor and existing workflow state. Ask only for missing
inputs and perform authorized routine work yourself. Capture requires the user
and their physical phone.

Scan/replay and OpenAI capture review are built in. Advanced fitting uses
configured workers with source-bound output contracts. An API key alone cannot
enable a missing worker. Keep pending stages and inferred geometry explicit.

For maintenance, preserve recordings and keep credentials outside Git. Use
`uv sync --frozen --python 3.12`, `uv run python -m unittest discover -s tests -v`
and `node --test tests/*.mjs` as appropriate. Refresh release manifests/assets
when shipping. User instructions and host permissions take precedence.
