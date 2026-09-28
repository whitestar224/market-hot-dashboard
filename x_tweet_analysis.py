"""Durable bridge queue for important-person X post analysis.

The dashboard owns discovery and popup delivery.  A separate, already signed-in
ChatGPT bridge owns only the reasoning turn: claim one post, send the generated
prompt to the pinned ``X推文分析`` chat, then write the JSON result back.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


CHAT_TITLE = "X推文分析"
LEASE_MS = 20 * 60_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _timestamp_ms(value: Any) -> int:
    try:
        result = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    return result * 1000 if 0 < result < 10_000_000_000 else result


def normalize_analysis(value: Any, *, job_id: str = "") -> dict[str, Any] | None:
    """Accept the compact bridge schema and discard ambiguous partial replies."""
    if not isinstance(value, dict):
        return None
    returned_id = _text(value.get("jobId"), 160)
    if job_id and returned_id != job_id:
        return None
    one_line = _text(value.get("oneLine") or value.get("conclusion"), 180)
    meaning = _text(value.get("meaning") or value.get("summary"), 700)
    if not one_line or not meaning:
        return None
    potential_aliases = {
        "strong": "strong", "high": "strong", "强": "strong", "强势": "strong",
        "medium": "medium", "normal": "medium", "中": "medium", "一般": "medium",
        "weak": "weak", "low": "weak", "弱": "weak", "不足": "weak",
    }
    potential = potential_aliases.get(
        _text(value.get("memePotential"), 24).casefold(), "weak"
    )
    worth = value.get("worthWatching")
    if not isinstance(worth, bool):
        worth = potential in {"strong", "medium"}
    novelty_aliases = {
        "high": "high", "strong": "high", "高": "high",
        "medium": "medium", "normal": "medium", "中": "medium",
        "low": "low", "weak": "low", "低": "low",
    }
    novelty = novelty_aliases.get(
        _text(value.get("novelty"), 20).casefold(), "medium" if worth else "low"
    )
    generic_theme = value.get("genericTheme") is True
    if generic_theme or novelty == "low":
        worth = False
    keywords = [
        _text(item, 48) for item in (value.get("keywords") or [])
        if _text(item, 48)
    ][:3]
    tokens: list[dict[str, Any]] = []
    for raw in value.get("tokens") or []:
        if not isinstance(raw, dict):
            continue
        symbol = _text(raw.get("symbol") or raw.get("name"), 40)
        contract = _text(raw.get("ca") or raw.get("contractAddress"), 180)
        if not symbol and not contract:
            continue
        tokens.append({
            "symbol": symbol,
            "chain": _text(raw.get("chain"), 40),
            "ca": contract,
            "relationship": _text(raw.get("relationship") or raw.get("reason"), 180),
            "official": bool(raw.get("official")),
            "evidence": _text(raw.get("evidence"), 240),
        })
        if len(tokens) >= 5:
            break
    urgency = _text(value.get("urgency"), 20).casefold()
    if urgency not in {"high", "normal", "low"}:
        urgency = "high" if worth and potential == "strong" else "normal" if worth else "low"
    if not worth:
        urgency = "low"
    return {
        "jobId": returned_id or job_id,
        "worthWatching": worth,
        "urgency": urgency,
        "oneLine": one_line,
        "meaning": meaning,
        "novelty": novelty,
        "genericTheme": generic_theme,
        "noveltyReason": _text(value.get("noveltyReason"), 300),
        "keywords": keywords,
        "memePotential": potential,
        "memeReason": _text(value.get("memeReason"), 500),
        "tokens": tokens,
        "risk": _text(value.get("risk") or value.get("risks"), 500),
    }


class XTweetAnalysisQueue:
    """Small SQLite state machine shared by the dashboard and chat bridge."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._incremental_cutoff_ms = 0
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS x_tweet_analysis_jobs (
                    job_id TEXT PRIMARY KEY,
                    tweet_id TEXT NOT NULL DEFAULT '',
                    author TEXT NOT NULL DEFAULT '',
                    handle TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    post_url TEXT NOT NULL DEFAULT '',
                    published_at INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    claim_token TEXT NOT NULL DEFAULT '',
                    target_thread_id TEXT NOT NULL DEFAULT '',
                    target_thread_title TEXT NOT NULL DEFAULT '',
                    lease_until INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    retry_at INTEGER NOT NULL DEFAULT 0,
                    sent_at INTEGER NOT NULL DEFAULT 0,
                    completed_at INTEGER NOT NULL DEFAULT 0,
                    alerted_at INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT NOT NULL DEFAULT '',
                    raw_response TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS x_tweet_analysis_status
                    ON x_tweet_analysis_jobs(status, retry_at, created_at);
                CREATE INDEX IF NOT EXISTS x_tweet_analysis_thread
                    ON x_tweet_analysis_jobs(target_thread_id, status);
                CREATE TABLE IF NOT EXISTS x_tweet_analysis_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL DEFAULT 0
                );
            """)
            row = conn.execute(
                "SELECT value FROM x_tweet_analysis_meta WHERE key='incremental_cutoff_ms'"
            ).fetchone()
            self._incremental_cutoff_ms = int(row["value"] or 0) if row else 0

    def enable_incremental_only(self, *, now_ms: int | None = None) -> dict[str, Any]:
        """Persist a one-time baseline and retire every pre-baseline unfinished job."""
        now = int(now_ms or _now_ms())
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM x_tweet_analysis_meta WHERE key='incremental_cutoff_ms'"
            ).fetchone()
            initialized = row is None
            cutoff = int(row["value"] or 0) if row else now
            if initialized:
                conn.execute("""INSERT INTO x_tweet_analysis_meta(key,value,updated_at)
                    VALUES('incremental_cutoff_ms',?,?)""", (str(cutoff), now))
            suppressed = conn.execute("""UPDATE x_tweet_analysis_jobs
                SET status='suppressed',claim_token='',target_thread_id='',target_thread_title='',
                    lease_until=0,error='pre-incremental history retired',updated_at=?
                WHERE status IN ('pending','claimed','sent') AND published_at<=?""", (
                now, cutoff,
            )).rowcount
            conn.commit()
            self._incremental_cutoff_ms = cutoff
        return {
            "ok": True,
            "initialized": initialized,
            "cutoffMs": cutoff,
            "suppressed": int(suppressed or 0),
        }

    @staticmethod
    def _job_id(event: dict[str, Any]) -> str:
        identity = _text(
            event.get("tweetId") or event.get("key") or event.get("id") or event.get("url"),
            500,
        )
        if not identity:
            identity = json.dumps(event, ensure_ascii=False, sort_keys=True)[:4000]
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        return f"xpost-{digest}"

    @staticmethod
    def _prompt(job_id: str, event: dict[str, Any]) -> str:
        source = {
            "jobId": job_id,
            "author": _text(event.get("source") or event.get("sourceName") or event.get("author"), 100),
            "handle": _text(event.get("authorHandle") or event.get("handle"), 80),
            "category": _text(event.get("xCategory") or event.get("category"), 40),
            "publishedAt": _timestamp_ms(event.get("time") or event.get("publishedAt")),
            "url": _text(event.get("url"), 800),
            "originalText": _text(event.get("originalText") or event.get("text"), 3000),
            "quotedText": _text(event.get("quoteText"), 1800),
        }
        return (
            "请按本聊天既定的‘X推文分析’标准分析下面这一条重要人物新推文。"
            "先理解语境、真实含义和传播点；只有上下文确实不足时才联网核对。"
            "必须从语义上判断信息增量，不得用关键词命中代替判断。"
            "泛 AGI/AI 概念、常见年份预测、重复观点、口号或情绪表达，即使作者很重要，"
            "只要没有首次披露、可验证产品/政策落地、明确新数据、新合作，或具体加密标的映射，"
            "都属于低新颖度：genericTheme=true、novelty=low、worthWatching=false。"
            "只有具有可验证新事实、明显市场催化，或新出现且可传播的具体命名/形象时才允许提示。"
            "如发现已有对应币，核验链与 CA，并优先选择热度、成交额、流动性综合最强的代表币；"
            "非官方币必须明确不是官方发行，不得编造关系。\n\n"
            "只返回一个 JSON 对象，不要 Markdown，不要额外文字。字段必须完整：\n"
            "{\"jobId\":\"原样返回\",\"worthWatching\":true或false,"
            "\"urgency\":\"high|normal|low\",\"oneLine\":\"一句话结论\","
            "\"meaning\":\"推文真正意思及起因经过结果\","
            "\"novelty\":\"high|medium|low\",\"genericTheme\":true或false,"
            "\"noveltyReason\":\"为什么是新信息或泛化旧话题\","
            "\"keywords\":[\"最多3个潜在炒作词\"],"
            "\"memePotential\":\"strong|medium|weak\",\"memeReason\":\"判断依据\","
            "\"tokens\":[{\"symbol\":\"\",\"chain\":\"\",\"ca\":\"\","
            "\"relationship\":\"\",\"official\":false,\"evidence\":\"\"}],"
            "\"risk\":\"最大反例或风险\"}\n\n"
            f"推文数据：{json.dumps(source, ensure_ascii=False, separators=(',', ':'))}"
        )

    def refresh_pending_prompts(self, *, now_ms: int | None = None) -> int:
        """Upgrade queued jobs to the latest semantic-analysis instructions."""
        now = int(now_ms or _now_ms())
        changed = 0
        with self._lock, self._connection() as conn:
            rows = conn.execute("""SELECT job_id,payload_json,prompt
                FROM x_tweet_analysis_jobs WHERE status='pending'""").fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                prompt = self._prompt(row["job_id"], payload)
                if prompt == row["prompt"]:
                    continue
                changed += conn.execute("""UPDATE x_tweet_analysis_jobs
                    SET prompt=?,updated_at=? WHERE job_id=? AND status='pending'""", (
                    prompt, now, row["job_id"],
                )).rowcount
        return int(changed or 0)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        result = dict(row)
        for key in ("payload_json", "result_json"):
            raw = result.pop(key, "")
            try:
                result["payload" if key == "payload_json" else "analysis"] = json.loads(raw) if raw else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                result["payload" if key == "payload_json" else "analysis"] = {}
        return result

    def enqueue(self, events: list[dict[str, Any]], *, min_published_at: int = 0,
                now_ms: int | None = None) -> dict[str, Any]:
        now = int(now_ms or _now_ms())
        accepted: list[str] = []
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for event in events:
                if not isinstance(event, dict):
                    continue
                published = _timestamp_ms(event.get("time") or event.get("publishedAt"))
                cutoff = max(int(min_published_at or 0), self._incremental_cutoff_ms)
                if not published or published <= cutoff:
                    continue
                text = _text(event.get("originalText") or event.get("text"), 3000)
                if not text:
                    continue
                job_id = self._job_id(event)
                payload = {
                    "key": _text(event.get("key"), 240),
                    "tweetId": _text(event.get("tweetId") or event.get("id"), 100),
                    "author": _text(event.get("source") or event.get("sourceName"), 100),
                    "handle": _text(event.get("authorHandle") or event.get("handle"), 80),
                    "category": _text(event.get("xCategory"), 40),
                    "url": _text(event.get("url"), 800),
                    "publishedAt": published,
                    "originalText": text,
                    "quoteText": _text(event.get("quoteText"), 1800),
                }
                changed = conn.execute("""INSERT OR IGNORE INTO x_tweet_analysis_jobs
                    (job_id,tweet_id,author,handle,category,post_url,published_at,payload_json,prompt,
                     status,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?)""", (
                    job_id, payload["tweetId"], payload["author"], payload["handle"],
                    payload["category"], payload["url"], published,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    self._prompt(job_id, event), now, now,
                )).rowcount
                if changed:
                    accepted.append(job_id)
            conn.commit()
        return {"ok": True, "accepted": len(accepted), "jobIds": accepted}

    def claim(self, *, target_thread_id: str, target_thread_title: str = CHAT_TITLE,
              now_ms: int | None = None) -> dict[str, Any]:
        now = int(now_ms or _now_ms())
        thread_id = _text(target_thread_id, 200)
        thread_title = _text(target_thread_title, 200)
        if not thread_id or thread_title != CHAT_TITLE:
            raise ValueError(f"只允许绑定标题精确为‘{CHAT_TITLE}’的聊天")
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""UPDATE x_tweet_analysis_jobs
                SET status='pending',claim_token='',target_thread_id='',target_thread_title='',
                    lease_until=0,retry_at=?,error='领取超时，已重新排队',updated_at=?
                WHERE status='claimed' AND lease_until>0 AND lease_until<?""", (now, now, now))
            active = conn.execute("""SELECT * FROM x_tweet_analysis_jobs
                WHERE target_thread_id=? AND status IN ('claimed','sent')
                ORDER BY created_at LIMIT 1""", (thread_id,)).fetchone()
            if active:
                conn.commit()
                row = self._row(active)
                return {
                    "ok": True, "status": row["status"], "jobId": row["job_id"],
                    "claimToken": row["claim_token"],
                    "prompt": row["prompt"] if row["status"] == "claimed" else "",
                    "targetThreadId": thread_id, "targetThreadTitle": CHAT_TITLE,
                    "sentAt": row["sent_at"], "recover": True,
                }
            pending = conn.execute("""SELECT * FROM x_tweet_analysis_jobs
                WHERE status='pending' AND retry_at<=?
                ORDER BY published_at,created_at LIMIT 1""", (now,)).fetchone()
            if not pending:
                conn.commit()
                return {"ok": True, "status": "empty"}
            token = secrets.token_urlsafe(24)
            changed = conn.execute("""UPDATE x_tweet_analysis_jobs
                SET status='claimed',claim_token=?,target_thread_id=?,target_thread_title=?,
                    lease_until=?,attempts=attempts+1,error='',updated_at=?
                WHERE job_id=? AND status='pending'""", (
                token, thread_id, CHAT_TITLE, now + LEASE_MS, now, pending["job_id"],
            )).rowcount
            conn.commit()
            if not changed:
                return {"ok": True, "status": "busy"}
            return {
                "ok": True, "status": "claimed", "jobId": pending["job_id"],
                "claimToken": token, "prompt": pending["prompt"],
                "targetThreadId": thread_id, "targetThreadTitle": CHAT_TITLE,
                "leaseUntil": now + LEASE_MS,
            }

    def mark_sent(self, job_id: str, claim_token: str, *, now_ms: int | None = None) -> dict[str, Any]:
        now = int(now_ms or _now_ms())
        with self._lock, self._connection() as conn:
            changed = conn.execute("""UPDATE x_tweet_analysis_jobs
                SET status='sent',sent_at=CASE WHEN sent_at=0 THEN ? ELSE sent_at END,
                    lease_until=0,error='',updated_at=?
                WHERE job_id=? AND claim_token=? AND status IN ('claimed','sent')""", (
                now, now, _text(job_id, 160), _text(claim_token, 200),
            )).rowcount
        return {"ok": bool(changed), "status": "sent" if changed else "stale"}

    def complete(self, job_id: str, claim_token: str, analysis: Any, *, raw_response: str = "",
                 now_ms: int | None = None) -> dict[str, Any]:
        now = int(now_ms or _now_ms())
        job_id = _text(job_id, 160)
        normalized = normalize_analysis(analysis, job_id=job_id)
        if normalized is None:
            raise ValueError("聊天分析结果缺少字段、任务 ID 不一致或格式无效")
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM x_tweet_analysis_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if not existing:
                conn.commit()
                return {"ok": False, "status": "missing"}
            if existing["status"] == "completed":
                if existing["claim_token"] != _text(claim_token, 200):
                    conn.commit()
                    return {"ok": False, "status": "stale"}
                conn.commit()
                row = self._row(existing)
                return {"ok": True, "status": "completed", "duplicate": True, "job": row}
            changed = conn.execute("""UPDATE x_tweet_analysis_jobs
                SET status='completed',completed_at=?,result_json=?,raw_response=?,
                    lease_until=0,error='',updated_at=?
                WHERE job_id=? AND claim_token=? AND status IN ('claimed','sent')""", (
                now, json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
                _text(raw_response, 12000), now, job_id, _text(claim_token, 200),
            )).rowcount
            row = conn.execute(
                "SELECT * FROM x_tweet_analysis_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            conn.commit()
        return {"ok": bool(changed), "status": "completed" if changed else "stale", "job": self._row(row)}

    def mark_alerted(self, job_id: str, *, now_ms: int | None = None) -> bool:
        now = int(now_ms or _now_ms())
        with self._lock, self._connection() as conn:
            return bool(conn.execute("""UPDATE x_tweet_analysis_jobs
                SET alerted_at=CASE WHEN alerted_at=0 THEN ? ELSE alerted_at END,updated_at=?
                WHERE job_id=? AND status='completed'""", (now, now, _text(job_id, 160))).rowcount)

    def suppress_disallowed_handles(
        self,
        allowed_handles: set[str] | frozenset[str],
        *,
        now_ms: int | None = None,
    ) -> int:
        """Stop queued organization/minor-account jobs before they consume AI work."""
        now = int(now_ms or _now_ms())
        allowed = sorted({_text(value, 80).casefold().lstrip("@") for value in allowed_handles if _text(value, 80)})
        with self._lock, self._connection() as conn:
            if allowed:
                placeholders = ",".join("?" for _ in allowed)
                query = f"""UPDATE x_tweet_analysis_jobs
                    SET status='suppressed',claim_token='',target_thread_id='',target_thread_title='',
                        lease_until=0,error='source removed from leader-person roster',updated_at=?
                    WHERE status IN ('pending','claimed','sent')
                      AND (category='project_official' OR lower(handle) NOT IN ({placeholders}))"""
                changed = conn.execute(query, (now, *allowed)).rowcount
            else:
                changed = conn.execute("""UPDATE x_tweet_analysis_jobs
                    SET status='suppressed',claim_token='',target_thread_id='',target_thread_title='',
                        lease_until=0,error='leader-person roster empty',updated_at=?
                    WHERE status IN ('pending','claimed','sent')""", (now,)).rowcount
        return int(changed or 0)

    def fail(self, job_id: str, claim_token: str, error: Any, *, now_ms: int | None = None) -> dict[str, Any]:
        now = int(now_ms or _now_ms())
        with self._lock, self._connection() as conn:
            changed = conn.execute("""UPDATE x_tweet_analysis_jobs
                SET status='pending',claim_token='',target_thread_id='',target_thread_title='',
                    lease_until=0,retry_at=?,error=?,updated_at=?
                WHERE job_id=? AND claim_token=? AND status IN ('claimed','sent')""", (
                now + 60_000, _text(error, 500), now,
                _text(job_id, 160), _text(claim_token, 200),
            )).rowcount
        return {"ok": bool(changed), "status": "pending" if changed else "stale"}

    def status(self) -> dict[str, Any]:
        with self._connection() as conn:
            counts = {
                row["status"]: int(row["total"])
                for row in conn.execute("""SELECT status,COUNT(*) total
                    FROM x_tweet_analysis_jobs GROUP BY status""")
            }
            active = [self._row(row) for row in conn.execute("""SELECT * FROM x_tweet_analysis_jobs
                WHERE status IN ('claimed','sent') ORDER BY created_at""")]
            latest = self._row(conn.execute("""SELECT * FROM x_tweet_analysis_jobs
                ORDER BY created_at DESC LIMIT 1""").fetchone())
        for row in active:
            row.pop("prompt", None)
            row.pop("raw_response", None)
        if latest:
            latest.pop("prompt", None)
            latest.pop("raw_response", None)
        return {
            "ok": True, "chatTitle": CHAT_TITLE, "counts": counts,
            "active": active, "latest": latest,
        }
