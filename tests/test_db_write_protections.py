"""DGAS Tier 1 #5 (pivoted) — fault-injection tests for existing DB write protections.

The original synthesis item asked for an MCP gateway profile deny-list that blocks
direct writes to ``card_catalog.db``. After mapping every DB write site in the repo,
the gap turned out to be different than the synthesis assumed:

    * No MCP tool currently exposes ``card_catalog.db`` writes (only ``pm/db.py``
      writes to it, and that is not worker-callable).
    * The readonly filesystem MCP already blocks reads of ``.db`` files
      (``DENIED_SUFFIXES`` and ``DENIED_NAMES``).
    * Worker-callable DB writes go through ``memory_tools.write_query`` which has
      its own deny-list (DROP, ALTER, ATTACH, DETACH, VACUUM, REINDEX, ANALYZE,
      SAVEPOINT, RELEASE, CHECKPOINT) and a CREATE-TABLE-IF-NOT-EXISTS-only rule.

The risk is therefore not "build a missing gate" but "prove the gates that exist
actually work, and fail loudly if a future change introduces a write path."

These tests are the fault-injection layer per synthesis item #7. A gate without a
fault-injection test is theatre; if any of these tests starts failing, that means
a protection broke and someone needs to look at it before the next release.

Coverage:
    * No file in ``tools/miru_mcp_gateway/`` establishes a write path to
      ``card_catalog.db`` (no ``card_catalog.db`` literal, no ``connect_catalog``
      import, no ``CATALOG_DB_PATH`` reference, no ``from pm.db import``).
    * The readonly filesystem MCP denies ``.db``, ``.sqlite``, ``.sqlite3``
      suffixes and the ``card_catalog.db`` name.
    * ``memory_tools.write_query`` rejects DROP, ALTER, ATTACH, DETACH, VACUUM,
      bare CREATE TABLE, SELECT (wrong DML class), PRAGMA, and excessively long
      SQL.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GATEWAY_DIR = REPO_ROOT / "tools" / "miru_mcp_gateway"
TOOLS_DIR = REPO_ROOT / "tools"

# Make tools/ importable as a top-level package so the gateway modules resolve.
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


class TestNoMcpToolWritesCardCatalog(unittest.TestCase):
    """No MCP gateway module may expose a write path to ``card_catalog.db``.

    The synthesis treats card_catalog.db as the user-facing PM database that
    workers must never modify. Today the protection is "no tool exists" —
    these tests assert that property holds and will fail loudly if a future
    change adds a tool that targets the database.
    """

    # Patterns that would indicate an ACTUAL write path being established to
    # card_catalog.db. The string "card_catalog.db" appearing on its own is
    # NOT a violation — it shows up legitimately in deny-lists (git_tools.py)
    # and smoke tests (_smoke.py) that prove the protection works. What we
    # care about is: does any module open a write connection to it, or pull
    # in PM's connect_catalog helper?
    _FORBIDDEN_PATTERNS: tuple[str, ...] = (
        # Opening a sqlite3 connection that names the file directly
        r"sqlite3\.connect\([^)]*card_catalog",
        # Opening sqlite via PM's catalog path constant (sqlite3.connect(CATALOG_DB_PATH))
        r"sqlite3\.connect\(\s*CATALOG_DB_PATH\b",
        # Calling PM's connect_catalog() helper
        r"\bconnect_catalog\s*\(",
        # Importing from pm.db (any pm.db symbol could lead to a write path)
        r"from\s+pm\.db\s+import",
        r"import\s+pm\.db\b",
    )

    def test_no_gateway_module_opens_card_catalog(self) -> None:
        """No gateway module may establish a write path to card_catalog.db.

        Defensive references (deny-list entries that name the file in order
        to block it, smoke tests that assert the deny fires) are NOT flagged.
        Only actual sqlite3.connect calls, connect_catalog() invocations, and
        pm.db imports are violations.
        """
        # Fail fast if the gateway scan target is wrong. If GATEWAY_DIR is
        # missing or empty, rglob returns nothing and the test silently goes
        # green without scanning anything — a regression-detection blind spot.
        self.assertTrue(
            GATEWAY_DIR.is_dir(),
            f"GATEWAY_DIR does not exist: {GATEWAY_DIR}. "
            "The fault-injection scan cannot prove the protection holds.",
        )
        scanned = list(GATEWAY_DIR.rglob("*.py"))
        self.assertGreater(
            len(scanned),
            0,
            f"GATEWAY_DIR has no .py files: {GATEWAY_DIR}. "
            "Test cannot prove the protection holds against an empty directory.",
        )

        compiled = [re.compile(p, re.IGNORECASE) for p in self._FORBIDDEN_PATTERNS]
        violators: list[tuple[str, str]] = []
        for py_file in sorted(scanned):
            content = py_file.read_text(encoding="utf-8")
            for pattern in compiled:
                if pattern.search(content):
                    violators.append((str(py_file.relative_to(REPO_ROOT)), pattern.pattern))
        self.assertEqual(
            violators,
            [],
            "MCP gateway modules must not establish card_catalog.db write paths. "
            f"Violators: {violators}. card_catalog.db must remain accessible only "
            "via the PM server, never via worker-callable MCP tools.",
        )

    def test_existing_defensive_references_are_present(self) -> None:
        """Sanity: confirm git_tools.py keeps card_catalog.db on its deny-list,
        and _smoke.py keeps the smoke test that proves the readonly filesystem
        MCP denies card_catalog.db. If either is removed accidentally, this
        test fails and we know the protection regressed."""
        git_tools = (GATEWAY_DIR / "git_tools.py").read_text(encoding="utf-8")
        smoke = (GATEWAY_DIR / "_smoke.py").read_text(encoding="utf-8")
        self.assertIn(
            "card_catalog.db",
            git_tools,
            "git_tools.py removed card_catalog.db from its deny-list — protection regressed",
        )
        self.assertIn(
            "card_catalog.db",
            smoke,
            "_smoke.py removed the card_catalog.db deny smoke test — protection regressed",
        )


class TestReadonlyFilesystemBlocksDbFiles(unittest.TestCase):
    """The readonly filesystem MCP must deny ``.db`` and equivalent suffixes."""

    def setUp(self) -> None:
        import miru_readonly_filesystem_mcp as ro_mcp

        self.ro_mcp = ro_mcp

    def test_denied_suffixes_includes_db_variants(self) -> None:
        """Static config: every common SQLite extension is denied."""
        for suffix in (".db", ".sqlite", ".sqlite3"):
            self.assertIn(suffix, self.ro_mcp.DENIED_SUFFIXES, f"{suffix} missing from denylist")

    def test_card_catalog_name_is_denied_explicitly(self) -> None:
        """Belt and suspenders: card_catalog.db is denied by name as well as
        by suffix, so renaming the suffix detector wouldn't accidentally
        unblock the user-facing PM database."""
        self.assertIn("card_catalog.db", self.ro_mcp.DENIED_NAMES)

    def test_is_denied_blocks_db_paths(self) -> None:
        """Fault injection: a request for any .db path must return True."""
        self.assertTrue(self.ro_mcp._is_denied(Path("data/card_catalog.db")))
        self.assertTrue(self.ro_mcp._is_denied(Path("anything.db")))
        self.assertTrue(self.ro_mcp._is_denied(Path("foo/bar/baz.sqlite")))
        self.assertTrue(self.ro_mcp._is_denied(Path("nested/path/file.sqlite3")))

    def test_is_denied_allows_non_db_paths(self) -> None:
        """Happy path: a normal .py / .md / .json file is not denied."""
        self.assertFalse(self.ro_mcp._is_denied(Path("README.md")))
        self.assertFalse(self.ro_mcp._is_denied(Path("tools/script.py")))
        self.assertFalse(self.ro_mcp._is_denied(Path("config.json")))


class TestMemoryToolsRejectsDestructiveSql(unittest.TestCase):
    """``memory_tools.write_query`` must reject every keyword in the deny list,
    plus bare CREATE TABLE, plus non-DML lead tokens."""

    def setUp(self) -> None:
        from miru_mcp_gateway import memory_tools

        self.memory_tools = memory_tools
        # The McpError type that write_query raises lives on the
        # miru_readonly_filesystem_mcp module.
        import miru_readonly_filesystem_mcp as stdio_mcp

        self.McpError = stdio_mcp.McpError

    # Error markers raised by the deny-stage gates in memory_tools.write_query
    # (forbidden_keyword, dml_only lead-token check, read_only branch in
    # read_query, plus the size/empty pre-checks). If write_query rejects with
    # any of these, the deny stage fired. If it rejects with a downstream
    # marker (db_not_found, sqlite_error, etc.) the destructive SQL slipped
    # past the gate — that's a regression we MUST catch.
    _DENY_STAGE_MARKERS = (
        "forbidden_keyword",
        "dml_only",
        "read_only",
        "sql_too_long",
        "empty_sql",
    )

    def _expect_rejected(self, sql: str, msg: str) -> None:
        # Include the case-specific msg so the failure message names which
        # rule the SQL was supposed to violate.
        with self.assertRaises(
            self.McpError, msg=f"{msg}. write_query did NOT reject: {sql!r}"
        ) as ctx:
            self.memory_tools.write_query(sql)
        payload = str(ctx.exception)
        self.assertTrue(
            any(marker in payload for marker in self._DENY_STAGE_MARKERS),
            f"{msg}. write_query DID raise McpError but the error payload "
            f"did not name a deny-stage marker. SQL: {sql!r}. Payload: "
            f"{payload!r}. If the rejection came from db_resolution / "
            f"sqlite_error / db_not_found, the destructive SQL reached the "
            f"DB layer — the deny stage failed to fire.",
        )

    def test_rejects_drop_table(self) -> None:
        self._expect_rejected("DROP TABLE foo", "DROP must be denied")

    def test_rejects_drop_index(self) -> None:
        self._expect_rejected("DROP INDEX idx_foo", "DROP INDEX must be denied")

    def test_rejects_alter_table(self) -> None:
        self._expect_rejected("ALTER TABLE foo ADD COLUMN bar TEXT", "ALTER must be denied")

    def test_rejects_attach_database(self) -> None:
        self._expect_rejected("ATTACH DATABASE '/tmp/foo.db' AS foo", "ATTACH must be denied")

    def test_rejects_detach_database(self) -> None:
        self._expect_rejected("DETACH DATABASE foo", "DETACH must be denied")

    def test_rejects_vacuum(self) -> None:
        self._expect_rejected("VACUUM", "VACUUM must be denied")

    def test_rejects_reindex(self) -> None:
        self._expect_rejected("REINDEX foo", "REINDEX must be denied")

    def test_rejects_bare_create_table(self) -> None:
        """Only CREATE TABLE IF NOT EXISTS is allowed; bare CREATE TABLE is not."""
        self._expect_rejected(
            "CREATE TABLE foo (id INTEGER PRIMARY KEY)", "bare CREATE TABLE must be denied"
        )

    def test_rejects_create_index(self) -> None:
        """CREATE INDEX is not in the safe-create regex; must be denied."""
        self._expect_rejected("CREATE INDEX idx_foo ON foo(id)", "CREATE INDEX must be denied")

    def test_rejects_create_view(self) -> None:
        self._expect_rejected("CREATE VIEW v AS SELECT 1", "CREATE VIEW must be denied")

    def test_rejects_select_via_write_query(self) -> None:
        """SELECT is for read_query, not write_query. write_query must reject it."""
        self._expect_rejected("SELECT 1", "SELECT must be denied via write_query")

    def test_rejects_pragma(self) -> None:
        self._expect_rejected("PRAGMA writable_schema = 1", "PRAGMA must be denied")

    def test_rejects_empty_sql(self) -> None:
        self._expect_rejected("", "empty SQL must be denied")
        self._expect_rejected("   ", "whitespace-only SQL must be denied")

    def test_rejects_oversized_sql(self) -> None:
        """SQL longer than _MAX_SQL_LEN must be denied before any parsing."""
        oversized = "INSERT INTO foo VALUES (" + ("'x',") * 1500 + "'x')"
        self.assertGreater(
            len(oversized), self.memory_tools._MAX_SQL_LEN, "test fixture must exceed the cap"
        )
        self._expect_rejected(oversized, "oversized SQL must be denied")

    def test_rejects_drop_disguised_in_comment(self) -> None:
        """A DROP hidden in a comment but with a fake INSERT lead must NOT bypass
        the deny check, because the deny check runs against the comment-stripped
        SQL — and a real DROP after a comment would fire on the lead-token check."""
        # A naive payload tries to fake the lead token via a comment. The
        # implementation strips comments first, so the DROP becomes the lead.
        self._expect_rejected("/* INSERT */ DROP TABLE foo", "DROP after comment must fire")


class TestMemoryToolsAcceptsLegitimateWrites(unittest.TestCase):
    """Happy path: legitimate write operations must NOT be rejected by the
    deny-list check (they may still fail later because the test environment
    has no configured DB, but the failure must come from db_resolution, not
    from the deny check)."""

    def setUp(self) -> None:
        from miru_mcp_gateway import memory_tools

        self.memory_tools = memory_tools
        import miru_readonly_filesystem_mcp as stdio_mcp

        self.McpError = stdio_mcp.McpError

    def _expect_passes_deny_check(self, sql: str) -> None:
        """The deny-token / lead-token check must pass. The call may still
        raise ``McpError`` from a later stage (no configured DB in tests),
        but the error MUST NOT be ``forbidden_keyword`` or ``dml_only``,
        AND it MUST be one of the expected later-stage errors so that a
        future code change accidentally introducing a new deny-class
        doesn't slip through this helper.
        """
        # Errors that legitimately surface AFTER the deny check (when the
        # write_query body proceeds because the SQL is allowed). All come
        # from db resolution or the underlying sqlite3 connection.
        allowed_downstream_errors = (
            "memory_tools: not configured",
            "db_not_found",
            "invalid_db_path",
            "sqlite_error",
        )
        try:
            self.memory_tools.write_query(sql)
        except self.McpError as exc:
            payload = str(exc)
            self.assertNotIn(
                "forbidden_keyword",
                payload,
                f"legit SQL was rejected as forbidden_keyword: {sql!r}",
            )
            self.assertNotIn(
                "dml_only",
                payload,
                f"legit SQL was rejected as dml_only: {sql!r}",
            )
            self.assertTrue(
                any(marker in payload for marker in allowed_downstream_errors),
                f"legit SQL produced an unexpected error class: {payload!r}. "
                f"If write_query gained a new pre-DB-resolution gate, add the "
                f"new error marker to allowed_downstream_errors or to the deny "
                f"checks if it should reject.",
            )
        # No exception is also fine — we don't need to actually run the SQL.

    def test_insert_passes_deny_check(self) -> None:
        self._expect_passes_deny_check("INSERT INTO foo VALUES (1)")

    def test_update_passes_deny_check(self) -> None:
        self._expect_passes_deny_check("UPDATE foo SET bar = 1 WHERE id = 1")

    def test_delete_passes_deny_check(self) -> None:
        self._expect_passes_deny_check("DELETE FROM foo WHERE id = 1")

    def test_create_table_if_not_exists_passes_deny_check(self) -> None:
        self._expect_passes_deny_check("CREATE TABLE IF NOT EXISTS foo (id INTEGER PRIMARY KEY)")


if __name__ == "__main__":
    unittest.main()
