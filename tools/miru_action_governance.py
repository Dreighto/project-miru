from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tools.miru_ai_onepiece import normalize_card_code
from tools.miru_dossier_store import MiruDossierStore
from tools.miru_learning_engine import MiruLearningEngine
from tools.miru_project_sync import (
    DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    DEFAULT_DECK_INTEL_DB_PATH,
    DEFAULT_INCREMENTAL_SYNC_LIMIT,
    DEFAULT_PROJECT_DB_PATH,
    DEFAULT_PROJECT_PRICES_PATH,
    DEFAULT_RULES_DB_PATH,
    DEFAULT_RUNTIME_DOSSIER_DB_PATH,
    connect_catalog_db,
    ensure_catalog_sync_schema,
    load_miru_card_insight_status,
    plan_worktree_card_insight_sync,
    run_worktree_card_insight_sync,
)
from tools.miru_source_registry import build_source_registry


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ACTION_METADATA_KEY = "miru_action_governance"
PUBLICATION_METADATA_KEY = "miru_publication_readiness"
REVIEW_QUEUE_METADATA_KEY = "miru_review_queue"
STAGING_METADATA_KEY = "miru_publication_stage"
BATCH_METADATA_KEY = "miru_publication_batches"
PUBLICATION_RELEASE_METADATA_KEY = "miru_publication_release"
REVALIDATION_METADATA_KEY = "miru_revalidation_planning"
FALLBACK_INSIGHT_TEXT = "Not enough verified data yet."
DEFAULT_READINESS_BATCH_LIMIT = 60
MAX_EXECUTION_LIMIT = 80
APPROVAL_STATES = {"", "pending_review", "approved_for_candidate", "rejected", "deferred", "superseded"}
PROMOTION_STATES = {"", "candidate_only", "review_approved_candidate", "blocked_from_promotion", "deferred"}
STAGE_STATES = {"", "unstaged", "staged_candidate", "staged_batch_member", "blocked_from_staging", "removed_from_stage"}
BATCH_STATUSES = {"", "draft", "review_ready", "mixed_state", "blocked", "archived"}
CANDIDATE_SCORE_BANDS = ("blocked", "watch", "solid", "strong", "elite")
BATCH_QUALITY_BANDS = ("blocked", "weak", "mixed", "strong", "elite")
PUBLISH_STATUSES = ("publish_ready", "publish_requires_review", "publish_deferred", "publish_blocked")
BATCH_PUBLISH_STATUSES = (
    "publish_ready_batch",
    "publish_review_required_batch",
    "publish_mixed_batch",
    "publish_blocked_batch",
)
DOSSIER_GAP_CLASSES = (
    "stale_dossier",
    "thin_source_support",
    "weak_provenance",
    "missing_usage_meta",
    "missing_rules_legality",
    "market_only",
    "partial_but_promising",
    "ready_for_revalidation",
    "stable",
)
REVALIDATION_STATUSES = (
    "recheck_soon",
    "recheck_later",
    "hold",
    "escalate_review",
    "stable_enough",
)
COVERAGE_VALUE_BANDS = ("low_value", "medium_value", "high_value")
EXPANSION_OBJECTIVES = (
    "usage_meta_fill",
    "source_depth_fill",
    "leader_profile_expand",
    "legality_recheck",
    "stale_refresh",
)
NEW_APPROVED_SOURCE_SUPPORT_LANES = (
    "official-deck-features",
    "official-rules-faq",
    "official-restriction-notices",
    "official-errata-cards",
)
OPTCG_API_SOURCE_LANE = "optcg-api"
GOVERNED_AUTONOMY_PHASE1_SEED_CANDIDATES: tuple[dict[str, str], ...] = (
    {
        "url": "https://en.onepiece-cardgame.com/cardlist/",
        "title": "Official ONE PIECE CARD GAME Card List",
        "notes": "Official Bandai card catalog page.",
    },
    {
        "url": "https://en.onepiece-cardgame.com/rules/restriction/",
        "title": "Official ONE PIECE restriction notice",
        "notes": "Official legality and restriction notice page.",
    },
    {
        "url": "https://optcgapi.com/api/sets/card/OP01-001/",
        "title": "OPTCG API card endpoint",
        "notes": "Structured public API endpoint for card lookup.",
    },
    {
        "url": "https://onepiecetopdecks.com/deck-list/meta-report/",
        "title": "Community meta deck report",
        "notes": "Public community decklist and meta analysis coverage.",
    },
    {
        "url": "https://discord.com/channels/onepiece-card-game/decklist-discussion",
        "title": "Community decklist Discord",
        "notes": "Login-gated community discussion surface.",
    },
    {
        "url": "https://www.ebay.com/sch/i.html?_nkw=one+piece+card+op01",
        "title": "Marketplace card listings",
        "notes": "Auction and seller listing coverage.",
    },
)

ACTION_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "action_id": "observe.runtime_probe_refresh",
        "title": "Refresh runtime probe",
        "description": "Refresh the current Dev/runtime health snapshot from the existing local probe.",
        "category": "observe",
        "allowed_mode": "read_only",
        "risk_level": "low",
        "required_preconditions": ("dev_runtime_online",),
        "target_scope": "runtime",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "observe.dev_status_refresh",
        "title": "Refresh governed status summary",
        "description": "Re-evaluate the current control, sync, and publication-readiness state without mutating storefront behavior.",
        "category": "observe",
        "allowed_mode": "read_only",
        "risk_level": "low",
        "required_preconditions": ("dev_runtime_online",),
        "target_scope": "dev_status",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "sync.incremental_priority_batch",
        "title": "Run bounded priority sync",
        "description": "Trigger the existing bounded incremental dossier/projection sync against the highest-priority pending candidates.",
        "category": "sync",
        "allowed_mode": "safe_action",
        "risk_level": "medium",
        "required_preconditions": ("dev_runtime_online", "catalog_db_writable", "runtime_dossier_readable", "sync_backlog_present"),
        "target_scope": "card_catalog_projection",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "project.refresh_publication_readiness",
        "title": "Refresh publication readiness",
        "description": "Refresh bounded publication-readiness classifications from existing dossier-backed projection rows.",
        "category": "project",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("catalog_db_writable", "readiness_candidates_present"),
        "target_scope": "publication_readiness",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "revalidate.plan_revalidation_batch",
        "title": "Plan bounded revalidation batch",
        "description": "Classify the highest-value coverage gaps, refresh dossier-backed readiness where justified, and persist a bounded revalidation plan.",
        "category": "verify",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("catalog_db_writable", "revalidation_candidates_present"),
        "target_scope": "coverage_revalidation",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "revalidate.refresh_partial_candidate",
        "title": "Refresh partial candidate",
        "description": "Refresh one partial-but-promising card through the existing dossier-backed readiness path without mutating storefront behavior.",
        "category": "verify",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("card_target_present", "dossier_evidence_present"),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "revalidate.verify_stale_candidate",
        "title": "Verify stale candidate",
        "description": "Re-check one stale dossier-backed candidate and refresh its bounded governance metadata from existing stored evidence.",
        "category": "verify",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("card_target_present", "dossier_evidence_present"),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "revalidate.refresh_rules_sensitive_candidate",
        "title": "Refresh rules-sensitive candidate",
        "description": "Refresh one legality-sensitive or rules-sensitive card through the existing dossier-backed readiness path without mutating storefront behavior.",
        "category": "verify",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("card_target_present", "dossier_evidence_present"),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "revalidate.refresh_usage_meta_candidate",
        "title": "Refresh usage-meta candidate",
        "description": "Refresh one usage-heavy card through the existing dossier-backed readiness path without mutating storefront behavior.",
        "category": "verify",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("card_target_present", "dossier_evidence_present"),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "verify.card_projection",
        "title": "Verify card projection",
        "description": "Build the canonical dossier, strict insight, and publication-readiness view for one card from stored evidence only.",
        "category": "verify",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("card_target_present", "dossier_evidence_present"),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "review.publish_candidate_summary",
        "title": "Generate publish-candidate summary",
        "description": "Produce a future publication-readiness summary for one card without mutating storefront code or data surfaces.",
        "category": "publish_candidate",
        "allowed_mode": "safe_action",
        "risk_level": "medium",
        "required_preconditions": ("card_target_present", "dossier_evidence_present"),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "review.score_candidate",
        "title": "Score publication candidate",
        "description": "Build a deterministic publication-value score and rationale for one dossier-backed candidate without mutating storefront behavior.",
        "category": "publish_candidate",
        "allowed_mode": "read_only",
        "risk_level": "low",
        "required_preconditions": ("card_target_present", "dossier_evidence_present"),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "publish.evaluate_candidate",
        "title": "Evaluate final publish eligibility",
        "description": "Run the final backend-only publish gate for one dossier-backed candidate and record whether it is publish-ready, review-bound, deferred, or blocked.",
        "category": "publish_candidate",
        "allowed_mode": "read_only",
        "risk_level": "low",
        "required_preconditions": ("card_target_present", "dossier_evidence_present"),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "publish.generate_payload",
        "title": "Generate publication payload contract",
        "description": "Produce the backend-only publication payload Miru would hand to a future storefront bridge without mutating any UI surface.",
        "category": "publish_candidate",
        "allowed_mode": "read_only",
        "risk_level": "low",
        "required_preconditions": ("card_target_present", "dossier_evidence_present"),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "publish.validate_before_release",
        "title": "Validate final publish gate",
        "description": "Run the strict final gate for a candidate or prepared batch and explain whether a future release step must be allowed, reviewed, deferred, or blocked.",
        "category": "publish_candidate",
        "allowed_mode": "read_only",
        "risk_level": "low",
        "required_preconditions": (),
        "target_scope": "publication_target",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "review.mark_review_required",
        "title": "Mark review required",
        "description": "Record that a card or action path needs human review before any future publish-oriented step.",
        "category": "review",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("card_target_present",),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "review.approve_candidate",
        "title": "Approve review candidate",
        "description": "Record an approval decision for a dossier-backed publication candidate without mutating storefront behavior.",
        "category": "review",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("card_target_present", "dossier_evidence_present"),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "review.reject_candidate",
        "title": "Reject review candidate",
        "description": "Record that a candidate should not be promoted toward future publication until evidence or review changes.",
        "category": "review",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("card_target_present", "dossier_evidence_present"),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "review.defer_candidate",
        "title": "Defer review candidate",
        "description": "Record that a candidate should stay deferred while Miru waits for stronger evidence or operator attention.",
        "category": "review",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("card_target_present", "dossier_evidence_present"),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "stage.stage_candidate",
        "title": "Stage approved candidate",
        "description": "Place an approved publication candidate into backend-only staging without mutating storefront behavior.",
        "category": "project",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("card_target_present", "dossier_evidence_present"),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "stage.unstage_candidate",
        "title": "Remove candidate from stage",
        "description": "Remove a staged publication candidate from backend-only staging while preserving governance history.",
        "category": "project",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("card_target_present",),
        "target_scope": "single_card",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "batch.create_publication_batch",
        "title": "Create publication-prep batch",
        "description": "Create a bounded backend-only batch from staged publication candidates for later reviewed promotion prep.",
        "category": "project",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("catalog_db_writable", "staged_candidates_present"),
        "target_scope": "publication_batch",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "batch.add_candidate",
        "title": "Add staged candidate to batch",
        "description": "Add an approved staged candidate to a backend publication-prep batch without touching storefront content.",
        "category": "project",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("card_target_present", "dossier_evidence_present", "batch_target_present"),
        "target_scope": "publication_batch",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "batch.remove_candidate",
        "title": "Remove candidate from batch",
        "description": "Remove a staged candidate from a backend publication-prep batch while preserving stage history.",
        "category": "project",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("card_target_present", "batch_target_present"),
        "target_scope": "publication_batch",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "batch.generate_summary",
        "title": "Generate publication batch summary",
        "description": "Build a backend-only governance summary for a staged publication-prep batch.",
        "category": "publish_candidate",
        "allowed_mode": "read_only",
        "risk_level": "low",
        "required_preconditions": ("batch_target_present",),
        "target_scope": "publication_batch",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "batch.recommend_split",
        "title": "Recommend batch split",
        "description": "Analyze a publication-prep batch and propose cleaner backend-only batch groupings when the current mix is risky or uneven.",
        "category": "publish_candidate",
        "allowed_mode": "read_only",
        "risk_level": "low",
        "required_preconditions": ("batch_target_present",),
        "target_scope": "publication_batch",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "publish.evaluate_batch",
        "title": "Evaluate batch publish readiness",
        "description": "Run the final backend-only publish gate for a prepared publication batch and explain whether the batch is ready, mixed, review-bound, or blocked.",
        "category": "publish_candidate",
        "allowed_mode": "read_only",
        "risk_level": "low",
        "required_preconditions": ("batch_target_present",),
        "target_scope": "publication_batch",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "batch.mark_review_ready",
        "title": "Refresh batch review state",
        "description": "Recompute a backend publication-prep batch so its stored status matches the current staged members and guardrails.",
        "category": "project",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("batch_target_present",),
        "target_scope": "publication_batch",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "batch.archive",
        "title": "Archive publication batch",
        "description": "Archive a backend publication-prep batch without mutating storefront behavior or deleting history.",
        "category": "project",
        "allowed_mode": "safe_action",
        "risk_level": "low",
        "required_preconditions": ("batch_target_present",),
        "target_scope": "publication_batch",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "route.worker_handoff_prompt",
        "title": "Generate worker handoff prompt",
        "description": "Generate a deterministic backend worker handoff recommendation grounded in the current governed state.",
        "category": "route_worker",
        "allowed_mode": "read_only",
        "risk_level": "low",
        "required_preconditions": ("dev_runtime_online",),
        "target_scope": "worker_route",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "route.worker_handoff_for_gap_cluster",
        "title": "Generate gap-cluster handoff",
        "description": "Generate a deterministic backend worker handoff recommendation for the highest-value coverage gap cluster.",
        "category": "route_worker",
        "allowed_mode": "read_only",
        "risk_level": "low",
        "required_preconditions": ("dev_runtime_online",),
        "target_scope": "worker_route",
        "preferred_worker": "Codex",
    },
    {
        "action_id": "publish.storefront_mutation",
        "title": "Apply storefront mutation",
        "description": "Mutate Project Miru storefront behavior or rendering from the backend action layer.",
        "category": "publish",
        "allowed_mode": "safe_action",
        "risk_level": "high",
        "required_preconditions": (),
        "target_scope": "project_miru_storefront",
        "preferred_worker": "Codex",
    },
)


def _utc_now_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json_dump(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=True, sort_keys=True)
    except Exception:
        return "{}"


def _json_load(payload: Any, default: Any) -> Any:
    text = str(payload or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


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


def _normalize_gap_class(value: Any) -> str:
    gap = str(value or "").strip().lower()
    return gap if gap in DOSSIER_GAP_CLASSES else ""


def _normalize_revalidation_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    return status if status in REVALIDATION_STATUSES else ""


def _normalize_coverage_value_band(value: Any) -> str:
    band = str(value or "").strip().lower()
    return band if band in COVERAGE_VALUE_BANDS else ""


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt, expected_len in (
        ("%Y-%m-%dT%H:%M:%SZ", 20),
        ("%Y-%m-%dT%H:%M:%S", 19),
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d", 10),
    ):
        try:
            parsed = datetime.strptime(text[:expected_len], fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _age_days(value: Any) -> float:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return 9999.0
    now = datetime.now(timezone.utc)
    return max((now - parsed).total_seconds() / 86400.0, 0.0)


def _normalize_approval_state(value: Any) -> str:
    state = str(value or "").strip().lower()
    return state if state in APPROVAL_STATES else ""


def _normalize_promotion_state(value: Any) -> str:
    state = str(value or "").strip().lower()
    return state if state in PROMOTION_STATES else ""


def _normalize_stage_state(value: Any) -> str:
    state = str(value or "").strip().lower()
    return state if state in STAGE_STATES else ""


def _normalize_batch_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    return status if status in BATCH_STATUSES else ""


def _normalize_publish_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    return status if status in PUBLISH_STATUSES else ""


def _normalize_batch_publish_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    return status if status in BATCH_PUBLISH_STATUSES else ""


def _normalize_code(card_code: str) -> str:
    normalized = normalize_card_code(card_code or "")
    return str(normalized.get("canonical_code") or card_code or "").strip().upper()


def build_action_registry() -> list[dict[str, Any]]:
    return [dict(entry) for entry in ACTION_REGISTRY]


def _store_metadata(conn: sqlite3.Connection, *, sync_key: str, payload: dict[str, Any]) -> None:
    now = _utc_now_timestamp()
    conn.execute(
        """
        INSERT INTO miru_sync_metadata (sync_key, payload_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(sync_key) DO UPDATE SET
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (sync_key, _json_dump(payload), now),
    )


def _load_metadata(conn: sqlite3.Connection, *, sync_key: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT payload_json, updated_at FROM miru_sync_metadata WHERE sync_key = ? LIMIT 1",
        (sync_key,),
    ).fetchone()
    if row is None:
        return {}
    payload = _json_load(row["payload_json"], {})
    if isinstance(payload, dict):
        payload.setdefault("updated_at", str(row["updated_at"] or "").strip())
        return payload
    return {"updated_at": str(row["updated_at"] or "").strip()}


def _load_projection_row(conn: sqlite3.Connection, card_code: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            c.canonical_code AS canonical_code,
            c.card_name AS card_name,
            c.set_code AS set_code,
            c.set_name AS set_name,
            card_id,
            role_label,
            role_summary,
            deck_usage_summary,
            price_value,
            price_source,
            price_trend_note,
            meta_relevance_score,
            confidence_score,
            source_summary,
            last_verified_at,
            legality_state,
            legality_note,
            rulings_summary,
            rulings_sources_json,
            usage_profile_json,
            section_confidence_json,
            source_agreement_json,
            projection_sections_json,
            projection_source_updated_at,
            last_sync_reason,
            last_sync_mode,
            last_priority_score,
            last_priority_context_json,
            publication_readiness,
            publication_guardrail,
            publication_rationale,
            publication_updated_at,
            approval_state,
            promotion_state,
            promotion_rationale,
            promotion_updated_at,
            publication_candidate_score,
            publication_candidate_score_band,
            publication_candidate_profile,
            publication_candidate_reasons_json,
            publication_candidate_risks_json,
            publication_candidate_updated_at,
            publish_status,
            publish_updated_at,
            dossier_gap_class,
            dossier_gap_tags_json,
            coverage_value_score,
            coverage_value_band,
            coverage_gap_summary,
            revalidation_status,
            revalidation_reason,
            revalidation_priority_score,
            revalidation_priority_bucket,
            revalidation_updated_at
        FROM card_intelligence ci
        JOIN cards c
            ON c.id = ci.card_id
        WHERE c.canonical_code = ?
        LIMIT 1
        """,
        (card_code,),
    ).fetchone()
    if row is None:
        return {}
    out = dict(row)
    out["rulings_sources"] = _json_load(out.get("rulings_sources_json"), [])
    out["usage_profile"] = _json_load(out.get("usage_profile_json"), {})
    out["section_confidence"] = _json_load(out.get("section_confidence_json"), {})
    out["source_agreement"] = _json_load(out.get("source_agreement_json"), {})
    out["projection_sections"] = _json_load(out.get("projection_sections_json"), [])
    out["priority_context"] = _json_load(out.get("last_priority_context_json"), {})
    out["publication_candidate_reasons"] = _json_load(out.get("publication_candidate_reasons_json"), [])
    out["publication_candidate_risks"] = _json_load(out.get("publication_candidate_risks_json"), [])
    out["dossier_gap_tags"] = _json_load(out.get("dossier_gap_tags_json"), [])
    return out


def _load_best_insight_row(conn: sqlite3.Connection, card_code: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            insight_type,
            insight_text,
            confidence,
            quality_tier,
            source_ref,
            leader_code,
            used_sections_json,
            sync_reason,
            source_updated_at,
            updated_at
        FROM miru_card_insights
        WHERE card_id = ?
        ORDER BY confidence DESC, updated_at DESC, insight_type ASC
        LIMIT 1
        """,
        (card_code,),
    ).fetchone()
    if row is None:
        return {}
    out = dict(row)
    out["used_sections"] = _json_load(out.get("used_sections_json"), [])
    return out


def _build_store(
    *,
    canonical_dossier_db_path: Path,
    rules_db_path: Path,
    deck_intel_db_path: Path,
) -> MiruDossierStore:
    store = MiruDossierStore(canonical_dossier_db_path)
    store.rules_db_path = Path(rules_db_path)
    store.deck_intel_db_path = Path(deck_intel_db_path)
    return store


def _strong_sections_from_dossier(dossier: dict[str, Any]) -> list[str]:
    sections: list[str] = []
    if str(dossier.get("rulings_summary") or "").strip():
        sections.append("rulings")
    if str(dossier.get("legality_state") or "").strip() or str(dossier.get("legality_note") or "").strip():
        sections.append("legality")
    if str(dossier.get("deck_usage_summary") or "").strip() or list(dossier.get("top_leaders_used_in") or []):
        sections.append("usage_meta")
    if str(dossier.get("gameplay_role") or "").strip():
        sections.append("gameplay_role")
    if dossier.get("price_low") not in (None, "") or str(dossier.get("price_trend_note") or "").strip():
        sections.append("market")
    deduped: list[str] = []
    seen: set[str] = set()
    for section in sections:
        if section not in seen:
            seen.add(section)
            deduped.append(section)
    return deduped


def evaluate_publication_readiness(
    *,
    card_code: str,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
    dossier: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_code = _normalize_code(card_code)
    if not normalized_code:
        return {
            "card_code": "",
            "readiness_state": "blocked_by_guardrail",
            "guardrail_label": "Blocked",
            "risk_level": "high",
            "confidence": 0.0,
            "rationale": "No canonical card target was provided.",
            "strong_sections": [],
            "used_sections": [],
        }

    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    with closing(connect_catalog_db(project_path)) as conn:
        projection_row = _load_projection_row(conn, normalized_code)
        stored_insight = _load_best_insight_row(conn, normalized_code)

    store = _build_store(
        canonical_dossier_db_path=Path(canonical_dossier_db_path),
        rules_db_path=Path(rules_db_path),
        deck_intel_db_path=Path(deck_intel_db_path),
    )
    canonical_dossier = dict(dossier or store.build_card_dossier(normalized_code, prices_path=prices_path))
    generated_insight = dict(store.generate_card_insight(normalized_code, prices_path=prices_path, dossier=canonical_dossier) or {})
    effective_insight = stored_insight if stored_insight else generated_insight

    insight_text = str(effective_insight.get("text") or effective_insight.get("insight_text") or "").strip()
    if not insight_text:
        insight_text = str(generated_insight.get("text") or "").strip()
    used_sections = list(effective_insight.get("used_sections") or generated_insight.get("used_sections") or [])
    confidence = max(
        _safe_float(canonical_dossier.get("confidence_score")),
        _safe_float(projection_row.get("confidence_score")),
        _safe_float(effective_insight.get("confidence")),
    )
    source_agreement = dict(
        projection_row.get("source_agreement")
        or canonical_dossier.get("source_agreement")
        or {}
    )
    agreement_level = str(source_agreement.get("agreement_level") or "").strip().lower()
    source_count = _safe_int(source_agreement.get("source_count") or len(canonical_dossier.get("sources") or []))
    strong_sections = _strong_sections_from_dossier(canonical_dossier)
    projection_sections = [str(item).strip() for item in list(projection_row.get("projection_sections") or []) if str(item).strip()]
    legality_note = str(canonical_dossier.get("legality_note") or "").strip()
    price_trend_note = str(canonical_dossier.get("price_trend_note") or "").strip().lower()
    has_strict_insight = bool(insight_text) and insight_text != FALLBACK_INSIGHT_TEXT
    market_only = bool(strong_sections) and set(strong_sections) == {"market"}
    has_legality = "legality" in strong_sections
    has_rules = "rulings" in strong_sections
    has_usage = "usage_meta" in strong_sections or "gameplay_role" in strong_sections
    single_point_market = market_only and "single stored watch-price point only" in price_trend_note

    readiness_state = "not_ready"
    guardrail_label = "Read-only"
    risk_level = "low"
    rationale = "Projection exists, but the evidence is still too limited for future site surfacing."
    review_reason = "insufficient_evidence"
    recommended_next_step = "Keep expanding dossier-backed evidence before any publication-oriented review."
    queue_worthy = False

    if agreement_level == "conflict":
        readiness_state = "blocked_by_guardrail"
        guardrail_label = "Blocked"
        risk_level = "high"
        rationale = "Source agreement is in conflict, so Miru should refuse future publish-oriented steps until the dossier is reconciled."
        review_reason = "source_conflict"
        recommended_next_step = "Reconcile conflicting sources before considering any publish-oriented step."
        queue_worthy = True
    elif not strong_sections or not has_strict_insight:
        readiness_state = "blocked_by_guardrail"
        guardrail_label = "Blocked"
        risk_level = "medium"
        rationale = "The card does not yet have enough dossier-backed sections for a safe publish-oriented recommendation."
        review_reason = "insufficient_dossier_evidence"
        recommended_next_step = "Wait for stronger dossier-backed evidence before sending this toward review."
    elif confidence < 0.65 or source_count <= 0:
        readiness_state = "blocked_by_guardrail"
        guardrail_label = "Blocked"
        risk_level = "medium"
        rationale = "Confidence or provenance is too weak for future publication readiness."
        review_reason = "weak_provenance"
        recommended_next_step = "Strengthen provenance and source support before any publish-oriented review."
        queue_worthy = bool(strong_sections)
    elif has_legality and ("effective" in legality_note.lower() or "upcoming" in legality_note.lower() or agreement_level in {"partial", "single_source"}):
        readiness_state = "ready_for_review"
        guardrail_label = "Review required"
        risk_level = "medium"
        rationale = "Legality-sensitive dossier content should receive human review before Miru treats it as publish-ready."
        review_reason = "legality_sensitive"
        recommended_next_step = "Human review should verify the legality and rulings context before any publish-candidate step."
        queue_worthy = True
    elif market_only and single_point_market:
        readiness_state = "not_ready"
        guardrail_label = "Read-only"
        risk_level = "low"
        rationale = "Only a sparse watch-price signal is stored, so Miru should keep this as a market observation rather than a publish candidate."
        review_reason = "market_watch_only"
        recommended_next_step = "Keep monitoring market evidence; do not send this to review yet."
    elif confidence >= 0.85 and agreement_level != "conflict" and source_count >= 1 and projection_sections and (has_usage or has_rules or has_legality or market_only):
        readiness_state = "ready_for_publish_candidate"
        guardrail_label = "Safe action"
        risk_level = "low"
        rationale = "The card has a strict dossier-backed insight, projected sections, and enough confidence for a future publish-candidate review step."
        review_reason = "publish_candidate"
        recommended_next_step = "Safe to generate a publish-candidate summary; storefront publication still requires approval."
    else:
        readiness_state = "ready_for_review"
        guardrail_label = "Review required"
        risk_level = "medium"
        rationale = "The dossier is meaningful, but Miru should keep a human review step before any future site-facing use."
        review_reason = "guarded_publish_review"
        recommended_next_step = "Human review should confirm the dossier-backed claim set before any publish-oriented step."
        queue_worthy = True

    return {
        "card_code": normalized_code,
        "readiness_state": readiness_state,
        "guardrail_label": guardrail_label,
        "risk_level": risk_level,
        "confidence": round(confidence, 3),
        "rationale": rationale,
        "strong_sections": strong_sections,
        "used_sections": [str(item).strip() for item in used_sections if str(item).strip()],
        "projection_sections": projection_sections,
        "agreement_level": agreement_level or "unknown",
        "source_count": source_count,
        "insight_text": insight_text,
        "review_reason": review_reason,
        "recommended_next_step": recommended_next_step,
        "queue_worthy": queue_worthy,
        "source_updated_at": str(
            canonical_dossier.get("source_updated_at")
            or projection_row.get("projection_source_updated_at")
            or effective_insight.get("source_updated_at")
            or ""
        ).strip(),
    }


def _upsert_publication_readiness(conn: sqlite3.Connection, readiness: dict[str, Any]) -> None:
    approval_state = _normalize_approval_state(readiness.get("approval_state"))
    promotion_state = _normalize_promotion_state(readiness.get("promotion_state"))
    conn.execute(
        """
        UPDATE card_intelligence
        SET publication_readiness = ?,
            publication_guardrail = ?,
            publication_rationale = ?,
            publication_updated_at = ?,
            approval_state = ?,
            promotion_state = ?,
            promotion_rationale = ?,
            promotion_updated_at = ?,
            publication_candidate_score = ?,
            publication_candidate_score_band = ?,
            publication_candidate_profile = ?,
            publication_candidate_reasons_json = ?,
            publication_candidate_risks_json = ?,
            publication_candidate_updated_at = ?,
            publish_status = ?,
            publish_reasons_json = ?,
            publish_risks_json = ?,
            publish_payload_json = ?,
            publish_updated_at = ?
        WHERE card_id IN (
            SELECT id
            FROM cards
            WHERE canonical_code = ?
        )
        """,
        (
            str(readiness.get("readiness_state") or "").strip(),
            str(readiness.get("guardrail_label") or "").strip(),
            str(readiness.get("rationale") or "").strip(),
            _utc_now_timestamp(),
            approval_state,
            promotion_state,
            str(readiness.get("promotion_rationale") or "").strip(),
            _utc_now_timestamp(),
            _safe_float(readiness.get("candidate_score")),
            str(readiness.get("candidate_score_band") or "").strip(),
            str(readiness.get("candidate_profile") or "").strip(),
            _json_dump(readiness.get("candidate_score_reasons") or []),
            _json_dump(readiness.get("candidate_risk_factors") or []),
            _utc_now_timestamp(),
            _normalize_publish_status(readiness.get("publish_status")),
            _json_dump(readiness.get("publish_reasons") or []),
            _json_dump(readiness.get("publish_risks") or []),
            _json_dump(readiness.get("publication_payload") or {}),
            _utc_now_timestamp(),
            str(readiness.get("card_code") or "").strip().upper(),
        ),
    )


def _load_review_queue_row(conn: sqlite3.Connection, card_code: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            item_key,
            target_id,
            readiness_state,
            review_reason,
            guardrail_label,
            confidence_score,
            risk_level,
            recommended_next_step,
            summary_text,
            supporting_sections_json,
            payload_json,
            status,
            approval_state,
            promotion_state,
            approval_note,
            decision_source,
            resolution_note,
            created_at,
            updated_at,
            resolved_at,
            approval_updated_at
        FROM miru_review_queue
        WHERE item_key = ?
        LIMIT 1
        """,
        (_review_queue_item_key(card_code),),
    ).fetchone()
    if row is None:
        return {}
    out = dict(row)
    out["supporting_sections"] = _json_load(out.get("supporting_sections_json"), [])
    out["payload"] = _json_load(out.get("payload_json"), {})
    return out


def _derive_promotion_fields(
    *,
    readiness_state: str,
    approval_state: str,
    queue_status: str,
    guardrail_label: str,
) -> tuple[str, str]:
    readiness_key = str(readiness_state or "").strip().lower()
    approval_key = _normalize_approval_state(approval_state)
    queue_key = str(queue_status or "").strip().lower()
    guardrail = str(guardrail_label or "").strip().lower()

    if approval_key == "approved_for_candidate":
        return (
            "review_approved_candidate",
            "A human review approval is stored, so this item is prepared for a future publish candidate step.",
        )
    if approval_key in {"rejected", "superseded"}:
        return (
            "blocked_from_promotion",
            "A recorded review decision currently blocks promotion for this item.",
        )
    if approval_key == "deferred" or queue_key == "deferred":
        return (
            "deferred",
            "Promotion is currently deferred pending stronger evidence or a later review decision.",
        )
    if readiness_key in {"blocked_by_guardrail", "not_ready"} or guardrail == "blocked":
        return (
            "blocked_from_promotion",
            "Current readiness guardrails block this item from future promotion.",
        )
    if readiness_key in {"ready_for_publish_candidate", "ready_for_review"}:
        return (
            "candidate_only",
            "The dossier supports candidate preparation, but no approval for future promotion is stored yet.",
        )
    return (
        "",
        "",
    )


def _count_readiness_candidates(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM card_intelligence
        WHERE trim(coalesce(projection_sections_json, '')) != ''
          AND (
            trim(coalesce(publication_readiness, '')) = ''
            OR trim(coalesce(publication_updated_at, '')) = ''
            OR (
                trim(coalesce(projection_source_updated_at, '')) != ''
                AND trim(coalesce(publication_updated_at, '')) < trim(coalesce(projection_source_updated_at, ''))
            )
          )
        """
    ).fetchone()
    return _safe_int(row[0] if row is not None else 0)


def _summarize_publication_readiness_counts(conn: sqlite3.Connection) -> dict[str, Any]:
    counts = {
        "not_ready": 0,
        "ready_for_review": 0,
        "ready_for_publish_candidate": 0,
        "blocked_by_guardrail": 0,
        "unclassified": 0,
    }
    for row in conn.execute(
        """
        SELECT publication_readiness, COUNT(*) AS row_count
        FROM card_intelligence
        GROUP BY publication_readiness
        """
    ).fetchall():
        key = str(row["publication_readiness"] or "").strip()
        if not key:
            counts["unclassified"] += _safe_int(row["row_count"])
        else:
            counts[key] = counts.get(key, 0) + _safe_int(row["row_count"])
    return {
        "counts": counts,
        "remaining_candidate_count": _count_readiness_candidates(conn),
    }


def refresh_publication_readiness_batch(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
    limit: int = DEFAULT_READINESS_BATCH_LIMIT,
) -> dict[str, Any]:
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    bounded_limit = max(1, min(_safe_int(limit or DEFAULT_READINESS_BATCH_LIMIT), MAX_EXECUTION_LIMIT))
    with closing(connect_catalog_db(project_path)) as conn:
        counts_before = _summarize_publication_readiness_counts(conn).get("counts") or {}
        rows = conn.execute(
            """
            SELECT
                c.canonical_code AS card_code,
                last_sync_reason,
                confidence_score,
                last_priority_score
            FROM card_intelligence ci
            JOIN cards c
                ON c.id = ci.card_id
            WHERE trim(coalesce(projection_sections_json, '')) != ''
              AND (
                trim(coalesce(publication_readiness, '')) = ''
                OR trim(coalesce(publication_updated_at, '')) = ''
                OR (
                    trim(coalesce(projection_source_updated_at, '')) != ''
                    AND trim(coalesce(publication_updated_at, '')) < trim(coalesce(projection_source_updated_at, ''))
                )
              )
            ORDER BY
                CASE
                    WHEN trim(coalesce(ci.rulings_summary, '')) != '' OR trim(coalesce(ci.legality_note, '')) != '' THEN 0
                    WHEN trim(coalesce(ci.deck_usage_summary, '')) != '' OR trim(coalesce(ci.role_summary, '')) != '' THEN 1
                    WHEN ci.price_value IS NOT NULL OR trim(coalesce(ci.price_trend_note, '')) != '' THEN 2
                    ELSE 3
                END,
                COALESCE(last_priority_score, 0) DESC,
                COALESCE(confidence_score, 0) DESC,
                c.canonical_code ASC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
        selected_codes = [str(row["card_code"] or "").strip().upper() for row in rows if str(row["card_code"] or "").strip()]
        updated: list[dict[str, Any]] = []
        queue_updates: list[dict[str, Any]] = []
        for card_code in selected_codes:
            summary = build_publication_candidate_summary(
                card_code=card_code,
                project_db_path=project_path,
                canonical_dossier_db_path=canonical_dossier_db_path,
                rules_db_path=rules_db_path,
                deck_intel_db_path=deck_intel_db_path,
                prices_path=prices_path,
            )
            _upsert_publication_readiness(conn, summary)
            queue_update = _upsert_review_queue_entry(conn, summary=summary, forced=False)
            queue_updates.append(queue_update)
            updated.append(
                {
                    "card_code": summary["card_code"],
                    "readiness_state": summary["readiness_state"],
                    "guardrail_label": summary["guardrail_label"],
                    "confidence": summary["confidence"],
                    "rationale": summary["rationale"],
                    "review_reason": summary["review_reason"],
                    "queue_action": queue_update.get("action"),
                }
            )
        summary = _summarize_publication_readiness_counts(conn)
        conn.commit()
        queue_summary = load_review_queue_summary(project_db_path=project_path, limit=8)
        queue_counts = dict(queue_summary.get("counts") or {})
        approval_counts = dict(queue_summary.get("approval_counts") or {})
        _store_metadata(
            conn,
            sync_key=PUBLICATION_METADATA_KEY,
            payload={
                "updated_at": _utc_now_timestamp(),
                "selected_count": len(selected_codes),
                "selected_cards": updated,
                "counts_before": counts_before,
                "remaining_candidate_count": _count_readiness_candidates(conn),
                "readiness_counts": summary.get("counts") or {},
                "queue_counts": queue_counts,
                "approval_counts": approval_counts,
                "queue_updates": queue_updates[:12],
            },
        )
        _store_metadata(
            conn,
            sync_key=REVIEW_QUEUE_METADATA_KEY,
            payload={
                "updated_at": _utc_now_timestamp(),
                "source": "publication_readiness_refresh",
                "selected_count": len(selected_codes),
                "counts": queue_counts,
                "approval_counts": approval_counts,
                "queue_updates": queue_updates[:12],
            },
        )
    return {
        "ok": True,
        "selected_count": len(selected_codes),
        "selected_cards": updated,
        "remaining_candidate_count": summary.get("remaining_candidate_count", 0),
        "readiness_counts": summary.get("counts") or {},
        "counts_before": counts_before,
        "queue_counts": queue_counts,
        "approval_counts": approval_counts,
        "queue_updates": queue_updates,
    }


def _publication_target_candidates(
    conn: sqlite3.Connection,
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            c.canonical_code AS card_code,
            publication_readiness,
            publication_guardrail,
            publication_rationale,
            approval_state,
            promotion_state,
            confidence_score,
            publication_candidate_score,
            publication_candidate_score_band,
            publication_candidate_profile,
            last_sync_reason,
            last_priority_score
        FROM card_intelligence ci
        JOIN cards c
            ON c.id = ci.card_id
        WHERE trim(coalesce(publication_readiness, '')) != ''
        ORDER BY
            CASE publication_readiness
                WHEN 'ready_for_publish_candidate' THEN 0
                WHEN 'ready_for_review' THEN 1
                WHEN 'not_ready' THEN 2
                WHEN 'blocked_by_guardrail' THEN 3
                ELSE 4
            END,
            COALESCE(publication_candidate_score, 0) DESC,
            COALESCE(last_priority_score, 0) DESC,
            COALESCE(confidence_score, 0) DESC,
            c.canonical_code ASC
        LIMIT ?
        """,
        (max(1, limit),),
    ).fetchall()
    return [dict(row) for row in rows]


def _truncate_text(value: Any, limit: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def _candidate_score_band(score: float) -> str:
    if score >= 88:
        return "elite"
    if score >= 74:
        return "strong"
    if score >= 58:
        return "solid"
    if score >= 40:
        return "watch"
    return "blocked"


def _batch_quality_band(score: float) -> str:
    if score >= 84:
        return "elite"
    if score >= 70:
        return "strong"
    if score >= 52:
        return "mixed"
    if score >= 34:
        return "weak"
    return "blocked"


def _confidence_level(score: float) -> str:
    if score >= 0.9:
        return "high"
    if score >= 0.75:
        return "medium"
    return "low"


def _contains_any(texts: list[str], needles: tuple[str, ...]) -> bool:
    haystack = " ".join(str(item or "").strip().lower() for item in texts if str(item or "").strip())
    return any(needle in haystack for needle in needles)


def _build_publication_payload_contract(
    *,
    summary: dict[str, Any],
    canonical_dossier: dict[str, Any],
) -> dict[str, Any]:
    confidence = _safe_float(summary.get("confidence"))
    source_agreement = dict(summary.get("source_agreement") or {})
    return {
        "card_id": str(summary.get("card_code") or "").strip().upper(),
        "name": str(summary.get("card_name") or canonical_dossier.get("name") or "").strip(),
        "insight_summary": str(summary.get("insight_text") or summary.get("summary_text") or FALLBACK_INSIGHT_TEXT).strip(),
        "score": round(_safe_float(summary.get("candidate_score")), 3),
        "score_band": str(summary.get("candidate_score_band") or "").strip(),
        "approval_state": _normalize_approval_state(summary.get("approval_state")),
        "promotion_state": _normalize_promotion_state(summary.get("promotion_state")),
        "publish_status": _normalize_publish_status(summary.get("publish_status")),
        "confidence_indicators": {
            "score": round(confidence, 3),
            "confidence_level": _confidence_level(confidence),
            "source_count": _safe_int(summary.get("source_count")),
            "agreement_level": str(source_agreement.get("agreement_level") or "").strip(),
            "last_verified_at": str(summary.get("last_verified_at") or "").strip(),
        },
        "source_attribution": {
            "source_count": _safe_int(summary.get("source_count")),
            "agreement_level": str(source_agreement.get("agreement_level") or "").strip(),
            "source_summary": str(summary.get("source_summary") or "").strip(),
        },
        "supporting_sections": list(summary.get("strong_sections") or []),
        "flags": {
            "review_required": bool(summary.get("review_required")),
            "legality_sensitive": bool(summary.get("legality_sensitive")),
            "confidence_level": _confidence_level(confidence),
        },
    }


def _evaluate_publication_candidate_gate_from_summary(
    summary: dict[str, Any],
    *,
    canonical_dossier: dict[str, Any] | None = None,
) -> dict[str, Any]:
    readiness_state = str(summary.get("readiness_state") or "").strip().lower()
    approval_state = _normalize_approval_state(summary.get("approval_state"))
    promotion_state = _normalize_promotion_state(summary.get("promotion_state"))
    guardrail = str(summary.get("guardrail_label") or "").strip().lower()
    review_reason = str(summary.get("review_reason") or "").strip().lower()
    candidate_score = _safe_float(summary.get("candidate_score"))
    candidate_band = str(summary.get("candidate_score_band") or "").strip()
    confidence = _safe_float(summary.get("confidence"))
    source_count = _safe_int(summary.get("source_count"))
    strong_sections = list(summary.get("strong_sections") or [])
    insight_text = str(summary.get("insight_text") or "").strip()
    risks = list(summary.get("candidate_risk_factors") or [])
    reasons = list(summary.get("candidate_score_reasons") or [])
    source_agreement = dict(summary.get("source_agreement") or {})
    agreement_level = str(source_agreement.get("agreement_level") or "").strip().lower()
    legality_note = str((canonical_dossier or {}).get("legality_note") or "").strip().lower()
    legality_sensitive = bool(
        review_reason == "legality_sensitive"
        or ("legality" in strong_sections)
        or _contains_any([legality_note, *risks], ("effective", "banned", "restricted", "suspended", "legality"))
    )
    review_required = False
    publish_status = ""
    publish_reasons: list[str] = []
    publish_risks: list[str] = []

    blocked_risk = _contains_any(
        risks,
        (
            "current readiness guardrails still block",
            "stored review decision currently rejects",
            "strict insight output still falls back",
            "confidence is weak",
            "source agreement is in conflict",
            "candidate is currently deferred",
        ),
    )
    review_risk = _contains_any(
        risks,
        (
            "review-heavy",
            "require a review step",
            "no explicit approval",
            "legality timing or context",
        ),
    )

    if approval_state == "deferred" or promotion_state == "deferred" or readiness_state == "not_ready":
        publish_status = "publish_deferred"
        publish_reasons.append("The candidate is still deferred or watch-only for future publication.")
        if readiness_state == "not_ready":
            publish_risks.append("Current readiness is still not_ready.")
    elif (
        readiness_state == "blocked_by_guardrail"
        or approval_state in {"rejected", "superseded"}
        or guardrail == "blocked"
        or candidate_band == "blocked"
        or blocked_risk
        or confidence < 0.68
        or not insight_text
        or insight_text == FALLBACK_INSIGHT_TEXT
    ):
        publish_status = "publish_blocked"
        publish_reasons.append("The final publish gate is blocked by current guardrails or weak evidence.")
    elif (
        guardrail == "review required"
        or review_reason in {"legality_sensitive", "guarded_publish_review"}
        or legality_sensitive
        or approval_state != "approved_for_candidate"
        or promotion_state != "review_approved_candidate"
        or candidate_score < 78.0
        or review_risk
    ):
        publish_status = "publish_requires_review"
        review_required = True
        publish_reasons.append("The candidate is meaningful, but the final publish gate still requires explicit review.")
    else:
        publish_status = "publish_ready"
        publish_reasons.append("The candidate clears the current backend-only publish gate.")

    if approval_state == "approved_for_candidate":
        publish_reasons.append("A stored approval decision is present.")
    if promotion_state == "review_approved_candidate":
        publish_reasons.append("Promotion state is review-approved for future candidate use.")
    if candidate_score >= 88:
        publish_reasons.append(f"Candidate score is elite at {candidate_score:.1f}.")
    elif candidate_score >= 74:
        publish_reasons.append(f"Candidate score is strong at {candidate_score:.1f}.")
    if confidence >= 0.82:
        publish_reasons.append(f"Confidence clears the publish gate at {confidence:.2f}.")
    if source_count >= 2:
        publish_reasons.append(f"{source_count} stored sources support the payload contract.")

    if legality_sensitive:
        publish_risks.append("Legality-sensitive material still needs explicit review before any future storefront release.")
        review_required = True
    if agreement_level in {"single_source", "partial"}:
        publish_risks.append(f"Source agreement remains {agreement_level}.")
    if source_count <= 1:
        publish_risks.append("Source support is still thin for a final release gate.")
    publish_risks.extend(risks)

    gate_decision = "allow" if publish_status == "publish_ready" else ("review_required" if publish_status == "publish_requires_review" else "refuse")
    payload = _build_publication_payload_contract(
        summary={
            **summary,
            "publish_status": publish_status,
            "review_required": review_required,
            "legality_sensitive": legality_sensitive,
        },
        canonical_dossier=canonical_dossier or {},
    )
    return {
        "publish_status": publish_status,
        "publish_reasons": _dedupe_texts(publish_reasons, limit=4),
        "publish_risks": _dedupe_texts(publish_risks, limit=5),
        "publish_gate_decision": gate_decision,
        "review_required": review_required,
        "legality_sensitive": legality_sensitive,
        "confidence_level": _confidence_level(confidence),
        "explicit_approval_required": publish_status == "publish_requires_review",
        "publication_payload": payload,
    }


def _evaluate_batch_publication_gate_from_summary(batch_summary: dict[str, Any]) -> dict[str, Any]:
    batch_status = str(batch_summary.get("batch_status") or "").strip()
    batch_profile = str(batch_summary.get("batch_profile") or "").strip()
    batch_quality_score = _safe_float(batch_summary.get("batch_quality_score"))
    member_counts = dict(batch_summary.get("counts") or {})
    members = list(batch_summary.get("members") or [])
    member_publish_statuses = [str(item.get("publish_status") or "").strip() for item in members if str(item.get("publish_status") or "").strip()]
    has_blocked = member_counts.get("blocked_member_count", 0) > 0 or member_counts.get("deferred_member_count", 0) > 0 or "publish_blocked" in member_publish_statuses or "publish_deferred" in member_publish_statuses
    has_review = member_counts.get("review_member_count", 0) > 0 or "publish_requires_review" in member_publish_statuses
    split_needed = bool((batch_summary.get("split_suggestion") or {}).get("needed"))
    risks = list(batch_summary.get("unresolved_risks") or [])
    reasons = list(batch_summary.get("strongest_reasons") or [])

    if has_blocked or batch_status == "blocked" or batch_profile == "blocked":
        publish_status = "publish_blocked_batch"
        reasons.append("At least one member still blocks final publication handling.")
    elif split_needed or batch_profile == "mixed":
        publish_status = "publish_mixed_batch"
        reasons.append("The batch mixes incompatible publication lanes and should be split first.")
    elif has_review or batch_profile == "review_heavy":
        publish_status = "publish_review_required_batch"
        reasons.append("The batch stays review-bound because at least one member still needs explicit review.")
    elif batch_status == "review_ready" and batch_profile == "cohesive" and batch_quality_score >= 70.0:
        publish_status = "publish_ready_batch"
        reasons.append("All current members clear the backend-only batch publish gate.")
    else:
        publish_status = "publish_blocked_batch"
        reasons.append("The batch does not yet meet the final publish gate requirements.")

    if batch_quality_score < 70.0 and publish_status == "publish_ready_batch":
        publish_status = "publish_review_required_batch"
        risks.append("Batch quality is not strong enough for a clean publish-ready designation.")
    if split_needed:
        risks.append("A split is still recommended before future storefront release prep.")
    if batch_quality_score < 58.0:
        risks.append("Batch quality remains weak for release prep.")

    return {
        "batch_publish_status": publish_status,
        "batch_publish_reasons": _dedupe_texts(reasons, limit=4),
        "batch_publish_risks": _dedupe_texts(risks, limit=4),
        "batch_publish_gate_decision": "allow" if publish_status == "publish_ready_batch" else ("review_required" if publish_status == "publish_review_required_batch" else "refuse"),
    }


def _dedupe_texts(values: list[str], *, limit: int = 4) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _candidate_sort_key(summary: dict[str, Any]) -> tuple[float, float, str]:
    return (
        -_safe_float(summary.get("candidate_score")),
        -_safe_float(summary.get("confidence")),
        str(summary.get("card_code") or summary.get("target_id") or ""),
    )


def _score_publication_candidate(
    *,
    canonical_dossier: dict[str, Any],
    projection_row: dict[str, Any],
    readiness_view: dict[str, Any],
    queue_row: dict[str, Any],
    stage_view: dict[str, Any],
    insight_text: str,
    strong_sections: list[str],
    projection_sections: list[str],
    used_sections: list[str],
    stored_insight: dict[str, Any],
) -> dict[str, Any]:
    readiness_state = str(readiness_view.get("readiness_state") or "").strip().lower()
    approval_state = _normalize_approval_state(
        queue_row.get("approval_state") or projection_row.get("approval_state") or readiness_view.get("approval_state")
    )
    promotion_state = _normalize_promotion_state(
        queue_row.get("promotion_state") or projection_row.get("promotion_state") or readiness_view.get("promotion_state")
    )
    guardrail = str(readiness_view.get("guardrail_label") or projection_row.get("publication_guardrail") or "").strip().lower()
    review_reason = str(readiness_view.get("review_reason") or "").strip().lower()
    confidence = max(
        _safe_float(readiness_view.get("confidence")),
        _safe_float(canonical_dossier.get("confidence_score")),
        _safe_float(projection_row.get("confidence_score")),
        _safe_float(stored_insight.get("confidence")),
    )
    source_agreement = dict(projection_row.get("source_agreement") or canonical_dossier.get("source_agreement") or {})
    agreement_level = str(source_agreement.get("agreement_level") or "").strip().lower()
    source_count = _safe_int(readiness_view.get("source_count") or source_agreement.get("source_count") or len(canonical_dossier.get("sources") or []))
    has_strict_insight = bool(insight_text) and insight_text != FALLBACK_INSIGHT_TEXT
    quality_tier = str(stored_insight.get("quality_tier") or "").strip().lower()
    meta_relevance = max(
        _safe_float(canonical_dossier.get("meta_relevance_score")),
        _safe_float(projection_row.get("meta_relevance_score")),
    )
    top_leaders = list(canonical_dossier.get("top_leaders_used_in") or [])
    leader_count = _safe_int(canonical_dossier.get("leader_count") or len(top_leaders))
    tracked_deck_count = _safe_int(canonical_dossier.get("tracked_deck_count"))
    price_low = canonical_dossier.get("price_low")
    price_note = str(canonical_dossier.get("price_trend_note") or projection_row.get("price_trend_note") or "").strip().lower()
    legality_note = str(canonical_dossier.get("legality_note") or projection_row.get("legality_note") or "").strip().lower()

    score = 0.0
    reasons: list[str] = []
    risks: list[str] = []

    readiness_weight = {
        "ready_for_publish_candidate": 34.0,
        "ready_for_review": 27.0,
        "not_ready": 12.0,
        "blocked_by_guardrail": 4.0,
    }.get(readiness_state, 6.0)
    score += readiness_weight
    if readiness_state == "ready_for_publish_candidate":
        reasons.append("Readiness already marks this as a publish-candidate class item.")
    elif readiness_state == "ready_for_review":
        reasons.append("The dossier is strong enough for human review before future surfacing.")
    elif readiness_state == "not_ready":
        risks.append("Readiness is still watch-only, so this is not a publish-ready candidate yet.")
    else:
        risks.append("Current readiness guardrails still block this candidate.")

    score += min(confidence, 1.0) * 22.0
    if confidence >= 0.9:
        reasons.append(f"Confidence is strong at {confidence:.2f}.")
    elif confidence < 0.65:
        risks.append(f"Confidence is weak at {confidence:.2f}.")

    agreement_bonus = {
        "full": 9.0,
        "majority": 7.0,
        "partial": 4.0,
        "single_source": 1.5,
        "conflict": -18.0,
    }.get(agreement_level, 0.0)
    score += agreement_bonus
    if agreement_level in {"full", "majority"}:
        reasons.append(f"Source agreement is {agreement_level}.")
    elif agreement_level == "conflict":
        risks.append("Source agreement is in conflict.")
    elif agreement_level in {"partial", "single_source"}:
        risks.append(f"Source agreement is only {agreement_level}.")

    if source_count > 0:
        score += min(source_count, 4) * 2.0
    if source_count >= 2:
        reasons.append(f"{source_count} stored source references support the dossier.")
    elif source_count <= 0:
        risks.append("No inspectable source count is stored.")

    section_count = len(strong_sections)
    score += min(section_count, 4) * 4.0
    if section_count >= 3:
        reasons.append(f"{section_count} dossier sections are meaningfully populated.")
    elif section_count <= 1:
        risks.append("Only a narrow slice of the dossier is populated.")

    if projection_sections:
        score += min(len(projection_sections), 4) * 1.5
    else:
        risks.append("Projection coverage is still sparse.")

    if has_strict_insight:
        score += 10.0
        reasons.append("A strict dossier-backed insight is available.")
    else:
        score -= 10.0
        risks.append("Strict insight output still falls back to insufficient data.")

    tier_bonus = {
        "evidenced": 6.0,
        "strategic": 4.0,
        "contextual": 2.0,
        "generic": -5.0,
    }.get(quality_tier, 0.0)
    score += tier_bonus
    if quality_tier == "evidenced":
        reasons.append("Stored insight quality is evidenced-tier.")
    elif quality_tier == "generic":
        risks.append("Stored insight quality is still generic-tier.")

    if "usage_meta" in strong_sections or "gameplay_role" in strong_sections:
        score += 8.0
        if meta_relevance > 0:
            score += min(meta_relevance, 1.0) * 10.0
        score += min(leader_count, 4) * 2.0
        score += min(tracked_deck_count, 20) * 0.35
        if leader_count or tracked_deck_count:
            reasons.append(
                f"Usage/meta evidence covers {leader_count} leader(s) and {tracked_deck_count} tracked deck slots."
            )
    if "rulings" in strong_sections:
        score += 6.0
        reasons.append("Official rulings context is stored.")
    if "legality" in strong_sections:
        score += 6.0
        reasons.append("Legality context is stored.")
        if review_reason == "legality_sensitive" or "effective" in legality_note or "upcoming" in legality_note:
            score -= 3.0
            risks.append("Legality timing or context keeps this candidate review-heavy.")
    if "market" in strong_sections:
        if price_low not in (None, ""):
            try:
                score += min(float(price_low), 40.0) * 0.35
            except Exception:
                pass
        if price_note and "single stored watch-price point only" not in price_note:
            score += 3.0
            reasons.append("Stored market evidence includes more than a single watch point.")
        elif "single stored watch-price point only" in price_note:
            score -= 7.0
            risks.append("Market evidence is still just a single watch-price point.")

    if approval_state == "approved_for_candidate":
        score += 8.0
        reasons.append("A review approval is already stored.")
    elif approval_state in {"rejected", "superseded"}:
        score -= 28.0
        risks.append("A stored review decision currently rejects future promotion.")
    elif approval_state == "deferred":
        score -= 12.0
        risks.append("The candidate is currently deferred.")
    elif readiness_state in {"ready_for_review", "ready_for_publish_candidate"}:
        risks.append("No explicit approval decision is stored yet.")

    if promotion_state == "review_approved_candidate":
        score += 6.0
    elif promotion_state == "blocked_from_promotion":
        score -= 20.0
    elif promotion_state == "deferred":
        score -= 10.0

    if guardrail == "safe action":
        score += 5.0
    elif guardrail == "review required":
        score -= 2.0
        risks.append("Guardrails still require a review step.")
    elif guardrail == "blocked":
        score -= 18.0
    elif guardrail == "read-only":
        score -= 6.0

    if bool(stage_view.get("stageable")):
        score += 3.0
    elif readiness_state not in {"blocked_by_guardrail", "not_ready"}:
        risks.append("Current staging eligibility is still not satisfied.")

    score = max(0.0, min(score, 100.0))
    if readiness_state == "blocked_by_guardrail" or approval_state in {"rejected", "superseded"}:
        score = min(score, 28.0)
        profile = "blocked"
    elif readiness_state == "not_ready" or not has_strict_insight:
        score = min(score, 49.0)
        profile = "weak_partial"
    elif guardrail == "review required" or review_reason in {"legality_sensitive", "guarded_publish_review"}:
        profile = "high_value_review_heavy" if score >= 60.0 else "weak_partial"
    else:
        profile = "high_value_safe" if score >= 72.0 else "weak_partial"

    score = round(score, 3)
    return {
        "candidate_score": score,
        "candidate_score_band": _candidate_score_band(score),
        "candidate_profile": profile,
        "candidate_score_reasons": _dedupe_texts(reasons, limit=4),
        "candidate_risk_factors": _dedupe_texts(risks, limit=4),
    }


def _curate_batch_quality(
    member_summaries: list[dict[str, Any]],
    *,
    existing_status: str = "",
) -> dict[str, Any]:
    if not member_summaries:
        return {
            "batch_quality_score": 0.0,
            "batch_quality_band": "blocked",
            "batch_profile": "weak",
            "recommended_next_step": "Stage or add candidates before creating a publication-prep batch.",
            "strongest_reasons": [],
            "unresolved_risks": ["No active staged members are attached to this batch."],
            "common_sections": [],
            "split_suggestion": {"needed": False, "groups": []},
        }

    member_count = len(member_summaries)
    scores = [_safe_float(item.get("candidate_score")) for item in member_summaries]
    avg_score = sum(scores) / max(member_count, 1)
    score_spread = (max(scores) - min(scores)) if scores else 0.0
    review_heavy = [item for item in member_summaries if str(item.get("candidate_profile") or "") == "high_value_review_heavy"]
    safe_members = [item for item in member_summaries if str(item.get("candidate_profile") or "") == "high_value_safe"]
    weak_members = [item for item in member_summaries if str(item.get("candidate_profile") or "") == "weak_partial"]
    blocked_members = [
        item for item in member_summaries
        if str(item.get("candidate_profile") or "") == "blocked"
        or _normalize_approval_state(item.get("approval_state")) in {"deferred", "rejected", "superseded"}
    ]
    all_sections = [set(item.get("strong_sections") or []) for item in member_summaries if list(item.get("strong_sections") or [])]
    common_sections = sorted(set.intersection(*all_sections)) if all_sections else []

    quality_score = float(avg_score)
    strongest_reasons: list[str] = []
    unresolved_risks: list[str] = []

    if safe_members:
        strongest_reasons.append(f"{len(safe_members)} high-value safe member(s)")
        quality_score += min(len(safe_members), 4) * 4.0
    if review_heavy:
        strongest_reasons.append(f"{len(review_heavy)} review-heavy member(s)")
        quality_score -= len(review_heavy) * 3.0
        unresolved_risks.append(f"{len(review_heavy)} review-heavy member(s)")
    if weak_members:
        unresolved_risks.append(f"{len(weak_members)} weak or partial member(s)")
        quality_score -= len(weak_members) * 5.0
    if blocked_members:
        unresolved_risks.append(f"{len(blocked_members)} blocked or deferred member(s)")
        quality_score -= len(blocked_members) * 18.0
    if common_sections:
        strongest_reasons.append(f"shared sections: {', '.join(common_sections[:3])}")
        quality_score += min(len(common_sections), 3) * 3.0
    if score_spread <= 10:
        strongest_reasons.append("member score spread is tight")
        quality_score += 5.0
    elif score_spread >= 20:
        unresolved_risks.append("member score spread is wide")
        quality_score -= 5.0

    profiles = {str(item.get("candidate_profile") or "") for item in member_summaries if str(item.get("candidate_profile") or "")}
    split_groups: list[dict[str, Any]] = []
    if safe_members:
        split_groups.append({
            "profile": "high_value_safe",
            "member_ids": [str(item.get("target_id") or item.get("card_code") or "") for item in sorted(safe_members, key=_candidate_sort_key)],
        })
    if review_heavy:
        split_groups.append({
            "profile": "high_value_review_heavy",
            "member_ids": [str(item.get("target_id") or item.get("card_code") or "") for item in sorted(review_heavy, key=_candidate_sort_key)],
        })
    if weak_members:
        split_groups.append({
            "profile": "weak_partial",
            "member_ids": [str(item.get("target_id") or item.get("card_code") or "") for item in sorted(weak_members, key=_candidate_sort_key)],
        })
    if blocked_members:
        split_groups.append({
            "profile": "blocked",
            "member_ids": [str(item.get("target_id") or item.get("card_code") or "") for item in sorted(blocked_members, key=_candidate_sort_key)],
        })

    if blocked_members:
        batch_profile = "blocked"
        recommended_next = "Remove blocked or deferred members before treating this as a future publication-prep batch."
    elif review_heavy and safe_members:
        batch_profile = "mixed"
        recommended_next = "Split the safe members from the review-heavy members before future promotion prep."
    elif review_heavy and not safe_members:
        batch_profile = "review_heavy"
        recommended_next = "Keep this batch review-bound until the remaining review-heavy members are explicitly cleared."
    elif avg_score < 58 or weak_members:
        batch_profile = "weak"
        recommended_next = "Strengthen dossier quality or remove weak members before advancing this batch."
    else:
        batch_profile = "cohesive"
        recommended_next = "This batch is a strong backend-only proposal group for future reviewed surfacing."

    if existing_status == "archived":
        batch_profile = "archived"
        recommended_next = "Archived batches remain for history only."
    elif batch_profile == "mixed":
        quality_score = min(quality_score, 72.0)
    elif batch_profile == "review_heavy":
        quality_score = min(quality_score, 68.0)
    elif batch_profile == "weak":
        quality_score = min(quality_score, 55.0)
    elif batch_profile == "blocked":
        quality_score = min(quality_score, 32.0)

    quality_score = max(0.0, min(round(quality_score, 3), 100.0))
    return {
        "batch_quality_score": quality_score,
        "batch_quality_band": _batch_quality_band(quality_score),
        "batch_profile": batch_profile,
        "recommended_next_step": recommended_next,
        "strongest_reasons": _dedupe_texts(strongest_reasons, limit=4),
        "unresolved_risks": _dedupe_texts(unresolved_risks, limit=4),
        "common_sections": common_sections[:4],
        "split_suggestion": {
            "needed": batch_profile in {"mixed", "blocked"},
            "groups": split_groups[:4],
        },
    }


def build_publication_candidate_summary(
    *,
    card_code: str,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
    readiness: dict[str, Any] | None = None,
    dossier: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_code = _normalize_code(card_code)
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    with closing(connect_catalog_db(project_path)) as conn:
        projection_row = _load_projection_row(conn, normalized_code)
        stored_insight = _load_best_insight_row(conn, normalized_code)
        queue_row = _load_review_queue_row(conn, normalized_code)
        stage_row = _load_stage_row(conn, normalized_code)

    store = _build_store(
        canonical_dossier_db_path=Path(canonical_dossier_db_path),
        rules_db_path=Path(rules_db_path),
        deck_intel_db_path=Path(deck_intel_db_path),
    )
    canonical_dossier = dict(dossier or store.build_card_dossier(normalized_code, prices_path=prices_path))
    readiness_view = dict(
        readiness
        or evaluate_publication_readiness(
            card_code=normalized_code,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
            dossier=canonical_dossier,
        )
    )
    insight_text = str(
        readiness_view.get("insight_text")
        or stored_insight.get("insight_text")
        or stored_insight.get("text")
        or ""
    ).strip()
    signals: list[str] = []
    for value in (
        canonical_dossier.get("legality_note"),
        canonical_dossier.get("rulings_summary"),
        canonical_dossier.get("deck_usage_summary"),
        canonical_dossier.get("price_trend_note"),
    ):
        text = _truncate_text(value)
        if text and text not in signals:
            signals.append(text)
    price_low = canonical_dossier.get("price_low")
    if price_low not in (None, ""):
        try:
            signals.append(f"Stored watch price: ${float(price_low):.2f}.")
        except Exception:
            pass
    if insight_text and insight_text != FALLBACK_INSIGHT_TEXT:
        signals.insert(0, _truncate_text(insight_text, limit=260))
    strong_sections = list(readiness_view.get("strong_sections") or [])
    projection_sections = list(readiness_view.get("projection_sections") or [])
    used_sections = list(readiness_view.get("used_sections") or [])
    queue_status = str(queue_row.get("status") or "").strip().lower()
    approval_state = _normalize_approval_state(
        queue_row.get("approval_state") or projection_row.get("approval_state")
    )
    if not approval_state and queue_status == "pending":
        approval_state = "pending_review"
    elif not approval_state and queue_status == "deferred":
        approval_state = "deferred"
    promotion_state, promotion_rationale = _derive_promotion_fields(
        readiness_state=str(readiness_view.get("readiness_state") or "").strip(),
        approval_state=approval_state,
        queue_status=queue_status,
        guardrail_label=str(readiness_view.get("guardrail_label") or projection_row.get("publication_guardrail") or "").strip(),
    )
    name = str(canonical_dossier.get("name") or projection_row.get("card_name") or normalized_code).strip()
    if readiness_view.get("readiness_state") == "ready_for_publish_candidate":
        headline = f"{name} is a dossier-backed publish candidate."
    elif readiness_view.get("readiness_state") == "ready_for_review":
        headline = f"{name} needs review before Miru treats it as publish-ready."
    elif readiness_view.get("readiness_state") == "not_ready":
        headline = f"{name} is still a watch-only publication candidate."
    else:
        headline = f"{name} is blocked from publication readiness right now."
    summary_text = " ".join(
        part for part in (
            headline,
            _truncate_text(readiness_view.get("rationale"), limit=220),
            signals[0] if signals else "",
        ) if part
    ).strip()
    stage_view = _derive_staging_fields(
        {
            "readiness_state": str(readiness_view.get("readiness_state") or "").strip(),
            "approval_state": approval_state,
            "promotion_state": promotion_state,
            "guardrail_label": str(readiness_view.get("guardrail_label") or "").strip(),
            "review_reason": str(readiness_view.get("review_reason") or "").strip(),
        },
        stage_row=stage_row,
        runtime_uncertain=False,
    )
    candidate_scoring = _score_publication_candidate(
        canonical_dossier=canonical_dossier,
        projection_row=projection_row,
        readiness_view={**readiness_view, "approval_state": approval_state, "promotion_state": promotion_state},
        queue_row=queue_row,
        stage_view=stage_view,
        insight_text=insight_text,
        strong_sections=strong_sections,
        projection_sections=projection_sections,
        used_sections=used_sections,
        stored_insight=stored_insight,
    )
    summary = {
        "card_code": normalized_code,
        "card_name": name,
        "set_code": str(canonical_dossier.get("set_code") or projection_row.get("set_code") or "").strip(),
        "set_name": str(canonical_dossier.get("set_name") or projection_row.get("set_name") or "").strip(),
        "rarity": str(canonical_dossier.get("rarity") or "").strip(),
        "readiness_state": str(readiness_view.get("readiness_state") or "").strip(),
        "stored_readiness_state": str(projection_row.get("publication_readiness") or "").strip(),
        "guardrail_label": str(readiness_view.get("guardrail_label") or "").strip(),
        "risk_level": str(readiness_view.get("risk_level") or "").strip(),
        "confidence": _safe_float(readiness_view.get("confidence")),
        "rationale": str(readiness_view.get("rationale") or "").strip(),
        "review_reason": str(readiness_view.get("review_reason") or "").strip(),
        "recommended_next_step": str(readiness_view.get("recommended_next_step") or "").strip(),
        "queue_worthy": bool(readiness_view.get("queue_worthy")),
        "queue_status": queue_status,
        "approval_state": approval_state,
        "approval_note": str(queue_row.get("approval_note") or "").strip(),
        "decision_source": str(queue_row.get("decision_source") or "").strip(),
        "promotion_state": promotion_state,
        "promotion_rationale": promotion_rationale,
        "stage_state": str(stage_row.get("stage_state") or stage_view.get("stage_state") or "").strip(),
        "stage_batch_id": str(stage_row.get("batch_id") or stage_view.get("batch_id") or "").strip(),
        "stage_decision": str(stage_view.get("decision") or "").strip(),
        "stage_guardrail_label": str(stage_view.get("guardrail_label") or "").strip(),
        "stage_rationale": str(stage_view.get("rationale") or "").strip(),
        "stageable": bool(stage_view.get("stageable")),
        "stage_note": str(stage_row.get("note") or "").strip(),
        "stage_decision_source": str(stage_row.get("decision_source") or stage_view.get("decision_source") or "").strip(),
        "stage_updated_at": str(stage_row.get("updated_at") or stage_view.get("updated_at") or "").strip(),
        "strong_sections": strong_sections,
        "projection_sections": projection_sections,
        "used_sections": used_sections,
        "signals": signals[:4],
        "summary_text": summary_text,
        "insight_text": insight_text,
        "deck_usage_summary": str(canonical_dossier.get("deck_usage_summary") or projection_row.get("deck_usage_summary") or "").strip(),
        "meta_relevance_score": _safe_float(canonical_dossier.get("meta_relevance_score") or projection_row.get("meta_relevance_score")),
        "rulings_summary": str(canonical_dossier.get("rulings_summary") or projection_row.get("rulings_summary") or "").strip(),
        "legality_note": str(canonical_dossier.get("legality_note") or projection_row.get("legality_note") or "").strip(),
        "price_value": canonical_dossier.get("price_low")
        if canonical_dossier.get("price_low") not in (None, "")
        else projection_row.get("price_value"),
        "price_trend_note": str(canonical_dossier.get("price_trend_note") or projection_row.get("price_trend_note") or "").strip(),
        "candidate_score": _safe_float(candidate_scoring.get("candidate_score")),
        "candidate_score_band": str(candidate_scoring.get("candidate_score_band") or "").strip(),
        "candidate_profile": str(candidate_scoring.get("candidate_profile") or "").strip(),
        "candidate_score_reasons": list(candidate_scoring.get("candidate_score_reasons") or []),
        "candidate_risk_factors": list(candidate_scoring.get("candidate_risk_factors") or []),
        "source_agreement": dict(projection_row.get("source_agreement") or {}),
        "source_count": _safe_int(readiness_view.get("source_count")),
        "last_verified_at": str(canonical_dossier.get("last_verified_at") or projection_row.get("last_verified_at") or "").strip(),
        "source_updated_at": str(readiness_view.get("source_updated_at") or "").strip(),
        "sync_reason": str(projection_row.get("last_sync_reason") or "").strip(),
        "priority_score": _safe_float(projection_row.get("last_priority_score")),
        "priority_context": dict(projection_row.get("priority_context") or {}),
        "source_summary": str(canonical_dossier.get("source_summary") or projection_row.get("source_summary") or "").strip(),
        "stored_publish_status": str(projection_row.get("publish_status") or "").strip(),
        "stored_gap_class": str(projection_row.get("dossier_gap_class") or "").strip(),
        "stored_revalidation_status": str(projection_row.get("revalidation_status") or "").strip(),
    }
    publish_gate = _evaluate_publication_candidate_gate_from_summary(
        summary,
        canonical_dossier=canonical_dossier,
    )
    return {
        **summary,
        **publish_gate,
    }


def evaluate_publication_candidate_gate(
    *,
    card_code: str,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
) -> dict[str, Any]:
    summary = build_publication_candidate_summary(
        card_code=card_code,
        project_db_path=project_db_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
    )
    return {
        "card_code": str(summary.get("card_code") or "").strip(),
        "publish_status": str(summary.get("publish_status") or "").strip(),
        "publish_gate_decision": str(summary.get("publish_gate_decision") or "").strip(),
        "publish_reasons": list(summary.get("publish_reasons") or []),
        "publish_risks": list(summary.get("publish_risks") or []),
        "review_required": bool(summary.get("review_required")),
        "legality_sensitive": bool(summary.get("legality_sensitive")),
        "confidence_level": str(summary.get("confidence_level") or "").strip(),
        "publication_payload": dict(summary.get("publication_payload") or {}),
        "summary": summary,
    }


def _coverage_value_band(score: float) -> str:
    if score >= 85.0:
        return "high_value"
    if score >= 45.0:
        return "medium_value"
    return "low_value"


def _revalidation_priority_bucket(score: float) -> str:
    if score >= 155.0:
        return "critical"
    if score >= 115.0:
        return "high"
    if score >= 75.0:
        return "elevated"
    if score >= 40.0:
        return "medium"
    return "baseline"


def _normalize_expansion_objectives(values: list[Any] | tuple[Any, ...] | set[Any] | Any) -> list[str]:
    if isinstance(values, (list, tuple, set)):
        raw_values = list(values)
    else:
        raw_values = [values]
    objectives: list[str] = []
    for raw in raw_values:
        value = str(raw or "").strip()
        if value and value in EXPANSION_OBJECTIVES and value not in objectives:
            objectives.append(value)
    return objectives


def _classify_revalidation_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    strong_sections = {str(item).strip() for item in list(summary.get("strong_sections") or []) if str(item).strip()}
    priority_context = dict(summary.get("priority_context") or {})
    confidence = _safe_float(summary.get("confidence"))
    source_count = _safe_int(summary.get("source_count"))
    agreement_level = str((summary.get("source_agreement") or {}).get("agreement_level") or "").strip().lower()
    review_reason = str(summary.get("review_reason") or "").strip().lower()
    readiness_state = str(summary.get("readiness_state") or "").strip().lower()
    publish_status = str(summary.get("publish_status") or "").strip().lower()
    stored_readiness_state = str(summary.get("stored_readiness_state") or "").strip().lower()
    stored_publish_status = str(summary.get("stored_publish_status") or "").strip().lower()
    candidate_score = _safe_float(summary.get("candidate_score"))
    priority_score = _safe_float(summary.get("priority_score"))
    meta_relevance_score = _safe_float(summary.get("meta_relevance_score"))
    price_value = summary.get("price_value")
    deck_usage_summary = str(summary.get("deck_usage_summary") or "").strip()
    rulings_summary = str(summary.get("rulings_summary") or "").strip()
    legality_note = str(summary.get("legality_note") or "").strip()
    legality_sensitive = bool(summary.get("legality_sensitive"))
    last_verified_days = _age_days(summary.get("last_verified_at") or summary.get("source_updated_at"))
    is_leader = str(summary.get("rarity") or "").strip().upper() == "L"
    queue_state = str(summary.get("queue_status") or "").strip().lower()

    market_only = bool(strong_sections) and strong_sections == {"market"}
    has_usage = bool(deck_usage_summary) or "usage_meta" in strong_sections or "gameplay_role" in strong_sections
    has_rules_legality = bool(rulings_summary or legality_note) or bool({"rulings", "legality"} & strong_sections)

    value_score = 0.0
    if is_leader:
        value_score += 70.0
    value_score += min(meta_relevance_score * 70.0, 70.0)
    if has_usage:
        value_score += 18.0
    if has_rules_legality or legality_sensitive:
        value_score += 18.0
    if price_value not in (None, ""):
        try:
            numeric_price = float(price_value)
        except Exception:
            numeric_price = 0.0
        value_score += min(numeric_price, 20.0)
        if numeric_price >= 5.0:
            value_score += 8.0
    if readiness_state in {"ready_for_publish_candidate", "ready_for_review"}:
        value_score += 18.0
    if stored_readiness_state in {"ready_for_publish_candidate", "ready_for_review"}:
        value_score += 10.0
    if summary.get("approval_state") == "approved_for_candidate":
        value_score += 12.0
    if str(summary.get("stage_state") or "").strip() in {"staged_candidate", "staged_batch_member"}:
        value_score += 12.0
    value_score += min(candidate_score / 4.0, 25.0)
    value_score += min(priority_score / 12.0, 30.0)
    coverage_value_score = round(min(value_score, 100.0), 3)
    coverage_value_band = _coverage_value_band(coverage_value_score)

    gap_tags: list[str] = []
    if confidence < 0.68 or review_reason == "weak_provenance" or agreement_level == "conflict":
        gap_tags.append("weak_provenance")
    if source_count <= 1 or agreement_level in {"single_source", "partial"}:
        gap_tags.append("thin_source_support")
    stale_threshold = 21.0 if legality_sensitive or has_rules_legality else (45.0 if has_usage else 90.0)
    if strong_sections and last_verified_days > stale_threshold:
        gap_tags.append("stale_dossier")
    if (is_leader or meta_relevance_score >= 0.5 or candidate_score >= 70.0) and not has_usage:
        gap_tags.append("missing_usage_meta")
    if legality_sensitive and not has_rules_legality:
        gap_tags.append("missing_rules_legality")
    if market_only:
        gap_tags.append("market_only")
    if (
        not stored_readiness_state
        or not stored_publish_status
        or publish_status in {"publish_requires_review", "publish_deferred"}
    ) and candidate_score >= 58.0 and confidence >= 0.74 and strong_sections:
        gap_tags.append("partial_but_promising")
    if gap_tags and coverage_value_band in {"high_value", "medium_value"} and confidence >= 0.72:
        gap_tags.append("ready_for_revalidation")

    objective_tags = _normalize_expansion_objectives(priority_context.get("priority_objectives") or [])
    if ("usage_meta" in strong_sections or "missing_usage_meta" in gap_tags or (is_leader and not has_usage)) and "usage_meta_fill" not in objective_tags:
        objective_tags.append("usage_meta_fill")
    if (is_leader or meta_relevance_score >= 0.55) and "leader_profile_expand" not in objective_tags:
        objective_tags.append("leader_profile_expand")
    if "thin_source_support" in gap_tags and "source_depth_fill" not in objective_tags:
        objective_tags.append("source_depth_fill")
    if (legality_sensitive or "missing_rules_legality" in gap_tags) and "legality_recheck" not in objective_tags:
        objective_tags.append("legality_recheck")
    if any(tag in gap_tags for tag in ("stale_dossier", "partial_but_promising", "ready_for_revalidation")) and "stale_refresh" not in objective_tags:
        objective_tags.append("stale_refresh")
    objective_tags = _normalize_expansion_objectives(objective_tags)

    primary_gap = "stable"
    for key in (
        "weak_provenance",
        "missing_rules_legality",
        "missing_usage_meta",
        "stale_dossier",
        "market_only",
        "thin_source_support",
        "partial_but_promising",
        "ready_for_revalidation",
    ):
        if key in gap_tags:
            primary_gap = key
            break

    revalidation_status = "stable_enough"
    revalidation_reason = "Current dossier-backed coverage already looks strong enough to leave alone for now."
    if primary_gap == "weak_provenance" and coverage_value_band == "low_value":
        revalidation_status = "hold"
        revalidation_reason = "Evidence is too thin or conflicted to justify an autonomous refresh right now."
    elif primary_gap == "weak_provenance":
        revalidation_status = "escalate_review"
        revalidation_reason = "High-value coverage is blocked by weak provenance and should stay review-bound."
    elif primary_gap == "missing_rules_legality":
        revalidation_status = "escalate_review" if legality_sensitive else "recheck_soon"
        revalidation_reason = "Rules or legality context is the most important missing section for this card."
    elif primary_gap == "missing_usage_meta":
        revalidation_status = "recheck_soon"
        revalidation_reason = "Usage and meta coverage would materially improve this high-value card."
    elif primary_gap == "stale_dossier":
        revalidation_status = "recheck_soon"
        revalidation_reason = "Stored dossier freshness is old enough that a bounded recheck is worthwhile."
    elif primary_gap == "partial_but_promising":
        revalidation_status = "recheck_soon" if coverage_value_band == "high_value" else "recheck_later"
        revalidation_reason = "The card is close to useful publish-candidate coverage and should be refreshed through existing pipelines."
    elif primary_gap == "market_only":
        revalidation_status = "recheck_later"
        revalidation_reason = "The card only has market-like evidence right now, so refresh can wait for broader dossier support."
    elif primary_gap == "thin_source_support":
        revalidation_status = "recheck_later" if coverage_value_band != "low_value" else "hold"
        revalidation_reason = "Source support is still thin, so this should stay bounded until better corroboration arrives."
    elif primary_gap == "ready_for_revalidation":
        revalidation_status = "recheck_soon"
        revalidation_reason = "This card has enough value and partial support to justify a bounded autonomous revalidation pass."
    elif readiness_state == "ready_for_review" and queue_state == "pending":
        revalidation_status = "escalate_review"
        revalidation_reason = "This card is already meaningful and should stay routed toward explicit review."

    revalidation_score = coverage_value_score
    if revalidation_status == "recheck_soon":
        revalidation_score += 34.0
    elif revalidation_status == "escalate_review":
        revalidation_score += 28.0
    elif revalidation_status == "recheck_later":
        revalidation_score += 14.0
    elif revalidation_status == "hold":
        revalidation_score -= 12.0
    if "stale_dossier" in gap_tags:
        revalidation_score += 12.0
    if "missing_usage_meta" in gap_tags:
        revalidation_score += 16.0
    if "missing_rules_legality" in gap_tags:
        revalidation_score += 18.0
    if legality_sensitive:
        revalidation_score += 10.0
    if not stored_publish_status:
        if readiness_state == "ready_for_publish_candidate":
            revalidation_score += 28.0
        elif readiness_state == "ready_for_review":
            revalidation_score += 20.0
        elif candidate_score >= 90.0:
            revalidation_score += 10.0
    if "leader_profile_expand" in objective_tags:
        revalidation_score += 12.0
    if "source_depth_fill" in objective_tags:
        revalidation_score += 10.0
    if "legality_recheck" in objective_tags:
        revalidation_score += 12.0
    if "stale_refresh" in objective_tags:
        revalidation_score += 10.0
    revalidation_priority_score = round(max(revalidation_score, 0.0), 3)
    revalidation_priority_bucket = _revalidation_priority_bucket(revalidation_priority_score)

    summary_bits: list[str] = []
    if is_leader:
        summary_bits.append("leader card")
    if meta_relevance_score > 0:
        summary_bits.append(f"meta={meta_relevance_score:.2f}")
    if legality_sensitive:
        summary_bits.append("legality-sensitive")
    if candidate_score > 0:
        summary_bits.append(f"candidate={candidate_score:.1f}")
    if source_count > 0:
        summary_bits.append(f"sources={source_count}")
    if objective_tags:
        summary_bits.append(f"objectives={','.join(objective_tags[:3])}")
    coverage_gap_summary = f"{primary_gap.replace('_', ' ')}: " + ", ".join(summary_bits or ["bounded dossier follow-up"])

    return {
        "dossier_gap_class": _normalize_gap_class(primary_gap) or "stable",
        "dossier_gap_tags": _dedupe_texts(gap_tags, limit=8),
        "objective_tags": objective_tags,
        "primary_objective": objective_tags[0] if objective_tags else "",
        "coverage_value_score": coverage_value_score,
        "coverage_value_band": _normalize_coverage_value_band(coverage_value_band) or "low_value",
        "coverage_gap_summary": coverage_gap_summary,
        "revalidation_status": _normalize_revalidation_status(revalidation_status) or "stable_enough",
        "revalidation_reason": revalidation_reason,
        "revalidation_priority_score": revalidation_priority_score,
        "revalidation_priority_bucket": revalidation_priority_bucket,
        "last_verified_age_days": round(last_verified_days, 2),
    }


def build_revalidation_candidate_summary(
    *,
    card_code: str,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
) -> dict[str, Any]:
    summary = build_publication_candidate_summary(
        card_code=card_code,
        project_db_path=project_db_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
    )
    return {**summary, **_classify_revalidation_from_summary(summary)}


def _persist_revalidation_summary(conn: sqlite3.Connection, summary: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE card_intelligence
        SET dossier_gap_class = ?,
            dossier_gap_tags_json = ?,
            coverage_value_score = ?,
            coverage_value_band = ?,
            coverage_gap_summary = ?,
            revalidation_status = ?,
            revalidation_reason = ?,
            revalidation_priority_score = ?,
            revalidation_priority_bucket = ?,
            revalidation_updated_at = ?
        WHERE card_id IN (
            SELECT id
            FROM cards
            WHERE canonical_code = ?
        )
        """,
        (
            _normalize_gap_class(summary.get("dossier_gap_class")),
            _json_dump(summary.get("dossier_gap_tags") or []),
            _safe_float(summary.get("coverage_value_score")),
            _normalize_coverage_value_band(summary.get("coverage_value_band")),
            str(summary.get("coverage_gap_summary") or "").strip(),
            _normalize_revalidation_status(summary.get("revalidation_status")),
            str(summary.get("revalidation_reason") or "").strip(),
            _safe_float(summary.get("revalidation_priority_score")),
            str(summary.get("revalidation_priority_bucket") or "").strip(),
            _utc_now_timestamp(),
            str(summary.get("card_code") or "").strip().upper(),
        ),
    )


def load_revalidation_summary(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    limit: int = 8,
) -> dict[str, Any]:
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    bounded_limit = max(1, limit)
    with closing(connect_catalog_db(project_path)) as conn:
        gap_counts: dict[str, int] = {}
        for row in conn.execute(
            "SELECT dossier_gap_class, COUNT(*) AS row_count FROM card_intelligence GROUP BY dossier_gap_class"
        ).fetchall():
            key = _normalize_gap_class(row["dossier_gap_class"])
            if key:
                gap_counts[key] = _safe_int(row["row_count"])
        revalidation_counts: dict[str, int] = {}
        for row in conn.execute(
            "SELECT revalidation_status, COUNT(*) AS row_count FROM card_intelligence GROUP BY revalidation_status"
        ).fetchall():
            key = _normalize_revalidation_status(row["revalidation_status"])
            if key:
                revalidation_counts[key] = _safe_int(row["row_count"])
        value_band_counts: dict[str, int] = {}
        for row in conn.execute(
            "SELECT coverage_value_band, COUNT(*) AS row_count FROM card_intelligence GROUP BY coverage_value_band"
        ).fetchall():
            key = _normalize_coverage_value_band(row["coverage_value_band"])
            if key:
                value_band_counts[key] = _safe_int(row["row_count"])
        top_rows = conn.execute(
            """
            SELECT
                c.canonical_code AS card_code,
                c.card_name,
                ci.dossier_gap_class,
                ci.coverage_value_score,
                ci.coverage_value_band,
                ci.coverage_gap_summary,
                ci.revalidation_status,
                ci.revalidation_reason,
                ci.revalidation_priority_score,
                ci.revalidation_priority_bucket,
                ci.publication_readiness,
                ci.publish_status,
                ci.publication_candidate_score,
                ci.confidence_score,
                ci.revalidation_updated_at
            FROM card_intelligence ci
            JOIN cards c
                ON c.id = ci.card_id
            WHERE trim(coalesce(ci.revalidation_status, '')) != ''
            ORDER BY
                CASE trim(coalesce(ci.revalidation_status, ''))
                    WHEN 'recheck_soon' THEN 0
                    WHEN 'escalate_review' THEN 1
                    WHEN 'recheck_later' THEN 2
                    WHEN 'hold' THEN 3
                    ELSE 4
                END,
                COALESCE(ci.revalidation_priority_score, 0) DESC,
                c.canonical_code ASC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
        high_value_pending_row = conn.execute(
            """
            SELECT COUNT(*)
            FROM card_intelligence
            WHERE trim(coalesce(coverage_value_band, '')) = 'high_value'
              AND trim(coalesce(revalidation_status, '')) IN ('recheck_soon', 'recheck_later', 'escalate_review')
            """
        ).fetchone()
        latest = _load_metadata(conn, sync_key=REVALIDATION_METADATA_KEY)
    active_statuses = {"recheck_soon", "recheck_later", "escalate_review"}
    top_candidates = [dict(row) for row in top_rows]
    top_gap_clusters = [
        {"gap_class": key, "count": count}
        for key, count in sorted(gap_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
    ]
    return {
        "gap_counts": gap_counts,
        "revalidation_counts": revalidation_counts,
        "value_band_counts": value_band_counts,
        "revalidation_candidate_count": sum(count for key, count in revalidation_counts.items() if key in active_statuses),
        "high_value_pending_count": _safe_int(high_value_pending_row[0] if high_value_pending_row is not None else 0),
        "stale_dossier_count": _safe_int(gap_counts.get("stale_dossier")),
        "top_gap_clusters": top_gap_clusters,
        "top_candidates": top_candidates,
        "recently_promoted": list(latest.get("recently_promoted") or []),
        "latest": latest,
    }


def refresh_revalidation_candidate(
    *,
    card_code: str,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
    decision_source: str = "revalidation_refresh",
) -> dict[str, Any]:
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    summary = build_revalidation_candidate_summary(
        card_code=card_code,
        project_db_path=project_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
    )
    if not str(summary.get("card_code") or "").strip():
        return {"ok": False, "error": "No canonical card target was resolved."}
    with closing(connect_catalog_db(project_path)) as conn:
        _upsert_publication_readiness(conn, summary)
        queue_update = _upsert_review_queue_entry(conn, summary=summary, forced=False, decision_source=decision_source)
        _persist_revalidation_summary(conn, summary)
        _store_metadata(
            conn,
            sync_key=REVALIDATION_METADATA_KEY,
            payload={
                "updated_at": _utc_now_timestamp(),
                "source": decision_source,
                "selected_count": 1,
                "selected_cards": [
                    {
                        "card_code": str(summary.get("card_code") or "").strip(),
                        "dossier_gap_class": str(summary.get("dossier_gap_class") or "").strip(),
                        "primary_objective": str(summary.get("primary_objective") or "").strip(),
                        "objective_tags": list(summary.get("objective_tags") or []),
                        "revalidation_status": str(summary.get("revalidation_status") or "").strip(),
                        "revalidation_priority_score": _safe_float(summary.get("revalidation_priority_score")),
                        "coverage_value_band": str(summary.get("coverage_value_band") or "").strip(),
                    }
                ],
                "recently_promoted": [],
            },
        )
        conn.commit()
    return {"ok": True, "summary": summary, "queue_update": queue_update}


def refresh_revalidation_planning_batch(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
    limit: int = DEFAULT_READINESS_BATCH_LIMIT,
) -> dict[str, Any]:
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    bounded_limit = max(1, min(_safe_int(limit or DEFAULT_READINESS_BATCH_LIMIT), MAX_EXECUTION_LIMIT))
    candidate_pool_limit = max(bounded_limit * 4, 160)
    with closing(connect_catalog_db(project_path)) as conn:
        rows = conn.execute(
            """
            SELECT
                c.canonical_code AS card_code
            FROM card_intelligence ci
            JOIN cards c
                ON c.id = ci.card_id
            WHERE trim(coalesce(ci.projection_sections_json, '')) != ''
               OR trim(coalesce(ci.publication_readiness, '')) != ''
               OR trim(coalesce(ci.publish_status, '')) != ''
            ORDER BY
                CASE
                    WHEN trim(coalesce(ci.publish_status, '')) IN ('publish_ready', 'publish_requires_review') THEN 0
                    WHEN trim(coalesce(ci.publication_readiness, '')) IN ('ready_for_publish_candidate', 'ready_for_review') THEN 1
                    WHEN trim(coalesce(ci.approval_state, '')) = 'approved_for_candidate'
                      OR trim(coalesce(ci.promotion_state, '')) IN ('candidate_only', 'review_approved_candidate') THEN 2
                    WHEN upper(trim(coalesce(c.rarity, ''))) = 'L' THEN 3
                    WHEN coalesce(ci.meta_relevance_score, 0) > 0 THEN 4
                    WHEN trim(coalesce(ci.deck_usage_summary, '')) != '' THEN 5
                    WHEN trim(coalesce(ci.rulings_summary, '')) != '' OR trim(coalesce(ci.legality_note, '')) != '' THEN 6
                    WHEN ci.price_value IS NOT NULL THEN 7
                    ELSE 8
                END,
                CASE
                    WHEN trim(coalesce(ci.publication_readiness, '')) = 'ready_for_publish_candidate' THEN 0
                    WHEN trim(coalesce(ci.publication_readiness, '')) = 'ready_for_review' THEN 1
                    WHEN trim(coalesce(ci.publish_status, '')) = 'publish_ready' THEN 2
                    WHEN trim(coalesce(ci.publish_status, '')) = 'publish_requires_review' THEN 3
                    ELSE 4
                END,
                COALESCE(ci.publication_candidate_score, 0) DESC,
                COALESCE(ci.meta_relevance_score, 0) DESC,
                COALESCE(ci.last_priority_score, 0) DESC,
                COALESCE(ci.confidence_score, 0) DESC,
                c.canonical_code ASC
            LIMIT ?
            """,
            (candidate_pool_limit,),
        ).fetchall()
    analyzed = [
        build_revalidation_candidate_summary(
            card_code=str(row["card_code"] or "").strip(),
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )
        for row in rows
        if str(row["card_code"] or "").strip()
    ]
    analyzed.sort(
        key=lambda item: (
            -_safe_float(item.get("revalidation_priority_score")),
            -_safe_float(item.get("coverage_value_score")),
            -_safe_float(item.get("candidate_score")),
            str(item.get("card_code") or ""),
        )
    )
    selected = analyzed[:bounded_limit]
    queue_updates: list[dict[str, Any]] = []
    promoted: list[dict[str, Any]] = []
    with closing(connect_catalog_db(project_path)) as conn:
        for summary in selected:
            before_readiness = str(summary.get("stored_readiness_state") or "").strip()
            before_publish = str(summary.get("stored_publish_status") or "").strip()
            _upsert_publication_readiness(conn, summary)
            queue_update = _upsert_review_queue_entry(conn, summary=summary, forced=False, decision_source="revalidate.plan_revalidation_batch")
            _persist_revalidation_summary(conn, summary)
            queue_updates.append(queue_update)
            after_readiness = str(summary.get("readiness_state") or "").strip()
            after_publish = str(summary.get("publish_status") or "").strip()
            promoted_readiness = before_readiness in {"", "blocked_by_guardrail", "not_ready"} and after_readiness in {"ready_for_publish_candidate", "ready_for_review"}
            promoted_publish = before_publish == "" and after_publish in {"publish_ready", "publish_requires_review"}
            if promoted_readiness or promoted_publish:
                promoted.append(
                    {
                        "card_code": str(summary.get("card_code") or "").strip(),
                        "readiness_state": after_readiness,
                        "publish_status": after_publish,
                        "candidate_score": _safe_float(summary.get("candidate_score")),
                        "dossier_gap_class": str(summary.get("dossier_gap_class") or "").strip(),
                    }
                )
        _store_metadata(
            conn,
            sync_key=REVALIDATION_METADATA_KEY,
            payload={
                "updated_at": _utc_now_timestamp(),
                "source": "revalidate.plan_revalidation_batch",
                "selected_count": len(selected),
                "selected_cards": [
                    {
                        "card_code": str(item.get("card_code") or "").strip(),
                        "dossier_gap_class": str(item.get("dossier_gap_class") or "").strip(),
                        "primary_objective": str(item.get("primary_objective") or "").strip(),
                        "objective_tags": list(item.get("objective_tags") or []),
                        "revalidation_status": str(item.get("revalidation_status") or "").strip(),
                        "revalidation_priority_score": _safe_float(item.get("revalidation_priority_score")),
                        "coverage_value_band": str(item.get("coverage_value_band") or "").strip(),
                    }
                    for item in selected[:12]
                ],
                "recently_promoted": promoted[:12],
                "queue_updates": queue_updates[:12],
            },
        )
        conn.commit()
    return {
        "ok": True,
        "selected_count": len(selected),
        "selected_cards": [
            {
                "card_code": str(item.get("card_code") or "").strip(),
                "dossier_gap_class": str(item.get("dossier_gap_class") or "").strip(),
                "primary_objective": str(item.get("primary_objective") or "").strip(),
                "objective_tags": list(item.get("objective_tags") or []),
                "revalidation_status": str(item.get("revalidation_status") or "").strip(),
                "revalidation_priority_score": _safe_float(item.get("revalidation_priority_score")),
                "coverage_value_band": str(item.get("coverage_value_band") or "").strip(),
                "publish_status": str(item.get("publish_status") or "").strip(),
            }
            for item in selected
        ],
        "recently_promoted": promoted,
        "queue_updates": queue_updates,
        "summary": load_revalidation_summary(project_db_path=project_path, limit=8),
    }


def _expansion_status_snapshot(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "readiness_state": str(summary.get("readiness_state") or "").strip(),
        "publish_status": str(summary.get("publish_status") or "").strip(),
        "dossier_gap_class": str(summary.get("dossier_gap_class") or "").strip(),
        "revalidation_status": str(summary.get("revalidation_status") or "").strip(),
        "confidence": round(_safe_float(summary.get("confidence")), 3),
        "candidate_score": round(_safe_float(summary.get("candidate_score")), 3),
        "coverage_value_band": str(summary.get("coverage_value_band") or "").strip(),
    }


def _expansion_status_movement(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    movement: dict[str, Any] = {}
    for field_name in (
        "readiness_state",
        "publish_status",
        "dossier_gap_class",
        "revalidation_status",
        "coverage_value_band",
        "confidence",
        "candidate_score",
    ):
        before_value = before.get(field_name)
        after_value = after.get(field_name)
        if before_value != after_value:
            movement[field_name] = {
                "before": before_value,
                "after": after_value,
            }
    return movement


def _expansion_enrichment_path(candidate: dict[str, Any], summary: dict[str, Any]) -> list[str]:
    objectives = _normalize_expansion_objectives(
        list(candidate.get("priority_objectives") or []) + list(summary.get("objective_tags") or [])
    )
    path = ["incremental_priority_sync", "dossier_projection"]
    if any(item in objectives for item in ("usage_meta_fill", "leader_profile_expand")):
        path.append("deck_usage_meta_refresh")
    if "source_depth_fill" in objectives:
        path.append("source_support_revalidation")
    if "legality_recheck" in objectives:
        path.append("rules_legality_revalidation")
    if "stale_refresh" in objectives:
        path.append("publication_readiness_recheck")
    return path


def _diagnose_selected_card_blocker(summary: dict[str, Any]) -> dict[str, Any]:
    readiness_state = str(summary.get("readiness_state") or "").strip()
    publish_status = str(summary.get("publish_status") or "").strip()
    review_reason = str(summary.get("review_reason") or "").strip().lower()
    approval_state = _normalize_approval_state(summary.get("approval_state"))
    promotion_state = _normalize_promotion_state(summary.get("promotion_state"))
    queue_status = str(summary.get("queue_status") or "").strip().lower()
    guardrail_label = str(summary.get("guardrail_label") or "").strip()
    stage_guardrail_label = str(summary.get("stage_guardrail_label") or "").strip()
    stage_state = str(summary.get("stage_state") or "").strip()
    stageable = bool(summary.get("stageable"))
    source_agreement = dict(summary.get("source_agreement") or {})
    agreement_level = str(source_agreement.get("agreement_level") or "").strip().lower()
    source_count = _safe_int(summary.get("source_count"))
    gap_class = str(summary.get("dossier_gap_class") or "").strip().lower()
    revalidation_status = str(summary.get("revalidation_status") or "").strip().lower()
    legality_sensitive = bool(summary.get("legality_sensitive"))
    strong_sections = {str(item).strip() for item in list(summary.get("strong_sections") or []) if str(item).strip()}
    has_usage = bool(str(summary.get("deck_usage_summary") or "").strip()) or bool({"usage_meta", "gameplay_role"} & strong_sections)
    has_rules_legality = bool(str(summary.get("rulings_summary") or "").strip() or str(summary.get("legality_note") or "").strip()) or bool({"rulings", "legality"} & strong_sections)

    if agreement_level in {"conflict", "single_source", "partial"} or source_count <= 1 or gap_class in {"thin_source_support", "weak_provenance"}:
        next_action = "revalidate.refresh_usage_meta_candidate" if has_usage else "revalidate.refresh_partial_candidate"
        if legality_sensitive or gap_class == "missing_rules_legality":
            next_action = "revalidate.refresh_rules_sensitive_candidate"
        return {
            "readiness_state": readiness_state,
            "publish_status": publish_status,
            "dominant_blocker": "more_distinct_source_support",
            "minimum_unlock_condition": "Add a distinct corroborating source and raise agreement above single-source or partial support.",
            "recommended_next_bounded_backend_action": next_action,
        }

    if legality_sensitive or gap_class == "missing_rules_legality" or (review_reason == "legality_sensitive" and not has_rules_legality):
        return {
            "readiness_state": readiness_state,
            "publish_status": publish_status,
            "dominant_blocker": "official_legality_rules_confirmation",
            "minimum_unlock_condition": "Store official legality or rules confirmation so the dossier no longer depends on inferred or incomplete timing.",
            "recommended_next_bounded_backend_action": "revalidate.refresh_rules_sensitive_candidate",
        }

    if gap_class == "missing_usage_meta" or (not has_usage and "usage_meta_fill" in _normalize_expansion_objectives(summary.get("objective_tags") or [])):
        return {
            "readiness_state": readiness_state,
            "publish_status": publish_status,
            "dominant_blocker": "fresher_usage_meta_evidence",
            "minimum_unlock_condition": "Refresh deck usage and leader/meta coverage so usage-backed sections become materially stronger.",
            "recommended_next_bounded_backend_action": "revalidate.refresh_usage_meta_candidate",
        }

    if approval_state != "approved_for_candidate" or promotion_state != "review_approved_candidate":
        next_action = "review.approve_candidate" if queue_status == "pending" else "review.mark_review_required"
        if publish_status == "publish_requires_review" and queue_status in {"", "resolved"}:
            next_action = "review.publish_candidate_summary"
        return {
            "readiness_state": readiness_state,
            "publish_status": publish_status,
            "dominant_blocker": "explicit_review_approval_movement",
            "minimum_unlock_condition": "Record an explicit review decision so approval and promotion state move beyond pending or implicit readiness only.",
            "recommended_next_bounded_backend_action": next_action,
        }

    if not stageable or stage_state == "blocked_from_staging" or stage_guardrail_label.lower() in {"blocked", "review required"}:
        return {
            "readiness_state": readiness_state,
            "publish_status": publish_status,
            "dominant_blocker": "staging_guardrail_clearance",
            "minimum_unlock_condition": "Clear staging guardrails so the approved candidate is stageable without blocked or review-bound stage status.",
            "recommended_next_bounded_backend_action": "stage.stage_candidate",
        }

    next_action = "revalidate.verify_stale_candidate" if gap_class == "stale_dossier" or revalidation_status in {"recheck_later", "hold"} else "revalidate.refresh_partial_candidate"
    return {
        "readiness_state": readiness_state,
        "publish_status": publish_status,
        "dominant_blocker": "defer_recheck_later",
        "minimum_unlock_condition": "Wait for fresher evidence or a stronger verification window before spending more bounded backend work here.",
        "recommended_next_bounded_backend_action": next_action,
    }


def diagnose_leader_staple_expansion_blockers(
    *,
    limit: int = 12,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
) -> dict[str, Any]:
    expansion = run_leader_staple_intelligence_expansion(
        limit=limit,
        project_db_path=project_db_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
    )
    diagnosed_cards: list[dict[str, Any]] = []
    blocker_counts: dict[str, int] = {}
    for item in list(expansion.get("selected_cards") or []):
        diagnosis = dict(item.get("blocker_diagnosis") or {})
        if not diagnosis:
            summary = build_revalidation_candidate_summary(
                card_code=str(item.get("card_code") or "").strip(),
                project_db_path=project_db_path,
                canonical_dossier_db_path=canonical_dossier_db_path,
                rules_db_path=rules_db_path,
                deck_intel_db_path=deck_intel_db_path,
                prices_path=prices_path,
            )
            diagnosis = _diagnose_selected_card_blocker(summary)
        blocker_key = str(diagnosis.get("dominant_blocker") or "").strip()
        if blocker_key:
            blocker_counts[blocker_key] = blocker_counts.get(blocker_key, 0) + 1
        diagnosed_cards.append(
            {
                "card_code": str(item.get("card_code") or "").strip(),
                "card_name": str(item.get("card_name") or "").strip(),
                "selected_reason": str(item.get("selected_reason") or "").strip(),
                "objectives": list(item.get("objectives") or []),
                "enrichment_path": list(item.get("enrichment_path") or []),
                "remaining_blockers": list(item.get("remaining_blockers") or []),
                "blocker_diagnosis": diagnosis,
            }
        )

    return {
        "ok": True,
        "selected_count": len(diagnosed_cards),
        "blocker_counts": blocker_counts,
        "selected_cards": diagnosed_cards,
        "expansion": expansion,
    }


def _agreement_strength(level: str) -> int:
    return {
        "conflict": 0,
        "single_source": 1,
        "partial": 2,
        "majority": 3,
        "full": 4,
    }.get(str(level or "").strip().lower(), 0)


def _source_support_snapshot(summary: dict[str, Any]) -> dict[str, Any]:
    agreement = dict(summary.get("source_agreement") or {})
    return {
        "source_count": _safe_int(summary.get("source_count") or agreement.get("source_count")),
        "agreement_level": str(agreement.get("agreement_level") or "").strip().lower(),
    }


def _runtime_distinct_source_ids(engine: MiruLearningEngine, card_code: str) -> list[str]:
    return sorted(
        {
            str(item.get("source_id") or "").strip().lower()
            for item in engine.fetch_dossier_source_records(card_code)
            if str(item.get("source_id") or "").strip()
        }
    )


def _review_proximity_rank(summary: dict[str, Any]) -> int:
    readiness_state = str(summary.get("readiness_state") or "").strip().lower()
    publish_status = str(summary.get("publish_status") or "").strip().lower()
    if publish_status == "publish_ready":
        return 4
    if readiness_state == "ready_for_publish_candidate":
        return 3
    if publish_status == "publish_requires_review" or readiness_state == "ready_for_review":
        return 2
    if publish_status == "publish_deferred" or readiness_state == "not_ready":
        return 1
    return 0


def _run_source_support_backend_action(
    *,
    action_id: str,
    card_code: str,
    project_db_path: str | Path,
    canonical_dossier_db_path: str | Path,
    rules_db_path: str | Path,
    deck_intel_db_path: str | Path,
    prices_path: str | Path,
) -> dict[str, Any]:
    normalized_action = str(action_id or "").strip()
    if normalized_action in {
        "revalidate.refresh_partial_candidate",
        "revalidate.verify_stale_candidate",
        "revalidate.refresh_rules_sensitive_candidate",
        "revalidate.refresh_usage_meta_candidate",
    }:
        return refresh_revalidation_candidate(
            card_code=card_code,
            project_db_path=project_db_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
            decision_source=normalized_action,
        )
    return execute_governed_action(
        action_id=normalized_action,
        target_card_code=card_code,
        project_db_path=project_db_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
    )


def advance_leader_staple_source_support_batch(
    *,
    limit: int = 24,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
) -> dict[str, Any]:
    bounded_limit = max(1, min(_safe_int(limit or 24), MAX_EXECUTION_LIMIT))
    diagnosed = diagnose_leader_staple_expansion_blockers(
        limit=bounded_limit,
        project_db_path=project_db_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
    )
    eligible_cards = [
        item
        for item in list(diagnosed.get("selected_cards") or [])
        if str(((item.get("blocker_diagnosis") or {}).get("dominant_blocker")) or "").strip() == "more_distinct_source_support"
    ]
    if not eligible_cards:
        return {
            "ok": True,
            "total_cards_processed": 0,
            "eligible_count": 0,
            "blocker_counts_before": dict(diagnosed.get("blocker_counts") or {}),
            "blocker_counts_after": dict(diagnosed.get("blocker_counts") or {}),
            "blocker_distribution_changed": False,
            "selected_cards": [],
            "diagnostics": diagnosed,
        }

    action_groups: dict[str, list[str]] = {}
    processed_cards: list[dict[str, Any]] = []
    blocker_counts_after: dict[str, int] = {}
    improved_source_support_cards: list[str] = []
    readiness_changed_cards: list[str] = []
    publish_changed_cards: list[str] = []
    moved_closer_cards: list[str] = []

    for item in eligible_cards:
        card_code = str(item.get("card_code") or "").strip().upper()
        before_summary = build_revalidation_candidate_summary(
            card_code=card_code,
            project_db_path=project_db_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )
        before_diagnosis = dict(item.get("blocker_diagnosis") or {})
        action_id = str(before_diagnosis.get("recommended_next_bounded_backend_action") or "").strip() or "revalidate.refresh_partial_candidate"
        action_groups.setdefault(action_id, []).append(card_code)
        before_support = _source_support_snapshot(before_summary)
        before_proximity = _review_proximity_rank(before_summary)

        action_result = _run_source_support_backend_action(
            action_id=action_id,
            card_code=card_code,
            project_db_path=project_db_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )
        after_summary = dict(action_result.get("summary") or {})
        if not after_summary:
            after_summary = build_revalidation_candidate_summary(
                card_code=card_code,
                project_db_path=project_db_path,
                canonical_dossier_db_path=canonical_dossier_db_path,
                rules_db_path=rules_db_path,
                deck_intel_db_path=deck_intel_db_path,
                prices_path=prices_path,
            )
        after_diagnosis = _diagnose_selected_card_blocker(after_summary)
        after_support = _source_support_snapshot(after_summary)
        after_proximity = _review_proximity_rank(after_summary)
        after_blocker = str(after_diagnosis.get("dominant_blocker") or "").strip()
        if after_blocker:
            blocker_counts_after[after_blocker] = blocker_counts_after.get(after_blocker, 0) + 1

        improved_source_support = (
            after_support["source_count"] > before_support["source_count"]
            or _agreement_strength(after_support["agreement_level"]) > _agreement_strength(before_support["agreement_level"])
            or (
                str(before_diagnosis.get("dominant_blocker") or "").strip() == "more_distinct_source_support"
                and after_blocker != "more_distinct_source_support"
            )
        )
        readiness_changed = str(before_summary.get("readiness_state") or "").strip() != str(after_summary.get("readiness_state") or "").strip()
        publish_changed = str(before_summary.get("publish_status") or "").strip() != str(after_summary.get("publish_status") or "").strip()
        moved_closer = after_proximity > before_proximity

        if improved_source_support:
            improved_source_support_cards.append(card_code)
        if readiness_changed:
            readiness_changed_cards.append(card_code)
        if publish_changed:
            publish_changed_cards.append(card_code)
        if moved_closer:
            moved_closer_cards.append(card_code)

        processed_cards.append(
            {
                "card_code": card_code,
                "card_name": str(item.get("card_name") or "").strip(),
                "action_id": action_id,
                "before_blocker": before_diagnosis,
                "after_blocker": after_diagnosis,
                "before_source_support": before_support,
                "after_source_support": after_support,
                "before_readiness_state": str(before_summary.get("readiness_state") or "").strip(),
                "after_readiness_state": str(after_summary.get("readiness_state") or "").strip(),
                "before_publish_status": str(before_summary.get("publish_status") or "").strip(),
                "after_publish_status": str(after_summary.get("publish_status") or "").strip(),
                "before_review_proximity": before_proximity,
                "after_review_proximity": after_proximity,
                "improved_distinct_source_support": improved_source_support,
                "readiness_state_changed": readiness_changed,
                "publish_status_changed": publish_changed,
                "moved_closer_to_publish": moved_closer,
                "next_blocker_after_pass": after_blocker,
            }
        )

    representative_examples = processed_cards[: min(len(processed_cards), 8)]
    return {
        "ok": True,
        "total_cards_processed": len(processed_cards),
        "eligible_count": len(eligible_cards),
        "action_groups": action_groups,
        "cards_with_improved_distinct_source_support": improved_source_support_cards,
        "cards_with_readiness_state_change": readiness_changed_cards,
        "cards_with_publish_status_change": publish_changed_cards,
        "cards_moved_closer_to_publish": moved_closer_cards,
        "blocker_counts_before": dict(diagnosed.get("blocker_counts") or {}),
        "blocker_counts_after": blocker_counts_after,
        "blocker_distribution_changed": dict(diagnosed.get("blocker_counts") or {}) != blocker_counts_after,
        "selected_cards": processed_cards,
        "representative_examples": representative_examples,
        "diagnostics": diagnosed,
    }


def advance_leader_staple_approved_source_support_batch(
    *,
    limit: int = 24,
    per_card_source_limit: int = 4,
    preferred_source_ids: Sequence[str] | None = None,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    runtime_dossier_db_path: str | Path = DEFAULT_RUNTIME_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
) -> dict[str, Any]:
    bounded_limit = max(1, min(_safe_int(limit or 24), MAX_EXECUTION_LIMIT))
    bounded_lane_limit = max(1, min(_safe_int(per_card_source_limit or 4), 8))
    diagnosed = diagnose_leader_staple_expansion_blockers(
        limit=bounded_limit,
        project_db_path=project_db_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
    )
    eligible_cards = [
        item
        for item in list(diagnosed.get("selected_cards") or [])
        if str(((item.get("blocker_diagnosis") or {}).get("dominant_blocker")) or "").strip() == "more_distinct_source_support"
    ]
    if not eligible_cards:
        return {
            "ok": True,
            "backend_only": True,
            "total_cards_processed": 0,
            "eligible_count": 0,
            "cards_with_new_distinct_approved_source": [],
            "cards_with_no_new_source_gain": [],
            "source_lanes_attempted": {},
            "source_lanes_with_new_support": {},
            "cards_with_readiness_state_change": [],
            "cards_with_publish_status_change": [],
            "cards_with_review_proximity_change": [],
            "cards_moved_closer_to_publish": [],
            "blocker_counts_before": dict(diagnosed.get("blocker_counts") or {}),
            "blocker_counts_after": dict(diagnosed.get("blocker_counts") or {}),
            "blocker_distribution_changed": False,
            "selected_cards": [],
            "representative_examples": [],
            "diagnostics": diagnosed,
        }

    engine = MiruLearningEngine(
        dossier_db_path=runtime_dossier_db_path,
        verified_dossier_db_path=canonical_dossier_db_path,
        project_db_path=project_db_path,
    )
    engine.ensure_datastores()

    source_lane_attempted_counts: dict[str, int] = {}
    source_lane_productive_counts: dict[str, int] = {}
    processed_cards: list[dict[str, Any]] = []
    blocker_counts_after: dict[str, int] = {}
    new_source_cards: list[str] = []
    no_new_source_cards: list[str] = []
    readiness_changed_cards: list[str] = []
    publish_changed_cards: list[str] = []
    review_proximity_changed_cards: list[str] = []
    moved_closer_cards: list[str] = []

    source_priority = tuple(
        str(item or "").strip().lower()
        for item in (
            preferred_source_ids
            or (
                "official-deck-features",
                "official-rules-faq",
                "official-restriction-notices",
                "official-errata-cards",
                "official-cardlist",
                "community-cardlist",
                "reputable-card-db",
            )
        )
        if str(item or "").strip()
    )

    for item in eligible_cards:
        card_code = str(item.get("card_code") or "").strip().upper()
        before_summary = build_revalidation_candidate_summary(
            card_code=card_code,
            project_db_path=project_db_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )
        before_diagnosis = dict(item.get("blocker_diagnosis") or {})
        before_support = _source_support_snapshot(before_summary)
        before_proximity = _review_proximity_rank(before_summary)
        before_runtime_sources = _runtime_distinct_source_ids(engine, card_code)

        planned_lanes = engine.plan_approved_source_support_lanes(
            card_code=card_code,
            limit=bounded_lane_limit,
            preferred_source_ids=source_priority,
        )
        lane_attempts: list[dict[str, Any]] = []
        for lane in planned_lanes:
            source_id = str(lane.get("source_id") or "").strip().lower()
            governance = dict(lane.get("governance") or {})
            attempt_entry = {
                "source_id": source_id,
                "source_name": str(lane.get("source_name") or "").strip(),
                "trust_tier": _safe_int(lane.get("trust_tier")),
                "execution_kind": str(lane.get("execution_kind") or "").strip(),
                "already_present": bool(lane.get("already_present")),
                "has_adapter_input": bool(lane.get("has_adapter_input")),
                "attemptable": bool(lane.get("attemptable")),
                "governance": governance,
            }
            if bool(lane.get("attemptable")):
                source_lane_attempted_counts[source_id] = source_lane_attempted_counts.get(source_id, 0) + 1
                result = engine.verify_card_from_source_lane(
                    card_code=card_code,
                    source_id=source_id,
                    task_payload=dict(lane.get("task_payload") or {}),
                    execution_kind=str(lane.get("execution_kind") or "").strip() or "learning-intake",
                    task_type="verify_official_fields",
                    suppress_notifications=True,
                )
                if bool(result.get("new_distinct_source_added")):
                    source_lane_productive_counts[source_id] = source_lane_productive_counts.get(source_id, 0) + 1
                attempt_entry.update(
                    {
                        "attempted": bool(result.get("attempted")),
                        "skipped": bool(result.get("skipped")),
                        "reason": str(result.get("reason") or "").strip(),
                        "source_reference": str(result.get("source_reference") or "").strip(),
                        "new_source_ids": list(result.get("new_source_ids") or []),
                        "new_distinct_source_added": bool(result.get("new_distinct_source_added")),
                        "fact_acceptance": dict(result.get("fact_acceptance") or {}),
                        "project_sync": dict(result.get("project_sync") or {}),
                    }
                )
            else:
                if bool(lane.get("already_present")):
                    reason = "Approved source lane already exists in the runtime dossier."
                elif not bool(lane.get("has_adapter_input")):
                    reason = "No approved snapshot or URL input was available for this source lane."
                else:
                    reason = str(governance.get("reason") or governance.get("policy_summary") or "").strip()
                attempt_entry.update(
                    {
                        "attempted": False,
                        "skipped": True,
                        "reason": reason,
                        "new_source_ids": [],
                        "new_distinct_source_added": False,
                    }
                )
            lane_attempts.append(attempt_entry)

        refresh_result = refresh_revalidation_candidate(
            card_code=card_code,
            project_db_path=project_db_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
            decision_source="approved_source_support_expansion",
        )
        after_summary = dict(refresh_result.get("summary") or {})
        if not after_summary:
            after_summary = build_revalidation_candidate_summary(
                card_code=card_code,
                project_db_path=project_db_path,
                canonical_dossier_db_path=canonical_dossier_db_path,
                rules_db_path=rules_db_path,
                deck_intel_db_path=deck_intel_db_path,
                prices_path=prices_path,
            )
        after_diagnosis = _diagnose_selected_card_blocker(after_summary)
        after_support = _source_support_snapshot(after_summary)
        after_proximity = _review_proximity_rank(after_summary)
        after_runtime_sources = _runtime_distinct_source_ids(engine, card_code)
        new_runtime_sources = sorted(set(after_runtime_sources) - set(before_runtime_sources))
        after_blocker = str(after_diagnosis.get("dominant_blocker") or "").strip()
        if after_blocker:
            blocker_counts_after[after_blocker] = blocker_counts_after.get(after_blocker, 0) + 1

        gained_new_source = bool(new_runtime_sources)
        readiness_changed = str(before_summary.get("readiness_state") or "").strip() != str(after_summary.get("readiness_state") or "").strip()
        publish_changed = str(before_summary.get("publish_status") or "").strip() != str(after_summary.get("publish_status") or "").strip()
        review_proximity_changed = after_proximity != before_proximity
        moved_closer = after_proximity > before_proximity
        projected_source_support_improved = (
            after_support["source_count"] > before_support["source_count"]
            or _agreement_strength(after_support["agreement_level"]) > _agreement_strength(before_support["agreement_level"])
            or (
                str(before_diagnosis.get("dominant_blocker") or "").strip() == "more_distinct_source_support"
                and after_blocker != "more_distinct_source_support"
            )
        )

        if gained_new_source:
            new_source_cards.append(card_code)
        else:
            no_new_source_cards.append(card_code)
        if readiness_changed:
            readiness_changed_cards.append(card_code)
        if publish_changed:
            publish_changed_cards.append(card_code)
        if review_proximity_changed:
            review_proximity_changed_cards.append(card_code)
        if moved_closer:
            moved_closer_cards.append(card_code)

        processed_cards.append(
            {
                "card_code": card_code,
                "card_name": str(item.get("card_name") or "").strip(),
                "before_blocker": before_diagnosis,
                "after_blocker": after_diagnosis,
                "before_runtime_sources": before_runtime_sources,
                "after_runtime_sources": after_runtime_sources,
                "new_runtime_sources": new_runtime_sources,
                "before_source_support": before_support,
                "after_source_support": after_support,
                "projected_source_support_improved": projected_source_support_improved,
                "before_readiness_state": str(before_summary.get("readiness_state") or "").strip(),
                "after_readiness_state": str(after_summary.get("readiness_state") or "").strip(),
                "before_publish_status": str(before_summary.get("publish_status") or "").strip(),
                "after_publish_status": str(after_summary.get("publish_status") or "").strip(),
                "before_review_proximity": before_proximity,
                "after_review_proximity": after_proximity,
                "gained_new_distinct_approved_source": gained_new_source,
                "readiness_state_changed": readiness_changed,
                "publish_status_changed": publish_changed,
                "review_proximity_changed": review_proximity_changed,
                "moved_closer_to_publish": moved_closer,
                "source_lane_attempts": lane_attempts,
                "remaining_blocker_after_pass": after_blocker,
            }
        )

    representative_examples = processed_cards[: min(len(processed_cards), 8)]
    return {
        "ok": True,
        "backend_only": True,
        "total_cards_processed": len(processed_cards),
        "eligible_count": len(eligible_cards),
        "cards_with_new_distinct_approved_source": new_source_cards,
        "cards_with_no_new_source_gain": no_new_source_cards,
        "source_lanes_attempted": source_lane_attempted_counts,
        "source_lanes_with_new_support": source_lane_productive_counts,
        "cards_with_readiness_state_change": readiness_changed_cards,
        "cards_with_publish_status_change": publish_changed_cards,
        "cards_with_review_proximity_change": review_proximity_changed_cards,
        "cards_moved_closer_to_publish": moved_closer_cards,
        "blocker_counts_before": dict(diagnosed.get("blocker_counts") or {}),
        "blocker_counts_after": blocker_counts_after,
        "blocker_distribution_changed": dict(diagnosed.get("blocker_counts") or {}) != blocker_counts_after,
        "selected_cards": processed_cards,
        "representative_examples": representative_examples,
        "diagnostics": diagnosed,
    }


def _summarize_new_lane_payload_readiness(
    engine: MiruLearningEngine,
    *,
    source_id: str,
    cohort_card_codes: set[str],
) -> dict[str, Any]:
    payload = engine.resolve_source_task_payload(source_id, {})
    payload_ready = bool(engine.source_payload_has_adapter_input(payload))
    payload_mode = (
        "inline_payload"
        if isinstance(payload.get("payload"), dict) and payload.get("payload")
        else ("snapshot_path" if str(payload.get("snapshot_path") or "").strip() else ("snapshot_url" if str(payload.get("snapshot_url") or "").strip() else "none"))
    )
    records = []
    error = ""
    if payload_ready:
        try:
            records = engine.fetch_official_source_records(
                source_id=source_id,
                task_payload=payload,
            )
        except Exception as exc:
            error = str(exc)
            records = []
    normalized_card_codes = {
        str(getattr(item, "card_code", "") or "").strip().upper()
        for item in records
        if str(getattr(item, "card_code", "") or "").strip()
    }
    mapped_cards = sorted(normalized_card_codes & cohort_card_codes)
    payload_summary = {}
    if isinstance(payload.get("payload"), dict):
        payload_summary = dict((payload.get("payload") or {}).get("source") or {})
    return {
        "source_id": str(source_id or "").strip().lower(),
        "payload_ready": payload_ready,
        "payload_mode": payload_mode,
        "payload_origin": str(payload_summary.get("payload_origin") or "").strip(),
        "snapshot_path": str(payload.get("snapshot_path") or "").strip(),
        "snapshot_url": str(payload.get("snapshot_url") or "").strip(),
        "normalized_record_count": len(records),
        "mapped_cohort_card_count": len(mapped_cards),
        "mapped_cohort_cards": mapped_cards[:12],
        "error": error,
    }


def verify_new_approved_source_lane_snapshot_support(
    *,
    limit: int = 24,
    per_card_source_limit: int = 4,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    runtime_dossier_db_path: str | Path = DEFAULT_RUNTIME_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
) -> dict[str, Any]:
    bounded_limit = max(1, min(_safe_int(limit or 24), MAX_EXECUTION_LIMIT))
    bounded_lane_limit = max(1, min(_safe_int(per_card_source_limit or 4), 8))
    batch_result = advance_leader_staple_approved_source_support_batch(
        limit=bounded_limit,
        per_card_source_limit=bounded_lane_limit,
        preferred_source_ids=NEW_APPROVED_SOURCE_SUPPORT_LANES,
        project_db_path=project_db_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        runtime_dossier_db_path=runtime_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
    )
    diagnosed = dict(batch_result.get("diagnostics") or {})
    cohort_card_codes = {
        str(item.get("card_code") or "").strip().upper()
        for item in list(diagnosed.get("selected_cards") or [])
        if str(((item.get("blocker_diagnosis") or {}).get("dominant_blocker")) or "").strip() == "more_distinct_source_support"
        and str(item.get("card_code") or "").strip()
    }
    engine = MiruLearningEngine(
        dossier_db_path=runtime_dossier_db_path,
        verified_dossier_db_path=canonical_dossier_db_path,
        project_db_path=project_db_path,
    )
    engine.ensure_datastores()
    lane_snapshot_status = {
        source_id: _summarize_new_lane_payload_readiness(
            engine,
            source_id=source_id,
            cohort_card_codes=cohort_card_codes,
        )
        for source_id in NEW_APPROVED_SOURCE_SUPPORT_LANES
    }
    audit = audit_approved_source_roster_coverage(
        limit=bounded_limit,
        project_db_path=project_db_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        runtime_dossier_db_path=runtime_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
    )
    return {
        "ok": True,
        "backend_only": True,
        "focus_lanes": list(NEW_APPROVED_SOURCE_SUPPORT_LANES),
        "payload_ready_lanes": [
            source_id
            for source_id, summary in lane_snapshot_status.items()
            if bool(summary.get("payload_ready"))
        ],
        "lane_snapshot_status": lane_snapshot_status,
        "normalized_records_per_lane": {
            source_id: int((summary or {}).get("normalized_record_count") or 0)
            for source_id, summary in lane_snapshot_status.items()
        },
        "lane_mapped_card_counts": {
            source_id: int((summary or {}).get("mapped_cohort_card_count") or 0)
            for source_id, summary in lane_snapshot_status.items()
        },
        "total_cards_processed": int(batch_result.get("total_cards_processed") or 0),
        "cards_with_new_distinct_approved_source": list(batch_result.get("cards_with_new_distinct_approved_source") or []),
        "cards_with_no_new_source_gain": list(batch_result.get("cards_with_no_new_source_gain") or []),
        "blocker_counts_before": dict(batch_result.get("blocker_counts_before") or {}),
        "blocker_counts_after": dict(batch_result.get("blocker_counts_after") or {}),
        "blocker_distribution_changed": bool(batch_result.get("blocker_distribution_changed")),
        "cards_with_readiness_state_change": list(batch_result.get("cards_with_readiness_state_change") or []),
        "cards_with_publish_status_change": list(batch_result.get("cards_with_publish_status_change") or []),
        "cards_moved_closer_to_publish": list(batch_result.get("cards_moved_closer_to_publish") or []),
        "representative_examples": list(batch_result.get("representative_examples") or []),
        "remaining_underserved_after_snapshot_normalization": list(audit.get("underserved_after_expansion") or []),
        "batch_result": batch_result,
    }


def prepare_live_new_approved_source_payloads_and_verify(
    *,
    limit: int = 24,
    per_card_source_limit: int = 4,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    runtime_dossier_db_path: str | Path = DEFAULT_RUNTIME_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
) -> dict[str, Any]:
    engine = MiruLearningEngine(
        dossier_db_path=runtime_dossier_db_path,
        verified_dossier_db_path=canonical_dossier_db_path,
        project_db_path=project_db_path,
    )
    engine.ensure_datastores()
    preparation = engine.materialize_real_official_source_snapshots(
        source_ids=NEW_APPROVED_SOURCE_SUPPORT_LANES,
        overwrite=False,
        ingest_staging=True,
    )
    verification = verify_new_approved_source_lane_snapshot_support(
        limit=limit,
        per_card_source_limit=per_card_source_limit,
        project_db_path=project_db_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        runtime_dossier_db_path=runtime_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
    )
    missing_operational_prerequisites = {
        source_id: str(item.get("operational_prerequisite") or "").strip()
        for source_id, item in dict(preparation.get("snapshots") or {}).items()
        if not bool((item or {}).get("real_payload_ready"))
        and str((item or {}).get("operational_prerequisite") or "").strip()
    }
    return {
        "ok": True,
        "backend_only": True,
        "preparation": preparation,
        "verification": verification,
        "real_payload_ready_lanes": list(preparation.get("ready_lanes") or []),
        "record_counts_per_lane": {
            source_id: int((item or {}).get("record_count") or 0)
            for source_id, item in dict(preparation.get("snapshots") or {}).items()
        },
        "missing_operational_prerequisites": missing_operational_prerequisites,
        "cards_with_new_distinct_approved_source": list(verification.get("cards_with_new_distinct_approved_source") or []),
        "blocker_counts_before": dict(verification.get("blocker_counts_before") or {}),
        "blocker_counts_after": dict(verification.get("blocker_counts_after") or {}),
        "cards_with_readiness_state_change": list(verification.get("cards_with_readiness_state_change") or []),
        "cards_with_publish_status_change": list(verification.get("cards_with_publish_status_change") or []),
        "cards_moved_closer_to_publish": list(verification.get("cards_moved_closer_to_publish") or []),
        "representative_examples": list(verification.get("representative_examples") or []),
        "remaining_underserved_after_snapshot_normalization": list(
            verification.get("remaining_underserved_after_snapshot_normalization") or []
        ),
    }


def run_governed_autonomy_phase1_source_discovery(
    *,
    limit: int = 12,
    seed_candidates: Sequence[dict[str, Any]] | None = None,
    persist_candidates: bool = False,
    persist_assessments: bool = False,
    runtime_dossier_db_path: str | Path = DEFAULT_RUNTIME_DOSSIER_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
) -> dict[str, Any]:
    bounded_limit = max(1, min(_safe_int(limit or 12), 24))
    engine = MiruLearningEngine(
        dossier_db_path=runtime_dossier_db_path,
        verified_dossier_db_path=canonical_dossier_db_path,
        project_db_path=project_db_path,
    )
    engine.ensure_datastores()
    candidate_rows = list(seed_candidates or GOVERNED_AUTONOMY_PHASE1_SEED_CANDIDATES)[:bounded_limit]
    phase_result = engine.run_governed_source_discovery_phase1(
        candidate_rows=candidate_rows,
        limit=bounded_limit,
        persist_candidates=persist_candidates,
        persist_assessments=persist_assessments,
    )
    registry_summary = _summarize_roster_lanes(engine, build_source_registry())
    accepted_examples = list(phase_result.get("accepted_examples") or [])
    deferred_examples = list(phase_result.get("deferred_examples") or [])
    rejected_examples = list(phase_result.get("rejected_examples") or [])
    assessments = list(phase_result.get("assessments") or [])
    auto_queue_examples: list[dict[str, Any]] = []
    for item in assessments:
        summary = dict(item.get("operator_summary") or {})
        if not bool(summary.get("auto_intake_allowed")):
            continue
        candidate = dict(item.get("candidate") or {})
        auto_queue_examples.append(
            {
                "url": str(candidate.get("url") or "").strip(),
                "recommended_next_action": str(summary.get("recommended_next_action") or "").strip(),
                "queue_candidate": dict(summary.get("queue_candidate") or {}),
            }
        )
        if len(auto_queue_examples) >= 4:
            break

    return {
        "ok": True,
        "backend_only": True,
        "phase": "governed_autonomy_phase1",
        "what_phase1_allows": [
            "Discover bounded candidate source URLs and classify likely source type, trust role, permission posture, and gap usefulness.",
            "Auto-queue intake only when a discovered candidate already maps to an existing governed registry lane and the current policy gate explicitly allows the execution path.",
            "Defer promising but unapproved candidates into operator-facing registry review recommendations without self-authorizing new source lanes.",
        ],
        "what_phase1_cannot_do": [
            "Self-approve a new source lane that is not already governed in Miru's registry.",
            "Auto-intake manual-only, login-gated, unclear-permission, market-hint, or otherwise ambiguous sources.",
            "Override official Bandai truth with reference-safe or community sources.",
        ],
        "discovery_workflow": {
            "candidate_input_mode": "bounded_seeded_candidates" if candidate_rows else "stored_pending_candidates",
            "candidate_count": len(candidate_rows),
            "classification_path": [
                "heuristic discovery profile",
                "registry host/path match",
                "governance gate evaluation",
                "fail-closed autonomy decision",
            ],
            "accepted_representation": "autonomy_state=accepted with queue_learning_intake or queue_reference_safe_intake",
            "deferred_representation": "autonomy_state=deferred with recommend_registry_review or defer_manual_registry_review",
            "rejected_representation": "autonomy_state=rejected with reject_manual_only_source, reject_market_signal_source, or reject_policy_blocked_source",
        },
        "candidate_summary": {
            "total_candidates_assessed": int(phase_result.get("total_candidates_assessed") or 0),
            "accepted_count": int(phase_result.get("accepted_count") or 0),
            "deferred_count": int(phase_result.get("deferred_count") or 0),
            "rejected_count": int(phase_result.get("rejected_count") or 0),
            "recommended_action_counts": dict(phase_result.get("recommended_action_counts") or {}),
            "gap_demand_counts": dict(phase_result.get("gap_demand_counts") or {}),
        },
        "accepted_examples": accepted_examples,
        "deferred_examples": deferred_examples,
        "rejected_examples": rejected_examples,
        "auto_queue_examples": auto_queue_examples,
        "operator_summary_path": {
            "function": "run_governed_autonomy_phase1_source_discovery",
            "backend_only": True,
            "accepted_examples_present": bool(accepted_examples),
            "deferred_examples_present": bool(deferred_examples),
            "rejected_examples_present": bool(rejected_examples),
        },
        "safety_confirmations": {
            "official_truth_remains_highest": True,
            "ambiguous_or_manual_only_sources_fail_closed": True,
            "unsafe_sources_not_auto_ingested": True,
            "storefront_visibility_added": False,
        },
        "registry_context": {
            "governed_lane_count": int(registry_summary.get("lane_count") or 0),
            "category_counts": dict(registry_summary.get("category_counts") or {}),
        },
        "assessments": assessments,
    }


def run_governed_autonomy_phase1_queue_handoff(
    *,
    limit: int = 24,
    runtime_dossier_db_path: str | Path = DEFAULT_RUNTIME_DOSSIER_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
) -> dict[str, Any]:
    """Backend-only bridge: Phase 1 autonomy_accepted + queue_candidate → learning_queue tasks.

    Uses ``MiruLearningEngine.enqueue_phase1_queue_candidate_handoffs`` (bulk_ingest_registry
    for official-cardlist / official-restriction-notices). Reference-only lanes fail closed with
    metadata audit (no queue flood).
    """
    bounded_limit = max(1, min(_safe_int(limit or 24), 48))
    engine = MiruLearningEngine(
        dossier_db_path=runtime_dossier_db_path,
        verified_dossier_db_path=canonical_dossier_db_path,
        project_db_path=project_db_path,
    )
    engine.ensure_datastores()
    return engine.enqueue_phase1_queue_candidate_handoffs(limit=bounded_limit)


def verify_optcg_api_priority_cohort_support(
    *,
    limit: int = 24,
    per_card_source_limit: int = 1,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    runtime_dossier_db_path: str | Path = DEFAULT_RUNTIME_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
) -> dict[str, Any]:
    bounded_limit = max(1, min(_safe_int(limit or 24), MAX_EXECUTION_LIMIT))
    bounded_lane_limit = max(1, min(_safe_int(per_card_source_limit or 1), 4))
    engine = MiruLearningEngine(
        dossier_db_path=runtime_dossier_db_path,
        verified_dossier_db_path=canonical_dossier_db_path,
        project_db_path=project_db_path,
    )
    engine.ensure_datastores()
    source_entry = engine.resolve_source_entry(OPTCG_API_SOURCE_LANE)
    learning_gate = engine.evaluate_source_execution_gate(
        source_id=OPTCG_API_SOURCE_LANE,
        execution_kind="learning-intake",
    )
    reference_gate = engine.evaluate_source_execution_gate(
        source_id=OPTCG_API_SOURCE_LANE,
        execution_kind="reference-safe",
    )
    policy_assessment = {
        "learning_gate": learning_gate,
        "reference_gate": reference_gate,
        "source_notes": str(getattr(source_entry, "notes", "") or "").strip(),
        "allowed_access": str(getattr(source_entry, "allowed_access", "") or "").strip(),
        "fetch_mode": str(getattr(source_entry, "fetch_mode", "") or "").strip(),
        "trust_tier": int(getattr(source_entry, "trust_tier", 4) or 4),
        "trust_label": str(getattr(source_entry, "trust_label", "") or "").strip(),
    }
    if not bool(reference_gate.get("proceed")):
        return {
            "ok": False,
            "backend_only": True,
            "source_id": OPTCG_API_SOURCE_LANE,
            "policy_approved_for_use": False,
            "lane_executable": False,
            "policy_assessment": policy_assessment,
            "reason": str(reference_gate.get("reason") or reference_gate.get("policy_summary") or "").strip(),
        }

    batch_result = advance_leader_staple_approved_source_support_batch(
        limit=bounded_limit,
        per_card_source_limit=bounded_lane_limit,
        preferred_source_ids=(OPTCG_API_SOURCE_LANE,),
        project_db_path=project_db_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        runtime_dossier_db_path=runtime_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
    )

    payload = engine.resolve_source_task_payload(OPTCG_API_SOURCE_LANE, {})
    processed_cards = list(batch_result.get("selected_cards") or [])
    blocker_counts_before: dict[str, int] = {}
    blocker_counts_after: dict[str, int] = {}
    existing_lane_cards: list[str] = []
    no_match_cards: list[str] = []
    total_records = 0
    mapped_cards: list[str] = []
    representative_helped: list[dict[str, Any]] = []
    representative_still_blocked: list[dict[str, Any]] = []
    for item in processed_cards:
        card_code = str(item.get("card_code") or "").strip().upper()
        if not card_code:
            continue
        before_blocker = str(((item.get("before_blocker") or {}).get("dominant_blocker")) or "").strip()
        after_blocker = str(((item.get("after_blocker") or {}).get("dominant_blocker")) or "").strip()
        if before_blocker:
            blocker_counts_before[before_blocker] = blocker_counts_before.get(before_blocker, 0) + 1
        if after_blocker:
            blocker_counts_after[after_blocker] = blocker_counts_after.get(after_blocker, 0) + 1
        try:
            records = engine.fetch_official_source_records(
                source_id=OPTCG_API_SOURCE_LANE,
                card_code=card_code,
                task_payload=payload,
            )
        except Exception:
            records = []
        if records:
            mapped_cards.append(card_code)
            total_records += len(records)
        lane_attempts = list(item.get("source_lane_attempts") or [])
        if any(bool(attempt.get("already_present")) for attempt in lane_attempts):
            existing_lane_cards.append(card_code)
        elif not bool(item.get("gained_new_distinct_approved_source")):
            no_match_cards.append(card_code)
        if bool(item.get("gained_new_distinct_approved_source")) and len(representative_helped) < 6:
            representative_helped.append(
                {
                    "card_code": card_code,
                    "new_runtime_sources": list(item.get("new_runtime_sources") or []),
                    "remaining_blocker_after_pass": str(item.get("remaining_blocker_after_pass") or "").strip(),
                    "after_readiness_state": str(item.get("after_readiness_state") or "").strip(),
                    "after_publish_status": str(item.get("after_publish_status") or "").strip(),
                }
            )
        elif not bool(item.get("gained_new_distinct_approved_source")) and len(representative_still_blocked) < 6:
            representative_still_blocked.append(
                {
                    "card_code": card_code,
                    "remaining_blocker_after_pass": str(item.get("remaining_blocker_after_pass") or "").strip(),
                    "lane_attempts": list(item.get("source_lane_attempts") or []),
                }
            )

    return {
        "ok": True,
        "backend_only": True,
        "source_id": OPTCG_API_SOURCE_LANE,
        "policy_approved_for_use": True,
        "lane_executable": True,
        "policy_assessment": policy_assessment,
        "runtime_payload_ready": bool(engine.source_payload_has_adapter_input(payload)),
        "payload_mode": "snapshot_url" if str(payload.get("snapshot_url") or "").strip() else "none",
        "payload_url_template": str(payload.get("snapshot_url") or "").strip(),
        "record_count": total_records,
        "mapped_card_coverage": len(mapped_cards),
        "mapped_cards": mapped_cards,
        "total_cards_processed": int(batch_result.get("total_cards_processed") or 0),
        "cards_with_new_distinct_source_support": list(batch_result.get("cards_with_new_distinct_approved_source") or []),
        "cards_with_no_new_source_gain": list(batch_result.get("cards_with_no_new_source_gain") or []),
        "cards_with_existing_optcg_source_lane": existing_lane_cards,
        "cards_with_no_optcg_match": no_match_cards,
        "blocker_counts_before": blocker_counts_before,
        "blocker_counts_after": blocker_counts_after,
        "blocker_distribution_changed": blocker_counts_before != blocker_counts_after,
        "cards_with_readiness_state_change": list(batch_result.get("cards_with_readiness_state_change") or []),
        "cards_with_publish_status_change": list(batch_result.get("cards_with_publish_status_change") or []),
        "cards_with_review_proximity_change": list(batch_result.get("cards_with_review_proximity_change") or []),
        "cards_moved_closer_to_publish": list(batch_result.get("cards_moved_closer_to_publish") or []),
        "representative_helped": representative_helped,
        "representative_still_blocked": representative_still_blocked,
        "worth_keeping_as_governed_source": bool(mapped_cards),
        "batch_result": batch_result,
    }


def _summarize_roster_lanes(
    engine: MiruLearningEngine,
    registry: dict[str, Any],
) -> dict[str, Any]:
    lanes: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {}
    gap_coverage: dict[str, list[str]] = {}
    for entry in sorted(registry.values(), key=lambda item: (str(item.source_category or ""), int(item.trust_tier or 4), str(item.source_id or ""))):
        payload = {}
        try:
            payload = engine.resolve_default_source_task_payload(entry.source_id)
        except Exception:
            payload = {}
        snapshot_candidates = [
            str(path)
            for path in engine.default_source_snapshot_candidates(entry.source_id, source_entry=entry)
            if path.is_file()
        ]
        learning_gate = engine.evaluate_source_execution_gate(
            source_id=entry.source_id,
            execution_kind="learning-intake",
            source_type=entry.source_type,
            source_url=entry.base_url or entry.snapshot_url,
            notes=entry.notes,
            trust_tier=entry.trust_tier,
            trust_label=entry.trust_label,
            public_data_only=entry.public_data_only,
            requires_login=entry.requires_login,
            respect_site_policies=entry.respect_site_policies,
            review_state=entry.review_state,
        )
        reference_gate = engine.evaluate_source_execution_gate(
            source_id=entry.source_id,
            execution_kind="reference-safe",
            source_type=entry.source_type,
            source_url=entry.base_url or entry.snapshot_url,
            notes=entry.notes,
            trust_tier=entry.trust_tier,
            trust_label=entry.trust_label,
            public_data_only=entry.public_data_only,
            requires_login=entry.requires_login,
            respect_site_policies=entry.respect_site_policies,
            review_state=entry.review_state,
        )
        preferred_gate = learning_gate if bool(learning_gate.get("proceed")) else reference_gate
        planning_status = "planning_only"
        _adapter = str(getattr(entry, "execution_adapter", "") or "").strip().lower()
        _adapter = {
            "community-deck-meta": "community-structured",
            "community-card-reference": "community-structured",
        }.get(_adapter, _adapter)
        if _adapter in {
            "",
            "official-cardlist",
            "official-card-images",
            "official-deck-features",
            "official-rules-faq",
            "official-restriction-notices",
            "official-errata-cards",
            "optcg-api",
            "community-structured",
        }:
            planning_status = "existing_adapter_ready"
        if not bool(preferred_gate.get("proceed")):
            planning_status = "governance_blocked"
        lane = {
            "source_id": str(entry.source_id or "").strip().lower(),
            "source_name": str(entry.source_name or "").strip(),
            "source_category": str(getattr(entry, "source_category", "") or "uncategorized").strip() or "uncategorized",
            "trust_tier": int(entry.trust_tier or 4),
            "allowed_access": str(entry.allowed_access or "").strip(),
            "execution_adapter": str(getattr(entry, "execution_adapter", "") or "").strip(),
            "planning_status": planning_status,
            "payload_ready": bool(payload),
            "snapshot_candidates_present": snapshot_candidates,
            "capability_tags": list(getattr(entry, "capability_tags", ()) or ()),
            "gap_support": list(getattr(entry, "gap_support", ()) or ()),
            "learning_gate": {
                "proceed": bool(learning_gate.get("proceed")),
                "execution_outcome": str(learning_gate.get("execution_outcome") or "").strip(),
                "policy_evidence_role": str(learning_gate.get("policy_evidence_role") or "").strip(),
            },
            "reference_gate": {
                "proceed": bool(reference_gate.get("proceed")),
                "execution_outcome": str(reference_gate.get("execution_outcome") or "").strip(),
                "policy_evidence_role": str(reference_gate.get("policy_evidence_role") or "").strip(),
            },
        }
        lanes.append(lane)
        category_key = str(lane["source_category"])
        category_counts[category_key] = category_counts.get(category_key, 0) + 1
        for gap in list(lane["gap_support"]):
            if gap:
                gap_coverage.setdefault(str(gap), []).append(str(lane["source_id"]))
    return {
        "lane_count": len(lanes),
        "lanes": lanes,
        "category_counts": category_counts,
        "gap_coverage": {key: sorted(dict.fromkeys(value)) for key, value in gap_coverage.items()},
    }


def audit_approved_source_roster_coverage(
    *,
    limit: int = 60,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    runtime_dossier_db_path: str | Path = DEFAULT_RUNTIME_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
) -> dict[str, Any]:
    bounded_limit = max(1, min(_safe_int(limit or 60), MAX_EXECUTION_LIMIT))
    engine = MiruLearningEngine(
        dossier_db_path=runtime_dossier_db_path,
        verified_dossier_db_path=canonical_dossier_db_path,
        project_db_path=project_db_path,
    )
    engine.ensure_datastores()

    before_registry = build_source_registry(include_expanded_builtin=False)
    after_registry = build_source_registry(include_expanded_builtin=True)
    before_summary = _summarize_roster_lanes(engine, before_registry)
    after_summary = _summarize_roster_lanes(engine, after_registry)
    new_lane_ids = sorted(set(after_registry) - set(before_registry))

    diagnosed = diagnose_leader_staple_expansion_blockers(
        limit=bounded_limit,
        project_db_path=project_db_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
    )
    selected_cards = list(diagnosed.get("selected_cards") or [])

    gap_need_counts: dict[str, int] = {}
    sample_cards_by_gap: dict[str, list[str]] = {}
    for item in selected_cards:
        card_code = str(item.get("card_code") or "").strip().upper()
        objectives = [str(obj).strip() for obj in list(item.get("objectives") or []) if str(obj).strip()]
        blocker = str(((item.get("blocker_diagnosis") or {}).get("dominant_blocker")) or "").strip()
        needed_gaps = set(objectives)
        if blocker == "more_distinct_source_support":
            needed_gaps.add("source_depth_fill")
        for gap in needed_gaps:
            gap_need_counts[gap] = gap_need_counts.get(gap, 0) + 1
            sample_cards_by_gap.setdefault(gap, [])
            if card_code and card_code not in sample_cards_by_gap[gap] and len(sample_cards_by_gap[gap]) < 6:
                sample_cards_by_gap[gap].append(card_code)

    expected_usefulness_by_gap: dict[str, list[dict[str, Any]]] = {}
    for lane in list(after_summary.get("lanes") or []):
        if str(lane.get("source_id") or "").strip().lower() not in new_lane_ids:
            continue
        usefulness = {
            "source_id": str(lane.get("source_id") or "").strip(),
            "source_name": str(lane.get("source_name") or "").strip(),
            "source_category": str(lane.get("source_category") or "").strip(),
            "planning_status": str(lane.get("planning_status") or "").strip(),
            "payload_ready": bool(lane.get("payload_ready")),
        }
        for gap in list(lane.get("gap_support") or []):
            if gap:
                expected_usefulness_by_gap.setdefault(str(gap), []).append(usefulness)

    underserved_after_expansion: list[dict[str, Any]] = []
    for gap, need_count in sorted(gap_need_counts.items(), key=lambda item: (-item[1], item[0])):
        relevant_lanes = [
            lane
            for lane in list(after_summary.get("lanes") or [])
            if gap in set(lane.get("gap_support") or [])
        ]
        adapter_ready = [
            lane
            for lane in relevant_lanes
            if str(lane.get("planning_status") or "") == "existing_adapter_ready"
        ]
        payload_ready = [lane for lane in relevant_lanes if bool(lane.get("payload_ready"))]
        if not relevant_lanes or not payload_ready:
            underserved_after_expansion.append(
                {
                    "gap_type": gap,
                    "selected_card_count": need_count,
                    "sample_cards": list(sample_cards_by_gap.get(gap) or []),
                    "available_lane_count": len(relevant_lanes),
                    "payload_ready_lane_count": len(payload_ready),
                    "existing_adapter_ready_lane_count": len(adapter_ready),
                    "reason": (
                        "No policy-compliant roster lanes map to this gap yet."
                        if not relevant_lanes
                        else "Roster coverage improved, but no normalized snapshots are mounted for the relevant lanes yet."
                    ),
                }
            )

    rejected_sources = [
        {
            "source_candidate": "bandai-tcg-plus-tournament-data",
            "reason": "Rejected fail-closed because access is gated and any reusable API or export permission is uncertain.",
        },
        {
            "source_candidate": "social-community-platforms",
            "reason": "Rejected for verified intelligence because community/social posts remain lead-only and do not meet Miru's truth hierarchy for publish-oriented support.",
        },
        {
            "source_candidate": "marketplace-listings-and-auctions",
            "reason": "Rejected as a roster expansion target because market listings are price hints only and are not authoritative for identity, usage, or legality corroboration.",
        },
    ]

    return {
        "ok": True,
        "backend_only": True,
        "approved_lanes_before": before_summary,
        "approved_lanes_after": after_summary,
        "new_lane_ids": new_lane_ids,
        "expected_usefulness_by_gap": expected_usefulness_by_gap,
        "gap_need_counts": gap_need_counts,
        "rejected_sources": rejected_sources,
        "underserved_after_expansion": underserved_after_expansion,
        "future_batch_impact": {
            "should_materially_improve_future_distinct_source_support_batches": bool(new_lane_ids),
            "rationale": (
                "The expanded roster adds official leader/meta and rules/legality lanes that were not previously available in the built-in registry. "
                "Future batches should improve once normalized snapshots are mounted for those lanes."
            ),
        },
        "diagnostics": diagnosed,
    }


def run_leader_staple_intelligence_expansion(
    *,
    limit: int = 12,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
) -> dict[str, Any]:
    bounded_limit = max(1, min(_safe_int(limit or 12), MAX_EXECUTION_LIMIT))
    planned = plan_worktree_card_insight_sync(limit=bounded_limit)
    selected_candidates = list(planned.get("selected") or [])[:bounded_limit]
    if not selected_candidates:
        return {
            "ok": True,
            "selected_count": 0,
            "selected_cards": [],
            "sync_result": None,
            "verification": {
                "selected_cards": [],
                "moved_count": 0,
                "unchanged_count": 0,
            },
        }

    before_by_code: dict[str, dict[str, Any]] = {}
    for candidate in selected_candidates:
        card_code = str(candidate.get("card_code") or "").strip().upper()
        if not card_code:
            continue
        before_by_code[card_code] = build_revalidation_candidate_summary(
            card_code=card_code,
            project_db_path=project_db_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )

    sync_report = run_worktree_card_insight_sync(limit=bounded_limit, rebuild=False)
    refreshed_cards: list[dict[str, Any]] = []
    moved_count = 0
    unchanged_count = 0
    objective_counts: dict[str, int] = {}
    blocker_counts: dict[str, int] = {}
    for candidate in selected_candidates:
        card_code = str(candidate.get("card_code") or "").strip().upper()
        if not card_code:
            continue
        refresh_result = refresh_revalidation_candidate(
            card_code=card_code,
            project_db_path=project_db_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
            decision_source="leader_staple_expansion",
        )
        summary = dict(refresh_result.get("summary") or {})
        before_snapshot = _expansion_status_snapshot(before_by_code.get(card_code) or {})
        after_snapshot = _expansion_status_snapshot(summary)
        movement = _expansion_status_movement(before_snapshot, after_snapshot)
        if movement:
            moved_count += 1
        else:
            unchanged_count += 1
        objectives = _normalize_expansion_objectives(
            list(candidate.get("priority_objectives") or []) + list(summary.get("objective_tags") or [])
        )
        diagnosis = _diagnose_selected_card_blocker(summary)
        blocker_key = str(diagnosis.get("dominant_blocker") or "").strip()
        if blocker_key:
            blocker_counts[blocker_key] = blocker_counts.get(blocker_key, 0) + 1
        for objective in objectives:
            objective_counts[objective] = objective_counts.get(objective, 0) + 1
        refreshed_cards.append(
            {
                "card_code": card_code,
                "card_name": str(summary.get("card_name") or "").strip(),
                "selected_reason": str(candidate.get("reason") or "").strip(),
                "why_selected": _dedupe_texts(
                    [str(candidate.get("priority_summary") or "").strip(), *list(candidate.get("selection_reasons") or [])],
                    limit=5,
                ),
                "objectives": objectives,
                "primary_objective": str(summary.get("primary_objective") or (objectives[0] if objectives else "")).strip(),
                "enrichment_path": _expansion_enrichment_path(candidate, summary),
                "blocker_diagnosis": diagnosis,
                "status_movement": movement,
                "before": before_snapshot,
                "after": after_snapshot,
                "remaining_blockers": _dedupe_texts(
                    list(summary.get("candidate_risk_factors") or [])
                    + list(summary.get("publish_risks") or [])
                    + [str(summary.get("revalidation_reason") or "").strip()],
                    limit=6,
                ),
            }
        )

    return {
        "ok": True,
        "selected_count": len(refreshed_cards),
        "selected_cards": refreshed_cards,
        "planned_candidates": {
            "candidate_count": int(planned.get("candidate_count") or 0),
            "remaining_count": int(planned.get("remaining_count") or 0),
            "selected_reason_counts": dict(planned.get("selected_reason_counts") or {}),
            "selected_priority_bucket_counts": dict(planned.get("selected_priority_bucket_counts") or {}),
        },
        "objective_counts": objective_counts,
        "blocker_counts": blocker_counts,
        "sync_result": (sync_report or {}).get("sync_result") or sync_report,
        "verification": {
            "selected_cards": refreshed_cards,
            "moved_count": moved_count,
            "unchanged_count": unchanged_count,
        },
    }


def build_publication_payload_contract(
    *,
    card_code: str,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
) -> dict[str, Any]:
    summary = build_publication_candidate_summary(
        card_code=card_code,
        project_db_path=project_db_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
    )
    return dict(summary.get("publication_payload") or {})


def _review_queue_item_key(card_code: str) -> str:
    return f"publication:{_normalize_code(card_code)}"


def image_variant_sp_queue_item_key(card_code: str) -> str:
    return f"image_variant_sp:{_normalize_code(card_code)}"


def enqueue_image_variant_sp_review_queue(
    conn: sqlite3.Connection,
    *,
    canonical_code: str,
    summary_text: str,
    payload: dict[str, Any],
    decision_source: str = "image_variant_classifier",
) -> None:
    """Queue operator review when image analysis reports [SP] on the card ID label."""
    code = _normalize_code(canonical_code)
    if not code:
        return
    item_key = image_variant_sp_queue_item_key(code)
    now = _utc_now_timestamp()
    review_reason = "image_analysis_sp_marker_detected"
    approval_state = "pending_review"
    promotion_state, _ = _derive_promotion_fields(
        readiness_state="image_sp_review_pending",
        approval_state=approval_state,
        queue_status="pending",
        guardrail_label="",
    )
    conn.execute(
        """
        INSERT INTO miru_review_queue (
            item_key,
            queue_type,
            target_type,
            target_id,
            readiness_state,
            review_reason,
            guardrail_label,
            confidence_score,
            risk_level,
            recommended_next_step,
            summary_text,
            supporting_sections_json,
            payload_json,
            status,
            approval_state,
            promotion_state,
            approval_note,
            decision_source,
            resolution_note,
            created_at,
            updated_at,
            resolved_at,
            approval_updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_key) DO UPDATE SET
            queue_type = excluded.queue_type,
            target_type = excluded.target_type,
            target_id = excluded.target_id,
            readiness_state = excluded.readiness_state,
            review_reason = excluded.review_reason,
            guardrail_label = excluded.guardrail_label,
            confidence_score = excluded.confidence_score,
            risk_level = excluded.risk_level,
            recommended_next_step = excluded.recommended_next_step,
            summary_text = excluded.summary_text,
            supporting_sections_json = excluded.supporting_sections_json,
            payload_json = excluded.payload_json,
            status = CASE
                WHEN trim(coalesce(miru_review_queue.approval_state, '')) IN ('approved_for_candidate', 'rejected', 'superseded')
                    THEN miru_review_queue.status
                ELSE 'pending'
            END,
            approval_state = CASE
                WHEN trim(coalesce(miru_review_queue.approval_state, '')) IN ('approved_for_candidate', 'rejected', 'superseded')
                    THEN miru_review_queue.approval_state
                ELSE excluded.approval_state
            END,
            promotion_state = CASE
                WHEN trim(coalesce(miru_review_queue.approval_state, '')) IN ('approved_for_candidate', 'rejected', 'superseded')
                    THEN miru_review_queue.promotion_state
                ELSE excluded.promotion_state
            END,
            approval_note = CASE
                WHEN trim(coalesce(miru_review_queue.approval_state, '')) IN ('approved_for_candidate', 'rejected', 'superseded')
                    THEN miru_review_queue.approval_note
                ELSE excluded.approval_note
            END,
            decision_source = excluded.decision_source,
            resolution_note = CASE
                WHEN trim(coalesce(miru_review_queue.approval_state, '')) IN ('approved_for_candidate', 'rejected', 'superseded')
                    THEN miru_review_queue.resolution_note
                ELSE ''
            END,
            updated_at = excluded.updated_at,
            resolved_at = CASE
                WHEN trim(coalesce(miru_review_queue.approval_state, '')) IN ('approved_for_candidate', 'rejected', 'superseded')
                    THEN miru_review_queue.resolved_at
                ELSE ''
            END,
            approval_updated_at = CASE
                WHEN trim(coalesce(miru_review_queue.approval_state, '')) IN ('approved_for_candidate', 'rejected', 'superseded')
                    THEN miru_review_queue.approval_updated_at
                ELSE excluded.approval_updated_at
            END
        """,
        (
            item_key,
            "image_variant_sp",
            "card",
            code,
            "image_sp_review_pending",
            review_reason,
            "",
            0.0,
            "medium",
            "Confirm [SP] on card image; approve to set catalog variant_subtype=sp for this code.",
            str(summary_text or "").strip(),
            _json_dump([]),
            _json_dump(payload),
            "pending",
            approval_state,
            promotion_state,
            "",
            str(decision_source or "").strip(),
            "",
            now,
            now,
            "",
            now,
        ),
    )


def _should_queue_review_summary(summary: dict[str, Any], *, forced: bool = False) -> bool:
    if forced:
        return True
    state = str(summary.get("readiness_state") or "").strip()
    if state == "ready_for_review":
        return True
    if state == "blocked_by_guardrail" and bool(summary.get("queue_worthy")):
        return True
    return False


def _upsert_review_queue_entry(
    conn: sqlite3.Connection,
    *,
    summary: dict[str, Any],
    forced: bool = False,
    note: str = "",
    decision_source: str = "auto_refresh",
) -> dict[str, Any]:
    card_code = str(summary.get("card_code") or "").strip().upper()
    item_key = _review_queue_item_key(card_code)
    existing = conn.execute(
        """
        SELECT id, status, readiness_state, approval_state
        FROM miru_review_queue
        WHERE item_key = ?
        LIMIT 1
        """,
        (item_key,),
    ).fetchone()
    now = _utc_now_timestamp()
    should_queue = _should_queue_review_summary(summary, forced=forced)
    next_step = str(note or summary.get("recommended_next_step") or "").strip()
    payload = dict(summary)
    if note:
        payload["operator_note"] = note

    if should_queue:
        review_reason = str(summary.get("review_reason") or "manual_review_required").strip() or "manual_review_required"
        existing_approval = _normalize_approval_state(existing["approval_state"]) if existing and "approval_state" in existing.keys() else ""
        approval_state = existing_approval or "pending_review"
        promotion_state, _ = _derive_promotion_fields(
            readiness_state=str(summary.get("readiness_state") or "").strip(),
            approval_state=approval_state,
            queue_status="pending",
            guardrail_label=str(summary.get("guardrail_label") or "").strip(),
        )
        conn.execute(
            """
            INSERT INTO miru_review_queue (
                item_key,
                queue_type,
                target_type,
                target_id,
                readiness_state,
                review_reason,
                guardrail_label,
                confidence_score,
                risk_level,
                recommended_next_step,
                summary_text,
                supporting_sections_json,
                payload_json,
                status,
                approval_state,
                promotion_state,
                approval_note,
                decision_source,
                resolution_note,
                created_at,
                updated_at,
                resolved_at,
                approval_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_key) DO UPDATE SET
                readiness_state = excluded.readiness_state,
                review_reason = excluded.review_reason,
                guardrail_label = excluded.guardrail_label,
                confidence_score = excluded.confidence_score,
                risk_level = excluded.risk_level,
                recommended_next_step = excluded.recommended_next_step,
                summary_text = excluded.summary_text,
                supporting_sections_json = excluded.supporting_sections_json,
                payload_json = excluded.payload_json,
                status = 'pending',
                approval_state = CASE
                    WHEN trim(coalesce(miru_review_queue.approval_state, '')) IN ('approved_for_candidate', 'rejected', 'superseded')
                        THEN miru_review_queue.approval_state
                    ELSE excluded.approval_state
                END,
                promotion_state = excluded.promotion_state,
                approval_note = CASE
                    WHEN trim(coalesce(miru_review_queue.approval_state, '')) IN ('approved_for_candidate', 'rejected', 'superseded')
                        THEN miru_review_queue.approval_note
                    ELSE excluded.approval_note
                END,
                decision_source = excluded.decision_source,
                resolution_note = '',
                updated_at = excluded.updated_at,
                resolved_at = '',
                approval_updated_at = CASE
                    WHEN trim(coalesce(miru_review_queue.approval_state, '')) IN ('approved_for_candidate', 'rejected', 'superseded')
                        THEN miru_review_queue.approval_updated_at
                    ELSE excluded.approval_updated_at
                END
            """,
            (
                item_key,
                "publication_readiness",
                "card",
                card_code,
                str(summary.get("readiness_state") or "").strip(),
                review_reason,
                str(summary.get("guardrail_label") or "").strip(),
                round(_safe_float(summary.get("confidence")), 3),
                str(summary.get("risk_level") or "").strip(),
                next_step,
                str(summary.get("summary_text") or "").strip(),
                _json_dump(summary.get("strong_sections") or []),
                _json_dump(payload),
                "pending",
                approval_state,
                promotion_state,
                str(note or summary.get("approval_note") or "").strip(),
                str(decision_source or "").strip(),
                "",
                now,
                now,
                "",
                now if approval_state else "",
            ),
        )
        return {
            "item_key": item_key,
            "target_id": card_code,
            "status": "pending",
            "action": "queued" if existing is None else "updated",
            "readiness_state": str(summary.get("readiness_state") or "").strip(),
            "review_reason": str(summary.get("review_reason") or "").strip(),
            "approval_state": approval_state,
            "promotion_state": promotion_state,
        }

    if existing is not None:
        if str(summary.get("readiness_state") or "").strip() == "ready_for_publish_candidate":
            new_status = "resolved"
            resolution_note = next_step or "Readiness improved to ready_for_publish_candidate."
        else:
            new_status = "deferred"
            resolution_note = next_step or "Review is not actionable until stronger dossier-backed evidence exists."
        approval_state = _normalize_approval_state(existing["approval_state"]) if "approval_state" in existing.keys() else ""
        promotion_state, _ = _derive_promotion_fields(
            readiness_state=str(summary.get("readiness_state") or "").strip(),
            approval_state=approval_state,
            queue_status=new_status,
            guardrail_label=str(summary.get("guardrail_label") or "").strip(),
        )
        conn.execute(
            """
            UPDATE miru_review_queue
            SET readiness_state = ?,
                guardrail_label = ?,
                confidence_score = ?,
                risk_level = ?,
                recommended_next_step = ?,
                summary_text = ?,
                supporting_sections_json = ?,
                payload_json = ?,
                status = ?,
                promotion_state = ?,
                decision_source = ?,
                resolution_note = ?,
                updated_at = ?,
                resolved_at = ?
            WHERE item_key = ?
            """,
            (
                str(summary.get("readiness_state") or "").strip(),
                str(summary.get("guardrail_label") or "").strip(),
                round(_safe_float(summary.get("confidence")), 3),
                str(summary.get("risk_level") or "").strip(),
                next_step,
                str(summary.get("summary_text") or "").strip(),
                _json_dump(summary.get("strong_sections") or []),
                _json_dump(payload),
                new_status,
                promotion_state,
                str(decision_source or "").strip(),
                resolution_note,
                now,
                now,
                item_key,
            ),
        )
        return {
            "item_key": item_key,
            "target_id": card_code,
            "status": new_status,
            "action": new_status,
            "readiness_state": str(summary.get("readiness_state") or "").strip(),
            "review_reason": str(summary.get("review_reason") or "").strip(),
            "approval_state": approval_state,
            "promotion_state": promotion_state,
        }

    return {
        "item_key": item_key,
        "target_id": card_code,
        "status": "not_queued",
        "action": "ignored",
        "readiness_state": str(summary.get("readiness_state") or "").strip(),
        "review_reason": str(summary.get("review_reason") or "").strip(),
        "approval_state": "",
        "promotion_state": _derive_promotion_fields(
            readiness_state=str(summary.get("readiness_state") or "").strip(),
            approval_state="",
            queue_status="",
            guardrail_label=str(summary.get("guardrail_label") or "").strip(),
        )[0],
    }


def load_review_queue_entries(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    status: str | None = "pending",
    limit: int = 12,
) -> list[dict[str, Any]]:
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    where = ""
    params: list[Any] = []
    if status:
        where = "WHERE status = ?"
        params.append(str(status).strip().lower())
    params.append(max(1, limit))
    with closing(connect_catalog_db(project_path)) as conn:
        rows = conn.execute(
            f"""
            SELECT
                item_key,
                queue_type,
                target_type,
                target_id,
                readiness_state,
                review_reason,
                guardrail_label,
                confidence_score,
                risk_level,
                recommended_next_step,
                summary_text,
                supporting_sections_json,
                payload_json,
                status,
                approval_state,
                promotion_state,
                approval_note,
                decision_source,
                resolution_note,
                created_at,
                updated_at,
                resolved_at,
                approval_updated_at
            FROM miru_review_queue
            {where}
            ORDER BY
                CASE status
                    WHEN 'pending' THEN 0
                    WHEN 'deferred' THEN 1
                    WHEN 'resolved' THEN 2
                    ELSE 3
                END,
                updated_at DESC,
                target_id ASC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["supporting_sections"] = _json_load(item.get("supporting_sections_json"), [])
        item["payload"] = _json_load(item.get("payload_json"), {})
        effective_approval_state = _normalize_approval_state(item.get("approval_state"))
        if not effective_approval_state and str(item.get("status") or "").strip().lower() == "pending":
            effective_approval_state = "pending_review"
        elif not effective_approval_state and str(item.get("status") or "").strip().lower() == "deferred":
            effective_approval_state = "deferred"
        item["approval_state"] = effective_approval_state
        out.append(item)
    return out


def load_review_queue_summary(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    limit: int = 8,
) -> dict[str, Any]:
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    with closing(connect_catalog_db(project_path)) as conn:
        counts = {"pending": 0, "deferred": 0, "resolved": 0}
        approval_counts = {
            "pending_review": 0,
            "approved_for_candidate": 0,
            "rejected": 0,
            "deferred": 0,
            "superseded": 0,
            "none": 0,
        }
        for row in conn.execute(
            "SELECT status, COUNT(*) AS row_count FROM miru_review_queue GROUP BY status"
        ).fetchall():
            counts[str(row["status"] or "").strip() or "pending"] = _safe_int(row["row_count"])
        for row in conn.execute(
            "SELECT status, approval_state FROM miru_review_queue"
        ).fetchall():
            key = _normalize_approval_state(row["approval_state"])
            status_key = str(row["status"] or "").strip().lower()
            if not key and status_key == "pending":
                key = "pending_review"
            elif not key and status_key == "deferred":
                key = "deferred"
            elif not key:
                key = "none"
            approval_counts[key] = approval_counts.get(key, 0) + 1
        pending_items = load_review_queue_entries(project_db_path=project_path, status="pending", limit=limit)
        approved_rows = conn.execute(
            """
            SELECT
                item_key,
                queue_type,
                target_type,
                target_id,
                readiness_state,
                review_reason,
                guardrail_label,
                confidence_score,
                risk_level,
                recommended_next_step,
                summary_text,
                supporting_sections_json,
                payload_json,
                status,
                approval_state,
                promotion_state,
                approval_note,
                decision_source,
                resolution_note,
                created_at,
                updated_at,
                resolved_at,
                approval_updated_at
            FROM miru_review_queue
            WHERE approval_state = 'approved_for_candidate'
            ORDER BY approval_updated_at DESC, updated_at DESC, target_id ASC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
        approved_candidates: list[dict[str, Any]] = []
        for row in approved_rows:
            item = dict(row)
            item["supporting_sections"] = _json_load(item.get("supporting_sections_json"), [])
            item["payload"] = _json_load(item.get("payload_json"), {})
            approved_candidates.append(item)
        latest = _load_metadata(conn, sync_key=REVIEW_QUEUE_METADATA_KEY)
    return {
        "counts": counts,
        "approval_counts": approval_counts,
        "pending_count": counts.get("pending", 0),
        "pending_items": pending_items,
        "approved_candidates": approved_candidates[:limit],
        "latest": latest,
    }


def _load_stage_row(conn: sqlite3.Connection, card_code: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            item_key,
            target_type,
            target_id,
            readiness_state,
            approval_state,
            promotion_state,
            stage_state,
            guardrail_label,
            confidence_score,
            risk_level,
            candidate_score,
            candidate_score_band,
            candidate_profile,
            candidate_score_reasons_json,
            candidate_risk_factors_json,
            rationale,
            summary_text,
            supporting_sections_json,
            payload_json,
            batch_id,
            note,
            decision_source,
            created_at,
            updated_at,
            removed_at
        FROM miru_publication_stage
        WHERE item_key = ?
        LIMIT 1
        """,
        (_review_queue_item_key(card_code),),
    ).fetchone()
    if row is None:
        return {}
    out = dict(row)
    out["supporting_sections"] = _json_load(out.get("supporting_sections_json"), [])
    out["payload"] = _json_load(out.get("payload_json"), {})
    out["candidate_score_reasons"] = _json_load(out.get("candidate_score_reasons_json"), [])
    out["candidate_risk_factors"] = _json_load(out.get("candidate_risk_factors_json"), [])
    return out


def _load_batch_row(conn: sqlite3.Connection, batch_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            batch_id,
            batch_status,
            batch_title,
            rationale,
            summary_text,
            guardrail_label,
            batch_quality_score,
            batch_quality_band,
            batch_profile,
            member_count,
            ready_member_count,
            review_member_count,
            blocked_member_count,
            deferred_member_count,
            batch_publish_status,
            batch_publish_reasons_json,
            batch_publish_risks_json,
            batch_publish_payload_json,
            batch_publish_updated_at,
            strongest_reasons_json,
            unresolved_risks_json,
            recommended_next_step,
            payload_json,
            created_at,
            updated_at,
            archived_at
        FROM miru_publication_batches
        WHERE batch_id = ?
        LIMIT 1
        """,
        (str(batch_id or "").strip(),),
    ).fetchone()
    if row is None:
        return {}
    out = dict(row)
    out["payload"] = _json_load(out.get("payload_json"), {})
    out["batch_publish_payload"] = _json_load(out.get("batch_publish_payload_json"), {})
    out["batch_publish_reasons"] = _json_load(out.get("batch_publish_reasons_json"), [])
    out["batch_publish_risks"] = _json_load(out.get("batch_publish_risks_json"), [])
    out["strongest_reasons"] = _json_load(out.get("strongest_reasons_json"), [])
    out["unresolved_risks"] = _json_load(out.get("unresolved_risks_json"), [])
    return out


def _load_batch_members(conn: sqlite3.Connection, batch_id: str, *, include_removed: bool = False) -> list[dict[str, Any]]:
    where = "" if include_removed else "WHERE status = 'active'"
    rows = conn.execute(
        f"""
        SELECT
            batch_id,
            item_key,
            target_id,
            stage_state,
            readiness_state,
            approval_state,
            promotion_state,
            guardrail_label,
            confidence_score,
            candidate_score,
            candidate_score_band,
            candidate_profile,
            rationale,
            payload_json,
            status,
            note,
            added_at,
            updated_at,
            removed_at
        FROM miru_publication_batch_items
        WHERE batch_id = ?
          {'' if include_removed else "AND status = 'active'"}
        ORDER BY updated_at DESC, target_id ASC
        """,
        (str(batch_id or "").strip(),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["payload"] = _json_load(item.get("payload_json"), {})
        out.append(item)
    return out


def _derive_staging_fields(
    summary: dict[str, Any],
    *,
    stage_row: dict[str, Any] | None = None,
    runtime_uncertain: bool = False,
) -> dict[str, Any]:
    stage_info = dict(stage_row or {})
    readiness_state = str(summary.get("readiness_state") or "").strip().lower()
    approval_state = _normalize_approval_state(summary.get("approval_state"))
    promotion_state = _normalize_promotion_state(summary.get("promotion_state"))
    guardrail = str(summary.get("guardrail_label") or "").strip().lower()
    review_reason = str(summary.get("review_reason") or "").strip().lower()
    current_stage_state = _normalize_stage_state(stage_info.get("stage_state"))
    current_batch_id = str(stage_info.get("batch_id") or "").strip()

    decision = "blocked"
    guardrail_label = "Blocked"
    rationale = "This item does not yet satisfy staging guardrails."
    stageable = False

    if approval_state == "deferred":
        rationale = "This candidate is deferred, so it must stay out of publication staging."
    elif approval_state in {"rejected", "superseded"}:
        rationale = "A stored approval decision blocks this candidate from staging."
    elif readiness_state in {"blocked_by_guardrail", "not_ready"}:
        rationale = "Current readiness guardrails block this candidate from staging."
    elif approval_state != "approved_for_candidate" or promotion_state != "review_approved_candidate":
        rationale = "This candidate still needs an explicit approval decision before Miru stages it."
    elif runtime_uncertain or guardrail == "review required" or review_reason in {"legality_sensitive", "guarded_publish_review"}:
        decision = "allowed_with_review"
        guardrail_label = "Review required"
        rationale = (
            "The candidate is approved, but Miru should keep the staging step visibly review-bound because the "
            "stored signals still need careful handling."
        )
        stageable = True
    else:
        decision = "allowed_now"
        guardrail_label = "Safe action"
        rationale = "The candidate is approved and dossier-backed enough for backend-only staging."
        stageable = True

    if current_stage_state in {"staged_candidate", "staged_batch_member"}:
        stage_state = current_stage_state
    elif current_stage_state == "removed_from_stage":
        stage_state = "removed_from_stage"
    elif stageable:
        stage_state = "unstaged"
    else:
        stage_state = "blocked_from_staging"

    return {
        "stage_state": stage_state,
        "stageable": stageable,
        "decision": decision,
        "guardrail_label": guardrail_label,
        "rationale": rationale,
        "batch_id": current_batch_id,
        "note": str(stage_info.get("note") or "").strip(),
        "decision_source": str(stage_info.get("decision_source") or "").strip(),
        "updated_at": str(stage_info.get("updated_at") or "").strip(),
    }


def _stage_counts_template() -> dict[str, int]:
    return {
        "unstaged": 0,
        "staged_candidate": 0,
        "staged_batch_member": 0,
        "blocked_from_staging": 0,
        "removed_from_stage": 0,
    }


def load_publication_stage_entries(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    stage_state: str | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    params: list[Any] = []
    where = ""
    if stage_state:
        where = "WHERE stage_state = ?"
        params.append(_normalize_stage_state(stage_state))
    params.append(max(1, limit))
    with closing(connect_catalog_db(project_path)) as conn:
        rows = conn.execute(
            f"""
            SELECT
                item_key,
                target_type,
                target_id,
                readiness_state,
                approval_state,
                promotion_state,
                stage_state,
                guardrail_label,
                confidence_score,
                risk_level,
                candidate_score,
                candidate_score_band,
                candidate_profile,
                candidate_score_reasons_json,
                candidate_risk_factors_json,
                rationale,
                summary_text,
                supporting_sections_json,
                payload_json,
                batch_id,
                note,
                decision_source,
                created_at,
                updated_at,
                removed_at
            FROM miru_publication_stage
            {where}
            ORDER BY
                CASE stage_state
                    WHEN 'staged_batch_member' THEN 0
                    WHEN 'staged_candidate' THEN 1
                    WHEN 'blocked_from_staging' THEN 2
                    WHEN 'removed_from_stage' THEN 3
                    ELSE 4
                END,
                COALESCE(candidate_score, 0) DESC,
                COALESCE(confidence_score, 0) DESC,
                updated_at DESC,
                target_id ASC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["supporting_sections"] = _json_load(item.get("supporting_sections_json"), [])
        item["payload"] = _json_load(item.get("payload_json"), {})
        if (
            item.get("stage_state") in {"staged_candidate", "staged_batch_member", "blocked_from_staging"}
            and not str((item.get("payload") or {}).get("publish_status") or "").strip()
        ):
            item["payload"] = build_publication_candidate_summary(
                card_code=str(item.get("target_id") or "").strip(),
                project_db_path=project_path,
            )
        item["candidate_score_reasons"] = _json_load(item.get("candidate_score_reasons_json"), [])
        item["candidate_risk_factors"] = _json_load(item.get("candidate_risk_factors_json"), [])
        out.append(item)
    return out


def load_publication_stage_summary(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    limit: int = 8,
) -> dict[str, Any]:
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    with closing(connect_catalog_db(project_path)) as conn:
        counts = _stage_counts_template()
        for row in conn.execute(
            "SELECT stage_state, COUNT(*) AS row_count FROM miru_publication_stage GROUP BY stage_state"
        ).fetchall():
            key = _normalize_stage_state(row["stage_state"])
            if key:
                counts[key] = counts.get(key, 0) + _safe_int(row["row_count"])
        active_stage = load_publication_stage_entries(
            project_db_path=project_path,
            stage_state=None,
            limit=max(1, limit * 2),
        )
        active_items = [
            item for item in active_stage
            if item.get("stage_state") in {"staged_candidate", "staged_batch_member"}
        ][:limit]
        top_scored_candidates = sorted(
            active_items,
            key=lambda item: (
                -_safe_float(item.get("candidate_score") or ((item.get("payload") or {}).get("candidate_score"))),
                -_safe_float(item.get("confidence_score")),
                str(item.get("target_id") or ""),
            ),
        )[:limit]
        profile_counts = {
            "high_value_safe": 0,
            "high_value_review_heavy": 0,
            "weak_partial": 0,
            "blocked": 0,
            "unknown": 0,
        }
        publish_counts = {status: 0 for status in PUBLISH_STATUSES}
        for item in active_stage:
            key = str(item.get("candidate_profile") or ((item.get("payload") or {}).get("candidate_profile")) or "").strip()
            if not key:
                key = "unknown"
            profile_counts[key] = profile_counts.get(key, 0) + 1
            publish_key = _normalize_publish_status((item.get("payload") or {}).get("publish_status"))
            if publish_key:
                publish_counts[publish_key] = publish_counts.get(publish_key, 0) + 1
        publish_ready_candidates = [
            item for item in active_items
            if _normalize_publish_status((item.get("payload") or {}).get("publish_status")) == "publish_ready"
        ][:limit]
        review_candidates = [
            item for item in active_items
            if _normalize_publish_status((item.get("payload") or {}).get("publish_status")) == "publish_requires_review"
        ][:limit]
        blocked_candidates = [
            item for item in active_items
            if _normalize_publish_status((item.get("payload") or {}).get("publish_status")) == "publish_blocked"
        ][:limit]
        waiting_row = conn.execute(
            """
            SELECT COUNT(*)
            FROM miru_review_queue rq
            WHERE rq.approval_state = 'approved_for_candidate'
              AND NOT EXISTS (
                SELECT 1
                FROM miru_publication_stage ps
                WHERE ps.item_key = rq.item_key
                  AND ps.stage_state IN ('staged_candidate', 'staged_batch_member')
              )
            """
        ).fetchone()
        approved_waiting_to_stage = _safe_int(waiting_row[0] if waiting_row is not None else 0)
        latest = _load_metadata(conn, sync_key=STAGING_METADATA_KEY)
    return {
        "counts": counts,
        "active_count": counts.get("staged_candidate", 0) + counts.get("staged_batch_member", 0),
        "approved_waiting_to_stage": approved_waiting_to_stage,
        "active_items": active_items,
        "top_scored_candidates": top_scored_candidates,
        "candidate_profile_counts": profile_counts,
        "candidate_publish_counts": publish_counts,
        "publish_ready_candidates": publish_ready_candidates,
        "review_required_candidates": review_candidates,
        "blocked_candidates": blocked_candidates,
        "latest": latest,
    }


def _batch_counts_template() -> dict[str, int]:
    return {
        "draft": 0,
        "review_ready": 0,
        "mixed_state": 0,
        "blocked": 0,
        "archived": 0,
    }


def _derive_batch_status(member_rows: list[dict[str, Any]]) -> tuple[str, str, str, dict[str, int]]:
    counts = {
        "ready_member_count": 0,
        "review_member_count": 0,
        "blocked_member_count": 0,
        "deferred_member_count": 0,
    }
    if not member_rows:
        return ("draft", "Read-only", "No staged members are currently attached to this batch.", counts)
    for item in member_rows:
        approval_state = _normalize_approval_state(item.get("approval_state"))
        readiness_state = str(item.get("readiness_state") or "").strip().lower()
        guardrail = str(item.get("guardrail_label") or "").strip().lower()
        if approval_state == "deferred":
            counts["deferred_member_count"] += 1
        elif approval_state in {"rejected", "superseded"} or readiness_state in {"blocked_by_guardrail", "not_ready"}:
            counts["blocked_member_count"] += 1
        elif guardrail == "review required" or readiness_state == "ready_for_review":
            counts["review_member_count"] += 1
        else:
            counts["ready_member_count"] += 1
    if counts["blocked_member_count"] > 0 or counts["deferred_member_count"] > 0:
        return ("blocked", "Blocked", "At least one batch member is blocked or deferred, so the batch cannot be treated as a clean promotion-prep group.", counts)
    if counts["review_member_count"] > 0:
        return ("mixed_state", "Review required", "The batch mixes approved-safe members with review-heavy members, so it needs careful batch review.", counts)
    return ("review_ready", "Safe action", "All staged members are approved and clean enough for backend review-ready batch preparation.", counts)


def build_publication_batch_summary(
    *,
    batch_id: str,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    limit: int = 24,
) -> dict[str, Any]:
    normalized_batch_id = str(batch_id or "").strip()
    if not normalized_batch_id:
        return {"batch_id": "", "batch_status": "", "members": [], "counts": _batch_counts_template()}
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    with closing(connect_catalog_db(project_path)) as conn:
        batch_row = _load_batch_row(conn, normalized_batch_id)
        member_rows = _load_batch_members(conn, normalized_batch_id, include_removed=False)
    derived_status, guardrail_label, rationale, member_counts = _derive_batch_status(member_rows)
    if batch_row and _normalize_batch_status(batch_row.get("batch_status")) == "archived":
        derived_status = "archived"
        guardrail_label = str(batch_row.get("guardrail_label") or "Read-only").strip() or "Read-only"
        rationale = str(batch_row.get("rationale") or rationale).strip() or rationale
    member_summaries: list[dict[str, Any]] = []
    for row in member_rows:
        payload = dict(row.get("payload") or {})
        if (
            not payload
            or not str(payload.get("candidate_score_band") or "").strip()
            or not str(payload.get("publish_status") or "").strip()
        ):
            payload = build_publication_candidate_summary(
                card_code=str(row.get("target_id") or "").strip(),
                project_db_path=project_path,
            )
        payload = {
            **payload,
            "target_id": str(row.get("target_id") or payload.get("card_code") or "").strip(),
            "item_key": str(row.get("item_key") or "").strip(),
            "stage_state": str(row.get("stage_state") or payload.get("stage_state") or "").strip(),
            "readiness_state": str(row.get("readiness_state") or payload.get("readiness_state") or "").strip(),
            "approval_state": _normalize_approval_state(row.get("approval_state") or payload.get("approval_state")),
            "promotion_state": _normalize_promotion_state(row.get("promotion_state") or payload.get("promotion_state")),
            "guardrail_label": str(row.get("guardrail_label") or payload.get("guardrail_label") or "").strip(),
            "confidence": round(_safe_float(row.get("confidence_score") or payload.get("confidence")), 3),
            "candidate_score": round(_safe_float(row.get("candidate_score") or payload.get("candidate_score")), 3),
            "candidate_score_band": str(row.get("candidate_score_band") or payload.get("candidate_score_band") or "").strip(),
            "candidate_profile": str(row.get("candidate_profile") or payload.get("candidate_profile") or "").strip(),
            "summary_text": str(payload.get("summary_text") or "").strip(),
            "strong_sections": list(payload.get("strong_sections") or []),
            "candidate_score_reasons": list(payload.get("candidate_score_reasons") or []),
            "candidate_risk_factors": list(payload.get("candidate_risk_factors") or []),
            "publish_status": str(payload.get("publish_status") or "").strip(),
            "publish_reasons": list(payload.get("publish_reasons") or []),
            "publish_risks": list(payload.get("publish_risks") or []),
            "review_required": bool(payload.get("review_required")),
            "legality_sensitive": bool(payload.get("legality_sensitive")),
            "confidence_level": str(payload.get("confidence_level") or "").strip(),
        }
        member_summaries.append(payload)
    curation = _curate_batch_quality(
        member_summaries,
        existing_status=str(batch_row.get("batch_status") or derived_status or "").strip(),
    )
    members = [
        {
            "target_id": str(item.get("target_id") or "").strip(),
            "item_key": str(item.get("item_key") or "").strip(),
            "stage_state": str(item.get("stage_state") or "").strip(),
            "readiness_state": str(item.get("readiness_state") or "").strip(),
            "approval_state": _normalize_approval_state(item.get("approval_state")),
            "promotion_state": _normalize_promotion_state(item.get("promotion_state")),
            "guardrail_label": str(item.get("guardrail_label") or "").strip(),
            "confidence": round(_safe_float(item.get("confidence")), 3),
            "candidate_score": round(_safe_float(item.get("candidate_score")), 3),
            "candidate_score_band": str(item.get("candidate_score_band") or "").strip(),
            "candidate_profile": str(item.get("candidate_profile") or "").strip(),
            "summary_text": str(item.get("summary_text") or "").strip(),
            "strong_sections": list(item.get("strong_sections") or []),
            "candidate_score_reasons": list(item.get("candidate_score_reasons") or []),
            "candidate_risk_factors": list(item.get("candidate_risk_factors") or []),
            "publish_status": str(item.get("publish_status") or "").strip(),
            "publish_reasons": list(item.get("publish_reasons") or []),
            "publish_risks": list(item.get("publish_risks") or []),
            "review_required": bool(item.get("review_required")),
            "legality_sensitive": bool(item.get("legality_sensitive")),
            "confidence_level": str(item.get("confidence_level") or "").strip(),
        }
        for item in sorted(member_summaries, key=_candidate_sort_key)[: max(1, limit)]
    ]
    summary = {
        "batch_id": normalized_batch_id,
        "batch_status": derived_status,
        "batch_title": str(batch_row.get("batch_title") or normalized_batch_id).strip() if batch_row else normalized_batch_id,
        "guardrail_label": guardrail_label,
        "rationale": rationale,
        "summary_text": str(batch_row.get("summary_text") or rationale).strip() if batch_row else rationale,
        "batch_quality_score": _safe_float(batch_row.get("batch_quality_score") or curation.get("batch_quality_score")),
        "batch_quality_band": str(batch_row.get("batch_quality_band") or curation.get("batch_quality_band") or "").strip(),
        "batch_profile": str(batch_row.get("batch_profile") or curation.get("batch_profile") or "").strip(),
        "member_count": len(member_rows),
        "counts": {
            "ready_member_count": member_counts["ready_member_count"],
            "review_member_count": member_counts["review_member_count"],
            "blocked_member_count": member_counts["blocked_member_count"],
            "deferred_member_count": member_counts["deferred_member_count"],
        },
        "members": members,
        "strongest_reasons": list(batch_row.get("strongest_reasons") or curation.get("strongest_reasons") or []),
        "unresolved_risks": list(batch_row.get("unresolved_risks") or curation.get("unresolved_risks") or []),
        "recommended_next_step": str(batch_row.get("recommended_next_step") or curation.get("recommended_next_step") or "").strip(),
        "common_sections": list(curation.get("common_sections") or []),
        "split_suggestion": dict(curation.get("split_suggestion") or {"needed": False, "groups": []}),
        "created_at": str(batch_row.get("created_at") or "").strip() if batch_row else "",
        "updated_at": str(batch_row.get("updated_at") or "").strip() if batch_row else "",
        "archived_at": str(batch_row.get("archived_at") or "").strip() if batch_row else "",
    }
    publish_gate = _evaluate_batch_publication_gate_from_summary(summary)
    return {
        **summary,
        **publish_gate,
    }


def evaluate_publication_batch_gate(
    *,
    batch_id: str,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    limit: int = 24,
) -> dict[str, Any]:
    summary = build_publication_batch_summary(
        batch_id=batch_id,
        project_db_path=project_db_path,
        limit=limit,
    )
    return {
        "batch_id": str(summary.get("batch_id") or "").strip(),
        "batch_publish_status": str(summary.get("batch_publish_status") or "").strip(),
        "batch_publish_gate_decision": str(summary.get("batch_publish_gate_decision") or "").strip(),
        "batch_publish_reasons": list(summary.get("batch_publish_reasons") or []),
        "batch_publish_risks": list(summary.get("batch_publish_risks") or []),
        "recommended_next_step": str(summary.get("recommended_next_step") or "").strip(),
        "batch": summary,
    }


def load_publication_batch_summary(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    limit: int = 8,
) -> dict[str, Any]:
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    with closing(connect_catalog_db(project_path)) as conn:
        counts = _batch_counts_template()
        for row in conn.execute(
            "SELECT batch_status, COUNT(*) AS row_count FROM miru_publication_batches GROUP BY batch_status"
        ).fetchall():
            key = _normalize_batch_status(row["batch_status"])
            if key:
                counts[key] = counts.get(key, 0) + _safe_int(row["row_count"])
        batch_rows = conn.execute(
            """
            SELECT batch_id
            FROM miru_publication_batches
            ORDER BY
                CASE batch_status
                    WHEN 'review_ready' THEN 0
                    WHEN 'mixed_state' THEN 1
                    WHEN 'draft' THEN 2
                    WHEN 'blocked' THEN 3
                    WHEN 'archived' THEN 4
                    ELSE 5
                END,
                updated_at DESC,
                batch_id ASC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
        latest = _load_metadata(conn, sync_key=BATCH_METADATA_KEY)
    batches = [
        build_publication_batch_summary(batch_id=str(row["batch_id"] or "").strip(), project_db_path=project_path, limit=6)
        for row in batch_rows
    ]
    quality_counts = {band: 0 for band in BATCH_QUALITY_BANDS}
    profile_counts: dict[str, int] = {"cohesive": 0, "mixed": 0, "review_heavy": 0, "weak": 0, "blocked": 0, "archived": 0}
    publish_counts = {status: 0 for status in BATCH_PUBLISH_STATUSES}
    for batch in batches:
        band = str(batch.get("batch_quality_band") or "").strip()
        if band:
            quality_counts[band] = quality_counts.get(band, 0) + 1
        profile = str(batch.get("batch_profile") or "").strip()
        if profile:
            profile_counts[profile] = profile_counts.get(profile, 0) + 1
        publish_status = _normalize_batch_publish_status(batch.get("batch_publish_status"))
        if publish_status:
            publish_counts[publish_status] = publish_counts.get(publish_status, 0) + 1
    top_batches = sorted(
        batches,
        key=lambda item: (
            -_safe_float(item.get("batch_quality_score")),
            -_safe_int(item.get("member_count")),
            str(item.get("batch_id") or ""),
        ),
    )[:limit]
    mixed_batches = [batch for batch in batches if str(batch.get("batch_profile") or "") in {"mixed", "review_heavy", "blocked"}][:limit]
    publish_ready_batches = [batch for batch in batches if str(batch.get("batch_publish_status") or "") == "publish_ready_batch"][:limit]
    return {
        "counts": counts,
        "active_count": counts.get("draft", 0) + counts.get("review_ready", 0) + counts.get("mixed_state", 0) + counts.get("blocked", 0),
        "batches": batches,
        "batch_quality_counts": quality_counts,
        "batch_profile_counts": profile_counts,
        "batch_publish_counts": publish_counts,
        "top_batches": top_batches,
        "mixed_batches": mixed_batches,
        "publish_ready_batches": publish_ready_batches,
        "latest": latest,
    }


def load_publication_release_summary(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    limit: int = 8,
) -> dict[str, Any]:
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    bounded_limit = max(1, limit)
    with closing(connect_catalog_db(project_path)) as conn:
        counts = {status: 0 for status in PUBLISH_STATUSES}
        for row in conn.execute(
            "SELECT publish_status, COUNT(*) AS row_count FROM card_intelligence GROUP BY publish_status"
        ).fetchall():
            key = _normalize_publish_status(row["publish_status"])
            if key:
                counts[key] = _safe_int(row["row_count"])
        rows = conn.execute(
            """
            SELECT
                c.canonical_code AS card_code,
                c.card_name,
                ci.publication_readiness,
                ci.approval_state,
                ci.promotion_state,
                ci.publication_guardrail,
                ci.confidence_score,
                ci.publication_candidate_score,
                ci.publication_candidate_score_band,
                ci.publication_candidate_profile,
                ci.publish_status,
                ci.publish_reasons_json,
                ci.publish_risks_json,
                ci.publish_payload_json,
                ci.last_verified_at
            FROM card_intelligence ci
            JOIN cards c
                ON c.id = ci.card_id
            WHERE trim(coalesce(ci.publish_status, '')) != ''
            ORDER BY
                CASE trim(coalesce(ci.publish_status, ''))
                    WHEN 'publish_ready' THEN 0
                    WHEN 'publish_requires_review' THEN 1
                    WHEN 'publish_deferred' THEN 2
                    WHEN 'publish_blocked' THEN 3
                    ELSE 4
                END,
                CASE trim(coalesce(ci.approval_state, ''))
                    WHEN 'approved_for_candidate' THEN 0
                    WHEN 'pending_review' THEN 1
                    WHEN 'deferred' THEN 3
                    WHEN 'rejected' THEN 4
                    ELSE 4
                END,
                COALESCE(ci.publication_candidate_score, 0) DESC,
                COALESCE(ci.confidence_score, 0) DESC,
                c.canonical_code ASC
            LIMIT ?
            """,
            (bounded_limit * 6,),
        ).fetchall()
        latest = _load_metadata(conn, sync_key=PUBLICATION_RELEASE_METADATA_KEY)
    candidates: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_load(row["publish_payload_json"], {})
        if not isinstance(payload, dict):
            payload = {}
        confidence_indicators = payload.get("confidence_indicators")
        if not isinstance(confidence_indicators, dict):
            confidence_indicators = {}
        flags = payload.get("flags")
        if not isinstance(flags, dict):
            flags = {}
        source_attribution = payload.get("source_attribution")
        if not isinstance(source_attribution, dict):
            source_attribution = {}
        candidates.append(
            {
                "card_code": str(row["card_code"] or "").strip(),
                "card_name": str(row["card_name"] or "").strip(),
                "readiness_state": str(row["publication_readiness"] or "").strip(),
                "approval_state": _normalize_approval_state(row["approval_state"]),
                "promotion_state": _normalize_promotion_state(row["promotion_state"]),
                "guardrail_label": str(row["publication_guardrail"] or "").strip(),
                "confidence": _safe_float(row["confidence_score"]),
                "candidate_score": _safe_float(row["publication_candidate_score"]),
                "candidate_score_band": str(row["publication_candidate_score_band"] or "").strip(),
                "candidate_profile": str(row["publication_candidate_profile"] or "").strip(),
                "publish_status": _normalize_publish_status(row["publish_status"]),
                "publish_reasons": list(_json_load(row["publish_reasons_json"], [])),
                "publish_risks": list(_json_load(row["publish_risks_json"], [])),
                "review_required": bool(flags.get("review_required")),
                "legality_sensitive": bool(flags.get("legality_sensitive")),
                "confidence_level": str(
                    confidence_indicators.get("confidence_level") or flags.get("confidence_level") or ""
                ).strip(),
                "last_verified_at": str(confidence_indicators.get("last_verified_at") or row["last_verified_at"] or "").strip(),
                "summary_text": str(payload.get("insight_summary") or "").strip(),
                "supporting_sections": list(payload.get("supporting_sections") or []),
                "publication_payload": payload,
                "source_attribution": source_attribution,
            }
        )
    publish_ready = sorted(
        [item for item in candidates if _normalize_publish_status(item.get("publish_status")) == "publish_ready"],
        key=lambda item: (-_safe_float(item.get("candidate_score")), -_safe_float(item.get("confidence")), str(item.get("card_code") or "")),
    )[:bounded_limit]
    review_required = sorted(
        [item for item in candidates if _normalize_publish_status(item.get("publish_status")) == "publish_requires_review"],
        key=lambda item: (-_safe_float(item.get("candidate_score")), -_safe_float(item.get("confidence")), str(item.get("card_code") or "")),
    )[:bounded_limit]
    blocked = sorted(
        [item for item in candidates if _normalize_publish_status(item.get("publish_status")) == "publish_blocked"],
        key=lambda item: (_safe_float(item.get("candidate_score")), str(item.get("card_code") or "")),
    )[:bounded_limit]
    deferred = sorted(
        [item for item in candidates if _normalize_publish_status(item.get("publish_status")) == "publish_deferred"],
        key=lambda item: (-_safe_float(item.get("candidate_score")), -_safe_float(item.get("confidence")), str(item.get("card_code") or "")),
    )[:bounded_limit]
    batch_summary = load_publication_batch_summary(project_db_path=project_path, limit=bounded_limit)
    return {
        "counts": counts,
        "publish_ready_candidates": publish_ready,
        "review_required_candidates": review_required,
        "blocked_candidates": blocked,
        "deferred_candidates": deferred,
        "publish_ready_batches": batch_summary.get("publish_ready_batches") or [],
        "mixed_batches": batch_summary.get("mixed_batches") or [],
        "batch_publish_counts": batch_summary.get("batch_publish_counts") or {},
        "latest": latest,
    }


def _persist_stage_row(
    conn: sqlite3.Connection,
    *,
    summary: dict[str, Any],
    stage_state: str,
    batch_id: str = "",
    note: str = "",
    decision_source: str = "",
) -> dict[str, Any]:
    card_code = str(summary.get("card_code") or "").strip().upper()
    item_key = _review_queue_item_key(card_code)
    now = _utc_now_timestamp()
    normalized_state = _normalize_stage_state(stage_state)
    conn.execute(
        """
        INSERT INTO miru_publication_stage (
            item_key,
            target_type,
            target_id,
            readiness_state,
            approval_state,
            promotion_state,
            stage_state,
            guardrail_label,
            confidence_score,
            risk_level,
            candidate_score,
            candidate_score_band,
            candidate_profile,
            candidate_score_reasons_json,
            candidate_risk_factors_json,
            rationale,
            summary_text,
            supporting_sections_json,
            payload_json,
            batch_id,
            note,
            decision_source,
            created_at,
            updated_at,
            removed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_key) DO UPDATE SET
            readiness_state = excluded.readiness_state,
            approval_state = excluded.approval_state,
            promotion_state = excluded.promotion_state,
            stage_state = excluded.stage_state,
            guardrail_label = excluded.guardrail_label,
            confidence_score = excluded.confidence_score,
            risk_level = excluded.risk_level,
            candidate_score = excluded.candidate_score,
            candidate_score_band = excluded.candidate_score_band,
            candidate_profile = excluded.candidate_profile,
            candidate_score_reasons_json = excluded.candidate_score_reasons_json,
            candidate_risk_factors_json = excluded.candidate_risk_factors_json,
            rationale = excluded.rationale,
            summary_text = excluded.summary_text,
            supporting_sections_json = excluded.supporting_sections_json,
            payload_json = excluded.payload_json,
            batch_id = excluded.batch_id,
            note = excluded.note,
            decision_source = excluded.decision_source,
            updated_at = excluded.updated_at,
            removed_at = excluded.removed_at
        """,
        (
            item_key,
            "card",
            card_code,
            str(summary.get("readiness_state") or "").strip(),
            _normalize_approval_state(summary.get("approval_state")),
            _normalize_promotion_state(summary.get("promotion_state")),
            normalized_state,
            str(summary.get("guardrail_label") or "").strip(),
            round(_safe_float(summary.get("confidence")), 3),
            str(summary.get("risk_level") or "").strip(),
            round(_safe_float(summary.get("candidate_score")), 3),
            str(summary.get("candidate_score_band") or "").strip(),
            str(summary.get("candidate_profile") or "").strip(),
            _json_dump(summary.get("candidate_score_reasons") or []),
            _json_dump(summary.get("candidate_risk_factors") or []),
            str(summary.get("stage_rationale") or summary.get("rationale") or "").strip(),
            str(summary.get("summary_text") or "").strip(),
            _json_dump(summary.get("strong_sections") or []),
            _json_dump(summary),
            str(batch_id or "").strip(),
            str(note or summary.get("approval_note") or "").strip(),
            str(decision_source or "").strip(),
            now,
            now,
            now if normalized_state == "removed_from_stage" else "",
        ),
    )
    return {
        "item_key": item_key,
        "target_id": card_code,
        "stage_state": normalized_state,
        "batch_id": str(batch_id or "").strip(),
        "candidate_score": round(_safe_float(summary.get("candidate_score")), 3),
        "candidate_profile": str(summary.get("candidate_profile") or "").strip(),
    }


def _refresh_publication_stage_metadata(
    conn: sqlite3.Connection,
    *,
    project_db_path: str | Path,
    source: str,
    last_change: dict[str, Any] | None = None,
) -> None:
    summary = load_publication_stage_summary(project_db_path=project_db_path, limit=8)
    _store_metadata(
        conn,
        sync_key=STAGING_METADATA_KEY,
        payload={
            "updated_at": _utc_now_timestamp(),
            "source": source,
            "counts": summary.get("counts") or {},
            "active_count": summary.get("active_count") or 0,
            "approved_waiting_to_stage": summary.get("approved_waiting_to_stage") or 0,
            "candidate_profile_counts": summary.get("candidate_profile_counts") or {},
            "candidate_publish_counts": summary.get("candidate_publish_counts") or {},
            "top_scored_candidates": summary.get("top_scored_candidates") or [],
            "publish_ready_candidates": summary.get("publish_ready_candidates") or [],
            "review_required_candidates": summary.get("review_required_candidates") or [],
            "blocked_candidates": summary.get("blocked_candidates") or [],
            "last_change": last_change or {},
        },
    )


def _refresh_publication_batch_metadata(
    conn: sqlite3.Connection,
    *,
    project_db_path: str | Path,
    source: str,
    last_change: dict[str, Any] | None = None,
) -> None:
    summary = load_publication_batch_summary(project_db_path=project_db_path, limit=8)
    _store_metadata(
        conn,
        sync_key=BATCH_METADATA_KEY,
        payload={
            "updated_at": _utc_now_timestamp(),
            "source": source,
            "counts": summary.get("counts") or {},
            "active_count": summary.get("active_count") or 0,
            "batch_quality_counts": summary.get("batch_quality_counts") or {},
            "batch_profile_counts": summary.get("batch_profile_counts") or {},
            "batch_publish_counts": summary.get("batch_publish_counts") or {},
            "top_batches": summary.get("top_batches") or [],
            "publish_ready_batches": summary.get("publish_ready_batches") or [],
            "mixed_batches": summary.get("mixed_batches") or [],
            "last_change": last_change or {},
        },
    )


def _refresh_publication_release_metadata(
    conn: sqlite3.Connection,
    *,
    project_db_path: str | Path,
    source: str,
    last_change: dict[str, Any] | None = None,
) -> None:
    summary = load_publication_release_summary(project_db_path=project_db_path, limit=8)
    _store_metadata(
        conn,
        sync_key=PUBLICATION_RELEASE_METADATA_KEY,
        payload={
            "updated_at": _utc_now_timestamp(),
            "source": source,
            "counts": summary.get("counts") or {},
            "batch_publish_counts": summary.get("batch_publish_counts") or {},
            "publish_ready_candidates": summary.get("publish_ready_candidates") or [],
            "review_required_candidates": summary.get("review_required_candidates") or [],
            "blocked_candidates": summary.get("blocked_candidates") or [],
            "deferred_candidates": summary.get("deferred_candidates") or [],
            "publish_ready_batches": summary.get("publish_ready_batches") or [],
            "mixed_batches": summary.get("mixed_batches") or [],
            "last_change": last_change or {},
        },
    )


def _select_curated_stage_rows(stage_rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    if not stage_rows:
        return []
    bounded_limit = max(1, limit)
    enriched: list[dict[str, Any]] = []
    for row in stage_rows:
        payload = dict(row.get("payload") or {})
        enriched.append(
            {
                **row,
                "payload": payload,
                "candidate_score": _safe_float(row.get("candidate_score") or payload.get("candidate_score")),
                "candidate_profile": str(row.get("candidate_profile") or payload.get("candidate_profile") or "").strip(),
                "confidence_score": _safe_float(row.get("confidence_score") or payload.get("confidence")),
                "strong_sections": list(payload.get("strong_sections") or []),
                "guardrail_label": str(row.get("guardrail_label") or payload.get("guardrail_label") or "").strip(),
            }
        )
    enriched.sort(
        key=lambda item: (
            -_safe_float(item.get("candidate_score")),
            -_safe_float(item.get("confidence_score")),
            str(item.get("target_id") or ""),
        )
    )
    seed = enriched[0]
    seed_profile = str(seed.get("candidate_profile") or "").strip()
    seed_sections = set(seed.get("strong_sections") or [])

    def compatibility(item: dict[str, Any]) -> float:
        value = 0.0
        if str(item.get("candidate_profile") or "").strip() == seed_profile and seed_profile:
            value += 16.0
        if str(item.get("guardrail_label") or "").strip().lower() == str(seed.get("guardrail_label") or "").strip().lower():
            value += 6.0
        overlap = len(seed_sections.intersection(set(item.get("strong_sections") or [])))
        value += overlap * 3.0
        if str(item.get("candidate_profile") or "").strip() == "blocked":
            value -= 30.0
        return value

    selected: list[dict[str, Any]] = []
    for item in sorted(enriched, key=lambda entry: (-compatibility(entry), -_safe_float(entry.get("candidate_score")), str(entry.get("target_id") or ""))):
        if len(selected) >= bounded_limit:
            break
        if not selected:
            selected.append(item)
            continue
        if compatibility(item) >= 10.0 or (
            str(item.get("candidate_profile") or "").strip()
            and str(item.get("candidate_profile") or "").strip() == seed_profile
        ):
            selected.append(item)
    if not selected:
        selected = [seed]
    return selected[:bounded_limit]


def _generate_batch_id(member_ids: list[str]) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    sample = "-".join(member_ids[:2]).lower().replace(" ", "-")
    sample = sample[:32] if sample else "batch"
    return f"miru-stage-{stamp}-{sample}"


def _upsert_batch_member_row(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    summary: dict[str, Any],
    stage_state: str,
    note: str = "",
    decision_source: str = "",
) -> dict[str, Any]:
    card_code = str(summary.get("card_code") or "").strip().upper()
    item_key = _review_queue_item_key(card_code)
    now = _utc_now_timestamp()
    conn.execute(
        """
        INSERT INTO miru_publication_batch_items (
            batch_id,
            item_key,
            target_id,
            stage_state,
            readiness_state,
            approval_state,
            promotion_state,
            guardrail_label,
            confidence_score,
            candidate_score,
            candidate_score_band,
            candidate_profile,
            rationale,
            payload_json,
            status,
            note,
            added_at,
            updated_at,
            removed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(batch_id, item_key) DO UPDATE SET
            target_id = excluded.target_id,
            stage_state = excluded.stage_state,
            readiness_state = excluded.readiness_state,
            approval_state = excluded.approval_state,
            promotion_state = excluded.promotion_state,
            guardrail_label = excluded.guardrail_label,
            confidence_score = excluded.confidence_score,
            candidate_score = excluded.candidate_score,
            candidate_score_band = excluded.candidate_score_band,
            candidate_profile = excluded.candidate_profile,
            rationale = excluded.rationale,
            payload_json = excluded.payload_json,
            status = excluded.status,
            note = excluded.note,
            updated_at = excluded.updated_at,
            removed_at = excluded.removed_at
        """,
        (
            str(batch_id or "").strip(),
            item_key,
            card_code,
            _normalize_stage_state(stage_state),
            str(summary.get("readiness_state") or "").strip(),
            _normalize_approval_state(summary.get("approval_state")),
            _normalize_promotion_state(summary.get("promotion_state")),
            str(summary.get("guardrail_label") or "").strip(),
            round(_safe_float(summary.get("confidence")), 3),
            round(_safe_float(summary.get("candidate_score")), 3),
            str(summary.get("candidate_score_band") or "").strip(),
            str(summary.get("candidate_profile") or "").strip(),
            str(summary.get("stage_rationale") or summary.get("rationale") or "").strip(),
            _json_dump(summary),
            "active",
            str(note or "").strip(),
            now,
            now,
            "",
        ),
    )
    return {"batch_id": str(batch_id or "").strip(), "item_key": item_key, "target_id": card_code}


def _refresh_batch_record(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    batch_title: str = "",
    rationale_note: str = "",
    force_status: str = "",
) -> dict[str, Any]:
    normalized_batch_id = str(batch_id or "").strip()
    member_rows = _load_batch_members(conn, normalized_batch_id, include_removed=False)
    batch_row = _load_batch_row(conn, normalized_batch_id)
    derived_status, guardrail_label, rationale, member_counts = _derive_batch_status(member_rows)
    batch_status = _normalize_batch_status(force_status) or derived_status
    now = _utc_now_timestamp()
    member_count = len(member_rows)
    if batch_status == "archived":
        archived_at = now
    else:
        archived_at = str(batch_row.get("archived_at") or "").strip()
    summary_text = (
        str(rationale_note or batch_row.get("summary_text") or "").strip()
        or rationale
    )
    member_payloads: list[dict[str, Any]] = []
    for item in member_rows:
        payload = dict(item.get("payload") or {})
        if (
            not payload
            or not str(payload.get("candidate_score_band") or "").strip()
            or not str(payload.get("publish_status") or "").strip()
        ):
            payload = build_publication_candidate_summary(
                card_code=str(item.get("target_id") or "").strip(),
                project_db_path=DEFAULT_PROJECT_DB_PATH if project_db_path is None else project_db_path,
            )
        payload.setdefault("target_id", str(item.get("target_id") or "").strip())
        payload.setdefault("candidate_score", _safe_float(item.get("candidate_score")))
        payload.setdefault("candidate_score_band", str(item.get("candidate_score_band") or "").strip())
        payload.setdefault("candidate_profile", str(item.get("candidate_profile") or "").strip())
        payload.setdefault("approval_state", _normalize_approval_state(item.get("approval_state")))
        payload.setdefault("strong_sections", list(payload.get("strong_sections") or []))
        member_payloads.append(payload)
    curation = _curate_batch_quality(member_payloads, existing_status=batch_status)
    payload = {
        "batch_id": normalized_batch_id,
        "member_ids": [str(item.get("target_id") or "").strip() for item in member_rows],
        "member_count": member_count,
        "guardrail_label": guardrail_label,
        "rationale": rationale,
        "counts": member_counts,
        "batch_quality_score": _safe_float(curation.get("batch_quality_score")),
        "batch_quality_band": str(curation.get("batch_quality_band") or "").strip(),
        "batch_profile": str(curation.get("batch_profile") or "").strip(),
        "strongest_reasons": list(curation.get("strongest_reasons") or []),
        "unresolved_risks": list(curation.get("unresolved_risks") or []),
        "recommended_next_step": str(curation.get("recommended_next_step") or "").strip(),
        "split_suggestion": dict(curation.get("split_suggestion") or {"needed": False, "groups": []}),
    }
    publish_gate = _evaluate_batch_publication_gate_from_summary(
        {
            "batch_id": normalized_batch_id,
            "batch_status": batch_status,
            "batch_profile": str(curation.get("batch_profile") or "").strip(),
            "batch_quality_score": _safe_float(curation.get("batch_quality_score")),
            "counts": member_counts,
            "members": member_payloads,
            "unresolved_risks": list(curation.get("unresolved_risks") or []),
            "recommended_next_step": str(curation.get("recommended_next_step") or "").strip(),
            "split_suggestion": dict(curation.get("split_suggestion") or {"needed": False, "groups": []}),
        }
    )
    payload.update(publish_gate)
    conn.execute(
        """
        INSERT INTO miru_publication_batches (
            batch_id,
            batch_status,
            batch_title,
            rationale,
            summary_text,
            guardrail_label,
            batch_quality_score,
            batch_quality_band,
            batch_profile,
            member_count,
            ready_member_count,
            review_member_count,
            blocked_member_count,
            deferred_member_count,
            batch_publish_status,
            batch_publish_reasons_json,
            batch_publish_risks_json,
            batch_publish_payload_json,
            batch_publish_updated_at,
            strongest_reasons_json,
            unresolved_risks_json,
            recommended_next_step,
            payload_json,
            created_at,
            updated_at,
            archived_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(batch_id) DO UPDATE SET
            batch_status = excluded.batch_status,
            batch_title = excluded.batch_title,
            rationale = excluded.rationale,
            summary_text = excluded.summary_text,
            guardrail_label = excluded.guardrail_label,
            batch_quality_score = excluded.batch_quality_score,
            batch_quality_band = excluded.batch_quality_band,
            batch_profile = excluded.batch_profile,
            member_count = excluded.member_count,
            ready_member_count = excluded.ready_member_count,
            review_member_count = excluded.review_member_count,
            blocked_member_count = excluded.blocked_member_count,
            deferred_member_count = excluded.deferred_member_count,
            batch_publish_status = excluded.batch_publish_status,
            batch_publish_reasons_json = excluded.batch_publish_reasons_json,
            batch_publish_risks_json = excluded.batch_publish_risks_json,
            batch_publish_payload_json = excluded.batch_publish_payload_json,
            batch_publish_updated_at = excluded.batch_publish_updated_at,
            strongest_reasons_json = excluded.strongest_reasons_json,
            unresolved_risks_json = excluded.unresolved_risks_json,
            recommended_next_step = excluded.recommended_next_step,
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at,
            archived_at = excluded.archived_at
        """,
        (
            normalized_batch_id,
            batch_status,
            str(batch_title or batch_row.get("batch_title") or normalized_batch_id).strip(),
            str(rationale_note or batch_row.get("rationale") or rationale).strip(),
            summary_text,
            guardrail_label,
            _safe_float(curation.get("batch_quality_score")),
            str(curation.get("batch_quality_band") or "").strip(),
            str(curation.get("batch_profile") or "").strip(),
            member_count,
            member_counts["ready_member_count"],
            member_counts["review_member_count"],
            member_counts["blocked_member_count"],
            member_counts["deferred_member_count"],
            _normalize_batch_publish_status(publish_gate.get("batch_publish_status")),
            _json_dump(publish_gate.get("batch_publish_reasons") or []),
            _json_dump(publish_gate.get("batch_publish_risks") or []),
            _json_dump({
                "batch_id": normalized_batch_id,
                "batch_status": batch_status,
                "batch_quality_score": _safe_float(curation.get("batch_quality_score")),
                "batch_quality_band": str(curation.get("batch_quality_band") or "").strip(),
                "batch_profile": str(curation.get("batch_profile") or "").strip(),
                "batch_publish_status": _normalize_batch_publish_status(publish_gate.get("batch_publish_status")),
                "batch_publish_gate_decision": str(publish_gate.get("batch_publish_gate_decision") or "").strip(),
                "member_ids": [str(item.get("target_id") or "").strip() for item in member_rows],
            }),
            now,
            _json_dump(curation.get("strongest_reasons") or []),
            _json_dump(curation.get("unresolved_risks") or []),
            str(curation.get("recommended_next_step") or "").strip(),
            _json_dump(payload),
            str(batch_row.get("created_at") or now).strip() if batch_row else now,
            now,
            archived_at,
        ),
    )
    return {
        "batch_id": normalized_batch_id,
        "batch_status": batch_status,
        "guardrail_label": guardrail_label,
        "rationale": rationale,
        "member_count": member_count,
        "batch_quality_score": _safe_float(curation.get("batch_quality_score")),
        "batch_profile": str(curation.get("batch_profile") or "").strip(),
        "batch_publish_status": _normalize_batch_publish_status(publish_gate.get("batch_publish_status")),
    }


def stage_publication_candidate(
    *,
    card_code: str,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
    note: str = "",
    decision_source: str = "",
    batch_id: str = "",
    runtime_uncertain: bool = False,
    persist_blocked: bool = False,
) -> dict[str, Any]:
    project_path = Path(project_db_path)
    summary = build_publication_candidate_summary(
        card_code=card_code,
        project_db_path=project_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
    )
    normalized_code = str(summary.get("card_code") or "").strip().upper()
    with closing(connect_catalog_db(project_path)) as conn:
        stage_row = _load_stage_row(conn, normalized_code)
        stage_view = _derive_staging_fields(summary, stage_row=stage_row, runtime_uncertain=runtime_uncertain)
        summary = {**summary, "stage_state": stage_view["stage_state"], "stage_rationale": stage_view["rationale"]}
        if not stage_view.get("stageable"):
            if persist_blocked:
                blocked = _persist_stage_row(
                    conn,
                    summary=summary,
                    stage_state="blocked_from_staging",
                    note=str(note or stage_view.get("rationale") or "").strip(),
                    decision_source=decision_source or "stage.blocked_attempt",
                )
                conn.commit()
                _refresh_publication_stage_metadata(
                    conn,
                    project_db_path=project_path,
                    source=decision_source or "stage.blocked_attempt",
                    last_change=blocked,
                )
            return {
                "ok": False,
                "card_code": normalized_code,
                "stageable": False,
                "stage": stage_view,
                "summary": summary,
            }
        next_stage_state = "staged_batch_member" if str(batch_id or "").strip() else "staged_candidate"
        stage_record = _persist_stage_row(
            conn,
            summary=summary,
            stage_state=next_stage_state,
            batch_id=str(batch_id or "").strip(),
            note=note,
            decision_source=decision_source or "stage.stage_candidate",
        )
        batch_summary: dict[str, Any] = {}
        if str(batch_id or "").strip():
            _upsert_batch_member_row(
                conn,
                batch_id=str(batch_id or "").strip(),
                summary={**summary, "stage_rationale": stage_view.get("rationale")},
                stage_state=next_stage_state,
                note=note,
                decision_source=decision_source or "stage.stage_candidate",
            )
            _refresh_batch_record(
                conn,
                batch_id=str(batch_id or "").strip(),
                rationale_note=str(note or "").strip(),
            )
            conn.commit()
            _refresh_publication_batch_metadata(
                conn,
                project_db_path=project_path,
                source=decision_source or "stage.stage_candidate",
                last_change=stage_record,
            )
            batch_summary = build_publication_batch_summary(
                batch_id=str(batch_id or "").strip(),
                project_db_path=project_path,
                limit=12,
            )
        conn.commit()
        _refresh_publication_stage_metadata(
            conn,
            project_db_path=project_path,
            source=decision_source or "stage.stage_candidate",
            last_change=stage_record,
        )
    return {
        "ok": True,
        "card_code": normalized_code,
        "stageable": True,
        "stage": {**stage_view, "stage_state": next_stage_state, "batch_id": str(batch_id or "").strip()},
        "summary": {**summary, "stage_state": next_stage_state, "stage_batch_id": str(batch_id or "").strip()},
        "batch": batch_summary,
    }


def remove_staged_candidate(
    *,
    card_code: str,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    note: str = "",
    decision_source: str = "",
) -> dict[str, Any]:
    normalized_code = _normalize_code(card_code)
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    with closing(connect_catalog_db(project_path)) as conn:
        row = _load_stage_row(conn, normalized_code)
        if not row:
            return {"ok": False, "error": f"Stage item not found for {normalized_code}"}
        now = _utc_now_timestamp()
        batch_id = str(row.get("batch_id") or "").strip()
        conn.execute(
            """
            UPDATE miru_publication_stage
            SET stage_state = 'removed_from_stage',
                batch_id = '',
                note = ?,
                decision_source = ?,
                updated_at = ?,
                removed_at = ?
            WHERE item_key = ?
            """,
            (
                str(note or "").strip() or "Removed from publication staging.",
                str(decision_source or "").strip() or "stage.unstage_candidate",
                now,
                now,
                str(row.get("item_key") or "").strip(),
            ),
        )
        if batch_id:
            conn.execute(
                """
                UPDATE miru_publication_batch_items
                SET status = 'removed',
                    note = ?,
                    updated_at = ?,
                    removed_at = ?
                WHERE batch_id = ?
                  AND item_key = ?
                  AND status = 'active'
                """,
                (
                    str(note or "").strip() or "Removed from batch staging.",
                    now,
                    now,
                    batch_id,
                    str(row.get("item_key") or "").strip(),
                ),
            )
        conn.commit()
        _refresh_publication_stage_metadata(
            conn,
            project_db_path=project_path,
            source=decision_source or "stage.unstage_candidate",
            last_change={"target_id": normalized_code, "stage_state": "removed_from_stage", "batch_id": batch_id},
        )
        if batch_id:
            _refresh_publication_batch_metadata(
                conn,
                project_db_path=project_path,
                source=decision_source or "stage.unstage_candidate",
                last_change={"batch_id": batch_id, "target_id": normalized_code, "action": "remove_member"},
            )
    batch_summary = (
        build_publication_batch_summary(batch_id=batch_id, project_db_path=project_path, limit=12)
        if batch_id else {}
    )
    return {"ok": True, "target_id": normalized_code, "stage_state": "removed_from_stage", "batch": batch_summary}


def create_publication_batch(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    member_card_codes: list[str] | None = None,
    batch_id: str = "",
    note: str = "",
    limit: int = 8,
) -> dict[str, Any]:
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    requested = [_normalize_code(code) for code in list(member_card_codes or []) if _normalize_code(code)]
    selected_rows: list[dict[str, Any]] = []
    with closing(connect_catalog_db(project_path)) as conn:
        if requested:
            placeholders = ",".join("?" for _ in requested)
            rows = conn.execute(
                f"""
                SELECT *
                FROM miru_publication_stage
                WHERE target_id IN ({placeholders})
                  AND stage_state IN ('staged_candidate', 'staged_batch_member')
                ORDER BY updated_at DESC, target_id ASC
                """,
                tuple(requested),
            ).fetchall()
            selected_rows = [dict(row) for row in rows]
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM miru_publication_stage
                WHERE stage_state = 'staged_candidate'
                  AND trim(coalesce(batch_id, '')) = ''
                ORDER BY COALESCE(candidate_score, 0) DESC, confidence_score DESC, updated_at DESC, target_id ASC
                LIMIT ?
                """,
                (max(8, limit * 3),),
            ).fetchall()
            selected_rows = [dict(row) for row in rows]
        for row in selected_rows:
            row["payload"] = dict(_json_load(row.get("payload_json"), {}))
        if not requested:
            selected_rows = _select_curated_stage_rows(selected_rows, limit=max(1, limit))
        if not selected_rows:
            return {"ok": False, "error": "No eligible staged candidates are waiting for batch creation."}
        normalized_batch_id = str(batch_id or "").strip() or _generate_batch_id(
            [str(row.get("target_id") or "").strip().upper() for row in selected_rows]
        )
        for row in selected_rows:
            payload = dict(row.get("payload") or _json_load(row.get("payload_json"), {}))
            next_stage_state = "staged_batch_member"
            conn.execute(
                """
                UPDATE miru_publication_stage
                SET stage_state = ?,
                    batch_id = ?,
                    note = ?,
                    decision_source = ?,
                    updated_at = ?
                WHERE item_key = ?
                """,
                (
                    next_stage_state,
                    normalized_batch_id,
                    str(note or "").strip() or "Added to publication-prep batch.",
                    "batch.create_publication_batch",
                    _utc_now_timestamp(),
                    str(row.get("item_key") or "").strip(),
                ),
            )
            _upsert_batch_member_row(
                conn,
                batch_id=normalized_batch_id,
                summary=payload,
                stage_state=next_stage_state,
                note=note,
                decision_source="batch.create_publication_batch",
            )
        _refresh_batch_record(
            conn,
            batch_id=normalized_batch_id,
            rationale_note=str(note or "").strip(),
        )
        conn.commit()
        _refresh_publication_stage_metadata(
            conn,
            project_db_path=project_path,
            source="batch.create_publication_batch",
            last_change={"batch_id": normalized_batch_id, "member_count": len(selected_rows)},
        )
        _refresh_publication_batch_metadata(
            conn,
            project_db_path=project_path,
            source="batch.create_publication_batch",
            last_change={"batch_id": normalized_batch_id, "member_count": len(selected_rows)},
        )
    batch_summary = build_publication_batch_summary(batch_id=normalized_batch_id, project_db_path=project_path, limit=12)
    return {"ok": True, "batch_id": normalized_batch_id, "batch": batch_summary}


def add_candidate_to_batch(
    *,
    card_code: str,
    batch_id: str,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
    note: str = "",
    runtime_uncertain: bool = False,
) -> dict[str, Any]:
    normalized_batch_id = str(batch_id or "").strip()
    if not normalized_batch_id:
        return {"ok": False, "error": "A batch_id is required."}
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    with closing(connect_catalog_db(project_path)) as conn:
        existing_batch = _load_batch_row(conn, normalized_batch_id)
        if not existing_batch:
            return {"ok": False, "error": f"Publication batch not found: {normalized_batch_id}"}
    staged = stage_publication_candidate(
        card_code=card_code,
        project_db_path=project_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
        note=note or "Added to publication-prep batch.",
        decision_source="batch.add_candidate",
        batch_id=normalized_batch_id,
        runtime_uncertain=runtime_uncertain,
        persist_blocked=True,
    )
    if not staged.get("ok"):
        return staged
    batch_summary = build_publication_batch_summary(batch_id=normalized_batch_id, project_db_path=project_path, limit=12)
    return {"ok": True, "batch_id": normalized_batch_id, "batch": batch_summary, "stage": staged.get("stage")}


def remove_candidate_from_batch(
    *,
    card_code: str,
    batch_id: str,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    note: str = "",
    decision_source: str = "",
) -> dict[str, Any]:
    normalized_code = _normalize_code(card_code)
    normalized_batch_id = str(batch_id or "").strip()
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    with closing(connect_catalog_db(project_path)) as conn:
        row = conn.execute(
            """
            SELECT item_key
            FROM miru_publication_batch_items
            WHERE batch_id = ?
              AND target_id = ?
              AND status = 'active'
            LIMIT 1
            """,
            (normalized_batch_id, normalized_code),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": f"{normalized_code} is not an active member of batch {normalized_batch_id}."}
        now = _utc_now_timestamp()
        conn.execute(
            """
            UPDATE miru_publication_batch_items
            SET status = 'removed',
                note = ?,
                updated_at = ?,
                removed_at = ?
            WHERE batch_id = ?
              AND item_key = ?
            """,
            (
                str(note or "").strip() or "Removed from publication-prep batch.",
                now,
                now,
                normalized_batch_id,
                str(row["item_key"] or "").strip(),
            ),
        )
        conn.execute(
            """
            UPDATE miru_publication_stage
            SET stage_state = 'staged_candidate',
                batch_id = '',
                note = ?,
                decision_source = ?,
                updated_at = ?
            WHERE item_key = ?
            """,
            (
                str(note or "").strip() or "Returned to standalone staged candidate.",
                str(decision_source or "").strip() or "batch.remove_candidate",
                now,
                str(row["item_key"] or "").strip(),
            ),
        )
        _refresh_batch_record(
            conn,
            batch_id=normalized_batch_id,
            rationale_note=str(note or "").strip(),
        )
        conn.commit()
        _refresh_publication_stage_metadata(
            conn,
            project_db_path=project_path,
            source=decision_source or "batch.remove_candidate",
            last_change={"batch_id": normalized_batch_id, "target_id": normalized_code, "action": "removed_from_batch"},
        )
        _refresh_publication_batch_metadata(
            conn,
            project_db_path=project_path,
            source=decision_source or "batch.remove_candidate",
            last_change={"batch_id": normalized_batch_id, "target_id": normalized_code, "action": "removed_from_batch"},
        )
    batch_summary = build_publication_batch_summary(batch_id=normalized_batch_id, project_db_path=project_path, limit=12)
    return {"ok": True, "batch_id": normalized_batch_id, "target_id": normalized_code, "batch": batch_summary}


def refresh_publication_batch(
    *,
    batch_id: str,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    note: str = "",
    force_status: str = "",
) -> dict[str, Any]:
    normalized_batch_id = str(batch_id or "").strip()
    if not normalized_batch_id:
        return {"ok": False, "error": "A batch_id is required."}
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    with closing(connect_catalog_db(project_path)) as conn:
        batch_row = _load_batch_row(conn, normalized_batch_id)
        if not batch_row:
            return {"ok": False, "error": f"Publication batch not found: {normalized_batch_id}"}
        member_rows = _load_batch_members(conn, normalized_batch_id, include_removed=False)
        derived_status, guardrail_label, rationale, member_counts = _derive_batch_status(member_rows)
        next_status = _normalize_batch_status(force_status) or derived_status
        now = _utc_now_timestamp()
        member_payloads: list[dict[str, Any]] = []
        for item in member_rows:
            payload = dict(item.get("payload") or {})
            if (
                not payload
                or not str(payload.get("candidate_score_band") or "").strip()
                or not str(payload.get("publish_status") or "").strip()
            ):
                payload = build_publication_candidate_summary(
                    card_code=str(item.get("target_id") or "").strip(),
                    project_db_path=project_path,
                )
            payload.setdefault("target_id", str(item.get("target_id") or "").strip())
            payload.setdefault("candidate_score", _safe_float(item.get("candidate_score")))
            payload.setdefault("candidate_score_band", str(item.get("candidate_score_band") or "").strip())
            payload.setdefault("candidate_profile", str(item.get("candidate_profile") or "").strip())
            payload.setdefault("approval_state", _normalize_approval_state(item.get("approval_state")))
            payload.setdefault("strong_sections", list(payload.get("strong_sections") or []))
            member_payloads.append(payload)
        curation = _curate_batch_quality(member_payloads, existing_status=next_status)
        publish_gate = _evaluate_batch_publication_gate_from_summary(
            {
                "batch_id": normalized_batch_id,
                "batch_status": next_status,
                "batch_profile": str(curation.get("batch_profile") or "").strip(),
                "batch_quality_score": _safe_float(curation.get("batch_quality_score")),
                "counts": member_counts,
                "members": member_payloads,
                "unresolved_risks": list(curation.get("unresolved_risks") or []),
                "recommended_next_step": str(curation.get("recommended_next_step") or "").strip(),
                "split_suggestion": dict(curation.get("split_suggestion") or {"needed": False, "groups": []}),
            }
        )
        conn.execute(
            """
            UPDATE miru_publication_batches
            SET batch_status = ?,
                rationale = ?,
                summary_text = ?,
                guardrail_label = ?,
                batch_quality_score = ?,
                batch_quality_band = ?,
                batch_profile = ?,
                member_count = ?,
                ready_member_count = ?,
                review_member_count = ?,
                blocked_member_count = ?,
                deferred_member_count = ?,
                batch_publish_status = ?,
                batch_publish_reasons_json = ?,
                batch_publish_risks_json = ?,
                batch_publish_payload_json = ?,
                batch_publish_updated_at = ?,
                strongest_reasons_json = ?,
                unresolved_risks_json = ?,
                recommended_next_step = ?,
                payload_json = ?,
                updated_at = ?,
                archived_at = CASE WHEN ? = 'archived' THEN ? ELSE archived_at END
            WHERE batch_id = ?
            """,
            (
                next_status,
                str(note or "").strip() or rationale,
                str(note or "").strip() or rationale,
                guardrail_label,
                _safe_float(curation.get("batch_quality_score")),
                str(curation.get("batch_quality_band") or "").strip(),
                str(curation.get("batch_profile") or "").strip(),
                len(member_rows),
                member_counts["ready_member_count"],
                member_counts["review_member_count"],
                member_counts["blocked_member_count"],
                member_counts["deferred_member_count"],
                _normalize_batch_publish_status(publish_gate.get("batch_publish_status")),
                _json_dump(publish_gate.get("batch_publish_reasons") or []),
                _json_dump(publish_gate.get("batch_publish_risks") or []),
                _json_dump(
                    {
                        "batch_id": normalized_batch_id,
                        "batch_status": next_status,
                        "batch_quality_score": _safe_float(curation.get("batch_quality_score")),
                        "batch_quality_band": str(curation.get("batch_quality_band") or "").strip(),
                        "batch_profile": str(curation.get("batch_profile") or "").strip(),
                        "batch_publish_status": _normalize_batch_publish_status(publish_gate.get("batch_publish_status")),
                        "batch_publish_gate_decision": str(publish_gate.get("batch_publish_gate_decision") or "").strip(),
                        "member_ids": [str(item.get("target_id") or "").strip() for item in member_rows],
                    }
                ),
                now,
                _json_dump(curation.get("strongest_reasons") or []),
                _json_dump(curation.get("unresolved_risks") or []),
                str(curation.get("recommended_next_step") or "").strip(),
                _json_dump(
                    {
                        "member_ids": [str(item.get("target_id") or "").strip() for item in member_rows],
                        "counts": member_counts,
                        "derived_status": derived_status,
                        "batch_quality_score": _safe_float(curation.get("batch_quality_score")),
                        "batch_quality_band": str(curation.get("batch_quality_band") or "").strip(),
                        "batch_profile": str(curation.get("batch_profile") or "").strip(),
                        "strongest_reasons": list(curation.get("strongest_reasons") or []),
                        "unresolved_risks": list(curation.get("unresolved_risks") or []),
                        "recommended_next_step": str(curation.get("recommended_next_step") or "").strip(),
                        "split_suggestion": dict(curation.get("split_suggestion") or {"needed": False, "groups": []}),
                    }
                ),
                now,
                next_status,
                now,
                normalized_batch_id,
            ),
        )
        if next_status == "archived":
            conn.execute(
                """
                UPDATE miru_publication_batch_items
                SET status = 'removed',
                    note = ?,
                    updated_at = ?,
                    removed_at = CASE WHEN removed_at = '' THEN ? ELSE removed_at END
                WHERE batch_id = ?
                  AND status = 'active'
                """,
                (
                    str(note or "").strip() or "Batch archived.",
                    now,
                    now,
                    normalized_batch_id,
                ),
            )
            conn.execute(
                """
                UPDATE miru_publication_stage
                SET stage_state = 'staged_candidate',
                    batch_id = '',
                    note = ?,
                    decision_source = ?,
                    updated_at = ?
                WHERE batch_id = ?
                """,
                (
                    str(note or "").strip() or "Batch archived; returned to staged candidates.",
                    "batch.archive",
                    now,
                    normalized_batch_id,
                ),
            )
        conn.commit()
        _refresh_publication_stage_metadata(
            conn,
            project_db_path=project_path,
            source="batch.refresh",
            last_change={"batch_id": normalized_batch_id, "status": next_status},
        )
        _refresh_publication_batch_metadata(
            conn,
            project_db_path=project_path,
            source="batch.refresh",
            last_change={"batch_id": normalized_batch_id, "status": next_status},
        )
        _refresh_publication_release_metadata(
            conn,
            project_db_path=project_path,
            source="batch.refresh",
            last_change={"batch_id": normalized_batch_id, "status": next_status},
        )
    batch_summary = build_publication_batch_summary(batch_id=normalized_batch_id, project_db_path=project_path, limit=12)
    return {"ok": True, "batch_id": normalized_batch_id, "batch": batch_summary}

def update_review_queue_item(
    *,
    target_id: str = "",
    item_key: str = "",
    status: str = "",
    approval_state: str = "",
    note: str = "",
    decision_source: str = "",
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
) -> dict[str, Any]:
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    desired_status = str(status or "").strip().lower()
    if desired_status and desired_status not in {"pending", "resolved", "deferred"}:
        desired_status = "resolved"
    desired_approval_state = _normalize_approval_state(approval_state)
    lookup_key = str(item_key or "").strip() or _review_queue_item_key(target_id)
    now = _utc_now_timestamp()
    with closing(connect_catalog_db(project_path)) as conn:
        row = conn.execute(
            """
            SELECT item_key, target_id, status, approval_state, approval_note, readiness_state, guardrail_label, queue_type
            FROM miru_review_queue
            WHERE item_key = ?
            LIMIT 1
            """,
            (lookup_key,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": f"Review queue item not found: {lookup_key}"}
        next_status = desired_status or str(row["status"] or "").strip().lower() or "pending"
        next_approval_state = desired_approval_state or _normalize_approval_state(row["approval_state"])
        if desired_approval_state == "deferred" and not desired_status:
            next_status = "deferred"
        elif desired_approval_state in {"approved_for_candidate", "rejected", "superseded"} and not desired_status:
            next_status = "resolved"
        elif desired_status == "deferred" and not desired_approval_state:
            next_approval_state = "deferred"
        promotion_state, promotion_rationale = _derive_promotion_fields(
            readiness_state=str(row["readiness_state"] or "").strip(),
            approval_state=next_approval_state,
            queue_status=next_status,
            guardrail_label=str(row["guardrail_label"] or "").strip(),
        )
        resolved_at = now if next_status in {"resolved", "deferred"} else ""
        conn.execute(
            """
            UPDATE miru_review_queue
            SET status = ?,
                approval_state = ?,
                promotion_state = ?,
                approval_note = ?,
                decision_source = ?,
                resolution_note = ?,
                updated_at = ?,
                resolved_at = ?,
                approval_updated_at = ?
            WHERE item_key = ?
            """,
            (
                next_status,
                next_approval_state,
                promotion_state,
                str(note or "").strip() or str(row["approval_note"] or "").strip() or str(row["approval_state"] or "").strip(),
                str(decision_source or "").strip() or "review_queue_update",
                str(note or "").strip() or f"Marked {next_status or 'updated'}.",
                now,
                resolved_at,
                now if next_approval_state else "",
                lookup_key,
            ),
        )
        queue_type = str(row["queue_type"] or "").strip()
        if queue_type == "publication_readiness":
            conn.execute(
                """
                UPDATE card_intelligence
                SET approval_state = ?,
                    promotion_state = ?,
                    promotion_rationale = ?,
                    promotion_updated_at = ?
                WHERE card_id IN (
                    SELECT id
                    FROM cards
                    WHERE canonical_code = ?
                )
                """,
                (
                    next_approval_state,
                    promotion_state,
                    promotion_rationale,
                    now,
                    str(row["target_id"] or "").strip().upper(),
                ),
            )
        if queue_type == "image_variant_sp":
            card_code = str(row["target_id"] or "").strip().upper()
            if next_approval_state == "approved_for_candidate" and next_status == "resolved":
                conn.execute(
                    """
                    UPDATE cards
                    SET variant_category = 'premium_rarity',
                        variant_subtype = 'sp'
                    WHERE canonical_code = ?
                    """,
                    (card_code,),
                )
                conn.execute(
                    """
                    UPDATE image_variant_analysis
                    SET operator_decision = ?,
                        review_status = 'OPERATOR_APPROVED'
                    WHERE canonical_code = ?
                    """,
                    ("approved_sp_variant", card_code),
                )
            elif next_approval_state == "rejected" and next_status == "resolved":
                conn.execute(
                    """
                    UPDATE image_variant_analysis
                    SET review_status = 'reviewed_not_sp'
                    WHERE canonical_code = ?
                      AND COALESCE(sp_marker_detected, 0) = 1
                    """,
                    (card_code,),
                )
        if next_approval_state in {"rejected", "deferred", "superseded"}:
            stage_row = _load_stage_row(conn, str(row["target_id"] or "").strip().upper())
            if stage_row:
                prior_batch_id = str(stage_row.get("batch_id") or "").strip()
                conn.execute(
                    """
                    UPDATE miru_publication_stage
                    SET stage_state = 'removed_from_stage',
                        batch_id = '',
                        note = ?,
                        decision_source = ?,
                        updated_at = ?,
                        removed_at = ?
                    WHERE item_key = ?
                    """,
                    (
                        str(note or "").strip() or f"Removed from stage because approval became {next_approval_state}.",
                        str(decision_source or "").strip() or "review_queue_update",
                        now,
                        now,
                        str(stage_row.get("item_key") or "").strip(),
                    ),
                )
                if prior_batch_id:
                    conn.execute(
                        """
                        UPDATE miru_publication_batch_items
                        SET status = 'removed',
                            note = ?,
                            updated_at = ?,
                            removed_at = ?
                        WHERE batch_id = ?
                          AND item_key = ?
                          AND status = 'active'
                        """,
                        (
                            str(note or "").strip() or f"Removed from batch because approval became {next_approval_state}.",
                            now,
                            now,
                            prior_batch_id,
                            str(stage_row.get("item_key") or "").strip(),
                        ),
                    )
        summary = load_review_queue_summary(project_db_path=project_path, limit=8)
        _store_metadata(
            conn,
            sync_key=REVIEW_QUEUE_METADATA_KEY,
            payload={
                "updated_at": now,
                "last_change": {
                    "item_key": lookup_key,
                    "target_id": str(row["target_id"] or "").strip(),
                    "status": next_status,
                    "approval_state": next_approval_state,
                    "note": str(note or "").strip(),
                },
                "counts": summary.get("counts") or {},
                "approval_counts": summary.get("approval_counts") or {},
            },
        )
        _refresh_publication_stage_metadata(
            conn,
            project_db_path=project_path,
            source=str(decision_source or "").strip() or "review_queue_update",
            last_change={
                "item_key": lookup_key,
                "target_id": str(row["target_id"] or "").strip(),
                "approval_state": next_approval_state,
            },
        )
        _refresh_publication_batch_metadata(
            conn,
            project_db_path=project_path,
            source=str(decision_source or "").strip() or "review_queue_update",
            last_change={
                "item_key": lookup_key,
                "target_id": str(row["target_id"] or "").strip(),
                "approval_state": next_approval_state,
            },
        )
    return {
        "ok": True,
        "item_key": lookup_key,
        "status": next_status,
        "approval_state": next_approval_state,
        "promotion_state": promotion_state,
    }


def resolve_review_queue_item(
    *,
    target_id: str = "",
    item_key: str = "",
    status: str = "resolved",
    note: str = "",
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
) -> dict[str, Any]:
    return update_review_queue_item(
        target_id=target_id,
        item_key=item_key,
        status=status,
        approval_state="deferred" if str(status or "").strip().lower() == "deferred" else "",
        note=note,
        decision_source="review_queue_resolution",
        project_db_path=project_db_path,
    )

def _log_action_history(
    conn: sqlite3.Connection,
    *,
    action_id: str,
    action_title: str,
    category: str,
    target_type: str,
    target_id: str,
    execution_status: str,
    eligibility: str,
    guardrail_label: str,
    risk_level: str,
    confidence_score: float,
    rationale: str,
    sync_reason: str = "",
    priority_score: float | None = None,
    priority_bucket: str = "",
    publication_readiness: str = "",
    worker_hint: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO miru_action_history (
            action_id,
            action_title,
            category,
            target_type,
            target_id,
            execution_status,
            eligibility,
            guardrail_label,
            risk_level,
            confidence_score,
            rationale,
            sync_reason,
            priority_score,
            priority_bucket,
            publication_readiness,
            worker_hint,
            payload_json,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            action_id,
            action_title,
            category,
            target_type,
            target_id,
            execution_status,
            eligibility,
            guardrail_label,
            risk_level,
            round(_safe_float(confidence_score), 3),
            rationale,
            sync_reason,
            priority_score,
            priority_bucket,
            publication_readiness,
            worker_hint,
            _json_dump(payload or {}),
            _utc_now_timestamp(),
        ),
    )


def load_action_history(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    limit: int = 12,
) -> list[dict[str, Any]]:
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    with closing(connect_catalog_db(project_path)) as conn:
        rows = conn.execute(
            """
            SELECT
                action_id,
                action_title,
                category,
                target_type,
                target_id,
                execution_status,
                eligibility,
                guardrail_label,
                risk_level,
                confidence_score,
                rationale,
                sync_reason,
                priority_score,
                priority_bucket,
                publication_readiness,
                worker_hint,
                payload_json,
                created_at
            FROM miru_action_history
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["payload"] = _json_load(item.get("payload_json"), {})
        out.append(item)
    return out


def _build_worker_handoff(
    *,
    state: dict[str, Any],
    action_id: str,
    target_card_code: str = "",
    target_batch_id: str = "",
) -> dict[str, Any]:
    sync_status = state.get("sync_status") or {}
    top_pending = list(sync_status.get("top_pending_candidates") or [])
    pending_text = ""
    if top_pending:
        first = top_pending[0] or {}
        pending_text = str(first.get("summary") or first.get("reason") or "").strip()
    if action_id == "sync.incremental_priority_batch":
        prompt = (
            "Run a bounded Miru dossier/projection sync against the highest-priority pending candidates, "
            "verify the resulting stored outputs, and report whether any card moved into a stronger publish-ready state."
        )
    elif action_id == "publish.evaluate_candidate":
        prompt = (
            f"Run the final backend publish gate for {target_card_code or 'the selected card'}, "
            "explain whether it is publish-ready, review-bound, deferred, or blocked, and call out the decisive evidence and risks."
        )
    elif action_id == "publish.generate_payload":
        prompt = (
            f"Build the backend-only publication payload contract for {target_card_code or 'the selected card'}, "
            "including the strict insight summary, approval state, promotion state, confidence indicators, and review flags."
        )
    elif action_id == "publish.validate_before_release":
        prompt = (
            f"Validate the final publish gate for {target_batch_id or target_card_code or 'the selected target'}, "
            "and explain exactly why Miru would allow, review-bind, defer, or refuse a future storefront bridge step."
        )
    elif action_id == "review.score_candidate":
        prompt = (
            f"Score the publication value of {target_card_code or 'the selected card'} from dossier-backed signals only, "
            "explain its strongest reasons and limiting risks, and confirm whether it belongs in a safe, review-heavy, weak, or blocked lane."
        )
    elif action_id == "batch.recommend_split" and target_batch_id:
        prompt = (
            f"Review backend publication batch {target_batch_id}, compare its member scores and risk profiles, "
            "and propose the cleanest split or curated grouping without mutating storefront behavior."
        )
    elif action_id.startswith("batch.") and target_batch_id:
        prompt = (
            f"Review backend publication batch {target_batch_id}, confirm its staged members and guardrails, "
            "and report whether it is review-ready, mixed, or blocked without mutating storefront behavior."
        )
    elif action_id == "review.publish_candidate_summary":
        prompt = (
            f"Review the canonical dossier and strict insight for {target_card_code or 'the selected card'}, "
            "explain the publish-readiness decision, and call out any guardrail that still blocks storefront use."
        )
    elif action_id == "revalidate.plan_revalidation_batch":
        top_gap = ((state.get("revalidation_summary") or {}).get("top_gap_clusters") or [{}])[0] or {}
        prompt = (
            "Review the highest-value dossier gap cluster, explain why those cards should be revalidated next, "
            "and recommend the cheapest bounded refresh lane that stays backend-only."
        )
        if str(top_gap.get("gap_class") or "").strip():
            prompt += f" Current top gap cluster: {str(top_gap.get('gap_class') or '').strip()}."
    elif action_id == "route.worker_handoff_for_gap_cluster":
        top_gap = ((state.get("revalidation_summary") or {}).get("top_gap_clusters") or [{}])[0] or {}
        prompt = (
            "Prepare a worker handoff for the highest-value Miru coverage gap cluster, including what evidence is missing, "
            "which stored sources already exist, and which bounded backend-only refresh should happen next."
        )
        if str(top_gap.get("gap_class") or "").strip():
            prompt += f" Focus on the {str(top_gap.get('gap_class') or '').strip()} cluster first."
    else:
        prompt = (
            "Review the current governed action state, confirm the live runtime and projection signals, "
            "and recommend the next safe backend step without mutating Project Miru storefront files."
        )
    if pending_text:
        prompt += f" Current top pending signal: {pending_text}"
    return {
        "preferred_worker": "Codex",
        "route_task": prompt,
    }


def _build_governance_state(
    *,
    dev_payload: dict[str, Any] | None,
    project_db_path: Path,
    runtime_dossier_db_path: Path,
) -> dict[str, Any]:
    payload = dict(dev_payload or {})
    control_layer = payload.get("control_layer") or {}
    system_health = list(control_layer.get("system_health") or [])
    system_by_key = {
        str(item.get("key") or "").strip(): dict(item)
        for item in system_health
        if str(item.get("key") or "").strip()
    }
    runtime_reliability = dict((control_layer.get("runtime_reliability") or {}).get("project_miru") or {})
    learning_engine = payload.get("learning_engine") or {}
    sync_status = load_miru_card_insight_status(
        project_db_path=project_db_path,
        runtime_dossier_db_path=runtime_dossier_db_path,
    )
    with closing(connect_catalog_db(project_db_path)) as conn:
        readiness_summary = _summarize_publication_readiness_counts(conn)
        publication_targets = _publication_target_candidates(conn)
        publication_refresh = _load_metadata(conn, sync_key=PUBLICATION_METADATA_KEY)
        review_queue_refresh = _load_metadata(conn, sync_key=REVIEW_QUEUE_METADATA_KEY)
        stage_refresh = _load_metadata(conn, sync_key=STAGING_METADATA_KEY)
        batch_refresh = _load_metadata(conn, sync_key=BATCH_METADATA_KEY)
        revalidation_refresh = _load_metadata(conn, sync_key=REVALIDATION_METADATA_KEY)
    review_queue_summary = load_review_queue_summary(project_db_path=project_db_path, limit=8)
    stage_summary = load_publication_stage_summary(project_db_path=project_db_path, limit=8)
    batch_summary = load_publication_batch_summary(project_db_path=project_db_path, limit=6)
    publication_release_summary = load_publication_release_summary(project_db_path=project_db_path, limit=6)
    revalidation_summary = load_revalidation_summary(project_db_path=project_db_path, limit=8)
    governed = payload.get("governed_autopilot") or {}
    return {
        "generated_at": _utc_now_timestamp(),
        "dev_runtime_online": str((system_by_key.get("miru_ai") or {}).get("status") or "").strip().upper() != "FAILED",
        "project_runtime_healthy": bool(runtime_reliability.get("healthy")),
        "project_runtime_uncertain": bool(runtime_reliability.get("uncertain")),
        "project_runtime_observation": str(runtime_reliability.get("state") or "").strip(),
        "worker_status": str(((learning_engine.get("worker_status") or {}).get("label")) or "").strip().lower(),
        "worker_stale": str(((learning_engine.get("worker_status") or {}).get("label")) or "").strip().lower() in {"stale", "offline", "stalled"},
        "sync_status": sync_status,
        "sync_backlog_present": _safe_int(sync_status.get("remaining_candidate_count")) > 0,
        "catalog_db_writable": bool((sync_status.get("db_health") or {}).get("project_catalog_writable")),
        "runtime_dossier_readable": bool((sync_status.get("db_health") or {}).get("runtime_dossier_readable")),
        "governed_review_count": len(list(governed.get("open_review_items") or [])),
        "pending_approvals_count": _safe_int((review_queue_summary.get("approval_counts") or {}).get("pending_review")),
        "approved_waiting_to_stage": _safe_int(stage_summary.get("approved_waiting_to_stage")),
        "readiness_summary": readiness_summary,
        "publication_refresh": publication_refresh,
        "publication_targets": publication_targets,
        "readiness_candidates_present": _safe_int(readiness_summary.get("remaining_candidate_count")) > 0,
        "review_queue_summary": review_queue_summary,
        "review_queue_refresh": review_queue_refresh,
        "stage_summary": stage_summary,
        "stage_refresh": stage_refresh,
        "staged_candidates_present": _safe_int(stage_summary.get("active_count")) > 0,
        "batch_summary": batch_summary,
        "batch_refresh": batch_refresh,
        "publication_release_summary": publication_release_summary,
        "publication_release_refresh": publication_release_summary.get("latest") or {},
        "revalidation_summary": revalidation_summary,
        "revalidation_refresh": revalidation_refresh,
        "revalidation_candidates_present": _safe_int(revalidation_summary.get("revalidation_candidate_count")) > 0,
        "judgment": dict((control_layer.get("judgment") or {})),
    }


def _decision_from_preconditions(preconditions: list[str], state: dict[str, Any]) -> tuple[str, list[str]]:
    failed: list[str] = []
    for item in preconditions:
        if item == "dev_runtime_online" and not state.get("dev_runtime_online"):
            failed.append(item)
        elif item == "catalog_db_writable" and not state.get("catalog_db_writable"):
            failed.append(item)
        elif item == "runtime_dossier_readable" and not state.get("runtime_dossier_readable"):
            failed.append(item)
        elif item == "sync_backlog_present" and not state.get("sync_backlog_present"):
            failed.append(item)
        elif item == "readiness_candidates_present" and not state.get("readiness_candidates_present"):
            failed.append(item)
        elif item == "revalidation_candidates_present" and not state.get("revalidation_candidates_present"):
            failed.append(item)
        elif item == "staged_candidates_present" and not state.get("staged_candidates_present"):
            failed.append(item)
        elif item == "batch_target_present" and not state.get("batch_target_present"):
            failed.append(item)
    return ("allowed_now" if not failed else "not_applicable", failed)


def _evaluate_action(
    *,
    action: dict[str, Any],
    state: dict[str, Any],
    target_card_code: str = "",
    target_batch_id: str = "",
    target_readiness: dict[str, Any] | None = None,
    target_batch_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    action_id = str(action.get("action_id") or "").strip()
    target_type = "batch" if target_batch_id else ("card" if target_card_code else "system")
    decision, failed_preconditions = _decision_from_preconditions(
        list(action.get("required_preconditions") or []),
        state,
    )
    rationale = "Current runtime and dossier state allow this action."
    guardrail_label = {
        "read_only": "Read-only",
        "safe_action": "Safe action",
        "review_required": "Review required",
        "blocked": "Blocked",
    }.get(str(action.get("allowed_mode") or "").strip().lower(), "Read-only")
    confidence = 0.86
    risk = str(action.get("risk_level") or "low").strip().lower()

    if action_id == "publish.storefront_mutation":
        # Testing environment only — 18080 is the test bed, not the live storefront.
        # publish_ready rows are operator-approved. Blocked permanently once 8080 promotion is enabled.
        decision = "allowed_with_review"
        failed_preconditions = []
        rationale = (
            "Testing environment execution: publish_ready insights will be populated into "
            "card_catalog.db for 18080 test site consumption. Operator must explicitly trigger. "
            "This path is blocked when 8080 promotion is re-enabled."
        )
        confidence = 0.92
        risk = "medium"
        guardrail_label = "Review required"
    elif "card_target_present" in action.get("required_preconditions", ()) and not target_card_code:
        decision = "not_applicable"
        failed_preconditions.append("card_target_present")
        rationale = "This action needs a canonical card target."
        confidence = 0.9
    elif "batch_target_present" in action.get("required_preconditions", ()) and not target_batch_id:
        decision = "not_applicable"
        failed_preconditions.append("batch_target_present")
        rationale = "This action needs a publication batch target."
        confidence = 0.9
    elif "dossier_evidence_present" in action.get("required_preconditions", ()) and target_card_code and not target_readiness:
        decision = "blocked"
        failed_preconditions.append("dossier_evidence_present")
        rationale = "The target card does not currently have enough dossier evidence for this action."
        confidence = 0.92
        risk = "medium"
    elif action_id == "stage.stage_candidate" and target_readiness:
        summary = build_publication_candidate_summary(card_code=target_card_code, readiness=target_readiness)
        if not bool(summary.get("stageable")):
            decision = "blocked"
            rationale = str(summary.get("stage_rationale") or "This candidate does not currently satisfy staging guardrails.")
            confidence = max(0.82, _safe_float(summary.get("confidence")))
            risk = "medium"
            guardrail_label = "Blocked"
        elif state.get("project_runtime_uncertain") or str(summary.get("stage_guardrail_label") or "").strip().lower() == "review required":
            decision = "allowed_with_review"
            rationale = str(summary.get("stage_rationale") or "The staging step is safe, but it should remain visibly review-bound.")
            confidence = max(0.78, _safe_float(summary.get("confidence")))
            risk = "medium"
            guardrail_label = "Review required"
        else:
            decision = "allowed_now"
            rationale = str(summary.get("stage_rationale") or "The candidate can be staged safely in the backend.")
            confidence = max(0.82, _safe_float(summary.get("confidence")))
            risk = "low"
    elif action_id == "stage.unstage_candidate":
        summary = build_publication_candidate_summary(card_code=target_card_code)
        if str(summary.get("stage_state") or "").strip() not in {"staged_candidate", "staged_batch_member"}:
            decision = "not_applicable"
            rationale = "This candidate is not currently staged."
            confidence = 0.93
        else:
            decision = "allowed_now"
            rationale = "Removing a staged candidate is a safe backend-only governance action."
            confidence = 0.95
            risk = "low"
    elif action_id == "review.publish_candidate_summary" and target_readiness:
        readiness_state = str(target_readiness.get("readiness_state") or "").strip()
        if readiness_state == "blocked_by_guardrail":
            decision = "blocked"
            rationale = str(target_readiness.get("rationale") or "The target card is blocked by guardrail.")
            risk = "high"
            guardrail_label = "Blocked"
        else:
            decision = "allowed_now"
            if readiness_state == "not_ready":
                rationale = "Miru can safely generate an observational publication summary, but the card is still not ready for review or publication."
                risk = "low"
            else:
                rationale = "Miru can safely generate a publish-candidate summary from dossier-backed data; future site-facing use still follows the stored readiness guardrail."
                risk = "low"
            guardrail_label = "Safe action"
        confidence = max(0.7, _safe_float(target_readiness.get("confidence")))
    elif action_id == "review.score_candidate" and target_readiness:
        summary = build_publication_candidate_summary(card_code=target_card_code, readiness=target_readiness)
        decision = "allowed_now"
        rationale = (
            f"Candidate score {summary.get('candidate_score', 0):.1f} ({summary.get('candidate_score_band') or 'unscored'}) "
            f"classifies this card as {str(summary.get('candidate_profile') or 'unclassified').replace('_', ' ')}."
        )
        confidence = max(0.78, _safe_float(summary.get("confidence")))
        risk = "low" if str(summary.get("candidate_profile") or "") == "high_value_safe" else "medium"
        guardrail_label = "Read-only"
    elif action_id in {"publish.evaluate_candidate", "publish.generate_payload", "publish.validate_before_release"} and target_readiness:
        summary = build_publication_candidate_summary(card_code=target_card_code, readiness=target_readiness)
        publish_status = str(summary.get("publish_status") or "").strip()
        decision = "allowed_now"
        if action_id == "publish.generate_payload":
            rationale = "Miru can safely build the backend-only publication payload contract without mutating any storefront surface."
        elif action_id == "publish.validate_before_release":
            rationale = (
                f"Final publish gate currently resolves to {publish_status or 'unclassified'} "
                f"with decision {str(summary.get('publish_gate_decision') or 'refuse')}."
            )
        else:
            rationale = (
                f"Final publish eligibility resolves to {publish_status or 'unclassified'} "
                f"for this dossier-backed candidate."
            )
        confidence = max(0.8, _safe_float(summary.get("confidence")))
        risk = "low" if publish_status == "publish_ready" else ("medium" if publish_status in {"publish_requires_review", "publish_deferred"} else "high")
        guardrail_label = "Read-only"
    elif action_id == "review.mark_review_required":
        decision = "allowed_now" if target_card_code else "not_applicable"
        rationale = "Recording a review requirement is a safe governance-side action and does not mutate storefront behavior."
        confidence = 0.94
    elif action_id == "review.approve_candidate":
        readiness_state = str((target_readiness or {}).get("readiness_state") or "").strip()
        if readiness_state in {"blocked_by_guardrail", "not_ready"}:
            decision = "blocked"
            rationale = "Miru must not mark this candidate approved while readiness still blocks promotion."
            confidence = 0.95
            risk = "medium"
            guardrail_label = "Blocked"
        elif state.get("project_runtime_uncertain"):
            decision = "allowed_with_review"
            rationale = "Approval can be recorded, but runtime reliability is currently mixed, so the approval should stay visibly review-bound."
            confidence = 0.82
            risk = "medium"
            guardrail_label = "Review required"
        else:
            decision = "allowed_now"
            rationale = "Recording an approval decision is safe and prepares future promotion without mutating storefront behavior."
            confidence = 0.9
            risk = "low"
            guardrail_label = "Safe action"
    elif action_id == "review.reject_candidate":
        decision = "allowed_now" if target_card_code else "not_applicable"
        rationale = "Recording a rejection is a safe governance decision and does not touch storefront behavior."
        confidence = 0.94
        risk = "low"
    elif action_id == "review.defer_candidate":
        decision = "allowed_now" if target_card_code else "not_applicable"
        rationale = "Deferring a candidate is a safe queue lifecycle update while Miru waits for stronger evidence or later review."
        confidence = 0.93
        risk = "low"
    elif action_id == "batch.create_publication_batch":
        if not state.get("staged_candidates_present"):
            decision = "not_applicable"
            rationale = "No staged candidates are waiting to be grouped into a publication batch."
        else:
            decision = "allowed_now"
            rationale = "Creating a backend publication-prep batch is safe because it only groups already staged candidates."
            confidence = 0.9
            risk = "low"
    elif action_id == "batch.add_candidate":
        if not target_batch_id:
            decision = "not_applicable"
            rationale = "Adding a candidate to a batch needs a batch target."
            confidence = 0.9
        elif not target_readiness:
            decision = "blocked"
            rationale = "The target card does not currently have dossier evidence for batch addition."
            confidence = 0.9
            risk = "medium"
            guardrail_label = "Blocked"
        else:
            summary = build_publication_candidate_summary(card_code=target_card_code, readiness=target_readiness)
            if not bool(summary.get("stageable")):
                decision = "blocked"
                rationale = str(summary.get("stage_rationale") or "This candidate cannot be staged into a batch right now.")
                confidence = max(0.8, _safe_float(summary.get("confidence")))
                risk = "medium"
                guardrail_label = "Blocked"
            elif state.get("project_runtime_uncertain") or str(summary.get("stage_guardrail_label") or "").strip().lower() == "review required":
                decision = "allowed_with_review"
                rationale = "The candidate can join a batch, but the current guardrail should stay visibly review-bound."
                confidence = max(0.78, _safe_float(summary.get("confidence")))
                risk = "medium"
                guardrail_label = "Review required"
            else:
                decision = "allowed_now"
                rationale = "The approved candidate can safely join the selected backend batch."
                confidence = max(0.82, _safe_float(summary.get("confidence")))
                risk = "low"
    elif action_id == "batch.remove_candidate":
        if not target_batch_id:
            decision = "not_applicable"
            rationale = "Removing a candidate from a batch needs a batch target."
            confidence = 0.9
        else:
            decision = "allowed_now"
            rationale = "Removing a candidate from a backend batch is a safe governance-side action."
            confidence = 0.95
            risk = "low"
    elif action_id in {"batch.generate_summary", "batch.recommend_split", "batch.mark_review_ready", "batch.archive"}:
        if not target_batch_summary:
            decision = "not_applicable"
            rationale = "This action needs a known publication batch."
            confidence = 0.9
        elif action_id == "batch.generate_summary":
            decision = "allowed_now"
            rationale = "Generating a batch summary is read-only and keeps batch governance inspectable."
            confidence = 0.96
            risk = "low"
        elif action_id == "batch.recommend_split":
            decision = "allowed_now"
            batch_profile = str((target_batch_summary or {}).get("batch_profile") or "").strip()
            if batch_profile in {"mixed", "review_heavy", "blocked"}:
                rationale = "Miru can safely recommend a cleaner split because this batch currently mixes different risk or approval lanes."
            else:
                rationale = "Miru can safely confirm whether this batch already looks cohesive enough to keep grouped."
            confidence = 0.95
            risk = "low"
        elif action_id == "batch.archive":
            if str((target_batch_summary or {}).get("batch_status") or "").strip() == "archived":
                decision = "not_applicable"
                rationale = "This publication batch is already archived."
                confidence = 0.95
            else:
                decision = "allowed_now"
                rationale = "Archiving a backend-only batch is safe and preserves history without touching storefront content."
                confidence = 0.93
                risk = "low"
        else:
            decision = "allowed_now"
            rationale = "Refreshing the stored batch review state is a safe backend-only governance action."
            confidence = 0.92
            risk = "low"
    elif action_id == "publish.evaluate_batch":
        if not target_batch_summary:
            decision = "not_applicable"
            rationale = "This action needs a known publication batch."
            confidence = 0.9
        else:
            decision = "allowed_now"
            rationale = (
                f"Final batch publish gate currently resolves to "
                f"{str((target_batch_summary or {}).get('batch_publish_status') or 'unclassified')}."
            )
            confidence = 0.94
            risk = "low" if str((target_batch_summary or {}).get("batch_publish_status") or "") == "publish_ready_batch" else "medium"
            guardrail_label = "Read-only"
    elif action_id == "publish.validate_before_release" and target_batch_summary:
        decision = "allowed_now"
        rationale = (
            f"Final batch publish gate currently resolves to "
            f"{str((target_batch_summary or {}).get('batch_publish_status') or 'unclassified')}."
        )
        confidence = 0.94
        risk = "low" if str((target_batch_summary or {}).get("batch_publish_status") or "") == "publish_ready_batch" else "medium"
        guardrail_label = "Read-only"
    elif action_id == "verify.card_projection" and target_readiness:
        decision = "allowed_now"
        rationale = "This action only rebuilds dossier-backed backend views for one card and updates readiness metadata."
        confidence = max(0.72, _safe_float(target_readiness.get("confidence")))
    elif action_id == "project.refresh_publication_readiness":
        if not state.get("readiness_candidates_present"):
            decision = "not_applicable"
            rationale = "No publication-readiness rows are waiting for refresh."
        else:
            decision = "allowed_now"
            rationale = "Miru can refresh bounded publication-readiness metadata from existing projected dossier rows."
        confidence = 0.9
    elif action_id == "revalidate.plan_revalidation_batch":
        if not state.get("revalidation_candidates_present"):
            decision = "not_applicable"
            rationale = "No bounded revalidation candidates are currently queued."
            confidence = 0.92
        else:
            decision = "allowed_now"
            rationale = "Miru can classify the highest-value dossier gaps and refresh bounded revalidation metadata without mutating storefront behavior."
            confidence = 0.9
            risk = "low"
    elif action_id in {
        "revalidate.refresh_partial_candidate",
        "revalidate.verify_stale_candidate",
        "revalidate.refresh_rules_sensitive_candidate",
        "revalidate.refresh_usage_meta_candidate",
    } and target_readiness:
        revalidation_summary = build_revalidation_candidate_summary(card_code=target_card_code)
        revalidation_status = str(revalidation_summary.get("revalidation_status") or "").strip()
        if revalidation_status == "hold":
            decision = "allowed_with_review"
            rationale = "Miru can refresh the stored governance metadata, but the current evidence is weak enough that the result should stay visibly review-bound."
            confidence = max(0.76, _safe_float(revalidation_summary.get("confidence")))
            risk = "medium"
            guardrail_label = "Review required"
        elif revalidation_status == "stable_enough":
            decision = "not_applicable"
            rationale = "This card does not currently need a revalidation refresh."
            confidence = max(0.82, _safe_float(revalidation_summary.get("confidence")))
            risk = "low"
        else:
            decision = "allowed_now"
            rationale = str(revalidation_summary.get("revalidation_reason") or "Miru can safely refresh this dossier-backed candidate through the existing revalidation path.")
            confidence = max(0.78, _safe_float(revalidation_summary.get("confidence")))
            risk = "low" if revalidation_status == "recheck_later" else "medium"
    elif action_id == "sync.incremental_priority_batch":
        if not state.get("sync_backlog_present"):
            decision = "not_applicable"
            rationale = "The bounded priority sync backlog is currently empty."
        elif state.get("worker_stale"):
            decision = "allowed_with_review"
            rationale = "The sync path itself is safe, but the stale worker signal should stay visible while you run it."
            guardrail_label = "Review required"
        else:
            decision = "allowed_now"
            rationale = "The existing incremental sync has pending candidates and a healthy enough backend state for a bounded run."
        confidence = 0.88
        risk = "medium"
    elif action_id.startswith("observe."):
        decision = "allowed_now" if state.get("dev_runtime_online") else "blocked"
        rationale = "Refreshing observation state is safe because it only reuses existing runtime and sync probes."
        confidence = 0.97
        risk = "low"
    elif action_id in {"route.worker_handoff_prompt", "route.worker_handoff_for_gap_cluster"}:
        decision = "allowed_now"
        rationale = "Generating a worker handoff is read-only and helps keep governed next steps inspectable."
        confidence = 0.95
        risk = "low"

    worker_handoff = _build_worker_handoff(
        state=state,
        action_id=action_id,
        target_card_code=target_card_code,
        target_batch_id=target_batch_id,
    )
    return {
        "action_id": action_id,
        "title": str(action.get("title") or "").strip(),
        "description": str(action.get("description") or "").strip(),
        "category": str(action.get("category") or "").strip(),
        "allowed_mode": str(action.get("allowed_mode") or "").strip(),
        "risk_level": risk,
        "target_scope": str(action.get("target_scope") or "").strip(),
        "decision": decision,
        "guardrail_label": guardrail_label,
        "confidence": round(confidence, 3),
        "rationale": rationale,
        "required_preconditions": list(action.get("required_preconditions") or []),
        "failed_preconditions": failed_preconditions,
        "target_type": target_type,
        "target_id": target_batch_id or target_card_code,
        "worker_handoff": worker_handoff,
        "target_readiness": dict(target_readiness or {}),
        "target_batch_summary": dict(target_batch_summary or {}),
    }


def build_action_governance_snapshot(
    *,
    dev_payload: dict[str, Any] | None = None,
    target_card_code: str = "",
    target_batch_id: str = "",
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    runtime_dossier_db_path: str | Path = DEFAULT_RUNTIME_DOSSIER_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
    persist: bool = False,
) -> dict[str, Any]:
    project_path = Path(project_db_path)
    runtime_path = Path(runtime_dossier_db_path)
    ensure_catalog_sync_schema(project_path)
    normalized_target = _normalize_code(target_card_code)
    normalized_batch = str(target_batch_id or "").strip()
    state = _build_governance_state(
        dev_payload=dev_payload,
        project_db_path=project_path,
        runtime_dossier_db_path=runtime_path,
    )
    target_readiness = None
    target_candidate_summary = None
    target_batch_summary = None
    if normalized_target:
        target_readiness = evaluate_publication_readiness(
            card_code=normalized_target,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )
        target_candidate_summary = build_publication_candidate_summary(
            card_code=normalized_target,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
            readiness=target_readiness,
        )
    if normalized_batch:
        target_batch_summary = build_publication_batch_summary(
            batch_id=normalized_batch,
            project_db_path=project_path,
            limit=12,
        )
    state = {
        **state,
        "batch_target_present": bool(normalized_batch and target_batch_summary and target_batch_summary.get("batch_id")),
    }
    evaluations = [
        _evaluate_action(
            action=entry,
            state=state,
            target_card_code=normalized_target,
            target_batch_id=normalized_batch,
            target_readiness=target_readiness,
            target_batch_summary=target_batch_summary,
        )
        for entry in build_action_registry()
    ]
    grouped = {
        "allowed_now": [item for item in evaluations if item["decision"] == "allowed_now"],
        "allowed_with_review": [item for item in evaluations if item["decision"] == "allowed_with_review"],
        "blocked": [item for item in evaluations if item["decision"] == "blocked"],
        "not_applicable": [item for item in evaluations if item["decision"] == "not_applicable"],
    }
    priority_order = [
        "sync.incremental_priority_batch",
        "revalidate.plan_revalidation_batch",
        "project.refresh_publication_readiness",
        "review.score_candidate",
        "publish.evaluate_candidate",
        "publish.validate_before_release",
        "stage.stage_candidate",
        "batch.create_publication_batch",
        "review.publish_candidate_summary",
        "publish.generate_payload",
        "publish.evaluate_batch",
        "batch.recommend_split",
        "batch.generate_summary",
        "verify.card_projection",
        "observe.runtime_probe_refresh",
        "observe.dev_status_refresh",
        "route.worker_handoff_for_gap_cluster",
        "route.worker_handoff_prompt",
    ]

    def pick_action(pool: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not pool:
            return None
        by_id = {str(item.get("action_id") or ""): item for item in pool}
        for action_key in priority_order:
            if action_key in by_id:
                return by_id[action_key]
        return pool[0]

    recommended_next = (
        pick_action(grouped["allowed_now"])
        or pick_action(grouped["allowed_with_review"])
        or pick_action(grouped["blocked"])
        or (evaluations[0] if evaluations else {})
    )

    snapshot = {
        "generated_at": _utc_now_timestamp(),
        "target_card_code": normalized_target,
        "target_batch_id": normalized_batch,
        "state": {
            "dev_runtime_online": state.get("dev_runtime_online"),
            "project_runtime_healthy": state.get("project_runtime_healthy"),
            "project_runtime_uncertain": state.get("project_runtime_uncertain"),
            "project_runtime_observation": state.get("project_runtime_observation"),
            "worker_status": state.get("worker_status"),
            "worker_stale": state.get("worker_stale"),
            "sync_backlog_present": state.get("sync_backlog_present"),
            "readiness_candidates_present": state.get("readiness_candidates_present"),
            "governed_review_count": state.get("governed_review_count"),
            "pending_approvals_count": state.get("pending_approvals_count"),
            "approved_waiting_to_stage": state.get("approved_waiting_to_stage"),
            "staged_candidates_present": state.get("staged_candidates_present"),
            "revalidation_candidates_present": state.get("revalidation_candidates_present"),
            "batch_target_present": state.get("batch_summary") is not None,
        },
        "sync_status": {
            "remaining_candidate_count": (state.get("sync_status") or {}).get("remaining_candidate_count"),
            "pending_reason_counts": (state.get("sync_status") or {}).get("pending_reason_counts"),
            "pending_priority_bucket_counts": (state.get("sync_status") or {}).get("pending_priority_bucket_counts"),
            "top_pending_candidates": (state.get("sync_status") or {}).get("top_pending_candidates"),
            "last_prioritized_sync_reason": (state.get("sync_status") or {}).get("last_prioritized_sync_reason"),
        },
        "publication_readiness": {
            "counts": (state.get("readiness_summary") or {}).get("counts") or {},
            "remaining_candidate_count": (state.get("readiness_summary") or {}).get("remaining_candidate_count") or 0,
            "top_targets": state.get("publication_targets") or [],
            "target_evaluation": target_readiness or {},
            "target_candidate_summary": target_candidate_summary or {},
            "last_refresh": state.get("publication_refresh") or {},
        },
        "review_queue": {
            "counts": ((state.get("review_queue_summary") or {}).get("counts") or {}),
            "approval_counts": ((state.get("review_queue_summary") or {}).get("approval_counts") or {}),
            "pending_count": ((state.get("review_queue_summary") or {}).get("pending_count") or 0),
            "pending_items": ((state.get("review_queue_summary") or {}).get("pending_items") or []),
            "approved_candidates": ((state.get("review_queue_summary") or {}).get("approved_candidates") or []),
            "last_refresh": state.get("review_queue_refresh") or {},
        },
        "publication_stage": {
            "counts": ((state.get("stage_summary") or {}).get("counts") or {}),
            "active_count": ((state.get("stage_summary") or {}).get("active_count") or 0),
            "approved_waiting_to_stage": ((state.get("stage_summary") or {}).get("approved_waiting_to_stage") or 0),
            "active_items": ((state.get("stage_summary") or {}).get("active_items") or []),
            "top_scored_candidates": ((state.get("stage_summary") or {}).get("top_scored_candidates") or []),
            "candidate_profile_counts": ((state.get("stage_summary") or {}).get("candidate_profile_counts") or {}),
            "candidate_publish_counts": ((state.get("stage_summary") or {}).get("candidate_publish_counts") or {}),
            "last_refresh": state.get("stage_refresh") or {},
        },
        "publication_batches": {
            "counts": ((state.get("batch_summary") or {}).get("counts") or {}),
            "active_count": ((state.get("batch_summary") or {}).get("active_count") or 0),
            "batches": ((state.get("batch_summary") or {}).get("batches") or []),
            "batch_quality_counts": ((state.get("batch_summary") or {}).get("batch_quality_counts") or {}),
            "batch_profile_counts": ((state.get("batch_summary") or {}).get("batch_profile_counts") or {}),
            "batch_publish_counts": ((state.get("batch_summary") or {}).get("batch_publish_counts") or {}),
            "top_batches": ((state.get("batch_summary") or {}).get("top_batches") or []),
            "mixed_batches": ((state.get("batch_summary") or {}).get("mixed_batches") or []),
            "publish_ready_batches": ((state.get("batch_summary") or {}).get("publish_ready_batches") or []),
            "target_batch": target_batch_summary or {},
            "last_refresh": state.get("batch_refresh") or {},
        },
        "publication_execution": {
            "counts": ((state.get("publication_release_summary") or {}).get("counts") or {}),
            "publish_ready_candidates": ((state.get("publication_release_summary") or {}).get("publish_ready_candidates") or []),
            "review_required_candidates": ((state.get("publication_release_summary") or {}).get("review_required_candidates") or []),
            "blocked_candidates": ((state.get("publication_release_summary") or {}).get("blocked_candidates") or []),
            "deferred_candidates": ((state.get("publication_release_summary") or {}).get("deferred_candidates") or []),
            "publish_ready_batches": ((state.get("publication_release_summary") or {}).get("publish_ready_batches") or []),
            "mixed_batches": ((state.get("publication_release_summary") or {}).get("mixed_batches") or []),
            "batch_publish_counts": ((state.get("publication_release_summary") or {}).get("batch_publish_counts") or {}),
            "last_refresh": state.get("publication_release_refresh") or {},
        },
        "coverage_revalidation": {
            "gap_counts": ((state.get("revalidation_summary") or {}).get("gap_counts") or {}),
            "revalidation_counts": ((state.get("revalidation_summary") or {}).get("revalidation_counts") or {}),
            "value_band_counts": ((state.get("revalidation_summary") or {}).get("value_band_counts") or {}),
            "revalidation_candidate_count": ((state.get("revalidation_summary") or {}).get("revalidation_candidate_count") or 0),
            "high_value_pending_count": ((state.get("revalidation_summary") or {}).get("high_value_pending_count") or 0),
            "stale_dossier_count": ((state.get("revalidation_summary") or {}).get("stale_dossier_count") or 0),
            "top_gap_clusters": ((state.get("revalidation_summary") or {}).get("top_gap_clusters") or []),
            "top_candidates": ((state.get("revalidation_summary") or {}).get("top_candidates") or []),
            "recently_promoted": ((state.get("revalidation_summary") or {}).get("recently_promoted") or []),
            "last_refresh": state.get("revalidation_refresh") or {},
        },
        "actions": {
            "all": evaluations,
            "allowed_now": grouped["allowed_now"],
            "allowed_with_review": grouped["allowed_with_review"],
            "blocked": grouped["blocked"],
            "not_applicable": grouped["not_applicable"],
        },
        "recommended_next": recommended_next,
        "refused_actions": grouped["blocked"],
        "recent_history": load_action_history(project_db_path=project_path, limit=8),
    }
    if persist:
        with closing(connect_catalog_db(project_path)) as conn:
            _store_metadata(conn, sync_key=ACTION_METADATA_KEY, payload=snapshot)
            _refresh_publication_release_metadata(
                conn,
                project_db_path=project_path,
                source="build_action_governance_snapshot",
                last_change={"target_card_code": normalized_target, "target_batch_id": normalized_batch},
            )
    return snapshot


def execute_governed_action(
    *,
    action_id: str,
    dev_payload: dict[str, Any] | None = None,
    target_card_code: str = "",
    batch_id: str = "",
    member_card_codes: list[str] | None = None,
    limit: int | None = None,
    note: str = "",
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    runtime_dossier_db_path: str | Path = DEFAULT_RUNTIME_DOSSIER_DB_PATH,
    canonical_dossier_db_path: str | Path = DEFAULT_CANONICAL_DOSSIER_DB_PATH,
    rules_db_path: str | Path = DEFAULT_RULES_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
) -> dict[str, Any]:
    snapshot = build_action_governance_snapshot(
        dev_payload=dev_payload,
        target_card_code=target_card_code,
        target_batch_id=batch_id,
        project_db_path=project_db_path,
        runtime_dossier_db_path=runtime_dossier_db_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
        persist=True,
    )
    action = next(
        (item for item in list(snapshot.get("actions", {}).get("all", [])) if str(item.get("action_id") or "") == str(action_id or "")),
        {},
    )
    if not action:
        return {"ok": False, "error": f"Unknown action_id: {action_id}"}

    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)
    bounded_limit = max(1, min(_safe_int(limit or DEFAULT_INCREMENTAL_SYNC_LIMIT), MAX_EXECUTION_LIMIT))
    target_readiness = dict(action.get("target_readiness") or {})
    execution_status = "skipped"
    result: dict[str, Any] = {"executed": False}

    if action.get("decision") == "blocked":
        execution_status = "blocked"
        if str(action.get("action_id") or "") == "stage.stage_candidate" and str(target_card_code or "").strip():
            blocked_stage = stage_publication_candidate(
                card_code=target_card_code,
                project_db_path=project_path,
                canonical_dossier_db_path=canonical_dossier_db_path,
                rules_db_path=rules_db_path,
                deck_intel_db_path=deck_intel_db_path,
                prices_path=prices_path,
                note=note,
                decision_source="stage.stage_candidate",
                batch_id="",
                runtime_uncertain=bool((snapshot.get("state") or {}).get("project_runtime_uncertain")),
                persist_blocked=True,
            )
            result = {"executed": False, "reason": action.get("rationale"), "stage_attempt": blocked_stage}
        else:
            result = {"executed": False, "reason": action.get("rationale")}
    elif action.get("decision") == "not_applicable":
        execution_status = "not_applicable"
        result = {"executed": False, "reason": action.get("rationale")}
    elif str(action.get("action_id") or "") == "observe.runtime_probe_refresh":
        execution_status = "executed"
        result = {"executed": True, "runtime": snapshot.get("state"), "sync_status": snapshot.get("sync_status")}
    elif str(action.get("action_id") or "") == "observe.dev_status_refresh":
        execution_status = "executed"
        result = {"executed": True, "snapshot_summary": snapshot.get("recommended_next")}
    elif str(action.get("action_id") or "") == "sync.incremental_priority_batch":
        execution_status = "executed"
        result = run_worktree_card_insight_sync(limit=bounded_limit, rebuild=False)
    elif str(action.get("action_id") or "") == "project.refresh_publication_readiness":
        execution_status = "executed"
        result = refresh_publication_readiness_batch(
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
            limit=bounded_limit,
        )
    elif str(action.get("action_id") or "") == "revalidate.plan_revalidation_batch":
        execution_status = "executed"
        result = refresh_revalidation_planning_batch(
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
            limit=bounded_limit,
        )
    elif str(action.get("action_id") or "") in {
        "revalidate.refresh_partial_candidate",
        "revalidate.verify_stale_candidate",
        "revalidate.refresh_rules_sensitive_candidate",
        "revalidate.refresh_usage_meta_candidate",
    }:
        execution_status = "executed"
        result = refresh_revalidation_candidate(
            card_code=target_card_code,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
            decision_source=str(action.get("action_id") or "").strip(),
        )
    elif str(action.get("action_id") or "") == "verify.card_projection":
        execution_status = "executed"
        summary = build_publication_candidate_summary(
            card_code=target_card_code,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )
        with closing(connect_catalog_db(project_path)) as conn:
            _upsert_publication_readiness(conn, summary)
            queue_update = _upsert_review_queue_entry(conn, summary=summary, forced=False, decision_source="verify.card_projection")
            conn.commit()
            _store_metadata(
                conn,
                sync_key=REVIEW_QUEUE_METADATA_KEY,
                payload={
                    "updated_at": _utc_now_timestamp(),
                    "source": "verify.card_projection",
                    "counts": load_review_queue_summary(project_db_path=project_path, limit=8).get("counts") or {},
                    "approval_counts": load_review_queue_summary(project_db_path=project_path, limit=8).get("approval_counts") or {},
                    "queue_updates": [queue_update],
                },
            )
        result = {"executed": True, "verification": summary, "queue_update": queue_update}
    elif str(action.get("action_id") or "") == "review.publish_candidate_summary":
        execution_status = "executed" if action.get("decision") == "allowed_with_review" else "skipped"
        summary = build_publication_candidate_summary(
            card_code=target_card_code,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )
        with closing(connect_catalog_db(project_path)) as conn:
            _upsert_publication_readiness(conn, summary)
            queue_update = _upsert_review_queue_entry(conn, summary=summary, forced=False, decision_source="review.publish_candidate_summary")
            conn.commit()
            _store_metadata(
                conn,
                sync_key=REVIEW_QUEUE_METADATA_KEY,
                payload={
                    "updated_at": _utc_now_timestamp(),
                    "source": "review.publish_candidate_summary",
                    "counts": load_review_queue_summary(project_db_path=project_path, limit=8).get("counts") or {},
                    "approval_counts": load_review_queue_summary(project_db_path=project_path, limit=8).get("approval_counts") or {},
                    "queue_updates": [queue_update],
                },
            )
        result = {
            "executed": execution_status == "executed",
            "publish_candidate": summary,
            "queue_update": queue_update,
        }
    elif str(action.get("action_id") or "") == "review.score_candidate":
        execution_status = "executed"
        summary = build_publication_candidate_summary(
            card_code=target_card_code,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )
        with closing(connect_catalog_db(project_path)) as conn:
            _upsert_publication_readiness(conn, summary)
            conn.commit()
        result = {
            "executed": True,
            "candidate_score": {
                "card_code": str(summary.get("card_code") or "").strip(),
                "candidate_score": _safe_float(summary.get("candidate_score")),
                "candidate_score_band": str(summary.get("candidate_score_band") or "").strip(),
                "candidate_profile": str(summary.get("candidate_profile") or "").strip(),
                "candidate_score_reasons": list(summary.get("candidate_score_reasons") or []),
                "candidate_risk_factors": list(summary.get("candidate_risk_factors") or []),
                "summary_text": str(summary.get("summary_text") or "").strip(),
            },
        }
    elif str(action.get("action_id") or "") == "publish.evaluate_candidate":
        execution_status = "executed"
        summary = build_publication_candidate_summary(
            card_code=target_card_code,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )
        with closing(connect_catalog_db(project_path)) as conn:
            _upsert_publication_readiness(conn, summary)
            conn.commit()
        result = {
            "executed": True,
            "publish_evaluation": evaluate_publication_candidate_gate(
                card_code=target_card_code,
                project_db_path=project_path,
                canonical_dossier_db_path=canonical_dossier_db_path,
                rules_db_path=rules_db_path,
                deck_intel_db_path=deck_intel_db_path,
                prices_path=prices_path,
            ),
        }
    elif str(action.get("action_id") or "") == "publish.generate_payload":
        execution_status = "executed"
        summary = build_publication_candidate_summary(
            card_code=target_card_code,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )
        with closing(connect_catalog_db(project_path)) as conn:
            _upsert_publication_readiness(conn, summary)
            conn.commit()
        result = {
            "executed": True,
            "publish_status": str(summary.get("publish_status") or "").strip(),
            "publication_payload": dict(summary.get("publication_payload") or {}),
        }
    elif str(action.get("action_id") or "") == "publish.validate_before_release":
        execution_status = "executed"
        if batch_id:
            batch_summary = build_publication_batch_summary(
                batch_id=batch_id,
                project_db_path=project_path,
                limit=max(6, bounded_limit),
            )
            result = {
                "executed": True,
                "target_type": "batch",
                "validation": evaluate_publication_batch_gate(
                    batch_id=batch_id,
                    project_db_path=project_path,
                    limit=max(6, bounded_limit),
                ),
                "batch": batch_summary,
            }
        else:
            summary = build_publication_candidate_summary(
                card_code=target_card_code,
                project_db_path=project_path,
                canonical_dossier_db_path=canonical_dossier_db_path,
                rules_db_path=rules_db_path,
                deck_intel_db_path=deck_intel_db_path,
                prices_path=prices_path,
            )
            with closing(connect_catalog_db(project_path)) as conn:
                _upsert_publication_readiness(conn, summary)
                conn.commit()
            result = {
                "executed": True,
                "target_type": "card",
                "validation": evaluate_publication_candidate_gate(
                    card_code=target_card_code,
                    project_db_path=project_path,
                    canonical_dossier_db_path=canonical_dossier_db_path,
                    rules_db_path=rules_db_path,
                    deck_intel_db_path=deck_intel_db_path,
                    prices_path=prices_path,
                ),
            }
    elif str(action.get("action_id") or "") == "review.mark_review_required":
        execution_status = "executed"
        summary = build_publication_candidate_summary(
            card_code=target_card_code,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )
        with closing(connect_catalog_db(project_path)) as conn:
            queue_update = _upsert_review_queue_entry(conn, summary=summary, forced=True, note=note, decision_source="review.mark_review_required")
            _upsert_publication_readiness(conn, {**summary, "approval_state": "pending_review"})
            conn.commit()
            _store_metadata(
                conn,
                sync_key=REVIEW_QUEUE_METADATA_KEY,
                payload={
                    "updated_at": _utc_now_timestamp(),
                    "source": "review.mark_review_required",
                    "counts": load_review_queue_summary(project_db_path=project_path, limit=8).get("counts") or {},
                    "approval_counts": load_review_queue_summary(project_db_path=project_path, limit=8).get("approval_counts") or {},
                    "queue_updates": [queue_update],
                },
            )
        result = {
            "executed": True,
            "review_required": True,
            "card_code": _normalize_code(target_card_code),
            "note": str(note or "").strip(),
            "queue_update": queue_update,
        }
    elif str(action.get("action_id") or "") == "review.approve_candidate":
        execution_status = "executed"
        summary = build_publication_candidate_summary(
            card_code=target_card_code,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )
        with closing(connect_catalog_db(project_path)) as conn:
            _upsert_review_queue_entry(conn, summary=summary, forced=True, decision_source="review.approve_candidate")
        queue_update = update_review_queue_item(
            target_id=target_card_code,
            approval_state="approved_for_candidate",
            status="resolved",
            note=str(note or "").strip() or "Approved for future publish candidate preparation.",
            decision_source="review.approve_candidate",
            project_db_path=project_path,
        )
        approved_summary = {
            **summary,
            "queue_status": "resolved",
            "approval_state": "approved_for_candidate",
            "approval_note": str(note or "").strip() or "Approved for future publish candidate preparation.",
            "decision_source": "review.approve_candidate",
            "promotion_state": "review_approved_candidate",
            "promotion_rationale": "A human review approval is stored, so this item is prepared for a future publish candidate step.",
        }
        with closing(connect_catalog_db(project_path)) as conn:
            _upsert_publication_readiness(conn, approved_summary)
        result = {"executed": True, "approval_state": "approved_for_candidate", "publish_candidate": approved_summary, "queue_update": queue_update}
    elif str(action.get("action_id") or "") == "review.reject_candidate":
        execution_status = "executed"
        summary = build_publication_candidate_summary(
            card_code=target_card_code,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )
        with closing(connect_catalog_db(project_path)) as conn:
            _upsert_review_queue_entry(conn, summary=summary, forced=True, decision_source="review.reject_candidate")
        queue_update = update_review_queue_item(
            target_id=target_card_code,
            approval_state="rejected",
            status="resolved",
            note=str(note or "").strip() or "Rejected for future publish promotion.",
            decision_source="review.reject_candidate",
            project_db_path=project_path,
        )
        rejected_summary = {
            **summary,
            "queue_status": "resolved",
            "approval_state": "rejected",
            "approval_note": str(note or "").strip() or "Rejected for future publish promotion.",
            "decision_source": "review.reject_candidate",
            "promotion_state": "blocked_from_promotion",
            "promotion_rationale": "A recorded review decision currently blocks promotion for this item.",
        }
        with closing(connect_catalog_db(project_path)) as conn:
            _upsert_publication_readiness(conn, rejected_summary)
        result = {"executed": True, "approval_state": "rejected", "publish_candidate": rejected_summary, "queue_update": queue_update}
    elif str(action.get("action_id") or "") == "review.defer_candidate":
        execution_status = "executed"
        summary = build_publication_candidate_summary(
            card_code=target_card_code,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
        )
        with closing(connect_catalog_db(project_path)) as conn:
            _upsert_review_queue_entry(conn, summary=summary, forced=True, decision_source="review.defer_candidate")
        queue_update = update_review_queue_item(
            target_id=target_card_code,
            approval_state="deferred",
            status="deferred",
            note=str(note or "").strip() or "Deferred pending stronger evidence or later review.",
            decision_source="review.defer_candidate",
            project_db_path=project_path,
        )
        deferred_summary = {
            **summary,
            "queue_status": "deferred",
            "approval_state": "deferred",
            "approval_note": str(note or "").strip() or "Deferred pending stronger evidence or later review.",
            "decision_source": "review.defer_candidate",
            "promotion_state": "deferred",
            "promotion_rationale": "Promotion is currently deferred pending stronger evidence or a later review decision.",
        }
        with closing(connect_catalog_db(project_path)) as conn:
            _upsert_publication_readiness(conn, deferred_summary)
        result = {"executed": True, "approval_state": "deferred", "publish_candidate": deferred_summary, "queue_update": queue_update}
    elif str(action.get("action_id") or "") == "stage.stage_candidate":
        execution_status = "executed"
        result = stage_publication_candidate(
            card_code=target_card_code,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
            note=note,
            decision_source="stage.stage_candidate",
            batch_id="",
            runtime_uncertain=bool((snapshot.get("state") or {}).get("project_runtime_uncertain")),
            persist_blocked=True,
        )
    elif str(action.get("action_id") or "") == "stage.unstage_candidate":
        execution_status = "executed"
        result = remove_staged_candidate(
            card_code=target_card_code,
            project_db_path=project_path,
            note=note,
            decision_source="stage.unstage_candidate",
        )
    elif str(action.get("action_id") or "") == "batch.create_publication_batch":
        execution_status = "executed"
        result = create_publication_batch(
            project_db_path=project_path,
            member_card_codes=member_card_codes,
            batch_id=batch_id,
            note=note,
            limit=bounded_limit,
        )
    elif str(action.get("action_id") or "") == "batch.add_candidate":
        execution_status = "executed"
        result = add_candidate_to_batch(
            card_code=target_card_code,
            batch_id=batch_id,
            project_db_path=project_path,
            canonical_dossier_db_path=canonical_dossier_db_path,
            rules_db_path=rules_db_path,
            deck_intel_db_path=deck_intel_db_path,
            prices_path=prices_path,
            note=note,
            runtime_uncertain=bool((snapshot.get("state") or {}).get("project_runtime_uncertain")),
        )
    elif str(action.get("action_id") or "") == "batch.remove_candidate":
        execution_status = "executed"
        result = remove_candidate_from_batch(
            card_code=target_card_code,
            batch_id=batch_id,
            project_db_path=project_path,
            note=note,
            decision_source="batch.remove_candidate",
        )
    elif str(action.get("action_id") or "") == "batch.generate_summary":
        execution_status = "executed"
        result = {
            "executed": True,
            "batch": build_publication_batch_summary(
                batch_id=batch_id,
                project_db_path=project_path,
                limit=max(6, bounded_limit),
            ),
        }
    elif str(action.get("action_id") or "") == "batch.recommend_split":
        execution_status = "executed"
        batch_summary = build_publication_batch_summary(
            batch_id=batch_id,
            project_db_path=project_path,
            limit=max(6, bounded_limit),
        )
        result = {
            "executed": True,
            "batch_id": batch_id,
            "batch_profile": str(batch_summary.get("batch_profile") or "").strip(),
            "batch_quality_score": _safe_float(batch_summary.get("batch_quality_score")),
            "batch_quality_band": str(batch_summary.get("batch_quality_band") or "").strip(),
            "recommended_next_step": str(batch_summary.get("recommended_next_step") or "").strip(),
            "split_suggestion": dict(batch_summary.get("split_suggestion") or {"needed": False, "groups": []}),
            "batch": batch_summary,
        }
    elif str(action.get("action_id") or "") == "publish.evaluate_batch":
        execution_status = "executed"
        batch_summary = build_publication_batch_summary(
            batch_id=batch_id,
            project_db_path=project_path,
            limit=max(6, bounded_limit),
        )
        result = {
            "executed": True,
            "batch_id": batch_id,
            "batch_publish_status": str(batch_summary.get("batch_publish_status") or "").strip(),
            "batch_publish_gate_decision": str(batch_summary.get("batch_publish_gate_decision") or "").strip(),
            "batch_publish_reasons": list(batch_summary.get("batch_publish_reasons") or []),
            "batch_publish_risks": list(batch_summary.get("batch_publish_risks") or []),
            "batch": batch_summary,
        }
    elif str(action.get("action_id") or "") == "batch.mark_review_ready":
        execution_status = "executed"
        result = refresh_publication_batch(
            batch_id=batch_id,
            project_db_path=project_path,
            note=note,
        )
    elif str(action.get("action_id") or "") == "batch.archive":
        execution_status = "executed"
        result = refresh_publication_batch(
            batch_id=batch_id,
            project_db_path=project_path,
            note=note or "Archived backend publication-prep batch.",
            force_status="archived",
        )
    elif str(action.get("action_id") or "") in {"route.worker_handoff_prompt", "route.worker_handoff_for_gap_cluster"}:
        execution_status = "executed"
        result = {
            "executed": True,
            "worker_handoff": action.get("worker_handoff"),
            "gap_summary": (snapshot.get("coverage_revalidation") or {}).get("top_gap_clusters") or [],
        }

    refreshed_snapshot = build_action_governance_snapshot(
        dev_payload=dev_payload,
        target_card_code=target_card_code,
        target_batch_id=batch_id,
        project_db_path=project_path,
        runtime_dossier_db_path=runtime_dossier_db_path,
        canonical_dossier_db_path=canonical_dossier_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        prices_path=prices_path,
        persist=True,
    )
    with closing(connect_catalog_db(project_path)) as conn:
        _log_action_history(
            conn,
            action_id=str(action.get("action_id") or ""),
            action_title=str(action.get("title") or ""),
            category=str(action.get("category") or ""),
            target_type=str(action.get("target_type") or ""),
            target_id=str(action.get("target_id") or ""),
            execution_status=execution_status,
            eligibility=str(action.get("decision") or ""),
            guardrail_label=str(action.get("guardrail_label") or ""),
            risk_level=str(action.get("risk_level") or ""),
            confidence_score=_safe_float(action.get("confidence")),
            rationale=str(action.get("rationale") or ""),
            sync_reason=str(((refreshed_snapshot.get("sync_status") or {}).get("last_prioritized_sync_reason")) or ""),
            priority_score=_safe_float((target_readiness or {}).get("confidence")),
            priority_bucket=str((((refreshed_snapshot.get("sync_status") or {}).get("top_pending_candidates") or [{}])[0] or {}).get("priority_bucket") or ""),
            publication_readiness=str((target_readiness or {}).get("readiness_state") or ""),
            worker_hint=str(((action.get("worker_handoff") or {}).get("route_task")) or ""),
            payload={
                "note": str(note or "").strip(),
                "result": result,
            },
        )
    return {
        "ok": True,
        "action": action,
        "result": result,
        "snapshot": refreshed_snapshot,
    }
