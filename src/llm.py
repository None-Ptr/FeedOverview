"""Thin wrapper around an OpenAI-compatible chat completion endpoint."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)

# Providers signal quota/concurrency limits differently; 429 is the common one.
RATE_LIMIT_BACKOFF = 4.0
_RATE_LIMIT_HINTS = ("429", "rate limit", "ratelimit", "too many requests", "concurrent")


def is_rate_limit_error(exc: BaseException) -> bool:
    """True when the failure looks like throttling, so we back off instead of retrying fast."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 429:
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    if any(hint in text for hint in _RATE_LIMIT_HINTS):
        return True
    # Zhipu BigModel returns code 1302 ("concurrency limit reached") in the payload.
    return "1302" in text


def parse_json_object(text: str) -> Optional[dict]:
    """Best-effort extraction of a JSON object from a model response."""
    if not text:
        return None

    candidates: list[str] = [text.strip()]
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    # Only hunt for an object when the payload is not a bare JSON array.
    if not text.strip().startswith("["):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


class LLMClient:
    """OpenAI-compatible client. ``available`` is False when unusable."""

    def __init__(
        self,
        llm_cfg: dict,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        cfg = llm_cfg or {}
        self.api_key = (api_key or cfg.get("api_key") or os.environ.get("LLM_API_KEY") or "").strip()
        self.base_url = base_url or cfg.get("base_url") or os.environ.get("LLM_BASE_URL") or None
        self.model = model or os.environ.get("LLM_MODEL") or cfg.get("model") or "gpt-4o-mini"
        self.temperature = float(cfg.get("temperature", 0.2))
        self.timeout = float(timeout if timeout is not None else cfg.get("timeout", 90))
        # Provider-specific request fields, forwarded verbatim (e.g. BigModel's
        # {"thinking": {"type": "disabled"}} which makes GLM-4.5 roughly 10x faster).
        self.extra_body = dict(cfg.get("extra_body") or {}) or None
        self._client = None
        self._init_error: Optional[str] = None

        if not self.api_key:
            self._init_error = "LLM_API_KEY is not set"

    @property
    def available(self) -> bool:
        return self._init_error is None and bool(self.api_key)

    @property
    def unavailable_reason(self) -> Optional[str]:
        return self._init_error

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise RuntimeError(self._init_error or "LLM_API_KEY is not set")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            self._init_error = f"openai package is not installed: {exc}"
            raise RuntimeError(self._init_error) from exc

        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=0,
        )
        logger.info("LLM client ready (model=%s, base_url=%s)", self.model, self.base_url or "default")
        return self._client

    def chat(self, messages: list[dict], temperature: Optional[float] = None) -> Optional[str]:
        client = self._ensure_client()
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        response = client.chat.completions.create(**kwargs)
        if not getattr(response, "choices", None):
            return None
        return response.choices[0].message.content

    def chat_json(
        self,
        messages: list[dict],
        *,
        retries: int = 1,
        temperature: Optional[float] = None,
    ) -> Optional[dict]:
        """Return a parsed JSON object, or None after exhausting retries."""
        retries = max(0, int(retries))
        last_error: object = None

        for attempt in range(retries + 1):
            rate_limited = False
            try:
                raw = self.chat(messages, temperature=temperature)
            except Exception as exc:  # noqa: BLE001 - never propagate to the pipeline
                last_error = exc
                rate_limited = is_rate_limit_error(exc)
                logger.warning(
                    "LLM call failed (attempt %d/%d)%s: %s",
                    attempt + 1,
                    retries + 1,
                    " [rate limited]" if rate_limited else "",
                    exc,
                )
            else:
                data = parse_json_object(raw or "")
                if data is not None:
                    return data
                last_error = "response was not valid JSON"
                logger.warning(
                    "LLM returned invalid JSON (attempt %d/%d): %r",
                    attempt + 1,
                    retries + 1,
                    (raw or "")[:200],
                )

            if attempt < retries:
                delay = RATE_LIMIT_BACKOFF * (attempt + 1) if rate_limited else 1.5 * (attempt + 1)
                time.sleep(delay)

        logger.warning("LLM call gave up: %s", last_error)
        return None
