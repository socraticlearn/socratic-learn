'use strict';

const path = require('node:path');
const { spawn: defaultSpawn } = require('node:child_process');
const { createPythonRuntime, buildPythonEnv } = require('./python-runtime.js');

const ENTRYPOINT_CODE = 'from socratic_learn.cli import entry_point; entry_point()';

function createLauncher(deps = {}) {
  const packageRoot = deps.packageRoot;
  const env = deps.env ?? process.env;
  const spawn = deps.spawn ?? defaultSpawn;
  const writeStderr = deps.writeStderr ?? ((chunk) => process.stderr.write(chunk));
  const registerSignalHandlers = deps.registerSignalHandlers ?? _defaultSignalHandlers;
  const forwardSignal = deps.forwardSignal;

  const runtime =
    deps.runtime ??
    createPythonRuntime({
      platform: deps.platform ?? process.platform,
      env,
      execFile: deps.execFile,
    });

  const resolveInterpreter = deps.resolveInterpreter ?? (() => runtime.resolveInterpreter());

  async function run(userArgs) {
    const vendorDir = path.join(packageRoot, 'vendor');
    let interpreter;
    try {
      interpreter = await resolveInterpreter();
    } catch (error) {
      writeStderr(`${error.message}\n`);
      return { status: 'exited', code: 1 };
    }

    const pythonArgs = [
      ...interpreter.args,
      '-c',
      ENTRYPOINT_CODE,
      ...userArgs,
    ];

    const child = spawn(interpreter.executable, pythonArgs, {
      env: buildPythonEnv(env, vendorDir, env.PYTHONPATH),
      stdio: 'inherit',
      windowsHide: true,
      shell: false,
    });

    const cleanup = registerSignalHandlers(child, { forwardSignal });

    return await new Promise((resolve) => {
      child.on('exit', (code, signal) => {
        cleanup();
        if (signal) {
          if (forwardSignal) {
            forwardSignal(signal);
          }
          resolve({ status: 'signaled', signal });
          return;
        }
        resolve({ status: 'exited', code: code ?? 1 });
      });
      child.on('error', (error) => {
        cleanup();
        const detail = error && error.message ? error.message : 'unknown error';
        writeStderr(
          `Failed to start ${interpreter.executable} after selecting Python ${interpreter.version.join('.')}: ${detail}\n`,
        );
        resolve({ status: 'exited', code: 1 });
      });
    });
  }

  return { run };
}

function _defaultSignalHandlers(child, { forwardSignal }) {
  const relay = (signal) => {
    try {
      child.kill(signal);
    } catch {
      // Ignore invalid signal delivery attempts on unsupported platforms.
    }
    if (forwardSignal) {
      forwardSignal(signal);
    }
  };

  const signals = ['SIGINT', 'SIGTERM', 'SIGHUP'];
  for (const signal of signals) {
    process.on(signal, relay);
  }

  return () => {
    for (const signal of signals) {
      process.off(signal, relay);
    }
  };
}

module.exports = {
  ENTRYPOINT_CODE,
  createLauncher,
};