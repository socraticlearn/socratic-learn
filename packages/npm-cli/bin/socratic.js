#!/usr/bin/env node
'use strict';

const path = require('node:path');
const { createLauncher } = require('../lib/launcher.js');
const { finalizeProcessExit } = require('../lib/exit-status.js');

const packageRoot = path.resolve(__dirname, '..');
const launcher = createLauncher({ packageRoot });

launcher.run(process.argv.slice(2)).then((outcome) => {
  finalizeProcessExit(outcome);
});