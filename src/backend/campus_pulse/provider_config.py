"""Machine-local OpenAI-compatible Provider configuration.

The public configuration and secret are deliberately stored outside the
repository.  Callers can inspect whether a key exists, but this module never
returns the key through a public status object.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


CONFIG_SCHEMA_VERSION = "campus-pulse-local-provider-config-v1"
_CONFIG_FILE_NAME = "provider-config.json"
_KEY_FILE_NAME = "provider-key"
_ALLOWED_REASONING = {"none", "low", "medium", "high"}


class ProviderConfigError(ValueError):
    """Raised when a local Provider configuration is missing or invalid."""


def _config_directory() -> Path:
    configured = os.environ.get("CAMPUS_PULSE_PROVIDER_CONFIG_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".campus-pulse").resolve()


def provider_config_paths() -> tuple[Path, Path]:
    directory = _config_directory()
    return directory / _CONFIG_FILE_NAME, directory / _KEY_FILE_NAME


def _normalize_base_url(value: str) -> str:
    text = value.strip().rstrip("/")
    if text.endswith("/chat/completions"):
        text = text[: -len("/chat/completions")].rstrip("/")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProviderConfigError("base_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderConfigError(
            "base_url must not contain credentials, query parameters, or fragments"
        )
    hostname = parsed.hostname.lower()
    if parsed.scheme == "http" and hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ProviderConfigError(
            "non-loopback Provider endpoints must use HTTPS"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    try:
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows ACLs are inherited from the user-profile directory.  chmod is
        # still attempted for POSIX hosts, but failure must not expose content.
        pass
    os.replace(temporary, path)


def _read_public_file() -> dict:
    config_path, _ = provider_config_paths()
    if not config_path.is_file():
        return {}
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderConfigError("local Provider configuration is unreadable") from exc
    if not isinstance(value, dict):
        raise ProviderConfigError("local Provider configuration is invalid")
    return value


def _key_exists() -> bool:
    _, key_path = provider_config_paths()
    try:
        return key_path.is_file() and key_path.stat().st_size > 0
    except OSError:
        return False


def provider_config_status() -> dict:
    """Return only non-secret configuration metadata."""

    value = _read_public_file()
    has_key = _key_exists()
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "configured": bool(value) and has_key,
        "api_key_present": has_key,
        "api_key_returned": False,
        "storage_scope": "local_machine_user_profile",
        "base_url": value.get("base_url", ""),
        "model_name": value.get("model_name", ""),
        "reasoning_effort": value.get("reasoning_effort", "none"),
        "temperature": value.get("temperature"),
        "max_output_tokens": value.get("max_output_tokens", 512),
        "timeout_seconds": value.get("timeout_seconds", 180),
        "max_concurrency": value.get("max_concurrency", 8),
        "updated_at": value.get("updated_at", ""),
    }


def save_provider_config(
    *,
    base_url: str,
    model_name: str,
    reasoning_effort: str,
    temperature: float | None,
    max_output_tokens: int,
    timeout_seconds: int,
    max_concurrency: int,
    api_key: str | None,
) -> dict:
    normalized_url = _normalize_base_url(base_url)
    normalized_model = model_name.strip()
    if not normalized_model:
        raise ProviderConfigError("model_name must not be empty")
    if reasoning_effort not in _ALLOWED_REASONING:
        raise ProviderConfigError("reasoning_effort is unsupported")

    config_path, key_path = provider_config_paths()
    normalized_key = api_key.strip() if isinstance(api_key, str) else None
    if normalized_key == "":
        raise ProviderConfigError("api_key must not be empty")
    if normalized_key is None and not _key_exists():
        raise ProviderConfigError("api_key is required for the initial configuration")

    public_value = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "base_url": normalized_url,
        "model_name": normalized_model,
        "reasoning_effort": reasoning_effort,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "timeout_seconds": timeout_seconds,
        "max_concurrency": max_concurrency,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    _atomic_write(
        config_path,
        json.dumps(public_value, ensure_ascii=False, indent=2) + "\n",
    )
    if normalized_key is not None:
        _atomic_write(key_path, normalized_key + "\n")
    return provider_config_status()


def load_provider_credentials() -> tuple[dict, str]:
    status = provider_config_status()
    if not status["configured"]:
        raise ProviderConfigError("local Provider configuration is incomplete")
    _, key_path = provider_config_paths()
    try:
        api_key = key_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ProviderConfigError("local Provider key is unreadable") from exc
    if not api_key:
        raise ProviderConfigError("local Provider key is empty")
    return status, api_key


def delete_provider_config() -> dict:
    config_path, key_path = provider_config_paths()
    for path in (config_path, key_path):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ProviderConfigError(
                "local Provider configuration could not be removed"
            ) from exc
    return provider_config_status()


def provider_chat_completions_url(base_url: str) -> str:
    return _normalize_base_url(base_url) + "/chat/completions"
