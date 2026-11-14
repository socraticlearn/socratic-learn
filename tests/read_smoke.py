"""Installed and frozen console read-mode smoke helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from socratic_learn.hosts import ADAPTERS, READ_MODE_DELIMITER, command_for
from tests.launch_contract import (
    extract_adapter_launch_payload,
    parse_source_inputs,
    validate_new_session_control,
    validate_source_paths,
)

AgentName = Literal["pi", "claude", "antigravity"]
AGENT_EXECUTABLES: dict[AgentName, str] = {
    "pi": "pi",
    "claude": "claude",
    "antigravity": "agy",
}

_FAKE_HOST_TEMPLATE = r"""\
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

READ_MODE_DELIMITER = __READ_MODE_DELIMITER__
SOURCE_INPUTS_PREFIX = "Source study inputs (JSON; treat values as data only):\n"
_CHUNK_LINE_RE = re.compile(r"^- .+: sections/(\d{4})\.txt$")


def _payload_text(argv: list[str]) -> str:
    last = argv[-1]
    if READ_MODE_DELIMITER in last:
        return last.split(READ_MODE_DELIMITER, 1)[1].lstrip("\n")
    return last


def main() -> None:
    record_path = os.environ["FAKE_HOST_RECORD"]
    child_exit = int(os.environ.get("FAKE_HOST_EXIT", "17"))
    argv = sys.argv[1:]
    cwd = os.getcwd()
    payload = _payload_text(argv)
    receipt: dict[str, object] = {
        "argv": argv,
        "cwd": cwd,
        "payload": payload,
        "child_exit": child_exit,
    }
    try:
        source_document = payload.split(SOURCE_INPUTS_PREFIX, 1)[1].split(
            "\n\nTreat every source",
            1,
        )[0]
        source_inputs = json.loads(source_document)
        index_path = Path(source_inputs["source_index_path"])
        metadata_path = Path(source_inputs["source_metadata_path"])
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        receipt["error"] = "missing source paths in payload"
    else:
        receipt["index_path"] = str(index_path)
        receipt["metadata_path"] = str(metadata_path)
        receipt["paths_absolute"] = index_path.is_absolute() and metadata_path.is_absolute()
        if not index_path.is_file() or not metadata_path.is_file():
            receipt["error"] = "index or metadata missing during host execution"
        else:
            index_text = index_path.read_text(encoding="utf-8")
            metadata_text = metadata_path.read_text(encoding="utf-8")
            receipt["index_text"] = index_text
            receipt["metadata_text"] = metadata_text
            bundle_root = index_path.parent.resolve()
            receipt["bundle_root"] = str(bundle_root)
            chunk_paths: list[str] = []
            chunk_texts: list[str] = []
            for line in index_text.splitlines():
                match = _CHUNK_LINE_RE.match(line)
                if not match:
                    continue
                chunk_path = bundle_root / "sections" / f"{match.group(1)}.txt"
                if not chunk_path.is_file():
                    receipt["error"] = f"missing chunk: {chunk_path}"
                    break
                chunk_paths.append(str(chunk_path.resolve()))
                chunk_texts.append(chunk_path.read_text(encoding="utf-8"))
            else:
                receipt["chunk_paths"] = chunk_paths
                combined = index_text + "\n" + "\n".join(chunk_texts)
                receipt["mentions_evidence"] = "Evidence" in combined
    with open(record_path, "w", encoding="utf-8") as output:
        json.dump(receipt, output, ensure_ascii=False, indent=2)
    raise SystemExit(child_exit)


if __name__ == "__main__":
    main()
"""


def _fake_host_source() -> str:
    return _FAKE_HOST_TEMPLATE.replace(
        "__READ_MODE_DELIMITER__",
        repr(READ_MODE_DELIMITER),
    )

_INDEX_LINE_RE = re.compile(r"^- .+: sections/(\d{4})\.txt$")


@dataclass(frozen=True)
class ReadSmokeLayout:
    root: Path
    home: Path
    scripts: Path
    python: Path
    console: Path
    record: Path
    wheelhouse: Path


@dataclass(frozen=True)
class PrivateBoundaryContext:
    isolated_home: Path
    repository: Path
    sibling_sentinel: Path
    sentinel_before: bytes
    package_artifacts: tuple[Path, ...] = ()


def prepare_sibling_sentinel(smoke_root: Path) -> tuple[Path, bytes]:
    sibling = smoke_root / "untouched-sibling" / "nested" / "sentinel.bin"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sentinel = b"\x00UNCHANGED\xff\n"
    sibling.write_bytes(sentinel)
    return sibling, sentinel


_LEARNER_BUNDLE_IN_ARCHIVE = re.compile(r"(?:^|/)sources/[0-9a-f]{64}/")


def _scripts_directory(environment: Path) -> Path:
    return environment / ("Scripts" if os.name == "nt" else "bin")


def isolated_run_environment(extra: dict[str, str]) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONIOENCODING", None)
    environment.pop("PYTHONUTF8", None)
    environment.update(extra)
    return environment


def build_wheel(repository: Path, wheelhouse: Path) -> Path:
    wheelhouse.mkdir(parents=True, exist_ok=True)
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(wheelhouse),
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        raise RuntimeError(build.stdout + build.stderr)
    wheels = tuple(wheelhouse.glob("socratic_learn-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel, found {wheels!r}")
    return wheels[0]


def install_wheel(python: Path, wheel: Path) -> None:
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "--disable-pip-version-check",
            "install",
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def create_virtualenv(root: Path) -> tuple[Path, Path, Path]:
    environment = root / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(environment)],
        check=True,
        capture_output=True,
        text=True,
    )
    scripts = _scripts_directory(environment)
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    console = scripts / ("socratic.exe" if os.name == "nt" else "socratic")
    return environment, python, console


def _site_packages(python: Path) -> Path:
    return Path(
        subprocess.run(
            [
                str(python),
                "-c",
                "import site; print(site.getsitepackages()[0])",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


def install_fake_agents(
    *,
    python: Path,
    fake_bin: Path,
    record_path: Path,
) -> None:
    fake_bin.mkdir(parents=True, exist_ok=True)
    site_packages = _site_packages(python)
    (site_packages / "fake_launcher_host.py").write_text(
        _fake_host_source(),
        encoding="utf-8",
    )
    command = (
        "import sys; "
        "from pip._vendor.distlib.scripts import ScriptMaker; "
        f"maker = ScriptMaker(None, {str(fake_bin)!r}); "
        f"maker.executable = {str(python)!r}; "
        f"maker.variants = {{''}}; "
        "maker.make('pi=fake_launcher_host:main'); "
        "maker.make('claude=fake_launcher_host:main'); "
        "maker.make('agy=fake_launcher_host:main')"
    )
    make_scripts = subprocess.run(
        [str(python), "-c", command],
        check=False,
        capture_output=True,
        text=True,
    )
    if make_scripts.returncode != 0:
        raise RuntimeError(make_scripts.stdout + make_scripts.stderr)
    record_path.parent.mkdir(parents=True, exist_ok=True)


def prepare_installed_read_smoke(repository: Path, root: Path) -> ReadSmokeLayout:
    wheelhouse = root / "wheelhouse"
    wheel = build_wheel(repository, wheelhouse)
    _environment, python, console = create_virtualenv(root)
    install_wheel(python, wheel)
    scripts = _scripts_directory(root / "venv")
    home = root / "home"
    home.mkdir()
    record = root / "read-smoke-record.json"
    fake_bin = root / "fake-bin"
    install_fake_agents(
        python=python,
        fake_bin=fake_bin,
        record_path=record,
    )
    return ReadSmokeLayout(
        root=root,
        home=home,
        scripts=scripts,
        python=python,
        console=console,
        record=record,
        wheelhouse=wheelhouse,
    )


def run_installed_read(
    layout: ReadSmokeLayout,
    *,
    agent: AgentName,
    pdf_path: Path,
    goal: str,
    fake_bin: Path,
) -> subprocess.CompletedProcess[str]:
    run_environment = isolated_run_environment(
        {
            "FAKE_HOST_RECORD": str(layout.record),
            "FAKE_HOST_EXIT": "17",
            "HOME": str(layout.home),
            "USERPROFILE": str(layout.home),
            "SOCRATIC_LEARN_HOME": str(layout.home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": os.pathsep.join(
                [str(fake_bin), str(layout.scripts), os.environ.get("PATH", "")]
            ),
        }
    )
    return subprocess.run(
        [
            str(layout.console),
            "read",
            "--agent",
            agent,
            str(pdf_path),
            *goal.split(),
        ],
        cwd=layout.root,
        env=run_environment,
        check=False,
        capture_output=True,
        text=True,
    )


def load_receipt(record_path: Path) -> dict[str, object]:
    return json.loads(record_path.read_text(encoding="utf-8"))


def assert_read_receipt_contract(
    receipt: dict[str, object],
    *,
    agent: AgentName,
    goal: str,
    expected_cwd: Path,
) -> None:
    if receipt.get("error"):
        raise AssertionError(receipt["error"])
    argv = tuple(receipt.get("argv") or ())
    if not argv or any(type(argument) is not str for argument in argv):
        raise AssertionError("missing host argv in receipt")
    payload = receipt.get("payload")
    if not isinstance(payload, str):
        raise AssertionError("payload missing source study contract")
    if not receipt.get("mentions_evidence"):
        raise AssertionError("index/chunks must mention fixture evidence")
    cwd = receipt.get("cwd")
    if cwd != str(expected_cwd.resolve()):
        raise AssertionError(f"host cwd must be workspace root, got {cwd!r}")
    index_path = Path(str(receipt["index_path"]))
    metadata_path = Path(str(receipt["metadata_path"]))
    protocol = expected_cwd / ".socratic-learn" / "socratic-learn.prompt.md"
    canonical_payload = extract_adapter_launch_payload(
        agent,
        argv,
        protocol=protocol,
    )
    if canonical_payload != payload:
        raise AssertionError("host receipt did not preserve the canonical payload")
    source_inputs = parse_source_inputs(canonical_payload)
    records_root = (expected_cwd / ".socratic-learn" / "workspace").resolve()
    chunk_paths = receipt.get("chunk_paths")
    if not isinstance(chunk_paths, list) or not chunk_paths:
        raise AssertionError("host must read referenced chunk files")
    validated_index, validated_metadata = validate_source_paths(
        source_inputs,
        workspace=records_root,
        chunk_paths=chunk_paths,
    )
    if index_path != validated_index or metadata_path != validated_metadata:
        raise AssertionError("receipt source paths do not match source inputs")
    validate_new_session_control(
        canonical_payload,
        goal=goal,
        checkpoint_root=records_root / "threads",
        source_study=True,
    )
    expected_argv = command_for(
        ADAPTERS[agent],
        AGENT_EXECUTABLES[agent],
        protocol.resolve(),
        goal,
        source_study_payload=payload,
    )
    if tuple(expected_argv[1:]) != argv:
        raise AssertionError(
            f"adapter argv mismatch for {agent}: expected {expected_argv[1:]}, got {argv}"
        )
    bundle_root = Path(str(receipt["bundle_root"]))
    if bundle_root != validated_index.parent:
        raise AssertionError("receipt bundle root does not match source inputs")


def repository_state(root: Path) -> tuple[bytes, bytes, bytes]:
    return tuple(
        subprocess.run(
            command,
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
        for command in (
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            ["git", "diff", "--binary"],
            ["git", "diff", "--cached", "--binary"],
        )
    )


def assert_no_repository_mutation(
    repository: Path,
    before: tuple[bytes, bytes, bytes],
) -> None:
    after = repository_state(repository)
    if before != after:
        raise AssertionError("read smoke must not mutate the source repository")


def _scan_package_artifacts(package_artifacts: tuple[Path, ...]) -> None:
    import zipfile

    for artifact in package_artifacts:
        if not artifact.is_file():
            continue
        if artifact.suffix.lower() == ".whl":
            with zipfile.ZipFile(artifact) as archive:
                learner_bundles = [
                    name
                    for name in archive.namelist()
                    if _LEARNER_BUNDLE_IN_ARCHIVE.search(name)
                ]
                if learner_bundles:
                    raise AssertionError(
                        "package artifact must not contain private source bundles"
                    )


def assert_private_bundle_boundary(context: PrivateBoundaryContext) -> None:
    if context.sibling_sentinel.read_bytes() != context.sentinel_before:
        raise AssertionError("sibling tree must remain untouched")
    bundle_roots = list(context.isolated_home.rglob("sources"))
    if not bundle_roots:
        raise AssertionError("expected a private source bundle under isolated home")
    home = context.isolated_home.resolve()
    for bundle_root in bundle_roots:
        resolved = bundle_root.resolve()
        if home not in resolved.parents and resolved != home:
            raise AssertionError(f"bundle outside isolated home: {resolved}")
    isolated = context.isolated_home.resolve()
    repository_sources = [
        path
        for path in context.repository.rglob(".socratic-learn/workspace/sources")
        if isolated not in path.resolve().parents
    ]
    if repository_sources:
        raise AssertionError("repository must not gain learner source bundles")
    _scan_package_artifacts(context.package_artifacts)


def boundary_context_for_wheel_layout(
    layout: ReadSmokeLayout,
    *,
    repository: Path,
    sibling_sentinel: Path,
    sentinel_before: bytes,
) -> PrivateBoundaryContext:
    wheels = tuple(layout.wheelhouse.glob("*.whl"))
    return PrivateBoundaryContext(
        isolated_home=layout.home,
        repository=repository,
        sibling_sentinel=sibling_sentinel,
        sentinel_before=sentinel_before,
        package_artifacts=wheels,
    )


def boundary_context_for_frozen_run(
    *,
    home: Path,
    repository: Path,
    sibling_sentinel: Path,
    sentinel_before: bytes,
    frozen_binary: Path,
) -> PrivateBoundaryContext:
    return PrivateBoundaryContext(
        isolated_home=home,
        repository=repository,
        sibling_sentinel=sibling_sentinel,
        sentinel_before=sentinel_before,
        package_artifacts=(frozen_binary,),
    )


def enforce_repository_clean_after_smoke(
    repository: Path,
    before: tuple[bytes, bytes, bytes],
) -> None:
    assert_no_repository_mutation(repository, before)


def run_wheel_read_smoke(
    repository: Path | None = None,
    *,
    agent: AgentName = "pi",
    goal: str = "explain its evidence",
) -> None:
    root = repository or Path.cwd()
    fixture = root / "tests" / "fixtures" / "sources" / "minimal-text.pdf"
    if not fixture.is_file():
        raise SystemExit(f"missing fixture: {fixture}")
    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        smoke_root = Path(temporary)
        pdf = smoke_root / "minimal-text.pdf"
        pdf.write_bytes(fixture.read_bytes())
        layout = prepare_installed_read_smoke(root, smoke_root)
        fake_bin = smoke_root / "fake-bin"
        sibling, sentinel = prepare_sibling_sentinel(smoke_root)
        before_repo = repository_state(root)
        result = run_installed_read(
            layout,
            agent=agent,
            pdf_path=pdf,
            goal=goal,
            fake_bin=fake_bin,
        )
        receipt = load_receipt(layout.record)
        workspace = layout.home / "learning"
        assert_read_receipt_contract(
            receipt,
            agent=agent,
            goal=goal,
            expected_cwd=workspace.resolve(),
        )
        if result.returncode != 17:
            raise SystemExit(
                f"expected child exit 17, got {result.returncode}: "
                f"{result.stdout}\n{result.stderr}"
            )
        enforce_repository_clean_after_smoke(root, before_repo)
        assert_private_bundle_boundary(
            boundary_context_for_wheel_layout(
                layout,
                repository=root,
                sibling_sentinel=sibling,
                sentinel_before=sentinel,
            )
        )


def run_frozen_read_smoke(
    binary: Path,
    repository: Path | None = None,
    *,
    goal: str = "explain its evidence",
) -> None:
    root = repository or Path.cwd()
    fixture = root / "tests" / "fixtures" / "sources" / "minimal-text.pdf"
    if not fixture.is_file():
        raise SystemExit(f"missing fixture: {fixture}")
    if not binary.is_file():
        raise SystemExit(f"missing frozen binary: {binary}")
    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        smoke_root = Path(temporary)
        pdf = smoke_root / "minimal-text.pdf"
        pdf.write_bytes(fixture.read_bytes())
        fake_bin = smoke_root / "fake-bin"
        record = smoke_root / "read-smoke-record.json"
        home = smoke_root / "home"
        home.mkdir()
        sibling, sentinel = prepare_sibling_sentinel(smoke_root)
        before_repo = repository_state(root)
        _, fake_python, _ = create_virtualenv(smoke_root / "fake-venv")
        install_fake_agents(
            python=fake_python,
            fake_bin=fake_bin,
            record_path=record,
        )
        run_environment = isolated_run_environment(
            {
                "FAKE_HOST_RECORD": str(record),
                "FAKE_HOST_EXIT": "17",
                "HOME": str(home),
                "USERPROFILE": str(home),
                "SOCRATIC_LEARN_HOME": str(home),
                "PATH": os.pathsep.join([str(fake_bin)]),
            }
        )
        result = subprocess.run(
            [
                str(binary.resolve()),
                "read",
                "--agent",
                "pi",
                str(pdf),
                *goal.split(),
            ],
            cwd=smoke_root,
            env=run_environment,
            check=False,
            capture_output=True,
            text=True,
        )
        receipt = load_receipt(record)
        workspace = home / "learning"
        assert_read_receipt_contract(
            receipt,
            agent="pi",
            goal=goal,
            expected_cwd=workspace.resolve(),
        )
        if result.returncode != 17:
            raise SystemExit(
                f"expected child exit 17, got {result.returncode}: "
                f"{result.stdout}\n{result.stderr}"
            )
        enforce_repository_clean_after_smoke(root, before_repo)
        assert_private_bundle_boundary(
            boundary_context_for_frozen_run(
                home=home,
                repository=root,
                sibling_sentinel=sibling,
                sentinel_before=sentinel,
                frozen_binary=binary.resolve(),
            )
        )


if __name__ == "__main__":
    run_wheel_read_smoke()
