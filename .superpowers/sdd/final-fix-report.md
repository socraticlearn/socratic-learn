# Final fix report — package manager distribution

## Status

**DONE**

## Commit

`6a96060` — fix: close final review release and smoke gaps

## Scope

Addressed complete final-review findings in one focused fix wave:

- Windows registry/local npm smoke invokes batch launchers through `COMSPEC` with
  `cmd /d /c call …` so argv[0] is always a real `.exe`; tests assert the wrapper.
- Added `verify-artifact` three-platform matrix after `npm-package`, before
  `publish-github`, running `run_local_artifact_smoke` on downloaded wheel and
  tarball artifacts; `publish-github` depends only on `verify-artifact`.
- npm tarball smoke in `npm-package` enforces exact version output via
  `assert_exact_version_output`.
- Privacy sentinel path corrected to
  `learning/.socratic-learn/workspace` under launcher home.
- Native Python safety matrix runs `npm test --prefix packages/npm-cli`.
- npm launcher prints concise stderr when Python spawn fails after probing.
- Documented strict one-line `vendor-requirements.lock` format in npm README.
- Validator/workflow tests updated for order, artifact verification, and no
  checkout import in artifact smoke; skip generated vendor/untracked controller
  markdown from validation.

## Evidence

| Check | Result |
| --- | --- |
| Focused registry + release validator tests (48) | OK |
| `python tests/validate_repository.py` | OK |
| Local `run_local_artifact_smoke` (wheel + tarball, Windows) | OK |
| `npm test --prefix packages/npm-cli` | 28 pass |
| Full Python suite | 1135 tests, 0 failures, 28 skipped |

## External

**PENDING** — live PyPI/npm publication and post-publish `verify-registry` matrix
remain conditional per public docs until first tagged release and registry setup.