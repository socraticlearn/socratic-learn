# Cursor adapter

Socratic Learn can install a **project-local** Cursor skill so the IDE agent can
run the same learning protocol inside a repository. This is not automatic when you
open the Socratic Learn source tree; you install into **your** project explicitly.

## Install into a project

From a machine that already has the `socratic` CLI:

```text
socratic install cursor PROJECT
```

From a source checkout without a global install:

```text
python -m socratic_learn install cursor PROJECT
```

Replace `PROJECT` with the path to the target repository. The command writes:

`PROJECT/.cursor/skills/socratic-learn/SKILL.md`

The installer renders it from the runtime
[template](../../socratic_learn/templates/cursor-skill.md). The checked-in
[adapter source](skills/socratic-learn/SKILL.md) documents how Cursor maps the
shared protocol to its own capabilities.

After upgrading Socratic Learn, refresh an intact managed installation with:

```text
socratic install cursor PROJECT --update
```

## What the skill does

The skill loads the universal [agent entry](../../AGENTS.md) and links to the
canonical [pedagogy](../../core/pedagogy.md), [session protocol](../../core/session-protocol.md),
[assessment](../../core/assessment.md), and [evidence policy](../../core/evidence-policy.md).
It maps those shared contracts to Cursor capabilities only.

The adapter exposes the same subject-neutral protocol. Mathematics and AI are included
starter curricula rather than adapter limits.

## Not a launcher host

Cursor integration does not replace Pi, Claude Code, or Antigravity for `socratic`
menu launches. Use launcher hosts for terminal sessions; use this adapter when you
want learning behavior inside Cursor on a specific project.

See also [Installation and lifecycle](../../docs/universal-installation.md) and
[Adding an adapter](../../docs/adding-an-adapter.md).
