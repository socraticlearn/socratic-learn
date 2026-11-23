"""Persisted launcher host preference without starting a session."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal, TextIO

from socratic_learn.config import LauncherConfig, load_config, save_config
from socratic_learn.errors import LearningTutorError, UsageError
from socratic_learn.hosts import ADAPTERS, AgentName, detect_hosts
from socratic_learn.workspace import launcher_home

HostCommand = Literal["list", "clear"] | tuple[Literal["set"], AgentName]

_LAUNCHER_ONLY_OPTIONS = ("--agent", "--here", "--allow-private-network")
_HOST_USAGE = "socratic host list|set NAME|clear"
_LEARN_HOST_ESCAPE = (
    "to learn about 'host' as a topic, run 'socratic learn host ...'"
)


def _host_remediation(detail: str | None = None) -> str:
    parts = [f"use '{_HOST_USAGE}'"]
    if detail is not None:
        parts.append(detail)
    parts.append(_LEARN_HOST_ESCAPE)
    return "; ".join(parts)


def _launcher_only_option(argument: str) -> str | None:
    for option in _LAUNCHER_ONLY_OPTIONS:
        if argument == option or argument.startswith(f"{option}="):
            return option
    return None


def parse_host_arguments(arguments: Sequence[str]) -> HostCommand:
    """Parse `host` subcommands; launcher-only options are not accepted."""
    positionals: list[str] = []
    argv = list(arguments)
    for index, argument in enumerate(argv):
        if argument == "--":
            positionals.extend(argv[index + 1 :])
            break
        option = _launcher_only_option(argument)
        if option is not None:
            raise UsageError(
                f"{option} cannot be used with host commands",
                remediation=_host_remediation(
                    f"remove {option}; for a one-session host choice during "
                    "learning, run 'socratic --agent NAME'"
                ),
            )
        if argument.startswith("-"):
            raise UsageError(
                f"unrecognized arguments: {argument}",
                remediation=_host_remediation(),
            )
        positionals.append(argument)

    if not positionals or positionals[0] != "host":
        raise UsageError(
            "host command was not found",
            remediation=f"use '{_HOST_USAGE}'",
        )

    rest = positionals[1:]
    if not rest:
        raise UsageError(
            "host command requires a subcommand",
            remediation=_host_remediation(),
        )

    subcommand = rest[0]
    if subcommand == "list":
        if len(rest) > 1:
            raise UsageError(
                "too many arguments for 'host list'",
                remediation="run 'socratic host list'",
            )
        return "list"

    if subcommand == "clear":
        if len(rest) > 1:
            raise UsageError(
                "too many arguments for 'host clear'",
                remediation="run 'socratic host clear'",
            )
        return "clear"

    if subcommand == "set":
        if len(rest) < 2:
            raise UsageError(
                "host set requires a host name",
                remediation="use 'socratic host set NAME'",
            )
        if len(rest) > 2:
            raise UsageError(
                "too many arguments for 'host set'",
                remediation="use 'socratic host set NAME' with exactly one host name",
            )
        name = rest[1]
        for agent in ADAPTERS:
            if agent == name:
                return ("set", agent)
        raise UsageError(
            f"invalid host '{name}'",
            remediation=f"choose one of: {', '.join(ADAPTERS)}",
        )

    raise UsageError(
        f"unknown host subcommand '{subcommand}'",
        remediation=_host_remediation(),
    )


def _config_path(environ: Mapping[str, str]) -> Path:
    return launcher_home(environ) / "config.json"


def _format_list_line(
    name: AgentName,
    *,
    installed: bool,
    executable: str | None,
    is_default: bool,
) -> str:
    if installed and executable is not None:
        line = f"{name}: installed ({executable})"
    else:
        line = f"{name}: missing"
    if is_default:
        line += " [default]"
    return line


def _command_list(
    *,
    which: Callable[[str], str | None],
    environ: Mapping[str, str],
    stdout: TextIO,
) -> None:
    detected = detect_hosts(which)
    config = load_config(_config_path(environ))
    for name in ADAPTERS:
        host = detected.get(name)
        stdout.write(
            _format_list_line(
                name,
                installed=host is not None,
                executable=None if host is None else host.executable,
                is_default=config.default_agent == name,
            )
            + "\n"
        )


def _command_set(
    name: AgentName,
    *,
    which: Callable[[str], str | None],
    environ: Mapping[str, str],
    stdout: TextIO,
) -> None:
    config_path = _config_path(environ)
    load_config(config_path)
    detected = detect_hosts(which)
    if name not in detected:
        raise UsageError(
            f"host '{name}' was not detected",
            remediation="install the host executable and retry, or run 'socratic host list'",
        )
    save_config(config_path, LauncherConfig(default_agent=name))
    stdout.write(f"Default host set to {name}.\n")


def _command_clear(*, environ: Mapping[str, str], stdout: TextIO) -> None:
    config_path = _config_path(environ)
    load_config(config_path)
    save_config(config_path, LauncherConfig(default_agent=None))
    stdout.write("Default host cleared.\n")


def run_host_cli(
    arguments: Sequence[str],
    *,
    which: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run `host list|set|clear` without launcher session side effects."""
    environment = os.environ if environ is None else environ
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    try:
        command = parse_host_arguments(arguments)
        if isinstance(command, tuple):
            _command_set(command[1], which=which, environ=environment, stdout=out)
        elif command == "list":
            _command_list(which=which, environ=environment, stdout=out)
        else:
            _command_clear(environ=environment, stdout=out)
        return 0
    except LearningTutorError as error:
        print(f"error: {error}", file=err)
        return error.exit_code
