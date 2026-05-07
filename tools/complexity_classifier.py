"""
complexity_classifier.py -- Phase 1 rule-based complexity classifier for Linear tickets.

Analyzes a ticket title and description to determine whether the ticket is a candidate
for parallel sub-task dispatch (the "Job Splitter" design). Returns a split
recommendation dict. Does NOT create sub-tickets.
"""

from __future__ import annotations

import re
from typing import TypedDict


class SuggestedSplit(TypedDict):
    label: str
    scope: str
    service_dirs: list[str]


class ClassificationResult(TypedDict):
    should_split: bool
    complexity: str  # "low" | "medium" | "high"
    signals: list[str]
    suggested_splits: list[SuggestedSplit]


# ---------------------------------------------------------------------------
# Service boundary catalog (mirrors CLAUDE.md "File Placement -- Hard Rules")
# ---------------------------------------------------------------------------

_SERVICES: list[tuple[str, str, list[str]]] = [
    # (label, canonical_dir, [lowercase match strings])
    ("miru_ai", "miru_ai/", ["miru_ai", "miru ai", "18765", "ai backend"]),
    ("pm_frontend", "pm/storefront/", ["storefront", "svelte", "sveltekit", "frontend"]),
    ("pm_api", "pm/", ["pm dashboard", "pm api", "18080", "flask"]),
    ("n8n_workflows", "docker/n8n/workflows/", ["n8n", "workflow json", "n8n workflow"]),
    (
        "dispatch_listener",
        "services/dispatch_listener/",
        ["dispatch listener", "spawn.js", "dispatch_listener"],
    ),
    ("tools", "tools/", ["tools/", "standalone script"]),
]

# ---------------------------------------------------------------------------
# File-type categories for mixing detection
# ---------------------------------------------------------------------------

_FILE_TYPE_CATS: list[tuple[str, list[str]]] = [
    ("python", [r"\.py\b", r"\bpython\b"]),
    (
        "frontend",
        [r"\.svelte\b", r"\.html\b", r"\.css\b", r"\bsvelte\b", r"\bfrontend\b", r"\btemplate\b"],
    ),
    ("workflow", [r"\.json\b", r"\bn8n\b", r"\bworkflow json\b"]),
    ("nodejs", [r"\bspawn\.js\b", r"\bnode\.js\b", r"\bdispatch_listener\b"]),
]

# ---------------------------------------------------------------------------
# Conjunction keyword patterns (weak signal)
# ---------------------------------------------------------------------------

_CONJUNCTION_PATS: list[str] = [
    r"\band also\b",
    r"\bas well as\b",
    r"\badditionally\b",
    r"\bin addition\b",
    r"\balso (?:update|add|fix|implement|create|write|test|build|change|modify)\b",
    r"\band (?:update|add|fix|implement|create|write|test|build|change|modify)\b",
    r"\bthen (?:also|update|add|fix|implement|create|write)\b",
]

# ---------------------------------------------------------------------------
# Large-scope keywords (weak signal -- two or more needed)
# ---------------------------------------------------------------------------

_SCOPE_PATS: list[str] = [
    r"\brefactor\b",
    r"\boverhaul\b",
    r"\bmigrate\b",
    r"\brewrite\b",
    r"\bredesign\b",
    r"\brework\b",
    r"\bcomprehensive\b",
    r"\bend[\s-]+to[\s-]+end\b",
]

# ---------------------------------------------------------------------------
# Signal weights
# ---------------------------------------------------------------------------
# Strong signals (weight 2): any single one triggers "high" complexity.
# Weak signals (weight 1): need two to reach "high", or one strong + one weak.
#
# Complexity thresholds: 0 = low, 1 = medium, >=2 = high.
# should_split = True when complexity == "high".

_STRONG_WEIGHT = 2
_WEAK_WEIGHT = 1
_HIGH_THRESHOLD = 2


# ---------------------------------------------------------------------------
# Detection helpers -- each returns (detected: bool, description: str)
# ---------------------------------------------------------------------------


def _corpus(title: str, description: str) -> str:
    return f"{title}\n{description}".lower()


def _detect_multi_service(corpus: str) -> tuple[bool, str]:
    hits: list[str] = []
    for label, _sdir, keywords in _SERVICES:
        for kw in keywords:
            if kw in corpus:
                hits.append(label)
                break
    hits = list(dict.fromkeys(hits))
    if len(hits) >= 2:
        return True, f"Touches {len(hits)} service boundaries: {', '.join(hits)}"
    return False, ""


def _detect_file_type_mixing(corpus: str) -> tuple[bool, str]:
    hits: list[str] = []
    for cat, pats in _FILE_TYPE_CATS:
        for pat in pats:
            if re.search(pat, corpus):
                hits.append(cat)
                break
    hits = list(dict.fromkeys(hits))
    if len(hits) >= 2:
        return True, f"Mixes {len(hits)} file-type categories: {', '.join(hits)}"
    return False, ""


def _detect_multiple_tasks(description: str) -> tuple[bool, str]:
    bullet_re = re.compile(r"^\s*(?:[*\-+]|\d+[.):])\s+\S.{9,}", re.MULTILINE)
    bullets = bullet_re.findall(description)
    if len(bullets) >= 3:
        return True, f"Contains {len(bullets)} discrete task items"
    return False, ""


def _detect_conjunctions(corpus: str) -> tuple[bool, str]:
    for pat in _CONJUNCTION_PATS:
        m = re.search(pat, corpus)
        if m:
            return True, f"Multi-task conjunction: {m.group(0).strip()!r}"
    return False, ""


def _detect_scope(corpus: str) -> tuple[bool, str]:
    seen: set[str] = set()
    hits: list[str] = []
    for pat in _SCOPE_PATS:
        m = re.search(pat, corpus)
        if m:
            term = m.group(0).strip()
            if term not in seen:
                seen.add(term)
                hits.append(term)
    if len(hits) >= 2:
        return True, f"Large-scope keywords: {', '.join(hits[:3])}"
    return False, ""


# ---------------------------------------------------------------------------
# Suggested splits generator
# ---------------------------------------------------------------------------


def _suggested_splits(
    title: str,
    description: str,
    signal_names: set[str],
) -> list[SuggestedSplit]:
    corpus = _corpus(title, description)

    if "multi_service_boundary" in signal_names:
        results: list[SuggestedSplit] = []
        for label, sdir, keywords in _SERVICES:
            for kw in keywords:
                if kw in corpus:
                    results.append(
                        {
                            "label": f"{label} work",
                            "scope": f"Changes isolated to {sdir}",
                            "service_dirs": [sdir],
                        }
                    )
                    break
        return results[:3]

    if "file_type_mixing" in signal_names:
        results = []
        for cat, pats in _FILE_TYPE_CATS:
            for pat in pats:
                if re.search(pat, corpus):
                    results.append(
                        {
                            "label": f"{cat} changes",
                            "scope": f"Changes to {cat} files only",
                            "service_dirs": [],
                        }
                    )
                    break
        return results[:3]

    if "multiple_discrete_tasks" in signal_names:
        bullet_re = re.compile(r"^\s*(?:[*\-+]|\d+[.):])\s+(\S.{9,})", re.MULTILINE)
        items = bullet_re.findall(description)
        mid = (len(items) + 1) // 2
        return [
            {
                "label": "Task group A",
                "scope": "; ".join(i.strip()[:60] for i in items[:mid]),
                "service_dirs": [],
            },
            {
                "label": "Task group B",
                "scope": "; ".join(i.strip()[:60] for i in items[mid:]),
                "service_dirs": [],
            },
        ]

    return [
        {"label": "Part 1", "scope": "First half of ticket scope", "service_dirs": []},
        {"label": "Part 2", "scope": "Second half of ticket scope", "service_dirs": []},
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_ticket(title: str, description: str = "") -> ClassificationResult:
    """Classify a Linear ticket for parallel split suitability.

    Args:
        title:       Ticket title string.
        description: Ticket body (markdown ok, may be empty).

    Returns:
        ClassificationResult with keys:
            should_split     -- True when the ticket is a candidate for parallel dispatch.
            complexity       -- "low" | "medium" | "high"
            signals          -- Human-readable description of each detected signal.
            suggested_splits -- Proposed split groupings (empty when should_split is False).
    """
    description = description or ""
    corpus = _corpus(title, description)

    # Each entry: (name, weight, description_str)
    detections: list[tuple[str, int, str]] = []

    ok, msg = _detect_multi_service(corpus)
    if ok:
        detections.append(("multi_service_boundary", _STRONG_WEIGHT, msg))

    ok, msg = _detect_file_type_mixing(corpus)
    if ok:
        detections.append(("file_type_mixing", _STRONG_WEIGHT, msg))

    ok, msg = _detect_multiple_tasks(description)
    if ok:
        detections.append(("multiple_discrete_tasks", _STRONG_WEIGHT, msg))

    ok, msg = _detect_conjunctions(corpus)
    if ok:
        detections.append(("conjunction_keywords", _WEAK_WEIGHT, msg))

    ok, msg = _detect_scope(corpus)
    if ok:
        detections.append(("scope_breadth", _WEAK_WEIGHT, msg))

    total_weight = sum(w for _, w, _ in detections)
    signal_names = {name for name, _, _ in detections}
    signal_descriptions = [desc for _, _, desc in detections]

    if total_weight == 0:
        complexity: str = "low"
    elif total_weight < _HIGH_THRESHOLD:
        complexity = "medium"
    else:
        complexity = "high"

    should_split = complexity == "high"
    splits = _suggested_splits(title, description, signal_names) if should_split else []

    return {
        "should_split": should_split,
        "complexity": complexity,
        "signals": signal_descriptions,
        "suggested_splits": splits,
    }
