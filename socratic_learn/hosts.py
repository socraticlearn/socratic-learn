"""Pure host detection and argument-vector construction."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from types import MappingProxyType
from typing import Literal

LAUNCH_PAYLOAD_DELIMITER = "\n\n--- SOCRATIC LEARN LAUNCH ---\n"
# Kept as an import-compatible alias for source-study smoke tooling.
READ_MODE_DELIMITER = LAUNCH_PAYLOAD_DELIMITER

AgentName = Literal["pi", "claude", "antigravity"]

BuildArgv = Callable[[str, Path, str], tuple[str, ...]]


@dataclass(frozen=True)
class HostAdapter:
    name: AgentName
    executable_names: tuple[str, ...]
    build_argv: BuildArgv


@dataclass(frozen=True)
class DetectedHost:
    adapter: HostAdapter
    executable: str


def _pi(
    executable: str,
    protocol: Path,
    launch_payload: str,
) -> tuple[str, ...]:
    return executable, "--append-system-prompt", str(protocol), launch_payload


def _claude(
    executable: str,
    protocol: Path,
    launch_payload: str,
) -> tuple[str, ...]:
    return executable, "--append-system-prompt-file", str(protocol), launch_payload


def _antigravity(
    executable: str,
    protocol: Path,
    launch_payload: str,
) -> tuple[str, ...]:
    protocol_control = json.dumps(
        {"protocol_path": str(protocol)},
        ensure_ascii=True,
        sort_keys=True,
    )
    bootstrap = (
        "Before responding, read and follow the Socratic Learn protocol "
        "whose path is encoded as JSON data:\n"
        f"{protocol_control}.{LAUNCH_PAYLOAD_DELIMITER}{launch_payload}"
    )
    return executable, "--prompt-interactive", bootstrap


ADAPTERS: Mapping[AgentName, HostAdapter] = MappingProxyType(
    {
        "pi": HostAdapter("pi", ("pi",), _pi),
        "claude": HostAdapter("claude", ("claude",), _claude),
        "antigravity": HostAdapter("antigravity", ("agy",), _antigravity),
    }
)


def detect_hosts(
    which: Callable[[str], str | None],
) -> dict[AgentName, DetectedHost]:
    """Detect each adapter's first available executable in canonical order."""
    detected: dict[AgentName, DetectedHost] = {}
    for name, adapter in ADAPTERS.items():
        for executable_name in adapter.executable_names:
            executable = which(executable_name)
            if executable is not None:
                detected[name] = DetectedHost(adapter, executable)
                break
    return detected


def command_for(
    adapter: HostAdapter,
    executable: str,
    protocol: Path,
    launch_payload: str,
    source_study_payload: str | None = None,
) -> tuple[str, ...]:
    """Build an argument vector without invoking a process."""
    final_payload = (
        launch_payload
        if source_study_payload is None
        else source_study_payload
    )
    if not final_payload.strip():
        raise ValueError("launch_payload must be nonblank")
    return adapter.build_argv(executable, protocol, final_payload)
