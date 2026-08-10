# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Shared SQLite claim queue for dynamically load-balancing TB2 rollout
across multiple model-serving endpoints (one vLLM replica per GPU).

Mirrors the claims-table pattern used for concurrent workers pulling from a
shared unsolved-task pool: several worker processes claim one task at a time
from this table instead of a static even split, so a GPU that happens to
draw a run of slow tasks does not stall the whole round while faster GPUs
sit idle. TB2 per-task duration varies a lot (15-30 min typical, up to 10x
slower under some harness configs), which makes static splitting unreliable.

Workers are separate OS processes (each wraps a `harbor run` subprocess), so
the queue has to be cross-process — SQLite with `BEGIN IMMEDIATE` gives us
that without adding an external dependency.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | claimed | done
    worker TEXT,
    passed INTEGER,
    claimed_at REAL,
    done_at REAL
);
"""


def init_pool(db_path: Path, task_names: list[str]) -> None:
    """Create a fresh pool db with *task_names* all in 'pending' state."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute(_SCHEMA)
        conn.executemany(
            "INSERT OR IGNORE INTO tasks(name, status) VALUES (?, 'pending')",
            [(t,) for t in task_names],
        )
        conn.commit()
    finally:
        conn.close()


def claim_next(db_path: Path, worker_id: str) -> str | None:
    """Atomically claim and return one pending task name, or None if empty."""
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT name FROM tasks WHERE status = 'pending' LIMIT 1").fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        name = row[0]
        conn.execute(
            "UPDATE tasks SET status='claimed', worker=?, claimed_at=? WHERE name=?",
            (worker_id, time.time(), name),
        )
        conn.commit()
        return name
    finally:
        conn.close()


def mark_done(db_path: Path, task_name: str, passed: bool) -> None:
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute(
            "UPDATE tasks SET status='done', passed=?, done_at=? WHERE name=?",
            (1 if passed else 0, time.time(), task_name),
        )
        conn.commit()
    finally:
        conn.close()


def results(db_path: Path) -> dict[str, bool]:
    """Return {task_name: passed} for every task marked done so far."""
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        rows = conn.execute("SELECT name, passed FROM tasks WHERE status='done'").fetchall()
        return {name: bool(passed) for name, passed in rows}
    finally:
        conn.close()


__all__ = ["init_pool", "claim_next", "mark_done", "results"]
