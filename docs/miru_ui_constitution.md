# Project Miru UI Constitution

This document is the authoritative blueprint for all UI work on Project Miru. Implementation agents must read and follow this document before touching any template or CSS file. When this document conflicts with existing code, the document wins — the existing code is the thing to migrate.

---

## 0. Current State (Know Before You Touch)

| Item | Current state |
|---|---|
| CSS | One file: `tools/static/miru_ai.css` (~5,700 lines, ~120 KB) |
| Templates | Three standalone HTML files, no Jinja2 inheritance |
| Token coverage | Colors, radii, shadows only — no spacing, z-index, motion tokens |
| Naming | camelCase components, `--` modifiers, `__` children (BEM-hybrid) — mostly consistent, one outlier: `miru_insight_card` |
| Page scoping | Works via `.pageBody--{key}` — solid foundation, not fully enforced |
| Template duplication | `<head>` boilerplate and `<nav class="topNav">` repeated verbatim in every template |

Pass A fixes the structural problems (token gaps, template inheritance, CSS layer ordering). Pass B migrates and standardizes. Neither pass changes the visual design.

---

## 1. CSS Architecture

### Rule: One Foundation File, One Page File per Page

The site uses **two CSS files per page load**:

1. `tools/static/miru_ai.css` — foundation (tokens + base + layout + shared components + animations). Loaded on every page.
2. `tools/static/page-{name}.css` — page-scoped additions. Loaded only for that page. Optional; only create when a page genuinely needs it.

Do not fragment the foundation into dozens of partials. One well-organized file loads faster, is easier to audit, and eliminates import order bugs. The foundation file is organized by **section headers** — not by separate files.

### Foundation File Section Order

The foundation file must be organized in this exact order, with these exact section header comments:

```css
/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   SECTION 1 — DESIGN TOKENS
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   SECTION 2 — BASE RESET & GLOBAL ELEMENTS
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   SECTION 3 — LAYOUT (appShell, topNav, page grids)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   SECTION 4 — SHARED COMPONENTS
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   SECTION 5 — PATTERNS (multi-component assemblies)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   SECTION 6 — PAGE VARIANTS (.pageBody--{key} scopes)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   SECTION 7 — ANIMATIONS (@keyframes only)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
```

**What goes where:**

| Section | Contents |
|---|---|
| 1 — Tokens | `:root { }` only. All `--custom-properties`. Nothing else. |
| 2 — Base | `* { }`, `html`, `body`, element resets (`button`, `input`, `a`). No class selectors. |
| 3 — Layout | `.appShell`, `.topNav`, `.brandLink`, `.navLinks`, `.navLink`, `.heroCard`. Page grid containers (`.cardPageContent`, `.leaderHubContent`). |
| 4 — Components | Standalone reusable units: `.panelCard`, `.infoCard`, `.ctaButton`, `.statusPill`, `.cardChip`, `.confidenceBadge`, `.rolePip`, `.fieldBlock`, `.noticeCard`, etc. |
| 5 — Patterns | Multi-component assemblies that combine primitives: `.miruInsightCard` (toggle + panel), `.voyageMap`, `.devConsoleShell`, `.trainingProgress`. |
| 6 — Page Variants | `.pageBody--dev .topNav { }`, `.pageBody--cardPage .panelCard { }`, etc. Page-scoped overrides ONLY. |
| 7 — Animations | `@keyframes` only. No selectors. Animation `animation:` properties live with their component in section 4 or 5. |

**Hard rules:**
- Section 6 selectors MUST start with `.pageBody--{key}`. No bare class selectors in section 6.
- Section 7 contains ONLY `@keyframes` blocks. No other declarations.
- Sections 1–3 contain NO component class selectors.
- New code goes into the correct section. Adding to the bottom of the file is forbidden.

---

## 2. Template Architecture

### Rule: Jinja2 Inheritance — One Base Template

Create `tools/templates/_base.html`. All page templates extend it. The base template owns:
- `<head>` boilerplate (meta tags, favicon, PWA, stylesheet)
- `<body class="pageBody pageBody--{{ page_key }}">`
- `<main class="appShell">`
- `<nav class="topNav">` (the shared navigation)
- `{% block content %}` — page content goes here
- `{% block scripts %}` — page-specific JS goes here
- `{% block head_extra %}` — per-page CSS `<link>` or `<meta>` goes here

### Base Template Shell

```jinja2
{# tools/templates/_base.html #}
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <meta name="theme-color" content="#130b1f">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="{{ app_name }}">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="description" content="{% block meta_description %}{{ app_name }}{% endblock %}">
    {% if manifest_url %}
    <link rel="manifest" href="{{ manifest_url }}?v={{ asset_version }}">
    {% endif %}
    {% if apple_icon_url %}
    <link rel="apple-touch-icon" href="{{ apple_icon_url }}{% if not apple_icon_url.startswith('data:') %}?v={{ asset_version }}{% endif %}">
    {% endif %}
    {% if favicon_url %}
    <link rel="icon" href="{{ favicon_url }}{% if not favicon_url.startswith('data:') %}?v={{ asset_version }}{% endif %}">
    {% endif %}
    <title>{% block page_title %}{{ app_name }}{% endblock %}</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='miru_ai.css') }}?v={{ asset_version }}">
    {% block head_extra %}{% endblock %}
</head>
<body class="pageBody pageBody--{{ page_key }}">
    <main class="appShell">
        <nav class="topNav" aria-label="Miru navigation">
            <a class="brandLink" href="{{ url_for('index') }}" aria-label="Go to Miru home">
                <img class="brandLogo miruLogo" src="/static/icons/miru-fruit.png" alt="Miru Logo">
                <span class="brandWordmark">
                    <strong>{{ app_name }}</strong>
                    <span>{{ app_tagline }}</span>
                </span>
            </a>
            <div class="navLinks">
                {% for item in nav_items %}
                <a class="navLink{% if item.active %} isActive{% endif %}" href="{{ item.href }}">{{ item.label }}</a>
                {% endfor %}
            </div>
        </nav>

        {% block content %}{% endblock %}

    </main>
    {% block scripts %}{% endblock %}
</body>
</html>
```

### Page Template Shell

```jinja2
{# tools/templates/card_page.html #}
{% extends "_base.html" %}

{% block meta_description %}{{ card_data.card_name or card_data.card_code }} – {{ app_name }}{% endblock %}
{% block page_title %}{{ card_data.card_name or card_data.card_code }} – {{ app_name }}{% endblock %}

{% block content %}
<div class="cardPageContent">
    {# ... card page content here ... #}
</div>
{% endblock %}

{% block scripts %}
<script>
    (function() {
        {# ... page-specific JS ... #}
    })();
</script>
{% endblock %}
```

### Template Rules

- Every new template MUST `{% extends "_base.html" %}`.
- `page_key` is set in Python and passed to the template context. It drives `class="pageBody pageBody--{{ page_key }}"`.
- Never copy the `<head>` boilerplate or `<nav>` block into a new template. If you need to, fix `_base.html` instead.
- `{% block head_extra %}` is for one `<link>` to a page-specific CSS file, or nothing. Not for inline `<style>` blocks.

---

## 3. Naming Convention

### Component Root Classes — camelCase

Component names are camelCase. The root class identifies the component.

```
panelCard          infoCard           heroCard
topNav             appShell           brandLink
navLink            ctaButton          runButton
fieldBlock         statusPill         noticeCard
cardChip           confidenceBadge    rolePip
miruInsightCard    voyageMap          devConsoleShell
```

### Modifier Classes — double-dash suffix

Modifiers follow BEM convention with `--`. Modifiers are applied to the component root.

```
panelCard--compact      panelCard--flat
heroCard--home          heroCard--leaderHub
ctaButton--primary      ctaButton--secondary    ctaButton--danger
statusPill--good        statusPill--warn        statusPill--danger    statusPill--neutral
confidenceBadge--low    confidenceBadge--medium confidenceBadge--strong
rolePip--core           rolePip--flex           rolePip--tech
pageBody--home          pageBody--dev           pageBody--cardPage    pageBody--leaderHub
pageBody--deckBuilder   pageBody--watchlist     pageBody--catalog
```

### Child Classes — double-underscore (BEM)

Children of a component that need scoping use `ComponentName__childName`.

```
miruInsightCard__toggle
miruInsightCard__panel
miruInsightCard__indicator
miruInsightCard__dot
miruInsightCard__category
miruInsightCard__text
miruInsightCard__footer
```

### State Classes — camelCase with `is` prefix

State classes describe runtime state. They are applied/removed by JavaScript.

```
isActive         isCollapsed      isHidden
isLoading        isExpanded       isDisabled
```

### Utility Classes — camelCase (minimal set, purpose-named)

```
srOnly           textClip         noSelect
```

### Page Content Wrapper — `{pageName}Content`

Every page has one top-level content wrapper inside `appShell`, named `{pageName}Content`.

```
cardPageContent
leaderHubContent
deckBuilderContent
watchlistContent
catalogContent
```

This wrapper is the page isolation scope — see section 6.

### Migration Note: `miru_insight_card`

The existing `miru_insight_card` and `miru_insight_card__*` class names use `snake_case`. This is the only outlier. **Do not rename it in Pass A** — renaming requires touching both CSS and every template simultaneously. Schedule the rename in Pass B. In the meantime, document it as a known exception.

---

## 4. Design Tokens

All tokens live in `:root {}` in Section 1 of `miru_ai.css`. Tokens are grouped by category with a comment header. Add new tokens in the correct group; do not scatter them at the bottom of `:root`.

### Complete Token Set

```css
:root {
    color-scheme: dark;

    /* ── Colors: Surface ─────────────────────────────── */
    --bg:              #08050f;
    --bg-soft:         rgba(19, 12, 31, 0.9);
    --panel-top:       rgba(28, 18, 44, 0.95);
    --panel-bottom:    rgba(11, 8, 19, 0.98);
    --panel-alt:       rgba(16, 11, 26, 0.92);
    --panel-raised:    rgba(32, 22, 52, 0.96);

    /* ── Colors: Stroke ──────────────────────────────── */
    --stroke:          rgba(192, 167, 255, 0.18);
    --stroke-strong:   rgba(216, 204, 255, 0.42);
    --stroke-faint:    rgba(192, 167, 255, 0.10);

    /* ── Colors: Text ────────────────────────────────── */
    --text:            #faf7ff;
    --text-soft:       #dbd2ef;
    --text-faint:      #aa9fc7;
    --text-placeholder: rgba(170, 159, 199, 0.5);

    /* ── Colors: Brand ───────────────────────────────── */
    --purple:          #c4adff;
    --purple-strong:   #8b5cf6;
    --purple-dim:      rgba(196, 173, 255, 0.15);
    --yellow:          #facc15;
    --yellow-soft:     rgba(250, 204, 21, 0.12);
    --yellow-dim:      rgba(250, 204, 21, 0.07);

    /* ── Colors: Semantic ────────────────────────────── */
    --good:            #86efac;
    --good-bg:         rgba(22, 101, 52, 0.18);
    --danger:          #fda4af;
    --danger-bg:       rgba(66, 17, 31, 0.9);
    --warn:            #fcd34d;
    --warn-bg:         rgba(120, 80, 0, 0.22);
    --neutral-bg:      rgba(124, 58, 237, 0.14);
    --cyan:            #67e8f9;
    --cyan-strong:     #22d3ee;
    --sea-bg:          rgba(34, 211, 238, 0.12);

    /* ── Colors: Intelligence Signal ────────────────── */
    --signal-strategy: var(--purple);
    --signal-meta:     var(--cyan);
    --signal-market:   #fb923c;
    --signal-market-bg: rgba(251, 146, 60, 0.12);
    --signal-variant:  var(--yellow);
    --signal-lore:     rgba(196, 173, 255, 0.6);

    /* ── Colors: Confidence ──────────────────────────── */
    --conf-low:        var(--text-faint);
    --conf-medium:     var(--yellow);
    --conf-strong:     var(--good);

    /* ── Colors: Role ────────────────────────────────── */
    --role-core:       var(--purple);
    --role-flex:       var(--cyan);
    --role-tech:       var(--text-faint);

    /* ── Spacing ─────────────────────────────────────── */
    --space-1:   0.25rem;   /* 4px  */
    --space-2:   0.5rem;    /* 8px  */
    --space-3:   0.75rem;   /* 12px */
    --space-4:   1rem;      /* 16px */
    --space-5:   1.25rem;   /* 20px */
    --space-6:   1.5rem;    /* 24px */
    --space-8:   2rem;      /* 32px */
    --space-10:  2.5rem;    /* 40px */
    --space-12:  3rem;      /* 48px */

    /* ── Typography ──────────────────────────────────── */
    --font-base:    "Avenir Next", "SF Pro Display", ui-rounded, system-ui, sans-serif;
    --font-mono:    "SF Mono", "Cascadia Mono", ui-monospace, monospace;

    --text-xs:   0.72rem;   /* 11.5px — labels, chip text, captions  */
    --text-sm:   0.82rem;   /* 13px   — secondary body, meta         */
    --text-base: 0.94rem;   /* 15px   — primary body                 */
    --text-md:   1.05rem;   /* 16.8px — slightly above body          */
    --text-lg:   1.18rem;   /* 18.9px — section headings             */
    --text-xl:   1.38rem;   /* 22px   — page titles                  */
    --text-2xl:  1.72rem;   /* 27.5px — hero headings                */
    --text-3xl:  2.2rem;    /* 35px   — display, home hero           */

    --weight-normal:   400;
    --weight-medium:   500;
    --weight-semibold: 600;
    --weight-bold:     700;

    --leading-tight:  1.15;
    --leading-base:   1.5;
    --leading-loose:  1.75;

    /* ── Radii ────────────────────────────────────────── */
    --radius-xs: 6px;
    --radius-sm: 14px;
    --radius-md: 18px;
    --radius-lg: 24px;
    --radius-full: 9999px;

    /* ── Shadows ──────────────────────────────────────── */
    --shadow-sm:  0 12px 26px rgba(6, 4, 12, 0.24);
    --shadow-lg:  0 22px 46px rgba(3, 2, 8, 0.42);
    --shadow-glow-purple: 0 0 24px rgba(139, 92, 246, 0.35);
    --shadow-glow-yellow: 0 0 20px rgba(250, 204, 21, 0.25);

    /* ── Borders ──────────────────────────────────────── */
    --border:        1px solid var(--stroke);
    --border-strong: 1px solid var(--stroke-strong);
    --border-faint:  1px solid var(--stroke-faint);

    /* ── Z-Index ──────────────────────────────────────── */
    --z-base:     0;
    --z-raise:    1;
    --z-panel:    2;
    --z-sticky:   5;
    --z-nav:      10;
    --z-dropdown: 20;
    --z-overlay:  40;
    --z-modal:    50;
    --z-toast:    60;

    /* ── Motion ───────────────────────────────────────── */
    --ease-out:    cubic-bezier(0.22, 1, 0.36, 1);
    --ease-in-out: cubic-bezier(0.4, 0, 0.2, 1);
    --ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);

    --dur-fast:   120ms;
    --dur-base:   220ms;
    --dur-slow:   380ms;
    --dur-enter:  300ms;
    --dur-exit:   180ms;

    /* ── Layout ───────────────────────────────────────── */
    --shell-width: 760px;
    --shell-padding-x: 0.92rem;
}

@media (prefers-reduced-motion: reduce) {
    :root {
        --dur-fast:  0ms;
        --dur-base:  0ms;
        --dur-slow:  0ms;
        --dur-enter: 0ms;
        --dur-exit:  0ms;
    }
}
```

### Token Usage Rules

- Never use a raw hex color or raw pixel value in component CSS if a token exists.
- Never define a new magic number — always add a token first.
- Semantic tokens (`--good`, `--danger`, `--role-core`) are preferred over brand tokens (`--purple`) in components.
- When a new signal category is added (e.g., a new insight type), add its color as a `--signal-*` token.

---

## 5. Shared UI Primitives

These are the components every page can use without scoping. They live in Section 4 of `miru_ai.css`.

### panelCard

The primary content container. Used for sections, cards, headers.

```css
.panelCard {
    position: relative;
    overflow: hidden;
    border-radius: var(--radius-lg);
    border: var(--border);
    box-shadow: var(--shadow-lg);
    background:
        linear-gradient(180deg, var(--panel-top), var(--panel-bottom)),
        linear-gradient(135deg, rgba(182, 156, 255, 0.09), rgba(250, 204, 21, 0.05));
    padding: var(--space-6);
    margin-bottom: var(--space-4);
}

.panelCard--compact {
    padding: var(--space-4);
}

.panelCard--flat {
    background: var(--panel-alt);
    box-shadow: none;
}
```

### ctaButton

Primary and secondary action buttons.

```css
.ctaButton {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-3) var(--space-5);
    border-radius: var(--radius-sm);
    border: var(--border);
    font-size: var(--text-sm);
    font-weight: var(--weight-semibold);
    cursor: pointer;
    transition: opacity var(--dur-fast) var(--ease-out),
                transform var(--dur-fast) var(--ease-out);
    touch-action: manipulation;
}

.ctaButton--primary {
    background: var(--purple-strong);
    border-color: transparent;
    color: #fff;
}

.ctaButton--secondary {
    background: var(--neutral-bg);
    color: var(--text-soft);
}

.ctaButton--danger {
    background: var(--danger-bg);
    border-color: var(--danger);
    color: var(--danger);
}

.ctaButton:hover { opacity: 0.85; }
.ctaButton:active { transform: scale(0.97); }
.ctaButton:disabled,
.ctaButton.isDisabled {
    opacity: 0.4;
    cursor: not-allowed;
    pointer-events: none;
}
```

### statusPill

Compact colored status indicator. Used in dev console, confidence display, process status.

```css
.statusPill {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: var(--space-1) var(--space-3);
    border-radius: var(--radius-full);
    font-size: var(--text-xs);
    font-weight: var(--weight-semibold);
    border: 1px solid transparent;
    white-space: nowrap;
}

.statusPill--good    { background: var(--good-bg);    color: var(--good);    border-color: var(--good); }
.statusPill--warn    { background: var(--warn-bg);    color: var(--warn);    border-color: var(--warn); }
.statusPill--danger  { background: var(--danger-bg);  color: var(--danger);  border-color: var(--danger); }
.statusPill--neutral { background: var(--neutral-bg); color: var(--purple);  border-color: var(--stroke); }
```

### confidenceBadge

Inline badge showing confidence tier.

```css
.confidenceBadge {
    display: inline-block;
    padding: 0.15em 0.55em;
    border-radius: var(--radius-xs);
    font-size: var(--text-xs);
    font-weight: var(--weight-semibold);
    text-transform: lowercase;
    letter-spacing: 0.02em;
}

.confidenceBadge--low    { background: var(--neutral-bg); color: var(--conf-low); }
.confidenceBadge--medium { background: var(--yellow-dim); color: var(--conf-medium); }
.confidenceBadge--strong { background: var(--good-bg);    color: var(--conf-strong); }
```

### cardChip

Small code chip used in card grids, archetype lists.

```css
.cardChip {
    display: inline-flex;
    align-items: center;
    padding: 0.2em 0.6em;
    border-radius: var(--radius-xs);
    border: var(--border-faint);
    background: var(--purple-dim);
    font-size: var(--text-xs);
    font-family: var(--font-mono);
    color: var(--purple);
    white-space: nowrap;
    cursor: default;
}

.cardChip--role-core  { border-color: var(--role-core);  color: var(--role-core); }
.cardChip--role-flex  { border-color: var(--role-flex);  color: var(--role-flex); }
.cardChip--role-tech  { border-color: var(--role-tech);  color: var(--role-tech); }
```

### fieldBlock

Form field wrapper, used for input, textarea, select groups.

```css
.fieldBlock {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    margin-bottom: var(--space-4);
}

.fieldBlock label {
    font-size: var(--text-sm);
    font-weight: var(--weight-medium);
    color: var(--text-soft);
}

.fieldBlock input,
.fieldBlock textarea,
.fieldBlock select {
    width: 100%;
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius-sm);
    border: var(--border);
    background: rgba(8, 5, 15, 0.7);
    color: var(--text);
    font-size: var(--text-base);
}

.fieldHelp {
    font-size: var(--text-xs);
    color: var(--text-faint);
    line-height: var(--leading-loose);
}
```

### noticeCard

Non-critical informational banner, used for startup warnings.

```css
.noticeCard {
    border-radius: var(--radius-sm);
    border: var(--border-faint);
    background: var(--warn-bg);
    padding: var(--space-4);
    font-size: var(--text-sm);
    color: var(--text-soft);
}

.noticeCard strong {
    color: var(--warn);
    display: block;
    margin-bottom: var(--space-1);
}
```

### miruInsightCard (formerly `miru_insight_card`)

The collapsible Miru intelligence panel. The class names currently use `snake_case` as a legacy exception. Do not rename in Pass A.

```css
/* Note: class names are legacy snake_case — rename to miruInsightCard__ in Pass B */
.miru_insight_card {
    position: relative;
}

.miru_insight_card__toggle {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    background: none;
    border: none;
    cursor: pointer;
    padding: var(--space-4) 0;
    width: 100%;
    text-align: left;
    color: var(--purple);
    font-size: var(--text-sm);
    font-weight: var(--weight-semibold);
    transition: opacity var(--dur-fast) var(--ease-out);
}

.miru_insight_card__toggle:hover { opacity: 0.8; }

.miru_insight_card__dot {
    display: inline-block;
    color: var(--purple-strong);
    animation: miru_insight_glow 2.8s ease-in-out infinite;
}

.miru_insight_card__panel {
    padding-top: var(--space-2);
    padding-bottom: var(--space-2);
}

.miru_insight_card__panel.isCollapsed {
    display: none;
}

.miru_insight_card__category {
    font-size: var(--text-xs);
    font-weight: var(--weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-faint);
    margin: 0 0 var(--space-2);
}

.miru_insight_card__text {
    font-size: var(--text-base);
    line-height: var(--leading-loose);
    color: var(--text-soft);
    margin: 0 0 var(--space-3);
}

.miru_insight_card__footer {
    font-size: var(--text-xs);
    color: var(--text-faint);
}
```

---

## 6. Page Isolation Strategy

### The Page Root Class

Every page has exactly one page root class on `<body>`: `pageBody--{key}`. This class is the scope for all page-specific styles.

```html
<body class="pageBody pageBody--cardPage">
```

Rules:
- `pageBody` (no modifier) carries only structural styles shared by ALL pages (e.g., font, background).
- `pageBody--{key}` carries page-specific layout and component overrides.
- The `page_key` value is the single source of truth. It drives both the body class and `data-page-key` on the content wrapper.

### Page Content Wrapper

Every page has one content wrapper as the first child of `.appShell` after the `topNav`:

```html
<div class="{pageName}Content">
    <!-- all page content here -->
</div>
```

This wrapper is where page-specific CSS can be scoped without using `.pageBody--*` on every rule.

```css
/* In Section 6 or page-{name}.css — both are valid */
.cardPageContent .panelCard {
    /* override panelCard only on the card page */
}

/* Equivalent using page root */
.pageBody--cardPage .panelCard {
    /* same thing */
}
```

Both scoping patterns are valid. Use `.{pageName}Content` for layout/structural rules. Use `.pageBody--{key}` for body-level overrides (e.g., nav compression on the dev page).

### Page-Specific CSS File

When a page needs more than ~40 lines of unique styles, create a page file:

```
tools/static/page-card.css
tools/static/page-leader-hub.css
tools/static/page-deck-builder.css
tools/static/page-watchlist.css
tools/static/page-dev.css
```

Load it in the template's `{% block head_extra %}`:

```jinja2
{% block head_extra %}
<link rel="stylesheet" href="{{ url_for('static', filename='page-card.css') }}?v={{ asset_version }}">
{% endblock %}
```

Every selector in a page-specific CSS file MUST be scoped under `.pageBody--{key}` or `.{pageName}Content`. Never write bare component selectors in a page file.

### Page Isolation Hard Rules

1. **A page file cannot override another page's styles.** If a rule starts with `.pageBody--leaderHub`, it can only appear in `page-leader-hub.css` or Section 6 of the foundation.
2. **A page file cannot redefine shared component defaults.** If `.panelCard` needs different padding on the card page, write `.cardPageContent .panelCard { padding: ... }`. Never write `.panelCard { ... }` in a page file.
3. **Section 4 (shared components) must work on every page without modification.** If a shared component is broken on one page, fix it via scoped override, not by changing the component.
4. **No inline `<style>` blocks in templates.** All styles go in CSS files.
5. **Page-key values are lowercase-kebab.** `card-page` becomes `pageBody--cardPage` via camelCase conversion in the class name.

### Current Page Keys

| `page_key` | Body class | Content wrapper | CSS file |
|---|---|---|---|
| `home` | `pageBody--home` | (managed by `miru_ai.html`) | foundation only |
| `dev` | `pageBody--dev` | (managed by `miru_ai.html`) | foundation (heavy Section 6) |
| `ask` | `pageBody--ask` | — | foundation only |
| `cardPage` | `pageBody--cardPage` | `cardPageContent` | `page-card.css` (future) |
| `leaderHub` | `pageBody--leaderHub` | `leaderHubContent` | `page-leader-hub.css` (future) |
| `deckBuilder` | `pageBody--deckBuilder` | `deckBuilderContent` | `page-deck-builder.css` (future) |
| `watchlist` | `pageBody--watchlist` | `watchlistContent` | `page-watchlist.css` (future) |
| `catalog` | `pageBody--catalog` | `catalogContent` | `page-catalog.css` (future) |

---

## 7. Regression Checklist

Every PR that touches a CSS file or HTML template must verify the following before merge. If any check fails, the PR is not ready.

### Visual Regression Checks (manual, ~2 minutes)

Load each of the following URLs and verify no obvious visual breakage:

- [ ] `http://127.0.0.1:18080/` — home page hero and nav intact
- [ ] `http://127.0.0.1:18080/dev` — dev console compact nav, panels, status pills intact
- [ ] `http://127.0.0.1:18765/leader/OP01-001` — leader hub panels, archetype cards, card chips intact
- [ ] `http://127.0.0.1:18765/card/OP01-001` — card page header, image section, usage context intact
- [ ] Mobile viewport (375px width) — nav wraps correctly, no horizontal scroll, touch targets ≥44px

### Structural Checks (CSS)

- [ ] No bare hex colors or raw `px` values in component CSS (use tokens)
- [ ] No magic z-index numbers (use `--z-*` tokens)
- [ ] No `!important` added (existing ones must not be increased)
- [ ] New page-specific styles are scoped under a page root class
- [ ] No selectors in a page CSS file that could match a different page's content
- [ ] Section order in `miru_ai.css` not violated (Section 7 contains only `@keyframes`)
- [ ] New tokens placed in the correct token group in Section 1
- [ ] Animations reference token durations and easings, not hardcoded values

### Structural Checks (Templates)

- [ ] New templates extend `_base.html`
- [ ] No `<head>` boilerplate duplicated outside `_base.html`
- [ ] No `<nav class="topNav">` outside `_base.html`
- [ ] No inline `<style>` blocks added to any template
- [ ] `page_key` is set correctly in the Python route and matches the intended body class

### Accessibility Checks

- [ ] Interactive elements have accessible labels (`aria-label` or visible text)
- [ ] `aria-expanded` managed correctly on toggle buttons
- [ ] New images have `alt` text
- [ ] Focus ring not removed without replacement (`:focus-visible` must be usable)

---

## 8. Phased Implementation Plan

### Pass A — UI Safety Foundation

**Goal**: Make the architecture safe for future workers. No visual changes. No new features.

**Work items (in order):**

1. **Expand token set** in Section 1 of `miru_ai.css`
   - Add spacing scale (`--space-1` through `--space-12`)
   - Add typography scale (`--text-xs` through `--text-3xl`, weight and leading)
   - Add z-index tokens (`--z-*`)
   - Add motion tokens (`--dur-*`, `--ease-*`)
   - Add `--radius-xs`, `--radius-full`
   - Add `--warn`, `--warn-bg`
   - Add `--signal-*` colors, `--conf-*` colors, `--role-*` colors
   - Add `--border`, `--border-strong`, `--border-faint`
   - Add `@media (prefers-reduced-motion)` zeroing block
   - **Verification**: `:root` renders no visual change

2. **Add section headers** to `miru_ai.css`
   - Insert the 7 section header comments at the correct positions
   - Do not move any CSS rules — headers only
   - **Verification**: Lint the file; no CSS rules changed

3. **Create `tools/templates/_base.html`**
   - Extract shared `<head>`, nav, and shell from `miru_ai.html`
   - Implement blocks: `head_extra`, `page_title`, `meta_description`, `content`, `scripts`
   - **Verification**: `miru_ai.html` still renders identically after refactor

4. **Migrate `card_page.html` to extend `_base.html`**
   - Remove duplicate head and nav
   - Wrap content in `{% block content %}`
   - Move inline `<script>` to `{% block scripts %}`
   - **Verification**: Card page renders identically at `http://127.0.0.1:18765/card/OP01-001`

5. **Migrate `leader_hub.html` to extend `_base.html`**
   - Same pattern as card_page
   - **Verification**: Leader hub renders identically at `http://127.0.0.1:18765/leader/OP01-001`

6. **Replace all magic z-index values** in `miru_ai.css` with `--z-*` tokens
   - Find: `z-index: 10` → `z-index: var(--z-nav)`
   - Find: `z-index: 60` → `z-index: var(--z-toast)`
   - Find: `z-index: 1`, `z-index: 2`, `z-index: 3` → `var(--z-raise)`, `var(--z-panel)`, etc.
   - **Verification**: No visual stacking changes

**Pass A exit criteria:**
- All three templates extend `_base.html`
- Token set is complete (spacing, typography, z-index, motion)
- CSS section headers are in place
- All checked regression items pass
- No visual difference from before Pass A

---

### Pass B — Full Migration and Standardization

**Goal**: Clean up all inconsistencies identified in the audit. Migrate everything to use the full token set. Rename the `miru_insight_card` outlier. Add page CSS files for new pages.

**Work items (no strict order, each independently testable):**

1. **Replace raw spacing values with tokens** throughout `miru_ai.css`
   - All `padding`, `margin`, `gap` values → `var(--space-*)` where token matches
   - Do not force tokens where the value is page-scoped and intentionally unique

2. **Replace raw typography values with tokens**
   - All `font-size`, `font-weight`, `line-height` → tokens

3. **Rename `miru_insight_card` → `miruInsightCard`**
   - Find/replace in: `miru_ai.css`, `card_page.html`, `leader_hub.html`, `miru_ai.html`
   - Update all JS `getElementById` and `classList` references
   - **Verification**: Insight toggle works on card page and leader hub

4. **Normalize `confidenceBadge` component**
   - Existing `.confidence--{{ level }}` patterns consolidate to `.confidenceBadge--{{ level }}`
   - Update all templates that use the old pattern
   - **Verification**: Confidence indicators render correctly on leader hub

5. **Normalize `cardChip` component**
   - Existing `.leaderHubCardChip` → `.cardChip` (or `.cardChip--role-*` variants)
   - **Verification**: Card chips render in archetype grids

6. **Create page CSS files** for card page and leader hub as they grow
   - Only create when passing 40 lines of page-specific styles

7. **Audit Section 6** for any rules that should be in Section 4
   - If a `.pageBody--dev .panelCard` override represents a genuinely useful component variant, promote it to a `.panelCard--compact` modifier in Section 4

**Pass B exit criteria:**
- No raw colors, spacing values, or z-index values in shared component CSS
- `miru_insight_card` → `miruInsightCard` everywhere
- `confidence--*` → `confidenceBadge--*` everywhere
- Regression checklist passes on all pages

---

## 9. Safe Global CSS — How Not to Cause Regressions

These are the practical rules for working in a shared CSS file.

### Surgical edits only

When modifying an existing component selector (e.g., `.panelCard`), change only the specific property that needs fixing. Do not reformat, reorder, or re-indent unrelated properties in the same block. Diff noise causes review errors and hides regressions.

### Add before you remove

When changing a component's appearance, add the new rule first (as a modifier or page-scoped override), verify it works in isolation, then remove the old value. Never remove first.

### Scope overrides, never redefine

If `.panelCard` needs to look different on the deck builder page, write:
```css
.deckBuilderContent .panelCard { ... }
```
Never change `.panelCard` directly. If you find yourself writing the same scoped override on 3+ pages, promote it to a modifier instead.

### The inheritance trap

CSS specificity compounds. `.pageBody--dev .panelCard` has higher specificity than `.panelCard`. Never add an override to fix a specificity battle — trace the root cause instead. The solution is almost always in the component definition, not in adding another layer of nesting.

### Test the nav and the home page first

The nav and home page are shared by everything. Any edit to Section 3 (Layout) requires loading the home page and dev page immediately. They are the most likely to show regressions from layout changes.

### Animation properties are cheap, keyframes are not

Adding an `animation:` property to an existing class is safe (it uses an already-loaded keyframe). Adding a new `@keyframes` is fine once it is in Section 7. Never define a `@keyframes` inside a component block — they must be in Section 7.

---

## 10. Code Snippets Reference

### A. Complete Page Shell HTML Structure

```jinja2
{# New page template — extends _base.html #}
{% extends "_base.html" %}

{% block meta_description %}Watchlist — {{ app_name }}{% endblock %}
{% block page_title %}Watchlist – {{ app_name }}{% endblock %}

{% block head_extra %}
{# Only add this line if page-watchlist.css exists and has content #}
<link rel="stylesheet" href="{{ url_for('static', filename='page-watchlist.css') }}?v={{ asset_version }}">
{% endblock %}

{% block content %}
<div class="watchlistContent">

    <header class="watchlistHeader panelCard">
        <h1 class="watchlistTitle">Watchlist</h1>
    </header>

    <section class="watchlistItems" aria-label="Watched cards">
        {% for card in watchlist_cards %}
        <article class="panelCard panelCard--compact watchlistItem">
            <span class="cardChip">{{ card.card_code }}</span>
            <span class="watchlistItemName">{{ card.card_name }}</span>
            <span class="confidenceBadge confidenceBadge--{{ card.confidence }}">{{ card.confidence }}</span>
        </article>
        {% endfor %}
    </section>

</div>
{% endblock %}

{% block scripts %}
<script>
    (function() {
        // page-specific JS only
    })();
</script>
{% endblock %}
```

### B. Page Root Scoping Pattern

```css
/* tools/static/page-watchlist.css */
/* ALL selectors in this file must start with .watchlistContent or .pageBody--watchlist */

.watchlistContent {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
}

.watchlistContent .panelCard--compact {
    /* Override panelCard padding on this page only */
    padding: var(--space-3) var(--space-4);
}

.watchlistItem {
    display: flex;
    align-items: center;
    gap: var(--space-3);
}

.watchlistItemName {
    flex: 1;
    font-size: var(--text-base);
    color: var(--text);
}

/* Body-level override via pageBody-- scope */
.pageBody--watchlist .topNav {
    /* Only if nav needs a watchlist-specific adjustment */
}
```

### C. Shared Panel Component Structure

```html
<!-- Standard panelCard with a section heading -->
<section class="panelCard" aria-label="Section name">
    <h2 class="sectionTitle">Section Name</h2>
    <p class="sectionBody">Content here.</p>
</section>

<!-- Compact panelCard with chip row -->
<div class="panelCard panelCard--compact">
    <div class="chipRow">
        <span class="cardChip cardChip--role-core">OP01-001</span>
        <span class="cardChip cardChip--role-flex">OP01-060</span>
    </div>
</div>

<!-- panelCard with action row at bottom -->
<div class="panelCard">
    <p>Content</p>
    <div class="actionRow">
        <button class="ctaButton ctaButton--primary">Confirm</button>
        <button class="ctaButton ctaButton--secondary">Cancel</button>
    </div>
</div>
```

### D. Token Definitions — Adding a New Signal Type

When a new insight category is added (e.g., "Deck Trend"), add its token to Section 1 before using the color anywhere:

```css
/* In :root — Colors: Intelligence Signal group */
--signal-deck-trend:    #a78bfa;
--signal-deck-trend-bg: rgba(167, 139, 250, 0.12);
```

Then add the component modifier in Section 4:

```css
/* In Section 4 — Shared Components, after confidenceBadge */
.signalBadge--deckTrend {
    background: var(--signal-deck-trend-bg);
    color: var(--signal-deck-trend);
    border-color: var(--signal-deck-trend);
}
```

### E. Isolated Page Stylesheet — Complete Example

```css
/* tools/static/page-deck-builder.css */
/* Scope: ALL rules scoped to .deckBuilderContent or .pageBody--deckBuilder */
/* Do not add bare component selectors. */

/* ── Page layout ───────────────────────── */
.deckBuilderContent {
    display: grid;
    grid-template-columns: 1fr;
    gap: var(--space-4);
}

@media (min-width: 700px) {
    .deckBuilderContent {
        grid-template-columns: 1fr 1fr;
    }
}

/* ── Page-specific component overrides ─── */
.deckBuilderContent .panelCard {
    margin-bottom: 0; /* gap from grid handles spacing */
}

/* ── Unique page components ────────────── */
.deckBuilderCardList {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
}

.deckBuilderCardRow {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-sm);
    background: var(--panel-alt);
    border: var(--border-faint);
    transition: background var(--dur-fast) var(--ease-out);
}

.deckBuilderCardRow:hover {
    background: var(--purple-dim);
    border-color: var(--stroke);
}

.deckBuilderCardCount {
    font-size: var(--text-sm);
    font-weight: var(--weight-semibold);
    color: var(--purple);
    min-width: 1.5ch;
    text-align: right;
}

/* ── Body-level overrides ──────────────── */
/* none needed for deck builder — remove section if empty */
```

### F. How to Add a New Page (Complete Checklist)

1. Choose a `page_key` (e.g., `deckBuilder`)
2. Add the route in `tools/miru_ai_server.py`, pass `page_key="deckBuilder"` to the template context
3. Create `tools/templates/deck_builder.html` extending `_base.html`
4. Add `pageBody--deckBuilder` styles in Section 6 of `miru_ai.css` (or a page CSS file)
5. Create `tools/static/page-deck-builder.css` if the page needs more than ~40 lines
6. Add the page to the nav items list if it should appear in the nav
7. Add the page key to the "Current Page Keys" table in this document
8. Run the regression checklist

---

## Appendix: Token Quick Reference

| Need | Token |
|---|---|
| Primary text | `var(--text)` |
| Muted text | `var(--text-soft)` |
| Faint text | `var(--text-faint)` |
| Body font | `var(--font-base)` |
| Code/mono font | `var(--font-mono)` |
| Primary accent | `var(--purple)` |
| Strong accent | `var(--purple-strong)` |
| Warning accent | `var(--yellow)` |
| Success | `var(--good)` |
| Danger | `var(--danger)` |
| Section container | `var(--panel-top)` / `var(--panel-bottom)` |
| Stroke / border | `var(--stroke)` |
| Small border radius | `var(--radius-sm)` |
| Large border radius | `var(--radius-lg)` |
| Standard shadow | `var(--shadow-lg)` |
| Standard spacing | `var(--space-4)` (1rem) |
| Nav z-index | `var(--z-nav)` |
| Modal z-index | `var(--z-modal)` |
| Fast transition | `var(--dur-fast) var(--ease-out)` |
| Enter animation | `var(--dur-enter) var(--ease-out)` |
| Confidence low | `var(--conf-low)` |
| Confidence medium | `var(--conf-medium)` |
| Confidence strong | `var(--conf-strong)` |
| Core role | `var(--role-core)` |
| Flex role | `var(--role-flex)` |
| Tech role | `var(--role-tech)` |
