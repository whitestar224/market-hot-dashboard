"""Small shared SQLite transaction for authoritative monitor exclusions."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


MONITOR_STATE_TABLES = (
    "price_watch_alert_state",
    "price_watch_oversold_alert_state",
    "price_watch_fib_alert_state",
    "price_watch_first_confirmations",
    "price_structure_observation_alert_state",
)


def persist_global_monitor_exclusion(
    db_path: str | Path,
    symbol: str,
    now_ms: int,
    retention_ms: int,
    *,
    timeout_seconds: float = 30,
) -> None:
    """Commit the durable tombstone and clear every symbol-level monitor state."""
    with closing(sqlite3.connect(Path(db_path), timeout=timeout_seconds)) as conn, conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {max(1, int(timeout_seconds * 1000))}")
        conn.execute(
            """
            INSERT INTO price_structure_exclusions (
                symbol, excluded_at, absent_at, absent_confirmations,
                last_absent_at, updated_at
            ) VALUES (?, ?, 0, 0, 0, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                excluded_at = excluded.excluded_at,
                absent_at = 0,
                absent_confirmations = 0,
                last_absent_at = 0,
                updated_at = excluded.updated_at
            """,
            (symbol, now_ms, now_ms),
        )
        conn.execute(
            """
            UPDATE price_watch_assets
            SET manual_pinned = 0,
                prior_high_excluded_at = ?,
                prior_high_absent_at = 0,
                opportunity_active = 0,
                opportunity_manual_removed_at = ?,
                dismissed_until = CASE
                    WHEN dismissed_until > ? THEN dismissed_until
                    ELSE ?
                END,
                updated_at = ?
            WHERE symbol = ?
            """,
            (
                now_ms,
                now_ms,
                now_ms + retention_ms,
                now_ms + retention_ms,
                now_ms,
                symbol,
            ),
        )
        for table in MONITOR_STATE_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE symbol = ?", (symbol,))
