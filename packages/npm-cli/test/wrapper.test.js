'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { describe, it } = require('node:test');
const { EventEmitter } = require('node:events');

const { finalizeProcessExit } = require('../lib/exit-status.js');
const { createLauncher } = require('../lib/launcher.js');

const binSource = fs.readFileSync(
  path.join(__dirname, '..', 'bin', 'socratic.js'),
  'utf8',
);

describe('finalizeProcessExit', () => {
  it('calls exit only with numeric child codes', () => {
    const calls = [];
    finalizeProcessExit({ status: 'exited', code: 17 }, {
      exit: (code) => calls.push({ type: 'exit', code }),
      kill: () => calls.push({ type: 'kill' }),
    });
    assert.deepEqual(calls, [{ type: 'exit', code: 17 }]);
  });

  it('does not translate signaled children into exit code 1', () => {
    const calls = [];
    finalizeProcessExit({ status: 'signaled', signal: 'SIGTERM' }, {
      exit: (code) => calls.push({ type: 'exit', code }),
      kill: (signal) => calls.push({ type: 'kill', signal }),
    });
    assert.deepEqual(calls, [{ type: 'kill', signal: 'SIGTERM' }]);
  });
});

describe('bin/socratic.js wrapper contract', () => {
  it('delegates completion to finalizeProcessExit', () => {
    assert.match(binSource, /finalizeProcessExit/);
    assert.doesNotMatch(binSource, /process\.exit\(code \?\? 1\)/);
  });

  it('preserves signaled exits through launcher and finalizeProcessExit', async () => {
    const child = new EventEmitter();
    const launcher = createLauncher({
      packageRoot: path.join(__dirname, '..'),
      resolveInterpreter: async () => ({
        executable: 'python3.11',
        args: [],
        version: [3, 11, 0],
      }),
      spawn: () => {
        queueMicrotask(() => child.emit('exit', null, 'SIGINT'));
        return child;
      },
      registerSignalHandlers: () => () => {},
    });

    const calls = [];
    const outcome = await launcher.run([]);
    finalizeProcessExit(outcome, {
      exit: (code) => calls.push({ type: 'exit', code }),
      kill: (signal) => calls.push({ type: 'kill', signal }),
    });
    assert.deepEqual(calls, [{ type: 'kill', signal: 'SIGINT' }]);
  });
});