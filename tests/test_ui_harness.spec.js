/**
 * Self-test for the UI verification harness.
 *
 * Creates known-good and known-bad HTML fixtures in tests/_tmp/ and asserts
 * that verify_ui.js produces the expected tap-target violation counts.
 */

const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const TMP = path.resolve(__dirname, '_tmp');
const VERIFIER = path.resolve(__dirname, '..', 'tools', 'ui_verifier', 'verify_ui.js');

function runVerifier(htmlPath) {
  const out = execSync(`node "${VERIFIER}" "${htmlPath}"`, {
    encoding: 'utf8',
    timeout: 30000,
  });
  return JSON.parse(out);
}

test.beforeAll(() => {
  fs.mkdirSync(TMP, { recursive: true });
});

test('known-good fixture -- 0 tap-target violations', () => {
  const html = `<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>button{width:44px;height:44px;cursor:pointer;}</style>
</head><body>
<button>OK</button>
</body></html>`;
  const p = path.join(TMP, 'good_fixture.html');
  fs.writeFileSync(p, html);

  const result = runVerifier(p);
  expect(result.render.ok).toBe(true);
  expect(result.tap_targets.violations_count).toBe(0);
});

test('known-bad fixture -- detects undersized button', () => {
  const html = `<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>
  .small { width: 30px; height: 30px; cursor: pointer; border: none; background: red; }
</style>
</head><body>
<button class="small" aria-label="tiny button">X</button>
</body></html>`;
  const p = path.join(TMP, 'bad_fixture.html');
  fs.writeFileSync(p, html);

  const result = runVerifier(p);
  expect(result.render.ok).toBe(true);
  expect(result.tap_targets.violations_count).toBeGreaterThanOrEqual(1);

  const v = result.tap_targets.violations[0];
  expect(v.tag).toBe('button');
  expect(v.tooNarrow).toBe(true);
  expect(v.tooShort).toBe(true);
  expect(v.width).toBeLessThan(44);
  expect(v.height).toBeLessThan(44);
});

test('screenshot file is created', () => {
  const html = `<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
</head><body><p>Screenshot test</p></body></html>`;
  const p = path.join(TMP, 'screenshot_fixture.html');
  fs.writeFileSync(p, html);

  const result = runVerifier(p);
  const screenshotAbs = path.resolve(result.screenshot);
  expect(fs.existsSync(screenshotAbs)).toBe(true);
  // PNG magic bytes: 89 50 4E 47
  const buf = fs.readFileSync(screenshotAbs);
  expect(buf[0]).toBe(0x89);
  expect(buf[1]).toBe(0x50);
});

test('render check catches JS error', () => {
  const html = `<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
</head><body>
<script>throw new Error('intentional test error');</script>
</body></html>`;
  const p = path.join(TMP, 'jserror_fixture.html');
  fs.writeFileSync(p, html);

  const result = runVerifier(p);
  expect(result.render.ok).toBe(false);
  expect(result.render.js_errors.length).toBeGreaterThanOrEqual(1);
});
