"""Body-free Yunwu connectivity and model smoke test.

The credential is read only from an environment variable.  Output contains
HTTP status, latency, model identifiers, token counts and content hashes, but
never the credential, request prompt, or Provider response text.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import socket
import ssl
import time
from typing import Any
from urllib.parse import urlparse


ALLOWED_HOSTS = {"yunwu.ai"}
ALLOWED_LOCAL_PROXIES = {"http://127.0.0.1:7897"}
DEFAULT_MODELS = ("gpt-5.6-luna", "gpt-5.4-nano")


class _ResolvedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, connect_ip: str, *, timeout: float) -> None:
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self._connect_ip = connect_ip

    def connect(self) -> None:
        raw = socket.create_connection(
            (self._connect_ip, self.port), timeout=self.timeout
        )
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _connection(
    host: str,
    *,
    timeout: float,
    connect_ip: str | None,
    proxy_url: str | None,
):
    if proxy_url is not None:
        parsed_proxy = urlparse(proxy_url)
        connection = http.client.HTTPSConnection(
            str(parsed_proxy.hostname),
            int(parsed_proxy.port or 80),
            timeout=timeout,
        )
        connection.set_tunnel(host, port=443)
        return connection
    if connect_ip is None:
        return http.client.HTTPSConnection(host, timeout=timeout)
    return _ResolvedHTTPSConnection(host, connect_ip, timeout=timeout)


def _request_json(
    *,
    host: str,
    path: str,
    method: str,
    api_key: str,
    timeout: float,
    connect_ip: str | None,
    proxy_url: str | None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | None, float]:
    encoded = None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Connection": "close",
    }
    if body is not None:
        encoded = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection = _connection(
        host,
        timeout=timeout,
        connect_ip=connect_ip,
        proxy_url=proxy_url,
    )
    started = time.perf_counter()
    try:
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        raw = response.read(2_000_001)
        elapsed = round(time.perf_counter() - started, 3)
        if len(raw) > 2_000_000:
            return int(response.status), None, elapsed
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = None
        return int(response.status), decoded if isinstance(decoded, dict) else None, elapsed
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://yunwu.ai/v1")
    parser.add_argument("--api-key-env", default="YUNWU_API_KEY")
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--connect-ip")
    parser.add_argument("--proxy-url")
    parser.add_argument("--execute-chat", action="store_true")
    arguments = parser.parse_args()

    parsed = urlparse(arguments.base_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.query
        or parsed.fragment
        or not 1 <= arguments.timeout_seconds <= 60
    ):
        raise SystemExit("refusing an unapproved Yunwu endpoint or timeout")
    if arguments.connect_ip is not None:
        try:
            socket.inet_aton(arguments.connect_ip)
        except OSError as exc:
            raise SystemExit("connect-ip must be an IPv4 address") from exc
    if (
        arguments.proxy_url is not None
        and arguments.proxy_url not in ALLOWED_LOCAL_PROXIES
    ):
        raise SystemExit("refusing an unapproved proxy URL")
    if arguments.connect_ip is not None and arguments.proxy_url is not None:
        raise SystemExit("connect-ip and proxy-url are mutually exclusive")
    api_key = os.environ.get(arguments.api_key_env, "").strip()
    if not api_key.startswith("sk-"):
        raise SystemExit("API key environment variable is unavailable")

    prefix = parsed.path.rstrip("/")
    models_path = prefix + "/models"
    try:
        status, document, elapsed = _request_json(
            host=str(parsed.hostname),
            path=models_path,
            method="GET",
            api_key=api_key,
            timeout=arguments.timeout_seconds,
            connect_ip=arguments.connect_ip,
            proxy_url=arguments.proxy_url,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "stage": "models",
                    "status": "transport_error",
                    "error_type": type(exc).__name__,
                    "connect_ip_override": arguments.connect_ip is not None,
                    "local_proxy": arguments.proxy_url is not None,
                    "prompt_sent": False,
                },
                sort_keys=True,
            )
        )
        return 2

    available = set()
    if status == 200 and isinstance(document, dict):
        rows = document.get("data")
        if isinstance(rows, list):
            available = {
                str(item["id"])
                for item in rows
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
    candidates = tuple(dict.fromkeys(arguments.models or DEFAULT_MODELS))
    summary: dict[str, Any] = {
        "stage": "models",
        "http_status": status,
        "latency_seconds": elapsed,
        "connect_ip_override": arguments.connect_ip is not None,
        "local_proxy": arguments.proxy_url is not None,
        "candidate_presence": {model: model in available for model in candidates},
        "available_model_count": len(available),
        "prompt_sent": False,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if status != 200 or not arguments.execute_chat:
        return 0 if status == 200 else 3

    chat_path = prefix + "/chat/completions"
    for model in candidates:
        if model not in available:
            continue
        request = {
            "model": model,
            "reasoning_effort": "none",
            "messages": [
                {
                    "role": "user",
                    "content": "Return exactly one JSON object with key ok and value true.",
                }
            ],
            "max_tokens": 32,
            "stream": False,
        }
        try:
            chat_status, response, chat_elapsed = _request_json(
                host=str(parsed.hostname),
                path=chat_path,
                method="POST",
                api_key=api_key,
                timeout=arguments.timeout_seconds,
                connect_ip=arguments.connect_ip,
                proxy_url=arguments.proxy_url,
                body=request,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "stage": "chat",
                        "model": model,
                        "status": "transport_error",
                        "error_type": type(exc).__name__,
                        "prompt_sent": True,
                        "automatic_retry": False,
                    },
                    sort_keys=True,
                )
            )
            return 4
        content = None
        usage = {}
        returned_model = None
        if isinstance(response, dict):
            returned_model = response.get("model")
            if isinstance(response.get("usage"), dict):
                usage = response["usage"]
            try:
                content = response["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                content = None
        print(
            json.dumps(
                {
                    "stage": "chat",
                    "model": model,
                    "http_status": chat_status,
                    "latency_seconds": chat_elapsed,
                    "returned_model": returned_model,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "content_sha256": (
                        hashlib.sha256(str(content).encode("utf-8")).hexdigest()
                        if content is not None
                        else None
                    ),
                    "prompt_sent": True,
                    "automatic_retry": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
