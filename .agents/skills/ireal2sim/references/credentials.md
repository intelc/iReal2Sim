# Credential and runtime routing

Doctor reads process environment and the selected `.env` without executing shell
code. It returns presence flags only and makes no network calls. Use
`--env-file /path/to/.env` for an existing credential file. Nonempty process
environment values take precedence; `FAL_API_KEY` aliases `FAL_KEY`.

An interactive agent login is separate from the Python client's OpenAI API key.
If the user wants only in-agent visual inspection, use the scan profile and the
host agent's image tools; label that as agent review, not an Astra API audit.

| Work | Check | Ask only when |
| --- | --- | --- |
| Self-sign/capture | Xcode, user's Apple Account, paired iPhone | Missing physical/account action; no API key needed. |
| Scan/texture/replay | Python, FFmpeg/ffprobe, CMake, RTAB-Map | Tools or recording unavailable; no cloud keys. |
| OpenAI/Astra review | `OPENAI_API_KEY`, optional `OPENAI_MODEL` | Requested review needs a missing/invalid key or unavailable model. |
| fal masks/meshes | `FAL_KEY` | A configured worker uses fal. |
| HaWoR/MANO | GPU runner, weights, user's licensed hand assets | Determine local vs remote runtime first. |
| RunPod | `RUNPOD_API_KEY` | Chosen RunPod provisioning/management path; existing SSH runners may need none. |
| Modal | `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET` or existing authentication | Chosen Modal runtime; never require both providers. |
| Gated model download | `HF_TOKEN` or `HUGGING_FACE_HUB_TOKEN` and access | Download actually needed; existing assets may suffice. |

Local workers may declare `MANO_RIGHT_PATH`, `MANO_LEFT_PATH`, `HAWOR_ROOT` in
`requires_paths`. Existence does not validate the asset format, license or GPU
compatibility. Remote workers may already have these assets; do not ask users
to download unnecessary local copies or accept licenses for them.

Keys belong in environment/gitignored `.env`, never chat, argv, plans, worker
config, screenshots or Git. A credential-file path is sufficient. Explain the
specific missing credential's purpose. Diagnose 401/403, model access and quota
errors without rotating through keys blindly. For new GPU rentals, establish a
provider/host and bounded runtime/spend if not already authorized; prefer an
existing runner. Do not rent compute just to test a credential.
