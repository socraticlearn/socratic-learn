'use strict';

function normalizeRunOutcome(outcome) {
  if (outcome && typeof outcome === 'object' && outcome.status === 'signaled') {
    return outcome;
  }
  const code = typeof outcome === 'number' ? outcome : outcome?.code;
  return { status: 'exited', code: code ?? 1 };
}

function finalizeProcessExit(outcome, deps = {}) {
  const normalized = normalizeRunOutcome(outcome);
  const exit = deps.exit ?? ((code) => process.exit(code));
  const kill = deps.kill ?? ((signal) => process.kill(process.pid, signal));

  if (normalized.status === 'signaled') {
    try {
      kill(normalized.signal);
    } catch {
      exit(1);
    }
    return;
  }
  exit(normalized.code);
}

module.exports = {
  finalizeProcessExit,
  normalizeRunOutcome,
};