#!/usr/bin/env node
/**
 * verify_ui.js -- UI verification harness for frontend worker output.
 *
 * Usage:
 *   node tools/ui_verifier/verify_ui.js <path-to-html-file> [--tap-min=44] [--port=0]
 *
 * Exits 0 always (informational tool). Output is a single JSON object.
 *
 * Checks:
 *   1. render  -- page loads without uncaught JS errors
 *   2. overflow -- no element overflows its nearest scrollable ancestor
 *   3. tap_targets -- interactive elements (button, a, input, select) >= tap_min px in both dimensions
 *   4. screenshot -- saved next to the HTML file as <name>.screenshot.png
 */

'use strict';

const path = require('path');
const fs = require('fs');
const http = require('http');
const { chromium } = require('@playwright/test');

const TAP_MIN_DEFAULT = 44;

function parseArgs(argv) {
  const args = argv.slice(2);
  let htmlPath = null;
  let tapMin = TAP_MIN_DEFAULT;
  let port = 0;

  for (const arg of args) {
    if (arg.startsWith('--tap-min=')) {
      tapMin = parseInt(arg.split('=')[1], 10);
    } else if (arg.startsWith('--port=')) {
      port = parseInt(arg.split('=')[1], 10);
    } else if (!arg.startsWith('--')) {
      htmlPath = arg;
    }
  }
  return { htmlPath, tapMin, port };
}

function serveDirectory(dir, preferredPort) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const relPath = decodeURIComponent(req.url.split('?')[0]);
      const filePath = path.join(dir, relPath === '/' ? 'index.html' : relPath);
      fs.readFile(filePath, (err, data) => {
        if (err) {
          res.writeHead(404);
          res.end('Not found');
          return;
        }
        const ext = path.extname(filePath).toLowerCase();
        const mime =
          {
            '.html': 'text/html',
            '.css': 'text/css',
            '.js': 'application/javascript',
            '.json': 'application/json',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.svg': 'image/svg+xml',
            '.woff2': 'font/woff2',
          }[ext] || 'application/octet-stream';
        res.writeHead(200, { 'Content-Type': mime });
        res.end(data);
      });
    });
    server.listen(preferredPort, '127.0.0.1', () => {
      resolve(server);
    });
    server.on('error', reject);
  });
}

async function runChecks({ htmlPath, tapMin }) {
  const absPath = path.resolve(htmlPath);
  if (!fs.existsSync(absPath)) {
    return { error: `File not found: ${absPath}` };
  }

  const dir = path.dirname(absPath);
  const fileName = path.basename(absPath);
  const screenshotPath = absPath.replace(/\.html$/i, '.screenshot.png');

  const server = await serveDirectory(dir, 0);
  const address = server.address();
  const url = `http://127.0.0.1:${address.port}/${encodeURIComponent(fileName)}`;

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 375, height: 812 },
    deviceScaleFactor: 2,
  });
  const page = await context.newPage();

  const jsErrors = [];
  page.on('pageerror', (err) => jsErrors.push(err.message));

  try {
    await page.goto(url, { waitUntil: 'networkidle', timeout: 15000 });
  } catch (e) {
    await browser.close();
    server.close();
    return { error: `Navigation failed: ${e.message}` };
  }

  // 1. Render check
  const renderOk = jsErrors.length === 0;

  // 2. Overflow check
  const overflowViolations = await page.evaluate(() => {
    const violations = [];
    const all = document.querySelectorAll('*');
    for (const el of all) {
      const rect = el.getBoundingClientRect();
      const parentRect = el.parentElement ? el.parentElement.getBoundingClientRect() : null;
      if (!parentRect) continue;
      const style = window.getComputedStyle(el.parentElement);
      const overflowX = style.overflowX;
      // Only flag visible overflow on containers that don't scroll
      if (
        (overflowX === 'hidden' || overflowX === 'visible') &&
        rect.right > parentRect.right + 2
      ) {
        violations.push({
          tag: el.tagName.toLowerCase(),
          text: (el.textContent || '').trim().slice(0, 40),
          overflowRight: Math.round(rect.right - parentRect.right),
        });
      }
    }
    return violations.slice(0, 20);
  });

  // 3. Tap target check
  const tapViolations = await page.evaluate((minPx) => {
    const selectors = 'button, a[href], input, select, textarea, [role="button"], [tabindex]';
    const elements = document.querySelectorAll(selectors);
    const violations = [];
    for (const el of elements) {
      const rect = el.getBoundingClientRect();
      // Skip hidden elements
      if (rect.width === 0 && rect.height === 0) continue;
      if (rect.width < minPx || rect.height < minPx) {
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        violations.push({
          tag: el.tagName.toLowerCase(),
          role: el.getAttribute('role') || null,
          label: el.getAttribute('aria-label') || el.textContent.trim().slice(0, 40) || null,
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          tooNarrow: rect.width < minPx,
          tooShort: rect.height < minPx,
        });
      }
    }
    return violations;
  }, tapMin);

  // 4. Screenshot
  await page.screenshot({ path: screenshotPath, fullPage: false });

  await browser.close();
  server.close();

  return {
    file: path.relative(process.cwd(), absPath),
    url,
    render: {
      ok: renderOk,
      js_errors: jsErrors,
    },
    overflow: {
      violations_count: overflowViolations.length,
      violations: overflowViolations,
    },
    tap_targets: {
      min_px: tapMin,
      violations_count: tapViolations.length,
      violations: tapViolations,
    },
    screenshot: path.relative(process.cwd(), screenshotPath),
  };
}

async function main() {
  const { htmlPath, tapMin } = parseArgs(process.argv);

  if (!htmlPath) {
    const usage = {
      error: 'Usage: node tools/ui_verifier/verify_ui.js <path-to-html> [--tap-min=44]',
    };
    process.stdout.write(JSON.stringify(usage, null, 2) + '\n');
    process.exit(0);
  }

  const result = await runChecks({ htmlPath, tapMin });
  process.stdout.write(JSON.stringify(result, null, 2) + '\n');
  process.exit(0);
}

main().catch((err) => {
  process.stdout.write(JSON.stringify({ error: err.message, stack: err.stack }) + '\n');
  process.exit(0);
});
