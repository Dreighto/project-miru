'use strict';

function ts() {
  return new Date().toISOString();
}

function emit(level, msg, fields) {
  const row = { ts: ts(), level, msg, ...(fields || {}) };
  const line = JSON.stringify(row);
  if (level === 'error' || level === 'fatal') {
    process.stderr.write(line + '\n');
  } else {
    process.stdout.write(line + '\n');
  }
}

module.exports = {
  info: (msg, fields) => emit('info', msg, fields),
  warn: (msg, fields) => emit('warn', msg, fields),
  error: (msg, fields) => emit('error', msg, fields),
  fatal: (msg, fields) => emit('fatal', msg, fields),
};
