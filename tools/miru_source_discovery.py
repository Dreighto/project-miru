from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse


DISCOVERY_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "deck_database",
        ("deck", "decks", "decklist", "decklists", "archetype", "archetypes"),
        ("limitlesstcg", "onepiecetopdecks", "egmanevents", "nakamadecks"),
    ),
    (
        "card_database",
        ("card", "cards", "cardlist", "catalog", "database"),
        ("onepiece-cardgame", "limitlesstcg", "tcgplayer", "collectr"),
    ),
    (
        "tournament_results",
        ("tournament", "results", "placements", "top cut", "regional", "championship"),
        ("limitlesstcg", "egmanevents", "topdeck", "melee"),
    ),
    (
        "meta_analysis",
        ("meta", "analysis", "tier list", "guide", "matchup", "strategy"),
        ("onepiecetopdecks", "pokemoncard", "youtube", "substack", "medium"),
    ),
)


@dataclass(frozen=True)
class DiscoveredSourceCandidate:
    url: str
    host: str
    source_kind: str
    confidence_score: float
    review_status: str
    detected_at: str
    title: str = ""
    notes: str = ""
    signals: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["metadata"] = dict(self.metadata or {})
        return payload


def _normalized_tokens(*values: str) -> list[str]:
    tokens: list[str] = []
    for value in values:
        text = str(value or "").strip().lower()
        if not text:
            continue
        normalized = (
            text.replace("-", " ")
            .replace("_", " ")
            .replace(".", " ")
            .replace("/", " ")
        )
        tokens.extend(part for part in normalized.split() if part)
    return tokens


def discover_source_candidate(
    *,
    url: str,
    title: str = "",
    notes: str = "",
    detected_at: str = "",
) -> DiscoveredSourceCandidate | None:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()
    tokens = _normalized_tokens(host, path, title, notes)
    token_set = set(tokens)

    best_kind = ""
    best_score = 0.0
    best_signals: list[str] = []
    for source_kind, keywords, host_hints in DISCOVERY_RULES:
        score = 0.0
        signals: list[str] = []
        for keyword in keywords:
            keyword_parts = tuple(keyword.split())
            if len(keyword_parts) == 1:
                if keyword_parts[0] in token_set:
                    score += 0.2
                    signals.append(keyword)
            elif all(part in token_set for part in keyword_parts):
                score += 0.25
                signals.append(keyword)
        for host_hint in host_hints:
            if host_hint in host:
                score += 0.35
                signals.append(host_hint)
        if score > best_score:
            best_kind = source_kind
            best_score = score
            best_signals = signals

    if best_score <= 0.0:
        return None

    confidence_score = min(round(0.35 + best_score, 2), 0.95)
    return DiscoveredSourceCandidate(
        url=parsed.geturl(),
        host=host,
        source_kind=best_kind or "unknown_candidate",
        confidence_score=confidence_score,
        review_status="pending_review",
        detected_at=str(detected_at or ""),
        title=str(title or "").strip(),
        notes=str(notes or "").strip(),
        signals=tuple(sorted(set(best_signals))),
        metadata={"path": path},
    )


def discover_source_candidates(
    rows: list[dict[str, Any]],
    *,
    detected_at: str = "",
) -> list[DiscoveredSourceCandidate]:
    candidates: list[DiscoveredSourceCandidate] = []
    seen: set[str] = set()
    for row in rows:
        candidate = discover_source_candidate(
            url=str(row.get("url") or ""),
            title=str(row.get("title") or ""),
            notes=str(row.get("notes") or ""),
            detected_at=detected_at,
        )
        if candidate is None:
            continue
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        candidates.append(candidate)
    return candidates
