"""Minimal LLM providers over plain HTTP (no SDK needed).

  FIXER_PROVIDER=anthropic  ANTHROPIC_API_KEY=...  [FIXER_MODEL=claude-sonnet-5-5]
  FIXER_PROVIDER=openai     OPENAI_API_KEY=...      FIXER_MODEL=...  [OPENAI_BASE_URL=...]
      (openai works with any OpenAI-compatible endpoint: Groq, Together, vLLM, Ollama...)
"""
import copy
import json
import os
import time
from dataclasses import dataclass, field

import requests


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    text: str
    calls: list = field(default_factory=list)


def _post(url, headers, body, retries=6):
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=120)
        except (requests.ConnectionError, requests.Timeout) as exc:  # transient network failure
            if attempt == retries - 1:
                raise RuntimeError(f"LLM API unreachable after {retries} attempts: {exc}") from exc
            time.sleep(min(30, 2 ** attempt * 2))
            continue
        if r.status_code in (429, 500, 502, 503, 529) and attempt < retries - 1:
            time.sleep(2 ** attempt * 2)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"LLM API error {r.status_code}: {r.text[:500]}")
        return r.json()


class AnthropicProvider:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model=None, max_tokens=2048):
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise RuntimeError("set ANTHROPIC_API_KEY in your shell (never paste it into code)")
        self.model = model or os.environ.get("FIXER_MODEL", "claude-sonnet-5-5")
        self.max_tokens = max_tokens
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def start(self, system, user):
        self.system, self.messages = system, [{"role": "user", "content": user}]

    def step(self, tools):
        # prompt caching: tools + system + conversation so far are re-read from cache each step
        tools = copy.deepcopy(tools)
        tools[-1]["cache_control"] = {"type": "ephemeral"}
        msgs = copy.deepcopy(self.messages)
        if isinstance(msgs[-1]["content"], str):
            msgs[-1]["content"] = [{"type": "text", "text": msgs[-1]["content"]}]
        msgs[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
        data = _post(self.URL, {"x-api-key": self.key, "anthropic-version": "2023-06-01",
                                "content-type": "application/json"},
                     {"model": self.model, "max_tokens": self.max_tokens,
                      "system": [{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                      "tools": tools, "messages": msgs})
        self.messages.append({"role": "assistant", "content": data["content"]})
        u = data.get("usage", {})
        self.usage["input_tokens"] += (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                                       + u.get("cache_creation_input_tokens", 0))
        self.usage["output_tokens"] += u.get("output_tokens", 0)
        self.usage["cached_tokens"] = self.usage.get("cached_tokens", 0) + u.get("cache_read_input_tokens", 0)
        text = "".join(b.get("text", "") for b in data["content"] if b["type"] == "text")
        calls = [ToolCall(b["id"], b["name"], b.get("input") or {})
                 for b in data["content"] if b["type"] == "tool_use"]
        return Turn(text.strip(), calls)

    def submit(self, results, note=None):
        content = [{"type": "tool_result", "tool_use_id": i, "content": c} for i, c in results]
        if note:
            content.append({"type": "text", "text": note})
        self.messages.append({"role": "user", "content": content})

    def nudge(self, text):
        self.messages.append({"role": "user", "content": text})


class OpenAIProvider:
    def __init__(self, model=None, max_tokens=2048):
        self.key = os.environ.get("OPENAI_API_KEY")
        if not self.key:
            raise RuntimeError("set OPENAI_API_KEY in your shell (never paste it into code)")
        self.model = model or os.environ.get("FIXER_MODEL")
        if not self.model:
            raise RuntimeError("set FIXER_MODEL (or --model) for the openai provider")
        self.base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.max_tokens = max_tokens
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def start(self, system, user):
        self.messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def step(self, tools):
        fns = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                 "parameters": t["input_schema"]}} for t in tools]
        data = _post(f"{self.base}/chat/completions",
                     {"Authorization": f"Bearer {self.key}", "content-type": "application/json"},
                     {"model": self.model, "messages": self.messages, "tools": fns,
                      "max_completion_tokens": self.max_tokens})
        msg = data["choices"][0]["message"]
        keep = {"role": "assistant", "content": msg.get("content")}
        if msg.get("tool_calls"):
            keep["tool_calls"] = msg["tool_calls"]
        self.messages.append(keep)
        u = data.get("usage", {})
        self.usage["input_tokens"] += u.get("prompt_tokens", 0)
        self.usage["output_tokens"] += u.get("completion_tokens", 0)
        calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(tc["id"], tc["function"]["name"], args))
        return Turn((msg.get("content") or "").strip(), calls)

    def submit(self, results, note=None):
        for i, c in results:
            self.messages.append({"role": "tool", "tool_call_id": i, "content": c})
        if note:
            self.messages.append({"role": "user", "content": note})

    def nudge(self, text):
        self.messages.append({"role": "user", "content": text})


class FakeProvider:
    """Replays a scripted list of turns. Used by tests to exercise the loop without an API key."""

    def __init__(self, script):
        self.script = list(script)
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.received = []

    def start(self, system, user):
        pass

    def step(self, tools):
        if not self.script:
            return Turn("", [ToolCall("end", "submit_report",
                                      {"root_causes": [], "summary": "script exhausted", "believes_recovered": False})])
        text, calls = self.script.pop(0)
        return Turn(text, [ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(calls)])

    def submit(self, results, note=None):
        self.received.append(results)

    def nudge(self, text):
        pass


def make_provider(name=None, model=None):
    name = name or os.environ.get("FIXER_PROVIDER", "anthropic")
    if name == "anthropic":
        return AnthropicProvider(model)
    if name == "openai":
        return OpenAIProvider(model)
    raise ValueError(f"unknown provider {name!r}")
