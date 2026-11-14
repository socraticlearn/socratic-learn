# Registry trusted publishing (maintainers)

`@socraticlearn/cli` and `socratic-learn` on PyPI are **not** public until the
first successful tag release completes after these external steps.

## PyPI

- Enable PyPI trusted publishing for repository `socraticlearn/socratic-learn`,
  workflow file `.github/workflows/release.yml`, environment **`pypi`**, and
  the protected **`pypi`** environment in GitHub.
- The `publish-pypi` job depends on `publish-github`, downloads artifact
  `python-package`, and uses `pypa/gh-action-pypi-publish@release/v1` with
  `id-token: write` (no API tokens).

## npm

- Configure npm trusted publishers for organization **`socraticlearn`**,
  package **`@socraticlearn/cli`**, GitHub repository **`socratic-learn`**,
  workflow **`release.yml`**, and GitHub environment **`npm`**.
- The `publish-npm` job depends on `publish-github`, downloads artifact
  `npm-package`, runs on Node **24** with **npm >= 11.5.1**, registry
  `https://registry.npmjs.org`, and `npm publish … --access public` (no
  `NODE_AUTH_TOKEN` or `NPM_TOKEN`).

## First publication checkpoint

1. Create a matching `v*` tag and let `release.yml` finish `publish-github`.
2. Confirm the GitHub Release assets include the wheel, sdist, and npm tarball.
3. Retry only failed registry jobs with **Re-run failed jobs**; do not republish
   an existing version.