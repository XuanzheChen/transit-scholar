"""Deterministic helpers for Layer2 (hashing, ids, token counting)."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['\-][A-Za-z0-9]+)*")


def sha256_file(path: str | Path) -> str:
    """SHA-256 hex digest of a file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json_hash(payload: dict[str, object]) -> str:
    """Deterministic SHA-256 over a JSON-serializable payload."""
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def count_tokens(text: str) -> int:
    """Deterministic approximate token counter (V1).

    Counts Latin words plus each CJK character as one token, mirroring the
    behaviour of typical multilingual tokenizers well enough for deterministic
    offline chunking tests. Declared approximation, not a real tokenizer.
    """
    tokens = len(_WORD_RE.findall(text))
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff":
            tokens += 1
    return tokens


class SequentialIds:
    """Deterministic id generator for canonical sections/blocks and chunks."""

    def __init__(self, prefix: str, width: int = 5) -> None:
        self._prefix = prefix
        self._width = width
        self._counter = 0

    def next_id(self) -> str:
        self._counter += 1
        return f"{self._prefix}_{self._counter:0{self._width}d}"

    @property
    def count(self) -> int:
        return self._counter


def new_parse_run_id() -> str:
    """Create a fresh parse-run id: ``parse_<utc-ts>-<hex>``."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:10]
    return f"parse_{ts}-{suffix}"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dependency_version(dist: str) -> str | None:
    """Return the installed distribution version or ``None`` when absent.

    Reads the installed package metadata (``importlib.metadata``) rather than
    importing the package, so it is cheap, offline-safe and truthful about
    which version is actually installed. Used by parser adapters so version
    fields in manifests always reflect the real package instead of hardcoded
    ``2.x`` / ``latest`` / ``0.x`` placeholders.
    """
    try:
        from importlib.metadata import version as _version

        return _version(dist)
    except Exception:  # noqa: BLE001 - package metadata lookup failure
        return None


def dependency_version_or(dist: str, fallback: str) -> str:
    """Like ``dependency_version`` but with a stable fallback value."""
    return dependency_version(dist) or fallback
