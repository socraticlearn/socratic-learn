"""Quiet, cached release discovery for learning-session startup."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version

_PYPI_URL = "https://pypi.org/pypi/socratic-learn/json"
_CACHE_NAME = "update-check.json"
_CACHE_TTL_SECONDS = 24 * 60 * 60
_TIMEOUT_SECONDS = 1.0

FetchJson = Callable[[str, float], object]


def _fetch_json(url: str, timeout: float) -> object:
    request = Request(url, headers={"User-Agent": "socratic-learn update-check"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(128 * 1024).decode("utf-8"))


def _read_fresh_cache(path: Path, now: float) -> str | None | object:
    if path.is_symlink():
        return _STALE
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _STALE
    if type(document) is not dict or document.get("schema_version") != 1:
        return _STALE
    checked_at = document.get("checked_at")
    latest = document.get("latest_version")
    if type(checked_at) not in (int, float) or (
        latest is not None and type(latest) is not str
    ):
        return _STALE
    age = now - float(checked_at)
    return latest if 0 <= age < _CACHE_TTL_SECONDS else _STALE


def _write_cache(path: Path, *, checked_at: float, latest: str | None) -> None:
    if path.is_symlink():
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        return
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    payload = json.dumps(
        {
            "schema_version": 1,
            "checked_at": checked_at,
            "latest_version": latest,
        },
        sort_keys=True,
    ) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _latest_from_payload(payload: object) -> str | None:
    if type(payload) is not dict:
        return None
    info = payload.get("info")
    if type(info) is not dict:
        return None
    version = info.get("version")
    return version if type(version) is str else None


def _is_newer(latest: str, current: str) -> bool:
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


def _notice(current: str, latest: str, channel: str | None) -> str:
    command = (
        "npx @socraticlearn/cli@latest"
        if channel == "npm"
        else "pipx upgrade socratic-learn"
    )
    return (
        f"Update available: Socratic Learn {latest} (installed: {current})\n"
        f"Run: {command}"
    )


_STALE: Any = object()


def check_for_update(
    current_version: str,
    *,
    home: Path,
    environ: Mapping[str, str],
    now: float | None = None,
    fetch_json: FetchJson = _fetch_json,
) -> str | None:
    """Return an update notice, or ``None`` without blocking normal startup."""
    if environ.get("SOCRATIC_NO_UPDATE_CHECK") == "1":
        return None
    timestamp = time.time() if now is None else now
    cache_path = home / _CACHE_NAME
    latest = _read_fresh_cache(cache_path, timestamp)
    if latest is _STALE:
        try:
            latest = _latest_from_payload(fetch_json(_PYPI_URL, _TIMEOUT_SECONDS))
        except Exception:
            latest = None
        try:
            _write_cache(cache_path, checked_at=timestamp, latest=latest)
        except Exception:
            pass
    if type(latest) is str and _is_newer(latest, current_version):
        return _notice(
            current_version,
            latest,
            environ.get("SOCRATIC_DISTRIBUTION_CHANNEL"),
        )
    return None
