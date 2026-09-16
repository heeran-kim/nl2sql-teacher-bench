"""Minimal Ollama chat client. No external HTTP dependency required."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def chat(
    model: str, prompt: str, host: str = "http://localhost:11434", timeout: int = 600, think: bool | None = None
) -> str:
    """Send one user message to `model` via Ollama's /api/chat and return its reply text.

    `think` controls whether reasoning-capable models (e.g. Qwen3) use their
    internal deliberation step before answering. Default `None` sends no
    override at all -- Ollama's own default for the model -- since for a
    teacher-selection benchmark, quality matters far more than speed
    (the whole point of the teacher role is that it runs once, offline) and
    disabling thinking by default would unfairly cap models specifically
    designed to reason their way to a correct answer. It's also
    dramatically slower (roughly 40s vs. 1s on a trivial prompt, in one
    observed case) and occasionally slower still on complex prompts, which
    is a --timeout tuning problem, not a reason to force it off by default.
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0},
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
