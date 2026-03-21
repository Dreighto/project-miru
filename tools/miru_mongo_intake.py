from __future__ import annotations

import argparse
import hashlib
import json
import socket
import time
from datetime import datetime, timezone
from typing import Any

from tools.miru_mongo_client import MiruMongoClient, MiruMongoSettings


RAW_SNAPSHOT_COLLECTION = "raw_source_snapshots"
INTAKE_RECORD_COLLECTION = "intake_records"
SMOKE_TEST_COLLECTION = "dev_healthchecks"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _payload_hash(value: Any) -> str:
    normalized = json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()


def stage_raw_source_snapshot(
    *,
    client: MiruMongoClient,
    source_id: str,
    card_code: str = "",
    source_reference: str = "",
    source_url: str = "",
    task_type: str = "",
    execution_kind: str = "",
    status: str = "pending",
    payload: Any = None,
    task_payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    collection_name: str = RAW_SNAPSHOT_COLLECTION,
) -> dict[str, Any]:
    captured_at = _utc_now()
    payload_sha256 = _payload_hash(payload)
    document = {
        "source_id": str(source_id or "").strip().lower(),
        "card_code": str(card_code or "").strip().upper(),
        "source_reference": str(source_reference or "").strip(),
        "source_url": str(source_url or "").strip(),
        "task_type": str(task_type or "").strip(),
        "execution_kind": str(execution_kind or "").strip(),
        "status": str(status or "").strip() or "pending",
        "captured_at": captured_at,
        "updated_at": captured_at,
        "payload_sha256": payload_sha256,
        "payload": _json_safe(payload),
        "task_payload": _json_safe(task_payload or {}),
        "metadata": _json_safe(metadata or {}),
    }
    result = client.collection(collection_name).update_one(
        {
            "source_id": document["source_id"],
            "card_code": document["card_code"],
            "source_reference": document["source_reference"],
            "payload_sha256": payload_sha256,
        },
        {"$set": document, "$setOnInsert": {"created_at": captured_at}},
        upsert=True,
    )
    return {
        "collection": collection_name,
        "matched": int(result.matched_count),
        "modified": int(result.modified_count),
        "upserted_id": str(result.upserted_id or ""),
        "payload_sha256": payload_sha256,
    }


def stage_intake_record(
    *,
    client: MiruMongoClient,
    source_id: str,
    card_code: str = "",
    source_reference: str = "",
    task_type: str = "",
    execution_kind: str = "",
    status: str = "pending",
    normalized_records: list[dict[str, Any]] | None = None,
    task_payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    collection_name: str = INTAKE_RECORD_COLLECTION,
) -> dict[str, Any]:
    updated_at = _utc_now()
    normalized = [_json_safe(item) for item in (normalized_records or [])]
    context_sha256 = _payload_hash(
        {
            "source_id": source_id,
            "card_code": card_code,
            "source_reference": source_reference,
            "task_type": task_type,
            "execution_kind": execution_kind,
            "task_payload": task_payload or {},
            "normalized_records": normalized,
        }
    )
    document = {
        "source_id": str(source_id or "").strip().lower(),
        "card_code": str(card_code or "").strip().upper(),
        "source_reference": str(source_reference or "").strip(),
        "task_type": str(task_type or "").strip(),
        "execution_kind": str(execution_kind or "").strip(),
        "status": str(status or "").strip() or "pending",
        "normalized_record_count": len(normalized),
        "normalized_records": normalized,
        "task_payload": _json_safe(task_payload or {}),
        "metadata": _json_safe(metadata or {}),
        "context_sha256": context_sha256,
        "updated_at": updated_at,
    }
    result = client.collection(collection_name).update_one(
        {"context_sha256": context_sha256},
        {"$set": document, "$setOnInsert": {"created_at": updated_at}},
        upsert=True,
    )
    return {
        "collection": collection_name,
        "matched": int(result.matched_count),
        "modified": int(result.modified_count),
        "upserted_id": str(result.upserted_id or ""),
        "context_sha256": context_sha256,
    }


def stage_source_verification(
    *,
    client: MiruMongoClient,
    source_id: str,
    card_code: str,
    source_reference: str = "",
    source_url: str = "",
    task_type: str = "",
    execution_kind: str = "",
    status: str = "processed",
    raw_payload: Any = None,
    normalized_records: list[dict[str, Any]] | None = None,
    task_payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_result = stage_raw_source_snapshot(
        client=client,
        source_id=source_id,
        card_code=card_code,
        source_reference=source_reference,
        source_url=source_url,
        task_type=task_type,
        execution_kind=execution_kind,
        status=status,
        payload=raw_payload,
        task_payload=task_payload,
        metadata=metadata,
    )
    intake_result = stage_intake_record(
        client=client,
        source_id=source_id,
        card_code=card_code,
        source_reference=source_reference,
        task_type=task_type,
        execution_kind=execution_kind,
        status=status,
        normalized_records=normalized_records,
        task_payload=task_payload,
        metadata=metadata,
    )
    return {
        "enabled": True,
        "status": status,
        "raw_snapshot": raw_result,
        "intake_record": intake_result,
    }


def run_mongo_smoke_test(
    *,
    settings: MiruMongoSettings | None = None,
    cleanup: bool = True,
) -> dict[str, Any]:
    client = MiruMongoClient(settings or MiruMongoSettings.from_env(default_enabled=True))
    ping = client.ping()
    nonce = f"miru-smoke-{int(time.time() * 1000)}"
    payload = {
        "kind": "mongo_smoke_test",
        "nonce": nonce,
        "created_at": _utc_now(),
        "host": socket.gethostname(),
    }
    collection = client.collection(SMOKE_TEST_COLLECTION)
    insert_result = collection.insert_one(payload)
    found = collection.find_one({"_id": insert_result.inserted_id})
    deleted = 0
    if cleanup:
        delete_result = collection.delete_one({"_id": insert_result.inserted_id})
        deleted = int(delete_result.deleted_count)
    return {
        "ok": bool(ping.get("ok")) and found is not None,
        "uri": client.settings.uri,
        "db_name": client.settings.db_name,
        "collection": SMOKE_TEST_COLLECTION,
        "inserted_id": str(insert_result.inserted_id),
        "read_back_nonce": str((found or {}).get("nonce") or ""),
        "deleted_count": deleted,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a bounded Miru Mongo staging smoke test.")
    parser.add_argument("--uri", default="", help="Mongo URI override.")
    parser.add_argument("--db-name", default="", help="Mongo DB name override.")
    parser.add_argument("--keep", action="store_true", help="Keep the inserted smoke-test document.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = MiruMongoSettings(
        uri=str(args.uri or "").strip() or MiruMongoSettings.from_env(default_enabled=True).uri,
        db_name=str(args.db_name or "").strip() or MiruMongoSettings.from_env(default_enabled=True).db_name,
        enabled=True,
    )
    result = run_mongo_smoke_test(settings=settings, cleanup=not bool(args.keep))
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
