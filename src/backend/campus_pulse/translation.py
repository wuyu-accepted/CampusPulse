"""Opt-in translation transport for already-public synthetic forum text."""

from __future__ import annotations

from collections import OrderedDict
import json
import os
from pathlib import Path
import ssl
import threading
import urllib.error
import urllib.request

from backend.campus_pulse.provider_config import (
    ProviderConfigError,
    load_provider_credentials,
    provider_chat_completions_url,
)


class TranslationUnavailable(RuntimeError):
    pass


_CACHE: "OrderedDict[tuple[str, str], tuple[str, str]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_LIMIT = 2048


def _credential() -> str:
    direct = os.environ.get("YUNWU_API_KEY", "").strip()
    if direct:
        return direct
    configured = os.environ.get("CAMPUS_PULSE_TRANSLATION_API_KEY_FILE", "").strip()
    if configured:
        secret = Path(configured).expanduser().resolve().read_text(encoding="utf-8").strip()
        if not secret:
            raise TranslationUnavailable("translation credential file is empty")
        return secret
    try:
        _, secret = load_provider_credentials()
    except (ProviderConfigError, OSError, UnicodeError) as exc:
        raise TranslationUnavailable("translation credential is not configured") from exc
    return secret


def _models() -> list[str]:
    configured = os.environ.get("CAMPUS_PULSE_TRANSLATION_MODELS", "").strip()
    if not configured:
        try:
            status, _ = load_provider_credentials()
            configured = str(status.get("model_name", "")).strip()
        except (ProviderConfigError, OSError, UnicodeError):
            configured = "gpt-4o-mini,gpt-5.4-mini,gpt-5.6-luna"
    models = [value.strip() for value in configured.split(",") if value.strip()]
    if not models:
        raise TranslationUnavailable("translation model list is empty")
    return models


def _endpoint() -> str:
    endpoint = os.environ.get("CAMPUS_PULSE_TRANSLATION_ENDPOINT", "").strip()
    if not endpoint:
        try:
            status, _ = load_provider_credentials()
            endpoint = provider_chat_completions_url(str(status["base_url"]))
        except (ProviderConfigError, KeyError, OSError, UnicodeError):
            endpoint = "https://yunwu.ai/v1/chat/completions"
    return endpoint


def _translate_once(text: str, target_language: str, model: str) -> str:
    endpoint = _endpoint()
    proxy = os.environ.get("CAMPUS_PULSE_TRANSLATION_PROXY", "").strip()
    request_body = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Translate public synthetic campus-forum content into clear English. "
                        "Preserve tone, uncertainty, numbers, IDs and quoted claims. "
                        "Return JSON only and add no facts."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"target_language": target_language, "text": text},
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
            "reasoning_effort": "none",
            "response_format": {"type": "json_object"},
            "max_tokens": 1024,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=request_body,
        headers={
            "Authorization": f"Bearer {_credential()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.HTTPSHandler(context=ssl.create_default_context())
    ]
    if proxy:
        handlers.insert(0, urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        decoded = json.loads(content)
        translation = decoded.get("translation") or decoded.get("translated_text")
        if not isinstance(translation, str) or not translation.strip():
            raise TranslationUnavailable("translation response contract failed")
        return translation.strip()
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        raise TranslationUnavailable("translation provider request failed") from exc


def translate_public_synthetic_text(text: str, target_language: str) -> dict[str, str]:
    key = (target_language, text)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached:
            _CACHE.move_to_end(key)
            return {"translation": cached[0], "model": cached[1], "source": "memory_cache"}

    last_error: Exception | None = None
    for model in _models():
        try:
            translated = _translate_once(text, target_language, model)
            with _CACHE_LOCK:
                _CACHE[key] = (translated, model)
                _CACHE.move_to_end(key)
                while len(_CACHE) > _CACHE_LIMIT:
                    _CACHE.popitem(last=False)
            return {"translation": translated, "model": model, "source": "yunwu_live"}
        except (urllib.error.HTTPError, TranslationUnavailable) as exc:
            last_error = exc
    raise TranslationUnavailable("no configured translation model succeeded") from last_error
