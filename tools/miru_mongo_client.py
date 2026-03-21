from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, MutableMapping

try:
    from pymongo import MongoClient
    from pymongo.collection import Collection
    from pymongo.database import Database
    from pymongo.errors import PyMongoError
except Exception:  # pragma: no cover - handled at runtime
    MongoClient = None  # type: ignore[assignment]
    Collection = Any  # type: ignore[assignment]
    Database = Any  # type: ignore[assignment]

    class PyMongoError(RuntimeError):
        pass


DEFAULT_MIRU_MONGO_URI = "mongodb://127.0.0.1:27017"
DEFAULT_MIRU_MONGO_DB = "miru_staging"


def _env_flag(value: str | None, *, default: bool = False) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    return text in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MiruMongoSettings:
    uri: str = DEFAULT_MIRU_MONGO_URI
    db_name: str = DEFAULT_MIRU_MONGO_DB
    enabled: bool = False
    app_name: str = "miru-intake"
    server_selection_timeout_ms: int = 2500
    connect_timeout_ms: int = 2500
    socket_timeout_ms: int = 5000

    @classmethod
    def from_env(
        cls,
        *,
        environ: MutableMapping[str, str] | None = None,
        default_enabled: bool = False,
    ) -> "MiruMongoSettings":
        env = environ if environ is not None else os.environ
        uri = str(env.get("MIRU_MONGO_URI") or DEFAULT_MIRU_MONGO_URI).strip() or DEFAULT_MIRU_MONGO_URI
        db_name = str(env.get("MIRU_MONGO_DB") or DEFAULT_MIRU_MONGO_DB).strip() or DEFAULT_MIRU_MONGO_DB
        app_name = str(env.get("MIRU_MONGO_APP_NAME") or "miru-intake").strip() or "miru-intake"
        uri_explicit = bool(str(env.get("MIRU_MONGO_URI") or "").strip())
        enabled = _env_flag(env.get("MIRU_MONGO_ENABLED"), default=(default_enabled or uri_explicit))
        return cls(
            uri=uri,
            db_name=db_name,
            enabled=enabled,
            app_name=app_name,
            server_selection_timeout_ms=max(int(env.get("MIRU_MONGO_SERVER_SELECTION_TIMEOUT_MS") or 2500), 250),
            connect_timeout_ms=max(int(env.get("MIRU_MONGO_CONNECT_TIMEOUT_MS") or 2500), 250),
            socket_timeout_ms=max(int(env.get("MIRU_MONGO_SOCKET_TIMEOUT_MS") or 5000), 250),
        )


class MiruMongoClient:
    def __init__(self, settings: MiruMongoSettings | None = None) -> None:
        self.settings = settings or MiruMongoSettings.from_env()
        self._client: MongoClient | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled)

    def available(self) -> bool:
        return self.enabled and MongoClient is not None

    def connect(self) -> MongoClient:
        if not self.enabled:
            raise RuntimeError("Miru Mongo staging is disabled.")
        if MongoClient is None:
            raise RuntimeError("pymongo is not installed; Mongo staging is unavailable.")
        if self._client is None:
            self._client = MongoClient(
                self.settings.uri,
                appname=self.settings.app_name,
                serverSelectionTimeoutMS=int(self.settings.server_selection_timeout_ms),
                connectTimeoutMS=int(self.settings.connect_timeout_ms),
                socketTimeoutMS=int(self.settings.socket_timeout_ms),
                retryWrites=False,
            )
        return self._client

    def database(self) -> Database:
        return self.connect()[self.settings.db_name]

    def collection(self, name: str) -> Collection:
        return self.database()[str(name or "").strip()]

    def ping(self) -> dict[str, Any]:
        client = self.connect()
        reply = client.admin.command("ping")
        return {
            "ok": bool(reply.get("ok")),
            "uri": self.settings.uri,
            "db_name": self.settings.db_name,
            "app_name": self.settings.app_name,
        }

    def health_check(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "enabled": self.enabled,
            "configured_uri": self.settings.uri,
            "db_name": self.settings.db_name,
            "driver_available": MongoClient is not None,
            "connected": False,
            "error": "",
        }
        if not self.enabled:
            info["error"] = "disabled"
            return info
        if MongoClient is None:
            info["error"] = "pymongo_missing"
            return info
        try:
            ping = self.ping()
            info["connected"] = bool(ping.get("ok"))
        except Exception as exc:  # pragma: no cover - exercised in live verification
            info["error"] = str(exc)
        return info

    def close(self) -> None:
        if self._client is None:
            return
        self._client.close()
        self._client = None
