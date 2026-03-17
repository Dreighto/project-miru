from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from config.miru_storage_layout import build_storage_layout
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_DESTINATION_KEYS = ("external", "archive", "offsite")
LIGHT_SNAPSHOT_TYPES = {"light", "daily_light", "growth_guard"}
FULL_SNAPSHOT_TYPES = {"full", "weekly_full", "schema_milestone", "manual_milestone"}


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _utc_compact_timestamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _slug(value: str, *, default: str = "snapshot") -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-")
    return cleaned or default


def _path_entry(path: Path) -> dict[str, Any]:
    exists = path.exists()
    is_dir = path.is_dir() if exists else False
    size = 0
    if exists and path.is_file():
        try:
            size = int(path.stat().st_size)
        except OSError:
            size = 0
    return {
        "path": str(path),
        "exists": exists,
        "is_dir": is_dir,
        "size_bytes": size,
    }


def _directory_stats(path: Path) -> dict[str, Any]:
    if not path.is_dir():
        return {
            "path": str(path),
            "exists": False,
            "is_dir": False,
            "file_count": 0,
            "total_bytes": 0,
        }
    file_count = 0
    total_bytes = 0
    try:
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            file_count += 1
            try:
                total_bytes += int(item.stat().st_size)
            except OSError:
                continue
    except OSError:
        pass
    return {
        "path": str(path),
        "exists": True,
        "is_dir": True,
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def _destination_mapping() -> dict[str, str]:
    return {
        "external": "external_ssd",
        "external_ssd": "external_ssd",
        "ssd": "external_ssd",
        "archive": "archive_nas",
        "archive_nas": "archive_nas",
        "nas": "archive_nas",
        "offsite": "offsite_google_drive",
        "google_drive": "offsite_google_drive",
        "offsite_google_drive": "offsite_google_drive",
        "gdrive": "offsite_google_drive",
    }


def _normalized_destination_key(destination_key: str) -> str:
    return _destination_mapping().get(str(destination_key or "").strip().lower(), "")


def _snapshot_policy(snapshot_type: str) -> dict[str, Any]:
    normalized = _slug(snapshot_type, default="light")
    is_full = normalized in FULL_SNAPSHOT_TYPES
    return {
        "snapshot_type": normalized,
        "is_full": is_full,
        "include_core_dbs": True,
        "include_runtime_metadata": True,
        "include_image_dirs": ["served_image_root"] if is_full else ["served_thumbnail_root"],
        "retention_tag": "full" if is_full else "light",
    }


def _collect_core_dbs(report: dict[str, Any]) -> dict[str, Path]:
    current = report["current_runtime_paths"]
    return {
        "catalog_db": Path(current["catalog_db"]),
        "verified_dossier_db": Path(current["verified_dossier_db"]),
        "watchlist_state_db": Path(current["watchlist_state_db"]),
        "learning_queue_db": Path(current["learning_queue_db"]),
        "learning_status_db": Path(current["learning_status_db"]),
        "learning_dossier_db": Path(current["learning_dossier_db"]),
        "insight_cache_db": Path(current["insight_cache_db"]),
    }


def _collect_runtime_metadata_paths(root: Path, report: dict[str, Any]) -> dict[str, Path]:
    runtime_paths = report["recommended_runtime_paths"]
    return {
        "runtime_state": root / "data" / "startup-logs" / "op_miru_runtime_state.json",
        "operator_notifications": root / "data" / "miru_operator_notifications.json",
        "limits_status": root / "data" / "miru_limits_status.json",
        "maintenance_state": Path(runtime_paths["maintenance_state_json"]),
    }


def _collect_image_dirs(report: dict[str, Any]) -> dict[str, Path]:
    current = report["current_runtime_paths"]
    recommended_images = report["recommended_image_paths"]
    return {
        "served_image_root": Path(current["served_image_root"]),
        "served_thumbnail_root": Path(current["served_thumbnail_root"]),
        "recommended_display_root": Path(recommended_images["display_root"]),
        "recommended_full_root": Path(recommended_images["full_hot_root"]),
        "archive_full_root": Path(recommended_images["archive_full_root"]),
    }


def _resolve_manifest_destination(
    *,
    destination: str | Path | None,
    destination_key: str | None,
    layout_report: dict[str, Any],
) -> Path | None:
    if destination is not None:
        return Path(destination)
    normalized = _normalized_destination_key(str(destination_key or ""))
    if not normalized:
        return None
    resolved = dict((layout_report.get("backup_destinations") or {}).get(normalized, {}))
    preferred = (
        resolved.get("manifest_path")
        or resolved.get("snapshot_path")
        or resolved.get("brain_backup_path")
        or resolved.get("root")
        or ""
    )
    if not preferred:
        return None
    return Path(preferred)


def _destination_availability(destination_name: str, record: dict[str, str]) -> dict[str, Any]:
    root = Path(str(record.get("root") or ""))
    manifest_root = Path(str(record.get("manifest_path") or record.get("snapshot_path") or record.get("brain_backup_path") or root))
    snapshot_root = Path(str(record.get("snapshot_path") or record.get("brain_backup_path") or root))
    exists = root.is_dir()
    writable = exists and os.access(root, os.W_OK)
    return {
        "name": destination_name,
        "root": str(root),
        "exists": exists,
        "writable": writable,
        "preferred_snapshot_root": str(snapshot_root),
        "preferred_manifest_root": str(manifest_root),
    }


def _build_snapshot_id(snapshot_type: str, reason: str, *, milestone: str = "") -> str:
    suffix_parts = [_slug(snapshot_type, default="light"), _slug(reason, default="manual")]
    if milestone:
        suffix_parts.append(_slug(milestone, default="milestone"))
    return f"{_utc_compact_timestamp()}__{'__'.join(suffix_parts)}"


def _selected_assets(
    *,
    manifest: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, dict[str, dict[str, Any]]]:
    selected: dict[str, dict[str, dict[str, Any]]] = {"core_dbs": {}, "runtime_metadata": {}, "image_dirs": {}}
    if policy.get("include_core_dbs"):
        selected["core_dbs"] = dict(manifest.get("core_dbs") or {})
    if policy.get("include_runtime_metadata"):
        selected["runtime_metadata"] = dict(manifest.get("runtime_metadata") or {})
    allowed_image_keys = set(list(policy.get("include_image_dirs") or []))
    selected["image_dirs"] = {
        key: value
        for key, value in dict(manifest.get("image_dirs") or {}).items()
        if key in allowed_image_keys
    }
    return selected


def miru_brain_backup_destinations(*, project_root: Path | None = None) -> dict[str, dict[str, str]]:
    root = Path(project_root or PROJECT_ROOT)
    layout = build_storage_layout(project_root=root)
    return layout.backup_destination_paths()


def miru_brain_manifest(
    *,
    project_root: Path | None = None,
    snapshot_type: str = "planning",
    reason: str = "manual",
    destination_keys: list[str] | None = None,
    milestone: str = "",
) -> dict[str, Any]:
    root = Path(project_root or PROJECT_ROOT)
    layout = build_storage_layout(project_root=root)
    report = layout.to_report()
    policy = _snapshot_policy(snapshot_type)
    core_dbs = {name: _path_entry(path) for name, path in _collect_core_dbs(report).items()}
    runtime_metadata = {name: _path_entry(path) for name, path in _collect_runtime_metadata_paths(root, report).items()}
    image_dirs = {}
    for name, path in _collect_image_dirs(report).items():
        if path.is_dir():
            image_dirs[name] = _directory_stats(path)
        else:
            image_dirs[name] = _path_entry(path)
    backup_destinations = report["backup_destination_paths"]
    destination_availability = {
        name: _destination_availability(name, record)
        for name, record in backup_destinations.items()
    }
    requested_destinations = list(destination_keys or [])
    selected = _selected_assets(
        manifest={
            "core_dbs": core_dbs,
            "runtime_metadata": runtime_metadata,
            "image_dirs": image_dirs,
        },
        policy=policy,
    )
    return {
        "generated_at": _utc_timestamp(),
        "project_root": str(root),
        "snapshot_type": policy["snapshot_type"],
        "snapshot_reason": str(reason or "manual"),
        "snapshot_milestone": str(milestone or ""),
        "storage_roots": {
            "active_data_root": report["active_data_root"],
            "archive_root": report["archive_root"],
            "served_image_root": report["served_image_root"],
            "external_backup_root": report["backup_destination_roots"]["external_ssd_backup_root"],
            "archive_backup_root": report["backup_destination_roots"]["archive_backup_root"],
            "offsite_backup_root": report["backup_destination_roots"]["offsite_backup_root"],
        },
        "runtime_root": report["recommended_roots"]["runtime_root"],
        "backup_destinations": backup_destinations,
        "destination_availability": destination_availability,
        "requested_destinations": requested_destinations,
        "snapshot_policy": policy,
        "core_dbs": core_dbs,
        "image_dirs": image_dirs,
        "runtime_metadata": runtime_metadata,
        "included_assets": selected,
        "notes": [
            "Brain snapshots copy local state without moving live DBs or changing runtime paths.",
            "SQLite files are copied through sqlite backup for safer hot snapshots.",
            "Unavailable destinations should be reported, not allowed to fail the whole multi-destination snapshot run.",
        ],
    }


def _copy_sqlite_database(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        if destination.exists():
            destination.unlink()
        with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as src_conn:
            with closing(sqlite3.connect(destination)) as dst_conn:
                src_conn.backup(dst_conn)
                dst_conn.commit()
        size_bytes = int(destination.stat().st_size) if destination.exists() else 0
        return {
            "ok": True,
            "path": str(destination),
            "copied_bytes": size_bytes,
            "copy_method": "sqlite_backup",
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": str(destination),
            "copied_bytes": 0,
            "copy_method": "sqlite_backup",
            "error": str(exc),
        }


def _copy_regular_file(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, destination)
        size_bytes = int(destination.stat().st_size) if destination.exists() else 0
        return {
            "ok": True,
            "path": str(destination),
            "copied_bytes": size_bytes,
            "copy_method": "copy2",
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": str(destination),
            "copied_bytes": 0,
            "copy_method": "copy2",
            "error": str(exc),
        }


def _copy_directory(source: Path, destination: Path) -> dict[str, Any]:
    copied_files = 0
    copied_bytes = 0
    errors: list[str] = []
    try:
        for item in source.rglob("*"):
            if not item.is_file():
                continue
            relative = item.relative_to(source)
            target = destination / relative
            result = _copy_regular_file(item, target)
            if result.get("ok"):
                copied_files += 1
                copied_bytes += int(result.get("copied_bytes") or 0)
            else:
                errors.append(str(result.get("error") or "copy_failed"))
        return {
            "ok": len(errors) == 0,
            "path": str(destination),
            "copied_files": copied_files,
            "copied_bytes": copied_bytes,
            "error_count": len(errors),
            "errors": errors[:20],
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": str(destination),
            "copied_files": copied_files,
            "copied_bytes": copied_bytes,
            "error_count": len(errors) + 1,
            "errors": errors[:20] + [str(exc)],
        }


def _ensure_directory_ready(path: Path) -> dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            return {"ok": False, "path": str(path), "reason": "not_a_directory"}
        probe = path / ".miru_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"ok": True, "path": str(path)}
    except Exception as exc:
        return {"ok": False, "path": str(path), "reason": str(exc)}


def _prepare_destination_roots(
    record: dict[str, str],
    *,
    snapshot_id: str,
) -> dict[str, Path]:
    snapshot_base = Path(str(record.get("snapshot_path") or record.get("brain_backup_path") or record.get("root") or ""))
    manifest_base = Path(str(record.get("manifest_path") or record.get("snapshot_path") or record.get("brain_backup_path") or record.get("root") or ""))
    return {
        "snapshot_base": snapshot_base,
        "manifest_base": manifest_base,
        "snapshot_root": snapshot_base / snapshot_id,
        "latest_manifest_path": manifest_base / "miru_brain_manifest.json",
        "versioned_manifest_path": manifest_base / f"miru_brain_manifest__{snapshot_id}.json",
    }


def _write_manifest_files(manifest: dict[str, Any], *, latest_manifest_path: Path, versioned_manifest_path: Path, snapshot_root: Path) -> dict[str, str]:
    payload = json.dumps(manifest, indent=2, sort_keys=True)
    latest_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    versioned_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_root.mkdir(parents=True, exist_ok=True)
    snapshot_manifest = snapshot_root / "manifest.json"
    latest_manifest_path.write_text(payload, encoding="utf-8")
    versioned_manifest_path.write_text(payload, encoding="utf-8")
    snapshot_manifest.write_text(payload, encoding="utf-8")
    return {
        "latest_manifest_path": str(latest_manifest_path),
        "versioned_manifest_path": str(versioned_manifest_path),
        "snapshot_manifest_path": str(snapshot_manifest),
    }


def _copy_included_assets(
    *,
    manifest: dict[str, Any],
    snapshot_root: Path,
) -> dict[str, list[dict[str, Any]]]:
    results: dict[str, list[dict[str, Any]]] = {
        "core_dbs": [],
        "runtime_metadata": [],
        "image_dirs": [],
    }
    for section in ("core_dbs", "runtime_metadata"):
        for name, entry in dict((manifest.get("included_assets") or {}).get(section) or {}).items():
            source = Path(str(entry.get("path") or ""))
            if not source.is_file():
                results[section].append(
                    {
                        "name": name,
                        "source_path": str(source),
                        "ok": True,
                        "status": "skipped_missing",
                    }
                )
                continue
            destination = snapshot_root / section / source.name
            copy_result = _copy_sqlite_database(source, destination) if source.suffix.lower() == ".db" else _copy_regular_file(source, destination)
            copy_result.update(
                {
                    "name": name,
                    "source_path": str(source),
                    "status": "copied" if copy_result.get("ok") else "failed",
                }
            )
            results[section].append(copy_result)

    for name, entry in dict((manifest.get("included_assets") or {}).get("image_dirs") or {}).items():
        source = Path(str(entry.get("path") or ""))
        if not source.is_dir():
            results["image_dirs"].append(
                {
                    "name": name,
                    "source_path": str(source),
                    "ok": True,
                    "status": "skipped_missing",
                }
            )
            continue
        destination = snapshot_root / "image_dirs" / name
        copy_result = _copy_directory(source, destination)
        copy_result.update(
            {
                "name": name,
                "source_path": str(source),
                "status": "copied" if copy_result.get("ok") else "failed",
            }
        )
        results["image_dirs"].append(copy_result)
    return results


def _finalize_destination_manifest(
    *,
    base_manifest: dict[str, Any],
    snapshot_id: str,
    destination_results: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest = dict(base_manifest)
    manifest["snapshot_id"] = snapshot_id
    manifest["destination_results"] = destination_results
    manifest["unavailable_destinations"] = [
        {
            "name": item.get("name", ""),
            "reason": item.get("status", ""),
            "root": item.get("root", ""),
        }
        for item in destination_results
        if not item.get("ok")
    ]
    return manifest


def _snapshot_to_destination_record(
    *,
    base_manifest: dict[str, Any],
    destination_name: str,
    record: dict[str, str],
    snapshot_id: str,
) -> dict[str, Any]:
    roots = _prepare_destination_roots(record, snapshot_id=snapshot_id)
    availability = _ensure_directory_ready(roots["snapshot_base"])
    if not availability.get("ok"):
        return {
            "name": destination_name,
            "ok": False,
            "status": "unavailable",
            "root": str(record.get("root") or ""),
            "reason": str(availability.get("reason") or "destination_unavailable"),
            "snapshot_root": str(roots["snapshot_root"]),
            "manifest_path": "",
        }

    manifest_availability = _ensure_directory_ready(roots["manifest_base"])
    if not manifest_availability.get("ok"):
        return {
            "name": destination_name,
            "ok": False,
            "status": "unavailable",
            "root": str(record.get("root") or ""),
            "reason": str(manifest_availability.get("reason") or "manifest_root_unavailable"),
            "snapshot_root": str(roots["snapshot_root"]),
            "manifest_path": "",
        }

    roots["snapshot_root"].mkdir(parents=True, exist_ok=True)
    copy_results = _copy_included_assets(manifest=base_manifest, snapshot_root=roots["snapshot_root"])
    copied_files = 0
    copied_bytes = 0
    error_count = 0
    for section_results in copy_results.values():
        for item in section_results:
            copied_files += 1 if item.get("status") == "copied" else 0
            copied_bytes += int(item.get("copied_bytes") or 0)
            error_count += 0 if item.get("ok") else 1

    return {
        "name": destination_name,
        "ok": error_count == 0,
        "status": "copied" if error_count == 0 else "partial_copy",
        "root": str(record.get("root") or ""),
        "snapshot_root": str(roots["snapshot_root"]),
        "manifest_path": str(roots["latest_manifest_path"]),
        "versioned_manifest_path": str(roots["versioned_manifest_path"]),
        "copied_files": copied_files,
        "copied_bytes": copied_bytes,
        "error_count": error_count,
        "copy_results": copy_results,
        "_paths": roots,
    }


def miru_brain_snapshot_bundle(
    destination_keys: list[str] | None = None,
    *,
    project_root: Path | None = None,
    snapshot_type: str = "daily_light",
    reason: str = "scheduled",
    milestone: str = "",
) -> dict[str, Any]:
    root = Path(project_root or PROJECT_ROOT)
    resolved_keys = list(destination_keys or list(DEFAULT_SNAPSHOT_DESTINATION_KEYS))
    base_manifest = miru_brain_manifest(
        project_root=root,
        snapshot_type=snapshot_type,
        reason=reason,
        destination_keys=resolved_keys,
        milestone=milestone,
    )
    report_destinations = dict(base_manifest.get("backup_destinations") or {})
    snapshot_id = _build_snapshot_id(snapshot_type, reason, milestone=milestone)
    destination_results: list[dict[str, Any]] = []
    successful_path_sets: list[dict[str, Path]] = []

    for raw_key in resolved_keys:
        normalized = _normalized_destination_key(raw_key)
        if not normalized:
            destination_results.append(
                {
                    "name": str(raw_key or ""),
                    "ok": False,
                    "status": "unknown_destination",
                    "root": "",
                    "reason": "unknown_destination_key",
                    "snapshot_root": "",
                    "manifest_path": "",
                }
            )
            continue
        record = dict(report_destinations.get(normalized) or {})
        result = _snapshot_to_destination_record(
            base_manifest=base_manifest,
            destination_name=normalized,
            record=record,
            snapshot_id=snapshot_id,
        )
        if result.get("_paths"):
            successful_path_sets.append(dict(result["_paths"]))
        destination_results.append(result)

    final_manifest = _finalize_destination_manifest(
        base_manifest=base_manifest,
        snapshot_id=snapshot_id,
        destination_results=[{k: v for k, v in result.items() if k != "_paths"} for result in destination_results],
    )
    for result in destination_results:
        paths = result.get("_paths")
        if not paths or not result.get("snapshot_root"):
            continue
        written = _write_manifest_files(
            final_manifest,
            latest_manifest_path=paths["latest_manifest_path"],
            versioned_manifest_path=paths["versioned_manifest_path"],
            snapshot_root=paths["snapshot_root"],
        )
        result["manifest_path"] = written["latest_manifest_path"]
        result["versioned_manifest_path"] = written["versioned_manifest_path"]
        result["snapshot_manifest_path"] = written["snapshot_manifest_path"]
        result.pop("_paths", None)

    ok = any(item.get("ok") for item in destination_results)
    return {
        "ok": ok,
        "mode": "copy_snapshot_bundle",
        "snapshot_id": snapshot_id,
        "snapshot_type": _slug(snapshot_type, default="light"),
        "reason": str(reason or "scheduled"),
        "milestone": str(milestone or ""),
        "manifest": final_manifest,
        "destination_results": destination_results,
        "successful_destinations": [item["name"] for item in destination_results if item.get("ok")],
        "failed_destinations": [item["name"] for item in destination_results if not item.get("ok")],
    }


def miru_brain_snapshot(
    destination: str | Path | None = None,
    *,
    destination_key: str | None = None,
    project_root: Path | None = None,
    snapshot_type: str = "daily_light",
    reason: str = "manual",
    milestone: str = "",
) -> dict[str, Any]:
    if destination is None and not destination_key:
        manifest = miru_brain_manifest(
            project_root=project_root,
            snapshot_type=snapshot_type,
            reason=reason,
            milestone=milestone,
        )
        destination_path = None
        return {
            "ok": True,
            "mode": "manifest_only",
            "destination_key": "",
            "manifest_path": "",
            "manifest": manifest,
            "snapshot_root": "",
        }

    if destination_key:
        bundle = miru_brain_snapshot_bundle(
            [destination_key],
            project_root=project_root,
            snapshot_type=snapshot_type,
            reason=reason,
            milestone=milestone,
        )
        first = dict((bundle.get("destination_results") or [{}])[0] or {})
        return {
            "ok": bool(first.get("ok")),
            "mode": "copy_snapshot",
            "destination_key": str(destination_key or ""),
            "manifest_path": str(first.get("manifest_path") or ""),
            "manifest": bundle.get("manifest") or {},
            "snapshot_root": str(first.get("snapshot_root") or ""),
            "destination_result": first,
            "snapshot_id": str(bundle.get("snapshot_id") or ""),
        }

    root = Path(project_root or PROJECT_ROOT)
    manifest = miru_brain_manifest(
        project_root=root,
        snapshot_type=snapshot_type,
        reason=reason,
        milestone=milestone,
    )
    snapshot_id = _build_snapshot_id(snapshot_type, reason, milestone=milestone)
    destination_path = _resolve_manifest_destination(
        destination=destination,
        destination_key=destination_key,
        layout_report=manifest,
    )
    if destination_path is None:
        return {
            "ok": False,
            "mode": "copy_snapshot",
            "destination_key": "",
            "manifest_path": "",
            "manifest": manifest,
            "snapshot_root": "",
            "reason": "destination_unresolved",
        }
    record = {
        "root": str(destination_path),
        "brain_backup_path": str(destination_path),
        "snapshot_path": str(destination_path),
        "manifest_path": str(destination_path),
    }
    result = _snapshot_to_destination_record(
        base_manifest=manifest,
        destination_name="custom_destination",
        record=record,
        snapshot_id=snapshot_id,
    )
    final_manifest = _finalize_destination_manifest(
        base_manifest=manifest,
        snapshot_id=snapshot_id,
        destination_results=[{k: v for k, v in result.items() if k != "_paths"}],
    )
    if result.get("_paths"):
        written = _write_manifest_files(
            final_manifest,
            latest_manifest_path=result["_paths"]["latest_manifest_path"],
            versioned_manifest_path=result["_paths"]["versioned_manifest_path"],
            snapshot_root=result["_paths"]["snapshot_root"],
        )
        result["manifest_path"] = written["latest_manifest_path"]
        result["versioned_manifest_path"] = written["versioned_manifest_path"]
        result["snapshot_manifest_path"] = written["snapshot_manifest_path"]
        result.pop("_paths", None)
    return {
        "ok": bool(result.get("ok")),
        "mode": "copy_snapshot",
        "destination_key": "",
        "manifest_path": str(result.get("manifest_path") or ""),
        "manifest": final_manifest,
        "snapshot_root": str(result.get("snapshot_root") or ""),
        "destination_result": result,
        "snapshot_id": snapshot_id,
    }


def miru_brain_restore(manifest_source: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(manifest_source, dict):
        manifest = dict(manifest_source)
        source_label = "dict"
    else:
        path = Path(manifest_source)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        source_label = str(path)
    return {
        "ok": False,
        "mode": "restore_planning_only",
        "source": source_label,
        "reason": "Full restore is intentionally deferred; this hook only validates and surfaces the manifest.",
        "manifest_summary": {
            "project_root": str(manifest.get("project_root") or ""),
            "generated_at": str(manifest.get("generated_at") or ""),
            "snapshot_id": str(manifest.get("snapshot_id") or ""),
            "snapshot_type": str(manifest.get("snapshot_type") or ""),
            "core_db_count": len(dict(manifest.get("core_dbs") or {})),
            "image_dir_count": len(dict(manifest.get("image_dirs") or {})),
        },
    }
