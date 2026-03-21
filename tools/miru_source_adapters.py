from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import sqlite3
import time
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from tools.miru_source_registry import MiruSourceEntry


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_CACHE_DB_PATH = PROJECT_ROOT / "data" / "miru_source_cache.db"
DEFAULT_DAILY_API_BUDGET = 1000
USAGE_NOTIFICATION_THRESHOLDS = (100, 500, 1000)


class SourceAdapterError(RuntimeError):
    pass


class MiruSourceCache:
    def __init__(
        self,
        *,
        db_path: Path = DEFAULT_SOURCE_CACHE_DB_PATH,
        logger: Any | None = None,
        notifier: Any | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.logger = logger
        self.notifier = notifier
        self.ensure_schema()

    def ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_cache (
                    cache_key TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    request_kind TEXT NOT NULL DEFAULT '',
                    request_target TEXT NOT NULL DEFAULT '',
                    response_json TEXT NOT NULL DEFAULT '',
                    fetched_at TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL DEFAULT '',
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    public_data_only INTEGER NOT NULL DEFAULT 1,
                    requires_login INTEGER NOT NULL DEFAULT 0,
                    rate_limit_hint TEXT NOT NULL DEFAULT '',
                    anti_crawl_policy TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS source_usage (
                    source_id TEXT NOT NULL,
                    period_kind TEXT NOT NULL,
                    period_value TEXT NOT NULL,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (source_id, period_kind, period_value)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def build_cache_key(self, *, source_id: str, request_kind: str, request_target: str) -> str:
        return json.dumps(
            {
                "source_id": str(source_id or "").strip().lower(),
                "request_kind": str(request_kind or "").strip().lower(),
                "request_target": str(request_target or "").strip(),
            },
            sort_keys=True,
            ensure_ascii=True,
        )

    def fetch_json(self, *, source_id: str, request_kind: str, request_target: str) -> dict[str, Any] | None:
        cache_key = self.build_cache_key(
            source_id=source_id,
            request_kind=request_kind,
            request_target=request_target,
        )
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT response_json
                FROM source_cache
                WHERE cache_key = ?
                  AND trim(coalesce(expires_at, '')) != ''
                  AND expires_at >= ?
                """,
                (cache_key, now),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE source_cache SET hit_count = hit_count + 1 WHERE cache_key = ?",
                (cache_key,),
            )
        try:
            payload = json.loads(str(row["response_json"] or "{}"))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def store_json(
        self,
        *,
        source_entry: MiruSourceEntry,
        request_kind: str,
        request_target: str,
        response_payload: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        fetched_at = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        expires_at = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + max(int(ttl_seconds), 60)))
        cache_key = self.build_cache_key(
            source_id=source_entry.source_id,
            request_kind=request_kind,
            request_target=request_target,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO source_cache (
                    cache_key, source_id, request_kind, request_target, response_json,
                    fetched_at, expires_at, hit_count, public_data_only, requires_login,
                    rate_limit_hint, anti_crawl_policy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    response_json = excluded.response_json,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at,
                    public_data_only = excluded.public_data_only,
                    requires_login = excluded.requires_login,
                    rate_limit_hint = excluded.rate_limit_hint,
                    anti_crawl_policy = excluded.anti_crawl_policy
                """,
                (
                    cache_key,
                    source_entry.source_id,
                    request_kind,
                    request_target,
                    json.dumps(response_payload, ensure_ascii=False, sort_keys=True),
                    fetched_at,
                    expires_at,
                    1 if source_entry.public_data_only else 0,
                    1 if source_entry.requires_login else 0,
                    source_entry.rate_limit_hint,
                    source_entry.anti_crawl_policy,
                ),
            )

    def record_external_request(self, *, source_entry: MiruSourceEntry) -> None:
        now = datetime.now(timezone.utc)
        hour_key = now.strftime("%Y-%m-%d %H")
        day_key = now.strftime("%Y-%m-%d")
        with self._connect() as conn:
            for period_kind, period_value in (("hour", hour_key), ("day", day_key)):
                conn.execute(
                    """
                    INSERT INTO source_usage (source_id, period_kind, period_value, request_count, updated_at)
                    VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT(source_id, period_kind, period_value) DO UPDATE SET
                        request_count = request_count + 1,
                        updated_at = excluded.updated_at
                    """,
                    (source_entry.source_id, period_kind, period_value, now.strftime("%Y-%m-%d %H:%M:%S")),
                )
            for threshold in USAGE_NOTIFICATION_THRESHOLDS:
                count = conn.execute(
                    """
                    SELECT request_count
                    FROM source_usage
                    WHERE source_id = ? AND period_kind = 'day' AND period_value = ?
                    """,
                    (source_entry.source_id, day_key),
                ).fetchone()
                daily_count = int(count["request_count"] or 0) if count is not None else 0
                if daily_count == threshold:
                    self._notify(
                        f"usage_{source_entry.source_id}_{threshold}",
                        f"Miru has made {daily_count} external requests to {source_entry.source_name} today. Cache-first and rate-limited safeguards remain active.",
                    )
            daily_count_row = conn.execute(
                """
                SELECT COALESCE(SUM(request_count), 0) AS total
                FROM source_usage
                WHERE period_kind = 'day' AND period_value = ?
                """,
                (day_key,),
            ).fetchone()
            daily_total = int(daily_count_row["total"] or 0) if daily_count_row is not None else 0
            if daily_total >= int(DEFAULT_DAILY_API_BUDGET * 0.9):
                self._notify(
                    f"daily_budget_{day_key}",
                    f"Miru is approaching its daily external request budget with {daily_total} requests today. Cached data should be preferred unless new evidence is required.",
                )

    def snapshot_usage(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        hour_key = now.strftime("%Y-%m-%d %H")
        day_key = now.strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source_id, period_kind, period_value, request_count
                FROM source_usage
                WHERE (period_kind = 'hour' AND period_value = ?) OR (period_kind = 'day' AND period_value = ?)
                ORDER BY source_id, period_kind
                """,
                (hour_key, day_key),
            ).fetchall()
        per_source: dict[str, dict[str, int]] = {}
        for row in rows:
            source_id = str(row["source_id"] or "")
            period_kind = str(row["period_kind"] or "")
            per_source.setdefault(source_id, {})
            per_source[source_id][period_kind] = int(row["request_count"] or 0)
        return {
            "requests_per_hour": sum(values.get("hour", 0) for values in per_source.values()),
            "requests_per_day": sum(values.get("day", 0) for values in per_source.values()),
            "requests_per_source": per_source,
        }

    def _notify(self, event_type: str, message: str) -> None:
        if self.notifier is None:
            return
        try:
            self.notifier(event_type, message, cooldown_seconds=3600)
        except Exception:
            if self.logger is not None:
                self.logger.warning("Source usage notification failed for %s.", event_type)


def _normalize_timestamp(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    if "T" not in text:
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_traits(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    if "/" in text:
        return [part.strip() for part in text.split("/") if part.strip()]
    return [text]


@dataclass(frozen=True)
class NormalizedSourceRecord:
    card_code: str
    card_name: str
    set_code: str
    set_name: str
    rarity: str
    color: str
    card_type: str
    cost: str
    power: str
    counter: str
    attribute: str
    traits: list[str]
    life: str
    effect_text: str
    trigger_text: str
    source_id: str
    source_url: str
    source_reference: str
    fetched_at: str
    illustrator: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedImageRecord:
    card_code: str
    print_id: str
    print_label: str
    variant_key: str
    variant_label: str
    source_id: str
    source_type: str
    source_url: str
    source_reference: str
    image_path: str
    fetched_at: str
    width: int
    height: int
    sample_flag: bool = False
    source_trust_tier: int = 4
    source_trust_label: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OfficialCardListSourceAdapter:
    adapter_id = "official-cardlist"

    def __init__(self, *, cache: MiruSourceCache | None = None) -> None:
        self.cache = cache or MiruSourceCache()

    @classmethod
    def from_path(cls, path: str | Path) -> dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def from_url(cls, url: str) -> dict[str, Any]:
        try:
            with urlopen(url, timeout=10.0) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise SourceAdapterError(f"HTTPError while fetching source snapshot: {exc.code}") from exc
        except URLError as exc:
            raise SourceAdapterError(f"URLError while fetching source snapshot: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise SourceAdapterError(f"Source snapshot was not valid JSON: {exc}") from exc

    def fetch_payload_with_cache(self, *, source_entry: MiruSourceEntry, url: str, request_kind: str) -> dict[str, Any]:
        if not source_entry.public_data_only or source_entry.requires_login or source_entry.allow_aggressive_crawling:
            raise SourceAdapterError(
                f"Source policy does not allow automated fetches for {source_entry.source_id}."
            )
        cached = self.cache.fetch_json(
            source_id=source_entry.source_id,
            request_kind=request_kind,
            request_target=url,
        )
        if cached is not None:
            return cached
        payload = self.from_url(url)
        self.cache.store_json(
            source_entry=source_entry,
            request_kind=request_kind,
            request_target=url,
            response_payload=payload,
            ttl_seconds=max(int(source_entry.request_spacing_seconds * 3600), 21600),
        )
        self.cache.record_external_request(source_entry=source_entry)
        return payload

    def load_payload(
        self,
        *,
        source_entry: MiruSourceEntry,
        payload: dict[str, Any] | None = None,
        snapshot_path: str | Path | None = None,
        snapshot_url: str | None = None,
    ) -> dict[str, Any]:
        if payload is not None:
            return payload
        if snapshot_path:
            return self.from_path(snapshot_path)
        resolved_url = str(snapshot_url or source_entry.snapshot_url or "").strip()
        if resolved_url:
            return self.fetch_payload_with_cache(
                source_entry=source_entry,
                url=resolved_url,
                request_kind="snapshot-json",
            )
        raise SourceAdapterError(
            "OfficialCardListSourceAdapter requires payload, snapshot_path, or snapshot_url."
        )

    def fetch_records(
        self,
        *,
        source_entry: MiruSourceEntry,
        card_code: str = "",
        set_code: str = "",
        payload: dict[str, Any] | None = None,
        snapshot_path: str | Path | None = None,
        snapshot_url: str | None = None,
    ) -> list[NormalizedSourceRecord]:
        snapshot = self.load_payload(
            source_entry=source_entry,
            payload=payload,
            snapshot_path=snapshot_path,
            snapshot_url=snapshot_url,
        )
        meta = snapshot.get("source") or {}
        cards = snapshot.get("cards") or []

        target_code = str(card_code or "").strip().upper()
        target_set = str(set_code or "").strip().upper()
        results: list[NormalizedSourceRecord] = []

        for item in cards:
            if not isinstance(item, dict):
                continue
            item_card_code = str(item.get("card_code") or "").strip().upper()
            item_set_code = str(item.get("set_code") or "").strip().upper()
            if target_code and item_card_code != target_code:
                continue
            if target_set and item_set_code != target_set:
                continue

            results.append(
                NormalizedSourceRecord(
                    card_code=item_card_code,
                    card_name=str(item.get("card_name") or "").strip(),
                    set_code=item_set_code,
                    set_name=str(item.get("set_name") or "").strip(),
                    rarity=str(item.get("rarity") or "").strip(),
                    color=str(item.get("color") or "").strip(),
                    card_type=str(item.get("card_type") or "").strip(),
                    cost=str(item.get("cost") or "").strip(),
                    power=str(item.get("power") or "").strip(),
                    counter=str(item.get("counter") or "").strip(),
                    attribute=str(item.get("attribute") or "").strip(),
                    traits=_normalize_traits(item.get("traits")),
                    life=str(item.get("life") or "").strip(),
                    effect_text=str(item.get("effect_text") or item.get("official_text") or "").strip(),
                    trigger_text=str(item.get("trigger_text") or "").strip(),
                    source_id=source_entry.source_id,
                    source_url=str(item.get("source_url") or meta.get("base_url") or source_entry.base_url).strip(),
                    source_reference=str(item.get("official_card_id") or item_card_code).strip(),
                    fetched_at=_normalize_timestamp(
                        item.get("last_checked_at")
                        or meta.get("snapshot_taken_at")
                        or time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
                    ),
                    illustrator=str(item.get("illustrator") or item.get("artist_credit") or "").strip(),
                )
            )

        return results


class OptcgApiSourceAdapter:
    adapter_id = "optcg-api"
    _BROAD_ENDPOINT_FAMILIES = frozenset(
        {
            "all-set-cards",
            "all-st-cards",
            "all-promo-cards",
            "all-don-cards",
        }
    )
    _FILTERED_ENDPOINT_FAMILIES = frozenset(
        {
            "filtered-set-cards",
            "filtered-st-cards",
            "filtered-promos",
            "filtered-don",
        }
    )
    _NON_CARD_ENDPOINT_FAMILIES = frozenset({"all-sets", "all-decks"})
    _FILTER_ALLOWLIST = frozenset(
        {
            "card_name",
            "set_name",
            "set_id",
            "structure_deck_id",
            "card_color",
            "card_type",
            "rarity",
            "attribute",
        }
    )
    _ENDPOINT_SPECS: dict[str, dict[str, Any]] = {
        "all-sets": {
            "path": "/api/allSets/",
            "request_kind": "sets-json",
            "requires_card_records": False,
        },
        "all-set-cards": {
            "path": "/api/allSetCards/",
            "request_kind": "all-set-cards-json",
            "requires_card_records": True,
        },
        "set-cards": {
            "path": "/api/sets/{set_id}/",
            "request_kind": "set-cards-json",
            "requires_card_records": True,
            "requires_set_id": True,
        },
        "set-card": {
            "path": "/api/sets/card/{card_id}/",
            "request_kind": "set-card-json",
            "requires_card_records": True,
            "requires_card_id": True,
        },
        "filtered-set-cards": {
            "path": "/api/sets/filtered/",
            "request_kind": "set-filtered-json",
            "requires_card_records": True,
            "requires_filters": True,
        },
        "all-decks": {
            "path": "/api/allDecks/",
            "request_kind": "all-decks-json",
            "requires_card_records": False,
        },
        "all-st-cards": {
            "path": "/api/allSTCards/",
            "request_kind": "all-st-cards-json",
            "requires_card_records": True,
        },
        "deck-cards": {
            "path": "/api/decks/{set_id}/",
            "request_kind": "deck-cards-json",
            "requires_card_records": True,
            "requires_set_id": True,
        },
        "deck-card": {
            "path": "/api/decks/card/{card_id}/",
            "request_kind": "deck-card-json",
            "requires_card_records": True,
            "requires_card_id": True,
        },
        "filtered-st-cards": {
            "path": "/api/decks/filtered/",
            "request_kind": "deck-filtered-json",
            "requires_card_records": True,
            "requires_filters": True,
        },
        "all-promo-cards": {
            "path": "/api/allPromoCards/",
            "request_kind": "all-promo-cards-json",
            "requires_card_records": True,
        },
        "promo-card": {
            "path": "/api/promos/card/{card_id}/",
            "request_kind": "promo-card-json",
            "requires_card_records": True,
            "requires_card_id": True,
        },
        "filtered-promos": {
            "path": "/api/promos/filtered/",
            "request_kind": "promo-filtered-json",
            "requires_card_records": True,
            "requires_filters": True,
        },
        "all-don-cards": {
            "path": "/api/allDonCards/",
            "request_kind": "all-don-cards-json",
            "requires_card_records": True,
        },
        "filtered-don": {
            "path": "/api/don/filtered/",
            "request_kind": "don-filtered-json",
            "requires_card_records": True,
            "requires_filters": True,
        },
    }
    _ENDPOINT_ALIASES = {
        "card": "set-card",
        "single-card": "set-card",
        "single_card": "set-card",
        "single": "set-card",
        "sets-card": "set-card",
        "starter-deck-card": "deck-card",
        "st-card": "deck-card",
        "starter-deck-cards": "deck-cards",
        "st-cards": "deck-cards",
        "promo": "promo-card",
        "promo-cards": "all-promo-cards",
        "don": "all-don-cards",
        "all-sets-cards": "all-set-cards",
        "all-starter-decks": "all-decks",
        "filtered-sets": "filtered-set-cards",
        "filtered-decks": "filtered-st-cards",
    }

    def __init__(self, *, cache: MiruSourceCache | None = None) -> None:
        self.cache = cache or MiruSourceCache()

    @classmethod
    def from_path(cls, path: str | Path) -> Any:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def from_url(cls, url: str) -> Any:
        try:
            with urlopen(url, timeout=10.0) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise SourceAdapterError(f"HTTPError while fetching OPTCG API payload: {exc.code}") from exc
        except URLError as exc:
            raise SourceAdapterError(f"URLError while fetching OPTCG API payload: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise SourceAdapterError(f"OPTCG API payload was not valid JSON: {exc}") from exc

    @staticmethod
    def _normalize_card_code(value: object) -> str:
        return str(value or "").strip().upper()

    def fetch_payload_with_cache(
        self,
        *,
        source_entry: MiruSourceEntry,
        url: str,
        request_kind: str,
    ) -> Any:
        if not source_entry.public_data_only or source_entry.requires_login or source_entry.allow_aggressive_crawling:
            raise SourceAdapterError(
                f"Source policy does not allow automated fetches for {source_entry.source_id}."
            )
        cached = self.cache.fetch_json(
            source_id=source_entry.source_id,
            request_kind=request_kind,
            request_target=url,
        )
        if cached is not None:
            if "payload" in cached:
                return cached.get("payload")
            return cached
        payload = self.from_url(url)
        cache_payload = payload if isinstance(payload, dict) else {"payload": payload}
        self.cache.store_json(
            source_entry=source_entry,
            request_kind=request_kind,
            request_target=url,
            response_payload=cache_payload,
            ttl_seconds=max(int(source_entry.request_spacing_seconds * 3600), 21600),
        )
        self.cache.record_external_request(source_entry=source_entry)
        return payload

    @staticmethod
    def _normalize_set_code(value: object) -> str:
        return str(value or "").strip().upper().replace("-", "")

    @staticmethod
    def _format_set_id(value: object) -> str:
        raw = str(value or "").strip().upper().replace("_", "-")
        if not raw:
            return ""
        compact = raw.replace("-", "")
        if re.fullmatch(r"[A-Z]{2,4}\d{2}", compact):
            return f"{compact[:-2]}-{compact[-2:]}"
        return raw

    @staticmethod
    def _normalize_traits_from_subtypes(value: object) -> list[str]:
        text = str(value or "").strip()
        if not text:
            return []
        if "/" in text:
            return [part.strip() for part in text.split("/") if part.strip()]
        if "," in text:
            return [part.strip() for part in text.split(",") if part.strip()]
        return [text]

    @classmethod
    def _normalize_endpoint_family(cls, value: object) -> str:
        family = str(value or "").strip().lower().replace("_", "-")
        family = cls._ENDPOINT_ALIASES.get(family, family)
        return family

    @staticmethod
    def _sanitize_filters(filters: dict[str, Any] | None) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, value in dict(filters or {}).items():
            normalized_key = str(key or "").strip().lower()
            text = str(value or "").strip()
            if normalized_key in OptcgApiSourceAdapter._FILTER_ALLOWLIST and text:
                cleaned[normalized_key] = text
        return cleaned

    @staticmethod
    def _is_promo_card_code(card_code: str) -> bool:
        normalized = str(card_code or "").strip().upper()
        return bool(re.fullmatch(r"P-?\d{3,4}", normalized))

    @staticmethod
    def _is_deck_card_code(card_code: str) -> bool:
        normalized = str(card_code or "").strip().upper()
        return bool(re.fullmatch(r"ST\d{2}-\d{3}", normalized))

    @staticmethod
    def _is_don_card_code(card_code: str) -> bool:
        normalized = str(card_code or "").strip().upper()
        return normalized.startswith("DON")

    @classmethod
    def _infer_card_endpoint_family(cls, *, card_code: str = "", set_code: str = "") -> str:
        resolved_card_code = cls._normalize_card_code(card_code)
        resolved_set_id = cls._format_set_id(set_code)
        if resolved_card_code:
            if cls._is_deck_card_code(resolved_card_code):
                return "deck-card"
            if cls._is_promo_card_code(resolved_card_code):
                return "promo-card"
            if cls._is_don_card_code(resolved_card_code):
                return "filtered-don"
            return "set-card"
        if resolved_set_id.startswith("ST-"):
            return "deck-cards"
        return "set-cards"

    @classmethod
    def _resolve_endpoint_family(
        cls,
        *,
        requested_family: str = "",
        card_code: str = "",
        set_code: str = "",
    ) -> str:
        normalized = cls._normalize_endpoint_family(requested_family)
        if normalized:
            if normalized not in cls._ENDPOINT_SPECS:
                raise SourceAdapterError(f"Unsupported OPTCG API endpoint_family: {requested_family}")
            return normalized
        return cls._infer_card_endpoint_family(card_code=card_code, set_code=set_code)

    @classmethod
    def _build_request_url(
        cls,
        *,
        source_entry: MiruSourceEntry,
        endpoint_family: str,
        card_code: str,
        set_code: str,
        snapshot_url: str,
        filters: dict[str, str],
        allow_catalog_pull: bool,
    ) -> tuple[str, str]:
        family = cls._resolve_endpoint_family(
            requested_family=endpoint_family,
            card_code=card_code,
            set_code=set_code,
        )
        spec = cls._ENDPOINT_SPECS.get(family)
        if spec is None:
            raise SourceAdapterError(f"Unsupported OPTCG API endpoint_family: {family}")
        if family in cls._BROAD_ENDPOINT_FAMILIES and not allow_catalog_pull:
            raise SourceAdapterError(
                f"OPTCG API endpoint '{family}' is intentionally bounded; set allow_catalog_pull=true only for explicit catalog pulls."
            )
        resolved_card_code = cls._normalize_card_code(card_code)
        resolved_set_id = cls._format_set_id(set_code)
        if spec.get("requires_card_id") and not resolved_card_code:
            raise SourceAdapterError(f"OPTCG API endpoint '{family}' requires a card_code.")
        if spec.get("requires_set_id") and not resolved_set_id:
            raise SourceAdapterError(f"OPTCG API endpoint '{family}' requires a set_code or set_id.")
        if spec.get("requires_filters") and not filters:
            raise SourceAdapterError(f"OPTCG API endpoint '{family}' requires at least one allowed filter.")

        template = str(snapshot_url or "").strip()
        if not template:
            template = str(spec.get("path") or "").strip()
            if not template:
                raise SourceAdapterError(f"OPTCG API endpoint '{family}' has no URL template.")
            template = f"{str(source_entry.base_url or 'https://optcgapi.com').rstrip('/')}{template}"
        request_url = template
        if "{card_code}" in request_url or "{card_id}" in request_url:
            request_url = request_url.replace("{card_code}", resolved_card_code).replace("{card_id}", resolved_card_code)
        if "{set_code}" in request_url or "{set_id}" in request_url:
            request_url = request_url.replace("{set_code}", resolved_set_id).replace("{set_id}", resolved_set_id)
        if filters:
            query = urlencode(sorted(filters.items()))
            joiner = "&" if "?" in request_url else "?"
            request_url = f"{request_url}{joiner}{query}"
        return family, request_url

    @staticmethod
    def _looks_like_endpoint_spec(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        return any(
            key in payload
            for key in (
                "endpoint_family",
                "filters",
                "allow_catalog_pull",
                "batch_limit",
                "snapshot_url",
                "set_code",
                "card_code",
            )
        )

    def load_payload(
        self,
        *,
        source_entry: MiruSourceEntry,
        card_code: str,
        set_code: str = "",
        payload: dict[str, Any] | list[Any] | None = None,
        snapshot_path: str | Path | None = None,
        snapshot_url: str | None = None,
        endpoint_family: str = "",
        filters: dict[str, Any] | None = None,
        allow_catalog_pull: bool = False,
    ) -> tuple[Any, str, str]:
        if payload is not None:
            return payload, "", self._resolve_endpoint_family(
                requested_family=endpoint_family,
                card_code=card_code,
                set_code=set_code,
            )
        if snapshot_path:
            return self.from_path(snapshot_path), str(snapshot_path), self._resolve_endpoint_family(
                requested_family=endpoint_family,
                card_code=card_code,
                set_code=set_code,
            )
        cleaned_filters = self._sanitize_filters(filters)
        resolved_family, request_url = self._build_request_url(
            source_entry=source_entry,
            card_code=card_code,
            set_code=set_code,
            snapshot_url=str(snapshot_url or "").strip(),
            endpoint_family=endpoint_family,
            filters=cleaned_filters,
            allow_catalog_pull=allow_catalog_pull,
        )
        try:
            payload = self.fetch_payload_with_cache(
                source_entry=source_entry,
                url=request_url,
                request_kind=str(self._ENDPOINT_SPECS[resolved_family]["request_kind"]),
            )
        except SourceAdapterError as exc:
            if "HTTPError while fetching OPTCG API payload: 404" in str(exc):
                self.cache.store_json(
                    source_entry=source_entry,
                    request_kind=str(self._ENDPOINT_SPECS[resolved_family]["request_kind"]),
                    request_target=request_url,
                    response_payload={"payload": []},
                    ttl_seconds=21600,
                )
                return [], request_url, resolved_family
            raise
        return payload, request_url, resolved_family

    def fetch_endpoint_payload(
        self,
        *,
        source_entry: MiruSourceEntry,
        endpoint_family: str,
        card_code: str = "",
        set_code: str = "",
        filters: dict[str, Any] | None = None,
        payload: dict[str, Any] | list[Any] | None = None,
        snapshot_path: str | Path | None = None,
        snapshot_url: str | None = None,
        allow_catalog_pull: bool = False,
    ) -> dict[str, Any]:
        loaded_payload, request_url, resolved_family = self.load_payload(
            source_entry=source_entry,
            card_code=card_code,
            set_code=set_code,
            payload=payload,
            snapshot_path=snapshot_path,
            snapshot_url=snapshot_url,
            endpoint_family=endpoint_family,
            filters=filters,
            allow_catalog_pull=allow_catalog_pull,
        )
        rows = self._rows_from_payload(loaded_payload)
        return {
            "endpoint_family": resolved_family,
            "request_url": request_url,
            "row_count": len(rows),
            "payload": loaded_payload,
            "filters": self._sanitize_filters(filters),
        }

    @staticmethod
    def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            value = payload.get("value")
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if all(key in payload for key in ("card_set_id", "card_name")):
                return [payload]
        return []

    @staticmethod
    def _sort_key(item: dict[str, Any], *, target_code: str) -> tuple[int, int, int]:
        row_code = str(item.get("card_set_id") or "").strip().upper()
        image_id = str(item.get("card_image_id") or "").strip().lower()
        name = str(item.get("card_name") or "").strip().lower()
        is_parallel = 1 if ("parallel" in name or "_p" in image_id) else 0
        missing_text = 1 if not str(item.get("card_text") or "").strip() else 0
        return (0 if row_code == target_code else 1, is_parallel, missing_text)

    def fetch_records(
        self,
        *,
        source_entry: MiruSourceEntry,
        card_code: str = "",
        set_code: str = "",
        payload: dict[str, Any] | list[Any] | None = None,
        snapshot_path: str | Path | None = None,
        snapshot_url: str | None = None,
    ) -> list[NormalizedSourceRecord]:
        target_code = str(card_code or "").strip().upper()
        task_spec = dict(payload) if self._looks_like_endpoint_spec(payload) else {}
        inline_payload = None if task_spec else payload
        resolved_set_code = str(task_spec.get("set_code") or set_code or "").strip().upper()
        endpoint_family = str(task_spec.get("endpoint_family") or "").strip()
        filters = task_spec.get("filters") if isinstance(task_spec.get("filters"), dict) else None
        allow_catalog_pull = bool(task_spec.get("allow_catalog_pull"))
        batch_limit = int(task_spec.get("batch_limit") or 0) if str(task_spec.get("batch_limit") or "").strip() else 0
        payload_snapshot_url = str(task_spec.get("snapshot_url") or snapshot_url or "").strip()
        allow_bulk_snapshot = bool(inline_payload is not None or snapshot_path)
        if not target_code and not allow_bulk_snapshot:
            requested_family = self._resolve_endpoint_family(
                requested_family=endpoint_family,
                card_code=target_code,
                set_code=resolved_set_code,
            )
            if requested_family not in {
                "all-sets",
                "all-decks",
                "set-cards",
                "deck-cards",
                "filtered-set-cards",
                "filtered-st-cards",
                "filtered-promos",
                "filtered-don",
                "all-set-cards",
                "all-st-cards",
                "all-promo-cards",
                "all-don-cards",
            }:
                raise SourceAdapterError("OPTCG API adapter requires a specific card_code for bounded lookup.")
        loaded_payload, request_url, resolved_family = self.load_payload(
            source_entry=source_entry,
            card_code=target_code or "BULK-SNAPSHOT",
            set_code=resolved_set_code,
            payload=inline_payload,
            snapshot_path=snapshot_path,
            snapshot_url=payload_snapshot_url,
            endpoint_family=endpoint_family,
            filters=filters,
            allow_catalog_pull=allow_catalog_pull,
        )
        if resolved_family in self._NON_CARD_ENDPOINT_FAMILIES:
            raise SourceAdapterError(
                f"OPTCG API endpoint '{resolved_family}' returns source index metadata, not card records."
            )
        rows = sorted(
            self._rows_from_payload(loaded_payload),
            key=lambda item: self._sort_key(item, target_code=target_code),
        )
        target_set = str(resolved_set_code or "").strip().upper()
        results: list[NormalizedSourceRecord] = []
        seen: set[tuple[str, str]] = set()
        for item in rows:
            item_code = str(item.get("card_set_id") or "").strip().upper()
            item_set = self._normalize_set_code(item.get("set_id"))
            if target_code and item_code != target_code:
                continue
            if target_set and item_set and item_set != target_set:
                continue
            source_reference = str(item.get("card_image_id") or item_code).strip()
            key = (item_code, source_reference)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                NormalizedSourceRecord(
                    card_code=item_code,
                    card_name=str(item.get("card_name") or "").strip(),
                    set_code=item_set,
                    set_name=str(item.get("set_name") or "").strip(),
                    rarity=str(item.get("rarity") or "").strip(),
                    color=str(item.get("card_color") or "").strip(),
                    card_type=str(item.get("card_type") or "").strip(),
                    cost=str(item.get("card_cost") or "").strip(),
                    power=str(item.get("card_power") or "").strip(),
                    counter=str(item.get("counter_amount") or "").strip(),
                    attribute=str(item.get("attribute") or "").strip(),
                    traits=self._normalize_traits_from_subtypes(item.get("sub_types")),
                    life=str(item.get("life") or "").strip().replace("NULL", ""),
                    effect_text=str(item.get("card_text") or "").strip(),
                    trigger_text="",
                    source_id=str(source_entry.source_id or "").strip().lower(),
                    source_url=request_url or str(source_entry.base_url or "").strip(),
                    source_reference=source_reference,
                    fetched_at=_normalize_timestamp(item.get("date_scraped")),
                    illustrator="",
                )
            )
            if batch_limit > 0 and len(results) >= max(min(batch_limit, 250), 1):
                break
        return results


def _metadata_texts(value: Any) -> list[str]:
    if isinstance(value, dict):
        texts: list[str] = []
        for item in value.values():
            texts.extend(_metadata_texts(item))
        return texts
    if isinstance(value, (list, tuple)):
        texts: list[str] = []
        for item in value:
            texts.extend(_metadata_texts(item))
        return texts
    text = str(value or "").strip()
    return [text] if text else []


def _looks_like_non_live_official_payload(*values: Any) -> bool:
    tokens = ("template", "example", "sample", "timing_test", "test-only", "fixture")
    joined = " ".join(part.lower() for value in values for part in _metadata_texts(value))
    if not joined:
        return False
    if "example.com" in joined:
        return True
    return any(token in joined for token in tokens)


class OfficialStructuredSourceAdapter:
    adapter_id = "official-structured-source"

    def __init__(self, *, cache: MiruSourceCache | None = None) -> None:
        self.cache = cache or MiruSourceCache()

    @classmethod
    def from_path(cls, path: str | Path) -> dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def from_url(cls, url: str) -> dict[str, Any]:
        try:
            with urlopen(url, timeout=10.0) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise SourceAdapterError(f"HTTPError while fetching structured source snapshot: {exc.code}") from exc
        except URLError as exc:
            raise SourceAdapterError(f"URLError while fetching structured source snapshot: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise SourceAdapterError(f"Structured source snapshot was not valid JSON: {exc}") from exc

    def fetch_payload_with_cache(self, *, source_entry: MiruSourceEntry, url: str, request_kind: str) -> dict[str, Any]:
        if not source_entry.public_data_only or source_entry.requires_login or source_entry.allow_aggressive_crawling:
            raise SourceAdapterError(
                f"Source policy does not allow automated fetches for {source_entry.source_id}."
            )
        cached = self.cache.fetch_json(
            source_id=source_entry.source_id,
            request_kind=request_kind,
            request_target=url,
        )
        if cached is not None:
            return cached
        payload = self.from_url(url)
        self.cache.store_json(
            source_entry=source_entry,
            request_kind=request_kind,
            request_target=url,
            response_payload=payload,
            ttl_seconds=max(int(source_entry.request_spacing_seconds * 3600), 21600),
        )
        self.cache.record_external_request(source_entry=source_entry)
        return payload

    def load_payload(
        self,
        *,
        source_entry: MiruSourceEntry,
        payload: dict[str, Any] | None = None,
        snapshot_path: str | Path | None = None,
        snapshot_url: str | None = None,
    ) -> dict[str, Any]:
        if payload is not None:
            return payload
        if snapshot_path:
            return self.from_path(snapshot_path)
        resolved_url = str(snapshot_url or source_entry.snapshot_url or "").strip()
        if resolved_url:
            return self.fetch_payload_with_cache(
                source_entry=source_entry,
                url=resolved_url,
                request_kind="snapshot-json",
            )
        raise SourceAdapterError(
            "OfficialStructuredSourceAdapter requires payload, snapshot_path, or snapshot_url."
        )

    @staticmethod
    def _pick(*values: Any) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def _build_record(
        self,
        *,
        source_entry: MiruSourceEntry,
        card_code: str,
        source_reference: str,
        fetched_at: str,
        source_url: str = "",
        card_name: str = "",
        set_code: str = "",
        set_name: str = "",
        rarity: str = "",
        color: str = "",
        card_type: str = "",
        cost: str = "",
        power: str = "",
        counter: str = "",
        attribute: str = "",
        traits: Any = None,
        life: str = "",
        effect_text: str = "",
        trigger_text: str = "",
        illustrator: str = "",
    ) -> NormalizedSourceRecord:
        return NormalizedSourceRecord(
            card_code=str(card_code or "").strip().upper(),
            card_name=str(card_name or "").strip(),
            set_code=str(set_code or "").strip().upper(),
            set_name=str(set_name or "").strip(),
            rarity=str(rarity or "").strip(),
            color=str(color or "").strip(),
            card_type=str(card_type or "").strip(),
            cost=str(cost or "").strip(),
            power=str(power or "").strip(),
            counter=str(counter or "").strip(),
            attribute=str(attribute or "").strip(),
            traits=_normalize_traits(traits),
            life=str(life or "").strip(),
            effect_text=str(effect_text or "").strip(),
            trigger_text=str(trigger_text or "").strip(),
            source_id=str(source_entry.source_id or "").strip().lower(),
            source_url=str(source_url or source_entry.base_url or "").strip(),
            source_reference=str(source_reference or card_code).strip(),
            fetched_at=_normalize_timestamp(fetched_at),
            illustrator=str(illustrator or "").strip(),
        )

    def _normalize_deck_feature_records(
        self,
        *,
        source_entry: MiruSourceEntry,
        snapshot: dict[str, Any],
        card_code: str,
        set_code: str,
    ) -> list[NormalizedSourceRecord]:
        meta = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
        entries = snapshot.get("features") or snapshot.get("decks") or []
        target_code = str(card_code or "").strip().upper()
        target_set = str(set_code or "").strip().upper()
        results: list[NormalizedSourceRecord] = []
        seen: set[tuple[str, str]] = set()
        for feature in entries:
            if not isinstance(feature, dict):
                continue
            if _looks_like_non_live_official_payload(meta, feature):
                continue
            feature_ref = self._pick(feature.get("feature_id"), feature.get("source_reference"), feature.get("slug"), feature.get("title"))
            feature_url = self._pick(feature.get("source_url"), feature.get("url"), meta.get("base_url"), source_entry.base_url)
            fetched_at = self._pick(feature.get("published_at"), feature.get("updated_at"), meta.get("snapshot_taken_at"))
            leader_block = feature.get("leader") if isinstance(feature.get("leader"), dict) else {}
            leader_code = self._pick(feature.get("leader_code"), feature.get("leader_card_code"), leader_block.get("card_code")).upper()
            leader_set = self._pick(feature.get("set_code"), leader_block.get("set_code")).upper()
            if (not target_code or target_code == leader_code) and (not target_set or not leader_set or leader_set == target_set):
                key = (leader_code, feature_ref or leader_code)
                if leader_code and key not in seen:
                    seen.add(key)
                    results.append(
                        self._build_record(
                            source_entry=source_entry,
                            card_code=leader_code,
                            source_reference=feature_ref or leader_code,
                            fetched_at=fetched_at,
                            source_url=feature_url,
                            card_name=self._pick(feature.get("leader_name"), leader_block.get("card_name")),
                            set_code=leader_set,
                            set_name=self._pick(feature.get("set_name"), leader_block.get("set_name")),
                            color=self._pick(feature.get("color"), leader_block.get("color")),
                            card_type=self._pick(feature.get("card_type"), leader_block.get("card_type"), "Leader"),
                            traits=leader_block.get("traits") or feature.get("traits") or [],
                            life=self._pick(feature.get("life"), leader_block.get("life")),
                        )
                    )
            for item in feature.get("cards") or feature.get("featured_cards") or feature.get("staples") or []:
                if isinstance(item, dict):
                    item_code = self._pick(item.get("card_code"), item.get("code")).upper()
                    item_set = self._pick(item.get("set_code"), feature.get("set_code")).upper()
                    item_name = self._pick(item.get("card_name"), item.get("name"))
                    item_ref = self._pick(item.get("source_reference"), feature_ref, item_code)
                    item_url = self._pick(item.get("source_url"), feature_url)
                    item_effect = self._pick(item.get("effect_text"), item.get("official_text"))
                    item_trigger = self._pick(item.get("trigger_text"))
                    item_traits = item.get("traits") or []
                    item_type = self._pick(item.get("card_type"))
                else:
                    item_code = str(item or "").strip().upper()
                    item_set = str(feature.get("set_code") or "").strip().upper()
                    item_name = ""
                    item_ref = feature_ref or item_code
                    item_url = feature_url
                    item_effect = ""
                    item_trigger = ""
                    item_traits = []
                    item_type = ""
                if not item_code:
                    continue
                if target_code and item_code != target_code:
                    continue
                if target_set and item_set and item_set != target_set:
                    continue
                key = (item_code, item_ref or item_code)
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    self._build_record(
                        source_entry=source_entry,
                        card_code=item_code,
                        source_reference=item_ref or item_code,
                        fetched_at=fetched_at,
                        source_url=item_url,
                        card_name=item_name,
                        set_code=item_set,
                        set_name=self._pick(feature.get("set_name")),
                        rarity=self._pick(item.get("rarity") if isinstance(item, dict) else ""),
                        color=self._pick(item.get("color") if isinstance(item, dict) else ""),
                        card_type=item_type,
                        cost=self._pick(item.get("cost") if isinstance(item, dict) else ""),
                        power=self._pick(item.get("power") if isinstance(item, dict) else ""),
                        counter=self._pick(item.get("counter") if isinstance(item, dict) else ""),
                        attribute=self._pick(item.get("attribute") if isinstance(item, dict) else ""),
                        traits=item_traits,
                        life=self._pick(item.get("life") if isinstance(item, dict) else ""),
                        effect_text=item_effect,
                        trigger_text=item_trigger,
                        illustrator=self._pick(item.get("illustrator") if isinstance(item, dict) else "", item.get("artist_credit") if isinstance(item, dict) else ""),
                    )
                )
        return results

    def _normalize_rules_faq_records(
        self,
        *,
        source_entry: MiruSourceEntry,
        snapshot: dict[str, Any],
        card_code: str,
        set_code: str,
    ) -> list[NormalizedSourceRecord]:
        meta = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
        entries = snapshot.get("rulings") or snapshot.get("entries") or []
        target_code = str(card_code or "").strip().upper()
        target_set = str(set_code or "").strip().upper()
        results: list[NormalizedSourceRecord] = []
        seen: set[tuple[str, str]] = set()
        for item in entries:
            if not isinstance(item, dict):
                continue
            if _looks_like_non_live_official_payload(meta, item):
                continue
            item_code = self._pick(item.get("card_code"), item.get("code")).upper()
            item_set = self._pick(item.get("set_code")).upper()
            if not item_code:
                continue
            if target_code and item_code != target_code:
                continue
            if target_set and item_set and item_set != target_set:
                continue
            source_reference = self._pick(item.get("ruling_id"), item.get("source_reference"), item.get("source_anchor"), item_code)
            key = (item_code, source_reference)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                self._build_record(
                    source_entry=source_entry,
                    card_code=item_code,
                    source_reference=source_reference,
                    fetched_at=self._pick(item.get("effective_at"), item.get("published_at"), meta.get("snapshot_taken_at")),
                    source_url=self._pick(item.get("source_url"), meta.get("base_url"), source_entry.base_url),
                    card_name=self._pick(item.get("card_name")),
                    set_code=item_set,
                    set_name=self._pick(item.get("set_name")),
                    effect_text=self._pick(item.get("effect_text"), item.get("updated_text")),
                    trigger_text=self._pick(item.get("trigger_text")),
                )
            )
        return results

    def _normalize_restriction_notice_records(
        self,
        *,
        source_entry: MiruSourceEntry,
        snapshot: dict[str, Any],
        card_code: str,
        set_code: str,
    ) -> list[NormalizedSourceRecord]:
        meta = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
        notices = snapshot.get("notices") or ([snapshot] if snapshot.get("affected_cards") else [])
        target_code = str(card_code or "").strip().upper()
        target_set = str(set_code or "").strip().upper()
        results: list[NormalizedSourceRecord] = []
        seen: set[tuple[str, str]] = set()
        for notice in notices:
            if not isinstance(notice, dict):
                continue
            if _looks_like_non_live_official_payload(meta, notice):
                continue
            notice_reference = self._pick(notice.get("notice_id"), notice.get("source_reference"), notice.get("title"))
            notice_url = self._pick(notice.get("source_url"), meta.get("base_url"), source_entry.base_url)
            fetched_at = self._pick(notice.get("effective_at"), notice.get("published_at"), meta.get("snapshot_taken_at"))
            for item in notice.get("affected_cards") or []:
                if not isinstance(item, dict):
                    continue
                item_code = self._pick(item.get("card_code"), item.get("code")).upper()
                item_set = self._pick(item.get("set_code")).upper()
                if not item_code:
                    continue
                if target_code and item_code != target_code:
                    continue
                if target_set and item_set and item_set != target_set:
                    continue
                source_reference = self._pick(item.get("source_reference"), notice_reference, item_code)
                key = (item_code, source_reference)
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    self._build_record(
                        source_entry=source_entry,
                        card_code=item_code,
                        source_reference=source_reference,
                        fetched_at=self._pick(item.get("effective_at"), fetched_at),
                        source_url=self._pick(item.get("source_url"), notice_url),
                        card_name=self._pick(item.get("card_name")),
                        set_code=item_set,
                        set_name=self._pick(item.get("set_name")),
                    )
                )
        return results

    def _normalize_errata_records(
        self,
        *,
        source_entry: MiruSourceEntry,
        snapshot: dict[str, Any],
        card_code: str,
        set_code: str,
    ) -> list[NormalizedSourceRecord]:
        meta = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
        target_code = str(card_code or "").strip().upper()
        target_set = str(set_code or "").strip().upper()
        results: list[NormalizedSourceRecord] = []
        seen: set[tuple[str, str]] = set()

        entries: list[dict[str, Any]] = []
        for item in snapshot.get("cards") or []:
            if isinstance(item, dict):
                entries.append(item)
        for item in snapshot.get("rulings") or []:
            if isinstance(item, dict):
                entries.append(item)
        for notice in snapshot.get("notices") or []:
            if not isinstance(notice, dict):
                continue
            for card in notice.get("affected_cards") or []:
                if isinstance(card, dict):
                    entries.append({**card, "notice_id": notice.get("notice_id"), "source_reference": notice.get("source_reference"), "source_url": notice.get("source_url"), "effective_at": card.get("effective_at") or notice.get("effective_at"), "published_at": notice.get("published_at")})

        for item in entries:
            if _looks_like_non_live_official_payload(meta, item):
                continue
            item_code = self._pick(item.get("card_code"), item.get("code")).upper()
            item_set = self._pick(item.get("set_code")).upper()
            if not item_code:
                continue
            if target_code and item_code != target_code:
                continue
            if target_set and item_set and item_set != target_set:
                continue
            source_reference = self._pick(item.get("ruling_id"), item.get("notice_id"), item.get("source_reference"), item_code)
            key = (item_code, source_reference)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                self._build_record(
                    source_entry=source_entry,
                    card_code=item_code,
                    source_reference=source_reference,
                    fetched_at=self._pick(item.get("effective_at"), item.get("published_at"), meta.get("snapshot_taken_at")),
                    source_url=self._pick(item.get("source_url"), meta.get("base_url"), source_entry.base_url),
                    card_name=self._pick(item.get("card_name")),
                    set_code=item_set,
                    set_name=self._pick(item.get("set_name")),
                    effect_text=self._pick(item.get("updated_text"), item.get("effect_text")),
                    trigger_text=self._pick(item.get("trigger_text")),
                )
            )
        return results

    def fetch_records(
        self,
        *,
        source_entry: MiruSourceEntry,
        card_code: str = "",
        set_code: str = "",
        payload: dict[str, Any] | None = None,
        snapshot_path: str | Path | None = None,
        snapshot_url: str | None = None,
    ) -> list[NormalizedSourceRecord]:
        snapshot = self.load_payload(
            source_entry=source_entry,
            payload=payload,
            snapshot_path=snapshot_path,
            snapshot_url=snapshot_url,
        )
        source_id = str(source_entry.source_id or "").strip().lower()
        if source_id == "official-deck-features":
            return self._normalize_deck_feature_records(
                source_entry=source_entry,
                snapshot=snapshot,
                card_code=card_code,
                set_code=set_code,
            )
        if source_id == "official-rules-faq":
            return self._normalize_rules_faq_records(
                source_entry=source_entry,
                snapshot=snapshot,
                card_code=card_code,
                set_code=set_code,
            )
        if source_id == "official-restriction-notices":
            return self._normalize_restriction_notice_records(
                source_entry=source_entry,
                snapshot=snapshot,
                card_code=card_code,
                set_code=set_code,
            )
        if source_id == "official-errata-cards":
            return self._normalize_errata_records(
                source_entry=source_entry,
                snapshot=snapshot,
                card_code=card_code,
                set_code=set_code,
            )
        return []


class CommunityStructuredSourceAdapter:
    adapter_id = "community-structured"

    def __init__(self, *, cache: MiruSourceCache | None = None) -> None:
        self.cache = cache or MiruSourceCache()

    @classmethod
    def from_path(cls, path: str | Path) -> Any:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def from_url(cls, url: str) -> Any:
        try:
            with urlopen(url, timeout=10.0) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise SourceAdapterError(f"HTTPError while fetching community source snapshot: {exc.code}") from exc
        except URLError as exc:
            raise SourceAdapterError(f"URLError while fetching community source snapshot: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise SourceAdapterError(f"Community source snapshot was not valid JSON: {exc}") from exc

    def fetch_payload_with_cache(
        self,
        *,
        source_entry: MiruSourceEntry,
        url: str,
        request_kind: str,
    ) -> Any:
        if not source_entry.public_data_only or source_entry.requires_login or source_entry.allow_aggressive_crawling:
            raise SourceAdapterError(
                f"Source policy does not allow automated fetches for {source_entry.source_id}."
            )
        cached = self.cache.fetch_json(
            source_id=source_entry.source_id,
            request_kind=request_kind,
            request_target=url,
        )
        if cached is not None:
            if "payload" in cached:
                return cached.get("payload")
            return cached
        payload = self.from_url(url)
        cache_payload = payload if isinstance(payload, dict) else {"payload": payload}
        self.cache.store_json(
            source_entry=source_entry,
            request_kind=request_kind,
            request_target=url,
            response_payload=cache_payload,
            ttl_seconds=max(int(source_entry.request_spacing_seconds * 3600), 21600),
        )
        self.cache.record_external_request(source_entry=source_entry)
        return payload

    def load_payload(
        self,
        *,
        source_entry: MiruSourceEntry,
        payload: dict[str, Any] | list[Any] | None = None,
        snapshot_path: str | Path | None = None,
        snapshot_url: str | None = None,
    ) -> Any:
        if payload is not None:
            return payload
        if snapshot_path:
            return self.from_path(snapshot_path)
        resolved_url = str(snapshot_url or source_entry.snapshot_url or "").strip()
        if resolved_url:
            return self.fetch_payload_with_cache(
                source_entry=source_entry,
                url=resolved_url,
                request_kind="community-snapshot-json",
            )
        raise SourceAdapterError(
            "CommunityStructuredSourceAdapter requires payload, snapshot_path, or snapshot_url."
        )

    @staticmethod
    def _pick(*values: Any) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _card_code(value: Any) -> str:
        return str(value or "").strip().upper()

    @staticmethod
    def _looks_like_card_dict(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        return bool(
            str(item.get("card_code") or item.get("code") or item.get("card_set_id") or "").strip()
        )

    @classmethod
    def _normalize_card_item(
        cls,
        item: dict[str, Any],
        *,
        source_entry: MiruSourceEntry,
        source_url: str,
        source_reference: str,
        fetched_at: str,
        default_set_code: str = "",
        default_set_name: str = "",
    ) -> NormalizedSourceRecord | None:
        card_code = cls._card_code(item.get("card_code") or item.get("code") or item.get("card_set_id"))
        if not card_code:
            return None
        card_name = cls._pick(item.get("card_name"), item.get("name"))
        resolved_set_code = cls._pick(item.get("set_code"), item.get("set_id"), default_set_code).upper().replace("-", "")
        effect_text = cls._pick(item.get("effect_text"), item.get("official_text"), item.get("card_text"))
        if not any(
            (
                card_name,
                resolved_set_code,
                effect_text,
                cls._pick(item.get("card_type"), item.get("type")),
                cls._pick(item.get("color"), item.get("card_color")),
            )
        ):
            return None
        return NormalizedSourceRecord(
            card_code=card_code,
            card_name=card_name,
            set_code=resolved_set_code,
            set_name=cls._pick(item.get("set_name"), default_set_name),
            rarity=cls._pick(item.get("rarity")),
            color=cls._pick(item.get("color"), item.get("card_color")),
            card_type=cls._pick(item.get("card_type"), item.get("type")),
            cost=cls._pick(item.get("cost"), item.get("card_cost")),
            power=cls._pick(item.get("power"), item.get("card_power")),
            counter=cls._pick(item.get("counter"), item.get("counter_amount")),
            attribute=cls._pick(item.get("attribute")),
            traits=_normalize_traits(item.get("traits") or item.get("sub_types") or item.get("trait")),
            life=cls._pick(item.get("life")),
            effect_text=effect_text,
            trigger_text=cls._pick(item.get("trigger_text")),
            source_id=str(source_entry.source_id or "").strip().lower(),
            source_url=str(item.get("source_url") or source_url).strip(),
            source_reference=cls._pick(item.get("source_reference"), item.get("id"), source_reference, card_code),
            fetched_at=_normalize_timestamp(item.get("updated_at") or item.get("date_scraped") or fetched_at),
            illustrator=cls._pick(item.get("illustrator"), item.get("artist_credit")),
        )

    @classmethod
    def _iter_decks(cls, snapshot: Any) -> list[dict[str, Any]]:
        if not isinstance(snapshot, dict):
            return []
        decks = snapshot.get("decklists") or snapshot.get("decks") or snapshot.get("entries") or snapshot.get("results") or []
        return [item for item in decks if isinstance(item, dict)]

    @classmethod
    def _iter_cards(cls, snapshot: Any) -> list[dict[str, Any]]:
        if not isinstance(snapshot, dict):
            return []
        cards = snapshot.get("cards") or snapshot.get("catalog") or snapshot.get("entries") or []
        return [item for item in cards if cls._looks_like_card_dict(item)]

    @classmethod
    def _role_classification(cls, item: dict[str, Any]) -> str:
        explicit = str(item.get("role_classification") or item.get("role") or "").strip().lower()
        if explicit in {"core", "staple", "flex", "tech", "support"}:
            return explicit
        try:
            count = int(item.get("count") or item.get("quantity") or item.get("qty") or 1)
        except (TypeError, ValueError):
            count = 1
        if count >= 4:
            return "core"
        if count >= 3:
            return "staple"
        if count == 2:
            return "flex"
        return "tech"

    @classmethod
    def _confidence_for_usage(cls, *, source_id: str, placement: int, quantity: int) -> float:
        base = 0.52 if source_id == "limitless" else 0.48 if source_id == "optcg-gg" else 0.42
        if placement and placement <= 8:
            base += 0.08
        if quantity >= 4:
            base += 0.04
        return round(min(base, 0.72), 2)

    def fetch_records(
        self,
        *,
        source_entry: MiruSourceEntry,
        card_code: str = "",
        set_code: str = "",
        payload: dict[str, Any] | list[Any] | None = None,
        snapshot_path: str | Path | None = None,
        snapshot_url: str | None = None,
    ) -> list[NormalizedSourceRecord]:
        snapshot = self.load_payload(
            source_entry=source_entry,
            payload=payload,
            snapshot_path=snapshot_path,
            snapshot_url=snapshot_url,
        )
        target_code = str(card_code or "").strip().upper()
        target_set = str(set_code or "").strip().upper()
        meta = snapshot.get("source") if isinstance(snapshot, dict) and isinstance(snapshot.get("source"), dict) else {}
        fetched_at = self._pick(meta.get("snapshot_taken_at"), time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))
        default_url = self._pick(meta.get("base_url"), source_entry.base_url)

        records: list[NormalizedSourceRecord] = []
        seen: set[tuple[str, str]] = set()
        for item in self._iter_cards(snapshot):
            record = self._normalize_card_item(
                item,
                source_entry=source_entry,
                source_url=default_url,
                source_reference=self._pick(item.get("id"), item.get("card_code"), item.get("code")),
                fetched_at=fetched_at,
            )
            if record is None:
                continue
            if target_code and record.card_code != target_code:
                continue
            if target_set and record.set_code and record.set_code != target_set:
                continue
            key = (record.card_code, record.source_reference)
            if key in seen:
                continue
            seen.add(key)
            records.append(record)

        for deck in self._iter_decks(snapshot):
            deck_url = self._pick(deck.get("source_url"), deck.get("url"), default_url)
            deck_ref = self._pick(deck.get("source_reference"), deck.get("decklist_key"), deck.get("id"), deck.get("slug"))
            deck_set = self._pick(deck.get("set_code"))
            deck_set_name = self._pick(deck.get("set_name"))
            leader = deck.get("leader") if isinstance(deck.get("leader"), dict) else {}
            leader_item = leader if self._looks_like_card_dict(leader) else {}
            if leader_item:
                record = self._normalize_card_item(
                    leader_item,
                    source_entry=source_entry,
                    source_url=deck_url,
                    source_reference=deck_ref,
                    fetched_at=self._pick(deck.get("observed_at"), deck.get("updated_at"), fetched_at),
                    default_set_code=deck_set,
                    default_set_name=deck_set_name,
                )
                if record is not None and (not target_code or record.card_code == target_code):
                    key = (record.card_code, record.source_reference)
                    if key not in seen:
                        seen.add(key)
                        records.append(record)
            for item in deck.get("cards") or deck.get("decklist") or deck.get("main_deck") or []:
                if not isinstance(item, dict):
                    item = {"card_code": str(item or "").strip().upper()}
                record = self._normalize_card_item(
                    item,
                    source_entry=source_entry,
                    source_url=deck_url,
                    source_reference=deck_ref,
                    fetched_at=self._pick(deck.get("observed_at"), deck.get("updated_at"), fetched_at),
                    default_set_code=deck_set,
                    default_set_name=deck_set_name,
                )
                if record is None:
                    continue
                if target_code and record.card_code != target_code:
                    continue
                if target_set and record.set_code and record.set_code != target_set:
                    continue
                key = (record.card_code, record.source_reference)
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)
        return records

    def extract_usage_signals(
        self,
        *,
        source_entry: MiruSourceEntry,
        payload: dict[str, Any] | list[Any] | None = None,
        snapshot_path: str | Path | None = None,
        snapshot_url: str | None = None,
    ) -> list[dict[str, Any]]:
        snapshot = self.load_payload(
            source_entry=source_entry,
            payload=payload,
            snapshot_path=snapshot_path,
            snapshot_url=snapshot_url,
        )
        meta = snapshot.get("source") if isinstance(snapshot, dict) and isinstance(snapshot.get("source"), dict) else {}
        signals: list[dict[str, Any]] = []
        seen: set[str] = set()
        for deck in self._iter_decks(snapshot):
            leader = deck.get("leader") if isinstance(deck.get("leader"), dict) else {}
            leader_code = self._card_code(deck.get("leader_code") or deck.get("leader_card_code") or leader.get("card_code"))
            leader_name = self._pick(deck.get("leader_name"), leader.get("card_name"), deck.get("leader"))
            archetype_label = self._pick(deck.get("archetype_label"), deck.get("archetype"), deck.get("meta_label"), deck.get("title"))
            event_key = self._pick(deck.get("event_key"), deck.get("tournament_key"), meta.get("event_key"))
            event_name = self._pick(deck.get("event_name"), deck.get("tournament_name"), meta.get("event_name"))
            decklist_key = self._pick(deck.get("decklist_key"), deck.get("deck_id"), deck.get("slug"), deck.get("id"))
            source_reference = self._pick(deck.get("source_reference"), decklist_key, event_key, event_name)
            source_url = self._pick(deck.get("source_url"), deck.get("url"), meta.get("base_url"), source_entry.base_url)
            observed_at = self._pick(deck.get("observed_at"), deck.get("updated_at"), deck.get("event_date"), meta.get("snapshot_taken_at"))
            try:
                placement = int(deck.get("placement") or deck.get("rank") or deck.get("standing") or 0)
            except (TypeError, ValueError):
                placement = 0
            for item in deck.get("cards") or deck.get("decklist") or deck.get("main_deck") or []:
                if not isinstance(item, dict):
                    item = {"card_code": str(item or "").strip().upper()}
                card_code = self._card_code(item.get("card_code") or item.get("code") or item.get("card_set_id"))
                if not card_code or (leader_code and card_code == leader_code):
                    continue
                try:
                    quantity = int(item.get("count") or item.get("quantity") or item.get("qty") or 1)
                except (TypeError, ValueError):
                    quantity = 1
                signal_key = json.dumps(
                    {
                        "source_id": str(source_entry.source_id or "").strip().lower(),
                        "decklist_key": decklist_key,
                        "card_code": card_code,
                        "leader_code": leader_code,
                        "archetype": archetype_label,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
                if signal_key in seen:
                    continue
                seen.add(signal_key)
                signals.append(
                    {
                        "source_id": str(source_entry.source_id or "").strip().lower(),
                        "source_type": str(source_entry.source_type or "").strip().lower(),
                        "source_url": source_url,
                        "source_reference": source_reference or card_code,
                        "event_key": event_key,
                        "event_name": event_name,
                        "placement": placement,
                        "decklist_key": decklist_key or source_reference or card_code,
                        "card_code": card_code,
                        "leader_code": leader_code,
                        "leader_name": leader_name,
                        "archetype_label": archetype_label,
                        "role_classification": self._role_classification(item),
                        "appearance_count": max(quantity, 1),
                        "confidence_input": self._confidence_for_usage(
                            source_id=str(source_entry.source_id or "").strip().lower(),
                            placement=placement,
                            quantity=quantity,
                        ),
                        "observed_at": observed_at,
                        "citation_payload": {
                            "deck_title": self._pick(deck.get("title"), deck.get("deck_name")),
                            "quantity": quantity,
                            "placement": placement,
                        },
                        "provenance": {
                            "adapter_id": self.adapter_id,
                            "snapshot_taken_at": self._pick(meta.get("snapshot_taken_at")),
                        },
                    }
                )
        return signals


class OfficialCardImageSourceAdapter:
    adapter_id = "official-card-images"

    def __init__(self, *, cache: MiruSourceCache | None = None) -> None:
        self.cache = cache or MiruSourceCache()

    @classmethod
    def from_path(cls, path: str | Path) -> dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def from_url(cls, url: str) -> dict[str, Any]:
        try:
            with urlopen(url, timeout=10.0) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise SourceAdapterError(f"HTTPError while fetching image source snapshot: {exc.code}") from exc
        except URLError as exc:
            raise SourceAdapterError(f"URLError while fetching image source snapshot: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise SourceAdapterError(f"Image source snapshot was not valid JSON: {exc}") from exc

    def fetch_payload_with_cache(self, *, source_entry: MiruSourceEntry, url: str, request_kind: str) -> dict[str, Any]:
        if not source_entry.public_data_only or source_entry.requires_login or source_entry.allow_aggressive_crawling:
            raise SourceAdapterError(
                f"Source policy does not allow automated fetches for {source_entry.source_id}."
            )
        cached = self.cache.fetch_json(
            source_id=source_entry.source_id,
            request_kind=request_kind,
            request_target=url,
        )
        if cached is not None:
            return cached
        payload = self.from_url(url)
        self.cache.store_json(
            source_entry=source_entry,
            request_kind=request_kind,
            request_target=url,
            response_payload=payload,
            ttl_seconds=max(int(source_entry.request_spacing_seconds * 3600), 21600),
        )
        self.cache.record_external_request(source_entry=source_entry)
        return payload

    def load_payload(
        self,
        *,
        source_entry: MiruSourceEntry,
        payload: dict[str, Any] | None = None,
        snapshot_path: str | Path | None = None,
        snapshot_url: str | None = None,
    ) -> dict[str, Any]:
        if payload is not None:
            return payload
        if snapshot_path:
            return self.from_path(snapshot_path)
        resolved_url = str(snapshot_url or source_entry.snapshot_url or "").strip()
        if resolved_url:
            return self.fetch_payload_with_cache(
                source_entry=source_entry,
                url=resolved_url,
                request_kind="image-snapshot-json",
            )
        raise SourceAdapterError(
            "OfficialCardImageSourceAdapter requires payload, snapshot_path, or snapshot_url."
        )

    def fetch_records(
        self,
        *,
        source_entry: MiruSourceEntry,
        card_code: str = "",
        set_code: str = "",
        variant_key: str = "",
        payload: dict[str, Any] | None = None,
        snapshot_path: str | Path | None = None,
        snapshot_url: str | None = None,
    ) -> list[NormalizedImageRecord]:
        snapshot = self.load_payload(
            source_entry=source_entry,
            payload=payload,
            snapshot_path=snapshot_path,
            snapshot_url=snapshot_url,
        )
        meta = snapshot.get("source") or {}
        images = snapshot.get("images") or snapshot.get("cards") or []

        target_code = str(card_code or "").strip().upper()
        target_set = str(set_code or "").strip().upper()
        target_variant = str(variant_key or "").strip().lower()
        results: list[NormalizedImageRecord] = []

        for item in images:
            if not isinstance(item, dict):
                continue
            item_card_code = str(item.get("card_code") or "").strip().upper()
            item_set_code = str(item.get("set_code") or "").strip().upper()
            item_variant = str(item.get("variant_key") or item.get("variant") or "").strip().lower()
            if target_code and item_card_code != target_code:
                continue
            if target_set and item_set_code != target_set:
                continue
            if target_variant and item_variant != target_variant:
                continue

            results.append(
                NormalizedImageRecord(
                    card_code=item_card_code,
                    print_id=str(item.get("print_id") or "").strip(),
                    print_label=str(item.get("print_label") or item.get("variant_label") or item.get("variant") or "").strip(),
                    variant_key=item_variant,
                    variant_label=str(item.get("variant_label") or item.get("variant") or item_variant).strip(),
                    source_id=source_entry.source_id,
                    source_type=str(source_entry.source_type or "").strip(),
                    source_url=str(item.get("image_url") or meta.get("base_url") or source_entry.base_url).strip(),
                    source_reference=str(
                        item.get("source_reference")
                        or item.get("official_image_id")
                        or item_card_code
                    ).strip(),
                    image_path=str(item.get("image_path") or "").strip(),
                    fetched_at=_normalize_timestamp(
                        item.get("last_checked_at")
                        or meta.get("snapshot_taken_at")
                        or time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
                    ),
                    width=int(item.get("width") or 0),
                    height=int(item.get("height") or 0),
                    sample_flag=bool(
                        item.get("sample")
                        or item.get("is_sample")
                        or item.get("sample_flag")
                        or ("sample" in str(item.get("image_url") or "").lower())
                        or ("sample" in str(item.get("source_reference") or "").lower())
                    ),
                    source_trust_tier=int(source_entry.trust_tier),
                    source_trust_label=str(source_entry.trust_label or ""),
                    metadata={
                        "quality_hint": str(item.get("quality_tier") or item.get("quality_hint") or "").strip(),
                        "crop_hint": str(item.get("crop_hint") or "").strip(),
                        "clarity_hint": str(item.get("clarity_hint") or "").strip(),
                        "source_name": str(item.get("source_name") or source_entry.source_name or "").strip(),
                    },
                )
            )

        return results
