"""One small interface for the language model, so the app can run on Google Gemini (free tier) or Claude.

Callers use the Anthropic-style shape everywhere:
    client.messages.create(model=..., max_tokens=..., system=..., tools=[{name, description, input_schema}],
                           tool_choice={"type": "tool", "name": ...}, messages=[...])
and read `.content` (blocks with .type 'text' or 'tool_use') and `.stop_reason` ('tool_use' or 'end_turn').
GeminiClient translates that to the Gemini REST API (generateContent with function calling) and back.

Provider choice (config.ai_provider): GEMINI_API_KEY set -> Gemini; else ANTHROPIC_API_KEY -> Claude; else none
(the app then uses its rule-based intake and offline copilot, so nothing depends on an AI key)."""
import itertools
import json
from types import SimpleNamespace
from typing import Optional

import httpx

from . import config

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_ALLOWED_SCHEMA_KEYS = {"type", "description", "enum", "properties", "required", "items", "minimum", "maximum",
                        "format", "nullable"}


class LLMError(Exception):
    pass


def _clean_schema(s: dict) -> dict:
    """Keep only the JSON-schema keywords Gemini's function declarations accept."""
    out = {}
    for k, v in s.items():
        if k not in _ALLOWED_SCHEMA_KEYS:
            continue
        if k == "properties":
            out[k] = {name: _clean_schema(sub) for name, sub in v.items()}
        elif k == "items" and isinstance(v, dict):
            out[k] = _clean_schema(v)
        else:
            out[k] = v
    if out.get("type") == "integer" and "enum" in out:  # Gemini enums must be strings
        out.pop("enum")
    return out


def _declaration(tool: dict) -> dict:
    d = {"name": tool["name"], "description": tool.get("description", "")}
    params = _clean_schema(tool.get("input_schema") or {})
    if params.get("properties"):
        d["parameters"] = params  # a function with no arguments must omit parameters
    return d


class GeminiClient:
    def __init__(self, api_key: str, transport: Optional[httpx.BaseTransport] = None, timeout: float = 30.0):
        self.api_key = api_key
        self.http = httpx.Client(timeout=timeout, transport=transport)
        self._ids = itertools.count(1)
        self._calls: dict = {}  # tool_use id -> (name, raw Gemini part, which may carry a thoughtSignature)
        self.messages = self

    def _to_contents(self, messages: list) -> list:
        contents = []
        for m in messages:
            role = "model" if m["role"] == "assistant" else "user"
            content = m["content"]
            if isinstance(content, str):
                contents.append({"role": role, "parts": [{"text": content}]})
                continue
            parts = []
            for b in content:
                t = b.get("type")
                if t == "text" and b.get("text"):
                    parts.append({"text": b["text"]})
                elif t == "tool_use":
                    known = self._calls.get(b["id"])
                    parts.append(known[1] if known else {"functionCall": {"name": b["name"], "args": b.get("input") or {}}})
                elif t == "tool_result":
                    name = self._calls.get(b["tool_use_id"], (b.get("name", "tool"),))[0]
                    raw = b.get("content")
                    try:
                        payload = json.loads(raw) if isinstance(raw, str) else raw
                    except ValueError:
                        payload = raw
                    parts.append({"functionResponse": {"name": name, "response": {"result": payload}}})
            if parts:
                contents.append({"role": role, "parts": parts})
        return contents

    def create(self, model: str, max_tokens: int, messages: list, system: str = "", tools: Optional[list] = None,
               tool_choice: Optional[dict] = None, **_):
        body = {"contents": self._to_contents(messages),
                "generationConfig": {"maxOutputTokens": max(int(max_tokens), 2048), "temperature": 0.2}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{"functionDeclarations": [_declaration(t) for t in tools]}]
            if tool_choice and tool_choice.get("type") == "tool":
                body["toolConfig"] = {"functionCallingConfig": {"mode": "ANY",
                                                                "allowedFunctionNames": [tool_choice["name"]]}}
        url = f"{GEMINI_BASE}/models/{model}:generateContent"
        r = self.http.post(url, json=body, headers={"x-goog-api-key": self.api_key})
        if r.status_code >= 400:
            raise LLMError(f"Gemini {r.status_code}: {r.text[:300]}")
        data = r.json()
        cands = data.get("candidates") or []
        if not cands:
            raise LLMError(f"Gemini returned no candidates: {json.dumps(data.get('promptFeedback', {}))[:200]}")
        blocks, used_tool = [], False
        for part in (cands[0].get("content") or {}).get("parts", []):
            if "functionCall" in part:
                fc = part["functionCall"]
                bid = f"gem_{next(self._ids)}"
                self._calls[bid] = (fc["name"], part)
                blocks.append(SimpleNamespace(type="tool_use", id=bid, name=fc["name"], input=fc.get("args") or {}))
                used_tool = True
            elif part.get("text") and not part.get("thought"):
                blocks.append(SimpleNamespace(type="text", text=part["text"]))
        return SimpleNamespace(content=blocks, stop_reason="tool_use" if used_tool else "end_turn")


def client():
    provider = config.ai_provider()
    if provider == "gemini":
        return GeminiClient(config.gemini_key())
    if provider == "claude":
        import anthropic
        return anthropic.Anthropic(api_key=config.anthropic_key(), timeout=30.0)
    raise LLMError("no AI provider configured")
