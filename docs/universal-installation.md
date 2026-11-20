# Socratic Learn Installation

## Install today from GitHub

The current public build installs directly from the official repository.
Users do not need to clone or build it. Install with
[pipx](https://pipx.pypa.io/) and
[Python 3.11 or newer](https://www.python.org/downloads/):

```text
pipx install "git+https://github.com/socraticlearn/socratic-learn.git"
socratic --version
```

After installation, run `socratic` from any directory. With no goal on the
command line, the launcher opens a small menu for a new session, recent saved
entries, and legacy imports. Pi, Claude Code, or Antigravity must already be
installed and authenticated; Socratic Learn does not bundle a host, model,
provider, or credentials.

Use the arrow keys and Enter on a capable terminal. A numbered list is used
when interactive navigation is unavailable.

## Future package channels

The PyPI package is not public yet. The npm package is not public yet. After the
first release, users will be able to choose either command:

```text
pipx install socratic-learn
npx @socraticlearn/cli
```

The future `npx` route will still need Python 3.11+ on the same machine. Each
invocation will download the adapter on demand and run the same Python
implementation as the pipx package.

## Prerequisites and setup links

Pipx installation needs Python 3.11+ and pipx itself. The optional `npx` route
also needs Node.js while still relying on Python 3.11+. Either way, Pi, Claude
Code, or Antigravity must already be installed and signed in; Socratic Learn does
not install hosts or provider credentials for you.

- Python: [python.org/downloads](https://www.python.org/downloads/)
- pipx: [pipx.pypa.io](https://pipx.pypa.io/)
- Node.js: [nodejs.org](https://nodejs.org/)
- Pi: [official quickstart](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/quickstart.md)
- Claude Code: [official quickstart](https://code.claude.com/docs/en/quickstart)
- Antigravity: [official setup](https://antigravity.google/docs/getting-started/)

Repository and issue tracking:
[https://github.com/socraticlearn/socratic-learn](https://github.com/socraticlearn/socratic-learn)

## Upgrades, removal, and preserved learner data

Pipx owns package lifecycle:

```text
pipx upgrade socratic-learn
pipx uninstall socratic-learn
```

Package installation has no launcher-owned update or uninstall command.
Package removal preserves learner profiles, progress, sessions,
and other private records under `~/.socratic-learn/learning/`. Delete those
directories yourself only when you intend to remove learner data.

For `npx`, each invocation resolves the package at run time. Upgrades follow
npm publishing; uninstalling a global npm copy is not required for occasional
`npx` use.

Learning-session startup checks the public PyPI package metadata at most once
every 24 hours. A newer version produces the update command for the active
distribution channel. Network and cache failures do not block startup. The
request does not include learner data. Set `SOCRATIC_NO_UPDATE_CHECK=1` to
disable it; cached metadata is stored at
`~/.socratic-learn/update-check.json` or under `SOCRATIC_LEARN_HOME`.

## Host ownership and first-run behavior

The launcher host supplies the terminal, provider authentication, model
selection, quotas, and optional tools. Socratic Learn supplies the learning
method, private workspace, and session checkpoints. Running `socratic` starts the
interactive new or resume menu unless you pass a goal on the command line or run
`socratic read` with a source. Use `socratic --here` when you want the current
directory treated as the project workspace; it does not skip the menu.

Pi model selection is session-scoped unless the learner saves a default. Run
`/model`, highlight the desired model, and press `Ctrl+S` to use it for future
sessions. Resuming a Pi session restores its latest model without changing that
default. After switching models in response to a failed request, submit a new
message because Pi does not automatically retry the failed request.

A provider `429` response is a quota failure, not a Socratic Learn failure. Read
the provider and model named in the error, then select a model with available
quota, configure another provider with `/login`, or wait for the reported reset.
Changing models does not grant quota or bypass provider account restrictions.
Socratic Learn does not inspect or store provider credentials.

## Source and development installation

Contributors clone the repository, build the checked-in distribution, and run
tests locally. Installing the published package is separate from adding loaders
inside a project. After pipx or `npx` installation, run
`socratic install TARGET PROJECT`. From a source checkout, use
`python -m socratic_learn install TARGET PROJECT` instead.

Use Python 3.11 or newer from the repository root. The distribution supports
exactly four project-local targets: `generic`, `cursor`, `claude-code`, and
`pi`. “Universal” means that one deterministic tutor protocol can be
distributed through these adapters. It does not certify every model, agent
version, or host capability.

Transactional build and update moves require an atomic native no-replace
rename: `renameat2(RENAME_NOREPLACE)` on Linux, `renameatx_np(RENAME_EXCL)` on
macOS 10.12 or newer, or the existing handle-bound operation on Windows. The
Linux C library must expose `renameat2`, and the active filesystem must support
the flag. If the API, kernel, or filesystem support is unavailable, the command
does not fall back to an overwrite-prone rename. It fails with recovery
guidance, preserves managed content, and cleans pre-publication update staging
when its identity remains provable.

These controls protect against ordinary collisions, accidental concurrent
changes, and cooperating Socratic Learn processes. Atomic no-replace moves,
parent-directory file descriptors, identity rechecks, exact-topology cleanup,
recovery artifacts, and Windows handle-bound operations remain mandatory. On
POSIX, malicious or noncooperating processes running as the same OS principal
are out of scope: `rename`, `unlink`, and `rmdir` cannot condition a leaf-entry
mutation on an already-open inode, so an irreducible leaf-entry TOCTOU window
remains. A detected ambiguity is preserved and reported; this limitation does
not disable normal Linux or macOS build, install, update, or cleanup.

Build the checked-in distribution before installing:

```powershell
python -m socratic_learn build
```

`export prompt --output PATH` is also no-force: every parent is created through
identity-bound operations, an existing output is rejected, and publication is
an atomic native no-replace move. If another process wins the final output
race, its file is preserved. The error reports the private temporary artifact
for recovery; it is never blindly unlinked after an identity ambiguity.

## Generic

```powershell
python -m socratic_learn install generic C:/path/to/project
```

The installer creates `.socratic-learn/loader.md`. Point an agent that accepts
project instructions to that loader. It links to the installed canonical
prompt rather than copying the pedagogy.

## Cursor

```powershell
python -m socratic_learn install cursor C:/path/to/project
```

Cursor receives `.cursor/skills/socratic-learn/SKILL.md`. The skill is a thin
loader for `.socratic-learn/socratic-learn.prompt.md`.

## Claude Code

```powershell
python -m socratic_learn install claude-code C:/path/to/project
```

If root `CLAUDE.md` does not exist, the installer creates and owns a thin
loader. If it already exists as a regular file, the installer preserves it and
prints the exact snippet to append manually. It never merges or overwrites a
user-owned `CLAUDE.md`.

## Pi

```powershell
python -m socratic_learn install pi C:/path/to/project
```

Pi receives `.pi/skills/socratic-learn/SKILL.md`, which links to the same
project-local prompt and private workspace as the other targets.

## Collisions and ownership

Every install records schema-2 ownership with product identifier
`socratic-learn` in
`.socratic-learn/install.json`. A clean install refuses an existing
`.socratic-learn/` path or native adapter destination. There is no `--force`
option. The receipt lists managed files and hashes every managed file except
the receipt itself. The installer also creates parent directories needed to
publish those files, but parent directories, staging directories, backup
directories, and recovery directories are not managed-file receipt entries
and are not receipt-hashed. The installer does not automatically merge,
replace, or delete pre-existing agent files.

After a successful install or update, private staging and backup directories
are removed; required installation and adapter parent directories remain as
containers for managed files. Before publication, failed operations clean up
operation-created temporary paths only when the exact recorded topology, entry
types, and per-entry identities still match. An inserted, replaced, missing,
or unknown child preserves the complete remaining recovery tree and produces
an actionable error.

Build applies the same rule to the previous `dist/` backup. It removes that
backup only when the old distribution has exactly the deterministic manifest
and bundle topology, their hashes validate, and every entry remains
identity-bound through the swap. Unknown or changed content is preserved in
the reported backup path.

Publication is deliberately conservative. If a failure occurs after a path
may have been published, or if identity-safe restoration or cleanup cannot be
proved, the operation preserves partial published paths, staging directories,
backups, and any other reported recovery paths for manual recovery. Those
preserved paths are recovery evidence, not newly adopted managed-file receipt
entries. The error lists their locations. Do not rerun installation or delete
them blindly: inspect the listed paths, establish identity and ownership,
recover manually, and then run doctor.

## Updating

Update only an intact installation with the same target:

```powershell
python -m socratic_learn install cursor C:/path/to/project --update
```

Before changing anything, update validates the receipt and every managed
file. An unchanged update is safe. If a managed file was edited, is missing,
or no longer matches its recorded identity, update refuses before replacing
other files. Preserve user changes and restore or reinstall only after review.

## Migrating from Learning Tutor

An intact schema-1 installation under `.learning-tutor/` can be migrated by
running the canonical update command with the target recorded in its receipt:

```powershell
python -m socratic_learn install TARGET C:/path/to/project --update
```

Migration verifies every legacy managed hash before mutation, refuses when
both old and new roots exist, and rejects unowned paths outside the private
workspace. It renames the managed root and owned native skill without
overwriting a destination, preserves private workspace content, replaces
managed files, and publishes the schema-2 receipt last. Modified or ambiguous
legacy installations remain untouched or are preserved with explicit recovery
paths.

## Private learner state

Personal launcher records belong under
`~/.socratic-learn/learning/.socratic-learn/workspace/`. Project-adapter
records belong under `PROJECT/.socratic-learn/workspace/`. The nested
`.socratic-learn/.gitignore` ignores workspace content while retaining
`workspace/.gitkeep`. Confirm repository behavior with:

```powershell
git check-ignore C:/path/to/project/.socratic-learn/workspace/private.md
```

## Doctor

```powershell
python -m socratic_learn doctor C:/path/to/project
```

`Installation healthy.` with exit code 0 means the receipt, managed hashes,
manifest, bundle, loader discovery path, and private ignore rules passed the
read-only checks. Diagnostic blocks use a stable code, path, message, and
remediation and exit with code 4. Doctor does not repair or delete files.

For `missing-receipt` or any recovery-state diagnostic, preserve all partial
and temporary paths and complete manual recovery first. For
`owned-file-changed`, restore the file from a trusted installation or keep the
change and perform a reviewed clean reinstall. For `tracked-private-file`,
remove the private path from the Git index without deleting the local file.

## Native CI

`.github/workflows/native-python.yml` defines a Python 3.11 matrix for native
Windows, Ubuntu, and macOS. Each job builds, runs the full test suite and
validator, performs install/update/doctor smoke checks, and requires clean
diff/status output. The workflow is structurally checked by repository tests.
Only Windows was executed locally for this change; native Linux and macOS
results remain pending until the hosted jobs run.
