'use strict';

const assert = require('node:assert/strict');
const { describe, it } = require('node:test');
const path = require('node:path');

const {
  MIN_PYTHON_VERSION,
  PYTHON_DOWNLOAD_URL,
  buildPythonEnv,
  buildPythonPath,
  createPythonRuntime,
  describeProbeFailure,
  parseVersionTuple,
  versionMeetsMinimum,
} = require('../lib/python-runtime.js');

describe('parseVersionTuple', () => {
  it('parses CPython version lines', () => {
    assert.deepEqual(parseVersionTuple('Python 3.11.4'), [3, 11, 4]);
    assert.deepEqual(parseVersionTuple('Python 3.12.0'), [3, 12, 0]);
  });

  it('rejects malformed output', () => {
    assert.equal(parseVersionTuple('not python'), null);
  });
});

describe('versionMeetsMinimum', () => {
  it('accepts 3.11 and newer', () => {
    assert.equal(versionMeetsMinimum([3, 11, 0], MIN_PYTHON_VERSION), true);
    assert.equal(versionMeetsMinimum([3, 12, 1], MIN_PYTHON_VERSION), true);
  });

  it('rejects older interpreters', () => {
    assert.equal(versionMeetsMinimum([3, 10, 9], MIN_PYTHON_VERSION), false);
    assert.equal(versionMeetsMinimum([2, 7, 18], MIN_PYTHON_VERSION), false);
  });
});

describe('buildPythonPath', () => {
  it('prepends vendor and preserves existing PYTHONPATH', () => {
    const vendor = path.join('/pkg', 'vendor');
  const joined = buildPythonPath(vendor, '/existing/lib');
    assert.equal(joined, `${vendor}${path.delimiter}/existing/lib`);
  });

  it('uses only vendor when PYTHONPATH is unset', () => {
    const vendor = '/pkg/vendor';
    assert.equal(buildPythonPath(vendor, undefined), vendor);
  });
});

describe('buildPythonEnv', () => {
  it('merges vendor ahead of existing environment', () => {
    const env = buildPythonEnv({ HOME: '/home' }, '/pkg/vendor', '/old');
    assert.equal(env.HOME, '/home');
    assert.equal(
      env.PYTHONPATH,
      `/pkg/vendor${path.delimiter}/old`,
    );
  });
});

describe('describeProbeFailure', () => {
  it('distinguishes missing commands from too-old interpreters', () => {
    assert.equal(describeProbeFailure({ status: 'missing' }), 'missing');
    assert.equal(describeProbeFailure({ status: 'too_old', version: '3.10.0' }), 'too_old');
    assert.equal(describeProbeFailure({ status: 'ok' }), 'ok');
  });
});

describe('createPythonRuntime', () => {
  it('uses SOCRATIC_PYTHON when it satisfies the minimum version', async () => {
    const calls = [];
    const runtime = createPythonRuntime({
      platform: 'linux',
      env: { SOCRATIC_PYTHON: '/custom/python3.11' },
      execFile: (file, args, opts, cb) => {
        calls.push({ file, args });
        cb(null, 'Python 3.11.2\n', '');
      },
    });

    const result = await runtime.resolveInterpreter();
    assert.deepEqual(result, {
      executable: '/custom/python3.11',
      args: [],
      version: [3, 11, 2],
    });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].file, '/custom/python3.11');
  });

  it('rejects SOCRATIC_PYTHON when the interpreter is too old', async () => {
    const runtime = createPythonRuntime({
      platform: 'linux',
      env: { SOCRATIC_PYTHON: '/usr/bin/python3.10' },
      execFile: (_file, _args, _opts, cb) => {
        cb(null, 'Python 3.10.12\n', '');
      },
    });

    await assert.rejects(
      () => runtime.resolveInterpreter(),
      (error) => {
        assert.match(error.message, /Python 3\.11\+/);
        assert.match(error.message, /python\.org\/downloads/);
        assert.equal(error.code, 'PYTHON_TOO_OLD');
        assert.equal(error.foundVersion, '3.10.12');
        return true;
      },
    );
  });

  it('treats ENOENT on override as missing executable', async () => {
    const runtime = createPythonRuntime({
      platform: 'linux',
      env: { SOCRATIC_PYTHON: '/missing/python' },
      execFile: (_file, _args, _opts, cb) => {
        const error = new Error('spawn ENOENT');
        error.code = 'ENOENT';
        cb(error);
      },
    });

    await assert.rejects(
      () => runtime.resolveInterpreter(),
      (error) => {
        assert.equal(error.code, 'PYTHON_MISSING');
        return true;
      },
    );
  });

  it('probes platform candidates when no override is set', async () => {
    const calls = [];
    const runtime = createPythonRuntime({
      platform: 'win32',
      env: {},
      execFile: (file, args, _opts, cb) => {
        calls.push({ file, args });
        if (file === 'py' && args[0] === '-3.11') {
          cb(null, 'Python 3.11.9\n', '');
          return;
        }
        const error = new Error('spawn ENOENT');
        error.code = 'ENOENT';
        cb(error);
      },
    });

    const result = await runtime.resolveInterpreter();
    assert.equal(result.executable, 'py');
    assert.deepEqual(result.args, ['-3.11']);
    assert.ok(calls.length >= 1);
  });

  it('skips missing candidates and rejects a present but old interpreter', async () => {
    const runtime = createPythonRuntime({
      platform: 'linux',
      env: {},
      execFile: (file, _args, _opts, cb) => {
        if (file === 'python3') {
          cb(null, 'Python 3.10.4\n', '');
          return;
        }
        const error = new Error('spawn ENOENT');
        error.code = 'ENOENT';
        cb(error);
      },
    });

    await assert.rejects(
      () => runtime.resolveInterpreter(),
      (error) => {
        assert.equal(error.code, 'PYTHON_TOO_OLD');
        assert.match(error.message, /3\.10\.4/);
        return true;
      },
    );
  });

  it('prints guidance when no suitable interpreter exists', async () => {
    const runtime = createPythonRuntime({
      platform: 'linux',
      env: {},
      execFile: (_file, _args, _opts, cb) => {
        const error = new Error('spawn ENOENT');
        error.code = 'ENOENT';
        cb(error);
      },
    });

    await assert.rejects(
      () => runtime.resolveInterpreter(),
      (error) => {
        assert.equal(error.code, 'PYTHON_MISSING');
        assert.match(error.message, /Python 3\.11\+/);
        assert.match(error.message, /python\.org\/downloads/);
        return true;
      },
    );
  });
});