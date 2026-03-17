from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
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
