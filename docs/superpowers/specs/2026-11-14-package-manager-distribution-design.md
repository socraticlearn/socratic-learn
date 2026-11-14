# Package Manager Distribution Design

**Date:** 2026-11-14
**Status:** Approved direction

## Purpose

Make the normal Socratic Learn installation easy to discover, install, update,
and remove without asking users to clone the repository, run a downloaded
script, or manually manage a Python environment.

PyPI is the primary public channel. An optional Node entry point offers the same
behavior for teams that standardize on npm. Winget, Homebrew, PyInstaller
binaries, bootstrap scripts, platform signing, notarization, native channel
metadata, and native self-update are outside the active distribution design.

## User experience

The normal path is:

```text
pipx install socratic-learn
```

After installation, the user runs:

```text
socratic
```

The optional Node path is:

```text
npx @socraticlearn/cli
```

Both routes execute the same Python implementation. Python 3.11 or newer is
required by both routes.

The existing interactive launcher remains the entry point. It offers a new
session or saved sessions and then launches an available AI host. If no
supported host is available, the launcher must provide concise setup guidance
instead of failing with an implementation error.

The README will lead with pipx. The npm path will appear as an optional
alternative for users who prefer `npx`.

## Package ownership

Pipx owns the normal installed CLI and its lifecycle. Users update and remove
Socratic Learn with pipx:

```text
pipx upgrade socratic-learn
pipx uninstall socratic-learn
```

Users who install through npm manage lifecycle with npm or their chosen package
manager for scoped packages.

The `socratic update` and `socratic uninstall` commands are removed as package
lifecycle commands. Socratic Learn will not create native channel metadata or
replace its own executable.

Project-adapter operations remain separate from package installation:

```text
socratic install generic
socratic install cursor
socratic install claude-code
socratic install pi
```

These commands install the learning protocol into a project. They do not
install or remove the CLI package.

Private learner records remain outside package-managed files and survive package
removal. Documentation will identify the private data location and explain how
to remove it separately.

## npm adapter

The npm package `@socraticlearn/cli` vendors the release-built Socratic Learn
wheel and its locked, pure-Python dependencies. A small Node launcher finds an
appropriate Python interpreter and invokes `socratic_learn.cli:entry_point`.

The npm adapter must forward arguments, standard streams, signals, and exit
status to the Python process. When Python 3.11 or newer is absent, it must fail
with concise setup guidance rather than an opaque error.

## Release architecture

GitHub Releases remain the source of immutable, versioned release artifacts.
The main repository and its releases live under the brand-owned
`socraticlearn` GitHub organization:

- GitHub: `socraticlearn/socratic-learn`
- PyPI: `socratic-learn`
- npm: `@socraticlearn/cli`

The existing repository will be transferred to
`https://github.com/socraticlearn/socratic-learn`.

The release workflow will produce the Python wheel and source distribution for
PyPI, the npm package contents derived from that release, and checksums as
needed for verification.

Registry publication must use one verified version and one source commit. Every
published package must reference that exact release and must never point at a
moving branch, `latest` URL, or unversioned download.

## Publication flow

Release publication is ordered:

1. Complete external checkpoints: create the `socraticlearn` organization,
   transfer the main repository, configure PyPI trusted publishing, and configure
   npm scope and trusted publishing for `@socraticlearn/cli`.
2. Update repository links, local remotes, and release credentials for the new
   owner.
3. Verify tests, repository validation, version, and clean generated output.
4. Build release artifacts for the tagged version (wheel, source distribution,
   and npm package contents).
5. Run pre-publish artifact smoke checks on clean platforms. Install and exercise
   the built wheel and npm bundle directly from those artifacts, not from PyPI or
   npm. Block publication if these checks fail.
6. Publish the immutable GitHub Release for that version and attach the release
   artifacts.
7. Publish to PyPI and npm from the same source commit, using the GitHub Release
   as the version anchor.
8. Confirm each registry entry resolves for the published version.
9. Run post-publish registry smoke checks on clean platforms. Install with
   `pipx install socratic-learn` and exercise `npx @socraticlearn/cli` as a
   public user would, then confirm upgrade and removal through the owning manager.
10. Update public availability claims only after step 8 succeeds and step 9
    passes.

External publication failures must not modify an already published artifact or
reuse a version number. They leave the GitHub Release intact, report the failed
registry, and allow republication after the blocker is resolved.

## Repository changes

The implementation will:

- publish `socratic-learn` to PyPI as the primary installable package;
- publish `@socraticlearn/cli` with the Node launcher and vendored wheel stack;
- remove self-managed native update and uninstall behavior, channel metadata,
  and code that is no longer reachable;
- remove bootstrap installers from the normal release and documentation;
- rewrite installation documentation around pipx and the optional npm path;
- retain project-adapter commands such as `socratic install cursor`, which
  install the learning protocol into a project and do not manage the CLI
  package.

Historical design and plan files remain unchanged except where this
specification supersedes prior distribution direction.

## Failure handling

Unsupported environments receive clear requirements: Python 3.11 or newer for
both entry points. The npm adapter must surface the same requirement when no
suitable interpreter is available.

Documentation for a missing pipx installation points to official pipx setup
instructions. Documentation for the npm path explains scope installation or
`npx` usage without silently downloading unrelated installers.

## Verification

Repository tests will verify package metadata, version consistency, and that
the npm launcher targets the correct Python entry point. Release CI will
validate PyPI and npm publication inputs from the same source commit.

Pre-publish artifact smoke checks exercise the built wheel and npm bundle before
any registry upload. Post-publish registry smoke checks, on clean machines:

1. install with `pipx install socratic-learn` or use `npx @socraticlearn/cli`
   as documented;
2. run `socratic` or the npm equivalent and confirm the launcher starts;
3. receive useful guidance if no AI host is configured;
4. update and remove through pipx or npm as appropriate; and
5. retain private learning records after package removal.

Public availability must not be claimed until each registry entry resolves for
the version and post-publish registry smoke checks pass.

## Sources

- pipx, documentation: https://pipx.pypa.io/
- PyPI, trusted publishing: https://docs.pypi.org/trusted-publishers/
- npm, scopes: https://docs.npmjs.com/cli/v10/using-npm/scope
- npm, trusted publishing: https://docs.npmjs.com/trusted-publishers