"""Review recorded images or mask overlays with Astra through the OpenAI API.

Outputs are review suggestions, never ground-truth labels or automatic geometry edits.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
from pathlib import Path

import requests

from .rgbd import write_json


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name, value = line.split("=", 1)
                values[name.strip().removeprefix("export ")] = value.strip().strip("\"'")
    return values


def review(images: list[Path], prompt: str, output: Path, env_file: Path = Path(".env"), max_output_tokens: int = 6000, before_send=None, model: str | None = None):
    key = os.environ.get("OPENAI_API_KEY") or read_env(env_file).get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new review directory to preserve the previous review")
    output.mkdir(parents=True, exist_ok=True)
    content = [{"type": "input_text", "text": prompt + "\nReturn a JSON object. Describe uncertainty; do not claim physical measurements from images."}]
    provenance = []
    for path in images:
        data = path.read_bytes()
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        content.extend([{"type": "input_text", "text": path.name},
                        {"type": "input_image", "image_url": f"data:{mime};base64," + base64.b64encode(data).decode(), "detail": "high"}])
        provenance.append({"path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest()})
    payload = {"model": model or os.environ.get("OPENAI_MODEL", "gpt-6-astra"), "store": False, "input": [{"role": "user", "content": content}],
               "max_output_tokens": max_output_tokens, "text": {"format": {"type": "json_object"}}}
    write_json(output / "request.json", {"model": payload["model"], "store": False, "max_output_tokens": max_output_tokens, "prompt": prompt, "images": provenance})
    if before_send is not None:
        before_send(output / "request.json")
    started = time.monotonic()
    response = requests.post("https://api.openai.com/v1/responses", headers={"Authorization": "Bearer " + key}, json=payload, timeout=240)
    if not response.ok:
        # Never print request headers or the key.
        write_json(output / "failure.json", {"http_status": response.status_code, "error": response.json().get("error", {})})
        raise RuntimeError(f"Astra review failed: HTTP {response.status_code}; see failure.json")
    result = response.json()
    write_json(output / "response.json", result)
    if result.get('status') != 'completed':
        raise RuntimeError('Astra response incomplete; see preserved response.json and use an adequate output budget')
    text = "".join(part.get("text", "") for item in result.get("output", []) for part in item.get("content", []) if part.get("type") == "output_text")
    try:
        decision = json.loads(text)
    except json.JSONDecodeError:
        (output / "unparsed-response.txt").write_text(text)
        raise RuntimeError("Astra did not return valid JSON; preserved its response for review")
    write_json(output / "review.json", {"schema": "iphone-rsa.astra-review/1", "model": result.get("model"),
               "response_id": result.get("id"), "elapsed_s": time.monotonic() - started, "usage": result.get("usage"),
               "status": result.get("status"), "review": decision,
               "limitations": "Model suggestions require overlay review; they are not metric or ground-truth annotations."})
    print(json.dumps({"model": result.get("model"), "status": result.get("status"), "output": str(output), "usage": result.get("usage")}))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("images", nargs="+", type=Path)
    p.add_argument("--prompt-file", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--env-file", type=Path, default=Path(".env"))
    p.add_argument("--max-output-tokens", type=int, default=6000)
    a = p.parse_args()
    review(a.images, a.prompt_file.read_text(), a.output, a.env_file, a.max_output_tokens)
