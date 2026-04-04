from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .models import (
    CardDossier,
    ConfidenceSummary,
    FactSourceCitation,
    FactSummary,
    RelationshipDossier,
    VariantDossier,
)
from .trust import SourceTrustProfile


DEFAULT_INTEL_DB_PATH = "data/miru_dossiers.db"


class IntelConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def get_intel_conn(db_path: str = DEFAULT_INTEL_DB_PATH):
    conn = sqlite3.connect(db_path, factory=IntelConnection)
    conn.row_factory = sqlite3.Row
    return conn


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def init_miru_intel_schema(db_path: str = DEFAULT_INTEL_DB_PATH) -> None:
    parent = Path(db_path).parent
    if str(parent):
        parent.mkdir(parents=True, exist_ok=True)
    with get_intel_conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS source_registry (
                source_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                trust_tier INTEGER NOT NULL,
                trust_label TEXT NOT NULL,
                default_weight REAL NOT NULL,
                source_kind TEXT NOT NULL,
                base_url TEXT,
                notes TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS enrichment_runs (
                run_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                total_cards INTEGER NOT NULL DEFAULT 0,
                completed_cards INTEGER NOT NULL DEFAULT 0,
                failed_cards INTEGER NOT NULL DEFAULT 0,
                skipped_cards INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                last_heartbeat_at TEXT
            );

            CREATE TABLE IF NOT EXISTS enrichment_run_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                canonical_code TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(run_id, canonical_code)
            );

            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_code TEXT NOT NULL UNIQUE,
                set_code TEXT,
                set_name TEXT,
                card_name TEXT,
                rarity TEXT,
                color TEXT,
                card_type TEXT,
                official_text TEXT,
                image_identity TEXT,
                overall_state TEXT,
                overall_score REAL,
                stable_refresh_after_at TEXT,
                dynamic_refresh_after_at TEXT,
                last_checked_at TEXT,
                last_run_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS card_variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                variant_key TEXT NOT NULL,
                variant_family TEXT,
                variant_label TEXT,
                image_identity TEXT,
                official_text TEXT,
                verification_state TEXT,
                confidence_score REAL,
                source_summary_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(card_id, variant_key),
                FOREIGN KEY(card_id) REFERENCES cards(id)
            );

            CREATE TABLE IF NOT EXISTS card_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                relationship_type TEXT NOT NULL,
                related_card_code TEXT,
                related_variant_key TEXT,
                related_label TEXT,
                notes TEXT,
                verification_state TEXT,
                confidence_score REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(card_id) REFERENCES cards(id)
            );

            CREATE TABLE IF NOT EXISTS card_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                field_name TEXT NOT NULL,
                value_text TEXT,
                value_json TEXT,
                value_type TEXT NOT NULL,
                verification_state TEXT NOT NULL,
                confidence_score REAL NOT NULL,
                stable_fact INTEGER NOT NULL DEFAULT 1,
                conflict_count INTEGER NOT NULL DEFAULT 0,
                missing_count INTEGER NOT NULL DEFAULT 0,
                supporting_source_count INTEGER NOT NULL DEFAULT 0,
                last_checked_at TEXT,
                refresh_after_at TEXT,
                summary_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(card_id, field_name),
                FOREIGN KEY(card_id) REFERENCES cards(id)
            );

            CREATE TABLE IF NOT EXISTS fact_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact_id INTEGER NOT NULL,
                source_key TEXT NOT NULL,
                source_url TEXT,
                source_title TEXT,
                trust_tier INTEGER NOT NULL,
                trust_label TEXT NOT NULL,
                source_weight REAL NOT NULL,
                observed_value_text TEXT,
                observed_value_json TEXT,
                citation_text TEXT,
                extraction_method TEXT,
                observed_at TEXT,
                is_selected INTEGER NOT NULL DEFAULT 0,
                is_conflicting INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(fact_id) REFERENCES card_facts(id)
            );

            CREATE TABLE IF NOT EXISTS confidence_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                scope TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                verification_state TEXT NOT NULL,
                confidence_score REAL NOT NULL,
                rationale_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(card_id) REFERENCES cards(id)
            );

            CREATE TABLE IF NOT EXISTS refresh_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                canonical_code TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                overall_category TEXT NOT NULL,
                changed_field_count INTEGER NOT NULL DEFAULT 0,
                counts_json TEXT,
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


class MiruIntelRepository:
    def __init__(self, db_path: str = DEFAULT_INTEL_DB_PATH):
        self.db_path = db_path
        init_miru_intel_schema(self.db_path)

    def register_sources(self, profiles: list[SourceTrustProfile]) -> None:
        now = utc_timestamp()
        with get_intel_conn(self.db_path) as conn:
            for profile in profiles:
                conn.execute(
                    """
                    INSERT INTO source_registry (
                        source_key, display_name, trust_tier, trust_label,
                        default_weight, source_kind, base_url, notes, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_key) DO UPDATE SET
                        display_name = excluded.display_name,
                        trust_tier = excluded.trust_tier,
                        trust_label = excluded.trust_label,
                        default_weight = excluded.default_weight,
                        source_kind = excluded.source_kind,
                        base_url = excluded.base_url,
                        notes = excluded.notes,
                        updated_at = excluded.updated_at
                    """,
                    (
                        profile.source_key,
                        profile.display_name,
                        profile.trust_tier,
                        profile.trust_label,
                        profile.default_weight,
                        profile.source_kind,
                        profile.base_url,
                        profile.notes,
                        now,
                    ),
                )

    def start_run(self, run_id: str, card_codes: list[str], *, mode: str, notes: str = "") -> None:
        now = utc_timestamp()
        with get_intel_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO enrichment_runs (
                    run_id, mode, status, total_cards, notes, started_at, last_heartbeat_at
                ) VALUES (?, ?, 'running', ?, ?, ?, ?)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (run_id, mode, len(card_codes), notes, now, now),
            )
            for code in card_codes:
                conn.execute(
                    """
                    INSERT INTO enrichment_run_cards (
                        run_id, canonical_code, status, updated_at
                    ) VALUES (?, ?, 'queued', ?)
                    ON CONFLICT(run_id, canonical_code) DO NOTHING
                    """,
                    (run_id, code, now),
                )

    def list_run_cards(self, run_id: str) -> list[dict[str, Any]]:
        with get_intel_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM enrichment_run_cards WHERE run_id = ? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        with get_intel_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM enrichment_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_run_card_status(
        self,
        run_id: str,
        canonical_code: str,
        status: str,
        *,
        error_message: str = "",
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        now = utc_timestamp()
        with get_intel_conn(self.db_path) as conn:
            conn.execute(
                """
                UPDATE enrichment_run_cards
                SET status = ?, error_message = ?, started_at = COALESCE(?, started_at),
                    finished_at = COALESCE(?, finished_at), updated_at = ?
                WHERE run_id = ? AND canonical_code = ?
                """,
                (status, error_message, started_at, finished_at, now, run_id, canonical_code),
            )
            counts = defaultdict(int)
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM enrichment_run_cards WHERE run_id = ? GROUP BY status",
                (run_id,),
            ).fetchall():
                counts[row["status"]] = row["count"]
            if counts.get("queued") or counts.get("running"):
                run_status = "running"
            elif counts.get("failed"):
                run_status = "failed"
            else:
                run_status = "completed"
            conn.execute(
                """
                UPDATE enrichment_runs
                SET completed_cards = ?, failed_cards = ?, skipped_cards = ?,
                    status = ?, last_heartbeat_at = ?
                WHERE run_id = ?
                """,
                (
                    counts.get("completed", 0),
                    counts.get("failed", 0),
                    counts.get("skipped", 0),
                    run_status,
                    now,
                    run_id,
                ),
            )

    def finish_run(self, run_id: str, *, status: str = "completed") -> None:
        now = utc_timestamp()
        with get_intel_conn(self.db_path) as conn:
            conn.execute(
                "UPDATE enrichment_runs SET status = ?, finished_at = ?, last_heartbeat_at = ? WHERE run_id = ?",
                (status, now, now, run_id),
            )

    def record_refresh_report(
        self,
        *,
        run_id: str,
        canonical_code: str,
        source_kind: str,
        overall_category: str,
        changed_field_count: int,
        counts: dict[str, Any],
        report: dict[str, Any],
    ) -> None:
        now = utc_timestamp()
        with get_intel_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO refresh_reports (
                    run_id, canonical_code, source_kind, overall_category,
                    changed_field_count, counts_json, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    canonical_code,
                    source_kind,
                    overall_category,
                    changed_field_count,
                    json.dumps(counts or {}, ensure_ascii=True, sort_keys=True),
                    json.dumps(report or {}, ensure_ascii=True, sort_keys=True),
                    now,
                ),
            )

    def list_refresh_reports(
        self,
        *,
        run_id: str | None = None,
        canonical_code: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM refresh_reports"
        clauses: list[str] = []
        params: list[str] = []
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if canonical_code:
            clauses.append("canonical_code = ?")
            params.append((canonical_code or "").strip().upper())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id ASC"
        with get_intel_conn(self.db_path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def upsert_card_summary(self, summary: dict[str, Any]) -> int:
        now = utc_timestamp()
        with get_intel_conn(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id FROM cards WHERE canonical_code = ?",
                (summary["canonical_code"],),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE cards
                    SET set_code = ?, set_name = ?, card_name = ?, rarity = ?, color = ?,
                        card_type = ?, official_text = ?, image_identity = ?, overall_state = ?,
                        overall_score = ?, stable_refresh_after_at = ?, dynamic_refresh_after_at = ?,
                        last_checked_at = ?, last_run_id = ?, updated_at = ?
                    WHERE canonical_code = ?
                    """,
                    (
                        summary.get("set_code"),
                        summary.get("set_name"),
                        summary.get("card_name"),
                        summary.get("rarity"),
                        summary.get("color"),
                        summary.get("card_type"),
                        summary.get("official_text"),
                        summary.get("image_identity"),
                        summary.get("overall_state"),
                        summary.get("overall_score"),
                        summary.get("stable_refresh_after_at"),
                        summary.get("dynamic_refresh_after_at"),
                        summary.get("last_checked_at"),
                        summary.get("last_run_id"),
                        now,
                        summary["canonical_code"],
                    ),
                )
                return int(existing["id"])
            cursor = conn.execute(
                """
                INSERT INTO cards (
                    canonical_code, set_code, set_name, card_name, rarity, color,
                    card_type, official_text, image_identity, overall_state, overall_score,
                    stable_refresh_after_at, dynamic_refresh_after_at, last_checked_at,
                    last_run_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary["canonical_code"],
                    summary.get("set_code"),
                    summary.get("set_name"),
                    summary.get("card_name"),
                    summary.get("rarity"),
                    summary.get("color"),
                    summary.get("card_type"),
                    summary.get("official_text"),
                    summary.get("image_identity"),
                    summary.get("overall_state"),
                    summary.get("overall_score"),
                    summary.get("stable_refresh_after_at"),
                    summary.get("dynamic_refresh_after_at"),
                    summary.get("last_checked_at"),
                    summary.get("last_run_id"),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def replace_card_details(
        self,
        card_id: int,
        *,
        variants: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        facts: list[dict[str, Any]],
        confidence_rows: list[dict[str, Any]],
    ) -> None:
        now = utc_timestamp()
        with get_intel_conn(self.db_path) as conn:
            conn.execute("DELETE FROM card_variants WHERE card_id = ?", (card_id,))
            conn.execute("DELETE FROM card_relationships WHERE card_id = ?", (card_id,))
            fact_ids = conn.execute(
                "SELECT id FROM card_facts WHERE card_id = ?",
                (card_id,),
            ).fetchall()
            for row in fact_ids:
                conn.execute("DELETE FROM fact_sources WHERE fact_id = ?", (row["id"],))
            conn.execute("DELETE FROM card_facts WHERE card_id = ?", (card_id,))
            conn.execute("DELETE FROM confidence_records WHERE card_id = ?", (card_id,))

            for variant in variants:
                conn.execute(
                    """
                    INSERT INTO card_variants (
                        card_id, variant_key, variant_family, variant_label, image_identity,
                        official_text, verification_state, confidence_score, source_summary_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        card_id,
                        variant.get("variant_key"),
                        variant.get("variant_family"),
                        variant.get("variant_label"),
                        variant.get("image_identity"),
                        variant.get("official_text"),
                        variant.get("verification_state"),
                        variant.get("confidence_score"),
                        variant.get("source_summary_json"),
                        now,
                        now,
                    ),
                )

            for relationship in relationships:
                conn.execute(
                    """
                    INSERT INTO card_relationships (
                        card_id, relationship_type, related_card_code, related_variant_key,
                        related_label, notes, verification_state, confidence_score, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        card_id,
                        relationship.get("relationship_type"),
                        relationship.get("related_card_code"),
                        relationship.get("related_variant_key"),
                        relationship.get("related_label"),
                        relationship.get("notes"),
                        relationship.get("verification_state"),
                        relationship.get("confidence_score"),
                        now,
                        now,
                    ),
                )

            for fact in facts:
                cursor = conn.execute(
                    """
                    INSERT INTO card_facts (
                        card_id, field_name, value_text, value_json, value_type, verification_state,
                        confidence_score, stable_fact, conflict_count, missing_count,
                        supporting_source_count, last_checked_at, refresh_after_at, summary_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        card_id,
                        fact.get("field_name"),
                        fact.get("value_text"),
                        fact.get("value_json"),
                        fact.get("value_type"),
                        fact.get("verification_state"),
                        fact.get("confidence_score"),
                        1 if fact.get("stable_fact", True) else 0,
                        fact.get("conflict_count", 0),
                        fact.get("missing_count", 0),
                        fact.get("supporting_source_count", 0),
                        fact.get("last_checked_at"),
                        fact.get("refresh_after_at"),
                        fact.get("summary_json"),
                        now,
                        now,
                    ),
                )
                fact_id = int(cursor.lastrowid)
                for citation in fact.get("citations") or []:
                    conn.execute(
                        """
                        INSERT INTO fact_sources (
                            fact_id, source_key, source_url, source_title, trust_tier, trust_label,
                            source_weight, observed_value_text, observed_value_json, citation_text,
                            extraction_method, observed_at, is_selected, is_conflicting, notes, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            fact_id,
                            citation.get("source_key"),
                            citation.get("source_url"),
                            citation.get("source_title"),
                            citation.get("trust_tier"),
                            citation.get("trust_label"),
                            citation.get("source_weight"),
                            citation.get("observed_value_text"),
                            citation.get("observed_value_json"),
                            citation.get("citation_text"),
                            citation.get("extraction_method"),
                            citation.get("observed_at"),
                            1 if citation.get("is_selected") else 0,
                            1 if citation.get("is_conflicting") else 0,
                            citation.get("notes"),
                            now,
                        ),
                    )

            for row in confidence_rows:
                conn.execute(
                    """
                    INSERT INTO confidence_records (
                        card_id, scope, scope_key, verification_state, confidence_score,
                        rationale_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        card_id,
                        row.get("scope"),
                        row.get("scope_key"),
                        row.get("verification_state"),
                        row.get("confidence_score"),
                        row.get("rationale_json"),
                        now,
                        now,
                    ),
                )

    def build_card_dossier(self, canonical_code: str) -> CardDossier | None:
        with get_intel_conn(self.db_path) as conn:
            card = conn.execute(
                "SELECT * FROM cards WHERE canonical_code = ?",
                ((canonical_code or "").strip().upper(),),
            ).fetchone()
            if not card:
                return None

            variants = tuple(
                VariantDossier(
                    variant_key=row["variant_key"],
                    variant_family=row["variant_family"] or "",
                    variant_label=row["variant_label"] or "",
                    image_identity=row["image_identity"] or "",
                    verification_state=row["verification_state"] or "missing",
                    confidence_score=float(row["confidence_score"] or 0.0),
                    source_summary_json=row["source_summary_json"] or "",
                )
                for row in conn.execute(
                    "SELECT * FROM card_variants WHERE card_id = ? ORDER BY variant_key",
                    (card["id"],),
                ).fetchall()
            )
            relationships = tuple(
                RelationshipDossier(
                    relationship_type=row["relationship_type"],
                    related_card_code=row["related_card_code"] or "",
                    related_variant_key=row["related_variant_key"] or "",
                    related_label=row["related_label"] or "",
                    notes=row["notes"] or "",
                    verification_state=row["verification_state"] or "missing",
                    confidence_score=float(row["confidence_score"] or 0.0),
                )
                for row in conn.execute(
                    "SELECT * FROM card_relationships WHERE card_id = ? ORDER BY relationship_type, related_card_code",
                    (card["id"],),
                ).fetchall()
            )

            facts: list[FactSummary] = []
            ledger: dict[tuple[str, str, str], FactSourceCitation] = {}
            for fact_row in conn.execute(
                "SELECT * FROM card_facts WHERE card_id = ? ORDER BY field_name",
                (card["id"],),
            ).fetchall():
                citations = tuple(
                    FactSourceCitation(
                        source_key=row["source_key"],
                        source_url=row["source_url"] or "",
                        source_title=row["source_title"] or "",
                        trust_tier=int(row["trust_tier"] or 3),
                        trust_label=row["trust_label"] or "unknown",
                        source_weight=float(row["source_weight"] or 0.0),
                        observed_value_text=row["observed_value_text"] or "",
                        observed_value_json=row["observed_value_json"] or "",
                        citation_text=row["citation_text"] or "",
                        extraction_method=row["extraction_method"] or "structured",
                        observed_at=row["observed_at"] or "",
                        is_selected=bool(row["is_selected"]),
                        is_conflicting=bool(row["is_conflicting"]),
                        notes=row["notes"] or "",
                    )
                    for row in conn.execute(
                        "SELECT * FROM fact_sources WHERE fact_id = ? ORDER BY trust_tier ASC, source_weight DESC, id ASC",
                        (fact_row["id"],),
                    ).fetchall()
                )
                for citation in citations:
                    ledger.setdefault(
                        (citation.source_key, citation.source_url, citation.observed_value_text),
                        citation,
                    )
                facts.append(
                    FactSummary(
                        field_name=fact_row["field_name"],
                        value_text=fact_row["value_text"] or "",
                        value_json=fact_row["value_json"] or "",
                        value_type=fact_row["value_type"] or "text",
                        verification_state=fact_row["verification_state"] or "missing",
                        confidence_score=float(fact_row["confidence_score"] or 0.0),
                        stable_fact=bool(fact_row["stable_fact"]),
                        conflict_count=int(fact_row["conflict_count"] or 0),
                        missing_count=int(fact_row["missing_count"] or 0),
                        supporting_source_count=int(fact_row["supporting_source_count"] or 0),
                        last_checked_at=fact_row["last_checked_at"] or "",
                        refresh_after_at=fact_row["refresh_after_at"] or "",
                        summary_json=fact_row["summary_json"] or "",
                        citations=citations,
                    )
                )

            fact_lookup = {fact.field_name: fact for fact in facts}

            def _fact_value(field_name: str):
                fact = fact_lookup.get(field_name)
                if not fact:
                    return ""
                if fact.value_type == "json":
                    if not fact.value_json:
                        return []
                    try:
                        return json.loads(fact.value_json)
                    except Exception:
                        return []
                return fact.value_text

            official_details = {
                "cost": _fact_value("cost"),
                "power": _fact_value("power"),
                "counter": _fact_value("counter"),
                "attribute": _fact_value("attribute"),
                "traits": _fact_value("traits"),
                "life": _fact_value("life"),
                "effect_text": _fact_value("effect_text"),
                "trigger_text": _fact_value("trigger_text"),
                "subtypes": _fact_value("subtypes"),
                "availability": _fact_value("availability"),
                "status": _fact_value("status"),
            }

            set_info = {
                "set_code": card["set_code"] or "",
                "set_name": card["set_name"] or "",
                "series_name": _fact_value("series_name"),
                "product_name": _fact_value("product_name"),
            }

            confidence_rows = conn.execute(
                "SELECT * FROM confidence_records WHERE card_id = ? ORDER BY scope, scope_key",
                (card["id"],),
            ).fetchall()
            by_state: dict[str, list[str]] = defaultdict(list)
            overall_row = None
            for row in confidence_rows:
                if row["scope"] == "card" and row["scope_key"] == "overall":
                    overall_row = row
                elif row["scope"] == "field":
                    by_state[row["verification_state"]].append(row["scope_key"])

            return CardDossier(
                canonical_code=card["canonical_code"],
                identity={
                    "card_name": card["card_name"] or "",
                    "set_code": card["set_code"] or "",
                    "rarity": card["rarity"] or "",
                    "color": card["color"] or "",
                    "card_type": card["card_type"] or "",
                    "official_text": card["official_text"] or "",
                    "image_identity": card["image_identity"] or "",
                },
                official_details=official_details,
                variants=variants,
                set_info=set_info,
                relationships=relationships,
                gameplay_context={
                    "status": "placeholder",
                    "notes": "Gameplay interpretation is intentionally deferred.",
                },
                market_context={
                    "status": "placeholder",
                    "notes": "Market interpretation is intentionally deferred.",
                },
                source_ledger=tuple(ledger.values()),
                facts=tuple(facts),
                confidence_summary=ConfidenceSummary(
                    overall_state=(overall_row["verification_state"] if overall_row else card["overall_state"] or "missing"),
                    overall_score=float(((overall_row["confidence_score"] if overall_row else card["overall_score"]) or 0.0)),
                    verified_fields=tuple(by_state.get("verified", [])),
                    likely_fields=tuple(by_state.get("likely", [])),
                    uncertain_fields=tuple(by_state.get("uncertain", [])),
                    conflicting_fields=tuple(by_state.get("conflict", [])),
                    missing_fields=tuple(by_state.get("missing", [])),
                ),
                refresh={
                    "last_checked_at": card["last_checked_at"] or "",
                    "stable_refresh_after_at": card["stable_refresh_after_at"] or "",
                    "dynamic_refresh_after_at": card["dynamic_refresh_after_at"] or "",
                    "last_run_id": card["last_run_id"] or "",
                },
                future_extensions={
                    "semantic_memory": {
                        "status": "deferred",
                        "attach_point": "facts and citations can be embedded later without replacing verified storage",
                    }
                },
            )
