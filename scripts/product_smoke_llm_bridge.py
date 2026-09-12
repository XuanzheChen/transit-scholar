"""Loopback LLM bridge for the real end-to-end product smoke (T-010).

Why this exists
---------------
The local application talks to its configured OpenAI-compatible provider
through ``transit_scholar.layer2.schema_extraction.llm.OpenAICompatibleLLMClient``.
The provider configured for this workstation (``https://opencode.ai/zen/go/v1``)
rejects requests that do not carry an ``x-opencode-session`` routing header
(HTTP 400 ``MissingSessionID``), and the frozen client sends only
``Authorization``/``Content-Type``.

``TRANSIT_SCHOLAR_LLM_BASE_URL`` is a supported configuration knob, so the
real-product smoke points the application at this transparent loopback bridge
instead of editing frozen layers. The bridge performs no faking: it forwards the
request body to the real provider, unchanged, adds only the routing header the
provider requires, and returns the provider's real status, body and headers.
Real provider tokens and latency are therefore consumed by the run.

Usage::

    python scripts/product_smoke_llm_bridge.py \\
        --target https://opencode.ai/zen/go/v1 \\
        --local-base-path /v1 [--port 0] [--session <id>]

With ``--port 0`` the chosen port is printed to stdout as
``LLM_BRIDGE_PORT=<port>`` and the process serves until it is terminated.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable

import httpx

DEFAULT_TARGET = "https://opencode.ai/zen/go/v1"
DEFAULT_HEADER = "x-opencode-session"

#: Headers answered by the bridge itself and never forwarded upstream.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

#: Truncation used for the request-side observation recorded in the JSONL log.
_PROMPT_HEAD_CHARS = 200


def _prompt_head(request_payload: dict) -> str | None:
    """Record the leading system instruction of a forwarded request.

    The bridge only observes; it never rewrites the request. Recording the
    prompt head lets a caller prove *which* application-level call the provider
    answered (each role uses a distinct prompt template).
    """
    messages = request_payload.get("messages")
    if not isinstance(messages, list):
        return None
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return " ".join(content.split())[:_PROMPT_HEAD_CHARS]
    return None


def build_handler(
    target: str,
    local_base_path: str,
    extra_headers: dict[str, str],
    log_path: Path | None = None,
    log_bodies: bool = False,
    upstream_proxy: str | None = None,
):
    """Build the forwarding request handler class for one target."""
    target = target.rstrip("/")
    local_base_path = "/" + local_base_path.strip("/") if local_base_path.strip("/") else ""
    log_lock = threading.Lock()

    def record(entry: dict) -> None:
        if log_path is None:
            return
        entry["at"] = time.time()
        line = json.dumps(entry, ensure_ascii=False)
        with log_lock:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    class _ForwardHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "TransitScholarLLMBridge/1.0"

        # The bridge is a test transport; keep stderr quiet by default.
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib signature
            if getattr(self.server, "verbose", False):
                super().log_message(fmt, *args)

        def _upstream_url(self) -> str:
            path = self.path
            if local_base_path and path.startswith(local_base_path):
                path = path[len(local_base_path) :]
            if not path.startswith("/"):
                path = "/" + path
            return target + path

        def _forward(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in _HOP_BY_HOP
            }
            headers.update(extra_headers)
            url = self._upstream_url()
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(600.0),
                    trust_env=False,
                    proxy=upstream_proxy,
                ) as client:
                    upstream = client.request(
                        self.command, url, headers=headers, content=body
                    )
            except httpx.HTTPError as exc:
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                payload = f'{{"error": {{"message": "bridge transport error: {exc}"}}}}'.encode()
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            response_body = upstream.content
            try:
                request_payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                request_payload = {}
            record(
                {
                    "method": self.command,
                    "path": self.path,
                    "model": request_payload.get("model"),
                    "prompt_head": _prompt_head(request_payload),
                    "response_format": (request_payload.get("response_format") or {}).get("type")
                    if isinstance(request_payload.get("response_format"), dict)
                    else None,
                    "status": upstream.status_code,
                    "response_bytes": len(response_body),
                    "error": (
                        response_body.decode("utf-8", errors="replace")[:400]
                        if upstream.status_code != 200
                        else None
                    ),
                }
            )
            if log_bodies:
                record(
                    {
                        "kind": "body",
                        "path": self.path,
                        "response_body": response_body.decode("utf-8", errors="replace")[:4000],
                        "request_messages": [
                            {
                                "role": message.get("role"),
                                "content": str(message.get("content"))[:1500],
                            }
                            for message in (request_payload.get("messages") or [])
                        ],
                    }
                )
            self.send_response(upstream.status_code)
            for key, value in upstream.headers.items():
                # ``content-encoding`` is dropped because httpx already decoded
                # the body; forwarding the header would make the application
                # decode plain bytes a second time.
                if key.lower() in _HOP_BY_HOP or key.lower() == "content-encoding":
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        do_GET = _forward
        do_POST = _forward

    return _ForwardHandler


def start_bridge(
    *,
    target: str = DEFAULT_TARGET,
    local_base_path: str = "/v1",
    port: int = 0,
    session: str | None = None,
    extra_headers: Iterable[tuple[str, str]] = (),
    log_path: Path | str | None = None,
    log_bodies: bool = False,
    upstream_proxy: str | None = None,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Start the bridge on ``port`` (0 picks a free port) and return it."""
    headers = {DEFAULT_HEADER: session or str(uuid.uuid4())}
    headers.update({key: value for key, value in extra_headers})
    handler = build_handler(
        target,
        local_base_path,
        headers,
        log_path=Path(log_path) if log_path is not None else None,
        log_bodies=log_bodies,
        upstream_proxy=upstream_proxy,
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="llm-bridge", daemon=True)
    thread.start()
    return server, thread


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=DEFAULT_TARGET, help="real provider base URL")
    parser.add_argument(
        "--local-base-path",
        default="/v1",
        help="path prefix the application appends before /chat/completions",
    )
    parser.add_argument("--port", type=int, default=0, help="0 picks a free loopback port")
    parser.add_argument("--session", default=None, help="value for the routing header")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="extra header to add to every upstream request",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--log-file",
        default=None,
        help="optional JSONL file recording one line per forwarded provider request",
    )
    parser.add_argument(
        "--upstream-proxy",
        default=None,
        help="optional explicit HTTP proxy for upstream egress (env proxies are ignored)",
    )
    args = parser.parse_args(argv)

    extra = []
    for item in args.header:
        name, _, value = item.partition("=")
        extra.append((name.strip(), value.strip()))

    server, _thread = start_bridge(
        target=args.target,
        local_base_path=args.local_base_path,
        port=args.port,
        session=args.session,
        extra_headers=extra,
        log_path=args.log_file,
        upstream_proxy=args.upstream_proxy,
    )
    server.verbose = args.verbose  # type: ignore[attr-defined]
    host, port = server.server_address[:2]
    sys.stdout.write(f"LLM_BRIDGE_PORT={port}\n")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
