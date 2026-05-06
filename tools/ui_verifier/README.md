# UI Verification Harness

Headless Playwright tool for verifying frontend worker HTML output.

## Usage

```
node tools/ui_verifier/verify_ui.js <path-to-html> [--tap-min=44]
```

Exits 0 always. Prints a single JSON object to stdout.

## Checks

| Check         | What it looks for                                                        |
| ------------- | ------------------------------------------------------------------------ |
| `render`      | Page loads without uncaught JS errors                                    |
| `overflow`    | No element bleeds past its hidden-overflow container                     |
| `tap_targets` | All interactive elements (button, a, input, select) >= 44px on both axes |
| `screenshot`  | Saved next to the HTML file as `<name>.screenshot.png`                   |

## Output schema

```json
{
  "file": "relative/path/to/page.html",
  "url": "http://127.0.0.1:<port>/page.html",
  "render": { "ok": true, "js_errors": [] },
  "overflow": { "violations_count": 0, "violations": [] },
  "tap_targets": {
    "min_px": 44,
    "violations_count": 2,
    "violations": [
      {
        "tag": "button",
        "role": null,
        "label": "Filter",
        "width": 36,
        "height": 36,
        "tooNarrow": true,
        "tooShort": true
      }
    ]
  },
  "screenshot": "relative/path/to/page.screenshot.png"
}
```

## Self-tests

```
npx playwright test tests/test_ui_harness.spec.js
```

## Benchmark files

Three reference files live in `data/batch_reports/gemini_bench/`:

| File                  | Expected tap violations                   |
| --------------------- | ----------------------------------------- |
| `l1_card_detail.html` | 0                                         |
| `l2_watchlist.html`   | >= 2 (filter tabs 36px, sort toggle 36px) |
| `l3_card_page.html`   | >= 1 (tab buttons 32px)                   |
