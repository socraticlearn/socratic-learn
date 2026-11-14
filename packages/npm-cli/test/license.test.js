'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { describe, it } = require('node:test');

describe('package license', () => {
  it('matches the repository MIT license', () => {
    const repoRoot = path.resolve(__dirname, '..', '..', '..');
    const repoLicense = fs.readFileSync(path.join(repoRoot, 'LICENSE'), 'utf8');
    const packageLicense = fs.readFileSync(
      path.join(__dirname, '..', 'LICENSE'),
      'utf8',
    );
    assert.equal(packageLicense, repoLicense);
  });
});