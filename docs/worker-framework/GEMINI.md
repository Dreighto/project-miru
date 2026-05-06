# GEMINI.md — Universal Gemini CLI Rules

# Source: Dreighto/worker-framework

**Read AGENTS.md first.** This file adds Gemini CLI-specific rules on top of the
universal baseline. Project-specific GEMINI.md files layer on top of this. When rules
conflict, project-specific rules win for that project.

---

## Role

Gemini CLI is the **frontend and UI specialist** — the primary worker for HTML, CSS,
JavaScript, and design system implementation.

### What Gemini owns

- Standalone HTML/CSS/JS files, prototypes, and UI components
- Design system replication and application
- Frontend benchmarks, iteration, and polish
- Mobile-first layout and interaction patterns

### What Gemini does not own

- Backend Python services — those belong to CC
- Database schema or data changes
- Infrastructure and service configuration
- Python scripts or test harnesses

---

## Required Configuration (all Gemini dispatches)

### Environment variable

```bash
GEMINI_CLI_TRUST_WORKSPACE=true
```

Required — prevents the workspace trust interactive prompt in headless mode.
Set in the environment before invoking `gemini`. Do not use `--skip-trust` —
that flag was removed in v0.38.0.

### Flags

```bash
gemini -p "" --yolo
```

- `-p ""` — runs in non-interactive (headless) mode; actual prompt is read from stdin
- `--yolo` — auto-approves all tool actions without prompting

### Dispatch listener note (Windows)

When spawned from the dispatch listener on Windows: the listener must NOT use
`CREATE_NO_WINDOW` (Node.js `windowsHide: true`) for the Gemini process. Gemini's
startup sequence runs `conpty_console_list_agent.js` which calls `AttachConsole()`.
`CREATE_NO_WINDOW` prevents console allocation, causing immediate failure.

The dispatch listener's startup script hides its console window via `ShowWindow(hwnd, 0)`
but keeps the console allocated. Without `windowsHide`, Gemini inherits that hidden
console and starts successfully.

---

## UI Quality Standards — Hard Rules

### Tap targets (mobile-first)

Every interactive control (button, link, tab, toggle, chip) must have a minimum tap
target of **44 × 44 px**. This applies to the entire touch target — not just the
visible size of the element.

**Common failure pattern:** Tab switcher buttons, filter controls, and sort toggles
sized at 32–36px. Always set an explicit `min-height: 44px` on interactive controls
and verify in the browser before declaring done.

### Overflow check

At the target viewport width (375px for mobile-first), no element should cause
horizontal overflow. Verify with:

```css
* {
  outline: 1px solid red;
} /* temporary — remove before committing */
```

or by checking `document.documentElement.scrollWidth > window.innerWidth` in the
browser console.

### Design tokens

When working on a project with an established design system:

- Extract CSS custom properties (design tokens) from the existing source before
  writing any styles
- Use CSS variables throughout — no hardcoded hex values in rules (only in `:root`)
- Match the project's font stack, spacing scale, and color system exactly

---

## Self-Verification Before Declaring Done

Before emitting `STATUS: CONFIRMED WORKING` on any UI change:

1. Open the output in a browser at the target viewport width.
2. Check every interactive element meets the 44px tap target minimum.
3. Verify no horizontal overflow at the target width.
4. Confirm background colors, text colors, and font stacks match the project design
   system.
5. If the project has a UI verification harness, run it and fix any reported violations.

Do not claim CONFIRMED WORKING without completing these checks. "It looks right" is
not verification. "I opened it at 375px, all tap targets are ≥44px, no overflow, colors
match the design tokens" is verification.

---

## Headless Output Format

When producing standalone UI files:

- Single self-contained HTML/CSS/JS file unless the project structure requires otherwise
- Mobile-first at 375px — `max-width: 375px` with auto margins for centering on desktop
- No CDN imports, no external dependencies — everything inline
- CSS custom properties in `:root` block — defines the design system for the file
- All tap targets ≥ 44px (height or min-height)
- `<meta name="viewport" content="width=device-width, initial-scale=1.0">` present

---

## PM Storefront Design System (Project Miru only)

When building UI for the PM Storefront, use these design tokens:

```css
:root {
  --color-miru-bg: #0a0912;
  --color-miru-surface: #12101f;
  --color-miru-stroke: rgba(255, 255, 255, 0.07);
  --color-miru-stroke-brand: rgba(244, 208, 120, 0.25);
  --color-miru-text: rgba(255, 255, 255, 0.92);
  --color-miru-muted: rgba(255, 255, 255, 0.44);
  --color-miru-muted-2: rgba(255, 255, 255, 0.26);
  --color-miru-gold: #f4d078;
  --color-leader-red: #ce1126;
  --font-display: 'Inter', system-ui, sans-serif;
  --font-ui: 'Inter', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, monospace;
  --bottom-nav-height: 58px;
  --action-bar-height: 72px;
}
```

Read `docs/pm/00_PRINCIPLES.md` and `docs/pm/08_PM_ANTI_PATTERNS.md` before writing
any copy or making any design decisions for the PM Storefront.

---

## Completion Contract

End every response with exactly one of:

- `STATUS: CONFIRMED WORKING`
- `STATUS: INCONCLUSIVE`
- `STATUS: FAILED`

Plus a one-line summary of what was produced and any notable design decisions.

If INCONCLUSIVE: state what was tried, why it failed, and ask one specific question.
If FAILED: state what failed and what would be needed to fix it.
