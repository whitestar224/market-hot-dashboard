"""Bounded local event journal. It never fetches news, calls AI, or sends alerts."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def article_alias(value: str) -> str:
    """Only identifiable articles/posts, never shared dashboards or coin charts."""
    try:
        url = urlsplit(str(value or ""))
        if url.scheme not in {"http", "https"} or not url.netloc:
            return ""
        if not any(part in url.path.lower() for part in (
            "/status/", "/article/", "/articles/", "/news/", "/newsflash/", "/announcement/",
        )):
            return ""
        return "article:" + urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path, url.query, ""))
    except ValueError:
        return ""


class EventFlowStore:
    def __init__(self, path: Path, *, max_events: int = 5000, history_limit: int = 12):
        self.path = Path(path)
        self.max_events = max(1, max_events)
        self.history_limit = max(1, history_limit)
        self.lock = threading.RLock()
        self.initialized = False

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=2)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        if not self.initialized:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA journal_size_limit=2097152")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS flow_meta (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO flow_meta VALUES (1,0);
                CREATE TABLE IF NOT EXISTS flow_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL,
                    activity_at INTEGER NOT NULL, created_at INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS flow_activity ON flow_events(activity_at DESC,id DESC);
                CREATE INDEX IF NOT EXISTS flow_revision ON flow_events(revision);
                CREATE TABLE IF NOT EXISTS flow_identities (
                    identity TEXT PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES flow_events(id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS flow_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL REFERENCES flow_events(id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL, kind TEXT NOT NULL, stage TEXT NOT NULL,
                    happened_at INTEGER NOT NULL, summary TEXT NOT NULL,
                    UNIQUE(event_id,fingerprint));
            """)
            self.initialized = True
        return conn

    def record(self, identity: str, kind: str, data: dict, stage: str, happened_at: int,
               *, aliases: list[str] | None = None) -> int:
        if not identity or kind not in {"news", "popup", "signal"}:
            return 0
        keys = list(dict.fromkeys([identity, *(key for key in aliases or [] if key)]))
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(f"{kind}:{stage}:{encoded}".encode()).hexdigest()
        now_ms = int(time.time() * 1000)
        happened_at = min(int(happened_at or now_ms), now_ms + 60_000)
        with self.lock, closing(self._connect()) as conn, conn:
            matches = conn.execute(
                f"SELECT DISTINCT event_id FROM flow_identities WHERE identity IN ({','.join('?' for _ in keys)}) ORDER BY event_id",
                keys,
            ).fetchall()
            event_id = matches[0][0] if matches else None
            if event_id is None:
                event_id = conn.execute(
                    "INSERT INTO flow_events(revision,activity_at,created_at,payload) VALUES(0,?,?,?)",
                    (happened_at, now_ms, "{}"),
                ).lastrowid
            # A later authoritative topic may link earlier separate source records.
            # Keep their progress and aliases before consolidating the duplicate card.
            for duplicate in [row[0] for row in matches[1:]]:
                conn.execute("UPDATE flow_identities SET event_id=? WHERE event_id=?", (event_id, duplicate))
                conn.execute("""INSERT OR IGNORE INTO flow_steps(event_id,fingerprint,kind,stage,happened_at,summary)
                    SELECT ?,fingerprint,kind,stage,happened_at,summary FROM flow_steps WHERE event_id=?""", (event_id, duplicate))
                old_data = json.loads(conn.execute("SELECT payload FROM flow_events WHERE id=?", (duplicate,)).fetchone()[0])
                current = json.loads(conn.execute("SELECT payload FROM flow_events WHERE id=?", (event_id,)).fetchone()[0])
                conn.execute("UPDATE flow_events SET payload=? WHERE id=?", (json.dumps({**old_data, **current}, ensure_ascii=False), event_id))
                conn.execute("DELETE FROM flow_events WHERE id=?", (duplicate,))
            for key in keys:
                conn.execute("INSERT OR IGNORE INTO flow_identities VALUES(?,?)", (key, event_id))
            row = conn.execute("SELECT * FROM flow_events WHERE id=?", (event_id,)).fetchone()
            payload = json.loads(row["payload"])
            next_part = {**data, "stage": stage}
            if payload.get(kind) == next_part and len(matches) <= 1:
                return event_id
            payload[kind] = next_part
            conn.execute("UPDATE flow_meta SET revision=revision+1 WHERE id=1")
            revision = conn.execute("SELECT revision FROM flow_meta WHERE id=1").fetchone()[0]
            conn.execute("UPDATE flow_events SET revision=?,activity_at=?,payload=? WHERE id=?", (
                revision, max(row["activity_at"], happened_at),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")), event_id,
            ))
            conn.execute("INSERT OR IGNORE INTO flow_steps(event_id,fingerprint,kind,stage,happened_at,summary) VALUES(?,?,?,?,?,?)", (
                event_id, fingerprint, kind, stage, happened_at,
                str(data.get("thesis") or data.get("reason") or data.get("title") or "")[:280],
            ))
            conn.execute("""DELETE FROM flow_steps WHERE event_id=? AND id NOT IN
                (SELECT id FROM flow_steps WHERE event_id=? ORDER BY id DESC LIMIT ?)""", (event_id, event_id, self.history_limit))
            conn.execute("""DELETE FROM flow_events WHERE id IN
                (SELECT id FROM flow_events ORDER BY activity_at DESC,id DESC LIMIT -1 OFFSET ?)""", (self.max_events,))
            return event_id

    def purge_discord_monitor_history(self):
        """Remove legacy inbound-Discord event cards without touching outbound delivery."""
        with self.lock, closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DROP TABLE IF EXISTS temp._discord_monitor_events")
            conn.execute("CREATE TEMP TABLE _discord_monitor_events(id INTEGER PRIMARY KEY)")
            conn.execute("""INSERT OR IGNORE INTO _discord_monitor_events
                SELECT id FROM flow_events
                WHERE lower(payload) LIKE '%discord.com/channels/%'
                   OR lower(payload) LIKE '%chat:discord:%'
                   OR payload LIKE '%DC机会%'
                   OR payload LIKE '%华尔街聚合 /%'""")
            conn.execute("""INSERT OR IGNORE INTO _discord_monitor_events
                SELECT event_id FROM flow_identities
                WHERE lower(identity) LIKE '%discord%'
                   OR identity LIKE '%DC机会%'
                   OR identity LIKE '%华尔街聚合 /%'""")
            conn.execute("""INSERT OR IGNORE INTO _discord_monitor_events
                SELECT event_id FROM flow_steps
                WHERE lower(summary) LIKE '%discord.com/channels/%'
                   OR lower(summary) LIKE '%chat:discord:%'
                   OR summary LIKE '%DC机会%'
                   OR summary LIKE '%华尔街聚合 /%'""")
            removed = conn.execute(
                "DELETE FROM flow_events WHERE id IN (SELECT id FROM _discord_monitor_events)"
            ).rowcount
            if removed:
                conn.execute("UPDATE flow_meta SET revision=revision+1 WHERE id=1")
            conn.execute("DROP TABLE IF EXISTS temp._discord_monitor_events")
        return removed

    def latest_news(self, identity: str) -> dict:
        with self.lock, closing(self._connect()) as conn:
            row = conn.execute("""SELECT e.payload FROM flow_events e
                JOIN flow_identities i ON i.event_id=e.id WHERE i.identity=?""", (identity,)).fetchone()
        return (json.loads(row[0]).get("news") or {}) if row else {}

    def read(self, *, after: int | None = None, before: tuple[int, int] | None = None, limit: int = 30, category: str = "all", live: bool = False, now_ms: int | None = None) -> dict:
        limit = max(1, min(100, int(limit)))
        filters = {
            "all": "1=1",
            "opportunity": "json_extract(payload,'$.news.active')=1 AND (json_extract(payload,'$.news.opportunity')=1 OR json_extract(payload,'$.news.preTokenMeme.eligible')=1)",
            "news": "json_type(payload,'$.news')='object'",
            "popup": "json_type(payload,'$.popup')='object'",
            "signal": "json_type(payload,'$.signal')='object'",
            "system": "json_extract(payload,'$.popup.kind') LIKE '%系统%'",
        }
        condition = filters.get(category, filters["all"])
        current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        if live:
            # Keep all records in the journal. The live stream includes current
            # evidence-backed trade opportunities, explicit pre-token Meme
            # observations, and price signals. Pre-token observations remain
            # non-executable and use a separate expiry window.
            condition = (f"({condition}) AND ((json_extract(payload,'$.news.active')=1 "
                          "AND json_extract(payload,'$.news.opportunity')=1 "
                          "AND json_extract(payload,'$.news.window.eligible')=1 "
                          f"AND json_extract(payload,'$.news.window.expiresAt')>{current_ms}) "
                         "OR (json_extract(payload,'$.news.active')=1 "
                         "AND json_extract(payload,'$.news.preTokenMeme.eligible')=1 "
                         f"AND json_extract(payload,'$.news.preTokenMeme.expiresAt')>{current_ms}) "
                         "OR (json_extract(payload,'$.signal.active')=1 "
                         "AND json_extract(payload,'$.signal.type') IN ('breakout','near') "
                         f"AND json_extract(payload,'$.signal.expiresAt')>{current_ms}))")
        with self.lock, closing(self._connect()) as conn, conn:
            revision = conn.execute("SELECT revision FROM flow_meta WHERE id=1").fetchone()[0]
            if after is not None:
                rows = conn.execute("SELECT * FROM flow_events WHERE revision>? ORDER BY revision LIMIT ?", (max(0, after), limit + 1)).fetchall()
            elif before:
                rows = conn.execute(f"SELECT * FROM flow_events WHERE {condition} AND (activity_at,id)<(?,?) ORDER BY activity_at DESC,id DESC LIMIT ?", (*before, limit + 1)).fetchall()
            else:
                rows = conn.execute(f"SELECT * FROM flow_events WHERE {condition} ORDER BY activity_at DESC,id DESC LIMIT ?", (limit + 1,)).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            items = []
            for row in rows:
                history = conn.execute("SELECT kind,stage,happened_at AS happenedAt,summary FROM flow_steps WHERE event_id=? ORDER BY id DESC LIMIT ?", (row["id"], self.history_limit)).fetchall()
                identities = [value[0] for value in conn.execute("SELECT identity FROM flow_identities WHERE event_id=?", (row["id"],))]
                items.append({"id": row["id"], "revision": row["revision"], "activityAt": row["activity_at"],
                              "archivedAt": row["created_at"], **json.loads(row["payload"]),
                              "history": [dict(step) for step in history], "identities": identities})
            cursor = rows[-1]["revision"] if after is not None and has_more and rows else revision
            return {"ok": True, "items": items, "cursor": cursor, "hasMore": has_more,
                    "nextBefore": [rows[-1]["activity_at"], rows[-1]["id"]] if rows else None,
                    "total": conn.execute(f"SELECT COUNT(*) FROM flow_events WHERE {condition}").fetchone()[0],
                    **({"activeIds": [row[0] for row in conn.execute(f"SELECT id FROM flow_events WHERE {condition}")],
                        "serverNow": current_ms, "scope": "current"} if live else {})}
