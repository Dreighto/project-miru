#!/usr/bin/env python
"""Inspect worktree learner/queue/review state from CLI. No server required."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    data = root / "data"
    status_db = data / "miru_learning_log.db"
    queue_db = data / "miru_learning_queue.db"
    dossier_db = data / "miru_learning_dossiers.db"

    out = {"status_db": str(status_db), "queue_db": str(queue_db), "dossier_db": str(dossier_db)}

    if status_db.is_file():
        with sqlite3.connect(status_db) as c:
            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            out["status_tables"] = tables
            try:
                out["learner_review_queue_count"] = c.execute("SELECT COUNT(*) FROM learner_review_queue").fetchone()[0]
                out["learner_review_queue_sample"] = [
                    dict(zip(("id", "card_code", "source_id", "confidence", "reason", "created_at"), row))
                    for row in c.execute(
                        "SELECT id, card_code, source_id, confidence, reason, created_at FROM learner_review_queue ORDER BY created_at DESC LIMIT 5"
                    ).fetchall()
                ]
            except sqlite3.OperationalError as e:
                out["learner_review_queue_error"] = str(e)
            try:
                row = c.execute(
                    "SELECT current_state, current_card_code, current_task_type, last_heartbeat, last_error FROM engine_status LIMIT 1"
                ).fetchone()
                if row:
                    out["engine_status"] = dict(zip(("current_state", "current_card_code", "current_task_type", "last_heartbeat", "last_error"), row))
            except sqlite3.OperationalError as e:
                out["engine_status_error"] = str(e)
    else:
        out["status_db_exists"] = False

    if queue_db.is_file():
        with sqlite3.connect(queue_db) as c:
            try:
                out["queue_queued_count"] = c.execute(
                    "SELECT COUNT(*) FROM learning_queue WHERE status IN ('queued','claimed')"
                ).fetchone()[0]
                out["queue_total"] = c.execute("SELECT COUNT(*) FROM learning_queue").fetchone()[0]
            except sqlite3.OperationalError as e:
                out["queue_error"] = str(e)
    else:
        out["queue_db_exists"] = False

    print(json.dumps(out, indent=2))
