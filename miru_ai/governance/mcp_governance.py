from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import queue
import re
import sqlite3
import stat
import subprocess
from threading import Lock, Thread
import time
import uuid
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MCP_CONFIG_PATH = PROJECT_ROOT / ".mcp.json"
DEFAULT_MCP_POLICY_PATH = PROJECT_ROOT / "config" / "miru_mcp_policy.json"
DEFAULT_CANONICAL_CATALOG_DB_PATH = PROJECT_ROOT / "data" / "card_catalog.db"
DEFAULT_CANONICAL_DOSSIER_DB_PATH = PROJECT_ROOT / "data" / "miru_dossiers.db"
DEFAULT_RUNTIME_DOSSIER_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_dossiers.db"
DEFAULT_MCP_STATE_DB_PATH = PROJECT_ROOT / "data" / "miru_mcp_governance.db"

STDIO_SUPPORTED_LANES = {
    "sqlite-ro-snapshot",
    "sequential-thinking",
    "perplexity",
    "youtube",
}
REQUIRED_MINIMUM_WIRING = (
    "sqlite-ro-snapshot",
    "sequential-thinking",
    "perplexity",
    "youtube",
)
CARD_CODE_RE = re.compile(r"\b(?:(?:OP|EB|ST|PRB)\d{2}-\d{3}[A-Z]?|P-\d{3})\b", re.I)
SET_CODE_RE = re.compile(r"\b(?:OP|EB|ST|PRB)\d{2}\b", re.I)
VARIANT_HINT_TERMS = {
    "variant",
    "parallel",
    "alt art",
    "alternate art",
    "mismatch",
    "discrepancy",
    "misprint",
    "wrong art",
    "error",
    "stamp",
    "foil",
}
SET_ANOMALY_HINT_TERMS = {
    "set",
    "checklist",
    "box opening",
    "case opening",
    "opening",
    "pulls",
    "anomaly",
    "missing",
    "card list",
}
ONE_PIECE_SIGNAL_TERMS = {
    "one piece",
    "one piece card game",
    "bandai",
    "opcardlist",
    "onepiece-cardgame",
    "romance dawn",
}
OFFICIAL_REFERENCE_HINTS = {
    "en.onepiece-cardgame.com",
    "onepiece-cardgame.com",
    "opcardlist.com",
    "official web site",
    "official card list",
}
YOUTUBE_RELEVANCE_TERMS = {
    "showcase",
    "review",
    "opening",
    "box opening",
    "case opening",
    "leader",
    "deck profile",
    "pulls",
}
UNRELATED_PENALTY_TERMS = {
    "background check",
    "employment",
    "hiring",
    "candidate",
    "payroll",
    "credit",
    "loan",
    "insurance",
    "criminal history",
    "degree",
}


def current_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json_load(path: Path, default: Any) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _json_loads(value: Any, default: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def _file_signature(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{stat.st_size}:{getattr(stat, 'st_mtime_ns', int(stat.st_mtime * 1_000_000_000))}"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truncate_text(value: Any, limit: int = 600) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def _normalize_server_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _compact_whitespace(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_match_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _extract_card_code(value: Any) -> str:
    match = CARD_CODE_RE.search(str(value or ""))
    return match.group(0).upper() if match else ""


def _extract_set_code(value: Any) -> str:
    match = SET_CODE_RE.search(str(value or ""))
    return match.group(0).upper() if match else ""


def _quoted(value: Any) -> str:
    text = _compact_whitespace(value)
    return f'"{text}"' if text else ""


def _token_hit_ratio(haystack: str, needle: Any) -> float:
    hay_tokens = set(_normalize_match_text(haystack).split())
    needle_tokens = [
        token
        for token in _normalize_match_text(needle).split()
        if token and len(token) > 1
    ]
    if not hay_tokens or not needle_tokens:
        return 0.0
    hits = sum(1 for token in set(needle_tokens) if token in hay_tokens)
    return hits / max(len(set(needle_tokens)), 1)


def _make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_make_json_safe(item) for item in value]
    return str(value)


def _read_sqlite_count(path: Path, table: str) -> int:
    if not path.is_file():
        return 0
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return 0


def _inspect_sqlite_tables(path: Path, target_tables: tuple[str, ...]) -> dict[str, Any]:
    status = {
        "path": str(path),
        "exists": path.is_file(),
        "openable": False,
        "table_counts": {},
        "signature": _file_signature(path),
        "error": "",
    }
    if not path.is_file():
        status["error"] = "missing"
        return status
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as conn:
            status["openable"] = True
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            for table in target_tables:
                if table in tables:
                    status["table_counts"][table] = int(
                        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    )
    except sqlite3.Error as exc:
        status["error"] = f"{exc.__class__.__name__}: {exc}"
    return status


def load_mcp_config(path: Path | None = None) -> dict[str, Any]:
    config_path = Path(path or DEFAULT_MCP_CONFIG_PATH)
    data = _json_load(config_path, {})
    if not isinstance(data, dict):
        data = {}
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    return {
        "config_path": str(config_path),
        "found": config_path.is_file(),
        "servers": servers,
    }


def load_mcp_policy(path: Path | None = None) -> dict[str, Any]:
    policy_path = Path(path or DEFAULT_MCP_POLICY_PATH)
    data = _json_load(policy_path, {})
    if not isinstance(data, dict):
        data = {}
    lanes = data.get("lanes")
    if not isinstance(lanes, list):
        lanes = []
    return {
        "policy_path": str(policy_path),
        "found": policy_path.is_file(),
        "policy_version": _safe_int(data.get("policy_version"), 0),
        "authority_boundary": dict(data.get("authority_boundary") or {}),
        "catalog_ingestion": dict(data.get("catalog_ingestion") or {}),
        "research_governance": dict(data.get("research_governance") or {}),
        "lanes": lanes,
    }


def _policy_lane_map(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in policy.get("lanes") or []:
        if not isinstance(item, dict):
            continue
        key = _normalize_server_id(item.get("server_id"))
        if key:
            out[key] = dict(item)
    return out


def _sanitize_server_config(server_id: str, config: dict[str, Any]) -> dict[str, Any]:
    transport = str(config.get("type") or "").strip().lower()
    args = list(config.get("args") or []) if isinstance(config.get("args"), list) else []
    headers = list((config.get("headers") or {}).keys()) if isinstance(config.get("headers"), dict) else []
    env = list((config.get("env") or {}).keys()) if isinstance(config.get("env"), dict) else []
    url_host = ""
    if transport == "http":
        try:
            url_host = str(urlparse(str(config.get("url") or "")).netloc or "")
        except Exception:
            url_host = ""
    return {
        "server_id": server_id,
        "transport": transport or "unknown",
        "command": str(config.get("command") or "").strip(),
        "args": [str(item) for item in args],
        "url_host": url_host,
        "header_keys": headers,
        "env_keys": env,
    }


def resolve_sqlite_snapshot_target(
    mcp_config: dict[str, Any] | None = None,
    *,
    server_id: str = "sqlite-ro-snapshot",
) -> Path | None:
    config_payload = mcp_config or load_mcp_config()
    servers = dict(config_payload.get("servers") or {})
    raw = servers.get(server_id)
    if not isinstance(raw, dict):
        return None
    args = list(raw.get("args") or []) if isinstance(raw.get("args"), list) else []
    for index, item in enumerate(args):
        text = str(item or "").strip()
        if text == "--db" and index + 1 < len(args):
            return Path(str(args[index + 1]).strip())
        if text.startswith("--db="):
            return Path(text.split("=", 1)[1].strip())
    return None


def _repo_local_duplicate_snapshot_path() -> Path:
    return PROJECT_ROOT / "miru-mcp" / "sqlite-ro" / "card_catalog.snapshot.db"


def ensure_governance_state_db(path: Path | None = None) -> Path:
    db_path = Path(path or DEFAULT_MCP_STATE_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS catalog_sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                source_db_path TEXT NOT NULL,
                snapshot_db_path TEXT NOT NULL,
                source_signature TEXT NOT NULL DEFAULT '',
                snapshot_signature_before TEXT NOT NULL DEFAULT '',
                snapshot_signature_after TEXT NOT NULL DEFAULT '',
                source_cards INTEGER NOT NULL DEFAULT 0,
                snapshot_cards INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                provenance_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS research_review_leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                lane_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                query_text TEXT NOT NULL,
                card_code TEXT NOT NULL DEFAULT '',
                set_code TEXT NOT NULL DEFAULT '',
                lead_type TEXT NOT NULL,
                source_type TEXT NOT NULL,
                trust_classification TEXT NOT NULL,
                approval_class TEXT NOT NULL,
                blocked_from_truth_authority INTEGER NOT NULL DEFAULT 1,
                authority_cross_check_required INTEGER NOT NULL DEFAULT 1,
                cross_check_targets_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 0.0,
                outcome TEXT NOT NULL DEFAULT 'review_required',
                review_status TEXT NOT NULL DEFAULT 'pending',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '{}',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                governance_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_research_review_leads_lane_status
            ON research_review_leads (lane_id, review_status, created_at DESC);
            """
        )
        conn.commit()
    return db_path


def _record_catalog_sync_run(
    report: dict[str, Any],
    *,
    state_db_path: Path | None = None,
) -> None:
    db_path = ensure_governance_state_db(state_db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO catalog_sync_runs (
                run_at,
                source_db_path,
                snapshot_db_path,
                source_signature,
                snapshot_signature_before,
                snapshot_signature_after,
                source_cards,
                snapshot_cards,
                status,
                detail,
                provenance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(report.get("run_at") or current_timestamp()),
                str(report.get("source_db_path") or ""),
                str(report.get("snapshot_db_path") or ""),
                str(report.get("source_signature") or ""),
                str(report.get("snapshot_signature_before") or ""),
                str(report.get("snapshot_signature_after") or ""),
                _safe_int(report.get("source_cards"), 0),
                _safe_int(report.get("snapshot_cards"), 0),
                str(report.get("status") or "unknown"),
                str(report.get("detail") or ""),
                json.dumps(_make_json_safe(report.get("provenance") or {})),
            ),
        )
        conn.commit()


def load_latest_catalog_sync_report(
    *,
    state_db_path: Path | None = None,
) -> dict[str, Any]:
    db_path = Path(state_db_path or DEFAULT_MCP_STATE_DB_PATH)
    if not db_path.is_file():
        return {}
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM catalog_sync_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return {}
    return {
        "run_at": str(row["run_at"] or ""),
        "source_db_path": str(row["source_db_path"] or ""),
        "snapshot_db_path": str(row["snapshot_db_path"] or ""),
        "source_signature": str(row["source_signature"] or ""),
        "snapshot_signature_before": str(row["snapshot_signature_before"] or ""),
        "snapshot_signature_after": str(row["snapshot_signature_after"] or ""),
        "source_cards": _safe_int(row["source_cards"], 0),
        "snapshot_cards": _safe_int(row["snapshot_cards"], 0),
        "status": str(row["status"] or ""),
        "detail": str(row["detail"] or ""),
        "provenance": _json_loads(row["provenance_json"], {}),
    }


def _ensure_windows_writable(path: Path) -> None:
    if not path.exists():
        return
    try:
        current_mode = path.stat().st_mode
        path.chmod(current_mode | stat.S_IWRITE)
    except OSError:
        pass


def sync_catalog_snapshot(
    *,
    canonical_catalog_db_path: Path | None = None,
    snapshot_db_path: Path | None = None,
    mcp_config_path: Path | None = None,
    state_db_path: Path | None = None,
) -> dict[str, Any]:
    source_path = Path(canonical_catalog_db_path or DEFAULT_CANONICAL_CATALOG_DB_PATH)
    config_payload = load_mcp_config(mcp_config_path)
    target_path = Path(snapshot_db_path) if snapshot_db_path else resolve_sqlite_snapshot_target(config_payload)
    report = {
        "run_at": current_timestamp(),
        "source_db_path": str(source_path),
        "snapshot_db_path": str(target_path) if target_path else "",
        "source_signature": _file_signature(source_path),
        "snapshot_signature_before": _file_signature(target_path) if target_path else "",
        "snapshot_signature_after": "",
        "source_cards": _read_sqlite_count(source_path, "cards"),
        "snapshot_cards": 0,
        "status": "failed_closed",
        "detail": "",
        "provenance": {
            "direction": "canonical_catalog_to_sqlite_ro_snapshot",
            "read_only_source": True,
            "truth_writeback_allowed": False,
            "configured_target_source": config_payload.get("config_path") or "",
        },
    }

    if not source_path.is_file():
        report["detail"] = "Canonical catalog db is missing."
        _record_catalog_sync_run(report, state_db_path=state_db_path)
        return report
    if target_path is None:
        report["detail"] = "sqlite-ro-snapshot target is not configured in .mcp.json."
        _record_catalog_sync_run(report, state_db_path=state_db_path)
        return report

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_target_path = target_path.with_suffix(target_path.suffix + ".tmp")
        if temp_target_path.exists():
            _ensure_windows_writable(temp_target_path)
            temp_target_path.unlink()
        with closing(sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)) as src_conn:
            with closing(sqlite3.connect(temp_target_path)) as dst_conn:
                src_conn.backup(dst_conn)
                dst_conn.commit()
        if target_path.exists():
            _ensure_windows_writable(target_path)
        os.replace(temp_target_path, target_path)
        report["snapshot_signature_after"] = _file_signature(target_path)
        report["snapshot_cards"] = _read_sqlite_count(target_path, "cards")
        if report["source_cards"] > 0 and report["source_cards"] == report["snapshot_cards"]:
            report["status"] = "synced"
            report["detail"] = (
                "Canonical catalog copied into sqlite-ro snapshot via sqlite backup "
                "and atomic target replace."
            )
        else:
            report["status"] = "failed_closed"
            report["detail"] = "Snapshot sync completed but row counts do not match; holding fail-closed."
    except (OSError, sqlite3.Error) as exc:
        report["status"] = "failed_closed"
        report["detail"] = f"{exc.__class__.__name__}: {exc}"
    _record_catalog_sync_run(report, state_db_path=state_db_path)
    return report


class McpInvocationError(RuntimeError):
    pass


class McpStdioSession:
    def __init__(
        self,
        *,
        server_id: str,
        server_config: dict[str, Any],
        cwd: Path | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.server_id = server_id
        self.server_config = server_config
        self.cwd = Path(cwd or PROJECT_ROOT)
        self.timeout_seconds = timeout_seconds
        self.process: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._stdout_queue: queue.Queue[bytes | None] = queue.Queue()
        self._stderr_lock = Lock()
        self._stderr_lines: list[str] = []
        self._stdout_noise_lock = Lock()
        self._stdout_noise_lines: list[str] = []
        self._stdout_thread: Thread | None = None
        self._stderr_thread: Thread | None = None

    def __enter__(self) -> "McpStdioSession":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        transport = str(self.server_config.get("type") or "").strip().lower()
        if transport != "stdio":
            raise McpInvocationError(
                f"{self.server_id} uses unsupported transport {transport!r}."
            )
        command = str(self.server_config.get("command") or "").strip()
        if not command:
            raise McpInvocationError(f"{self.server_id} is missing a command.")
        args = [str(item) for item in (self.server_config.get("args") or [])]
        uses_npx = os.path.basename(command).lower().startswith("npx") or any(
            os.path.basename(str(item)).lower().startswith("npx") for item in args
        )
        if os.path.basename(command).lower().startswith("npx") and (
            not args or args[0] not in {"-y", "--yes"}
        ):
            args = ["-y", *args]
        env = os.environ.copy()
        env.update(
            {
                str(key): str(value)
                for key, value in dict(self.server_config.get("env") or {}).items()
            }
        )
        if uses_npx:
            npm_cache = PROJECT_ROOT / ".npm-cache"
            npm_cache.mkdir(parents=True, exist_ok=True)
            env.setdefault("npm_config_cache", str(npm_cache))
        try:
            self.process = subprocess.Popen(
                [command, *args],
                cwd=str(self.cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            raise McpInvocationError(f"Failed to start {self.server_id}: {exc}") from exc
        self._stdout_thread = Thread(
            target=self._pump_stdout,
            name=f"{self.server_id}-mcp-stdout",
            daemon=True,
        )
        self._stderr_thread = Thread(
            target=self._pump_stderr,
            name=f"{self.server_id}-mcp-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        self._initialize()

    def close(self) -> None:
        proc = self.process
        self.process = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass

    def _append_stderr(self, line: bytes) -> None:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            return
        with self._stderr_lock:
            self._stderr_lines.append(text)
            if len(self._stderr_lines) > 40:
                self._stderr_lines = self._stderr_lines[-40:]

    def _append_stdout_noise(self, line: bytes) -> None:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            return
        with self._stdout_noise_lock:
            self._stdout_noise_lines.append(text)
            if len(self._stdout_noise_lines) > 20:
                self._stdout_noise_lines = self._stdout_noise_lines[-20:]

    def _pump_stdout(self) -> None:
        proc = self.process
        if proc is None or proc.stdout is None:
            self._stdout_queue.put(None)
            return
        try:
            for line in iter(proc.stdout.readline, b""):
                self._stdout_queue.put(line)
        finally:
            self._stdout_queue.put(None)

    def _pump_stderr(self) -> None:
        proc = self.process
        if proc is None or proc.stderr is None:
            return
        for line in iter(proc.stderr.readline, b""):
            self._append_stderr(line)

    def _diagnostic_suffix(self) -> str:
        details: list[str] = []
        proc = self.process
        if proc is not None and proc.poll() is not None:
            details.append(f"exit_code={proc.returncode}")
        with self._stderr_lock:
            if self._stderr_lines:
                details.append(
                    "stderr=" + _truncate_text(" | ".join(self._stderr_lines), 400)
                )
        with self._stdout_noise_lock:
            if self._stdout_noise_lines:
                details.append(
                    "stdout=" + _truncate_text(" | ".join(self._stdout_noise_lines), 240)
                )
        return f" ({'; '.join(details)})" if details else ""

    def _read_message(self, timeout_seconds: float | None = None) -> dict[str, Any]:
        if self.process is None:
            raise McpInvocationError(f"{self.server_id} is not running.")
        deadline = time.monotonic() + float(timeout_seconds or self.timeout_seconds)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpInvocationError(
                    f"{self.server_id} timed out while reading MCP response."
                    f"{self._diagnostic_suffix()}"
                )
            try:
                line = self._stdout_queue.get(timeout=remaining)
            except queue.Empty as exc:
                raise McpInvocationError(
                    f"{self.server_id} timed out while reading MCP response."
                    f"{self._diagnostic_suffix()}"
                ) from exc
            if line is None:
                raise McpInvocationError(
                    f"{self.server_id} closed stdout before sending a response."
                    f"{self._diagnostic_suffix()}"
                )
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue
            try:
                payload = json.loads(decoded)
            except json.JSONDecodeError:
                self._append_stdout_noise(line)
                continue
            if not isinstance(payload, dict):
                self._append_stdout_noise(line)
                continue
            return payload

    def _send(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise McpInvocationError(f"{self.server_id} is not running.")
        body = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            self.process.stdin.write(body)
            self.process.stdin.flush()
        except OSError as exc:
            raise McpInvocationError(
                f"Failed to write to {self.server_id}: {exc}{self._diagnostic_suffix()}"
            ) from exc

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        while True:
            message = self._read_message()
            if "id" not in message:
                continue
            if message.get("id") != request_id:
                continue
            error = message.get("error")
            if error:
                raise McpInvocationError(
                    f"{self.server_id} {method} failed: {_truncate_text(error, 200)}"
                    f"{self._diagnostic_suffix()}"
                )
            result = message.get("result")
            if not isinstance(result, dict):
                raise McpInvocationError(
                    f"{self.server_id} {method} returned no result payload."
                    f"{self._diagnostic_suffix()}"
                )
            return result

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "miru-ai", "version": "1.0"},
            },
        )
        self._notify("notifications/initialized", {})

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list", {})
        tools = result.get("tools")
        return list(tools) if isinstance(tools, list) else []

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
        )


def _extract_text_fragments(payload: Any) -> list[str]:
    fragments: list[str] = []
    if payload is None:
        return fragments
    if isinstance(payload, str):
        text = payload.strip()
        if text:
            fragments.append(text)
        return fragments
    if isinstance(payload, dict):
        for key in ("text", "results", "output_text"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                fragments.append(value.strip())
        if isinstance(payload.get("content"), list):
            fragments.extend(_extract_text_fragments(payload.get("content")))
        if payload.get("structuredContent") is not None:
            fragments.append(json.dumps(_make_json_safe(payload.get("structuredContent"))))
        return fragments
    if isinstance(payload, list):
        for item in payload:
            fragments.extend(_extract_text_fragments(item))
        return fragments
    text = str(payload).strip()
    if text:
        fragments.append(text)
    return fragments


def _tool_result_preview(result: Any) -> str:
    fragments = _extract_text_fragments(result)
    if not fragments:
        return ""
    return _truncate_text("\n\n".join(fragments), 1200)


def invoke_stdio_mcp_lane(
    *,
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    mcp_config_path: Path | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    config_payload = load_mcp_config(mcp_config_path)
    raw_server = dict((config_payload.get("servers") or {}).get(server_id) or {})
    if not raw_server:
        raise McpInvocationError(f"MCP server {server_id!r} is not configured.")
    with McpStdioSession(
        server_id=server_id,
        server_config=raw_server,
        cwd=PROJECT_ROOT,
        timeout_seconds=timeout_seconds,
    ) as session:
        tools = session.list_tools()
        result = session.call_tool(tool_name, arguments or {})
    return {
        "server_id": server_id,
        "tool_name": tool_name,
        "arguments": _make_json_safe(arguments or {}),
        "tools": [
            {
                "name": str(item.get("name") or ""),
                "description": _truncate_text(item.get("description") or "", 180),
            }
            for item in tools
            if isinstance(item, dict)
        ],
        "result": _make_json_safe(result),
        "preview": _tool_result_preview(result),
    }


def _probe_lane(
    lane_id: str,
    *,
    mcp_config_path: Path | None = None,
) -> dict[str, Any]:
    if lane_id == "sqlite-ro-snapshot":
        return invoke_stdio_mcp_lane(
            server_id=lane_id,
            tool_name="sqlite_get",
            arguments={"sql": "SELECT COUNT(*) AS card_count FROM cards"},
            mcp_config_path=mcp_config_path,
            timeout_seconds=20.0,
        )
    if lane_id == "sequential-thinking":
        return invoke_stdio_mcp_lane(
            server_id=lane_id,
            tool_name="sequentialthinking",
            arguments={
                "thought": "Connectivity probe only.",
                "thoughtNumber": 1,
                "totalThoughts": 1,
                "nextThoughtNeeded": False,
            },
            mcp_config_path=mcp_config_path,
            timeout_seconds=20.0,
        )
    if lane_id == "perplexity":
        return invoke_stdio_mcp_lane(
            server_id=lane_id,
            tool_name="perplexity_search",
            arguments={
                "query": "OP01-001 One Piece card",
                "country": "US",
                "max_results": 2,
                "max_tokens_per_page": 256,
            },
            mcp_config_path=mcp_config_path,
            timeout_seconds=40.0,
        )
    if lane_id == "youtube":
        return invoke_stdio_mcp_lane(
            server_id=lane_id,
            tool_name="search_videos",
            arguments={"query": "OP01-001 One Piece card", "maxResults": 2},
            mcp_config_path=mcp_config_path,
            timeout_seconds=30.0,
        )
    raise McpInvocationError(f"No probe routing configured for lane {lane_id!r}.")


def build_mcp_governance_summary(
    *,
    mcp_config_path: Path | None = None,
    policy_path: Path | None = None,
    state_db_path: Path | None = None,
    probe: bool = False,
) -> dict[str, Any]:
    config_payload = load_mcp_config(mcp_config_path)
    policy = load_mcp_policy(policy_path)
    policy_map = _policy_lane_map(policy)
    configured_servers = dict(config_payload.get("servers") or {})

    snapshot_target = resolve_sqlite_snapshot_target(config_payload)
    repo_duplicate = _repo_local_duplicate_snapshot_path()
    snapshot_status = _inspect_sqlite_tables(
        snapshot_target, ("cards", "miru_validations", "miru_card_insights")
    ) if snapshot_target else {
        "path": "",
        "exists": False,
        "openable": False,
        "table_counts": {},
        "signature": "",
        "error": "not_configured",
    }

    lanes: list[dict[str, Any]] = []
    probe_results: dict[str, Any] = {}
    for server_id in sorted(set(configured_servers.keys()) | set(policy_map.keys())):
        server_id = _normalize_server_id(server_id)
        raw_server = dict(configured_servers.get(server_id) or {})
        lane_policy = dict(policy_map.get(server_id) or {})
        sanitized = _sanitize_server_config(server_id, raw_server) if raw_server else {
            "server_id": server_id,
            "transport": "missing",
            "command": "",
            "args": [],
            "url_host": "",
            "header_keys": [],
            "env_keys": [],
        }
        wired = bool(raw_server) and (
            server_id in STDIO_SUPPORTED_LANES or str(sanitized.get("transport")) == "stdio"
        )
        lane = {
            **sanitized,
            "approval_class": str(lane_policy.get("approval_class") or "unclassified"),
            "lane_role": str(lane_policy.get("lane_role") or ""),
            "source_type": str(lane_policy.get("source_type") or ""),
            "blocked_from_truth_authority": bool(
                lane_policy.get("blocked_from_truth_authority", True)
            ),
            "publish_truth_directly": bool(
                lane_policy.get("publish_truth_directly", False)
            ),
            "operator_only": bool(lane_policy.get("operator_only", False)),
            "research_enabled": bool(lane_policy.get("research_enabled", False)),
            "default_confidence": _safe_float(
                lane_policy.get("default_confidence"), 0.0
            ),
            "cross_check_targets": list(lane_policy.get("cross_check_targets") or []),
            "configured": bool(raw_server),
            "wired": wired,
            "required_minimum": server_id in REQUIRED_MINIMUM_WIRING,
            "notes": str(lane_policy.get("notes") or ""),
        }
        if probe and raw_server and server_id in REQUIRED_MINIMUM_WIRING:
            try:
                probe_result = _probe_lane(server_id, mcp_config_path=mcp_config_path)
                probe_results[server_id] = {
                    "ok": True,
                    "tool_name": probe_result.get("tool_name") or "",
                    "preview": probe_result.get("preview") or "",
                }
                lane["probe_ok"] = True
            except Exception as exc:
                probe_results[server_id] = {
                    "ok": False,
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
                lane["probe_ok"] = False
        lanes.append(lane)

    review_summary = list_research_review_leads(limit=5, state_db_path=state_db_path)
    return {
        "config_path": str(config_payload.get("config_path") or ""),
        "policy_path": str(policy.get("policy_path") or ""),
        "policy_version": _safe_int(policy.get("policy_version"), 0),
        "authority_boundary": dict(policy.get("authority_boundary") or {}),
        "catalog_ingestion": {
            "canonical_catalog": _inspect_sqlite_tables(
                DEFAULT_CANONICAL_CATALOG_DB_PATH,
                ("cards", "miru_validations", "miru_card_insights"),
            ),
            "canonical_dossiers": _inspect_sqlite_tables(
                DEFAULT_CANONICAL_DOSSIER_DB_PATH, ("cards", "card_identity")
            ),
            "runtime_learning_dossiers": _inspect_sqlite_tables(
                DEFAULT_RUNTIME_DOSSIER_DB_PATH,
                ("learning_dossiers", "learning_dossier_sources"),
            ),
            "sqlite_snapshot_target": str(snapshot_target) if snapshot_target else "",
            "sqlite_snapshot_status": snapshot_status,
            "repo_local_duplicate_snapshot": {
                "path": str(repo_duplicate),
                "exists": repo_duplicate.is_file(),
                "active_target": bool(
                    snapshot_target
                    and snapshot_target.resolve() == repo_duplicate.resolve()
                )
                if snapshot_target and repo_duplicate.exists()
                else False,
            },
            "latest_sync": load_latest_catalog_sync_report(state_db_path=state_db_path),
            "notes": list((policy.get("catalog_ingestion") or {}).get("notes") or []),
        },
        "research_governance": dict(policy.get("research_governance") or {}),
        "required_minimum_wiring": list(REQUIRED_MINIMUM_WIRING),
        "lanes": lanes,
        "probe_results": probe_results,
        "review_queue": {
            "path": str(Path(state_db_path or DEFAULT_MCP_STATE_DB_PATH)),
            "total": review_summary.get("total", 0),
            "pending": review_summary.get("pending", 0),
            "items": review_summary.get("items", []),
        },
    }


def _load_catalog_research_context(
    *,
    query: str,
    card_code: str = "",
    set_code: str = "",
    catalog_db_path: Path | None = None,
) -> dict[str, Any]:
    source_query = _compact_whitespace(query)
    resolved_card_code = str(card_code or "").strip().upper() or _extract_card_code(source_query)
    resolved_set_code = str(set_code or "").strip().upper() or _extract_set_code(source_query)
    context: dict[str, Any] = {
        "raw_query": source_query,
        "card_code": resolved_card_code,
        "set_code": resolved_set_code,
        "card_name": "",
        "set_name": "",
        "card_type": "",
        "rarity": "",
        "color": "",
        "is_variant": False,
        "variant_category": "",
        "set_card_total": 0,
        "mode": "general_discrepancy",
        "scope": "general",
    }
    db_path = Path(catalog_db_path or DEFAULT_CANONICAL_CATALOG_DB_PATH)
    if not db_path.is_file():
        return context
    try:
        with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            row = None
            if resolved_card_code:
                row = conn.execute(
                    """
                    SELECT canonical_code, set_code, set_name, card_name, card_type, rarity, color,
                           is_variant, variant_category
                    FROM cards
                    WHERE canonical_code = ?
                    LIMIT 1
                    """,
                    (resolved_card_code,),
                ).fetchone()
            if row is None and source_query:
                candidates = conn.execute(
                    """
                    SELECT canonical_code, set_code, set_name, card_name, card_type, rarity, color,
                           is_variant, variant_category
                    FROM cards
                    WHERE instr(lower(?), lower(card_name)) > 0
                    ORDER BY length(card_name) DESC, canonical_code ASC
                    LIMIT 12
                    """,
                    (source_query.lower(),),
                ).fetchall()
                best_score = -1
                for candidate in candidates:
                    score = 0
                    candidate_set_code = str(candidate["set_code"] or "").upper()
                    candidate_set_name = str(candidate["set_name"] or "")
                    if resolved_set_code and candidate_set_code == resolved_set_code:
                        score += 4
                    if candidate_set_name and _token_hit_ratio(source_query, candidate_set_name) >= 0.8:
                        score += 3
                    if _token_hit_ratio(source_query, candidate["card_name"]) >= 0.95:
                        score += 2
                    if not bool(candidate["is_variant"]):
                        score += 1
                    if score > best_score:
                        row = candidate
                        best_score = score
            if row is not None:
                context.update(
                    {
                        "card_code": str(row["canonical_code"] or "").upper(),
                        "set_code": str(row["set_code"] or "").upper(),
                        "set_name": str(row["set_name"] or ""),
                        "card_name": str(row["card_name"] or ""),
                        "card_type": str(row["card_type"] or ""),
                        "rarity": str(row["rarity"] or ""),
                        "color": str(row["color"] or ""),
                        "is_variant": bool(_safe_int(row["is_variant"], 0)),
                        "variant_category": str(row["variant_category"] or ""),
                    }
                )
            if context["set_code"] and not context["set_name"]:
                set_row = conn.execute(
                    """
                    SELECT set_name, COUNT(*) AS set_total
                    FROM cards
                    WHERE set_code = ?
                    GROUP BY set_name
                    ORDER BY COUNT(*) DESC, set_name ASC
                    LIMIT 1
                    """,
                    (context["set_code"],),
                ).fetchone()
                if set_row is not None:
                    context["set_name"] = str(set_row["set_name"] or "")
                    context["set_card_total"] = _safe_int(set_row["set_total"], 0)
            elif context["set_code"]:
                set_total = conn.execute(
                    "SELECT COUNT(*) FROM cards WHERE set_code = ?",
                    (context["set_code"],),
                ).fetchone()
                context["set_card_total"] = _safe_int((set_total or [0])[0], 0)
    except sqlite3.Error:
        return context

    normalized_query = _normalize_match_text(source_query)
    if any(term in normalized_query for term in (_normalize_match_text(item) for item in VARIANT_HINT_TERMS)):
        context["mode"] = "variant_discrepancy"
    elif context["card_code"] or context["card_name"]:
        context["mode"] = "exact_card_lookup"
    elif context["set_code"] or context["set_name"] or any(
        term in normalized_query for term in (_normalize_match_text(item) for item in SET_ANOMALY_HINT_TERMS)
    ):
        context["mode"] = "set_anomaly_check"
    if context["card_code"] or context["card_name"]:
        context["scope"] = "card"
    elif context["set_code"] or context["set_name"]:
        context["scope"] = "set"
    return context


def _shape_perplexity_query(query: str, context: dict[str, Any]) -> str:
    parts = ['"One Piece Card Game"']
    if context.get("card_code"):
        parts.append(_quoted(context.get("card_code")))
    if context.get("card_name"):
        parts.append(_quoted(context.get("card_name")))
    if context.get("set_name"):
        parts.append(_quoted(context.get("set_name")))
    if context.get("set_code"):
        parts.append(str(context.get("set_code")))
    if context.get("card_type"):
        parts.append(_quoted(context.get("card_type")))
    mode = str(context.get("mode") or "general_discrepancy")
    if mode == "variant_discrepancy":
        parts.append('variant parallel "alt art" mismatch discrepancy misprint')
        parts.append('"official card list" opcardlist showcase')
    elif mode == "set_anomaly_check":
        parts.append('"card list" checklist "box opening" "case opening" pulls variants')
        parts.append('anomaly discrepancy mismatch missing parallel')
    else:
        parts.append('"official card list" opcardlist showcase review')
        if bool(context.get("is_variant")) or str(context.get("variant_category") or "").strip():
            parts.append('variant parallel "alt art"')
    parts.append('-background -employment -hiring -candidate -payroll -credit -loan -insurance')
    parts.append('-pokemon -lorcana -mtg')
    shaped = _compact_whitespace(" ".join(parts))
    return shaped or _compact_whitespace(query)


def _shape_youtube_query(query: str, context: dict[str, Any]) -> str:
    parts = ["One Piece Card Game"]
    if context.get("card_code"):
        parts.append(str(context.get("card_code")))
    if context.get("card_name"):
        parts.append(_compact_whitespace(context.get("card_name")))
    if context.get("set_name"):
        parts.append(_compact_whitespace(context.get("set_name")))
    if context.get("set_code"):
        parts.append(str(context.get("set_code")))
    mode = str(context.get("mode") or "general_discrepancy")
    if mode == "set_anomaly_check":
        parts.extend(["box opening", "case opening", "checklist", "pulls", "parallel"])
    elif str(context.get("card_type") or "").strip().lower() == "leader":
        parts.extend(["leader", "showcase", "review", "discussion"])
    else:
        parts.extend(["card showcase", "review"])
    if mode == "variant_discrepancy" or bool(context.get("is_variant")):
        parts.extend(["parallel art", "alt art", "variant"])
    shaped = _compact_whitespace(" ".join(parts))
    return shaped or _compact_whitespace(query)


def _build_research_request(
    *,
    server_id: str,
    query: str,
    card_code: str = "",
    set_code: str = "",
    max_results: int = 3,
    catalog_db_path: Path | None = None,
) -> dict[str, Any]:
    context = _load_catalog_research_context(
        query=query,
        card_code=card_code,
        set_code=set_code,
        catalog_db_path=catalog_db_path,
    )
    if server_id == "perplexity":
        shaped_query = _shape_perplexity_query(query, context)
    elif server_id == "youtube":
        shaped_query = _shape_youtube_query(query, context)
    else:
        shaped_query = _compact_whitespace(query)
    return {
        "context": context,
        "shaped_query": shaped_query,
        "arguments": _research_arguments(server_id, shaped_query, int(max_results)),
    }


def _research_tool_name(server_id: str) -> str:
    if server_id == "perplexity":
        return "perplexity_search"
    if server_id == "youtube":
        return "search_videos"
    if server_id == "justtcg":
        return ""
    raise McpInvocationError(f"No research tool configured for lane {server_id!r}.")


def _research_arguments(server_id: str, query: str, max_results: int) -> dict[str, Any]:
    if server_id == "perplexity":
        return {
            "query": query,
            "country": "US",
            "max_results": max(1, min(max_results, 5)),
            "max_tokens_per_page": 512,
        }
    if server_id == "youtube":
        return {"query": query, "maxResults": max(1, min(max_results, 5))}
    return {}


def _score_research_preview(
    *,
    lane_id: str,
    preview: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    raw_preview = str(preview or "").strip()
    normalized_preview = _normalize_match_text(raw_preview)
    score = 0.0
    signals: list[str] = []
    penalties: list[str] = []

    if not normalized_preview or normalized_preview == "[]":
        penalties.append("empty_preview")
        score -= 0.35

    card_code = str(context.get("card_code") or "").upper()
    if card_code and card_code.lower() in raw_preview.lower():
        signals.append("card_code_match")
        score += 0.35

    card_name = str(context.get("card_name") or "")
    if card_name:
        card_name_ratio = _token_hit_ratio(raw_preview, card_name)
        if card_name_ratio >= 0.95:
            signals.append("card_name_exact")
            score += 0.25
        elif card_name_ratio >= 0.55:
            signals.append("card_name_partial")
            score += 0.12

    set_code = str(context.get("set_code") or "").upper()
    if set_code and set_code.lower() in raw_preview.lower():
        signals.append("set_code_match")
        score += 0.10

    set_name = str(context.get("set_name") or "")
    if set_name:
        set_ratio = _token_hit_ratio(raw_preview, set_name)
        if set_ratio >= 0.95:
            signals.append("set_name_exact")
            score += 0.12
        elif set_ratio >= 0.55:
            signals.append("set_name_partial")
            score += 0.06

    one_piece_hits = [
        term for term in ONE_PIECE_SIGNAL_TERMS if term in raw_preview.lower()
    ]
    if one_piece_hits:
        signals.append("one_piece_context")
        score += 0.15

    if lane_id == "perplexity":
        official_hits = [
            term for term in OFFICIAL_REFERENCE_HINTS if term in raw_preview.lower()
        ]
        if official_hits:
            signals.append("official_reference_hint")
            score += 0.12
    if lane_id == "youtube":
        youtube_hits = [
            term for term in YOUTUBE_RELEVANCE_TERMS if term in normalized_preview
        ]
        if youtube_hits:
            signals.append("youtube_showcase_signal")
            score += 0.12

    mode = str(context.get("mode") or "")
    if mode == "variant_discrepancy":
        variant_hits = [
            term
            for term in (_normalize_match_text(item) for item in VARIANT_HINT_TERMS)
            if term and term in normalized_preview
        ]
        if variant_hits:
            signals.append("variant_discrepancy_signal")
            score += 0.08
    if mode == "set_anomaly_check":
        set_hits = [
            term
            for term in (_normalize_match_text(item) for item in SET_ANOMALY_HINT_TERMS)
            if term and term in normalized_preview
        ]
        if set_hits:
            signals.append("set_anomaly_signal")
            score += 0.08

    unrelated_hits = [
        term for term in UNRELATED_PENALTY_TERMS if term in raw_preview.lower()
    ]
    if unrelated_hits:
        penalties.extend(f"unrelated:{term}" for term in unrelated_hits[:3])
        score -= min(0.54, 0.18 * len(unrelated_hits))

    if not signals:
        penalties.append("low_specificity")
        score -= 0.10

    relevance_score = max(0.0, min(score, 1.0))
    return {
        "relevance_score": relevance_score,
        "signals": signals,
        "penalties": penalties,
    }


def _adjust_research_confidence(base_confidence: float, relevance_score: float) -> float:
    adjusted = float(base_confidence) + (float(relevance_score) - 0.5) * 0.5
    return max(0.05, min(adjusted, 0.95))


def _store_research_review_lead(
    *,
    lead: dict[str, Any],
    state_db_path: Path | None = None,
) -> dict[str, Any]:
    db_path = ensure_governance_state_db(state_db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO research_review_leads (
                lead_key,
                created_at,
                updated_at,
                lane_id,
                tool_name,
                query_text,
                card_code,
                set_code,
                lead_type,
                source_type,
                trust_classification,
                approval_class,
                blocked_from_truth_authority,
                authority_cross_check_required,
                cross_check_targets_json,
                confidence,
                outcome,
                review_status,
                title,
                summary,
                evidence_json,
                provenance_json,
                governance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(lead.get("lead_key") or ""),
                str(lead.get("created_at") or current_timestamp()),
                str(lead.get("updated_at") or current_timestamp()),
                str(lead.get("lane_id") or ""),
                str(lead.get("tool_name") or ""),
                str(lead.get("query_text") or ""),
                str(lead.get("card_code") or ""),
                str(lead.get("set_code") or ""),
                str(lead.get("lead_type") or "review_lead"),
                str(lead.get("source_type") or ""),
                str(lead.get("trust_classification") or ""),
                str(lead.get("approval_class") or ""),
                1 if lead.get("blocked_from_truth_authority", True) else 0,
                1 if lead.get("authority_cross_check_required", True) else 0,
                json.dumps(_make_json_safe(lead.get("cross_check_targets") or [])),
                _safe_float(lead.get("confidence"), 0.0),
                str(lead.get("outcome") or "review_required"),
                str(lead.get("review_status") or "pending"),
                str(lead.get("title") or ""),
                str(lead.get("summary") or ""),
                json.dumps(_make_json_safe(lead.get("evidence") or {})),
                json.dumps(_make_json_safe(lead.get("provenance") or {})),
                json.dumps(_make_json_safe(lead.get("governance") or {})),
            ),
        )
        conn.commit()
    return lead


def list_research_review_leads(
    *,
    limit: int = 20,
    state_db_path: Path | None = None,
) -> dict[str, Any]:
    db_path = Path(state_db_path or DEFAULT_MCP_STATE_DB_PATH)
    if not db_path.is_file():
        return {"total": 0, "pending": 0, "items": []}
    safe_limit = max(1, min(int(limit), 100))
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        total = int(
            conn.execute("SELECT COUNT(*) FROM research_review_leads").fetchone()[0]
        )
        pending = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM research_review_leads
                WHERE lower(coalesce(review_status, 'pending')) = 'pending'
                """
            ).fetchone()[0]
        )
        rows = conn.execute(
            """
            SELECT *
            FROM research_review_leads
            ORDER BY
                CASE WHEN lower(coalesce(review_status, 'pending')) = 'pending' THEN 0 ELSE 1 END ASC,
                confidence DESC,
                id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    items = []
    for row in rows:
        governance = _json_loads(row["governance_json"], {})
        items.append(
            {
                "lead_key": str(row["lead_key"] or ""),
                "created_at": str(row["created_at"] or ""),
                "lane_id": str(row["lane_id"] or ""),
                "tool_name": str(row["tool_name"] or ""),
                "query_text": str(row["query_text"] or ""),
                "card_code": str(row["card_code"] or ""),
                "set_code": str(row["set_code"] or ""),
                "lead_type": str(row["lead_type"] or ""),
                "source_type": str(row["source_type"] or ""),
                "trust_classification": str(row["trust_classification"] or ""),
                "approval_class": str(row["approval_class"] or ""),
                "blocked_from_truth_authority": bool(
                    _safe_int(row["blocked_from_truth_authority"], 1)
                ),
                "authority_cross_check_required": bool(
                    _safe_int(row["authority_cross_check_required"], 1)
                ),
                "cross_check_targets": list(
                    _json_loads(row["cross_check_targets_json"], [])
                ),
                "confidence": _safe_float(row["confidence"], 0.0),
                "outcome": str(row["outcome"] or ""),
                "review_status": str(row["review_status"] or ""),
                "title": str(row["title"] or ""),
                "summary": str(row["summary"] or ""),
                "evidence": _json_loads(row["evidence_json"], {}),
                "provenance": _json_loads(row["provenance_json"], {}),
                "governance": governance,
                "relevance_score": _safe_float(
                    dict(governance or {}).get("relevance_score"),
                    0.0,
                ),
            }
        )
    return {"total": total, "pending": pending, "items": items}


def run_governed_research(
    *,
    lane_id: str,
    query: str,
    card_code: str = "",
    set_code: str = "",
    max_results: int = 3,
    lead_type: str = "review_lead",
    catalog_db_path: Path | None = None,
    policy_path: Path | None = None,
    mcp_config_path: Path | None = None,
    state_db_path: Path | None = None,
) -> dict[str, Any]:
    lane_key = _normalize_server_id(lane_id)
    if not lane_key:
        raise McpInvocationError("lane_id is required.")
    if not str(query or "").strip():
        raise McpInvocationError("query is required.")

    policy = load_mcp_policy(policy_path)
    policy_map = _policy_lane_map(policy)
    lane_policy = dict(policy_map.get(lane_key) or {})
    if not lane_policy:
        raise McpInvocationError(f"Lane {lane_key!r} is not classified in policy.")
    if not bool(lane_policy.get("research_enabled", False)):
        raise McpInvocationError(
            f"Lane {lane_key!r} is not approved for governed research."
        )
    if lane_key not in STDIO_SUPPORTED_LANES:
        raise McpInvocationError(
            f"Lane {lane_key!r} is not wired for Miru-side invocation in this pass."
        )

    tool_name = _research_tool_name(lane_key)
    if not tool_name:
        raise McpInvocationError(
            f"Lane {lane_key!r} has no research tool routed in this pass."
        )
    config_payload = load_mcp_config(mcp_config_path)
    research_request = _build_research_request(
        server_id=lane_key,
        query=str(query).strip(),
        card_code=str(card_code or "").strip().upper(),
        set_code=str(set_code or "").strip().upper(),
        max_results=int(max_results),
        catalog_db_path=catalog_db_path,
    )
    call_result = invoke_stdio_mcp_lane(
        server_id=lane_key,
        tool_name=tool_name,
        arguments=dict(research_request.get("arguments") or {}),
        mcp_config_path=mcp_config_path,
        timeout_seconds=45.0,
    )
    preview = str(call_result.get("preview") or "").strip()
    quality = _score_research_preview(
        lane_id=lane_key,
        preview=preview,
        context=dict(research_request.get("context") or {}),
    )
    base_confidence = _safe_float(lane_policy.get("default_confidence"), 0.0)
    adjusted_confidence = _adjust_research_confidence(
        base_confidence,
        _safe_float(quality.get("relevance_score"), 0.0),
    )
    governance = {
        "source_type": str(lane_policy.get("source_type") or ""),
        "trust_classification": str(lane_policy.get("approval_class") or ""),
        "blocked_from_truth_authority": bool(
            lane_policy.get("blocked_from_truth_authority", True)
        ),
        "authority_cross_check_required": True,
        "cross_check_targets": list(
            lane_policy.get("cross_check_targets")
            or (policy.get("research_governance") or {}).get(
                "default_cross_check_targets", []
            )
        ),
        "confidence": adjusted_confidence,
        "base_confidence": base_confidence,
        "relevance_score": _safe_float(quality.get("relevance_score"), 0.0),
        "quality_signals": list(quality.get("signals") or []),
        "quality_penalties": list(quality.get("penalties") or []),
        "outcome": str(
            (policy.get("research_governance") or {}).get(
                "default_outcome", "review_required"
            )
        ),
        "fail_closed": True,
    }
    evidence = {
        "captured_at": current_timestamp(),
        "query": str(query).strip(),
        "shaped_query": str(research_request.get("shaped_query") or "").strip(),
        "catalog_context": _make_json_safe(research_request.get("context") or {}),
        "tool_result": _make_json_safe(call_result.get("result") or {}),
        "preview": preview,
        "quality": _make_json_safe(quality),
    }
    resolved_context = dict(research_request.get("context") or {})
    resolved_card_code = (
        str(card_code or "").strip().upper()
        or str(resolved_context.get("card_code") or "").strip().upper()
    )
    resolved_set_code = (
        str(set_code or "").strip().upper()
        or str(resolved_context.get("set_code") or "").strip().upper()
    )
    descriptor_parts = [lane_key, "review lead"]
    if resolved_card_code:
        descriptor_parts.append(resolved_card_code)
    if str(resolved_context.get("card_name") or "").strip():
        descriptor_parts.append(str(resolved_context.get("card_name") or "").strip())
    elif resolved_set_code:
        descriptor_parts.append(resolved_set_code)
    lead = {
        "lead_key": f"{lane_key}-{uuid.uuid4().hex}",
        "created_at": current_timestamp(),
        "updated_at": current_timestamp(),
        "lane_id": lane_key,
        "tool_name": tool_name,
        "query_text": str(query).strip(),
        "card_code": resolved_card_code,
        "set_code": resolved_set_code,
        "lead_type": str(lead_type or "review_lead").strip() or "review_lead",
        "source_type": str(lane_policy.get("source_type") or ""),
        "trust_classification": str(lane_policy.get("approval_class") or ""),
        "approval_class": str(lane_policy.get("approval_class") or ""),
        "blocked_from_truth_authority": bool(
            lane_policy.get("blocked_from_truth_authority", True)
        ),
        "authority_cross_check_required": True,
        "cross_check_targets": list(governance.get("cross_check_targets") or []),
        "confidence": adjusted_confidence,
        "outcome": str(governance.get("outcome") or "review_required"),
        "review_status": "pending",
        "title": _truncate_text(" ".join(descriptor_parts), 100),
        "summary": preview or f"{lane_key} returned evidence that requires cross-check review.",
        "evidence": evidence,
        "provenance": {
            "lane_id": lane_key,
            "tool_name": tool_name,
            "policy_path": str(policy.get("policy_path") or ""),
            "config_path": str(config_payload.get("config_path") or ""),
            "research_arguments": _make_json_safe(research_request.get("arguments") or {}),
        },
        "governance": governance,
    }
    return {
        "ok": True,
        "lead": _store_research_review_lead(lead=lead, state_db_path=state_db_path),
        "call_result": call_result,
    }
