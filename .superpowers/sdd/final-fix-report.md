# Final Whole-Branch Fix Report

## Outcome

All final review findings were addressed without changing the external plan or
IOI contracts. The fixes preserve the structured session-control schema,
cross-host resume data, immutable encounter records, source-study boundaries,
legacy em-dash reads, and the documented same-user filesystem threat model.

## Fixes

- Added PowerShell 5.1-compatible legacy native argument escaping only for the
  Windows batch-shim path. A real `powershell.exe` script now forwards `$args`
  to a real `python.exe` receiver and verifies the complete payload arrives as
  one byte-identical UTF-8 argument. Shell metacharacters remain data, the batch
  file is not executed, and no raw payload is persisted.
- Marked threads incomplete immediately before child launch. Later menu
  discovery recovers valid orphan checkpoints, discards empty or malformed
  regular pending files, and leaves unsafe pending symlinks untouched with a
  sanitized warning.
- Made menu discovery tolerant of corrupt thread and legacy entries while
  retaining strict fail-closed load and import APIs. One warning reports only
  sanitized counts.
- Added atomic legacy import provenance markers. Repeated imports reuse the
  existing thread, imported source files stay in place and disappear from the
  chooser, and legacy records without a refined subject/focus title remain
  complete prior evidence under their provisional title.
- Reworked the interactive chooser to display the complete current choice set,
  keep the selected row visible, show an arrow/Enter hint, truncate to terminal
  display width, and redraw safely. POSIX raw mode now spans the whole menu and
  restores on every exit without per-key input flushing.
- Made numbered built-in input print its prompt once and made chooser EOF or
  Ctrl+C return code 130 through the console CLI without a traceback.
- Parsed mixed timestamps before stable newest-first ordering and converted menu
  dates to local time.
- Ignored HTML comments when checking section emptiness and rejected the literal
  `Subject: Specific focus` placeholder.
- Removed the obsolete `command_for` payload replacement parameter and the
  misleading read-mode delimiter alias.
- Delayed source-thread creation until ingest succeeds and safely removes a new
  thread when payload or argv construction fails before launch.
- Aligned the documented Antigravity JSON spacing with actual output.

## TDD and verification

Each behavior was introduced with a focused failing regression before the
production change, then rerun green. Final verification:

- Focused affected suites: **533 tests passed**, **18 platform/capability skips**.
- Repository validator: passed.
- `compileall` over `socratic_learn`, `evaluation`, and `tests`: passed.
- Deterministic build and `dist/` artifact drift check: passed.
- `git diff --check`: passed.
- Full suite: intentionally not run, per request.

## Fix commits

- `57fc611` — launcher payload, lifecycle, recovery, discovery, legacy import,
  parser, ordering, documentation, and integration regressions.
- `0022646` — terminal chooser, raw-mode lifetime, prompt, and clean CLI exits.

## Remaining concerns

- The unsafe-pending-symlink regression is present but skipped on this Windows
  host because unprivileged symlink creation is unavailable; supported
  environments execute it.
- No external provider session was launched; host behavior was verified through
  the real Windows PowerShell/native boundary and isolated fake-host installed
  wheel tests.
