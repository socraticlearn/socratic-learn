# Launcher Adapters

Launcher adapters connect the installed `socratic` console to an interactive
agent executable. They are not project-discovery adapters: they start a host
process for a learning session rather than placing host-specific files in an
existing repository.

## Current hosts

Host selection follows this executable detection order:

1. Pi: detect `pi`.
2. Claude Code: detect `claude`.
3. Antigravity: detect `agy`.

Manage the persistent default with:

```text
socratic host list
socratic host set NAME
socratic host clear
```

`socratic host list` shows which launchers are installed and marks the saved
default. `socratic host set NAME` requires a detected host and stores a persistent
default for later launches. `socratic host clear` removes that default. Host
commands do not accept the launcher options `--agent`, `--here`, or
`--allow-private-network`; combining them with `socratic host ...` is a usage
error.

An explicit `--agent` selection must be detected and overrides the saved default
for that launch only; it is a one-session override and does not change the
persistent default. Without an explicit selection, the launcher uses the saved
default when it is still installed. When no usable default exists (none saved,
or the saved host is no longer installed), the launcher asks the user to choose
and automatically saves that choice as the new default. After
`socratic host clear`, the next launch therefore prompts, and the choice made
then is saved again.

## Ownership boundary

Socratic Learn prepares the tutoring protocol, learner workspace, and launch
payload. The selected host and its provider keep authentication, quotas, tools,
model choice, and billing. Socratic Learn does not run models and does not read
or store provider credentials.

## Exact command construction

The launcher passes an argument vector directly to the child process; it does
not interpolate a shell command.

- Pi: `pi --append-system-prompt PROTOCOL -- LAUNCH_PAYLOAD`
- Claude Code: `claude --append-system-prompt-file PROTOCOL -- LAUNCH_PAYLOAD`
- Antigravity: `agy --prompt-interactive BOOTSTRAP`

`PROTOCOL` is the absolute path to the prepared
`.socratic-learn/socratic-learn.prompt.md`. `LAUNCH_PAYLOAD` is one opaque
final argument for every host. It ends with a fenced session control block:

```text
--- SOCRATIC LEARN SESSION CONTROL (JSON) ---
```

followed by a sorted JSON object and:

```text
--- END SOCRATIC LEARN SESSION CONTROL ---
```

The control object includes at least:

- `checkpoint_path`: absolute pending path where the tutor writes exactly one
  valid session record at close.
- `mode`: `new` or `resume`.
- `resume`: host-independent resume metadata, including
  `prior_complete_encounter_path`, `prior_complete_encounter_role`,
  `latest_launch_incomplete`, `must_reestablish_current_state`, and
  `saved_next_step` when resuming a stored thread.
- `thread`: the learning goal, status, and display title for menu display.
- `session_record_contract`: public v2 close contract for the session parser.
  It includes `heading_pattern`, `title_rule`, ordered `section_headings`,
  `record_version` (currently `2`), `record_version_marker` (the
  `<!-- socratic-learn-record-version: 2 -->` HTML comment placed after the
  heading), and `instruction` text. Version 2 records use evidence identifiers
  `[E#]` in **Concepts established** and **Application evidence**, progress
  identifiers `[P#]` only in **Application evidence**, and gap identifiers
  `[G#]` in **Remaining gaps** and **Next step**. The same `[E#]` set must
  appear in **Concepts established** and **Application evidence**; **Next step**
  must list a nonempty subset of the `[G#]` identifiers in **Remaining gaps**
  when gaps remain, and both gap sections use exactly `- None.` when no gaps
  remain. Structured application bullets require exactly five pipe-separated
  fields in order: `Task`, `Conditions`, `Result`, `Support`, and `Limits`.
  Empty structured sections use exactly one sentinel bullet: `- None.` for
  concepts and gaps (including **Next step** when there are no gaps), and
  `- None collected.` for application evidence. Keep
  `E`/`P`/`G` identifiers in the private record only; never expose them in
  learner-facing conversation. A compliant host must produce a record that
  passes the launcher session parser. When
  `prior_complete_encounter_role` is `prior_evidence_only` or
  `must_reestablish_current_state` is true after an interrupted launch, resume
  uses structured session summaries and `prior_complete_encounter_path`, not
  provider chat history.

Antigravity wraps the same launch payload after a JSON protocol-path prefix and
a delimiter:

```text
Before responding, read and follow the Socratic Learn protocol whose path is encoded as JSON data:
{"protocol_path": "PROTOCOL"}.

--- SOCRATIC LEARN LAUNCH ---
```

The final bootstrap argument is that prefix plus `LAUNCH_PAYLOAD` unchanged.

Resume stays host-independent: the tutor uses structured session summaries and
launcher-supplied prior paths, not provider chat history or provider-specific
session restoration.

## Host-owned behavior

The child inherits authentication from the selected host; Socratic Learn does
not read or store provider credentials. It also inherits terminal input,
output, permissions, environment, and host-specific model and provider policy.
The launcher runs the child in the prepared personal or `--here` workspace and
returns the child's exit status.

## Adding a launcher host

A new launcher adapter must define:

- its stable public agent name and executable detection names;
- exact command construction as an argument vector;
- how the full protocol path and exact final launch payload are delivered;
- any host limitation that changes interactive terminal behavior.

Tests must use a fake executable found through an isolated `PATH`; no provider
account or network request belongs in the release gate. Required tests cover
detection, exact arguments, protocol delivery, working directory, Unicode and
spaces, saved-default reuse, explicit override, inherited exit status, and no
mutation outside the launcher's defined workspace boundaries in the controlled
acceptance fixture. This verifies the isolated home, fake-host log, untouched
sibling tree, and source repository; it does not claim observation of the
whole operating system.

For project-local discovery files instead, follow
[Adding an Adapter](adding-an-adapter.md).

## Source study (read mode)

Read-mode sessions build one shared `source_study_payload` string (learner goal,
absolute `index.md` and `metadata.json` paths, and untrusted-source rules). The
launcher does not embed normalized source text in argv. The launcher still ends
the launch payload with the same session control JSON trailer.

When `source_study_payload` is omitted, every host uses the normal vectors above
unchanged.

When it is supplied, the launch payload begins with:

```text
--- SOCRATIC LEARN SOURCE STUDY ---
```

followed by the exact `source_study_payload`, then the session control block.

- Pi: `pi --append-system-prompt PROTOCOL -- LAUNCH_PAYLOAD`
- Claude Code: `claude --append-system-prompt-file PROTOCOL -- LAUNCH_PAYLOAD`
- Antigravity: `agy --prompt-interactive BOOTSTRAP`

For Antigravity read mode, `BOOTSTRAP` is the JSON protocol-path prefix and
delimiter above followed by the full `LAUNCH_PAYLOAD` containing source study
and session control once. Pi and Claude still receive the canonical protocol
through their system-prompt file options.

Explicit `socratic read ...` goals and source inputs bypass the bare-launch
session chooser.