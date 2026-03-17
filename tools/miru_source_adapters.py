from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from tools.miru_source_registry import MiruSourceEntry


class SourceAdapterError(RuntimeError):
    pass


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedImageRecord:
    card_code: str
    variant_key: str
    source_id: str
    source_url: str
    source_reference: str
    image_path: str
    fetched_at: str
    width: int
    height: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OfficialCardListSourceAdapter:
    adapter_id = "official-cardlist"

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
            return self.from_url(resolved_url)
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
                )
            )

        return results


class OfficialCardImageSourceAdapter:
    adapter_id = "official-card-images"

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
            return self.from_url(resolved_url)
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
                    variant_key=item_variant,
                    source_id=source_entry.source_id,
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
                )
            )

        return results
