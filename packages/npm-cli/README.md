# @socraticlearn/cli

Node launcher for [Socratic Learn](https://github.com/socraticlearn/socratic-learn).
It finds a local Python 3.11 or newer interpreter, prepends the vendored package
stack to `PYTHONPATH`, and executes the same `socratic_learn.cli:entry_point`
entry point used by the PyPI package.

## Requirements

- Node.js 18 or newer
- Python 3.11 or newer on the same machine

You can point the launcher at a specific interpreter with `SOCRATIC_PYTHON`.

## Usage

```text
npx @socraticlearn/cli
npx @socraticlearn/cli --version
npx @socraticlearn/cli learn linear algebra
```

## Development

```text
npm test --prefix packages/npm-cli
```

The vendor tree is generated from the release wheel by `tools/build_npm_package.py`.
Generated `vendor/` output and npm tarballs are not committed to git.

`vendor-requirements.lock` uses a strict one-line pip format per dependency:

```text
name==version \
    --hash=sha256:<64 lowercase hex digits>
```

Each non-empty line must pin exactly one pure-Python wheel hash. Platform markers,
multiple hashes on one requirement, and unlocked dependency lines are rejected.