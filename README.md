# Socratic Learn

![Pixel-art Socrates thinking beside the Socratic Learn name and the line Learn anything. Prove it by doing.](docs/assets/socratic-learn-hero.png)

**Turn any AI agent into a tutor that checks whether you actually learned.**

Socratic Learn helps you learn any subject through a guided conversation that
ends in application. It works for any subject or skill, including physics,
programming, writing, and research.

Use it for **any subject** with **your AI host**. It measures
**progress through application**, not confidence alone.

```text
socratic "I want to learn how attention works"
```

[Install Socratic Learn](#quick-start)

[Python 3.11+](https://www.python.org/downloads/) · [License: MIT](LICENSE)

## Why it is different

The format can follow your needs. Diagrams, prose, code, and concrete examples
are presentation choices, not fixed learning styles. Your place on the route
comes from what you can explain, derive, predict, implement, or diagnose.

When an attempt reveals a gap, the tutor can offer a cue, a simpler case,
another representation, or a direct explanation. It then asks you to use the
idea again. A later session begins with a recorded gap instead of replaying the
previous answer.

A time limit changes how much the session covers, but it still keeps one final
application. I built Socratic Learn out of a recurring need in my work as an AI researcher.
I wanted a practical way to learn new theory and tools without mistaking a
fluent explanation for understanding. Mathematics and AI are starter curricula,
but they are examples rather than product boundaries. The same route can be
built for any subject or skill.

## Quick start

### 1. Install Socratic Learn

The current public build installs directly from this repository. You need
[Python 3.11+](https://www.python.org/downloads/) and
[pipx](https://pipx.pypa.io/). No repository clone or source build is required.

```text
pipx install "git+https://github.com/socraticlearn/socratic-learn.git"
socratic --version
```

### 2. Connect an AI host

Socratic Learn supplies the learning method. You also need one supported AI
host with access to a model. If you already use Pi, Claude Code, or Antigravity,
skip this step.

For the shortest general setup, follow
[Pi's official quickstart](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/quickstart.md).
Run `pi`, enter `/login`, connect a provider, and optionally use `/model` to
choose a model. You can instead use the official setup for
[Claude Code](https://code.claude.com/docs/en/quickstart) or
[Antigravity](https://antigravity.google/docs/getting-started/).

### 3. Start learning

Start without putting your learning goal in shell history:

```text
socratic
```

Choose **New session**, describe what you want to learn, and the selected host
opens the learning conversation. You can also pass a goal or choose a host
directly:

```text
socratic "I want to learn linear algebra"
socratic --agent pi
```

Starting without a goal is the privacy default. Socratic Learn does not store
provider credentials. The chosen host keeps its own authentication and model
settings.

Personal sessions use
`~/.socratic-learn/learning/.socratic-learn/workspace/`. To use the current
directory as the learning project, run:

```text
socratic --here
```

### Future package channels

The PyPI package is not public yet. The npm package is not public yet. After the
first release, these shorter commands will become available:

```text
pipx install socratic-learn
npx @socraticlearn/cli
```

Both future package channels run the same Python implementation. Neither ships
an AI host, model, provider account, or credentials. The npm route will require
Node.js and Python 3.11+ on the same machine.

Running `socratic` with no arguments opens a small menu: **New session**, your
five most recent saved entries, and **View all** when you have more saved
progress. The list can mix learning threads and legacy session records; choosing
a legacy record imports it into thread storage without removing the original
file. On a capable terminal, move with the arrow keys and press Enter; otherwise
pick from the numbered list. Supply a goal on the command line to skip the menu,
or run `socratic read` with a source to skip the menu entirely.

You can resume on Pi, Claude Code, or Antigravity from structured summaries and
checkpoints in your private workspace. Host chat logs are not used as session memory.
Completed encounters in a thread stay immutable once written. If a
launch ends before a valid checkpoint is written, the thread stays incomplete.
Older summaries may guide the first retrieval question, but they do not count as
fresh evidence until you demonstrate the idea again.

The launcher assembles a transient session-control payload in memory for the host process and does not persist that JSON on disk. Thread metadata and structured checkpoint files do persist, along with other private learner records already in the workspace. Launcher-owned storage does not keep raw host transcripts, provider credentials, or provider ids.

## Choose an AI host

Socratic Learn provides the learning method and private workspace. The launcher
host provides the interactive terminal and access to a provider and model.
Pi, Claude Code, and Antigravity are launcher hosts. They keep their own
authentication, quotas, tools, and model selection.

Pi offers a general interactive harness. Antigravity can be launched through
its `agy` command. Claude Code uses its own terminal experience. Cursor remains
a project integration rather than a launcher choice.

### Choose a model in Pi

Inside Pi, run `/model` to open the model picker. Selecting a model changes the
current session. To use it automatically in future sessions, highlight it in
the picker and press `Ctrl+S`. Resuming an existing Pi session restores that
session's latest model without changing the default for new sessions.

If a request already failed, changing the model does not resend it. Select the
new model, then submit a new message such as `continue`. A `429` response means
the selected provider or model has reached an account or project quota. The
error belongs to the provider, not Socratic Learn. Choose a model with available
quota, use `/login` to configure another provider, or wait for the stated reset.
Socratic Learn never reads or stores those credentials.

Supporting any subject does not mean every host knows every subject. Accuracy
still depends on the selected model, its sources, and its available tools.

Package lifecycle is owned by your installer. With pipx:

```text
pipx upgrade socratic-learn
pipx uninstall socratic-learn
```

`npx` is an on-demand invocation; it does not replace pipx upgrade or uninstall.
Package removal preserves learner records under `~/.socratic-learn/learning/`.

When a learning session starts, Socratic Learn checks PyPI at most once every
24 hours and prints a short notice when a newer release exists. The check never
reads learner data, fails silently offline, and can be disabled with
`SOCRATIC_NO_UPDATE_CHECK=1`. Its cache is
`~/.socratic-learn/update-check.json`.

Package installation is different from adding loaders inside a project. After
you install the published package, run `socratic install TARGET PROJECT`. If you
work from a source checkout, use `python -m socratic_learn install` instead. See
[Installation and lifecycle](docs/universal-installation.md).

Source and releases live at
[https://github.com/socraticlearn/socratic-learn](https://github.com/socraticlearn/socratic-learn).
The GitHub installation above is available now. PyPI, npm, and tagged releases
remain pending until the first public release completes.

## Learn from an article

`socratic learn` starts a general session. `socratic read` prepares one HTML or
XHTML page, or a textual PDF, for focused study.

```text
socratic read https://example.org/article
socratic read paper.pdf evaluate the main claim and its evidence
socratic read --agent pi paper.pdf explain its evidence
```

For a local development server or private network, add the explicit gate:

```text
socratic read --allow-private-network http://localhost/article
```

Socratic Learn creates the source bundle and shared study prompt. The launcher
host still owns the provider, model, credentials, quota, and optional research
tools.

Private bundles live under
`~/.socratic-learn/learning/.socratic-learn/workspace/sources/<source-id>/`.
With `--here`, they live under
`PROJECT/.socratic-learn/workspace/sources/<source-id>/`. Each bundle contains
an index, metadata, normalized sections, and the original bytes. Identical
input reuses a validated bundle instead of publishing a duplicate.

Supported input is HTTP or HTTPS HTML, XHTML, and regular local PDF files:

| Limit | Value |
| --- | --- |
| Raw input | 20 MiB |
| PDF length | 500 PDF pages |
| Extracted text | 5,000,000 Unicode code points |
| Socket timeout | 20-second read timeout |
| Redirects | five redirects |
| Section chunk | 12,000 code points |

There is no OCR and no JavaScript rendering. Image-only PDFs, encrypted PDFs,
and dynamic pages that need a browser are rejected with an actionable message.

Study is source-bounded by default. The tutor separates a source assertion,
within-source support, and external verification. External research requires an
explicit learner request, and the tutor must disclose the capability and
sources it used.

Source text is untrusted study material. Prompt-injection resistance and tool
restraint are best-effort controls, not a sandbox. The selected host may still
have network or shell access under its own policy.

For an image-only PDF, use a textual export. For dynamic pages, save a static
page or PDF. A quota or model error belongs to the host provider. For changing
agents, pass `--agent` or update `~/.socratic-learn/config.json`.

## How the method works

The protocol follows one connected path:

`objective → prerequisites → guided route → application → evidence`

1. **Define the outcome.** Turn the topic into something you can demonstrate.
2. **Find the starting point.** Check only prerequisites that could block it.
3. **Preview the route.** Explain what comes first and why, then start teaching
   automatically. You can redirect at any time.
4. **Teach adaptively.** Ask when you can reason forward and explain when
   required information is missing.
5. **Require evidence.** Close after an application, with remaining gaps and a
   useful next step.

The learning behavior is defined in [pedagogy](core/pedagogy.md), the
[session protocol](core/session-protocol.md),
[assessment](core/assessment.md), and the
[evidence policy](core/evidence-policy.md).

## Research and limitations

The [learner preference catalog](docs/research/learner-preference-patterns.md)
separates preference, engagement, and measured learning. The project uses that
distinction to adapt presentation without treating confidence or activity as
mastery evidence.

Research that informed specific design decisions includes
[Mahoney et al. (2023)](https://doi.org/10.1016/j.tsc.2023.101422),
[Munshi et al. (2023)](https://doi.org/10.1111/jcal.12761),
[Borchers et al. (2025)](https://doi.org/10.1016/j.compedu.2025.105364),
[van den Broek et al. (2025)](https://doi.org/10.1016/j.learninstruc.2025.102196),
[Stogniy et al. (2020)](https://doi.org/10.1096/fasebj.2020.34.s1.04148), and
[Lee et al. (2025)](https://doi.org/10.48550/arXiv.2509.16262).

Those studies examine their own learners, settings, and teaching methods. They
do not validate Socratic Learn as a complete product or guarantee mastery.

The first semantic baseline ran three unchanged-protocol Antigravity sessions.
All three stopped before a learner response because the host denied the same
workspace command. For that reason, no pedagogy score was assigned. The
[sanitized baseline report](evaluation/reports/2026-11-06-antigravity-baseline.md)
records the result without publishing raw learner evidence.

## Technical documentation

The runtime is dependency-light Python 3.11+ with `pypdf` for textual PDF
extraction. The checked-in prompt bundle and manifest are deterministic.
Learner profiles, progress, source bundles, and raw evaluation streams remain
private and excluded from published packages.

Installation protects against ordinary collisions, accidental concurrent
changes, and cooperating Socratic Learn processes. On POSIX systems, hostile
processes running as the same OS user are outside the threat model. A final-path
TOCTOU window remains because a final path mutation cannot be tied to an inode
that is already open. Ambiguous paths are preserved for manual recovery.

Contributors can clone and build the source:

```powershell
git clone https://github.com/socraticlearn/socratic-learn.git
cd socratic-learn
python -m socratic_learn build
```

Create the ignored example workspace with
`Copy-Item -Recurse workspace.example\* workspace` on PowerShell or
`cp -R workspace.example/. workspace/` on POSIX. Before publishing, run
`git ls-files workspace`. Its only output should be `workspace/.gitkeep`.

- [Installation and lifecycle](docs/universal-installation.md)
- [Launcher host contract](docs/launcher-adapters.md)
- [Adding an adapter](docs/adding-an-adapter.md)
- [Manifest reference](docs/manifest-reference.md)
- [Cursor adapter](adapters/cursor/README.md)
- [Claude Code adapter](adapters/claude-code/README.md)
- [Pi adapter](adapters/pi/README.md)
- [Pi root-ready skill](adapters/pi/skills/socratic-learn/SKILL.project.md.template)
- [Native CI workflow](.github/workflows/native-python.yml)

## License

Socratic Learn is available under the [MIT License](LICENSE).
