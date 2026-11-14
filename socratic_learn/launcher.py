"""Interactive host selection and learning-process lifecycle orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unicodedata
from typing import TextIO

from .config import LauncherConfig, load_config, save_config
from .errors import FilesystemError, IntegrityError, UsageError
from .hosts import AgentName, DetectedHost, command_for, detect_hosts
from .sources.ingest import ingest_source
from .sources.model import SourceRequest
from .sources.payload import build_source_study_payload
from .manifest import session_record_contract_payload
from .terminal_menu import MenuChoice, choose_menu_item
from .threads import (
    ThreadRecord,
    create_pending_encounter,
    create_thread,
    discard_pending_encounter,
    discover_legacy_sessions,
    finalize_thread_encounter,
    import_legacy_session,
    list_threads,
    load_thread,
    mark_thread_incomplete,
    parse_session_record,
    threads_root,
)
from .windows_security import (
    safe_regular_windows_file,
    trusted_windows_powershell,
)
from .workspace import (
    launcher_home,
    prepare_here_workspace,
    prepare_personal_workspace,
)


@dataclass(frozen=True)
class LaunchOptions:
    goal: str | None
    agent: AgentName | None
    here: bool
    cwd: Path
    source: SourceRequest | None = None


@dataclass(frozen=True, slots=True)
class _StoredMenuItem:
    value: str
    title: str
    updated_at: str
    next_step: str
    thread_id: str | None = None
    legacy_path: Path | None = None


_NEW_SESSION = "new-session"
_VIEW_ALL = "view-all"
_SOURCE_STUDY_DELIMITER = "\n\n--- SOCRATIC LEARN SOURCE STUDY ---\n"
_CONTROL_START = "--- SOCRATIC LEARN SESSION CONTROL (JSON) ---\n"
_CONTROL_END = "\n--- END SOCRATIC LEARN SESSION CONTROL ---"
_CLEANUP_WARNING = (
    "warning: session checkpoint cleanup did not complete; "
    "the primary launcher outcome was preserved.\n"
)


def _safe_menu_text(text: str, *, fallback: str) -> str:
    rendered = "".join(
        " " if unicodedata.category(character) in {"Cc", "Cf"} else character
        for character in text
    )
    compact = " ".join(rendered.split())
    return compact or fallback


def _stored_menu_items(records: Path) -> tuple[_StoredMenuItem, ...]:
    items: list[_StoredMenuItem] = []
    for summary in list_threads(records):
        items.append(
            _StoredMenuItem(
                value=f"thread:{summary.id}",
                title=summary.display_title,
                updated_at=summary.updated_at,
                next_step=summary.next_step,
                thread_id=summary.id,
            )
        )
    for index, summary in enumerate(discover_legacy_sessions(records)):
        items.append(
            _StoredMenuItem(
                value=f"legacy:{index}",
                title=summary.display_title,
                updated_at=summary.updated_at,
                next_step=summary.next_step,
                legacy_path=summary.path,
            )
        )
    items.sort(key=lambda item: (item.updated_at, item.value), reverse=True)
    return tuple(items)


def _menu_choices(
    items: Sequence[_StoredMenuItem],
    *,
    include_view_all: bool,
) -> tuple[MenuChoice, ...]:
    choices: list[MenuChoice] = [MenuChoice(_NEW_SESSION, "New session")]
    for item in items:
        title = _safe_menu_text(item.title, fallback="Untitled session")
        next_step = _safe_menu_text(item.next_step, fallback="No next step saved")
        activity_date = item.updated_at[:10]
        choices.append(
            MenuChoice(
                item.value,
                title,
                f"{activity_date} · {next_step}",
            )
        )
    if include_view_all:
        choices.append(MenuChoice(_VIEW_ALL, "View all"))
    return tuple(choices)


def _choose_stored_item(
    records: Path,
    *,
    choose_item: Callable[..., str],
    input_fn: Callable[[str], str],
    output: TextIO,
    menu_is_interactive: Callable[[], bool] | None,
) -> _StoredMenuItem | None:
    items = _stored_menu_items(records)
    recent = items[:5]
    include_view_all = len(items) > len(recent)
    choices = _menu_choices(recent, include_view_all=include_view_all)
    selection = choose_item(
        choices,
        output=output,
        input_fn=input_fn,
        is_interactive=menu_is_interactive,
    )
    if selection == _VIEW_ALL:
        selection = choose_item(
            _menu_choices(items, include_view_all=False),
            output=output,
            input_fn=input_fn,
            is_interactive=menu_is_interactive,
        )
    if selection == _NEW_SESSION:
        return None
    by_value = {item.value: item for item in items}
    try:
        return by_value[selection]
    except KeyError as error:
        raise UsageError(
            "session chooser returned an unknown selection",
            remediation="select one of the displayed sessions",
        ) from error


def _encounter_path(records: Path, thread: ThreadRecord) -> Path | None:
    if thread.latest_encounter is None:
        return None
    return (
        threads_root(records)
        / thread.id
        / "sessions"
        / thread.latest_encounter
    ).resolve()


def _build_launch_payload(
    *,
    thread: ThreadRecord,
    pending: Path,
    records: Path,
    is_resume: bool,
    source_study_payload: str | None,
) -> str:
    title = thread.refined_title or thread.provisional_title
    prior = _encounter_path(records, thread) if is_resume else None
    resume: dict[str, object] | None = None
    if is_resume:
        latest_incomplete = thread.status == "incomplete"
        if prior is None:
            prior_role = "none"
        elif latest_incomplete:
            prior_role = "prior_evidence_only"
        else:
            prior_role = "resume_context"
        resume = {
            "latest_launch_incomplete": latest_incomplete,
            "must_reestablish_current_state": latest_incomplete or prior is None,
            "prior_complete_encounter_path": (
                str(prior) if prior is not None else None
            ),
            "prior_complete_encounter_role": prior_role,
            "saved_next_step": thread.next_step or None,
        }
    control = {
        "checkpoint_path": str(pending.resolve()),
        "mode": "resume" if is_resume else "new",
        "resume": resume,
        "schema_version": 1,
        "session_record_contract": session_record_contract_payload(),
        "thread": {
            "goal": thread.original_goal,
            "status": thread.status,
            "title": title,
        },
    }
    trailer = (
        _CONTROL_START
        + json.dumps(
            control,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + _CONTROL_END
    )
    if source_study_payload is not None:
        return (
            _SOURCE_STUDY_DELIMITER.lstrip("\n")
            + source_study_payload
            + "\n\n"
            + trailer
        )
    return trailer


def _write_warning(output: TextIO, message: str) -> None:
    try:
        output.write(message)
        output.flush()
    except BaseException:
        pass


def _add_cleanup_note(primary: BaseException) -> None:
    try:
        primary.add_note(
            "Socratic Learn could not complete checkpoint cleanup; "
            "the primary outcome was preserved."
        )
    except BaseException:
        pass


def _best_effort(
    operation: Callable[[], None],
    *,
    output: TextIO,
    primary: BaseException | None = None,
) -> None:
    try:
        operation()
    except BaseException:
        _write_warning(output, _CLEANUP_WARNING)
        if primary is not None:
            _add_cleanup_note(primary)


def _finish_encounter(
    *,
    records: Path,
    thread_id: str,
    pending: Path,
    output: TextIO,
) -> None:
    try:
        has_content = pending.is_file() and pending.stat().st_size > 0
    except OSError:
        has_content = False
    if has_content:
        try:
            if pending.is_symlink():
                raise IntegrityError("pending checkpoint is a symbolic link")
            parse_session_record(pending.read_text(encoding="utf-8"))
        except (IntegrityError, OSError, UnicodeError):
            _write_warning(
                output,
                "warning: the agent wrote a malformed checkpoint; "
                "the thread remains incomplete.\n",
            )
        else:
            try:
                finalize_thread_encounter(records, thread_id, pending)
            except BaseException as primary:
                try:
                    mark_thread_incomplete(records, thread_id)
                except BaseException:
                    _add_cleanup_note(primary)
                raise
            else:
                return
    _mark_and_discard(records=records, thread_id=thread_id, pending=pending)


def _mark_and_discard(*, records: Path, thread_id: str, pending: Path) -> None:
    first_error: BaseException | None = None
    try:
        mark_thread_incomplete(records, thread_id)
    except BaseException as error:
        first_error = error
    try:
        if pending.is_file() and not pending.is_symlink():
            discard_pending_encounter(records, thread_id, pending)
    except BaseException as error:
        if first_error is None:
            first_error = error
    if first_error is not None:
        raise first_error


def _abandon_pending(*, records: Path, thread_id: str, pending: Path) -> None:
    _mark_and_discard(records=records, thread_id=thread_id, pending=pending)


def _safe_windows_host_argv(
    executable: str,
    argv: tuple[str, ...],
    environ: Mapping[str, str] | None,
) -> tuple[str, ...]:
    if os.name != "nt" or Path(executable).suffix.lower() not in {".cmd", ".bat"}:
        return argv
    batch = safe_regular_windows_file(
        Path(executable),
        label="detected Windows batch shim",
    )
    counterpart = batch.with_suffix(".ps1")
    if not counterpart.exists():
        raise UsageError(
            "a Windows batch host requires a sibling PowerShell counterpart",
            path=batch,
            remediation=f"install a regular non-linked shim at {counterpart}",
        )
    script = safe_regular_windows_file(
        counterpart,
        label="PowerShell counterpart",
    )
    powershell = trusted_windows_powershell(environ)
    return (
        str(powershell),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *argv[1:],
    )


def _prompt_for_agent(
    detected: Mapping[AgentName, DetectedHost],
    *,
    input_fn: Callable[[str], str],
    output: TextIO,
) -> AgentName:
    names = tuple(detected)
    output.write("Available agents:\n")
    for number, name in enumerate(names, start=1):
        output.write(f"{number}. {name}\n")

    while True:
        answer = input_fn("Select an agent by number: ")
        try:
            index = int(answer)
        except ValueError:
            continue
        if 1 <= index <= len(names) and answer.strip() == str(index):
            return names[index - 1]


def run_learning(
    options: LaunchOptions,
    *,
    input_fn: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
    which: Callable[[str], str | None] = shutil.which,
    run_process: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    environ: Mapping[str, str] | None = None,
    choose_item: Callable[..., str] = choose_menu_item,
    menu_is_interactive: Callable[[], bool] | None = None,
) -> int:
    """Select an installed host, prepare a workspace, and run the child."""
    detected = detect_hosts(which)
    if not detected:
        raise UsageError(
            "no compatible agent was found",
            remediation="install Pi, Claude Code, or Antigravity and retry",
        )
    if options.agent is not None and options.agent not in detected:
        raise UsageError(
            f"requested agent '{options.agent}' was not detected",
            remediation="choose an installed compatible agent",
        )

    home = launcher_home(environ)
    workspace = None
    selected_item: _StoredMenuItem | None = None
    bare_launch = options.goal is None and options.source is None
    if bare_launch:
        workspace = (
            prepare_here_workspace(options.cwd)
            if options.here
            else prepare_personal_workspace(home)
        )
        selected_item = _choose_stored_item(
            workspace.records,
            choose_item=choose_item,
            input_fn=input_fn,
            output=output,
            menu_is_interactive=menu_is_interactive,
        )

    goal = options.goal
    if goal is None and selected_item is None:
        prompt = (
            "What do you want to be able to do with this source?"
            if options.source is not None
            else "What do you want to learn? "
        )
        goal = input_fn(prompt)
    if goal is not None and not goal.strip():
        raise UsageError(
            "a learning goal is required",
            remediation="provide a non-empty learning goal",
        )

    config_path = home / "config.json"
    config = load_config(config_path)

    selected = options.agent
    if selected is None and config.default_agent in detected:
        selected = config.default_agent
    elif selected is None:
        if config.default_agent is not None:
            output.write(
                f"Saved agent '{config.default_agent}' is no longer available.\n"
            )
        selected = _prompt_for_agent(
            detected,
            input_fn=input_fn,
            output=output,
        )
        save_config(config_path, LauncherConfig(default_agent=selected))

    if workspace is None:
        workspace = (
            prepare_here_workspace(options.cwd)
            if options.here
            else prepare_personal_workspace(home)
        )

    is_resume = selected_item is not None
    if selected_item is None:
        assert goal is not None
        thread = create_thread(workspace.records, goal=goal)
    elif selected_item.thread_id is not None:
        thread = load_thread(workspace.records, selected_item.thread_id)
    else:
        assert selected_item.legacy_path is not None
        thread = import_legacy_session(workspace.records, selected_item.legacy_path)

    pending = create_pending_encounter(workspace.records, thread.id)
    source_payload: str | None = None
    if options.source is not None:
        assert goal is not None
        try:
            ingested = ingest_source(workspace.records, options.source)
            bundle_path = ingested.bundle_path.resolve()
            source_payload = build_source_study_payload(
                learner_goal=goal,
                index_path=bundle_path / "index.md",
                metadata_path=bundle_path / "metadata.json",
            )
        except BaseException as primary:
            _best_effort(
                lambda: _abandon_pending(
                    records=workspace.records,
                    thread_id=thread.id,
                    pending=pending,
                ),
                output=output,
                primary=primary,
            )
            raise

    launch_payload = _build_launch_payload(
        thread=thread,
        pending=pending,
        records=workspace.records,
        is_resume=is_resume,
        source_study_payload=source_payload,
    )

    host = detected[selected]
    try:
        argv = command_for(
            host.adapter,
            host.executable,
            workspace.protocol,
            launch_payload,
        )
        argv = _safe_windows_host_argv(host.executable, argv, environ)
    except BaseException as primary:
        _best_effort(
            lambda: _abandon_pending(
                records=workspace.records,
                thread_id=thread.id,
                pending=pending,
            ),
            output=output,
            primary=primary,
        )
        raise

    try:
        result = run_process(list(argv), cwd=workspace.root, check=False)
    except KeyboardInterrupt:
        _best_effort(
            lambda: _finish_encounter(
                records=workspace.records,
                thread_id=thread.id,
                pending=pending,
                output=output,
            ),
            output=output,
        )
        return 130
    except FileNotFoundError as error:
        launcher_error = FilesystemError(
            f"agent '{selected}' executable disappeared before launch",
            path=host.executable,
            remediation="restore the executable or select another agent",
        )
        _best_effort(
            lambda: _finish_encounter(
                records=workspace.records,
                thread_id=thread.id,
                pending=pending,
                output=output,
            ),
            output=output,
            primary=launcher_error,
        )
        raise launcher_error from error
    except BaseException as primary:
        _best_effort(
            lambda: _finish_encounter(
                records=workspace.records,
                thread_id=thread.id,
                pending=pending,
                output=output,
            ),
            output=output,
            primary=primary,
        )
        raise
    _best_effort(
        lambda: _finish_encounter(
            records=workspace.records,
            thread_id=thread.id,
            pending=pending,
            output=output,
        ),
        output=output,
    )
    return result.returncode
