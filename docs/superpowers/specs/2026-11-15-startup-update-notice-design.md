# Startup Update Notice Design

**Date:** 2026-11-15
**Status:** Approved

## Purpose

Tell learners when a newer Socratic Learn release is available without making
startup depend on network access.

## Behavior

Learning commands check the public PyPI package metadata at most once every 24
hours. A newer release produces a short notice before the session menu. Equal,
older, malformed, unavailable, or timed-out responses produce no notice and do
not interrupt startup.

The cache lives at `update-check.json` inside `SOCRATIC_LEARN_HOME`, or inside
`~/.socratic-learn` when no override is set. `SOCRATIC_NO_UPDATE_CHECK=1`
disables both network access and the notice.

Python and pipx launches recommend:

```text
pipx upgrade socratic-learn
```

The npm adapter marks its child process so the same checker recommends:

```text
npx @socraticlearn/cli@latest
```

## Privacy and failure handling

The request only retrieves `https://pypi.org/pypi/socratic-learn/json`. It does
not read or transmit learning goals, sources, session records, or agent
preferences. Cache and network failures are ignored because update discovery is
advisory.

## Verification

Tests cover version comparison, the 24-hour cache, opt-out, offline and invalid
responses, CLI rendering, npm channel marking, and the packaged Python/npm
artifacts.
