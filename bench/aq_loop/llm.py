"""Minimal client for the local Muse model via opencode zen/go API.

Endpoint: POST https://opencode.ai/zen/go/v1/responses
Model:    muse-spark-1.3-contributor
Auth:     Bearer token from $OPENCODE_ZEN_API_KEY (or ~/.config/opencode/zen_key).
          The key is never printed or logged.

Usage:
    from llm import complete
    text = complete("decide the next experiment given ...")

Schema notes (probed 2026-10-05 without a valid key):
  - missing key  -> {"type":"error","error":{"type":"AuthError", ...}}
  - bad key      -> AuthError "Invalid API key."
  - body shape follows the OpenAI Responses convention
    ({model, input, [instructions], [max_output_tokens], [stream:false]}); the parser
    below accepts several output shapes and raises on error payloads.
"""

import json
import os
import urllib.request
from pathlib import Path

ENDPOINT = "https://opencode.ai/zen/go/v1/responses"
MODEL = "muse-spark-1.3-contributor"
TIMEOUT = 300
# Per https://opencode.ai/docs/go: identify with our own UA (generic
# SDK/HTTP names get Cloudflare-blocked) and send a STABLE session id
# per conversation for routing + prompt caching.
USER_AGENT = "ccxt-aq-loop/1.0"
SESSION_ID = "ccxt-aq-loop-v1"


def _load_key() -> str:
    key = os.environ.get("OPENCODE_ZEN_API_KEY", "").strip()
    if key:
        return key
    p = Path.home() / ".config" / "opencode" / "zen_key"
    if p.exists():
        key = p.read_text().strip()
        if key:
            return key
    raise RuntimeError(
        "no zen API key: set $OPENCODE_ZEN_API_KEY or write it to "
        "~/.config/opencode/zen_key"
    )


def _post(payload: dict, timeout: int = TIMEOUT) -> dict:
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_load_key()}",
            "User-Agent": USER_AGENT,
            "x-opencode-session": SESSION_ID,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        body = e.read().decode(errors="ignore")[:2000]
        try:
            return json.loads(body)
        except ValueError:
            raise RuntimeError(f"HTTP {e.code}: {body}")


def _extract_text(data: dict) -> str:
    if isinstance(data, dict) and data.get("type") == "error":
        err = data.get("error", {})
        raise RuntimeError(f"API error {err.get('type')}: {err.get('message')}")
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected response: {str(data)[:300]}")
    # OpenAI Responses shape: output[] with message/content[] output_text blocks
    parts: list[str] = []
    out = data.get("output")
    if isinstance(out, list):
        for block in out:
            if not isinstance(block, dict):
                continue
            content = block.get("content", block)
            items = content if isinstance(content, list) else [content]
            for it in items:
                if isinstance(it, dict) and it.get("type") in ("output_text", "text"):
                    t = it.get("text")
                    parts.append(t if isinstance(t, str) else json.dumps(t))
                elif isinstance(it, str):
                    parts.append(it)
    if parts:
        return "\n".join(parts).strip()
    for key in ("text", "output_text", "response", "answer", "content"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    raise RuntimeError(f"could not extract text from: {json.dumps(data)[:500]}")


def complete(prompt: str, system: str | None = None,
             max_tokens: int = 4000, timeout: int = TIMEOUT,
             model: str = MODEL) -> str:
    """One non-streaming completion. Returns plain text or raises."""
    payload: dict = {"model": model, "input": prompt, "stream": False,
                     "max_output_tokens": max_tokens}
    if system:
        payload["instructions"] = system
    return _extract_text(_post(payload, timeout=timeout))


if __name__ == "__main__":
    import sys
    print(complete(sys.argv[1] if len(sys.argv) > 1 else "Reply with exactly: wrapper-alive"))
