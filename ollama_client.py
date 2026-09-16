"""Minimal Ollama chat client. No external HTTP dependency required."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def chat(
    model: str,
    prompt: str,
    host: str = "http://localhost:11434",
    timeout: int = 600,
    think: bool | None = None,
    num_ctx: int = 16384,
) -> str:
    """Send one user message to `model` via Ollama's /api/chat and return its reply.

    `think` controls reasoning-capable models' internal deliberation.
    `num_ctx` defaults to 16384 to accommodate prompts and reasoning output.
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0, "num_ctx": num_ctx},
    }
    if think is not None:
        payload["think"] = think
    request = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Failed to reach Ollama at {host} for model '{model}': {e}\n"
            f"Is Ollama running, and has '{model}' been pulled (`ollama pull {model}`)?"
        ) from e
    if "message" not in result:
        raise RuntimeError(f"Unexpected response from Ollama for model '{model}': {result}")
    return result["message"]["content"]
