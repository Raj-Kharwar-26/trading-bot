"""Minimal OpenAI-compatible chat client for the OmniRoute gateway.

Speaks to ``/v1/chat/completions`` over HTTP (httpx). OmniRoute routes the
request to a configured provider (default: the OpenRouter connection). Both
``/v1/chat/completions`` and ``/v1/models`` require no/maybe a Bearer key; with
``REQUIRE_API_KEY=false`` an empty/absent key is accepted.

Free OpenRouter models return 429 + a ``reset_seconds`` cooldown under load, so
a small bounded retry with a configurable model fallback is built in.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from backend.core.config import settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    """Raised when the gateway cannot produce a completion after retries."""


def _assemble_messages(system: str, user: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def complete(
    user_prompt: str,
    *,
    system_prompt: str,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    fallback: bool = True,
    timeout: float = 120.0,
    max_attempts: int = 4,
) -> str:
    """Return the assistant text for a single-turn completion.

    Retries with cooldown-aware backoff (free models 429 + cool down). If the
    primary model is rate-limited it waits out the reset window before retrying
    it, and only falls forward to the configured fallback model when the primary
    is exhausted or errors non-transiently.
    """
    model = model or settings.reasoning_model
    fallback_model = settings.reasoning_model_fallback if fallback else None
    candidates = [model]
    if fallback_model and fallback_model != model:
        candidates.append(fallback_model)

    last_err: Exception | None = None
    for attempt in range(max_attempts):
        for i, candidate in enumerate(candidates):
            try:
                return _complete_once(
                    user_prompt=user_prompt,
                    system_prompt=system_prompt,
                    model=candidate,
                    max_tokens=max_tokens or settings.reasoning_max_tokens,
                    temperature=temperature
                    if temperature is not None
                    else settings.reasoning_temperature,
                    timeout=timeout,
                )
            except LLMRateLimit as exc:
                last_err = exc
                wait = exc.reset_seconds or 20
                wait = min(wait, 90)
                remaining = max_attempts - attempt
                log.warning(
                    "model %s rate-limited; waiting %.0fs (attempt %d/%d)",
                    candidate,
                    wait,
                    attempt + 1,
                    max_attempts,
                )
                time.sleep(wait)
                continue
            except LLMError as exc:
                last_err = exc
                log.error("model %s failed (fatal): %s", candidate, exc)
                continue

    raise LLMError(f"All reasoning models failed: {last_err}") from last_err


class LLMRateLimit(LLMError):
    """Upstream rate limit carrying the cooldown in seconds."""

    def __init__(self, message: str, reset_seconds: int = 0):
        super().__init__(message)
        self.reset_seconds = int(reset_seconds or 0)


def _complete_once(
    *,
    user_prompt: str,
    system_prompt: str,
    model: str,
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> str:
    url = f"{settings.omniroute_base_url.rstrip('/')}/chat/completions"
    body: dict[str, Any] = {
        "model": model,
        "messages": _assemble_messages(system_prompt, user_prompt),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if settings.omniroute_api_key:
        headers["Authorization"] = f"Bearer {settings.omniroute_api_key}"

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body, headers=headers)

    if resp.status_code == 200:
        content = _extract_content(resp)
        if content:
            return content
        raise LLMError("gateway returned 200 with empty content")

    message = _extract_error(resp)
    if resp.status_code == 429:
        reset = _extract_reset_seconds(resp)
        raise LLMRateLimit(f"rate-limited ({resp.status_code}): {message}", reset)
    if resp.status_code >= 500:
        raise LLMRateLimit(f"upstream 5xx ({resp.status_code}): {message}", 10)
    raise LLMError(f"gateway returned {resp.status_code}: {message}")


def _extract_content(resp: httpx.Response) -> str:
    """Extract assistant text from a non-streaming JSON completion, gracefully
    handling responses that came back as an SSE stream (some reasoning models
    stream even with stream:false) or non-JSON payloads."""
    text = resp.text
    if not text:
        return ""

    # SSE stream payloads look like ": OK" / "data: {...}". Concatenate the
    # content deltas so a streamed reasoning model still yields usable text.
    if "data:" in text and "choices" in text and not text.lstrip().startswith("{"):
        parts: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
                delta = chunk.get("choices", [{}])[0].get("delta", {}) or {}
                content = delta.get("content") or ""
                if content:
                    parts.append(content)
            except Exception:  # noqa: BLE001
                continue
        if parts:
            return "".join(parts)

    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return ""

    if not isinstance(data, dict):
        return ""
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        log.warning("Unexpected completion shape: %s", exc)
        return ""


def _extract_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        err = data.get("error")
        if isinstance(err, dict):
            return err.get("message") or str(err)
        return str(err)
    except Exception:  # noqa: BLE001
        return resp.text[:300]


def _extract_reset_seconds(resp: httpx.Response) -> int:
    """Best-effort parse of the reset/cooldown window from a 429 body."""
    retry_after = resp.headers.get("retry-after", "")
    if retry_after:
        try:
            return int(float(retry_after))
        except ValueError:
            pass
    try:
        data = resp.json()
        err = data.get("error") or {}
        if isinstance(err, dict):
            raw = err.get("reset_seconds") or err.get("retry_after") or 0
            if isinstance(raw, (int, float)):
                return int(raw)
            if isinstance(raw, str) and raw.isdigit():
                return int(raw)
        return int(data.get("reset_seconds") or 0)
    except Exception:  # noqa: BLE001
        return 0
