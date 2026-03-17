from __future__ import annotations

import json
from typing import Any

from dashboard.miru_intel_models import CardDossier, FactSummary


def _fact_lookup(dossier: CardDossier) -> dict[str, FactSummary]:
    return {fact.field_name: fact for fact in dossier.facts}


def _fact_value(fact: FactSummary) -> Any:
    if fact.value_type == "json":
        if not fact.value_json:
            return []
        try:
            return json.loads(fact.value_json)
        except Exception:
            return []
    return fact.value_text


def get_identity_summary(dossier: CardDossier) -> dict[str, Any]:
    name = dossier.identity.get("card_name") or "Unknown card"
    set_name = dossier.set_info.get("set_name") or "Unknown set"
    return {
        "card_code": dossier.canonical_code,
        "card_name": name,
        "set_name": set_name,
        "answer": f"{dossier.canonical_code} is {name} from {set_name}.",
        "verification_state": dossier.confidence_summary.overall_state,
        "confidence_score": dossier.confidence_summary.overall_score,
    }


def get_fact_answer(dossier: CardDossier, fact_key: str) -> dict[str, Any]:
    fact = _fact_lookup(dossier).get(fact_key)
    if not fact:
        return {
            "fact_key": fact_key,
            "answer": f"Miru does not have a stored fact for {fact_key}.",
            "verification_state": "missing",
            "confidence_score": 0.0,
            "value": None,
            "sources": [],
        }
    value = _fact_value(fact)
    if fact.verification_state == "missing":
        answer = f"The official dossier does not currently provide {fact_key}."
    elif fact.verification_state == "conflict":
        answer = f"Miru has conflicting values for {fact_key} and will not guess."
    elif value in ("", [], None):
        answer = f"Miru has no usable value stored for {fact_key}."
    else:
        answer = f"{fact_key.replace('_', ' ').title()}: {value}."
    return {
        "fact_key": fact_key,
        "answer": answer,
        "verification_state": fact.verification_state,
        "confidence_score": fact.confidence_score,
        "value": value,
        "sources": [citation.source_title for citation in fact.citations if citation.is_selected],
    }


def get_variant_answer(dossier: CardDossier) -> dict[str, Any]:
    variants = [item for item in dossier.variants if item.variant_key and item.variant_key != "base"]
    return {
        "has_variant": bool(variants),
        "variant_labels": [item.variant_label or item.variant_key for item in variants],
        "image_identities": [item.image_identity for item in dossier.variants if item.image_identity],
        "answer": (
            f"Miru knows {len(variants)} non-base variant(s) for {dossier.canonical_code}."
            if variants
            else f"Miru does not currently have a non-base variant recorded for {dossier.canonical_code}."
        ),
    }


def get_source_summary(dossier: CardDossier, fact_key: str) -> dict[str, Any]:
    fact = _fact_lookup(dossier).get(fact_key)
    if not fact:
        return {
            "fact_key": fact_key,
            "answer": f"No stored source summary exists for {fact_key}.",
            "selected_sources": [],
        }
    selected = [citation for citation in fact.citations if citation.is_selected]
    answer = (
        f"{fact_key.replace('_', ' ').title()} is {fact.verification_state} because it is supported by "
        f"{', '.join(citation.source_title for citation in selected) or 'no selected source'}"
    )
    return {
        "fact_key": fact_key,
        "answer": answer,
        "selected_sources": [citation.to_dict() for citation in selected],
        "verification_state": fact.verification_state,
    }


def get_conflict_summary(dossier: CardDossier, fact_key: str) -> dict[str, Any]:
    fact = _fact_lookup(dossier).get(fact_key)
    if not fact or fact.verification_state != "conflict":
        return {
            "fact_key": fact_key,
            "answer": f"No stored conflict exists for {fact_key}.",
            "candidates": [],
            "sources": [],
        }
    try:
        candidates = json.loads(fact.value_json or "[]")
    except Exception:
        candidates = []
    return {
        "fact_key": fact_key,
        "answer": f"Miru recorded a conflict for {fact_key} and did not choose a winner.",
        "candidates": candidates,
        "sources": [citation.to_dict() for citation in fact.citations],
    }
