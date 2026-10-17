"""OpenAI-compatible chat client for Crusoe Managed Inference.

- Reads CRUSOE_API_KEY (and optional CRUSOE_BASE_URL) from the environment.
- Client-side sliding-window limiter per model (default 28 RPM, Crusoe's free tier is 30).
- Exponential backoff with jitter on 429/5xx/network errors.
- `RESPAWN_MOCK=1` (or `mock=True`) swaps in an offline responder so the whole loop runs without a key.
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque

BASE_URL = os.environ.get("CRUSOE_BASE_URL", "https://api.inference.crusoecloud.com/v1").rstrip("/")

# exact IDs from GET /v1/models (2026-10-17)
NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
LIGHTNING = "nvidia/Nemotron-3.5-Lightning-30B-A3B"
SUPER = "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B"


def load_env_file(path: str = ".env") -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip("'").strip('"')
        os.environ.setdefault(k.strip(), v)


class RateLimiter:
    def __init__(self, rpm: int):
        self.rpm = rpm
        self.calls: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def acquire(self, model: str) -> None:
        while True:
            with self.lock:
                q = self.calls[model]
                now = time.monotonic()
                while q and now - q[0] > 60:
                    q.popleft()
                if len(q) < self.rpm:
                    q.append(now)
                    return
                wait = 60 - (now - q[0]) + 0.05
            time.sleep(wait)


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.by_model = defaultdict(lambda: {"calls": 0, "prompt": 0, "completion": 0, "cached": 0, "retries": 0, "seconds": 0.0})

    def add(self, model, usage, seconds, retries):
        with self.lock:
            s = self.by_model[model]
            s["calls"] += 1
            s["retries"] += retries
            s["seconds"] += seconds
            if usage:
                s["prompt"] += usage.get("prompt_tokens", 0) or 0
                s["completion"] += usage.get("completion_tokens", 0) or 0
                s["cached"] += ((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)

    def summary(self) -> dict:
        with self.lock:
            return {m: dict(v) for m, v in self.by_model.items()}


class LLM:
    def __init__(self, api_key: str | None = None, rpm: int | None = None, mock: bool | None = None, max_tries: int = 7):
        load_env_file()
        self.api_key = api_key or os.environ.get("CRUSOE_API_KEY", "")
        self.mock = bool(int(os.environ.get("RESPAWN_MOCK", "0"))) if mock is None else mock
        if not self.mock and not self.api_key:
            raise SystemExit("CRUSOE_API_KEY is not set (or pass --mock to run offline)")
        self.limiter = RateLimiter(rpm or int(os.environ.get("RESPAWN_RPM", "28")))
        self.max_tries = max_tries
        self.stats = Stats()
        self.mock_responder = None  # callable(model, messages, tools) -> message dict

    def chat(self, model: str, messages: list, tools: list | None = None, max_tokens: int = 2048,
             temperature: float = 0.6, extra: dict | None = None) -> dict:
        """Returns the assistant message dict ({'content', 'tool_calls', ...})."""
        if self.mock:
            if self.mock_responder is None:
                raise RuntimeError("mock mode needs a mock_responder")
            msg = self.mock_responder(model, messages, tools)
            self.stats.add(model + " (mock)", None, 0.0, 0)
            return msg
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        if extra:
            body.update(extra)
        data = json.dumps(body).encode()
        t0 = time.monotonic()
        for attempt in range(self.max_tries):
            self.limiter.acquire(model)
            req = urllib.request.Request(f"{BASE_URL}/chat/completions", data=data, headers={
                "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    resp = json.load(r)
                self.stats.add(model, resp.get("usage"), time.monotonic() - t0, attempt)
                return resp["choices"][0]["message"]
            except urllib.error.HTTPError as e:
                detail = e.read()[:300].decode(errors="replace")
                if e.code not in (408, 409, 429, 500, 502, 503, 504) or attempt == self.max_tries - 1:
                    raise RuntimeError(f"{model}: HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
                if attempt == self.max_tries - 1:
                    raise
            time.sleep(min(45.0, 1.5 * 2 ** attempt) + random.uniform(0, 1.0))
        raise RuntimeError("unreachable")


def extract_json(text: str):
    """Pull the first JSON object out of a model reply (handles ```json fences and prose)."""
    if not text:
        raise ValueError("empty reply")
    if "```" in text:
        for chunk in text.split("```")[1::2]:
            chunk = chunk.strip()
            if chunk.startswith("json"):
                chunk = chunk[4:]
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                pass
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found")
