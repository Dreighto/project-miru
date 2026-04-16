from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from threading import Lock
from typing import Any


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _source_hash(payload: Any) -> str:
    try:
        normalized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    except Exception:
        normalized = "{}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


class MiruContextWindow:
    """In-memory, non-persistent context window for optional local hints."""

    def __init__(self, *, max_events: int = 80):
        self._max_events = max(12, int(max_events or 80))
        self._cards: deque[str] = deque(maxlen=self._max_events)
        self._leaders: deque[str] = deque(maxlen=self._max_events)
        self._updated_at = utc_timestamp()
        self._lock = Lock()

    def record_card_view(self, card_code: str, *, leader_code: str = "", is_leader: bool = False) -> None:
        normalized_card = _safe_text(card_code).upper()
        normalized_leader = _safe_text(leader_code).upper()
        if not normalized_card:
            return
        with self._lock:
            self._cards.appendleft(normalized_card)
            if is_leader:
                self._leaders.appendleft(normalized_card)
            if normalized_leader:
                self._leaders.appendleft(normalized_leader)
            self._updated_at = utc_timestamp()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "updated_at": self._updated_at,
                "recent_cards": list(self._cards),
                "recent_leaders": list(self._leaders),
            }


def _build_common_metadata(
    *,
    opportunity_type: str,
    confidence_label: str,
    confidence_score: float,
    evidence_posture: str,
    last_verified_at: str,
    source_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "opportunity_type": opportunity_type,
        "confidence_label": _safe_text(confidence_label) or "no_evidence",
        "confidence_score": round(max(min(_safe_float(confidence_score), 1.0), 0.0), 3),
        "evidence_posture": _safe_text(evidence_posture) or "no_evidence_found",
        "optional": True,
        "source": "verified_dossier",
        "provenance": {
            "context_scope": "local_page_and_recent_context",
            "last_verified_at": _safe_text(last_verified_at),
            "source_hash": _source_hash(source_payload),
        },
    }


def build_contextual_opportunities(
    *,
    card_code: str,
    snapshot: dict[str, Any],
    typed_insights: dict[str, Any],
    context_snapshot: dict[str, Any] | None = None,
    watchlist_context: dict[str, Any] | None = None,
    max_items: int = 3,
) -> list[dict[str, Any]]:
    normalized_code = _safe_text(card_code).upper()
    if not normalized_code:
        return []
    summary = dict(typed_insights.get("card_intelligence_summary") or {})
    usage = dict(typed_insights.get("usage_insight") or {})
    strategy = dict(typed_insights.get("strategy_insight") or {})
    meta = dict(typed_insights.get("meta_insight") or {})
    context = dict(context_snapshot or {})
    recent_cards = [str(code or "").strip().upper() for code in list(context.get("recent_cards") or []) if str(code or "").strip()]
    recent_leaders = [str(code or "").strip().upper() for code in list(context.get("recent_leaders") or []) if str(code or "").strip()]

    opportunities: list[dict[str, Any]] = []
    usage_role = _safe_text(usage.get("role_classification")).lower()
    support_count = _safe_int(usage.get("support_count"))
    leader_name = _safe_text(usage.get("leader_name"))
    leader_code = _safe_text(usage.get("leader_code")).upper()
    trend_label = _safe_text(meta.get("trend_label")).lower()
    confidence_label = _safe_text(summary.get("overall_confidence_label") or usage.get("confidence_label") or meta.get("confidence_label"))
    confidence_score = _safe_float(summary.get("overall_confidence") or usage.get("confidence_score") or meta.get("confidence_score"))
    last_verified_at = _safe_text(snapshot.get("last_verified_at") or snapshot.get("verified_at") or "")

    if usage_role in {"core", "staple"} and support_count > 0:
        opportunities.append(
            {
                **_build_common_metadata(
                    opportunity_type="deck_completion_priority",
                    confidence_label=confidence_label,
                    confidence_score=max(confidence_score, 0.62),
                    evidence_posture=_safe_text(usage.get("evidence_posture")),
                    last_verified_at=last_verified_at,
                    source_payload={"snapshot": snapshot, "usage": usage},
                ),
                "title": "Deck completion priority",
                "summary": (
                    f"I would treat {normalized_code} as a high-priority piece while finishing this shell."
                    if not leader_name
                    else f"I would keep {normalized_code} high on the completion list for {leader_name} builds."
                ),
            }
        )

    if leader_name and trend_label in {"stale", "unknown", "stable"}:
        opportunities.append(
            {
                **_build_common_metadata(
                    opportunity_type="off_meta_leader_suggestion",
                    confidence_label=_safe_text(meta.get("confidence_label") or usage.get("confidence_label")),
                    confidence_score=max(_safe_float(meta.get("confidence_score") or confidence_score), 0.45),
                    evidence_posture=_safe_text(meta.get("evidence_posture") or usage.get("evidence_posture")),
                    last_verified_at=last_verified_at,
                    source_payload={"usage": usage, "meta": meta, "context": {"recent_leaders": recent_leaders}},
                ),
                "title": "Off-meta leader angle",
                "summary": (
                    f"I can keep an eye on quieter {leader_name} lines around this card without overstating current meta share."
                ),
            }
        )

    if watchlist_context:
        current_price = _safe_float(watchlist_context.get("current_price"))
        target_price = _safe_float(watchlist_context.get("target_price"))
        if current_price > 0 and target_price > 0:
            if current_price <= target_price:
                watch_text = "Price is currently at or below the watch target; this is usually when I would check print variant and listing quality carefully."
            else:
                watch_text = "Price is still above the watch target; I would stay patient and re-check on the next local refresh."
        else:
            watch_text = "I can keep this on a cautious watch cadence while local evidence builds."
        opportunities.append(
            {
                **_build_common_metadata(
                    opportunity_type="market_watch_timing",
                    confidence_label=_safe_text(meta.get("confidence_label") or "likely_inference"),
                    confidence_score=max(_safe_float(meta.get("confidence_score")), 0.4),
                    evidence_posture=_safe_text(meta.get("evidence_posture") or "partial_meta_evidence"),
                    last_verified_at=last_verified_at,
                    source_payload={"watchlist": watchlist_context, "meta": meta},
                ),
                "title": "Watch timing note",
                "summary": watch_text,
            }
        )

    top_leader = dict(summary.get("top_leader") or {})
    top_leader_name = _safe_text(top_leader.get("leader_name") or leader_name)
    top_leader_code = _safe_text(top_leader.get("leader_code") or leader_code).upper()
    if top_leader_name or top_leader_code:
        opportunities.append(
            {
                **_build_common_metadata(
                    opportunity_type="related_leader_card_suggestion",
                    confidence_label=_safe_text(usage.get("confidence_label") or confidence_label),
                    confidence_score=max(_safe_float(usage.get("confidence_score") or confidence_score), 0.5),
                    evidence_posture=_safe_text(usage.get("evidence_posture")),
                    last_verified_at=last_verified_at,
                    source_payload={"summary": summary, "usage": usage, "recent_leaders": recent_leaders},
                ),
                "title": "Related leader angle",
                "summary": (
                    f"I can queue a related pass around {top_leader_name or top_leader_code} next."
                ),
            }
        )

    role_purpose = _safe_text(strategy.get("role_purpose"))
    if role_purpose:
        opportunities.append(
            {
                **_build_common_metadata(
                    opportunity_type="budget_substitute_hint",
                    confidence_label=_safe_text(strategy.get("confidence_label") or confidence_label),
                    confidence_score=max(_safe_float(strategy.get("confidence_score")), 0.42),
                    evidence_posture=_safe_text(strategy.get("evidence_posture")),
                    last_verified_at=last_verified_at,
                    source_payload={"strategy": strategy, "usage": usage},
                ),
                "title": "Budget path hint",
                "summary": (
                    "If budget is tight, I can target substitutes that preserve the same job in the list before chasing exact print upgrades."
                ),
            }
        )

    deduped: list[dict[str, Any]] = []
    seen_types: set[str] = set()
    for item in opportunities:
        kind = _safe_text(item.get("opportunity_type"))
        if not kind or kind in seen_types:
            continue
        seen_types.add(kind)
        deduped.append(item)
        if len(deduped) >= max(1, int(max_items or 3)):
            break
    return deduped
