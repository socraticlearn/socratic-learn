# Package Manager Distribution Design

**Date:** 2026-11-14
**Status:** Approved direction

## Purpose

Make the normal Socratic Learn installation easy to discover, install, update,
and remove without asking users to clone the repository, run a downloaded
script, or manage a Python environment.

The official installation channels will be:

- Winget for Windows x86-64.
- A project-owned Homebrew tap for macOS x86-64 and Linux x86-64.
- Pipx only as a documented fallback for unsupported architectures or systems.

The first package-manager release will not claim ARM support. ARM users will
receive the exact pipx fallback until native ARM artifacts are available.

## User experience

The normal path is one package-manager command:

```text
winget install --id SocraticLearn.SocraticLearn --exact
```

```text
brew install socraticlearn/tap/socratic-learn
```

After installation, the user runs:

```text
socratic
```

The existing interactive launcher remains the entry point. It offers a new
session or saved sessions and then launches an available AI host. If no
supported host is available, the launcher must provide concise setup guidance
instead of failing with an implementation error.

The README will lead with Winget and Homebrew. Pipx will appear in a clearly
labelled fallback section, not beside the normal installation commands.

## Package ownership

Winget and Homebrew own the installed executable and its lifecycle. Users
update and remove Socratic Learn with their package manager:

```text
winget upgrade --id SocraticLearn.SocraticLearn --exact
winget uninstall --id SocraticLearn.SocraticLearn --exact
```

```text
brew upgrade socratic-learn
brew uninstall socratic-learn
```

The `socratic update` and `socratic uninstall` commands will be removed from
the public CLI. Socratic Learn will no longer create native channel metadata or
replace its own executable. Removing the package will continue to preserve
private learning records. Documentation will identify the private data
location and explain how to remove it separately.

## Release architecture

GitHub Releases remain the source of immutable, versioned native artifacts.
The main repository and its releases will live under the brand-owned
`socraticlearn` GitHub organization, not a personal account. The existing
repository will be transferred to
`https://github.com/socraticlearn/socratic-learn`. The organization will also
own `https://github.com/socraticlearn/homebrew-tap`.

The release workflow will produce:

- `socratic-windows-x86_64.exe`
- `socratic-macos-x86_64`
- `socratic-linux-x86_64`
- SHA-256 checksums
- the Python wheel and source distribution used by the pipx fallback
- generated Winget and Homebrew package definitions for the same version

Every package definition must use an exact release URL and the checksum of the
matching artifact. Package definitions must never point at a moving branch,
`latest` URL, or unversioned download.

### Winget

Socratic Learn will use a portable Winget manifest with package identifier
`SocraticLearn.SocraticLearn`, architecture `x64`, and command alias
`socratic`. The manifest will reference the Windows executable from the
matching GitHub Release.

The generated manifest will be validated before publication and submitted to
the Windows Package Manager Community Repository. A release is not described
as available through Winget until that manifest is accepted and visible to a
normal `winget search`.

### Homebrew

The project will use the public tap
`socraticlearn/homebrew-tap` with a `socratic-learn` formula. The formula
will select the matching macOS or Linux artifact, verify its SHA-256 checksum,
install it as `socratic`, and contain a version check.

The tap remains separate from the application repository because Homebrew
discovers third-party formulae through tap repositories. Release automation
will update the tap only after all release artifacts and checksums exist.

## Publication flow

Release publication is ordered:

1. Create the `socraticlearn` organization, transfer the main repository, and
   create its `homebrew-tap` repository.
2. Update repository links, local remotes, trusted publishing, and release
   credentials for the new owner.
3. Verify tests, repository validation, version, and clean generated output.
4. Build and smoke-test the Python and native artifacts.
5. Sign the Windows executable. Sign and notarize the macOS executable. Verify
   both results before checksums are calculated.
6. Publish the immutable GitHub Release and PyPI fallback.
7. Generate package definitions from the published artifact metadata.
8. Update the Homebrew tap and submit the Winget manifest.
9. Verify installation, `socratic --version`, launcher startup, upgrade, and
   removal through each package manager.
10. Update public availability claims only after each channel is actually live.

External publication failures must not modify an already published binary or
reuse a version number. They leave the GitHub Release intact, report the failed
channel, and allow the same package definition to be submitted again.

## Repository changes

The implementation will:

- add deterministic package-definition generation and validation;
- add focused tests for package identifiers, URLs, checksums, architecture,
  command exposure, and version consistency;
- update release automation to publish package metadata only after artifacts
  exist;
- remove the bootstrap scripts from the normal release and documentation;
- remove self-managed native update and uninstall behavior, its channel
  metadata, and code that is no longer reachable;
- retain PyPI publication for the pipx fallback;
- rewrite installation documentation around Winget, Homebrew, and the fallback;
- preserve project-adapter commands such as `socratic install cursor`, which
  install the learning protocol into a project and do not manage the CLI
  package.

Historical design and plan files remain unchanged.

## Failure handling

Package-manager checksum verification is mandatory. A checksum mismatch must
stop installation. Unsupported architectures receive the pipx command and the
Python 3.11 requirement. Documentation for a missing Winget or Homebrew
installation provides a link to that manager's official setup instructions
plus the fallback, without silently downloading or executing another installer.

The public package-manager release requires Authenticode signing for Windows
and signing plus notarization for macOS. Signing credentials stay outside the
repository and are exposed only to the protected release job. A signing or
notarization failure stops publication before checksums or package definitions
are generated. Linux artifacts continue to rely on immutable release URLs and
package-manager checksum verification.

## Verification

Repository tests will verify deterministic package metadata and prevent stale
versions or hashes. Release CI will validate the Winget manifest and Homebrew
formula. Platform smoke checks will install the locally generated package,
run `socratic --version`, start the launcher far enough to confirm normal
startup, then upgrade and remove it through the package manager.

The public installation claim is accepted only when a clean x86-64 machine can:

1. install with the documented package-manager command;
2. run `socratic` without editing `PATH`;
3. receive useful guidance if no AI host is configured;
4. update and remove the executable through the same package manager; and
5. retain private learning records after package removal.

Windows and macOS checks must also confirm a valid platform signature. The
installation must not be advertised as the simple public path while either
platform still presents an unknown-publisher warning under its normal security
defaults.

## Sources

- Microsoft, "Create your package manifest":
  https://learn.microsoft.com/en-us/windows/package-manager/package/manifest
- Microsoft, "Submit packages to Windows Package Manager":
  https://learn.microsoft.com/en-us/windows/package-manager/package/
- Homebrew, "Formula Cookbook":
  https://docs.brew.sh/Formula-Cookbook
- Homebrew, "brew(1)":
  https://docs.brew.sh/Manpage
