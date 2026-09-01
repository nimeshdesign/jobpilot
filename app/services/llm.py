"""LLM access, provider-agnostic.

AI is optional everywhere in this app. Every caller must tolerate `None` coming
back: without a provider the tailoring falls back to a rule-based engine and
nothing else changes.

Three backends, chosen with JOBPILOT_LLM_PROVIDER:

  ollama    a model running on your own machine. Free, private, and the only
            option that keeps the app's "nothing leaves this computer" promise.
  openai    any OpenAI-compatible /chat/completions endpoint. One
            implementation covers Groq, OpenRouter, Together, LM Studio,
            llama.cpp's server and Google's OpenAI-compat endpoint — several of
            which have free tiers. Your resume and the job description are sent
            to that provider.
  anthropic the Claude API. Paid, best quality.

Structured output is handled by asking for JSON, validating with the caller's
Pydantic model, and retrying once with the validation error fed back. That
works on every provider, including ones with no schema support.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import (
    ANTHROPIC_API_KEY,
    LLM_PROVIDER,
    MODEL,
    OLLAMA_MODEL,
    OLLAMA_URL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
)

log = logging.getLogger("jobpilot.llm")
T = TypeVar("T", bound=BaseModel)

# Models that take adaptive thinking. Older ones use a token budget instead, so
# we simply omit the parameter for them.
ADAPTIVE_THINKING_MODELS = {
    "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-5", "claude-sonnet-4-6", "claude-fable-5",
}

_anthropic_client = None
_last_error: str = ""
_resolved: str | None = None


# --------------------------------------------------------------------------- #
# provider resolution
# --------------------------------------------------------------------------- #
def _ollama_reachable() -> bool:
    try:
        import httpx

        response = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=2.0)
        return response.status_code == 200
    except Exception:
        return False


def _is_local_endpoint(url: str) -> bool:
    return any(h in (url or "") for h in ("127.0.0.1", "localhost", "0.0.0.0", "::1"))


def _openai_usable() -> bool:
    """Configured well enough to actually answer."""
    if not OPENAI_BASE_URL:
        return False
    return bool(OPENAI_API_KEY) or _is_local_endpoint(OPENAI_BASE_URL)


def _anthropic_usable() -> bool:
    """A key is not enough — the SDK is an optional install."""
    if not ANTHROPIC_API_KEY:
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def provider(refresh: bool = False) -> str:
    """Which backend is actually usable right now. '' means none."""
    global _resolved
    if _resolved is not None and not refresh:
        return _resolved

    choice = (LLM_PROVIDER or "auto").strip().lower()

    if choice == "none":
        _resolved = ""
    elif choice == "ollama":
        _resolved = "ollama" if _ollama_reachable() else ""
    elif choice == "openai":
        # A base URL alone is not enough. Hosted gateways need a key, and
        # reporting "AI available" without one makes every tailoring run fail
        # and fall back silently. Local servers (LM Studio, llama.cpp) need no
        # key, so those are allowed through.
        _resolved = "openai" if _openai_usable() else ""
    elif choice == "anthropic":
        _resolved = "anthropic" if _anthropic_usable() else ""
    else:
        # auto: prefer whatever is configured, local first — it is free and
        # private, so it should never lose to a paid remote by accident.
        if _ollama_reachable():
            _resolved = "ollama"
        elif _openai_usable():
            _resolved = "openai"
        elif _anthropic_usable():
            _resolved = "anthropic"
        else:
            _resolved = ""
    return _resolved


def available() -> bool:
    return bool(provider())


def last_error() -> str:
    return _last_error


def test_connection() -> dict:
    """Real round-trip to the configured provider, for the Settings button.

    Worth its own endpoint: a wrong key or a retired model name otherwise only
    shows up as tailoring quietly falling back mid-run.
    """
    import time

    name = provider(refresh=True)
    if not name:
        return {"ok": False, "provider": "", "error": (
            "No AI provider configured. Set JOBPILOT_LLM_PROVIDER in .env "
            "(and restart), or leave it off to use the rule-based engine."
        )}

    class Ping(BaseModel):
        ok: bool
        greeting: str

    started = time.time()
    result = structured(
        "You reply only with JSON.",
        'Reply exactly: {"ok": true, "greeting": "hello"}',
        Ping,
        max_tokens=200,
    )
    elapsed = int((time.time() - started) * 1000)

    model = {"ollama": OLLAMA_MODEL, "openai": resolve_openai_model(),
             "anthropic": MODEL}.get(name, "")
    if result is None:
        return {"ok": False, "provider": name, "model": model,
                "ms": elapsed, "error": _last_error or "no reply"}
    return {"ok": True, "provider": name, "model": model, "ms": elapsed,
            "sample": result.greeting, "error": ""}


def describe() -> dict:
    """What the UI shows about the current AI setup."""
    name = provider()
    models = {
        "ollama": OLLAMA_MODEL,
        "openai": OPENAI_MODEL or (resolve_openai_model() if name == "openai" else ""),
        "anthropic": MODEL,
    }
    labels = {
        "ollama": "local model (free, private)",
        "openai": "OpenAI-compatible API",
        "anthropic": "Claude API",
        "": "off — rule-based tailoring",
    }
    return {
        "provider": name,
        "model": models.get(name, ""),
        "label": labels.get(name, name),
        "available": bool(name),
        "local": name == "ollama",
        "last_error": _last_error,
    }


# --------------------------------------------------------------------------- #
# JSON helpers
# --------------------------------------------------------------------------- #
def _extract_json(text: str) -> str:
    """Pull a JSON object out of a reply that may be wrapped in prose or fences."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def _schema_instruction(schema: type[BaseModel]) -> str:
    return (
        "Reply with a single JSON object and nothing else — no prose, no code "
        "fences. It must match this JSON Schema exactly:\n"
        + json.dumps(schema.model_json_schema(), indent=1)
    )


# --------------------------------------------------------------------------- #
# backends
# --------------------------------------------------------------------------- #
def _call_ollama(system: str, prompt: str, schema: type[BaseModel], max_tokens: int) -> str:
    import httpx

    payload: dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        # Ollama constrains generation to a JSON Schema when given one.
        "format": schema.model_json_schema(),
        "options": {"num_predict": max_tokens},
    }
    # A local CPU model can take minutes on a long job description.
    response = httpx.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=900.0)
    response.raise_for_status()
    return response.json().get("message", {}).get("content", "")


def _openai_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if OPENAI_API_KEY:
        headers["Authorization"] = f"Bearer {OPENAI_API_KEY}"
    return headers


# Families worth preferring, best first. Hosted providers retire model names
# every few months, so a hard-coded default eventually 404s; asking the provider
# what it currently serves survives that.
_MODEL_PREFERENCE = (
    "llama-3.3-70b", "llama-3.1-70b", "llama3-70b", "qwen", "gemma2-9b",
    "mixtral", "llama-3.1-8b", "llama3-8b", "gpt-oss",
)
_AVOID_IN_MODEL = ("whisper", "tts", "guard", "embed", "vision", "distil")
_model_cache: str = ""


def list_models() -> list[str]:
    """Chat models the configured OpenAI-compatible provider serves."""
    if not OPENAI_BASE_URL:
        return []
    try:
        import httpx

        response = httpx.get(
            OPENAI_BASE_URL.rstrip("/") + "/models",
            headers=_openai_headers(), timeout=20.0,
        )
        response.raise_for_status()
        data = response.json().get("data", [])
        return [m["id"] for m in data if isinstance(m, dict) and m.get("id")]
    except Exception as exc:  # noqa: BLE001
        log.info("could not list models: %s", exc)
        return []


def resolve_openai_model() -> str:
    """The configured model, or the best one the provider actually offers."""
    global _model_cache
    if OPENAI_MODEL:
        return OPENAI_MODEL
    if _model_cache:
        return _model_cache

    usable = [m for m in list_models()
              if not any(bad in m.lower() for bad in _AVOID_IN_MODEL)]
    for wanted in _MODEL_PREFERENCE:
        for name in usable:
            if wanted in name.lower():
                _model_cache = name
                log.info("auto-selected model %s", name)
                return name
    if usable:
        _model_cache = usable[0]
        return _model_cache
    return ""


def _call_openai(system: str, prompt: str, schema: type[BaseModel], max_tokens: int) -> str:
    import httpx

    headers = _openai_headers()
    model = resolve_openai_model()
    if not model:
        raise RuntimeError(
            "No model configured and the provider did not list any. Set "
            "JOBPILOT_OPENAI_MODEL in .env."
        )

    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system + "\n\n" + _schema_instruction(schema)},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    url = OPENAI_BASE_URL.rstrip("/") + "/chat/completions"
    response = httpx.post(url, json=body, headers=headers, timeout=300.0)

    # Not every gateway accepts response_format; retry plainly if it complains.
    if response.status_code == 400 and "response_format" in response.text:
        body.pop("response_format", None)
        response = httpx.post(url, json=body, headers=headers, timeout=300.0)

    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _anthropic() -> Any:
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic package not installed")
        return None
    _anthropic_client = anthropic.Anthropic(
        api_key=ANTHROPIC_API_KEY, timeout=180.0, max_retries=2
    )
    return _anthropic_client


def _call_anthropic(system: str, prompt: str, schema: type[T], max_tokens: int) -> T | None:
    global _last_error
    client = _anthropic()
    if client is None:
        return None

    kwargs: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
        "output_format": schema,
    }
    for attempt in (0, 1):
        try:
            call = dict(kwargs)
            if attempt == 0 and MODEL in ADAPTIVE_THINKING_MODELS:
                call["thinking"] = {"type": "adaptive"}
            response = client.messages.parse(**call)

            if getattr(response, "stop_reason", None) == "refusal":
                details = getattr(response, "stop_details", None)
                _last_error = f"Model declined this request ({getattr(details, 'category', '?')})"
                log.warning(_last_error)
                return None
            parsed = getattr(response, "parsed_output", None)
            if parsed is None:
                _last_error = "Model returned no structured output"
                return None
            _last_error = ""
            return parsed
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if attempt == 0 and "thinking" in message.lower():
                log.info("retrying without adaptive thinking: %s", message)
                continue
            _last_error = f"{type(exc).__name__}: {message[:300]}"
            log.warning("Anthropic call failed: %s", _last_error)
            return None
    return None


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def structured(
    system: str,
    prompt: str,
    schema: type[T],
    max_tokens: int = 16000,
    model: str | None = None,   # kept for callers that pin a model
) -> T | None:
    """One structured call. Returns a validated model, or None on any failure."""
    global _last_error

    backend = provider()
    if not backend:
        return None

    if backend == "anthropic":
        return _call_anthropic(system, prompt, schema, max_tokens)

    caller = _call_ollama if backend == "ollama" else _call_openai
    attempt_prompt = prompt

    # Small local models drift from the schema. Rather than give up, show them
    # the validation error once and let them correct it.
    for attempt in (0, 1):
        try:
            raw = caller(system, attempt_prompt, schema, max_tokens)
        except Exception as exc:  # noqa: BLE001
            _last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            log.warning("%s call failed: %s", backend, _last_error)
            return None

        try:
            parsed = schema.model_validate_json(_extract_json(raw))
            _last_error = ""
            return parsed
        except (ValidationError, ValueError) as exc:
            if attempt == 0:
                log.info("%s returned invalid JSON; asking it to fix", backend)
                attempt_prompt = (
                    f"{prompt}\n\nYour previous reply was not valid for the required "
                    f"schema. The error was:\n{str(exc)[:800]}\n\n"
                    f"{_schema_instruction(schema)}"
                )
                continue
            _last_error = f"{backend} did not return usable JSON: {str(exc)[:200]}"
            log.warning(_last_error)
            return None
    return None
