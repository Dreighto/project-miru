from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from .card_intel import clean_card_name, infer_set_code, lookup_set
from .db import MiruIntelRepository, utc_timestamp
from .models import FactSourceCitation, SourceCardRecord
from .trust import DEFAULT_SOURCE_TRUST, SourceTrustProfile, get_source_profile


FACT_FIELD_CONFIG: dict[str, dict[str, object]] = {
    "card_name": {"stable_fact": True, "value_type": "text"},
    "set_name": {"stable_fact": True, "value_type": "text"},
    "rarity": {"stable_fact": True, "value_type": "text"},
    "color": {"stable_fact": True, "value_type": "text"},
    "card_type": {"stable_fact": True, "value_type": "text"},
    "cost": {"stable_fact": True, "value_type": "text"},
    "power": {"stable_fact": True, "value_type": "text"},
    "counter": {"stable_fact": True, "value_type": "text"},
    "attribute": {"stable_fact": True, "value_type": "text"},
    "traits": {"stable_fact": True, "value_type": "json"},
    "life": {"stable_fact": True, "value_type": "text"},
    "block_number": {"stable_fact": True, "value_type": "text"},
    "ban_status": {"stable_fact": False, "value_type": "text"},
    "restriction_count": {"stable_fact": False, "value_type": "text"},
    "effect_text": {"stable_fact": True, "value_type": "text"},
    "trigger_text": {"stable_fact": True, "value_type": "text"},
    "subtypes": {"stable_fact": True, "value_type": "json"},
    "availability": {"stable_fact": False, "value_type": "text"},
    "status": {"stable_fact": False, "value_type": "text"},
    "series_name": {"stable_fact": True, "value_type": "text"},
    "product_name": {"stable_fact": True, "value_type": "text"},
    "official_text": {"stable_fact": True, "value_type": "text"},
    "image_identity": {"stable_fact": False, "value_type": "text"},
}


def canonicalize_card_code(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").upper())


def _normalize_value(value: Any) -> str:
    if isinstance(value, dict):
        if not value:
            return ""
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return str(value or "").strip()


def _refresh_after(*, stable_fact: bool, verification_state: str, last_checked_at: str) -> str:
    try:
        anchor = datetime.strptime(last_checked_at or "", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""
    if stable_fact:
        delta = timedelta(days=90)
    elif verification_state == "conflict":
        delta = timedelta(days=7)
    else:
        delta = timedelta(days=30)
    return (anchor + delta).strftime("%Y-%m-%d 00:00:00")


class MiruEnrichmentRunner:
    def __init__(
        self,
        repository: MiruIntelRepository,
        adapters: list[Any],
        *,
        source_registry: dict[str, SourceTrustProfile] | None = None,
    ) -> None:
        self.repository = repository
        self.adapters = adapters
        self.source_registry = source_registry or dict(DEFAULT_SOURCE_TRUST)
        self.repository.register_sources(list(self.source_registry.values()))

    def create_run_id(self) -> str:
        return f"miru-run-{uuid.uuid4().hex[:10]}"

    def run_batch(
        self,
        card_codes: list[str],
        *,
        run_id: str | None = None,
        resume: bool = False,
        mode: str = "controlled-enrichment",
        notes: str = "",
    ) -> dict[str, Any]:
        canonical_codes = [canonicalize_card_code(code) for code in card_codes if canonicalize_card_code(code)]
        run_id = run_id or self.create_run_id()
        existing = self.repository.load_run(run_id)
        if not existing:
            self.repository.start_run(run_id, canonical_codes, mode=mode, notes=notes)
        elif not resume:
            raise ValueError(f"Run {run_id} already exists; pass resume=True to continue it.")

        results: list[dict[str, Any]] = []
        run_rows = {row["canonical_code"]: row for row in self.repository.list_run_cards(run_id)}
        for code in canonical_codes:
            prior = run_rows.get(code)
            if resume and prior and prior["status"] == "completed":
                results.append({"canonical_code": code, "status": "skipped", "reason": "already-completed"})
                continue
            started_at = utc_timestamp()
            self.repository.update_run_card_status(run_id, code, "running", started_at=started_at)
            try:
                result = self.enrich_card(code, run_id=run_id)
                self.repository.update_run_card_status(run_id, code, "completed", finished_at=utc_timestamp())
                results.append({"canonical_code": code, "status": "completed", "overall_state": result["summary"]["overall_state"]})
            except Exception as exc:
                self.repository.update_run_card_status(run_id, code, "failed", error_message=str(exc), finished_at=utc_timestamp())
                results.append({"canonical_code": code, "status": "failed", "error": str(exc)})
        final_status = "failed" if any(item["status"] == "failed" for item in results) else "completed"
        self.repository.finish_run(run_id, status=final_status)
        return {"run": self.repository.load_run(run_id), "results": results}

    def enrich_card(self, canonical_code: str, *, run_id: str = "") -> dict[str, Any]:
        records: list[SourceCardRecord] = []
        for adapter in self.adapters:
            records.extend(adapter.fetch_card_records(canonical_code) or [])
        identity = self._resolve_identity_fields(canonical_code, records)
        variants = self._resolve_variants(records)
        relationships = self._resolve_relationships(records)
        summary = self._build_card_summary(canonical_code, identity, run_id=run_id)
        facts = self._build_fact_rows(identity)
        confidence_rows = self._build_confidence_rows(identity, summary)
        card_id = self.repository.upsert_card_summary(summary)
        self.repository.replace_card_details(
            card_id,
            variants=variants,
            relationships=relationships,
            facts=facts,
            confidence_rows=confidence_rows,
        )
        dossier = self.repository.build_card_dossier(canonical_code)
        return {
            "canonical_code": canonical_code,
            "summary": summary,
            "facts": facts,
            "variants": variants,
            "relationships": relationships,
            "dossier": dossier.to_dict() if dossier else {},
        }

    def _resolve_identity_fields(self, canonical_code: str, records: list[SourceCardRecord]) -> dict[str, dict[str, Any]]:
        resolved: dict[str, dict[str, Any]] = {}
        set_code = infer_set_code(canonical_code) or ""
        set_info = lookup_set(set_code)
        for field_name, config in FACT_FIELD_CONFIG.items():
            resolved[field_name] = self._resolve_field(
                field_name,
                records,
                stable_fact=bool(config.get("stable_fact", True)),
                value_type=str(config.get("value_type") or "text"),
            )
        if not resolved["set_name"]["value_text"] and set_info:
            resolved["set_name"] = {
                **resolved["set_name"],
                "value_text": set_info.name,
                "verification_state": "likely",
                "confidence_score": max(float(resolved["set_name"].get("confidence_score") or 0.0), 0.66),
                "summary_json": json.dumps({"derived_from": "lookup_set", "set_code": set_code}, ensure_ascii=True, sort_keys=True),
            }
        if not resolved["card_name"]["value_text"]:
            resolved["card_name"] = {
                **resolved["card_name"],
                "value_text": clean_card_name(canonical_code, canonical_code, set_info.name if set_info else None),
            }
        return resolved

    def _resolve_field(
        self,
        field_name: str,
        records: list[SourceCardRecord],
        *,
        stable_fact: bool,
        value_type: str,
    ) -> dict[str, Any]:
        observations: list[dict[str, Any]] = []
        for record in records:
            raw_value = getattr(record, field_name, "")
            normalized = _normalize_value(raw_value)
            if not normalized:
                continue
            profile = get_source_profile(record.source_key, self.source_registry)
            observations.append({"normalized": normalized, "record": record, "profile": profile})

        now = utc_timestamp()
        if not observations:
            return {
                "field_name": field_name,
                "value_text": "",
                "value_json": "",
                "value_type": value_type,
                "verification_state": "missing",
                "confidence_score": 0.0,
                "stable_fact": stable_fact,
                "conflict_count": 0,
                "missing_count": 1,
                "supporting_source_count": 0,
                "last_checked_at": now,
                "refresh_after_at": _refresh_after(stable_fact=stable_fact, verification_state="missing", last_checked_at=now),
                "summary_json": json.dumps({"reason": "no-source-observation"}, ensure_ascii=True, sort_keys=True),
                "citations": [],
            }

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for observation in observations:
            grouped[observation["normalized"]].append(observation)
        candidates = sorted(
            grouped.items(),
            key=lambda item: (
                min(obs["profile"].trust_tier for obs in item[1]),
                -max(obs["profile"].default_weight for obs in item[1]),
                -len(item[1]),
                item[0],
            ),
        )
        selected_value, selected_group = candidates[0]
        citations: list[FactSourceCitation] = []
        for normalized, group in candidates:
            is_selected_group = normalized == selected_value
            for observation in sorted(group, key=lambda obs: (obs["profile"].trust_tier, -obs["profile"].default_weight)):
                record = observation["record"]
                profile = observation["profile"]
                citations.append(
                    FactSourceCitation(
                        source_key=profile.source_key,
                        source_url=record.source_url,
                        source_title=record.source_title,
                        trust_tier=profile.trust_tier,
                        trust_label=profile.trust_label,
                        source_weight=profile.default_weight,
                        observed_value_text=normalized if value_type == "text" else "",
                        observed_value_json=normalized if value_type == "json" else "",
                        citation_text=f"{field_name} observed from {record.source_title or profile.display_name}",
                        observed_at=record.observed_at,
                        is_selected=is_selected_group,
                        is_conflicting=not is_selected_group,
                        notes=record.source_card_ref,
                    )
                )

        if len(candidates) > 1:
            return {
                "field_name": field_name,
                "value_text": "",
                "value_json": json.dumps([candidate for candidate, _ in candidates], ensure_ascii=True, sort_keys=True),
                "value_type": value_type,
                "verification_state": "conflict",
                "confidence_score": 0.49,
                "stable_fact": stable_fact,
                "conflict_count": len(candidates) - 1,
                "missing_count": 0,
                "supporting_source_count": len(selected_group),
                "last_checked_at": now,
                "refresh_after_at": _refresh_after(stable_fact=stable_fact, verification_state="conflict", last_checked_at=now),
                "summary_json": json.dumps({"candidates": [candidate for candidate, _ in candidates]}, ensure_ascii=True, sort_keys=True),
                "citations": [citation.to_dict() for citation in citations],
            }

        best_profile = min((obs["profile"] for obs in selected_group), key=lambda profile: (profile.trust_tier, -profile.default_weight))
        if best_profile.trust_tier == 1:
            verification_state = "verified"
            confidence_score = 0.95
        elif best_profile.trust_tier == 2:
            verification_state = "likely"
            confidence_score = 0.78
        else:
            verification_state = "uncertain"
            confidence_score = 0.52
        return {
            "field_name": field_name,
            "value_text": selected_value if value_type == "text" else "",
            "value_json": selected_value if value_type == "json" else "",
            "value_type": value_type,
            "verification_state": verification_state,
            "confidence_score": confidence_score,
            "stable_fact": stable_fact,
            "conflict_count": 0,
            "missing_count": 0,
            "supporting_source_count": len(selected_group),
            "last_checked_at": now,
            "refresh_after_at": _refresh_after(stable_fact=stable_fact, verification_state=verification_state, last_checked_at=now),
            "summary_json": json.dumps({"selected_source_tier": best_profile.trust_tier}, ensure_ascii=True, sort_keys=True),
            "citations": [citation.to_dict() for citation in citations],
        }

    def _resolve_variants(self, records: list[SourceCardRecord]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            profile = get_source_profile(record.source_key, self.source_registry)
            for variant in record.variants:
                if not variant.variant_key:
                    continue
                grouped[variant.variant_key].append({"variant": variant, "profile": profile})
        results: list[dict[str, Any]] = []
        for variant_key, items in sorted(grouped.items()):
            best = min(items, key=lambda item: (item["profile"].trust_tier, -item["profile"].default_weight))
            profile = best["profile"]
            variant = best["variant"]
            results.append(
                {
                    "variant_key": variant.variant_key,
                    "variant_family": variant.variant_family,
                    "variant_label": variant.variant_label or variant.variant_key,
                    "image_identity": variant.image_identity,
                    "official_text": variant.official_text,
                    "verification_state": "verified" if profile.trust_tier == 1 else "likely" if profile.trust_tier == 2 else "uncertain",
                    "confidence_score": 0.91 if profile.trust_tier == 1 else 0.72 if profile.trust_tier == 2 else 0.48,
                    "source_summary_json": json.dumps({"source_key": profile.source_key, "source_count": len(items)}, ensure_ascii=True, sort_keys=True),
                }
            )
        return results

    def _resolve_relationships(self, records: list[SourceCardRecord]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for record in records:
            profile = get_source_profile(record.source_key, self.source_registry)
            state = "verified" if profile.trust_tier == 1 else "likely" if profile.trust_tier == 2 else "uncertain"
            score = 0.88 if profile.trust_tier == 1 else 0.68 if profile.trust_tier == 2 else 0.45
            for relationship in record.relationships:
                key = (
                    relationship.relationship_type,
                    relationship.related_card_code,
                    relationship.related_variant_key,
                )
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    {
                        "relationship_type": relationship.relationship_type,
                        "related_card_code": relationship.related_card_code,
                        "related_variant_key": relationship.related_variant_key,
                        "related_label": relationship.related_label,
                        "notes": relationship.notes,
                        "verification_state": state,
                        "confidence_score": score,
                    }
                )
        return results

    def _build_card_summary(self, canonical_code: str, identity: dict[str, dict[str, Any]], *, run_id: str) -> dict[str, Any]:
        states = [payload["verification_state"] for payload in identity.values()]
        if "conflict" in states:
            overall_state = "conflict"
            overall_score = 0.42
        elif states and all(state == "verified" for state in states if state != "missing"):
            overall_state = "verified"
            overall_score = 0.93
        elif "likely" in states:
            overall_state = "likely"
            overall_score = 0.76
        elif "uncertain" in states:
            overall_state = "uncertain"
            overall_score = 0.54
        else:
            overall_state = "missing"
            overall_score = 0.0
        return {
            "canonical_code": canonical_code,
            "set_code": infer_set_code(canonical_code) or "",
            "set_name": identity["set_name"]["value_text"],
            "card_name": identity["card_name"]["value_text"],
            "rarity": identity["rarity"]["value_text"],
            "color": identity["color"]["value_text"],
            "card_type": identity["card_type"]["value_text"],
            "official_text": identity["official_text"]["value_text"],
            "image_identity": identity["image_identity"]["value_text"],
            "overall_state": overall_state,
            "overall_score": overall_score,
            "stable_refresh_after_at": max((payload["refresh_after_at"] for payload in identity.values() if payload.get("stable_fact")), default=""),
            "dynamic_refresh_after_at": max((payload["refresh_after_at"] for payload in identity.values() if not payload.get("stable_fact")), default=""),
            "last_checked_at": utc_timestamp(),
            "last_run_id": run_id,
        }

    def _build_fact_rows(self, identity: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        return [identity[field_name] for field_name in FACT_FIELD_CONFIG]

    def _build_confidence_rows(self, identity: dict[str, dict[str, Any]], summary: dict[str, Any]) -> list[dict[str, Any]]:
        rows = [
            {
                "scope": "card",
                "scope_key": "overall",
                "verification_state": summary["overall_state"],
                "confidence_score": summary["overall_score"],
                "rationale_json": json.dumps({"from_fields": sorted(identity.keys())}, ensure_ascii=True, sort_keys=True),
            }
        ]
        for field_name, payload in identity.items():
            rows.append(
                {
                    "scope": "field",
                    "scope_key": field_name,
                    "verification_state": payload["verification_state"],
                    "confidence_score": payload["confidence_score"],
                    "rationale_json": json.dumps(
                        {
                            "supporting_source_count": payload.get("supporting_source_count", 0),
                            "conflict_count": payload.get("conflict_count", 0),
                            "value_type": payload.get("value_type", "text"),
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                }
            )
        return rows
