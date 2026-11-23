"""Persisted launcher host preference without starting a session."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TextIO

from socratic_learn.config import LauncherConfig, load_config, save_config
from socratic_learn.errors import LearningTutorError, UsageError
from socratic_learn.hosts import ADAPTERS, AgentName, detect_hosts
from socratic_learn.workspace import launcher_home

HostCommand = str | tuple[str, AgentName]


def _host_option_tokens() -> frozenset[str]:
    return frozenset({"--here"})


def parse_host_arguments(arguments: Sequence[str]) -> HostCommand:
    """Parse `host` subcommands from argv that may include launcher options."""
    positionals: list[str] = []
    index = 0
    argv = list(arguments)
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            positionals.extend(argv[index + 1 :])
            break
        if argument == "--agent":
            if index + 1 >= len(argv):
                raise UsageError(
                    "argument --agent: expected one argument",
                    remediation="run 'socratic host list|set|clear'",
                )
            index += 2
            continue
        if argument.startswith("--agent="):
            index += 1
            continue
        if argument in _host_option_tokens():
            index += 1
            continue
        if argument.startswith("-"):
            raise UsageError(
                f"unrecognized arguments: {argument}",
                remediation="run 'socratic --help'",
            )
        positionals.append(argument)
        index += 1

    if not positionals or positionals[0] != "host":
        raise UsageError(
            "host command was not found",
            remediation="use 'socratic host list|set|clear'",
        )

    rest = positionals[1:]
    if not rest:
        raise UsageError(
            "host command requires a subcommand",
            remediation="use 'socratic host list|set|clear'",
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
        if len(rest) != 2:
            raise UsageError(
                "host set requires a host name",
                remediation="use 'socratic host set NAME'",
            )
        name = rest[1]
        for agent in ADAPTERS:
            if agent == name:
                return ("set", agent)
        raise UsageError(
            f"invalid host '{name}'",
            remediation="choose one of: pi, claude, antigravity",
        )

    raise UsageError(
        f"unknown host subcommand '{subcommand}'",
        remediation="use 'socratic host list|set|clear'",
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


def _command_clear(*, environ: Mapping[str, str]) -> None:
    config_path = _config_path(environ)
    load_config(config_path)
    save_config(config_path, LauncherConfig(default_agent=None))


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
        if command == "list":
            _command_list(which=which, environ=environment, stdout=out)
        elif command == "clear":
            _command_clear(environ=environment)
        else:
            assert isinstance(command, tuple)
            _command_set(command[1], which=which, environ=environment)
        return 0
    except LearningTutorError as error:
        print(f"error: {error}", file=err)
        return error.exit_code