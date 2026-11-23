'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { describe, it } = require('node:test');

describe('vendor-requirements.lock', () => {
  it('lists only hash-locked pure-python dependencies compatible with pyproject', () => {
    const repoRoot = path.resolve(__dirname, '..', '..', '..');
    const lock = fs.readFileSync(
      path.join(__dirname, '..', 'vendor-requirements.lock'),
      'utf8',
    );
    const pyproject = fs.readFileSync(path.join(repoRoot, 'pyproject.toml'), 'utf8');

    assert.match(
      lock,
      /^packaging==26\.1 \\\s*--hash=sha256:5d9c0669c6285e491e0ced2eee587eaf67b670d94a19e94e3984a481aba6802f$/m,
    );
    assert.match(pyproject, /"packaging>=26\.1,<27"/);
    assert.match(lock, /^pypdf==7\.\d+\.\d+ \\\s*--hash=sha256:[a-f0-9]{64}$/m);
    assert.doesNotMatch(lock, /win_amd64|manylinux|macosx/i);
    assert.match(pyproject, /"pypdf>=7\.0,<8"/);
    const pinned = lock.match(/^pypdf==(7\.\d+\.\d+)/m)[1];
    const [major, minor] = pinned.split('.').map(Number);
    assert.ok(major === 7 && minor >= 0);
  });
});