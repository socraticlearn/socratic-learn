'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const { describe, it } = require('node:test');
const { EventEmitter } = require('node:events');

const { createLauncher } = require('../lib/launcher.js');

function fakeChild() {
  const child = new EventEmitter();
  child.stdin = null;
  child.stdout = null;
  child.stderr = null;
  child.kill = () => {};
  child.pid = 4242;
  return child;
}

describe('createLauncher', () => {
  it('forwards user arguments without shell interpolation', async () => {
    const child = fakeChild();
    let spawnArgs;
    const launcher = createLauncher({
      packageRoot: '/pkg',
      resolveInterpreter: async () => ({
        executable: '/usr/bin/python3.11',
        args: [],
        version: [3, 11, 0],
      }),
      spawn: (_exe, args, _opts) => {
        spawnArgs = args;
        queueMicrotask(() => child.emit('exit', 0, null));
        return child;
      },
    });

    const outcome = await launcher.run(['--version', 'learn', 'math']);
    assert.deepEqual(outcome, { status: 'exited', code: 0 });
    const codeIndex = spawnArgs.indexOf('-c');
    assert.ok(codeIndex >= 0);
    assert.match(spawnArgs[codeIndex + 1], /entry_point\(\)/);
    assert.deepEqual(spawnArgs.slice(codeIndex + 2), ['--version', 'learn', 'math']);
  });

  it('prepends vendor to PYTHONPATH and inherits stdio', async () => {
    const child = fakeChild();
    let spawnOpts;
    const pkgRoot = path.join(path.sep, 'pkg');
    const launcher = createLauncher({
      packageRoot: pkgRoot,
      env: { PYTHONPATH: '/existing', USER: 'tester' },
      resolveInterpreter: async () => ({
        executable: 'python3.11',
        args: [],
        version: [3, 11, 0],
      }),
      spawn: (_exe, _args, opts) => {
        spawnOpts = opts;
        queueMicrotask(() => child.emit('exit', 0, null));
        return child;
      },
    });

    await launcher.run([]);
    assert.equal(spawnOpts.stdio, 'inherit');
    const vendorPath = path.join(pkgRoot, 'vendor');
    assert.equal(
      spawnOpts.env.PYTHONPATH,
      `${vendorPath}${path.delimiter}/existing`,
    );
    assert.equal(spawnOpts.env.USER, 'tester');
    assert.equal(spawnOpts.env.SOCRATIC_DISTRIBUTION_CHANNEL, 'npm');
    assert.equal(spawnOpts.windowsHide, true);
    assert.notEqual(spawnOpts.shell, true);
  });

  it('propagates child exit codes', async () => {
    const child = fakeChild();
    const launcher = createLauncher({
      packageRoot: '/pkg',
      resolveInterpreter: async () => ({
        executable: 'python3.11',
        args: [],
        version: [3, 11, 0],
      }),
      spawn: () => {
        queueMicrotask(() => child.emit('exit', 17, null));
        return child;
      },
    });

    const outcome = await launcher.run(['doctor']);
    assert.deepEqual(outcome, { status: 'exited', code: 17 });
  });

  it('forwards termination signals without masking them', async () => {
    const child = fakeChild();
    const forwarded = [];
    const launcher = createLauncher({
      packageRoot: '/pkg',
      resolveInterpreter: async () => ({
        executable: 'python3.11',
        args: [],
        version: [3, 11, 0],
      }),
      spawn: () => {
        queueMicrotask(() => child.emit('exit', null, 'SIGTERM'));
        return child;
      },
      forwardSignal: (signal) => forwarded.push(signal),
    });

    const outcome = await launcher.run([]);
    assert.deepEqual(outcome, { status: 'signaled', signal: 'SIGTERM' });
    assert.deepEqual(forwarded, ['SIGTERM']);
  });

  it('prints spawn guidance when interpreter probing succeeded', async () => {
    const child = fakeChild();
    const writes = [];
    const launcher = createLauncher({
      packageRoot: '/pkg',
      resolveInterpreter: async () => ({
        executable: '/usr/bin/python3.11',
        args: [],
        version: [3, 11, 8],
      }),
      spawn: () => {
        queueMicrotask(() => child.emit('error', new Error('spawn EACCES')));
        return child;
      },
      writeStderr: (chunk) => writes.push(chunk),
    });

    const outcome = await launcher.run(['--version']);
    assert.deepEqual(outcome, { status: 'exited', code: 1 });
    assert.match(writes.join(''), /Failed to start \/usr\/bin\/python3\.11/);
    assert.match(writes.join(''), /spawn EACCES/);
  });

  it('exits nonzero with guidance when Python is missing', async () => {
    const writes = [];
    const launcher = createLauncher({
      packageRoot: '/pkg',
      resolveInterpreter: async () => {
        const error = new Error('Python 3.11+ is required. Install from https://www.python.org/downloads/');
        error.code = 'PYTHON_MISSING';
        throw error;
      },
      writeStderr: (chunk) => writes.push(chunk),
    });

    const outcome = await launcher.run(['--version']);
    assert.deepEqual(outcome, { status: 'exited', code: 1 });
    assert.match(writes.join(''), /Python 3\.11\+/);
  });
});