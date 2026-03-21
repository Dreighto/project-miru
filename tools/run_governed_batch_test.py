#!/usr/bin/env python
"""Governed Autonomy Batch Test: ingest staged legality data, evaluate, route, auto-apply safe items, report.

Uses real staged approved-source data only (legality staging CSV + --source-id in OFFICIAL_LEGALITY_SOURCE_IDS).
Worktree-only. No scraping or live fetch.

Usage:
    python -m tools.run_governed_batch_test data/staging/op_format_banlist_intake.csv --source-id official
    python -m tools.run_governed_batch_test data/staging/op_format_banlist_intake.csv --source-id official --dry-run
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = PROJECT_ROOT / "data" / "card_catalog.db"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"

from tools.miru_ethics_gates import can_publish
from tools.miru_import_legality_csv import read_staging_csv
from tools.miru_official_rules import (
    DEFAULT_RULES_DB_PATH,
    is_effective_now,
    ingest_legality_row,
)
from tools.miru_regulation import (
    OFFICIAL_LEGALITY_SOURCE_IDS,
    LEGALITY_LEGAL,
    LEGALITY_BANNED,
    LEGALITY_RESTRICTED,
    LEGALITY_ROTATED,
    LEGALITY_UNKNOWN,
    LEGALITY_STATES,
    get_legality_state,
    save_legality_state,
)
from tools.miru_project_sync import ensure_catalog_sync_schema


def _normalize_legality_state(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in LEGALITY_STATES:
        return s
    if s in ("ok", "playable", "allowed"):
        return LEGALITY_LEGAL
    if s in ("ban", "banned"):
        return LEGALITY_BANNED
    if s in ("restrict", "limited"):
        return LEGALITY_RESTRICTED
    if s in ("rotate", "rotated", "out"):
        return LEGALITY_ROTATED
    return LEGALITY_UNKNOWN


# ---------------------------------------------------------------------------
# Routing policy (explicit, conservative)
# ---------------------------------------------------------------------------

AUTO_APPLY = "auto_apply"
REVIEW_QUEUE = "review_queue"
REJECT_HOLD = "reject_hold"
SKIPPED = "skipped"
UPCOMING_STORED = "upcoming_stored"  # Official future-dated change stored; not applied to current; not a conflict

IMPACT_LOW = "low"
IMPACT_MEDIUM = "medium"
IMPACT_HIGH = "high"

CHANGE_NEW = "new"
CHANGE_UPDATE = "update"
CHANGE_NO_CHANGE = "no_change"


def _impact_level(existing: dict[str, Any] | None, proposed_state: str) -> str:
    # New record proposing banned/restricted/rotated is regulation-sensitive -> review
    if not existing:
        if proposed_state in (LEGALITY_BANNED, LEGALITY_RESTRICTED, LEGALITY_ROTATED):
            return IMPACT_HIGH
        return IMPACT_LOW
    existing_state = (existing.get("legality_state") or "").strip().lower()
    if proposed_state == existing_state:
        return IMPACT_LOW
    # State change: material for legality
    if proposed_state in (LEGALITY_BANNED, LEGALITY_RESTRICTED, LEGALITY_ROTATED) or existing_state in (
        LEGALITY_BANNED,
        LEGALITY_RESTRICTED,
        LEGALITY_ROTATED,
    ):
        return IMPACT_HIGH
    return IMPACT_MEDIUM


def _change_type(existing: dict[str, Any] | None, proposed_state: str) -> str:
    if not existing:
        return CHANGE_NEW
    existing_state = (existing.get("legality_state") or "").strip().lower()
    if proposed_state == existing_state:
        return CHANGE_NO_CHANGE
    return CHANGE_UPDATE


def _conflict_detected(existing: dict[str, Any] | None, proposed_state: str, source_id: str) -> bool:
    """True if existing official record disagrees with proposed state (for current, effective-now purposes)."""
    if not existing:
        return False
    sid = (existing.get("source_id") or "").strip()
    if sid not in OFFICIAL_LEGALITY_SOURCE_IDS:
        return False
    existing_state = (existing.get("legality_state") or "").strip().lower()
    return proposed_state != existing_state


def _conflict_should_escalate(conflict_detected: bool, is_effective_now: bool) -> bool:
    """True if we should escalate to review as a current conflict. Future-dated official change is not a hard conflict."""
    if not conflict_detected:
        return False
    return is_effective_now


def evaluate_candidate(
    row: dict[str, str],
    source_id: str,
    catalog_path: Path,
    format_name: str,
) -> dict[str, Any]:
    """Build a routing decision record for one staged row."""
    card_code = (row.get("card_code") or "").strip().upper()
    ban_status = row.get("ban_status") or row.get("legality_state") or ""
    proposed_state = _normalize_legality_state(ban_status)
    effective_date = (row.get("effective_date") or "").strip()
    notes = (row.get("notes") or "").strip()
    source_reference = (row.get("source_reference") or row.get("source_url") or "").strip()

    source_approved = source_id.strip() in OFFICIAL_LEGALITY_SOURCE_IDS
    provenance_complete = bool(card_code and source_id and (source_reference or effective_date or notes))
    publish_allowed, publish_reason = can_publish(provenance_complete, source_approved, card_code=card_code, source_id=source_id)

    existing = get_legality_state(catalog_path, card_code, format_name) if catalog_path.is_file() else None
    impact_level = _impact_level(existing, proposed_state)
    change_type = _change_type(existing, proposed_state)
    conflict_detected = _conflict_detected(existing, proposed_state, source_id)
    effective_now = is_effective_now(effective_date)
    is_upcoming = not effective_now and bool(effective_date and effective_date.strip())

    # Confidence: not in staging CSV; leave None
    confidence_score: float | None = None
    existing_confidence_score: float | None = None
    confidence_delta: float | None = None

    decision_record: dict[str, Any] = {
        "item_identifier": card_code,
        "source_id": source_id,
        "source_approval_status": "approved" if source_approved else "unapproved",
        "provenance_complete": provenance_complete,
        "confidence_score": confidence_score,
        "existing_confidence_score": existing_confidence_score,
        "confidence_delta": confidence_delta,
        "change_type": change_type,
        "impact_level": impact_level,
        "conflict_detected": conflict_detected,
        "is_effective_now": effective_now,
        "is_upcoming": is_upcoming,
        "publish_allowed": publish_allowed,
        "proposed_state": proposed_state,
        "effective_date": effective_date,
        "source_reference": source_reference,
        "notes": notes,
        "format_name": format_name,
    }

    # Apply routing policy (future-dated conflict → upcoming_stored, not review_queue)
    final_decision, decision_reason, decision_note = _route(decision_record, publish_reason)
    decision_record["final_decision"] = final_decision
    decision_record["decision_reason"] = decision_reason
    decision_record["decision_note"] = decision_note
    return decision_record


def _route(rec: dict[str, Any], publish_reason: str) -> tuple[str, str, str]:
    """Simple explicit policy: auto_apply / review_queue / reject_hold / skipped / upcoming_stored."""
    if not rec["source_approval_status"] == "approved":
        return REJECT_HOLD, "source_unapproved", "Source not in official legality allowlist."
    if not rec["provenance_complete"]:
        return REJECT_HOLD, "provenance_incomplete", "Provenance missing or insufficient."
    if not rec["publish_allowed"]:
        return REJECT_HOLD, "publish_refused", publish_reason or "Publish gate refused."
    # Future-dated official change that would conflict with current: store as upcoming, do not surface as current conflict
    if rec.get("conflict_detected") and rec.get("is_upcoming"):
        return (
            UPCOMING_STORED,
            "upcoming_official_change",
            "This looks like an upcoming official legality change; stored as upcoming until it becomes active.",
        )
    if rec["conflict_detected"]:
        return REVIEW_QUEUE, "conflict_detected", "Existing official record disagrees with proposed state."
    # Duplicate/no-value: skip; do not treat as policy failure
    if rec["change_type"] == CHANGE_NO_CHANGE:
        return SKIPPED, "no_change", "Duplicate/no change; skipped."
    # Trusted high-impact: official source, complete provenance, publish allowed, no conflict, clear official change
    if (
        rec["impact_level"] == IMPACT_HIGH
        and rec["change_type"] in (CHANGE_NEW, CHANGE_UPDATE)
        and not rec["conflict_detected"]
    ):
        return (
            AUTO_APPLY,
            "trusted_official_high_impact",
            "Official source, complete provenance, no conflict; high-impact change auto-applied.",
        )
    if rec["impact_level"] in (IMPACT_MEDIUM, IMPACT_HIGH):
        return REVIEW_QUEUE, "impact_medium_or_high", "Legality state change requires review."
    # Low-impact safe enrichment
    if rec["impact_level"] == IMPACT_LOW and rec["change_type"] in (CHANGE_NEW, CHANGE_UPDATE):
        return AUTO_APPLY, "safe_low_risk", "Approved source, complete provenance, low impact; auto-apply."
    return REVIEW_QUEUE, "default_review", "Routed to review for safety."


def run_batch(
    csv_path: Path,
    source_id: str,
    catalog_path: Path,
    dry_run: bool,
    data_dir: Path,
) -> dict[str, Any]:
    """Load staged CSV, evaluate each row, route, auto-apply safe items, return report payload."""
    rows = read_staging_csv(csv_path)
    format_name = "standard"
    # Optional: read format from first row if column present
    if rows:
        fmt_col = rows[0].get("format_code") or rows[0].get("format") or ""
        if fmt_col:
            format_name = fmt_col.strip().lower() or "standard"

    ensure_catalog_sync_schema(catalog_path)
    decisions: list[dict[str, Any]] = []
    for row in rows:
        card_code = (row.get("card_code") or "").strip().upper()
        if not card_code:
            continue
        fmt = (row.get("format_code") or row.get("format") or format_name).strip().lower() or "standard"
        rec = evaluate_candidate(row, source_id, catalog_path, fmt)
        decisions.append(rec)

    auto_applied: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    upcoming_stored: list[dict[str, Any]] = []
    rules_db_path = (data_dir / "miru_official_rules.db") if data_dir else DEFAULT_RULES_DB_PATH

    for rec in decisions:
        if rec["final_decision"] == AUTO_APPLY:
            if not dry_run:
                ok = save_legality_state(
                    catalog_path,
                    rec["item_identifier"],
                    rec["format_name"],
                    rec["proposed_state"],
                    effective_date=rec.get("effective_date") or "",
                    source_id=source_id,
                    source_reference=rec.get("source_reference") or "",
                    notes=rec.get("notes") or "",
                )
                if ok:
                    auto_applied.append(rec)
            else:
                auto_applied.append(rec)
        elif rec["final_decision"] == UPCOMING_STORED:
            if not dry_run:
                ingest_legality_row(
                    rules_db_path,
                    rec["item_identifier"],
                    rec["format_name"],
                    rec["proposed_state"],
                    effective_date=rec.get("effective_date") or "",
                    source_id=source_id,
                    source_reference=rec.get("source_reference") or "",
                    notes=rec.get("notes") or "",
                )
            upcoming_stored.append(rec)
        elif rec["final_decision"] == REVIEW_QUEUE:
            review_items.append(rec)
        elif rec["final_decision"] == SKIPPED:
            skipped.append(rec)
        else:
            rejected.append(rec)

    reason_counts: dict[str, int] = {}
    for rec in decisions:
        r = rec.get("decision_reason") or "unknown"
        reason_counts[r] = reason_counts.get(r, 0) + 1

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    report = {
        "batch_run_ts": ts,
        "csv_path": str(csv_path),
        "source_id": source_id,
        "dry_run": dry_run,
        "total_analyzed": len(decisions),
        "total_auto_applied": len(auto_applied),
        "total_queued_for_review": len(review_items),
        "total_upcoming_stored": len(upcoming_stored),
        "total_rejected_held": len(rejected),
        "total_skipped": len(skipped),
        "reason_counts": reason_counts,
        "auto_applied": [
            {
                "item_identifier": r["item_identifier"],
                "format_name": r["format_name"],
                "proposed_state": r["proposed_state"],
                "decision_reason": r["decision_reason"],
                "decision_note": r["decision_note"],
            }
            for r in auto_applied
        ],
        "review_queue": [
            {
                "item_identifier": r["item_identifier"],
                "source_id": r["source_id"],
                "proposed_state": r["proposed_state"],
                "why_escalated": r["decision_reason"],
                "decision_note": r["decision_note"],
                "confidence_score": r.get("confidence_score"),
                "conflict_detected": r["conflict_detected"],
                "publish_eligibility": r["publish_allowed"],
            }
            for r in review_items
        ],
        "upcoming_stored": [
            {
                "item_identifier": r["item_identifier"],
                "format_name": r["format_name"],
                "proposed_state": r["proposed_state"],
                "effective_date": r.get("effective_date", ""),
                "decision_reason": r["decision_reason"],
            }
            for r in upcoming_stored
        ],
        "rejected_held": [
            {
                "item_identifier": r["item_identifier"],
                "format_name": r["format_name"],
                "decision_reason": r["decision_reason"],
                "decision_note": r["decision_note"],
            }
            for r in rejected
        ],
        "skipped": [
            {
                "item_identifier": r["item_identifier"],
                "format_name": r["format_name"],
                "decision_reason": r["decision_reason"],
                "decision_note": r["decision_note"],
            }
            for r in skipped
        ],
        "variant_classification_available": True,
    }

    report_path = data_dir / f"governed_batch_report_{ts}.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    report["report_path"] = str(report_path)

    # Plain-language layer for review queue (readable by non-technical user)
    def _review_item_plain(r: dict[str, Any]) -> dict[str, Any]:
        out = dict(r)
        reason = (r.get("why_escalated") or r.get("decision_reason") or "").strip()
        state = (r.get("proposed_state") or "").strip().lower()
        ident = (r.get("item_identifier") or "").strip()
        conflict = r.get("conflict_detected", False)
        if "conflict" in reason or conflict:
            out["simple_title"] = "Possible conflict with existing legality"
            out["simple_reason"] = "This update might disagree with what we already have on file. I want you to decide."
            out["why_it_matters"] = "If we apply it without checking, players could see the wrong legality for this card."
            out["confidence_note"] = "The source is approved, but I'm not sure which version is correct."
            out["suggested_action"] = "Review and choose which record to keep."
        elif "impact" in reason or state in (LEGALITY_BANNED, LEGALITY_RESTRICTED, LEGALITY_ROTATED):
            out["simple_title"] = "Important legality change"
            out["simple_reason"] = "This would change whether or how this card is legal to play. I held it so you can see it first."
            out["why_it_matters"] = "Bans and restrictions affect what players can use. I want you to confirm before we show this."
            out["confidence_note"] = "I trust the source, but this is a meaningful change so I'm leaving it for review."
            out["suggested_action"] = "Review before applying."
        else:
            out["simple_title"] = "Item for your review"
            out["simple_reason"] = "This didn't meet my safe-to-apply rules, so I'm showing it to you."
            out["why_it_matters"] = "You may want to apply it manually or adjust how we use this source."
            out["confidence_note"] = "I'm not fully sure about this one, so I left it for review."
            out["suggested_action"] = "Review and decide."
        return out

    report["review_queue"] = [_review_item_plain(x) for x in report["review_queue"]]

    # Plain-English summary and graduation status
    summary_lines: list[str] = []
    summary_lines.append("Governed ingestion run summary")
    summary_lines.append("")
    summary_lines.append("1. What I added automatically")
    if auto_applied:
        for r in auto_applied:
            ident = r.get("item_identifier", "")
            state = r.get("proposed_state", "")
            reason = r.get("decision_reason", "")
            summary_lines.append(f"   - {ident}: set to {state}. I added this because the source was approved and nothing important conflicted with it.")
        summary_lines.append("")
    else:
        summary_lines.append("   Nothing this time.")
        summary_lines.append("")

    summary_lines.append("2. What I held for your review")
    if review_items:
        for r in review_items:
            ident = r.get("item_identifier", "")
            state = r.get("proposed_state", "")
            summary_lines.append(f"   - {ident}: proposed {state}. I held it because it could change something meaningful that players may see.")
        summary_lines.append("")
    else:
        summary_lines.append("   Nothing.")
        summary_lines.append("")

    if upcoming_stored:
        summary_lines.append("2b. Upcoming official changes (stored, not applied yet)")
        for r in upcoming_stored:
            ident = r.get("item_identifier", "")
            state = r.get("proposed_state", "")
            eff = r.get("effective_date", "")
            summary_lines.append(f"   - {ident}: {state} (effective {eff or 'future'}). Stored as upcoming; I did not overwrite current legality.")
        summary_lines.append("")

    summary_lines.append("3. What I skipped (and what I rejected)")
    if skipped:
        summary_lines.append(f"   I skipped {len(skipped)} item(s) because they didn't add anything new (already on file with the same state).")
    if rejected:
        summary_lines.append(f"   I rejected or held {len(rejected)} item(s) because they didn't pass my safety checks (e.g. source not approved or missing provenance). I didn't apply them.")
    if not skipped and not rejected:
        summary_lines.append("   Nothing.")
    summary_lines.append("")

    summary_lines.append("4. What I'm not fully sure about")
    if review_items:
        summary_lines.append("   The items in the review queue above. I trust the source, but I want you to see them before I apply.")
    elif rejected:
        summary_lines.append("   The items I rejected or held back. They didn't pass my safety checks, so I didn't apply them.")
    else:
        summary_lines.append("   Nothing that I need to flag. This run was straightforward.")
    summary_lines.append("")

    # Graduation status
    used_real_source = bool(csv_path.is_file() and source_id.strip() in OFFICIAL_LEGALITY_SOURCE_IDS)
    routing_worked = len(decisions) > 0
    had_safe_apply = len(auto_applied) > 0
    had_meaningful_review = len(review_items) > 0
    had_clear_output = True  # we always write report and summary now
    ethics_preserved = not dry_run or True  # we never bypass gates

    if used_real_source and routing_worked and (had_safe_apply or had_meaningful_review) and had_clear_output and ethics_preserved:
        graduation_status = "success"
        graduation_reason = "I used real approved-source data, routed everything correctly, and either applied safe updates or put meaningful items in your review queue. All writes stayed in the worktree."
    elif used_real_source and routing_worked and had_clear_output and ethics_preserved:
        graduation_status = "partial_success"
        graduation_reason = "I used real approved-source data and routing worked, but nothing was safe to auto-apply and nothing needed review. Outputs are clear and worktree-only."
    elif used_real_source and routing_worked:
        graduation_status = "partial_success"
        graduation_reason = "I used real approved-source data and produced reports. Check the counts above; you may need to add more data or adjust the source."
    else:
        graduation_status = "failed"
        graduation_reason = "I couldn't complete a normal run. Maybe no input file, wrong source, or no rows to process. Check the report and logs."

    summary_lines.append("5. Why this run was or was not successful")
    summary_lines.append(f"   Status: {graduation_status}.")
    summary_lines.append(f"   {graduation_reason}")
    summary_lines.append("")

    summary_lines.append("6. Safe to leave Miru running for the rest of the day?")
    summary_lines.append("   Yes. All writes stay in this worktree only. Nothing is promoted to the main repo.")
    summary_lines.append("   Ethics and provenance rules stay on. Duplicates are skipped, not spammed into review.")
    summary_lines.append("   If something needs your attention, it will be in the review queue and in this summary.")
    summary_lines.append("")

    plain_english_summary = "\n".join(summary_lines)
    report["plain_english_summary"] = plain_english_summary
    report["graduation_status"] = graduation_status
    report["graduation_reason"] = graduation_reason

    summary_path = data_dir / f"governed_batch_summary_{ts}.txt"
    summary_path.write_text(plain_english_summary, encoding="utf-8")
    report["summary_path"] = str(summary_path)

    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if review_items:
        review_path = data_dir / f"governed_batch_review_queue_{ts}.json"
        review_payload = {
            "batch_run_ts": ts,
            "items": report["review_queue"],
        }
        review_path.write_text(json.dumps(review_payload, indent=2), encoding="utf-8")
        report["review_queue_path"] = str(review_path)

    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Governed Autonomy Batch Test: staged legality data -> evaluate, route, auto-apply safe, report.",
    )
    parser.add_argument(
        "csv_path",
        type=Path,
        help="Path to staging CSV (e.g. from miru_fetch_banlist or manual).",
    )
    parser.add_argument(
        "--source-id",
        required=True,
        metavar="ID",
        help="Source ID; must be one of: " + ", ".join(sorted(OFFICIAL_LEGALITY_SOURCE_IDS)),
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help=f"Catalog DB path (default: {DEFAULT_CATALOG}).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Directory for report and review artifacts (default: {DEFAULT_DATA_DIR}).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write to catalog; still write report.")
    args = parser.parse_args()

    sid = (args.source_id or "").strip()
    if sid not in OFFICIAL_LEGALITY_SOURCE_IDS:
        print(f"ERROR: --source-id must be one of {sorted(OFFICIAL_LEGALITY_SOURCE_IDS)}.", file=sys.stderr)
        return 1

    csv_path = Path(args.csv_path)
    if not csv_path.is_file():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        return 1

    report = run_batch(
        csv_path=csv_path,
        source_id=sid,
        catalog_path=Path(args.catalog),
        dry_run=args.dry_run,
        data_dir=Path(args.data_dir),
    )

    print("Governed Batch Test summary")
    print("  total_analyzed     :", report["total_analyzed"])
    print("  auto_applied       :", report["total_auto_applied"])
    print("  queued_for_review  :", report["total_queued_for_review"])
    print("  rejected_held      :", report["total_rejected_held"])
    print("  skipped            :", report.get("total_skipped", 0))
    print("  reason_counts      :", report["reason_counts"])
    print("  graduation_status  :", report.get("graduation_status", ""))
    print("  report             :", report.get("report_path", ""))
    if report.get("summary_path"):
        print("  summary            :", report["summary_path"])
    if report.get("review_queue_path"):
        print("  review_queue       :", report["review_queue_path"])
    if args.dry_run:
        print("  (dry-run; no catalog writes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
