"""Durable local popup outbox. Admission is not display; display is not reading."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager, closing
from pathlib import Path


class AlertDeliveryStore:
    MAX_ATTEMPTS = 3
    # Give Tk and the receipt writer enough time under heavy SQLite/UI load.
    # A mapped window is acknowledged separately and is never recycled merely
    # because hover-paused visible dwell is still below MIN_VISIBLE_MS.
    DISPLAY_LEASE_SECONDS = 30
    MIN_VISIBLE_MS = 4000

    def __init__(self, path):
        self.path = Path(path)
        self._ready = False
        self._init_lock = threading.Lock()
        self._last_prune = 0

    @contextmanager
    def db(self):
        if not self._ready:
            with self._init_lock:
                if not self._ready:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with closing(sqlite3.connect(self.path, timeout=3)) as db, db:
                        db.execute("PRAGMA journal_mode=WAL")
                        db.executescript("""
                        CREATE TABLE IF NOT EXISTS deliveries (
                            id INTEGER PRIMARY KEY, payload TEXT NOT NULL, priority INTEGER NOT NULL,
                            created REAL NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                            attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0,
                            lease_until REAL NOT NULL DEFAULT 0, token TEXT NOT NULL DEFAULT '',
                            shown REAL NOT NULL DEFAULT 0, visible_ms INTEGER NOT NULL DEFAULT 0,
                            closed INTEGER NOT NULL DEFAULT 0, read_at REAL NOT NULL DEFAULT 0,
                            reason TEXT NOT NULL DEFAULT '', updated REAL NOT NULL);
                        CREATE TABLE IF NOT EXISTS aliases (
                            key TEXT PRIMARY KEY, delivery_id INTEGER NOT NULL);
                        CREATE INDEX IF NOT EXISTS delivery_pending ON deliveries(state, priority DESC, id);
                        CREATE INDEX IF NOT EXISTS delivery_unread ON deliveries(read_at, id DESC);
                        """)
                    self._ready = True
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def admit(self, payload, keys, priority, *, now=None, ttl=600):
        now = time.time() if now is None else now
        keys = list(dict.fromkeys(str(k) for k in keys if k))
        if not keys:
            keys = [hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()]
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for key in keys:
                found = db.execute('SELECT delivery_id FROM aliases WHERE key=?', (key,)).fetchone()
                if found:
                    # A newer build may know additional stable aliases for a
                    # delivery that was first admitted by an older build.  Bind
                    # those aliases while the transaction is locked so a later
                    # title edit cannot create a second popup.
                    db.executemany(
                        'INSERT OR IGNORE INTO aliases(key,delivery_id) VALUES(?,?)',
                        [(alias, found[0]) for alias in keys],
                    )
                    return {"ok": True, "deduped": True, "deliveryId": found[0]}
            # No fixed memory-queue cap: pending messages stay on disk, not evicted.
            expires = now + ttl
            supplied = float(payload.get('expiresAt') or 0) / 1000
            if supplied > 0:
                expires = min(expires, supplied)
            cur = db.execute('INSERT INTO deliveries(payload,priority,created,expires,updated) VALUES(?,?,?,?,?)',
                             (json.dumps(payload, ensure_ascii=False), priority, now, expires, now))
            identity = cur.lastrowid
            db.executemany('INSERT INTO aliases(key,delivery_id) VALUES(?,?)', [(k, identity) for k in keys])
        return {"ok": True, "deduped": False, "queued": True, "deliveryId": identity}

    def purge_discord_monitor_history(self):
        """Delete only legacy inbound-Discord monitor deliveries; outbound pushes are unrelated."""
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DROP TABLE IF EXISTS temp._discord_monitor_deliveries")
            db.execute("CREATE TEMP TABLE _discord_monitor_deliveries(id INTEGER PRIMARY KEY)")
            db.execute("""INSERT OR IGNORE INTO _discord_monitor_deliveries
                SELECT id FROM deliveries
                WHERE lower(payload) LIKE '%discord.com/channels/%'
                   OR lower(payload) LIKE '%chat:discord:%'
                   OR payload LIKE '%DC机会%'
                   OR payload LIKE '%华尔街聚合 /%'""")
            db.execute("""INSERT OR IGNORE INTO _discord_monitor_deliveries
                SELECT delivery_id FROM aliases
                WHERE lower(key) LIKE '%discord%'
                   OR key LIKE '%DC机会%'
                   OR key LIKE '%华尔街聚合 /%'""")
            removed = db.execute(
                "DELETE FROM aliases WHERE delivery_id IN (SELECT id FROM _discord_monitor_deliveries)"
            ).rowcount
            deliveries = db.execute(
                "DELETE FROM deliveries WHERE id IN (SELECT id FROM _discord_monitor_deliveries)"
            ).rowcount
            removed += db.execute("DELETE FROM aliases WHERE delivery_id NOT IN (SELECT id FROM deliveries)").rowcount
            db.execute("DROP TABLE IF EXISTS temp._discord_monitor_deliveries")
        return {"deliveries": deliveries, "aliases": removed}

    @staticmethod
    def row(row):
        if not row:
            return None
        result = dict(row)
        result['payload'] = json.loads(result['payload'])
        return result

    def get(self, identity):
        with self.db() as db:
            return self.row(db.execute('SELECT * FROM deliveries WHERE id=?', (identity,)).fetchone())

    def explanation_context(self, key):
        """Recover a legacy reader context without replaying or marking its popup."""
        if not re.fullmatch(r"[a-f0-9]{40}", str(key)):
            return None
        with self.db() as db:
            row = db.execute("""SELECT payload FROM deliveries WHERE created>?
                AND json_extract(payload,'$.explanationKey')=? ORDER BY id DESC LIMIT 1""",
                (time.time()-3*86400, key)).fetchone()
        context = json.loads(row[0]).get('explanationContext') if row else None
        return context if isinstance(context, dict) else None

    def sweep(self, *, now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            db.execute("UPDATE deliveries SET state='expired', token='', reason='超过播报时效，保留记录，不补弹旧机会', updated=? WHERE state IN ('pending','starting') AND expires<=?", (now, now))
            db.execute("""UPDATE deliveries SET state=CASE WHEN attempts>=? THEN 'failed' ELSE 'pending' END,
                token='', next_try=?, reason='窗口未确认显示，等待重试或查看记录', updated=?
                WHERE state='starting' AND lease_until<=?""", (self.MAX_ATTEMPTS, now + 2, now, now))
            db.execute("""UPDATE deliveries SET state=CASE WHEN expires<=? THEN 'expired' WHEN attempts>=? THEN 'failed' ELSE 'pending' END,
                token='', next_try=?, reason='显示中断，未读记录已保留', updated=?
                WHERE state='displayed' AND visible_ms<? AND read_at=0 AND updated<?""",
                (now, self.MAX_ATTEMPTS, now+2, now, self.MIN_VISIBLE_MS, now-self.DISPLAY_LEASE_SECONDS))
            # Ninety-day retention, only terminal/previously displayed rows; never prune pending delivery.
            if now - self._last_prune > 3600:
                old = now - 90 * 86400
                db.execute("DELETE FROM aliases WHERE delivery_id IN (SELECT id FROM deliveries WHERE created<? AND state NOT IN ('pending','starting'))", (old,))
                db.execute("DELETE FROM deliveries WHERE created<? AND state NOT IN ('pending','starting')", (old,))
                self._last_prune = now

    def pending(self, *, now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            # When every window slot is occupied, the next free slot belongs to
            # the newest message in the highest priority tier. Old information
            # must never make a fresh event wait behind it.
            return self.row(db.execute("SELECT * FROM deliveries WHERE state='pending' AND next_try<=? AND expires>? ORDER BY priority DESC,created DESC,id DESC LIMIT 1", (now, now)).fetchone())

    def lease(self, identity, *, now=None):
        now = time.time() if now is None else now
        token = secrets.token_hex(24)
        with self.db() as db:
            cur = db.execute("""UPDATE deliveries SET state='starting', attempts=attempts+1, token=?,
                lease_until=?, updated=?, closed=0, visible_ms=0, shown=0
                WHERE id=? AND state='pending' AND expires>? AND next_try<=? AND attempts<?""",
                (token, now + self.DISPLAY_LEASE_SECONDS, now, identity, now, now, self.MAX_ATTEMPTS))
            if not cur.rowcount:
                return None
            return self.row(db.execute('SELECT * FROM deliveries WHERE id=?', (identity,)).fetchone())

    def receipt(self, identity, token, stage, visible_ms=0, *, now=None):
        if stage not in {'visible', 'covered', 'closed', 'read'}:
            return False
        now = time.time() if now is None else now
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM deliveries WHERE id=?', (identity,)).fetchone()
            if not row or not token or not secrets.compare_digest(row['token'], str(token)) or row['state'] not in {'starting', 'displayed'}:
                return False
            shown = row['shown'] or (now if stage in {'visible', 'read'} else 0)
            visible_ms = max(row['visible_ms'], min(7200000, max(0, int(visible_ms))))
            db.execute('UPDATE deliveries SET state=?,shown=?,visible_ms=?,closed=?,read_at=?,updated=? WHERE id=?',
                       ('displayed' if shown else row['state'], shown, visible_ms,
                        int(stage in {'closed', 'read'}), now if stage == 'read' else row['read_at'], now, identity))
        return True

    def fail(self, identity, token, reason, *, now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM deliveries WHERE id=? AND token=?', (identity, token)).fetchone()
            if not row or row['read_at'] or row['state'] not in {'starting', 'displayed'}:
                return
            if row['visible_ms'] >= self.MIN_VISIBLE_MS:
                state = 'displayed'
            elif now >= row['expires']:
                state = 'expired'
            else:
                state = 'failed' if row['attempts'] >= self.MAX_ATTEMPTS else 'pending'
            db.execute("UPDATE deliveries SET state=?, token='', closed=1, next_try=?,reason=?,updated=? WHERE id=?",
                       (state, now + min(8, 2 ** row['attempts']), reason[:200], now, identity))

    def archive_window(self, identity):
        with self.db() as db:
            return bool(db.execute("UPDATE deliveries SET closed=1,token='',reason='窗口收起，未读消息仍保留',updated=? WHERE id=? AND visible_ms>=?", (time.time(), identity, self.MIN_VISIBLE_MS)).rowcount)

    def suppress(self, identity, reason):
        with self.db() as db:
            db.execute("UPDATE deliveries SET state='suppressed',token='',closed=1,reason=?,updated=? WHERE id=?", (reason, time.time(), identity))

    def suppress_matching(self, predicate, reason):
        """Suppress every live outbox row whose decoded payload matches."""
        now = time.time()
        matched = []
        with self.db() as db:
            rows = db.execute(
                "SELECT id,payload FROM deliveries "
                "WHERE state IN ('pending','starting') OR (state='displayed' AND closed=0)"
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row['payload'])
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict) and predicate(payload):
                    matched.append(int(row['id']))
            if matched:
                placeholders = ','.join('?' for _ in matched)
                db.execute(
                    f"UPDATE deliveries SET state='suppressed',token='',closed=1,reason=?,updated=? "
                    f"WHERE id IN ({placeholders})",
                    (reason, now, *matched),
                )
        return matched

    def mark_read(self, identity):
        with self.db() as db:
            return bool(db.execute("UPDATE deliveries SET read_at=?,updated=?,closed=1,token='',state=CASE WHEN state IN ('pending','starting') THEN 'read' ELSE state END WHERE id=?", (time.time(), time.time(), identity)).rowcount)

    def inbox(self, *, before=0, unread=True, limit=50):
        limit = max(1, min(100, int(limit)))
        where, args = ["state!='suppressed'"], []
        if unread:
            where.append('read_at=0')
        if before:
            where.append('id<?')
            args.append(int(before))
        with self.db() as db:
            rows = db.execute('SELECT * FROM deliveries WHERE ' + ' AND '.join(where) + ' ORDER BY id DESC LIMIT ?', (*args, limit + 1)).fetchall()
            count = db.execute("SELECT count(*) FROM deliveries WHERE read_at=0 AND state!='suppressed'").fetchone()[0]
            pending = db.execute("SELECT count(*) FROM deliveries WHERE state IN ('pending','starting')").fetchone()[0]
        items = []
        for row in rows[:limit]:
            payload = json.loads(row['payload'])
            items.append({"id": row['id'], "title": payload.get('title', ''), "body": payload.get('body', ''),
                          "source": payload.get('source', ''), "kind": payload.get('kind', ''), "url": payload.get('url', ''),
                          "eventTime": payload.get('time'), "createdAt": int(row['created']*1000),
                          "state": row['state'], "attempts": row['attempts'], "reason": row['reason'],
                          "shownAt": int(row['shown']*1000), "readAt": int(row['read_at']*1000)})
        return {"ok": True, "items": items, "unread": count, "pending": pending,
                "nextBefore": items[-1]['id'] if len(rows) > limit else None}


class VisibleTimer:
    """Only count intervals visible at both ends; coverage never consumes dwell."""
    def __init__(self):
        self.last = None
        self.was_visible = False
        self.milliseconds = 0

    def tick(self, visible, now):
        if self.last is not None and self.was_visible and visible:
            self.milliseconds += max(0, min(1000, int((now-self.last)*1000)))
        self.last, self.was_visible = now, visible
        return self.milliseconds
