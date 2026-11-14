'use strict';

const path = require('node:path');
const { execFile: defaultExecFile } = require('node:child_process');

function promisifyExecFile(execFileImpl) {
  return (file, args, options) =>
    new Promise((resolve, reject) => {
      execFileImpl(file, args, options, (error, stdout, stderr) => {
        if (error) {
          reject(error);
          return;
        }
        resolve({ stdout, stderr });
      });
    });
}

const MIN_PYTHON_VERSION = Object.freeze({ major: 3, minor: 11 });
const PYTHON_DOWNLOAD_URL = 'https://www.python.org/downloads/';
const VERSION_ARGS = ['--version'];

function interpreterCandidatesForPlatform(platform) {
  if (platform === 'win32') {
    return [
      { executable: 'py', args: ['-3'] },
      { executable: 'python3', args: [] },
      { executable: 'python', args: [] },
    ];
  }
  return [
    { executable: 'python3', args: [] },
    { executable: 'python', args: [] },
  ];
}

function parseVersionTuple(stdout, stderr = '') {
  const output = `${stdout ?? ''}\n${stderr ?? ''}`;
  const match = /Python\s+(\d+)\.(\d+)(?:\.(\d+))?/i.exec(output);
  if (!match) {
    return null;
  }
  const patch = match[3] === undefined ? 0 : Number(match[3]);
  return [Number(match[1]), Number(match[2]), patch];
}

function versionMeetsMinimum(version, minimum = MIN_PYTHON_VERSION) {
  if (!version) {
    return false;
  }
  if (version[0] !== minimum.major) {
    return version[0] > minimum.major;
  }
  return version[1] >= minimum.minor;
}

function buildPythonPath(vendorDir, existingPythonPath) {
  if (!existingPythonPath) {
    return vendorDir;
  }
  return `${vendorDir}${path.delimiter}${existingPythonPath}`;
}

function buildPythonEnv(baseEnv, vendorDir, existingPythonPath) {
  return {
    ...baseEnv,
    PYTHONPATH: buildPythonPath(vendorDir, existingPythonPath ?? baseEnv.PYTHONPATH),
  };
}

function formatVersion(version) {
  return `${version[0]}.${version[1]}.${version[2]}`;
}

function missingPythonError() {
  const error = new Error(
    `Python 3.11+ is required. Install a supported interpreter from ${PYTHON_DOWNLOAD_URL}`,
  );
  error.code = 'PYTHON_MISSING';
  return error;
}

function tooOldPythonError(versionTuple) {
  const version = formatVersion(versionTuple);
  const error = new Error(
    `Python 3.11+ is required, but found ${version}. Install a supported interpreter from ${PYTHON_DOWNLOAD_URL}`,
  );
  error.code = 'PYTHON_TOO_OLD';
  error.foundVersion = version;
  return error;
}

function createPythonRuntime(deps = {}) {
  const platform = deps.platform ?? process.platform;
  const env = deps.env ?? process.env;
  const execFile = promisifyExecFile(deps.execFile ?? defaultExecFile);

  async function probeCandidate(candidate) {
    try {
      const { stdout, stderr } = await execFile(
        candidate.executable,
        [...candidate.args, ...VERSION_ARGS],
        { windowsHide: true },
      );
      const version = parseVersionTuple(String(stdout), String(stderr));
      if (!version) {
        return { status: 'unusable', candidate };
      }
      if (!versionMeetsMinimum(version)) {
        return { status: 'too_old', candidate, version };
      }
      return { status: 'ok', candidate, version };
    } catch (error) {
      if (error && error.code === 'ENOENT') {
        return { status: 'missing', candidate };
      }
      return { status: 'unusable', candidate, error };
    }
  }

  async function resolveInterpreter() {
    if (env.SOCRATIC_PYTHON) {
      const candidate = { executable: env.SOCRATIC_PYTHON, args: [] };
      const probe = await probeCandidate(candidate);
      if (probe.status === 'missing') {
        throw missingPythonError();
      }
      if (probe.status === 'too_old') {
        throw tooOldPythonError(probe.version);
      }
      if (probe.status !== 'ok') {
        throw missingPythonError();
      }
      return {
        executable: candidate.executable,
        args: candidate.args,
        version: probe.version,
      };
    }

    const candidates = interpreterCandidatesForPlatform(platform);
    let sawTooOld = null;
    for (const candidate of candidates) {
      const probe = await probeCandidate(candidate);
      if (probe.status === 'ok') {
        return {
          executable: candidate.executable,
          args: candidate.args,
          version: probe.version,
        };
      }
      if (probe.status === 'too_old' && !sawTooOld) {
        sawTooOld = probe;
      }
    }
    if (sawTooOld) {
      throw tooOldPythonError(sawTooOld.version);
    }
    throw missingPythonError();
  }

  return {
    resolveInterpreter,
  };
}

module.exports = {
  MIN_PYTHON_VERSION,
  PYTHON_DOWNLOAD_URL,
  buildPythonEnv,
  buildPythonPath,
  createPythonRuntime,
  interpreterCandidatesForPlatform,
  parseVersionTuple,
  versionMeetsMinimum,
};