"""Tests for tools/check_canon_freshness.py (PRO-337)."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

# Ensure tools/ is importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools import check_canon_freshness as ccf  # noqa: E402


def _utc_today() -> date:
    """UTC today — matches the tool's logic. Avoids local-timezone test flakiness near midnight.

    Per CodeRabbit feedback on PR #152.
    """
    return datetime.now(UTC).date()


# ----------------------------------------------------------------------------
# Fixture helpers — build a fake repo-shaped directory in tmp_path
# ----------------------------------------------------------------------------


def _make_canon_file(
    repo: Path, rel_path: str, *, date_field: str | None, date_val: str | None
) -> Path:
    """Write a canon-shaped file. Pass date_field=None to omit the stamp."""
    p = repo / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    if date_field is None or date_val is None:
        p.write_text("# Title\n\nNo date stamp here.\n", encoding="utf-8")
    else:
        # Mimic the real canon front-matter shape: code block at top with the field.
        p.write_text(
            f"# Title\n\n```text\nSome header line\n{date_field}: {date_val}\n```\n\nBody.\n",
            encoding="utf-8",
        )
    return p


def _seed_minimal_repo(tmp: Path, *, today: str = "2026-05-09") -> Path:
    """Create a fake repo with the minimum canon-file shape so glob expansion works.

    Does NOT pre-seed the three literal canon files (CLAUDE.md, AGENTS.md,
    miru-context/team-charter.md). Tests that use `check_canon_freshness()`
    directly and only inspect specific files use this and pick what to create.

    Tests that invoke `main()` (which exits 1 on `missing_file` per CodeRabbit
    feedback on PR #152) should use `_seed_full_repo` instead so the literal
    canon files exist.
    """
    repo = tmp / "repo"
    repo.mkdir()
    (repo / ".miru" / "overlays").mkdir(parents=True)
    (repo / ".miru" / "reference").mkdir(parents=True)
    (repo / "miru-context").mkdir()
    return repo


def _seed_full_repo(tmp: Path) -> Path:
    """Like _seed_minimal_repo + pre-seed the 3 literal canon files (fresh UTC today).

    Use for tests that invoke `main()` and don't want missing_file noise from
    the literals. Tests can still overwrite individual literal files to test
    specific behavior (the file gets replaced).
    """
    repo = _seed_minimal_repo(tmp)
    fresh = _utc_today().isoformat()
    _make_canon_file(repo, "CLAUDE.md", date_field="Effective", date_val=fresh)
    _make_canon_file(repo, "AGENTS.md", date_field="Last synced", date_val=fresh)
    _make_canon_file(
        repo, "miru-context/team-charter.md", date_field="Last updated", date_val=fresh
    )
    return repo


# ----------------------------------------------------------------------------
# Tests — all-fresh case
# ----------------------------------------------------------------------------


def test_all_fresh_returns_exit_zero(tmp_path: Path):
    """All canon files within threshold → exit 0, all results 'fresh'."""
    repo = _seed_minimal_repo(tmp_path)
    today = date(2026, 5, 9)

    _make_canon_file(repo, "CLAUDE.md", date_field="Effective", date_val="2026-05-09")
    _make_canon_file(repo, "AGENTS.md", date_field="Last synced", date_val="2026-05-08")
    _make_canon_file(
        repo, "miru-context/team-charter.md", date_field="Last updated", date_val="2026-05-09"
    )
    _make_canon_file(
        repo, ".miru/overlays/workflow-git.md", date_field="Last reviewed", date_val="2026-05-09"
    )
    _make_canon_file(
        repo, ".miru/reference/ports.md", date_field="Last reviewed", date_val="2026-05-08"
    )

    results = ccf.check_canon_freshness(repo, threshold=7, warn_threshold=5, today=today)

    assert len(results) == 5
    assert all(r.status == "fresh" for r in results), [
        (r.path, r.status, r.detail) for r in results
    ]


def test_main_exit_zero_when_all_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """End-to-end CLI: all-fresh → main() returns 0."""
    repo = _seed_full_repo(tmp_path)
    rc = ccf.main(["--repo-root", str(repo), "--threshold", "7", "--warn-threshold", "5"])
    assert rc == 0


# ----------------------------------------------------------------------------
# Tests — stale case (exit 1)
# ----------------------------------------------------------------------------


def test_one_stale_file_returns_exit_one(tmp_path: Path):
    repo = _seed_minimal_repo(tmp_path)
    today = date(2026, 5, 9)

    _make_canon_file(repo, "CLAUDE.md", date_field="Effective", date_val="2026-05-09")
    # Stale: 2026-04-15 is > 7 days before 2026-05-09
    _make_canon_file(
        repo, ".miru/overlays/stale.md", date_field="Last reviewed", date_val="2026-04-15"
    )

    results = ccf.check_canon_freshness(repo, threshold=7, warn_threshold=5, today=today)

    stale = [r for r in results if r.status == "stale"]
    assert len(stale) == 1
    assert "stale.md" in stale[0].path
    assert stale[0].days_old == 24


def test_main_stale_returns_one(tmp_path: Path):
    repo = _seed_full_repo(tmp_path)
    # Overwrite CLAUDE.md to be stale (overrides the fresh one from _seed_full_repo)
    _make_canon_file(repo, "CLAUDE.md", date_field="Effective", date_val="2026-04-15")
    rc = ccf.main(["--repo-root", str(repo), "--threshold", "7"])
    assert rc == 1


# ----------------------------------------------------------------------------
# Tests — warn zone
# ----------------------------------------------------------------------------


def test_warn_zone_does_not_fail(tmp_path: Path):
    """Files in [warn_threshold, threshold] are 'warn' but don't fail."""
    repo = _seed_minimal_repo(tmp_path)
    today = date(2026, 5, 9)

    # 6 days old: between warn (5) and fail (7) → warn
    _make_canon_file(repo, "CLAUDE.md", date_field="Effective", date_val="2026-05-03")

    results = ccf.check_canon_freshness(repo, threshold=7, warn_threshold=5, today=today)

    claude = next(r for r in results if Path(r.path).name == "CLAUDE.md")
    assert claude.status == "warn"
    assert claude.days_old == 6


def test_main_warn_returns_zero(tmp_path: Path):
    """Today's date is fresh, should pass with default thresholds."""
    repo = _seed_full_repo(tmp_path)
    rc = ccf.main(["--repo-root", str(repo), "--threshold", "7", "--warn-threshold", "5"])
    assert rc == 0


# ----------------------------------------------------------------------------
# Tests — missing stamp
# ----------------------------------------------------------------------------


def test_missing_stamp_returns_exit_one(tmp_path: Path):
    repo = _seed_minimal_repo(tmp_path)
    today = date(2026, 5, 9)

    _make_canon_file(repo, "CLAUDE.md", date_field=None, date_val=None)

    results = ccf.check_canon_freshness(repo, threshold=7, warn_threshold=5, today=today)

    claude = next(r for r in results if Path(r.path).name == "CLAUDE.md")
    assert claude.status == "missing_stamp"
    assert claude.field_name is None
    assert claude.stamp_date is None


def test_main_missing_stamp_exits_one(tmp_path: Path):
    repo = _seed_full_repo(tmp_path)
    # Overwrite CLAUDE.md to have no stamp (others remain fresh from _seed_full_repo)
    _make_canon_file(repo, "CLAUDE.md", date_field=None, date_val=None)
    rc = ccf.main(["--repo-root", str(repo)])
    assert rc == 1


# ----------------------------------------------------------------------------
# Tests — bad date format
# ----------------------------------------------------------------------------


def test_bad_date_format_returns_missing_stamp(tmp_path: Path):
    """A malformed date doesn't match the regex → reads as 'missing_stamp'."""
    repo = _seed_minimal_repo(tmp_path)
    today = date(2026, 5, 9)

    # Date like "May 9, 2026" doesn't match \d{4}-\d{2}-\d{2}
    p = repo / "CLAUDE.md"
    p.write_text(
        "# Title\n\n```text\nLast reviewed: May 9, 2026\n```\n\nBody.\n",
        encoding="utf-8",
    )

    results = ccf.check_canon_freshness(repo, threshold=7, warn_threshold=5, today=today)

    claude = next(r for r in results if Path(r.path).name == "CLAUDE.md")
    assert claude.status == "missing_stamp"


def test_invalid_iso_date_returns_bad_date(tmp_path: Path):
    """A regex-matching date that fails .fromisoformat() → 'bad_date'."""
    repo = _seed_minimal_repo(tmp_path)
    today = date(2026, 5, 9)

    # 2026-13-45 matches the regex but isn't a real date
    p = repo / "CLAUDE.md"
    p.write_text(
        "# Title\n\n```text\nLast reviewed: 2026-13-45\n```\n\nBody.\n",
        encoding="utf-8",
    )

    results = ccf.check_canon_freshness(repo, threshold=7, warn_threshold=5, today=today)

    claude = next(r for r in results if Path(r.path).name == "CLAUDE.md")
    assert claude.status == "bad_date"
    assert claude.field_name is not None


# ----------------------------------------------------------------------------
# Tests — date-field-name variants
# ----------------------------------------------------------------------------


def test_recognizes_all_field_names(tmp_path: Path):
    repo = _seed_minimal_repo(tmp_path)
    today = date(2026, 5, 9)

    _make_canon_file(repo, "CLAUDE.md", date_field="Effective", date_val="2026-05-09")
    _make_canon_file(repo, "AGENTS.md", date_field="Last synced", date_val="2026-05-09")
    _make_canon_file(
        repo, "miru-context/team-charter.md", date_field="Last updated", date_val="2026-05-09"
    )
    _make_canon_file(repo, ".miru/overlays/x.md", date_field="Last reviewed", date_val="2026-05-09")

    results = ccf.check_canon_freshness(repo, threshold=7, warn_threshold=5, today=today)

    fields = {r.field_name for r in results if r.field_name}
    assert "Effective" in fields
    assert "Last synced" in fields
    assert "Last updated" in fields
    assert "Last reviewed" in fields
    assert all(r.status == "fresh" for r in results)


def test_field_name_is_case_insensitive(tmp_path: Path):
    repo = _seed_minimal_repo(tmp_path)
    today = date(2026, 5, 9)

    p = repo / "CLAUDE.md"
    p.write_text(
        "# Title\n\n```text\nLAST REVIEWED: 2026-05-09\n```\n",
        encoding="utf-8",
    )
    results = ccf.check_canon_freshness(repo, threshold=7, warn_threshold=5, today=today)
    claude = next(r for r in results if Path(r.path).name == "CLAUDE.md")
    assert claude.status == "fresh"
    assert claude.field_name and claude.field_name.upper() == "LAST REVIEWED"


# ----------------------------------------------------------------------------
# Tests — boundary cases (warn_threshold > threshold)
# ----------------------------------------------------------------------------


def test_warn_threshold_greater_than_threshold_exits_two(tmp_path: Path):
    repo = _seed_full_repo(tmp_path)
    rc = ccf.main(["--repo-root", str(repo), "--threshold", "5", "--warn-threshold", "7"])
    assert rc == 2


def test_negative_threshold_exits_two(tmp_path: Path):
    """Per CodeRabbit round-4 feedback on PR #152: negative thresholds rejected."""
    repo = _seed_full_repo(tmp_path)
    rc = ccf.main(["--repo-root", str(repo), "--threshold", "-1"])
    assert rc == 2


def test_negative_warn_threshold_exits_two(tmp_path: Path):
    """Per CodeRabbit round-4 feedback on PR #152: negative warn threshold rejected."""
    repo = _seed_full_repo(tmp_path)
    rc = ccf.main(["--repo-root", str(repo), "--threshold", "7", "--warn-threshold", "-1"])
    assert rc == 2


def test_resolve_repo_root_raises_when_anchors_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Per CodeRabbit round-4 feedback on PR #152: _resolve_repo_root raises RuntimeError
    when the auto-detected candidate doesn't have BOTH CLAUDE.md and AGENTS.md."""
    # Move __file__ so the auto-detect lands in tmp_path/tools/
    fake_tools = tmp_path / "tools"
    fake_tools.mkdir()
    monkeypatch.setattr(ccf, "__file__", str(fake_tools / "check_canon_freshness.py"))
    with pytest.raises(RuntimeError, match="could not auto-detect repo root"):
        ccf._resolve_repo_root()


def test_main_exits_two_when_repo_root_autodetect_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """When --repo-root is not given AND autodetect fails, main() returns 2 with clear message."""
    fake_tools = tmp_path / "tools"
    fake_tools.mkdir()
    monkeypatch.setattr(ccf, "__file__", str(fake_tools / "check_canon_freshness.py"))
    rc = ccf.main([])  # no --repo-root
    assert rc == 2


def test_no_canon_files_exits_one(tmp_path: Path):
    """Empty repo (no canon files at expected paths) → exit 1 (missing_file user error).

    The literal canon paths (CLAUDE.md, AGENTS.md, team-charter.md) are always
    included — missing files surface as `missing_file` per CodeRabbit feedback,
    so a dropped canon file fails the gate instead of silently disappearing.
    """
    repo = tmp_path / "empty_repo"
    repo.mkdir()
    rc = ccf.main(["--repo-root", str(repo)])
    assert rc == 1


def test_missing_repo_root_exits_two(tmp_path: Path):
    rc = ccf.main(["--repo-root", str(tmp_path / "does_not_exist")])
    assert rc == 2


def test_missing_canon_file_returns_missing_file_status(tmp_path: Path):
    """Per CodeRabbit feedback on PR #152: literal canon paths must always be
    included; missing ones surface as `missing_file` rather than silently skipped."""
    repo = _seed_minimal_repo(tmp_path)
    today = date(2026, 5, 9)

    # Create CLAUDE.md but NOT AGENTS.md or team-charter.md (literal canon paths)
    _make_canon_file(repo, "CLAUDE.md", date_field="Effective", date_val="2026-05-09")

    results = ccf.check_canon_freshness(repo, threshold=7, warn_threshold=5, today=today)
    statuses = {Path(r.path).name: r.status for r in results}
    # CLAUDE.md is fresh
    assert statuses.get("CLAUDE.md") == "fresh"
    # AGENTS.md and team-charter.md are missing — should surface as missing_file
    assert statuses.get("AGENTS.md") == "missing_file"
    assert statuses.get("team-charter.md") == "missing_file"


def test_missing_file_exits_one(tmp_path: Path):
    """missing_file is a user/data error → exit 1, not 2."""
    repo = _seed_minimal_repo(tmp_path)
    _make_canon_file(repo, "CLAUDE.md", date_field="Effective", date_val=_utc_today().isoformat())
    # Don't create AGENTS.md — should exit 1 (not 2, since it's user-data not script-error)
    rc = ccf.main(["--repo-root", str(repo)])
    assert rc == 1


def test_bad_env_var_int_exits_two(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Per CodeRabbit feedback on PR #152: bad env var values exit 2 cleanly,
    not crash with ValueError."""
    repo = _seed_full_repo(tmp_path)
    monkeypatch.setenv("CANON_FRESHNESS_DAYS", "not_a_number")
    rc = ccf.main(["--repo-root", str(repo)])
    assert rc == 2


def test_empty_env_var_uses_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Empty env var should fall back to default, not crash."""
    repo = _seed_full_repo(tmp_path)
    monkeypatch.setenv("CANON_FRESHNESS_DAYS", "")
    rc = ccf.main(["--repo-root", str(repo)])
    assert rc == 0  # default 7-day threshold, today's date is fresh


def test_script_error_status_exits_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """OSError during file read should produce script_error status → exit 2."""
    repo = _seed_full_repo(tmp_path)

    # Monkey-patch Path.read_text to raise OSError for AGENTS.md path
    real_read_text = Path.read_text

    def fake_read_text(self, *args, **kwargs):
        if "CLAUDE.md" in str(self):
            raise PermissionError("simulated I/O failure")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)
    rc = ccf.main(["--repo-root", str(repo)])
    assert rc == 2


# ----------------------------------------------------------------------------
# Tests — JSON output
# ----------------------------------------------------------------------------


def test_json_output_is_parseable(tmp_path: Path):
    repo = _seed_full_repo(tmp_path)
    _make_canon_file(repo, ".miru/overlays/x.md", date_field="Last reviewed", date_val="2026-04-01")

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ccf.main(["--repo-root", str(repo), "--json"])
    assert rc == 1  # one stale file (x.md)
    payload = json.loads(buf.getvalue())
    assert isinstance(payload, list)
    # 3 fresh literals (from _seed_full_repo) + 1 stale overlay = 4
    assert len(payload) == 4
    statuses = {item["status"] for item in payload}
    assert "stale" in statuses
    assert "fresh" in statuses
    # Schema check
    for item in payload:
        assert set(item.keys()) == {
            "path",
            "status",
            "field_name",
            "stamp_date",
            "days_old",
            "detail",
        }


# ----------------------------------------------------------------------------
# Tests — future-dated stamps treated as fresh
# ----------------------------------------------------------------------------


def test_future_dated_stamp_is_fresh(tmp_path: Path):
    """A future date doesn't fail — it's just suspicious. Treat as fresh."""
    repo = _seed_minimal_repo(tmp_path)
    today = date(2026, 5, 9)

    _make_canon_file(repo, "CLAUDE.md", date_field="Effective", date_val="2026-12-31")

    results = ccf.check_canon_freshness(repo, threshold=7, warn_threshold=5, today=today)
    claude = next(r for r in results if Path(r.path).name == "CLAUDE.md")
    assert claude.status == "fresh"
    assert claude.days_old < 0
    assert "future-dated" in claude.detail


# ----------------------------------------------------------------------------
# Tests — date stamp must be in front-matter zone (first 30 lines)
# ----------------------------------------------------------------------------


def test_stamp_in_body_is_not_recognized(tmp_path: Path):
    """A date deep in the body shouldn't satisfy the freshness check."""
    repo = _seed_minimal_repo(tmp_path)
    today = date(2026, 5, 9)

    p = repo / "CLAUDE.md"
    body_lines = ["# Title", ""] + ["filler line"] * 35 + ["Last reviewed: 2026-05-09"]
    p.write_text("\n".join(body_lines), encoding="utf-8")

    results = ccf.check_canon_freshness(repo, threshold=7, warn_threshold=5, today=today)
    claude = next(r for r in results if Path(r.path).name == "CLAUDE.md")
    assert claude.status == "missing_stamp"


# ----------------------------------------------------------------------------
# Tests — first matching field wins
# ----------------------------------------------------------------------------


def test_first_field_wins_when_multiple_present(tmp_path: Path):
    """If a file has both 'Last reviewed' and 'Effective', the first match wins."""
    repo = _seed_minimal_repo(tmp_path)
    today = date(2026, 5, 9)

    p = repo / "CLAUDE.md"
    p.write_text(
        "# Title\n\n```text\nLast reviewed: 2026-05-09\nEffective: 2026-04-01\n```\n",
        encoding="utf-8",
    )

    results = ccf.check_canon_freshness(repo, threshold=7, warn_threshold=5, today=today)
    claude = next(r for r in results if Path(r.path).name == "CLAUDE.md")
    # 'Last reviewed' appears first in the file → should be the matched field
    assert claude.field_name and "reviewed" in claude.field_name.lower()
    assert claude.status == "fresh"


# ----------------------------------------------------------------------------
# Tests — real-repo smoke test
# ----------------------------------------------------------------------------


def test_real_repo_runs_without_crashing():
    """Smoke test against the actual repo. Don't assert pass/fail — just no exceptions.

    Per CodeRabbit round-5 feedback on PR #152: _resolve_repo_root now raises
    RuntimeError (instead of cwd fallback) when CLAUDE.md + AGENTS.md aren't
    both present. In non-Miru checkouts or partial trees, that's an expected
    skip — not an error.
    """
    try:
        repo_root = ccf._resolve_repo_root()
    except RuntimeError as exc:
        pytest.skip(f"Not in a real Miru repo checkout: {exc}")
    if not (repo_root / "CLAUDE.md").exists():
        pytest.skip("Not in a real Miru repo checkout")
    results = ccf.check_canon_freshness(repo_root, threshold=7, warn_threshold=5)
    # At minimum, CLAUDE.md should exist
    paths = [r.path for r in results]
    assert any("CLAUDE.md" in p for p in paths)
