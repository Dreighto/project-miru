"""Temporary smoke test for miru_run_sandbox_cycle — delete after run."""
import sys, json, tempfile, sqlite3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from tools.miru_run_sandbox_cycle import main as cycle_main, collect_files

# ---------------------------------------------------------------------------
# Test collect_files
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    (td / "a.csv").write_text("x")
    (td / "b.csv").write_text("x")
    (td / "c.json").write_text("{}")
    files = collect_files([], str(td), "*.csv")
    assert len(files) == 2, f"expected 2 csv, got {len(files)}"
    files2 = collect_files([str(td / "c.json")], "", "*.json")
    assert len(files2) == 1
print("collect_files: OK")

# ---------------------------------------------------------------------------
# Test: no inputs → exit 1
# ---------------------------------------------------------------------------
rc = cycle_main(["--db-path", "nonexistent.db"])
assert rc == 1, f"expected 1 for no inputs, got {rc}"
print("no-inputs guard: OK")

# ---------------------------------------------------------------------------
# End-to-end smoke test with real deck JSON files
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    staging = td / "staging"
    db = td / "deck_intel.db"
    doss = td / "dossiers.db"

    # Make a minimal dossiers DB so cost/trait stages don't warn
    conn = sqlite3.connect(str(doss))
    conn.executescript(
        "CREATE TABLE cards(id INTEGER PRIMARY KEY, canonical_code TEXT UNIQUE, card_name TEXT, card_type TEXT);"
        "CREATE TABLE card_facts(id INTEGER PRIMARY KEY, card_id INTEGER,"
        " field_name TEXT, value_text TEXT, verification_state TEXT, updated_at TEXT);"
    )
    conn.commit(); conn.close()

    # Build 2 decklist JSON files
    def make_deck_json(leader, cards_dict):
        return json.dumps({"leader": leader,
            "cards": [{"card_code": c, "quantity": q} for c, q in cards_dict.items()]})

    d1 = td / "deck1.json"
    d2 = td / "deck2.json"
    d1.write_text(make_deck_json("OP01-001", {f"OP01-C{i:02d}": 4 for i in range(10)}))
    # Deck 2: slightly different from deck1 (one card different qty) so different deck_uid
    d2.write_text(make_deck_json("OP01-001", {**{f"OP01-C{i:02d}": 4 for i in range(10)}, "OP01-C00": 3}))

    print()
    print("=== dry-run (deck-only) ===")
    rc = cycle_main([
        "--deck-files", str(d1), str(d2),
        "--db-path", str(db),
        "--dossiers-db", str(doss),
        "--staging-dir", str(staging),
        "--dry-run",
    ])
    assert rc == 0, f"dry-run exit: {rc}"
    assert not db.exists(), "DB must not be created in dry-run"
    print("  DB not created in dry-run: correct")

    print()
    print("=== live run (deck-only) ===")
    rc = cycle_main([
        "--deck-files", str(d1), str(d2),
        "--db-path", str(db),
        "--dossiers-db", str(doss),
        "--staging-dir", str(staging),
    ])
    assert rc == 0, f"live run exit: {rc}"
    assert db.exists(), "DB should be created"

    conn = sqlite3.connect(str(db))
    n_decks = conn.execute("SELECT COUNT(*) FROM decklists").fetchone()[0]
    n_signals = conn.execute("SELECT COUNT(*) FROM leader_card_signals").fetchone()[0]
    conn.close()
    assert n_decks == 2, f"expected 2 decks, got {n_decks}"
    assert n_signals > 0, "signals should be computed"
    print(f"  {n_decks} deck(s) imported, {n_signals} signal row(s): correct")

    print()
    print("=== idempotent re-run ===")
    rc = cycle_main([
        "--deck-files", str(d1), str(d2),
        "--db-path", str(db),
        "--dossiers-db", str(doss),
        "--staging-dir", str(staging),
    ])
    assert rc == 0
    conn = sqlite3.connect(str(db))
    n_decks2 = conn.execute("SELECT COUNT(*) FROM decklists").fetchone()[0]
    assert n_decks2 == n_decks, f"deck count changed: {n_decks} -> {n_decks2}"
    conn.close()
    print(f"  deck count unchanged ({n_decks2}): correct")

    print()
    print("=== bad deck file causes non-zero exit ===")
    bad = td / "bad.json"
    bad.write_text("NOT VALID JSON {{{")
    rc = cycle_main([
        "--deck-files", str(bad),
        "--db-path", str(db),
        "--dossiers-db", str(doss),
        "--staging-dir", str(staging),
    ])
    assert rc == 1, f"expected exit 1 for bad file, got {rc}"
    print("  bad file → exit 1: correct")

print()
print("All smoke tests passed.")
