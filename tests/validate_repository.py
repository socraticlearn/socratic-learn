from __future__ import annotations

import ast
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from socratic_learn.build import build_distribution
from socratic_learn.errors import LearningTutorError
from socratic_learn.manifest import validate_manifest
from evaluation.sanitize import assert_tracked_safe

REQUIRED_PATHS = (
    "README.md",
    "LICENSE",
    "AGENTS.md",
    ".gitattributes",
    ".gitignore",
    "core/pedagogy.md",
    "core/session-protocol.md",
    "core/assessment.md",
    "core/evidence-policy.md",
    "curricula/mathematics/map.md",
    "curricula/ai/map.md",
    "templates/learner-profile.md",
    "templates/learning-goal.md",
    "templates/session-log.md",
    "templates/progress.md",
    "adapters/cursor/README.md",
    "adapters/cursor/skills/socratic-learn/SKILL.md",
    "adapters/claude-code/CLAUDE.md",
    "adapters/claude-code/README.md",
    "adapters/pi/skills/socratic-learn/SKILL.md",
    "adapters/pi/skills/socratic-learn/SKILL.project.md.template",
    "adapters/pi/README.md",
    "workspace.example/learner-profile.md",
    "workspace.example/learning-goal.md",
    "workspace.example/progress.md",
    "workspace.example/sessions/2026-01-15-example.md",
    "workspace/.gitkeep",
    "learning_tutor/__init__.py",
    "learning_tutor/__main__.py",
    "socratic_learn/__init__.py",
    "socratic_learn/__main__.py",
    "socratic_learn/build.py",
    "socratic_learn/conformance.py",
    "socratic_learn/doctor.py",
    "socratic_learn/errors.py",
    "socratic_learn/export.py",
    "socratic_learn/install.py",
    "socratic_learn/manifest.py",
    "socratic_learn/templates/generic-loader.md",
    "socratic_learn/templates/cursor-skill.md",
    "socratic_learn/templates/claude-loader.md",
    "socratic_learn/templates/pi-skill.md",
    "dist/socratic-learn.prompt.md",
    "dist/manifest.json",
    "docs/universal-installation.md",
    "docs/launcher-adapters.md",
    "docs/manifest-reference.md",
    "docs/adding-an-adapter.md",
    "docs/source-study.md",
    ".github/workflows/native-python.yml",
    "pyproject.toml",
    ".github/workflows/release.yml",
)
RETIRED_NATIVE_RELEASE_TERMS = (
    "pyinstaller",
    "socratic-learn.spec",
    "release-manifest.schema.json",
    "scripts/install.ps1",
    "scripts/install.sh",
    "socratic_learn.release",
)
SKILL_PATHS = (
    "adapters/cursor/skills/socratic-learn/SKILL.md",
    "adapters/pi/skills/socratic-learn/SKILL.md",
)
POSITIONING_SKILL_PATHS = (
    "adapters/cursor/skills/socratic-learn/SKILL.md",
    "adapters/pi/skills/socratic-learn/SKILL.md",
    "adapters/pi/skills/socratic-learn/SKILL.project.md.template",
    "socratic_learn/templates/cursor-skill.md",
    "socratic_learn/templates/pi-skill.md",
)
README_POSITIONING_REQUIREMENTS = (
    "open-source tutoring protocol",
    "launcher",
    "portable session memory",
    "any subject",
    "starter curricula",
)
README_FORBIDDEN_POSITIONING_TERMS = (
    "agent-agnostic",
    "agentic learning framework",
)
README_TAGLINE = "Learn any subject. Prove it by doing."
README_OPENING_PROMISE = "learn any subject"
README_UNFRIENDLY_DASHES = ("—", "–")
README_PORTFOLIO_REQUIREMENTS = (
    "open-source tutoring protocol",
    "launcher",
    "portable session memory",
    "real application",
    "private learner records",
    "source-bounded study",
    "research cited in the docs informed design choices",
)
README_HOST_MANAGEMENT_REQUIREMENTS = (
    "socratic host list",
    "socratic host set",
    "socratic host clear",
    "persistent default",
    "one-session",
)
README_EVALUATION_REQUIREMENTS = (
    "evaluation/fixtures/rubric.json",
    "evaluation/reports/article-reading-v1.md",
    "synthetic",
    "do not prove",
    "human learning outcomes",
    "retention",
    "superiority",
    "host alone",
)
SOURCE_STUDY_SCOPE_REQUIREMENTS = (
    "one source",
    "multi-source",
)
MANUAL_LAUNCHER_CONFIG_PATTERN = re.compile(
    r"(?:edit|update)\s+`?~/?\.socratic-learn/config\.json`?",
    re.IGNORECASE,
)
_MARKDOWN_WHITESPACE = r"[ \t\r\n]+"
_PRODUCT_NAME = rf"Socratic{_MARKDOWN_WHITESPACE}Learn"
_EFFECTIVENESS_RESULT = rf"improves?{_MARKDOWN_WHITESPACE}learning"
_EVIDENCE_SUBJECT = r"(?:research|evidence|studies|literature)"
_EVIDENCE_VERB = r"(?:shows?|demonstrates?|proves?|validates?)"

# This is intentionally a lexical grammar, not natural-language inference.
# Markdown whitespace may separate its words. Positive forms cover product-led
# validation/improvement claims and evidence-led attribution to the product.
README_UNSUPPORTED_EFFECTIVENESS_PATTERNS = (
    re.compile(
        rf"\b{_PRODUCT_NAME}{_MARKDOWN_WHITESPACE}"
        rf"(?:is{_MARKDOWN_WHITESPACE})?"
        rf"(?:proven|scientifically{_MARKDOWN_WHITESPACE}validated)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_PRODUCT_NAME}{_MARKDOWN_WHITESPACE}"
        rf"{_EFFECTIVENESS_RESULT}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_EVIDENCE_SUBJECT}{_MARKDOWN_WHITESPACE}"
        rf"{_EVIDENCE_VERB}{_MARKDOWN_WHITESPACE}"
        rf"(?:that{_MARKDOWN_WHITESPACE})?{_PRODUCT_NAME}\b",
        re.IGNORECASE,
    ),
)
# Explicit lexical negations suppress positive submatches contained inside
# them. Other wording is deliberately outside this narrow grammar.
README_NEGATED_EFFECTIVENESS_PATTERNS = (
    re.compile(
        rf"\bno{_MARKDOWN_WHITESPACE}{_EVIDENCE_SUBJECT}"
        rf"{_MARKDOWN_WHITESPACE}{_EVIDENCE_VERB}{_MARKDOWN_WHITESPACE}"
        rf"(?:that{_MARKDOWN_WHITESPACE})?{_PRODUCT_NAME}"
        rf"(?:{_MARKDOWN_WHITESPACE}{_EFFECTIVENESS_RESULT})?\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_EVIDENCE_SUBJECT}{_MARKDOWN_WHITESPACE}"
        rf"does{_MARKDOWN_WHITESPACE}not{_MARKDOWN_WHITESPACE}"
        rf"(?:show|demonstrate|prove|validate){_MARKDOWN_WHITESPACE}"
        rf"(?:that{_MARKDOWN_WHITESPACE})?{_PRODUCT_NAME}"
        rf"(?:{_MARKDOWN_WHITESPACE}{_EFFECTIVENESS_RESULT})?\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_PRODUCT_NAME}{_MARKDOWN_WHITESPACE}"
        rf"(?:is{_MARKDOWN_WHITESPACE})?not{_MARKDOWN_WHITESPACE}"
        rf"(?:proven|scientifically{_MARKDOWN_WHITESPACE}validated)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_PRODUCT_NAME}{_MARKDOWN_WHITESPACE}"
        rf"does{_MARKDOWN_WHITESPACE}not{_MARKDOWN_WHITESPACE}"
        rf"improve{_MARKDOWN_WHITESPACE}learning\b",
        re.IGNORECASE,
    ),
)
RUNTIME_PDF_DEPENDENCY = "pypdf>=7.0,<8"
RUNTIME_VERSION_DEPENDENCY = "packaging>=26.1,<27"
RUNTIME_DEPENDENCIES = [
    RUNTIME_VERSION_DEPENDENCY,
    RUNTIME_PDF_DEPENDENCY,
]
WHEEL_READ_SMOKE_MARKERS = (
    "tests.read_smoke",
    "run_wheel_read_smoke",
    "socratic read --agent pi",
    "minimal-text.pdf",
)
INTERPRETER_ONLY_PDF_SMOKE_MARKERS = (
    "from pypdf import PdfReader",
    "PdfReader",
)
SOURCE_STUDY_REQUIREMENTS = (
    "socratic read https://example.org/article",
    "socratic read paper.pdf",
    "socratic read --agent pi",
    "--allow-private-network",
    "socratic learn",
    "launcher host",
    "provider",
    "model",
    "~/.socratic-learn/learning/.socratic-learn/workspace/",
    ".socratic-learn/workspace/sources/",
    "reuses",
    "20 mib",
    "500 pdf pages",
    "5,000,000",
    "20-second",
    "five redirects",
    "12,000",
    "no ocr",
    "no javascript",
    "html",
    "xhtml",
  "pdf",
    "source assertion",
    "within-source support",
    "external verification",
    "source-bounded",
    "explicit",
    "best-effort",
    "not a sandbox",
    "image-only",
    "dynamic pages",
    "quota",
    "change agents",
    "socratic host list",
    "socratic host set",
    "socratic host clear",
)
README_STANDARD_LIBRARY_RUNTIME_CLAIM = (
    "standard-library python 3.11+ with native and pipx"
)
README_LAUNCHER_REQUIREMENTS = (
    "socratic",
    'socratic "i want to learn linear algebra"',
    "socratic --agent pi",
    "socratic --here",
    'pipx install "git+https://github.com/socraticlearn/socratic-learn.git"',
    "pipx install socratic-learn",
    "npx @socraticlearn/cli",
    "python 3.11",
    "pipx",
    "pi",
    "claude code",
    "antigravity",
    "cursor",
    "socratic install",
    "does not read or store provider credentials",
    "https://github.com/socraticlearn/socratic-learn",
)
MENU_SKIP_HERE_PATTERN = re.compile(
    r"menu unless[^.\n]*--here",
    re.IGNORECASE,
)
SESSION_PROTOCOL_RESUME_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "prior evidence only",
        re.compile(r"prior_evidence_only", re.IGNORECASE),
    ),
    (
        "must reestablish state",
        re.compile(r"must_reestablish_current_state", re.IGNORECASE),
    ),
    (
        "interrupted not demonstrated",
        re.compile(
            r"do not treat an interrupted launch.{0,160}demonstrated evidence",
            re.DOTALL | re.IGNORECASE,
        ),
    ),
    (
        "retrieval before current state",
        re.compile(
            r"cannot establish current demonstrated state.{0,240}retrieval",
            re.DOTALL | re.IGNORECASE,
        ),
    ),
)
SESSION_PROTOCOL_CLOSE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "checkpoint close",
        re.compile(
            r"checkpoint path.{0,120}exactly one.{0,80}valid session record.{0,60}at close",
            re.DOTALL,
        ),
    ),
    (
        "subject focus title",
        re.compile(r"<subject>: <specific focus>", re.IGNORECASE),
    ),
)
SESSION_PROTOCOL_CONVERSATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "orchestration privacy",
        re.compile(r"checkpoint paths.{0,120}session control", re.DOTALL),
    ),
)
SESSION_PROTOCOL_PORTABLE_PATTERNS = (
    *SESSION_PROTOCOL_RESUME_PATTERNS,
    *SESSION_PROTOCOL_CLOSE_PATTERNS,
    *SESSION_PROTOCOL_CONVERSATION_PATTERNS,
)
README_PORTABLE_SESSION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("menu new session", re.compile(r"\bnew session\b", re.IGNORECASE)),
    ("recent threads", re.compile(r"\brecent threads\b", re.IGNORECASE)),
    ("structured summaries", re.compile(r"structured summaries", re.IGNORECASE)),
    (
        "host chat exclusion",
        re.compile(r"not provider chat logs", re.IGNORECASE),
    ),
)
LAUNCHER_SESSION_CONTROL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("checkpoint path", re.compile(r"`checkpoint_path`", re.IGNORECASE)),
    (
        "session record contract",
        re.compile(r"`session_record_contract`", re.IGNORECASE),
    ),
    ("heading pattern", re.compile(r"`heading_pattern`", re.IGNORECASE)),
    ("section headings", re.compile(r"`section_headings`", re.IGNORECASE)),
    (
        "prior encounter path",
        re.compile(r"`prior_complete_encounter_path`", re.IGNORECASE),
    ),
    (
        "prior encounter role",
        re.compile(r"`prior_complete_encounter_role`", re.IGNORECASE),
    ),
    (
        "must reestablish",
        re.compile(r"`must_reestablish_current_state`", re.IGNORECASE),
    ),
    ("host independent resume", re.compile(r"host[- ]independent", re.IGNORECASE)),
    (
        "structured summaries",
        re.compile(r"structured session summaries", re.IGNORECASE),
    ),
    (
        "not provider chat",
        re.compile(r"not provider chat history", re.IGNORECASE),
    ),
    (
        "session control delimiter",
        re.compile(
            r"--- socratic learn session control \(json\) ---",
            re.IGNORECASE,
        ),
    ),
    (
        "launch delimiter",
        re.compile(r"--- socratic learn launch ---", re.IGNORECASE),
    ),
)
MANIFEST_REFERENCE_TOP_LEVEL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "protocol version",
        re.compile(
            r"`protocol_version`: exact protocol version `1\.3\.0`",
            re.IGNORECASE,
        ),
    ),
)
MANIFEST_REFERENCE_CONFORMANCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "conformance version",
        re.compile(r"`conformance\.version` is integer `5`", re.IGNORECASE),
    ),
)
MANIFEST_REFERENCE_VERSION_POLICY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "exact conformance requirement",
        re.compile(
            r"require the exact current `conformance\.version`",
            re.IGNORECASE,
        ),
    ),
    (
        "older not current",
        re.compile(r"not silently treated as current", re.IGNORECASE),
    ),
)
MANIFEST_REFERENCE_VERSION_PATTERNS = (
    *MANIFEST_REFERENCE_TOP_LEVEL_PATTERNS,
    *MANIFEST_REFERENCE_CONFORMANCE_PATTERNS,
    *MANIFEST_REFERENCE_VERSION_POLICY_PATTERNS,
)
PYPI_RELEASE_CAVEAT = "the pypi package is not public yet."
NPM_RELEASE_CAVEAT = "the npm package is not public yet."
BRANDED_REPOSITORY_URL = "https://github.com/socraticlearn/socratic-learn"
README_ONBOARDING_ORDER = (
    'pipx install "git+https://github.com/socraticlearn/socratic-learn.git"',
    "\nsocratic\n",
    'socratic "i want to learn linear algebra"',
    "socratic --agent pi",
    "socratic --here",
    "socratic read https://example.org/article",
)
RETIRED_ACTIVE_INSTALLATION_TERMS = (
    "install.ps1",
    "install.sh",
    "pyinstaller",
    "smartscreen",
    "gatekeeper",
    "release-manifest",
    "channel.json",
    "native channel behavior",
    "socratic update",
    "socratic uninstall",
    "marcos-guilherme",
    "self-replacement",
    "sha256sums",
)
ACTIVE_INSTALLATION_SURFACES = (
    (
        "README.md",
        "## Quick start",
        "## How a session moves",
    ),
    (
        "docs/universal-installation.md",
        "## Install today from GitHub",
        "## Source and development installation",
    ),
)
UNSAFE_INSTALLER_PATTERNS = (
    re.compile(
        r"https?://raw[.]githubusercontent[.]com/"
        r"[^\s<>()`]+/main/",
        re.IGNORECASE,
    ),
    re.compile(
        r"https?://github[.]com/[^\s<>()`]+/(?:raw|blob)/main/"
        r"[^\s<>()`]*(?:install[.]ps1|install[.]sh)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"https?://github[.]com/[^\s<>()`]+/releases/latest/download/"
        r"[^\s<>()`]*(?:install[.]ps1|install[.]sh)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:irm|invoke-restmethod|iwr|invoke-webrequest)\b"
        r"[^\r\n|]*(?:\r?\n[ \t]*)?[|][ \t]*"
        r"(?:iex|invoke-expression)\b",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"\b(?:curl|wget)\b[^\r\n|]*"
        r"(?:\r?\n[ \t]*)?[|][ \t]*(?:sh|bash)\b",
        re.IGNORECASE | re.MULTILINE,
    ),
)
LAUNCHER_ADAPTER_REQUIREMENTS = (
    "executable detection",
    "exact command construction",
    "protocol delivery",
    "inherits authentication",
    "inherits terminal",
    "fake executable",
    "`pi`",
    "`claude`",
    "`agy`",
    "pi --append-system-prompt protocol -- launch_payload",
    "claude --append-system-prompt-file protocol -- launch_payload",
    "socratic host list",
    "socratic host set",
    "socratic host clear",
    "persistent default",
    "--agent",
    "one-session",
    "provider",
    "model",
)
ADAPTER_ROOTS = ("adapters",)
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
CANONICAL_RE = re.compile(
    r"<!-- canonical:start -->(.*?)<!-- canonical:end -->",
    re.DOTALL,
)
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FENCE_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")
UNIVERSAL_VALIDATION_INPUTS = (
    "socratic_learn/build.py",
    "dist/socratic-learn.prompt.md",
    "dist/manifest.json",
    "core/pedagogy.md",
    "core/session-protocol.md",
    "core/assessment.md",
    "core/evidence-policy.md",
    "curricula/mathematics/map.md",
    "curricula/ai/map.md",
)
POSIX_THREAT_MODEL_PATHS = (
    "docs/universal-installation.md",
)
POSIX_THREAT_MODEL_TERMS = (
    "ordinary collisions",
    "accidental concurrent changes",
    "cooperating socratic learn processes",
    "same os principal",
    "leaf-entry toctou",
)
IMPOSSIBLE_SAFETY_CLAIM = "no external data was deleted"
CANONICAL_DIST_FILES = {"manifest.json", "socratic-learn.prompt.md"}
NON_PRODUCT_TRACKED_ROOTS = (
    ".cursor/",
    ".superpowers/",
    "docs/superpowers/",
    "packages/pixel-scenes/",
)
RUNTIME_WORKSPACE_MARKERS = (".socratic-learn",)
TRACKED_EVALUATION_ASSET_ROOTS = (
    "evaluation/fixtures/antigravity/",
    "evaluation/reports/",
)


def markdown_files(root: Path) -> list[Path]:
    tracked, git_error = tracked_files(root)
    if git_error is None and tracked:
        candidates = (
            root / relative
            for relative in sorted(tracked)
            if relative.endswith(".md")
        )
    else:
        candidates = root.rglob("*.md")
    return [
        path
        for path in candidates
        if path.is_file()
        and ".git" not in path.relative_to(root).parts
        and "workspace" not in path.relative_to(root).parts
        and "vendor" not in path.relative_to(root).parts
    ]


def without_fenced_code(text: str) -> str:
    visible: list[str] = []
    fence_character = ""
    fence_length = 0
    for line in text.splitlines(keepends=True):
        match = FENCE_RE.match(line)
        if not fence_character:
            if match:
                fence = match.group(1)
                fence_character = fence[0]
                fence_length = len(fence)
            else:
                visible.append(line)
            continue
        if re.fullmatch(
            rf"[ ]{{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*(?:\r?\n)?",
            line,
        ):
            fence_character = ""
            fence_length = 0
    return "".join(visible)


def markdown_link_target(raw_target: str) -> str:
    raw_target = raw_target.strip()
    if raw_target.startswith("<"):
        end = raw_target.find(">")
        return raw_target[1:end] if end != -1 else raw_target
    return raw_target.split(maxsplit=1)[0] if raw_target else ""


def parse_frontmatter(text: str) -> dict[str, str] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def normalize(text: str) -> str:
    return " ".join(text.split())


def markdown_section(text: str, heading: str, *, until: str | None = None) -> str:
    marker = f"## {heading}"
    if marker not in text:
        return ""
    body = text.split(marker, 1)[1]
    if until is not None:
        end = body.find(f"## {until}")
        if end >= 0:
            body = body[:end]
    return body


def readme_portable_session_section(readme_text: str) -> str:
    return readme_text


def check_document_patterns(
    text: str,
    patterns: tuple[tuple[str, re.Pattern[str]], ...],
) -> list[str]:
    subject = normalize(text)
    return [
        name
        for name, pattern in patterns
        if pattern.search(subject) is None
    ]


def mutate_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(
            f"expected exactly one mutation target, found {count}"
        )
    return text.replace(old, new, 1)


def portable_documentation_errors(root: Path) -> list[str]:
    errors: list[str] = []

    session_protocol = root / "core/session-protocol.md"
    if session_protocol.is_file():
        protocol_text = session_protocol.read_text(encoding="utf-8")
        sections = (
            (
                markdown_section(
                    protocol_text,
                    "Resume and time bounds",
                    until="Natural learner-facing conversation",
                ),
                SESSION_PROTOCOL_RESUME_PATTERNS,
            ),
            (
                markdown_section(
                    protocol_text,
                    "Natural learner-facing conversation",
                    until="Source study",
                ),
                SESSION_PROTOCOL_CONVERSATION_PATTERNS,
            ),
            (
                protocol_text.split("## 5. Close and persist", 1)[-1].split(
                    "## Capability fallbacks",
                    1,
                )[0]
                + markdown_section(protocol_text, "Session record contract"),
                SESSION_PROTOCOL_CLOSE_PATTERNS,
            ),
        )
        if any(check_document_patterns(text, patterns) for text, patterns in sections):
            errors.append(
                "core/session-protocol.md is missing launcher checkpoint contracts"
            )

    readme = root / "README.md"
    if readme.is_file():
        section = readme_portable_session_section(
            readme.read_text(encoding="utf-8")
        )
        if check_document_patterns(section, README_PORTABLE_SESSION_PATTERNS):
            errors.append("README.md is missing portable session memory guidance")

    manifest_reference = root / "docs/manifest-reference.md"
    if manifest_reference.is_file():
        manifest_text = manifest_reference.read_text(encoding="utf-8")
        top_level = manifest_text.split("## Top-level fields", 1)[-1].split(
            "## Source records",
            1,
        )[0]
        conformance_section = markdown_section(
            manifest_text,
            "Conformance and session fields",
            until="Version policy",
        )
        version_policy = markdown_section(manifest_text, "Version policy")
        missing_manifest = (
            check_document_patterns(top_level, MANIFEST_REFERENCE_TOP_LEVEL_PATTERNS)
            + check_document_patterns(
                conformance_section,
                MANIFEST_REFERENCE_CONFORMANCE_PATTERNS,
            )
            + check_document_patterns(
                version_policy,
                MANIFEST_REFERENCE_VERSION_POLICY_PATTERNS,
            )
        )
        if missing_manifest:
            errors.append(
                "docs/manifest-reference.md is missing current version policy"
            )

    launcher_adapters = root / "docs/launcher-adapters.md"
    if launcher_adapters.is_file():
        launcher_text = launcher_adapters.read_text(encoding="utf-8")
        if check_document_patterns(
            launcher_text,
            LAUNCHER_SESSION_CONTROL_PATTERNS,
        ):
            errors.append(
                "docs/launcher-adapters.md is missing session control contracts"
            )

    return errors


def _opening_visible_markdown(text: str) -> str:
    text = re.sub(r"<!--.*?(?:-->|$)", "", text, flags=re.DOTALL)
    visible: list[str] = []
    fence_character = ""
    fence_length = 0
    for line in text.splitlines(keepends=True):
        match = FENCE_RE.match(line)
        if not fence_character:
            if match:
                fence = match.group(1)
                fence_character = fence[0]
                fence_length = len(fence)
                visible.append("\n<README_FENCED_BLOCK>\n\n")
            else:
                visible.append(line)
            continue
        if re.fullmatch(
            rf"[ ]{{0,3}}{re.escape(fence_character)}{{{fence_length},}}"
            r"[ \t]*(?:\r?\n)?",
            line,
        ):
            fence_character = ""
            fence_length = 0
    return "".join(visible)


_MARKDOWN_HTML_BLOCK_TAGS = (
    "address",
    "article",
    "aside",
    "base",
    "basefont",
    "blockquote",
    "body",
    "caption",
    "center",
    "col",
    "colgroup",
    "dd",
    "details",
    "dialog",
    "dir",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "frame",
    "frameset",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "head",
    "header",
    "hr",
    "html",
    "iframe",
    "legend",
    "li",
    "link",
    "main",
    "menu",
    "menuitem",
    "nav",
    "noframes",
    "ol",
    "optgroup",
    "option",
    "p",
    "param",
    "pre",
    "script",
    "search",
    "section",
    "style",
    "summary",
    "table",
    "tbody",
    "td",
    "textarea",
    "tfoot",
    "th",
    "thead",
    "title",
    "tr",
    "track",
    "ul",
)
_MARKDOWN_HTML_BLOCK_RE = re.compile(
    r"^[ ]{0,3}</?(?:"
    + "|".join(_MARKDOWN_HTML_BLOCK_TAGS)
    + r")(?=[ \t/>]|$)",
    re.IGNORECASE,
)
_HTML_TAG_NAME = r"[A-Za-z][A-Za-z0-9-]*"
_HTML_ATTRIBUTE = (
    r"[A-Za-z_:][A-Za-z0-9_.:-]*"
    r"(?:[ \t]*=[ \t]*(?:\"[^\"]*\"|'[^']*'|[^\s\"'=<>`]+))?"
)
_MARKDOWN_COMPLETE_HTML_TAG_RE = re.compile(
    r"^[ ]{0,3}(?:"
    rf"</{_HTML_TAG_NAME}[ \t]*>"
    r"|"
    rf"<{_HTML_TAG_NAME}(?:[ \t]+{_HTML_ATTRIBUTE})*[ \t]*/?>"
    r")[ \t]*$"
)


def _starts_markdown_html_block(line: str) -> bool:
    return bool(
        _MARKDOWN_HTML_BLOCK_RE.match(line)
        or _MARKDOWN_COMPLETE_HTML_TAG_RE.match(line)
        or re.match(r"^[ ]{0,3}(?:<\?|<![A-Z]|<!\[CDATA\[)", line)
    )


def _is_ordinary_markdown_paragraph(block: str) -> bool:
    lines = block.splitlines()
    if not lines:
        return False
    first = lines[0]
    if re.match(r"^(?:[ ]{4}|\t)", first):
        return False
    if re.match(
        r"^[ ]{0,3}(?:#{1,6}(?:[ \t]+|$)|>[ \t]?|[-+*][ \t]+|"
        r"\d+[.)][ \t]+)",
        first,
    ):
        return False
    if _starts_markdown_html_block(first):
        return False
    if re.fullmatch(
        r"[ \t]{0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|"
        r"(?:_[ \t]*){3,})",
        first,
    ):
        return False
    if len(lines) > 1 and re.fullmatch(
        r"[ \t]{0,3}(?:=+|-+)[ \t]*",
        lines[1],
    ):
        return False
    if len(lines) > 1 and re.fullmatch(
        r"[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*"
        r"(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*",
        lines[1],
    ):
        return False
    if any(re.match(r"^[ \t]*:[ \t]+", line) for line in lines[1:]):
        return False
    return True


def readme_opening_prose_paragraph(text: str) -> str:
    visible = _opening_visible_markdown(text)
    tagline = f"**{README_TAGLINE}**"
    lines = visible.splitlines()
    try:
        tagline_index = lines.index(tagline)
    except ValueError:
        return ""
    remainder = "\n".join(lines[tagline_index + 1 :])
    first_block = next(
        (
            block.strip("\r\n")
            for block in re.split(r"\n[ \t]*\n", remainder)
            if block.strip()
        ),
        "",
    )
    return first_block if _is_ordinary_markdown_paragraph(first_block) else ""


def contains_unsupported_effectiveness_claim(text: str) -> bool:
    negated_spans = [
        match.span()
        for pattern in README_NEGATED_EFFECTIVENESS_PATTERNS
        for match in pattern.finditer(text)
    ]
    return any(
        not any(
            negated_start <= match.start() and match.end() <= negated_end
            for negated_start, negated_end in negated_spans
        )
        for pattern in README_UNSUPPORTED_EFFECTIVENESS_PATTERNS
        for match in pattern.finditer(text)
    )


def tracked_files(root: Path) -> tuple[set[str], str | None]:
    if not (root / ".git").exists():
        return set(), None
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return set(), "git ls-files unavailable: git executable not found"
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip()
        message = "git ls-files failed"
        if detail:
            message += f": {detail}"
        return set(), message
    return (
        {line.replace("\\", "/") for line in result.stdout.splitlines()},
        None,
    )


class GuardedAssetPathError(ValueError):
    pass


def guarded_tracked_asset_path(root: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or any(
        part in ("", ".", "..") for part in relative_path.parts
    ):
        raise GuardedAssetPathError(
            f"Unsafe tracked evaluation asset path: {relative}"
        )

    asset = relative_path.as_posix()
    current = root
    components = [(".", current)]
    for part in relative_path.parts:
        current = current / part
        component = current.relative_to(root).as_posix()
        components.append((component, current))

    for index, (component, candidate) in enumerate(components):
        try:
            status = candidate.lstat()
        except OSError as error:
            raise GuardedAssetPathError(
                f"Unsafe tracked evaluation asset path component: {component} "
                f"(tracked asset: {asset})"
            ) from error
        if stat.S_ISLNK(status.st_mode):
            message = (
                f"Unsafe tracked evaluation asset symlink: {component}"
            )
            if component != asset:
                message += f" (tracked asset: {asset})"
            raise GuardedAssetPathError(message)
        is_leaf = index == len(components) - 1
        expected_type = (
            stat.S_ISREG(status.st_mode)
            if is_leaf
            else stat.S_ISDIR(status.st_mode)
        )
        if not expected_type:
            raise GuardedAssetPathError(
                f"Unsafe tracked evaluation asset path component: {component} "
                f"(tracked asset: {asset})"
            )
    return current


def semantic_runtime_errors(root: Path) -> list[str]:
    raw_root = root / ".semantic-runs"
    try:
        raw_status = raw_root.lstat()
    except FileNotFoundError:
        return []
    if stat.S_ISLNK(raw_status.st_mode):
        return ["Unsafe symlink under .semantic-runs: ."]
    if not stat.S_ISDIR(raw_status.st_mode):
        return []

    errors: list[str] = []
    pending = [(raw_root, ".")]
    while pending:
        directory, relative = pending.pop()
        subdirectories: set[str] = set()
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                child_relative = (
                    entry.name
                    if relative == "."
                    else f"{relative}/{entry.name}"
                )
                if entry.is_symlink():
                    errors.append(
                        "Unsafe symlink under .semantic-runs: "
                        + child_relative
                    )
                    continue
                if entry.is_dir(follow_symlinks=False):
                    subdirectories.add(entry.name)
                    pending.append(
                        (Path(entry.path), child_relative)
                    )
        names = subdirectories
        if names & set(RUNTIME_WORKSPACE_MARKERS) or (
            Path(directory).name == "home" and "learning" in names
        ):
            errors.append(f"Runtime home/cwd under .semantic-runs: {relative}")
    return sorted(errors)


def validate_distribution(root: Path) -> list[str]:
    if not all((root / relative).is_file() for relative in UNIVERSAL_VALIDATION_INPUTS):
        return []

    errors: list[str] = []
    bundle_path = root / "dist/socratic-learn.prompt.md"
    manifest_path = root / "dist/manifest.json"
    try:
        bundle = bundle_path.read_bytes()
        manifest_bytes = manifest_path.read_bytes()
    except OSError as error:
        return [f"Generated distribution could not be read: {error}"]

    if not bundle.startswith(b"# Socratic Learn\n"):
        errors.append(
            "Generated bundle does not have the expected header: "
            "# Socratic Learn"
        )

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        validate_manifest(manifest)
    except (UnicodeDecodeError, json.JSONDecodeError, LearningTutorError) as error:
        errors.append(f"Generated manifest is invalid: {error}")

    try:
        expected = build_distribution(root)
    except LearningTutorError as error:
        errors.append(f"In-memory distribution build failed: {error}")
        return errors

    if bundle != expected.bundle or manifest_bytes != expected.manifest:
        errors.append(
            "Found stale generated artifacts in dist/; run: "
            "python -m socratic_learn build"
        )
    return errors


def workflow_job(text: str, name: str) -> str:
    match = re.search(
        rf"^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [a-z0-9-]+:\n|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(0) if match else ""


def workflow_steps(job: str) -> list[str]:
    return [
        match.group(0)
        for match in re.finditer(
            r"^      - .*?(?=^      - |^  [a-z0-9-]+:\n|\Z)",
            job,
            re.MULTILINE | re.DOTALL,
        )
    ]


def workflow_step(job: str, name: str) -> str:
    prefix = f"      - name: {name}\n"
    return next(
        (step for step in workflow_steps(job) if step.startswith(prefix)),
        "",
    )


def workflow_job_needs(job: str) -> list[str]:
    if not job:
        return []
    needs: list[str] = []
    collecting = False
    for line in job.splitlines()[1:]:
        if re.match(r"^  [a-z0-9-]+:\s*$", line):
            break
        scalar = re.match(r"^\s+needs:\s+(\S+)\s*$", line)
        if scalar:
            return [scalar.group(1).strip()]
        if re.match(r"^\s+needs:\s*$", line):
            collecting = True
            continue
        if collecting:
            item = re.match(r"^\s+-\s+(\S+)\s*$", line)
            if item:
                needs.append(item.group(1).strip())
                continue
            if line.strip():
                collecting = False
    return needs


def workflow_setup_node_steps(*jobs: str) -> list[str]:
    steps: list[str] = []
    for job in jobs:
        if not job:
            continue
        for step in workflow_steps(job):
            first_line = step.splitlines()[0]
            if first_line.endswith("actions/setup-node@v4") or first_line.endswith(
                "actions/setup-node@v6"
            ):
                steps.append(step)
    return steps


def workflow_action_step(job: str, action: str) -> str:
    line = f"      - uses: {action}"
    return next(
        (step for step in workflow_steps(job) if step.splitlines()[0] == line),
        "",
    )


def workflow_action_input(step: str, name: str) -> str | None:
    lines = step.splitlines()
    try:
        start = lines.index("        with:") + 1
    except ValueError:
        return None
    prefix = f"          {name}: "
    for line in lines[start:]:
        if line.startswith(prefix):
            value = line[len(prefix) :].split(" #", 1)[0]
            return value.strip().strip('"\'')
        if line and not line.startswith("          "):
            break
    return None


def workflow_run_block(step: str) -> str:
    lines = step.splitlines()
    start = next(
        (
            index + 1
            for index, line in enumerate(lines)
            if re.fullmatch(r"        run: \|[+-]?", line)
        ),
        None,
    )
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start:]:
        if not line:
            body.append("")
        elif line.startswith("          "):
            body.append(line[10:])
        else:
            break
    return "\n".join(body)


def workflow_python_step_uses_literal_run_block(step: str) -> bool:
    return any(
        re.fullmatch(r"        run: \|[+-]?", line)
        for line in step.splitlines()
    )


def workflow_step_uses_python_shell(step: str) -> bool:
    return any(
        re.fullmatch(
            r"""        shell: +(?:python(?: +\{0\})?"""
            r"""|"python(?: \{0\})?"|'python(?: \{0\})?')"""
            r"(?: +(?:#.*)?)?",
            line,
        )
        for line in step.splitlines()
    )


def workflow_uses_run_shell_defaults(text: str) -> bool:
    mappings: list[tuple[int, str]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indentation = len(line) - len(line.lstrip(" "))
        while mappings and mappings[-1][0] >= indentation:
            mappings.pop()
        match = re.fullmatch(
            r" *(?P<key>[A-Za-z][A-Za-z0-9_-]*):(?P<value>.*)",
            line,
        )
        if match is None:
            continue
        key = match.group("key")
        if (
            key == "shell"
            and len(mappings) >= 2
            and [mapping[1] for mapping in mappings[-2:]]
            == ["defaults", "run"]
        ):
            return True
        value = match.group("value").strip()
        if not value or value.startswith("#"):
            mappings.append((indentation, key))
    return False


def workflow_python_tree(step: str) -> ast.Module | None:
    if not workflow_step_uses_python_shell(step):
        return None
    try:
        return ast.parse(workflow_run_block(step))
    except SyntaxError:
        return None


def workflow_python_local_imports(tree: ast.Module) -> list[ast.stmt]:
    imports: list[ast.stmt] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name == "socratic_learn"
            or alias.name.startswith("socratic_learn.")
            for alias in node.names
        ):
            imports.append(node)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (
                node.module == "socratic_learn"
                or node.module.startswith("socratic_learn.")
            )
        ):
            imports.append(node)
    return imports


def workflow_python_step_imports_local_package(step: str) -> bool:
    tree = workflow_python_tree(step)
    return tree is not None and bool(workflow_python_local_imports(tree))


def workflow_python_step_has_local_import_setup(step: str) -> bool:
    tree = workflow_python_tree(step)
    if tree is None:
        return False
    local_imports = workflow_python_local_imports(tree)
    if not local_imports:
        return True
    imported_at: dict[str, int] = {}
    required_setup = ast.parse("sys.path.insert(0, os.getcwd())").body[0]
    setup_at: list[int] = []
    for index, statement in enumerate(tree.body):
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.asname is None:
                    imported_at.setdefault(alias.name, index)
        if ast.dump(statement) == ast.dump(required_setup):
            setup_at.append(index)

    return (
        "os" in imported_at
        and "sys" in imported_at
        and len(setup_at) == 1
        and imported_at["os"] < setup_at[0]
        and imported_at["sys"] < setup_at[0]
        and tree.body[setup_at[0]].lineno
        < min(statement.lineno for statement in local_imports)
    )


def version_step_imports_local_package(step: str) -> bool:
    tree = workflow_python_tree(step)
    if tree is None or not workflow_python_step_has_local_import_setup(step):
        return False
    version_import_at: int | None = None
    for index, statement in enumerate(tree.body):
        if (
            isinstance(statement, ast.ImportFrom)
            and statement.module == "socratic_learn"
            and any(
                alias.name == "__version__" and alias.asname is None
                for alias in statement.names
            )
            and version_import_at is None
        ):
            version_import_at = index

    return version_import_at is not None


def validate_release_automation(root: Path) -> list[str]:
    errors: list[str] = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        pyproject_text = pyproject.read_text(encoding="utf-8")
        pyproject_lower = pyproject_text.lower()
        for term in RETIRED_NATIVE_RELEASE_TERMS:
            if term in pyproject_lower:
                errors.append(
                    "pyproject.toml must not reference retired native "
                    f"distribution term: {term}"
                )
        if 'socratic = "socratic_learn.cli:entry_point"' not in pyproject_text:
            errors.append("pyproject.toml must map the socratic console entry point")
        try:
            project = tomllib.loads(pyproject_text)
        except tomllib.TOMLDecodeError as error:
            errors.append(f"pyproject.toml is invalid: {error}")
        else:
            project_metadata = project.get("project", {})
            dependencies = project.get("project", {}).get("dependencies", [])
            if dependencies != RUNTIME_DEPENDENCIES:
                errors.append(
                    "pyproject.toml must declare exactly "
                    f"{', '.join(RUNTIME_DEPENDENCIES)} as runtime dependencies"
                )
            if (
                project_metadata.get("name") != "socratic-learn"
                or project_metadata.get("requires-python") != ">=3.11"
                or project_metadata.get("scripts")
                != {"socratic": "socratic_learn.cli:entry_point"}
            ):
                errors.append("pyproject.toml must preserve canonical package identity")
            if project_metadata.get("urls") != {
                "Homepage": "https://github.com/socraticlearn/socratic-learn",
                "Repository": "https://github.com/socraticlearn/socratic-learn",
                "Issues": "https://github.com/socraticlearn/socratic-learn/issues",
            }:
                errors.append("pyproject.toml must use branded project URLs")
            if project_metadata.get("optional-dependencies", {}).get("build") != [
                "build>=1.2,<2",
                "twine>=7,<8",
            ]:
                errors.append(
                    "pyproject.toml build extra must include bounded build and Twine"
                )

    release_path = root / ".github/workflows/release.yml"
    if release_path.is_file():
        release = release_path.read_text(encoding="utf-8")
        release_lower = release.lower()
        for term in RETIRED_NATIVE_RELEASE_TERMS:
            if term in release_lower:
                errors.append(
                    ".github/workflows/release.yml must not reference retired "
                    f"native distribution term: {term}"
                )
        if "permissions:\n  contents: read" not in release:
            errors.append("Release workflow must default to contents: read")
        if release.count("contents: write") != 1:
            errors.append(
                "Release workflow must grant contents: write only to GitHub publishing"
            )
        required_release_terms = (
            'tags: ["v*"]',
            "workflow_dispatch:",
            "ubuntu-latest",
            "actions/download-artifact@v4",
            "python -m unittest discover",
            "python tests/validate_repository.py",
            "run_wheel_read_smoke",
            "gh release create",
            "pypa/gh-action-pypi-publish@release/v1",
        )
        if not all(term in release for term in required_release_terms):
            errors.append("Release workflow is missing required triggers or artifacts")
        if workflow_uses_run_shell_defaults(release):
            errors.append(
                "Release workflow must not use defaults.run.shell; "
                "Python steps must declare shell explicitly"
            )

        python_steps = [
            step
            for step in workflow_steps(release)
            if workflow_step_uses_python_shell(step)
        ]
        if any(
            not workflow_python_step_uses_literal_run_block(step)
            for step in python_steps
        ):
            errors.append(
                "Release shell: python steps must use a literal run block"
            )
        local_import_steps = [
            step
            for step in python_steps
            if workflow_python_step_imports_local_package(step)
        ]
        if any(
            not workflow_python_step_has_local_import_setup(step)
            for step in local_import_steps
        ):
            errors.append(
                "Every release shell: python step importing socratic_learn "
                "must add the checkout to sys.path before import"
            )

        publish_gate = (
            "if: github.event_name == 'push' && "
            "startsWith(github.ref, 'refs/tags/v')"
        )
        github_publish = workflow_job(release, "publish-github")
        if (
            publish_gate not in github_publish
            or "contents: write" not in github_publish
        ):
            errors.append(
                "Release publishing must require a push event and version tag"
            )

        pypi_publish = workflow_job(release, "publish-pypi")
        if (
            publish_gate not in pypi_publish
            or "id-token: write" not in pypi_publish
            or "environment: pypi" not in pypi_publish
            or "pypa/gh-action-pypi-publish@release/v1" not in pypi_publish
        ):
            errors.append("PyPI publishing must use tag-only trusted publishing")
        npm_package = workflow_job(release, "npm-package")
        publish_npm = workflow_job(release, "publish-npm")
        if not npm_package:
            errors.append("Release workflow must include an npm-package job")
        else:
            npm_needs = workflow_job_needs(npm_package)
            if "python-package" not in npm_needs and "needs: python-package" not in npm_package:
                errors.append(
                    "npm-package job must depend on the verified python-package job"
                )
            if not any(
                marker in npm_package
                for marker in ('node-version: "24"', "node-version: 24")
            ):
                errors.append(
                    "npm-package job is missing required Node build or smoke steps"
                )
            if "package-manager-cache: false" not in npm_package:
                errors.append(
                    "npm-package job is missing required Node build or smoke steps"
                )
            if "actions/setup-node@v6" not in npm_package:
                errors.append(
                    "npm-package job is missing required Node build or smoke steps"
                )
            for term in (
                "npm@>=11.5.1",
                "tools/build_npm_package.py",
                "packages/npm-cli/package.json",
                "npm test --prefix packages/npm-cli",
                "assert_exact_version_output",
                '"--package"',
                "@socraticlearn/cli",
                "socraticlearn-cli-",
            ):
                if term not in npm_package:
                    errors.append(
                        "npm-package job is missing required Node build or smoke steps"
                    )
                    break
            if "python -m build" in npm_package:
                errors.append(
                    "npm-package job must not rebuild Python distributions"
                )
            npm_upload = workflow_action_step(
                npm_package,
                "actions/upload-artifact@v4",
            )
            if (
                workflow_action_input(npm_upload, "name") != "npm-package"
                or workflow_action_input(npm_upload, "if-no-files-found") != "error"
            ):
                errors.append(
                    "npm-package artifact upload must be strict and named npm-package"
                )
            python_download = workflow_action_step(
                npm_package,
                "actions/download-artifact@v4",
            )
            if workflow_action_input(python_download, "name") != "python-package":
                errors.append(
                    "npm-package job must download the python-package artifact"
                )
            npm_setup_steps = workflow_setup_node_steps(npm_package)
            if not npm_setup_steps:
                errors.append(
                    "npm-package job must configure Node through setup-node@v6"
                )
            for step in npm_setup_steps:
                if "registry-url:" in step or "registry_url:" in step.lower():
                    errors.append(
                        "npm setup-node must not set registry-url or auth injection"
                    )
                if "cache: false" in step and "package-manager-cache" not in step:
                    errors.append(
                        "npm setup-node must use package-manager-cache: false"
                    )
            npm_pkg_path = root / "packages/npm-cli/package.json"
            if npm_pkg_path.is_file():
                try:
                    npm_identity = json.loads(
                        npm_pkg_path.read_text(encoding="utf-8")
                    ).get("name")
                except (json.JSONDecodeError, AttributeError):
                    npm_identity = None
                if npm_identity != "@socraticlearn/cli":
                    errors.append(
                        "npm package metadata must identify @socraticlearn/cli"
                    )
                elif (
                    f'"{npm_identity}"' not in npm_package
                    and npm_identity not in npm_package
                ):
                    errors.append(
                        "npm-package job must verify canonical package identity"
                    )

        verify_artifact = workflow_job(release, "verify-artifact")
        if not verify_artifact:
            errors.append(
                "Release workflow must verify built artifacts before publishing"
            )
        else:
            artifact_needs = workflow_job_needs(verify_artifact)
            if artifact_needs != ["npm-package"]:
                errors.append(
                    "Artifact verification must wait for the npm-package job"
                )
            matrix_section = verify_artifact.split("strategy:", 1)
            if len(matrix_section) < 2:
                errors.append(
                    "Artifact verification must use a multi-platform matrix"
                )
            else:
                matrix = matrix_section[1].split("steps:", 1)[0]
                if "fail-fast: false" not in matrix:
                    errors.append(
                        "Artifact verification matrix must not fail fast "
                        "across runners"
                    )
                for runner in (
                    "windows-latest",
                    "ubuntu-latest",
                    "macos-latest",
                ):
                    if runner not in matrix:
                        errors.append(
                            "Artifact verification must run on clean "
                            f"{runner} runners"
                        )
                        break
            for marker in (
                'python-version: "3.11"',
                "pipx",
                'node-version: "24"',
                "package-manager-cache: false",
                "run_local_artifact_smoke",
                "name: python-package",
                "name: npm-package",
            ):
                if marker not in verify_artifact:
                    errors.append(
                        "Artifact verification is missing required tooling "
                        "or helper"
                    )
                    break
            if "from socratic_learn import" in verify_artifact:
                errors.append(
                    "Artifact verification must not import the checkout package"
                )
            if "sys.path.insert" in verify_artifact:
                errors.append(
                    "Artifact verification must not add the checkout to sys.path"
                )
            artifact_order = release.find("  verify-artifact:")
            npm_order = release.find("  npm-package:")
            github_order = release.find("  publish-github:")
            if (
                artifact_order == -1
                or npm_order == -1
                or github_order == -1
                or not (npm_order < artifact_order < github_order)
            ):
                errors.append(
                    "Artifact verification must run after npm-package and "
                    "before publish-github"
                )

        github_needs = workflow_job_needs(github_publish)
        if github_needs != ["verify-artifact"]:
            errors.append(
                "publish-github must wait for pre-publish artifact verification"
            )
        github_downloads = [
            workflow_action_input(step, "name")
            for step in workflow_steps(github_publish)
            if step.splitlines()[0].endswith("actions/download-artifact@v4")
        ]
        if set(github_downloads) != {"python-package", "npm-package"}:
            errors.append(
                "publish-github must download python-package and npm-package "
                "artifacts without rebuilding"
            )
        if any(
            marker in github_publish
            for marker in ("python -m build", "tools/build_npm_package.py")
        ):
            errors.append(
                "publish-github must attach verified artifacts without rebuilding"
            )

        if publish_npm:
            if publish_gate not in publish_npm.split("steps:", 1)[0]:
                errors.append(
                    "npm publishing must require a push event and version tag"
                )
            if "environment: npm" not in publish_npm:
                errors.append("npm publishing must use protected environment npm")
            if "id-token: write" not in publish_npm:
                errors.append("npm publishing must grant id-token: write")
            npm_needs = workflow_job_needs(publish_npm)
            if npm_needs and npm_needs != ["publish-github"]:
                errors.append(
                    "Registry publication jobs must remain independent after publish-github"
                )
            elif "publish-github" not in npm_needs and "needs: publish-github" not in publish_npm:
                errors.append(
                    "npm publishing must wait for publish-github"
                )
            if "publish-pypi" in npm_needs:
                errors.append(
                    "Registry publication jobs must remain independent after publish-github"
                )
            publish_setup_steps = workflow_setup_node_steps(publish_npm)
            if not publish_setup_steps or not all(
                "actions/setup-node@v6" in step for step in publish_setup_steps
            ):
                errors.append(
                    "npm publishing must use Node 24 with caching disabled"
                )
            for step in publish_setup_steps:
                if "registry-url:" in step or "registry_url:" in step.lower():
                    errors.append(
                        "npm setup-node must not set registry-url or auth injection"
                    )
                if "package-manager-cache: false" not in step:
                    errors.append(
                        "npm publishing must use Node 24 with caching disabled"
                    )
            registry_config = "npm config set registry https://registry.npmjs.org"
            if registry_config not in publish_npm:
                errors.append(
                    "npm publishing must set registry explicitly before publish"
                )
            elif publish_npm.index(registry_config) > publish_npm.index("npm publish"):
                errors.append(
                    "npm publishing must set registry explicitly before publish"
                )
            if "npm publish" not in publish_npm or "--access public" not in publish_npm:
                errors.append(
                    "npm publishing must publish @socraticlearn/cli publicly"
                )
            if "@socraticlearn/cli" not in publish_npm:
                errors.append(
                    "npm publishing must publish @socraticlearn/cli publicly"
                )
            if workflow_action_input(
                workflow_action_step(publish_npm, "actions/download-artifact@v4"),
                "name",
            ) != "npm-package":
                errors.append(
                    "npm publishing must consume the npm-package artifact only"
                )
            if "tools/build_npm_package.py" in publish_npm or "python -m build" in publish_npm:
                errors.append(
                    "npm publishing must consume the npm-package artifact only"
                )
            if not any(
                marker in publish_npm
                for marker in ('node-version: "24"', "node-version: 24")
            ):
                errors.append(
                    "npm publishing must use Node 24 with caching disabled"
                )
            if "npm@>=11.5.1" not in publish_npm:
                errors.append(
                    "npm publishing must meet npm trusted publishing version floor"
                )
        else:
            errors.append("Release workflow must include a publish-npm job")

        verify_registry = workflow_job(release, "verify-registry")
        if not verify_registry:
            errors.append(
                "Release workflow must verify published registry packages"
            )
        else:
            if publish_gate not in verify_registry.split("steps:", 1)[0]:
                errors.append(
                    "Registry verification must require a push event and version tag"
                )
            verify_needs = workflow_job_needs(verify_registry)
            if verify_needs != ["publish-pypi", "publish-npm"]:
                errors.append(
                    "Registry verification must wait for both registry publish jobs"
                )
            matrix_section = verify_registry.split("strategy:", 1)
            if len(matrix_section) < 2:
                errors.append(
                    "Registry verification must use a multi-platform matrix"
                )
            else:
                matrix = matrix_section[1].split("steps:", 1)[0]
                if "fail-fast: false" not in matrix:
                    errors.append(
                        "Registry verification matrix must not fail fast across runners"
                    )
                for runner in (
                    "windows-latest",
                    "ubuntu-latest",
                    "macos-latest",
                ):
                    if runner not in matrix:
                        errors.append(
                            "Registry verification must run on clean "
                            f"{runner} runners"
                        )
                        break
            for marker in (
                'python-version: "3.11"',
                "pipx",
                'node-version: "24"',
                "package-manager-cache: false",
                "tests.registry_smoke",
                "GITHUB_REF_NAME",
            ):
                if marker not in verify_registry:
                    errors.append(
                        "Registry verification is missing required tooling or helper"
                    )
                    break
            if "from socratic_learn import" in verify_registry:
                errors.append(
                    "Registry verification must not import the checkout package"
                )
            if "sys.path.insert" in verify_registry:
                errors.append(
                    "Registry verification must not add the checkout to sys.path"
                )
            if "run_registry_smoke" not in verify_registry:
                errors.append(
                    "Registry verification must invoke the registry smoke helper"
                )

        pypi_needs = workflow_job_needs(pypi_publish)
        if pypi_needs and pypi_needs != ["publish-github"]:
            errors.append(
                "Registry publication jobs must remain independent after publish-github"
            )
        elif "publish-github" not in pypi_needs and "needs: publish-github" not in pypi_publish:
            errors.append(
                "Registry publishing must wait for publish-github"
            )
        if "python-package" in pypi_needs:
            errors.append(
                "PyPI publishing must not bypass the GitHub release gate"
            )
        if "publish-npm" in pypi_needs:
            errors.append(
                "Registry publication jobs must remain independent after publish-github"
            )
        if publish_gate not in pypi_publish.split("steps:", 1)[0]:
            errors.append(
                "PyPI publishing must require a push event and version tag"
            )
        if any(
            marker in release_lower
            for marker in (
                "skip-existing",
                "pypi_api_token",
                "twine_password",
                "api-token:",
                "password:",
                "node_auth_token",
                "npm_token",
                "_authtoken",
                "always-auth",
            )
        ):
            errors.append(
                "Release publishing must not use skip-existing or registry tokens"
            )

        python_package = workflow_job(release, "python-package")
        build_step = workflow_step(
            python_package,
            "Build wheel and source distribution",
        )
        artifact_step = workflow_step(
            python_package,
            "Validate exact artifacts",
        )
        exact_artifact_markers = (
            "expected = {",
            "socratic_learn-{__version__}-py3-none-any.whl",
            "socratic_learn-{__version__}.tar.gz",
            "actual = {path for path in dist.iterdir() if path.is_file()}",
            "if actual != expected:",
        )
        if (
            "run: python -m build --sdist --wheel --outdir release-dist"
            not in build_step
            or not all(marker in artifact_step for marker in exact_artifact_markers)
        ):
            errors.append(
                "Release workflow must build exactly one versioned wheel and sdist"
            )

        twine_step = workflow_step(
            python_package,
            "Validate package metadata",
        )
        if "run: python -m twine check release-dist/*" not in twine_step:
            errors.append(
                "Release workflow must validate distributions with Twine"
            )

        smoke_step = workflow_step(
            python_package,
            "Smoke test installed socratic read",
        )
        exact_wheel_markers = (
            'wheel = root / "release-dist" / '
            'f"socratic_learn-{__version__}-py3-none-any.whl"',
            "run_wheel_read_smoke(root, wheel=wheel)",
        )
        if not all(marker in smoke_step for marker in exact_wheel_markers):
            errors.append(
                "Release workflow smoke must use the exact release-dist wheel"
            )

        upload = workflow_action_step(
            python_package,
            "actions/upload-artifact@v4",
        )
        if workflow_action_input(upload, "if-no-files-found") != "error":
            errors.append(
                "Release artifact upload must fail when files are missing"
            )
        pypi_download = workflow_action_step(
            pypi_publish,
            "actions/download-artifact@v4",
        )
        shared_artifact = (
            workflow_action_input(pypi_download, "name") == "python-package"
            and "python-package" in github_downloads
        )
        if (
            workflow_action_input(upload, "name") != "python-package"
            or workflow_action_input(upload, "path") != "release-dist/*"
            or not shared_artifact
        ):
            errors.append(
                "GitHub Release and PyPI must consume the same "
                "python-package artifact"
            )

        version_job = workflow_job(release, "verify-version")
        if (
            "github.ref_name" not in release
            or "__version__" not in version_job
            or "refs/tags/v" not in version_job
        ):
            errors.append("Release tags must exactly match the package version")
        version_step = workflow_step(version_job, "Require an exact version tag")
        if not version_step_imports_local_package(version_step):
            errors.append(
                "Release version gate must add the checkout to sys.path before import"
            )
        if not release.endswith("\n"):
            errors.append("Release workflow must end with a trailing newline")
        elif release.endswith("\n\n"):
            errors.append(
                "Release workflow must not include extra trailing newlines"
            )

        maintainer = root / "docs/maintainer-registry-trusted-publishing.md"
        if maintainer.is_file():
            maintainer_text = maintainer.read_text(encoding="utf-8")
            if (
                "workflow `release.yml`" not in maintainer_text
                and "**`release.yml`**" not in maintainer_text
            ):
                errors.append(
                    "Maintainer registry doc must name workflow release.yml"
                )
            if (
                "direct `npm publish`" not in maintainer_text
                and "direct **`npm publish`**" not in maintainer_text
            ):
                errors.append(
                    "Maintainer registry doc must require direct npm publish"
                )
            if not maintainer_text.endswith("\n") or maintainer_text.endswith("\n\n"):
                errors.append(
                    "Maintainer registry doc must end with a single trailing newline"
                )

    normal_path = root / ".github/workflows/native-python.yml"
    workflow_paths = (
        (release_path, "Release workflow")
        if release_path.is_file()
        else None,
        (normal_path, "Python safety matrix")
        if normal_path.is_file()
        else None,
    )
    for entry in workflow_paths:
        if entry is None:
            continue
        path, label = entry
        workflow = path.read_text(encoding="utf-8")
        if '"--no-deps"' in workflow or "'--no-deps'" in workflow:
            errors.append(
                f"{label} must install wheels with declared dependencies"
            )
        if not all(marker in workflow for marker in WHEEL_READ_SMOKE_MARKERS):
            errors.append(
                f"{label} must smoke-test installed socratic read with fake hosts"
            )
        if any(marker in workflow for marker in INTERPRETER_ONLY_PDF_SMOKE_MARKERS):
            if "run_wheel_read_smoke" not in workflow:
                errors.append(
                    f"{label} must not rely on interpreter-only PdfReader smoke"
                )
    if release_path.is_file():
        release = release_path.read_text(encoding="utf-8")
        python_package = workflow_job(release, "python-package")
        if python_package and not all(
            marker in python_package for marker in WHEEL_READ_SMOKE_MARKERS
        ):
            errors.append(
                "Release wheel job must smoke-test installed socratic read with fake hosts"
            )
    if normal_path.is_file():
        normal = normal_path.read_text(encoding="utf-8")
        install = 'python -m pip install -e ".[build]"'
        suite = "python -m unittest discover"
        if (
            install not in normal
            or suite not in normal
            or normal.index(install) > normal.index(suite)
        ):
            errors.append("Python safety matrix must install .[build] before tests")
        if "python -m build --wheel" not in normal or "run_wheel_read_smoke" not in normal:
            errors.append(
                "Python safety matrix must build and smoke-test an installed wheel"
            )
        for marker in (
            "actions/setup-node@v6",
            'node-version: "24"',
            "package-manager-cache: false",
            "npm test --prefix packages/npm-cli",
        ):
            if marker not in normal:
                errors.append(
                    "Python safety matrix must run npm launcher tests"
                )
                break
    return errors


def validate_local_release_gate(root: Path) -> list[str]:
    errors: list[str] = []
    dist = root / "dist"
    if dist.is_dir():
        actual = {path.name for path in dist.iterdir()}
        if actual != CANONICAL_DIST_FILES:
            errors.append(
                "Canonical dist/ must contain exactly manifest.json and "
                "socratic-learn.prompt.md; package builds use package-dist/"
            )
    return errors


def validate(root: Path) -> list[str]:
    errors: list[str] = []

    for relative in REQUIRED_PATHS:
        path = root / relative
        if path.is_file():
            continue
        if not path.exists():
            errors.append(f"Missing required path: {relative}")
        elif path.is_dir():
            errors.append(
                f"Required path is not a file: {relative} (found directory)"
            )
        else:
            errors.append(
                f"Required path is not a file: {relative} (found non-file)"
            )

    readme = root / "README.md"
    if readme.is_file():
        readme_text = readme.read_text(encoding="utf-8")
        readme_opening = readme_text.split("## What you get", 1)[0]
        first_prose_paragraph = readme_opening_prose_paragraph(readme_opening)
        if not all(
            normalize(phrase).lower() in normalize(readme_text).lower()
            for phrase in README_POSITIONING_REQUIREMENTS
        ) or (
            README_OPENING_PROMISE
            not in first_prose_paragraph.lower()
        ):
            errors.append("Narrow product positioning in README.md")
        if any(dash in readme_text for dash in README_UNFRIENDLY_DASHES):
            errors.append("README.md uses unfriendly dash punctuation")
        normalized_readme = normalize(readme_text).lower()
        if not all(
            normalize(requirement).lower() in normalized_readme
            for requirement in README_PORTFOLIO_REQUIREMENTS
        ):
            errors.append("README.md is missing portfolio narrative")
        if any(
            term in normalized_readme for term in README_FORBIDDEN_POSITIONING_TERMS
        ):
            errors.append("README.md uses retired universal agent positioning")
        if not all(
            requirement in normalized_readme
            for requirement in README_HOST_MANAGEMENT_REQUIREMENTS
        ):
            errors.append("README.md is missing host management guidance")
        evaluation_section = readme_text.split("## Evaluation status", 1)
        evaluation_text = (
            evaluation_section[1].split("## Honest limitations", 1)[0].lower()
            if len(evaluation_section) == 2
            else ""
        )
        if not all(
            requirement in evaluation_text
            for requirement in README_EVALUATION_REQUIREMENTS
        ):
            errors.append("README.md is missing evaluation status guidance")
        if contains_unsupported_effectiveness_claim(readme_text):
            errors.append("README.md contains an unsupported effectiveness claim")
        if README_STANDARD_LIBRARY_RUNTIME_CLAIM in normalize(readme_text).lower():
            errors.append(
                "README.md must not describe the runtime as standard-library-only"
            )
        if not all(
            requirement in normalized_readme
            for requirement in README_LAUNCHER_REQUIREMENTS
        ):
            errors.append("README.md is missing public launcher onboarding")
        quick_start = readme_text.split("## Quick start", 1)
        quick_start_text = (
            quick_start[1].split("## How a session moves", 1)[0]
            if len(quick_start) == 2
            else ""
        )
        order_positions = [
            quick_start_text.lower().find(marker)
            for marker in README_ONBOARDING_ORDER
        ]
        if (
            any(position < 0 for position in order_positions)
            or order_positions != sorted(order_positions)
        ):
            errors.append("README.md public onboarding is out of order")
        if MENU_SKIP_HERE_PATTERN.search(quick_start_text):
            errors.append(
                "README.md must not claim --here skips the session menu"
            )
        if not readme_text.endswith("\n"):
            errors.append("README.md must end with a trailing newline")

    source_study = root / "docs/source-study.md"
    if source_study.is_file():
        source_text = normalize(
            source_study.read_text(encoding="utf-8")
        ).lower()
        if not all(
            requirement in source_text
            for requirement in SOURCE_STUDY_REQUIREMENTS
        ):
            errors.append("docs/source-study.md is missing source-study guidance")
        if MANUAL_LAUNCHER_CONFIG_PATTERN.search(
            source_study.read_text(encoding="utf-8")
        ):
            errors.append(
                "docs/source-study.md must document host commands instead of "
                "manual config.json edits"
            )
        if not all(
            requirement in source_text
            for requirement in SOURCE_STUDY_SCOPE_REQUIREMENTS
        ):
            errors.append(
                "docs/source-study.md is missing single-source scope guidance"
            )

    for relative, section_start, section_end in ACTIVE_INSTALLATION_SURFACES:
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        _before_section, separator, after_start = text.partition(section_start)
        section_text = (
            after_start.partition(section_end)[0]
            if separator
            else ""
        )
        normalized_text = normalize(section_text).lower()
        caveats_are_contextual = (
            PYPI_RELEASE_CAVEAT in normalized_text
            and NPM_RELEASE_CAVEAT in normalized_text
            and "pipx install socratic-learn" in normalized_text
            and "npx @socraticlearn/cli" in normalized_text
            and normalized_text.index(PYPI_RELEASE_CAVEAT)
            < normalized_text.index("pipx install socratic-learn")
            and normalized_text.index(NPM_RELEASE_CAVEAT)
            < normalized_text.index("npx @socraticlearn/cli")
        )
        if not caveats_are_contextual:
            errors.append(
                f"{relative} must condition PyPI and npm instructions "
                "on publication"
            )
        visible = without_fenced_code(section_text).lower()
        retired_hits = [
            term
            for term in RETIRED_ACTIVE_INSTALLATION_TERMS
            if term in visible
        ]
        if retired_hits:
            errors.append(
                f"{relative} must not describe retired native installation "
                f"assets: {', '.join(retired_hits)}"
            )
        if BRANDED_REPOSITORY_URL not in text and relative == "README.md":
            errors.append(
                "README.md must link to the branded Socratic Learn repository"
            )
        if any(pattern.search(section_text) for pattern in UNSAFE_INSTALLER_PATTERNS):
            errors.append(
                f"{relative} contains unsafe or mutable installer instructions"
            )

    installation_guide = root / "docs/universal-installation.md"
    if installation_guide.is_file():
        installation_text = installation_guide.read_text(encoding="utf-8")
        if MENU_SKIP_HERE_PATTERN.search(installation_text):
            errors.append(
                "docs/universal-installation.md must not claim "
                "--here skips the session menu"
            )
        if not installation_text.endswith("\n"):
            errors.append(
                "docs/universal-installation.md must end with a trailing newline"
            )

    launcher_adapters = root / "docs/launcher-adapters.md"
    if launcher_adapters.is_file():
        launcher_text = normalize(
            launcher_adapters.read_text(encoding="utf-8")
        ).lower()
        if not all(
            requirement in launcher_text
            for requirement in LAUNCHER_ADAPTER_REQUIREMENTS
        ):
            errors.append(
                "docs/launcher-adapters.md is missing launcher adapter contracts"
            )
        if MANUAL_LAUNCHER_CONFIG_PATTERN.search(
            launcher_adapters.read_text(encoding="utf-8")
        ):
            errors.append(
                "docs/launcher-adapters.md must document host commands instead of "
                "manual config.json edits"
            )

    errors.extend(portable_documentation_errors(root))

    legacy_package = root / "learning_tutor"
    allowed_legacy_modules = {"__init__.py", "__main__.py"}
    if legacy_package.is_dir():
        for path in sorted(legacy_package.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            if (
                path.parent != legacy_package
                or path.name not in allowed_legacy_modules
            ):
                errors.append(
                    f"Legacy package contains implementation module: {relative}"
                )

    for relative in POSIX_THREAT_MODEL_PATHS:
        path = root / relative
        if not path.is_file():
            continue
        text = normalize(path.read_text(encoding="utf-8")).lower()
        missing = [
            term for term in POSIX_THREAT_MODEL_TERMS if term not in text
        ]
        if missing:
            errors.append(
                f"Missing POSIX same-principal threat model in {relative}: "
                + ", ".join(missing)
            )

    claim_paths = [
        root / "README.md",
        *(root / "docs").rglob("*.md"),
        *(root / "socratic_learn").rglob("*.py"),
    ]
    for path in claim_paths:
        if (
            path.is_file()
            and IMPOSSIBLE_SAFETY_CLAIM
            in path.read_text(encoding="utf-8").lower()
        ):
            relative = path.relative_to(root)
            errors.append(
                f"Impossible absolute filesystem safety claim in {relative}: "
                f"{IMPOSSIBLE_SAFETY_CLAIM}"
            )

    for path in markdown_files(root):
        text = path.read_text(encoding="utf-8")
        visible_text = without_fenced_code(text)
        for raw_target in LINK_RE.findall(visible_text):
            target = markdown_link_target(raw_target)
            target = target.split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (path.parent / target).resolve().exists():
                errors.append(
                    f"Broken Markdown link in {path.relative_to(root)}: {target}"
                )
        if any(
            line.strip() == "PRIVATE_LEARNER_DATA"
            for line in visible_text.splitlines()
        ):
            errors.append(
                f"Found private learner marker outside workspace: {path.relative_to(root)}"
            )

    for relative in SKILL_PATHS:
        path = root / relative
        if not path.is_file():
            continue
        frontmatter = parse_frontmatter(path.read_text(encoding="utf-8"))
        if (
            frontmatter is None
            or not SKILL_NAME_RE.fullmatch(frontmatter.get("name", ""))
            or frontmatter.get("name") != path.parent.name
            or frontmatter.get("name") != "socratic-learn"
            or not frontmatter.get("description")
        ):
            errors.append(f"Invalid skill frontmatter: {relative}")

    for relative in POSITIONING_SKILL_PATHS:
        path = root / relative
        if not path.is_file():
            continue
        frontmatter = parse_frontmatter(path.read_text(encoding="utf-8"))
        if (
            frontmatter is None
            or "any subject or skill"
            not in frontmatter.get("description", "").lower()
        ):
            errors.append(f"Narrow product positioning in {relative}")

    canonical_blocks: list[str] = []
    core = root / "core"
    if core.exists():
        for path in core.glob("*.md"):
            if not path.is_file():
                continue
            canonical_blocks.extend(
                normalize(block)
                for block in CANONICAL_RE.findall(
                    path.read_text(encoding="utf-8")
                )
                if len(normalize(block)) >= 40
            )

    for adapter_root in ADAPTER_ROOTS:
        base = root / adapter_root
        if not base.exists():
            continue
        for path in base.rglob("*.md"):
            if not path.is_file():
                continue
            adapter_text = normalize(path.read_text(encoding="utf-8"))
            for block in canonical_blocks:
                if block in adapter_text:
                    errors.append(
                        f"{path.relative_to(root)} duplicates canonical core text"
                    )
                    break

    gitignore = root / ".gitignore"
    if gitignore.is_file():
        rules = gitignore.read_text(encoding="utf-8").splitlines()
        if "workspace/*" not in rules or "!workspace/.gitkeep" not in rules:
            errors.append(".gitignore must ignore workspace except .gitkeep")
        if "/package-dist/" not in rules:
            errors.append(".gitignore must ignore /package-dist/")
        if "/.semantic-runs/" not in rules:
            errors.append(".gitignore must ignore /.semantic-runs/")
        for required in (
            "/.cursor/",
            "/.superpowers/",
            "/docs/superpowers/",
        ):
            if required not in rules:
                errors.append(f".gitignore must ignore {required}")

    gitattributes = root / ".gitattributes"
    if gitattributes.is_file():
        rules = gitattributes.read_text(encoding="utf-8").splitlines()
        if "dist/* text eol=lf" not in rules:
            errors.append(".gitattributes must enforce LF for dist/*")

    tracked, git_error = tracked_files(root)
    if git_error:
        errors.append(git_error)
    tracked_private = sorted(
        path
        for path in tracked
        if path.startswith("workspace/") and path != "workspace/.gitkeep"
    )
    if tracked_private:
        errors.append("Tracked private learner files: " + ", ".join(tracked_private))
    tracked_semantic_raw = sorted(
        path for path in tracked if path.startswith(".semantic-runs/")
    )
    if tracked_semantic_raw:
        errors.append(
            "Tracked raw semantic evaluation files: "
            + ", ".join(tracked_semantic_raw)
        )
    tracked_non_product = sorted(
        path
        for path in tracked
        if path.startswith(NON_PRODUCT_TRACKED_ROOTS)
    )
    if tracked_non_product:
        errors.append(
            "Tracked non-product artifacts: " + ", ".join(tracked_non_product)
        )
    for relative in sorted(tracked):
        if not relative.startswith(TRACKED_EVALUATION_ASSET_ROOTS):
            continue
        try:
            path = guarded_tracked_asset_path(root, relative)
        except GuardedAssetPathError as error:
            errors.append(str(error))
            continue
        try:
            assert_tracked_safe(
                path.read_text(encoding="utf-8"),
                forbidden_literals=(),
            )
        except (OSError, UnicodeError, ValueError):
            errors.append(f"Unsafe tracked evaluation asset: {relative}")

    errors.extend(semantic_runtime_errors(root))
    errors.extend(validate_distribution(root))
    errors.extend(validate_release_automation(root))
    errors.extend(validate_local_release_gate(root))
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Repository validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
