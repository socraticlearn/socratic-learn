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
      return 1;
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
          } else {
            process.kill(process.pid, signal);
          }
          resolve(null);
          return;
        }
        resolve(code ?? 1);
      });
      child.on('error', () => {
        cleanup();
        resolve(1);
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