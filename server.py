from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import hmac
import html
import json
import math
import os
import re
import secrets
import smtplib
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.request
import urllib3
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ROOT = Path(os.getenv("XINGYUN_APP_ROOT") or Path(__file__).resolve().parent).resolve()
CODEX_HOME = Path(os.getenv("CODEX_HOME") or (Path.home() / ".codex"))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    )
}
THS_HEADERS = {
    **HEADERS,
    "Referer": "https://eq.10jqka.com.cn/",
    "Accept": "application/json,text/plain,*/*",
    "Connection": "close",
}
CACHE: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 15
API_REFRESH_LOCK = threading.Lock()
API_REFRESHING: set[str] = set()
DESKTOP_ALERT_LOCK = threading.Lock()
DESKTOP_ALERT_SEEN: dict[str, float] = {}
DESKTOP_ALERT_SLOT = 0
DESKTOP_ALERT_TTL = 90 * 24 * 60 * 60
DESKTOP_ALERT_SEEN_LOADED = False
DESKTOP_ALERT_SEEN_LIMIT = 20000
DESKTOP_ALERT_QUEUE = deque()
DESKTOP_ALERT_QUEUE_ACTIVE = False
DESKTOP_ALERT_MIN_INTERVAL_SECONDS = 75
DESKTOP_ALERT_QUEUE_LIMIT = 80
DESKTOP_ALERT_LAST_LAUNCHED_AT = 0.0
SITE_ALERT_LOCK = threading.Lock()
SITE_ALERT_STATE: dict[str, Any] = {"seen": {}, "ready": []}
SITE_ALERT_STATE_LOADED = False
SITE_ALERT_SEEN_LIMIT = 10000
DEEPSEEK_INSIGHTS_LOCK = threading.Lock()
SITE_ALERT_FUTURE_TOLERANCE_MS = 10 * 60 * 1000
SITE_ALERT_MONITOR_ACTIVE = False
SITE_ALERT_MONITOR_STARTED_AT = 0
RANK_MONITOR_LOCK = threading.Lock()
RANK_MONITOR_INTERVAL = 30
RANK_MONITOR_COOLDOWN_SECONDS = 20 * 60
RANK_MONITOR_MAX_EVENTS_PER_RUN = 1
RANK_MONITOR_HOT_WATCH_RANK = 10
RANK_MONITOR_STOCK_HOT_WATCH_RANK = 10
RANK_MONITOR_TURNOVER_WATCH_LIMIT = 20
RANK_MONITOR_SKIP_UPDATE_SOURCES = {"okx-dex", "okx-dex-gainers"}
RANK_MONITOR_STATE_VERSION = 3
LOGO_CACHE: dict[str, tuple[float, list[str]]] = {}
LOGO_CACHE_TTL = 24 * 60 * 60
configured_runtime_dir = os.getenv("XINGYUN_RUNTIME_DIR", "").strip()
PERSIST_CACHE_DIR = Path(configured_runtime_dir).expanduser().resolve() if configured_runtime_dir else ROOT / ".runtime-cache"
PERSIST_CACHE_DIR.mkdir(exist_ok=True)
DESKTOP_ALERT_LOG_PATH = PERSIST_CACHE_DIR / "desktop_alert.log"
DESKTOP_ALERT_STATE_PATH = PERSIST_CACHE_DIR / "desktop_alert_seen.json"
DESKTOP_ALERT_MARKER_DIR = PERSIST_CACHE_DIR / "desktop_alert_markers"
DESKTOP_ALERT_MARKER_DIR.mkdir(exist_ok=True)
DESKTOP_ALERT_MARKER_TTL_SECONDS = 10 * 60
SITE_ALERT_STATE_PATH = PERSIST_CACHE_DIR / "site_alert_seen.json"
RANK_MONITOR_STATE_PATH = PERSIST_CACHE_DIR / "rank_monitor_state.json"
OKX_FUTURES_CACHE_PATH = PERSIST_CACHE_DIR / "okx_futures_hot.json"
OKX_DEX_SOURCE_CACHE_PATH = PERSIST_CACHE_DIR / "okx_dex_source.json"
THS_SOURCE_CACHE_PATH = PERSIST_CACHE_DIR / "ths_hot_source.json"
WECHAT_ACCOUNT_CACHE_PATH = PERSIST_CACHE_DIR / "wechat_accounts.json"
WECHAT_SOURCE_ALIAS_CACHE_PATH = PERSIST_CACHE_DIR / "wechat_source_aliases.json"
X_KOL_SOURCES_PATH = PERSIST_CACHE_DIR / "x_kol_sources.json"
CN_STOCK_GAINERS_CACHE_PATH = PERSIST_CACHE_DIR / "cn_stock_gainers_source.json"
DEEPSEEK_INSIGHTS_CACHE_PATH = PERSIST_CACHE_DIR / "deepseek_rank_insights.json"
AUTOMATION_BRIEF_IDS = ("automation", "automation-2")
AUTOMATION_BRIEF_PLACEHOLDER = "暂时没有找到这条自动化任务最近生成的简报正文。"
AUTOMATION_BRIEFS_REMOTE_CACHE_PATH = PERSIST_CACHE_DIR / "automation_briefs_remote.json"
AUTOMATION_BRIEFS_BUNDLED_PATH = ROOT / "docs" / "automation-briefs.json"
AUTOMATION_BRIEFS_DEFAULT_REMOTE_URL = (
    "https://raw.githubusercontent.com/whitestar224/market-hot-dashboard/main/docs/automation-briefs.json"
)
RSS_FETCH_MAX_BYTES = 2_500_000
RSS_MAX_STORED_ITEMS = 1800
RSS_RETENTION_MS = 14 * 24 * 60 * 60 * 1000
MAX_JSON_BODY_BYTES = int(os.getenv("XINGYUN_MAX_JSON_BODY_BYTES", "8000000") or "8000000")
WECHAT_ACCOUNT_COOLDOWN_SECONDS = 30 * 60
WECHAT_AUTH_ALERT_COOLDOWN_SECONDS = 15 * 60
WECHAT_AUTH_ALERT_LOCK = threading.Lock()
WECHAT_AUTH_ALERT_LAST_AT = 0.0
WECHAT_AUTH_POLLING_UUIDS: set[str] = set()
WECHAT_AUTH_VALIDATE_INTERVAL_SECONDS = 5 * 60
WECHAT_AUTH_MONITOR_INTERVAL_SECONDS = 3 * 60
WECHAT_AUTH_SCHEDULE_HOURS = (9, 12, 18, 22)
WECHAT_AUTH_MONITOR_ACTIVE = False
WECHAT_PLATFORM_AUTO_PAGE_LIMIT = 1
WECHAT_PLATFORM_BACKFILL_PAGE_LIMIT = 24
X_KOL_RETENTION_MS = 14 * 24 * 60 * 60 * 1000
X_KOL_FETCH_LIMIT = 80
AUTH_DB_PATH = PERSIST_CACHE_DIR / "xingyunshe_auth.db"
AUTH_SESSION_COOKIE = "xys_session"
AUTH_SESSION_DAYS = 7
AUTH_PASSWORD_ITERATIONS = 240_000
AUTH_DB_LOCK = threading.Lock()
AUTH_LOGIN_LOCK = threading.Lock()
AUTH_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
AUTH_PHONE_LOCK = threading.Lock()
AUTH_PHONE_CODES: dict[str, dict[str, Any]] = {}
AUTH_EMAIL_LOCK = threading.Lock()
AUTH_EMAIL_CODES: dict[str, dict[str, Any]] = {}
AUTH_OAUTH_LOCK = threading.Lock()
AUTH_OAUTH_STATES: dict[str, dict[str, Any]] = {}
AUTH_PHONE_CODE_TTL_SECONDS = 5 * 60
AUTH_EMAIL_CODE_TTL_SECONDS = 5 * 60
FIELD_ENCRYPTION_PREFIX = "enc:v1:"
FIELD_KEY_LOCK = threading.Lock()
FIELD_KEY_CACHE: bytes | None = None
FIELD_KEY_PATH = PERSIST_CACHE_DIR / "field_encryption.key"
SECURITY_AUDIT_RETENTION_DAYS = 180
SECURITY_RATE_LOCK = threading.Lock()
SECURITY_RATE_BUCKETS: dict[str, list[float]] = {}
USER_SCOPE_TODO = "todo"
USER_SCOPE_X_KOL_SOURCES = "x_kol_sources"
USER_SCOPE_RSS_SOURCES = "rss_sources"
USER_SCOPE_RSS_ITEMS = "rss_items"
STOCK_LOGO_OVERRIDES = {
    "hk:00100": ["minimax.io"],
    "hk:01236": ["ldrobot.com"],
}
EXCLUDED_FUTU_HK_HOT_CODES = {"00700", "09988", "01810", "03690"}
EXCLUDED_FUTU_HK_HOT_NAMES = ("腾讯", "阿里", "小米", "美团", "tencent", "alibaba", "xiaomi", "meituan")


TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
PRODUCTION_ENV_VALUES = {"prod", "production", "online"}


def raw_env_value(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def raw_env_flag(name: str, default: bool = False) -> bool:
    value = raw_env_value(name)
    if value == "":
        return default
    return value.lower() in TRUTHY_ENV_VALUES


def is_raw_production_mode() -> bool:
    return raw_env_value("XINGYUN_ENV").lower() in PRODUCTION_ENV_VALUES


def load_local_env() -> None:
    initial_env = set(os.environ)

    def load_file(env_path: Path) -> None:
        if not env_path.exists():
            return
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key or key in initial_env:
                continue
            if env_path.name == ".env" or not os.getenv(key):
                os.environ[key] = value

    explicit_env_file = raw_env_value("XINGYUN_ENV_FILE")
    if explicit_env_file:
        load_file(Path(explicit_env_file))

    load_example = raw_env_flag("XINGYUN_LOAD_ENV_EXAMPLE", default=not is_raw_production_mode())
    load_dotenv = raw_env_flag("XINGYUN_LOAD_DOTENV", default=True)
    if load_example:
        load_file(ROOT / ".env.example")
    if load_dotenv:
        load_file(ROOT / ".env")


load_local_env()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def is_production_mode() -> bool:
    return raw_env_value("XINGYUN_ENV").lower() in PRODUCTION_ENV_VALUES


def env_flag(name: str, default: bool = False) -> bool:
    return raw_env_flag(name, default)


def expose_dev_code(name: str) -> bool:
    return env_flag(name, default=not is_production_mode())


def cookie_secure_enabled() -> bool:
    public_base = raw_env_value("XINGYUN_PUBLIC_BASE_URL").lower()
    return env_flag("XINGYUN_COOKIE_SECURE", default=public_base.startswith("https://"))


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def decode_field_key(value: str) -> bytes:
    raw = str(value or "").strip()
    if not raw:
        return b""
    for decoder in (b64url_decode, lambda item: base64.b64decode(item.encode("ascii")), lambda item: bytes.fromhex(item)):
        try:
            decoded = decoder(raw)
            if len(decoded) == 32:
                return decoded
        except Exception:
            continue
    return hashlib.sha256(raw.encode("utf-8")).digest()


def field_encryption_key() -> bytes:
    global FIELD_KEY_CACHE
    with FIELD_KEY_LOCK:
        if FIELD_KEY_CACHE:
            return FIELD_KEY_CACHE
        configured = raw_env_value("XINGYUN_FIELD_ENCRYPTION_KEY")
        if configured:
            FIELD_KEY_CACHE = decode_field_key(configured)
            return FIELD_KEY_CACHE
        FIELD_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        if FIELD_KEY_PATH.exists():
            FIELD_KEY_CACHE = decode_field_key(FIELD_KEY_PATH.read_text(encoding="utf-8").strip())
            return FIELD_KEY_CACHE
        key = secrets.token_bytes(32)
        FIELD_KEY_PATH.write_text(b64url_encode(key), encoding="utf-8")
        try:
            os.chmod(FIELD_KEY_PATH, 0o600)
        except Exception:
            pass
        if is_production_mode():
            print(
                "WARNING: XINGYUN_FIELD_ENCRYPTION_KEY is not configured; generated a local runtime key.",
                file=sys.stderr,
            )
        FIELD_KEY_CACHE = key
        return FIELD_KEY_CACHE


def encrypt_field(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.startswith(FIELD_ENCRYPTION_PREFIX):
        return text
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(field_encryption_key()).encrypt(nonce, text.encode("utf-8"), b"xingyunshe-field-v1")
    return f"{FIELD_ENCRYPTION_PREFIX}{b64url_encode(nonce)}:{b64url_encode(ciphertext)}"


def decrypt_field(value: Any) -> str:
    text = str(value or "").strip()
    if not text or not text.startswith(FIELD_ENCRYPTION_PREFIX):
        return text
    body = text.removeprefix(FIELD_ENCRYPTION_PREFIX)
    try:
        nonce_b64, cipher_b64 = body.split(":", 1)
        plaintext = AESGCM(field_encryption_key()).decrypt(
            b64url_decode(nonce_b64),
            b64url_decode(cipher_b64),
            b"xingyunshe-field-v1",
        )
        return plaintext.decode("utf-8")
    except Exception:
        return ""


def field_blind_index(kind: str, value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    material = f"{kind}:{text}".encode("utf-8")
    return hmac.new(field_encryption_key(), material, hashlib.sha256).hexdigest()


def encrypted_email_value(email: str) -> tuple[str, str]:
    value = clean_email(email)
    return (encrypt_field(value), field_blind_index("email", value)) if value else ("", "")


def encrypted_phone_value(phone: str) -> tuple[str, str]:
    value = clean_phone(phone)
    return (encrypt_field(value), field_blind_index("phone", value)) if value else ("", "")


def encrypted_google_sub_value(sub: str) -> tuple[str, str]:
    value = str(sub or "").strip()[:160]
    return (encrypt_field(value), field_blind_index("google_sub", value)) if value else ("", "")


def encrypted_exchange_uid(value: str) -> str:
    uid = clean_exchange_uid(value)
    return encrypt_field(uid) if uid else ""


def safe_error_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = re.sub(r"(?i)(authorization|cookie|token|secret|password|api[_-]?key)\s*[:=]\s*[^,\s;]+", r"\1=***", text)
    text = re.sub(r"([A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]{16,}([A-Za-z0-9_\-]{4})", r"\1...\2", text)
    text = re.sub(r"([A-Za-z0-9._%+-]{2})[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", r"\1***\2", text)
    text = re.sub(r"(?<!\d)(\+?\d{3,4})\d{4,9}(\d{2})(?!\d)", r"\1****\2", text)
    return text[:500]


def sanitize_audit_meta(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k)[:80]: sanitize_audit_meta(v) for k, v in value.items() if str(k).lower() not in {"password", "code", "token", "cookie", "api_key", "apikey", "secret"}}
    if isinstance(value, list):
        return [sanitize_audit_meta(item) for item in value[:20]]
    if isinstance(value, str):
        return safe_error_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return safe_error_text(value)


def warm_runtime_cache() -> None:
    for key, fetcher in (
        ("binance-new", fetch_binance_new_coins),
        ("okx-new", fetch_okx_new_coins),
        ("bitget-new", fetch_bitget_new_coins),
    ):
        try:
            cached(key, fetcher)
        except Exception:
            pass


def cached(key: str, fn):
    now = time.time()
    if key in CACHE and now - CACHE[key][0] < CACHE_TTL:
        return CACHE[key][1]
    value = fn()
    CACHE[key] = (now, value)
    return value


def write_json_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def read_json_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def auth_db() -> sqlite3.Connection:
    conn = sqlite3.connect(AUTH_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def secure_field_for_migration(value: Any, normalizer) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        return "", ""
    if raw.startswith(FIELD_ENCRYPTION_PREFIX):
        plain = normalizer(decrypt_field(raw))
        return raw, plain
    plain = normalizer(raw)
    return (encrypt_field(plain), plain) if plain else ("", "")


def secure_uid_for_migration(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith(FIELD_ENCRYPTION_PREFIX):
        return raw
    try:
        return encrypted_exchange_uid(raw)
    except ValueError:
        return ""


def migrate_sensitive_user_fields(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT id, email, phone, google_sub, email_hash, phone_hash, google_sub_hash,
               binance_uid, okx_uid, bitget_uid
        FROM users
        """
    ).fetchall()
    for row in rows:
        email_value, email_plain = secure_field_for_migration(row["email"], clean_email)
        phone_value, phone_plain = secure_field_for_migration(row["phone"], clean_phone)
        google_value, google_plain = secure_field_for_migration(
            row["google_sub"],
            lambda item: str(item or "").strip()[:160],
        )
        email_hash = field_blind_index("email", email_plain)
        phone_hash = field_blind_index("phone", phone_plain)
        google_hash = field_blind_index("google_sub", google_plain)
        binance_uid = secure_uid_for_migration(row["binance_uid"])
        okx_uid = secure_uid_for_migration(row["okx_uid"])
        bitget_uid = secure_uid_for_migration(row["bitget_uid"])
        if (
            email_value != (row["email"] or "")
            or phone_value != (row["phone"] or "")
            or google_value != (row["google_sub"] or "")
            or email_hash != (row["email_hash"] or "")
            or phone_hash != (row["phone_hash"] or "")
            or google_hash != (row["google_sub_hash"] or "")
            or binance_uid != (row["binance_uid"] or "")
            or okx_uid != (row["okx_uid"] or "")
            or bitget_uid != (row["bitget_uid"] or "")
        ):
            conn.execute(
                """
                UPDATE users
                SET email = ?, phone = ?, google_sub = ?,
                    email_hash = ?, phone_hash = ?, google_sub_hash = ?,
                    binance_uid = ?, okx_uid = ?, bitget_uid = ?
                WHERE id = ?
                """,
                (
                    email_value,
                    phone_value,
                    google_value,
                    email_hash,
                    phone_hash,
                    google_hash,
                    binance_uid,
                    okx_uid,
                    bitget_uid,
                    int(row["id"]),
                ),
            )


def migrate_model_api_keys(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT user_id, api_key FROM user_model_settings").fetchall()
    for row in rows:
        raw_key = str(row["api_key"] or "").strip()
        if not raw_key or raw_key.startswith(FIELD_ENCRYPTION_PREFIX):
            continue
        clean_key = clean_api_key(raw_key)
        conn.execute(
            "UPDATE user_model_settings SET api_key = ? WHERE user_id = ?",
            (encrypt_field(clean_key) if clean_key else "", int(row["user_id"])),
        )


def write_audit_log(
    action: str,
    *,
    actor: dict[str, Any] | None = None,
    object_type: str = "",
    object_id: str | int = "",
    ip: str = "",
    user_agent: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        now = int(time.time())
        actor_id = int(actor.get("id") or 0) if actor else None
        actor_name = str(actor.get("username") or "")[:80] if actor else ""
        meta_text = json.dumps(sanitize_audit_meta(metadata or {}), ensure_ascii=False, separators=(",", ":"))[:4000]
        with AUTH_DB_LOCK, auth_db() as conn:
            conn.execute(
                """
                INSERT INTO security_audit_logs (
                    actor_user_id, actor_username, action, object_type, object_id,
                    ip_hash, user_agent, metadata, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    actor_id,
                    actor_name,
                    str(action or "")[:80],
                    str(object_type or "")[:80],
                    str(object_id or "")[:120],
                    field_blind_index("ip", ip) if ip else "",
                    str(user_agent or "")[:240],
                    meta_text,
                    now,
                ),
            )
            cutoff = now - SECURITY_AUDIT_RETENTION_DAYS * 24 * 60 * 60
            conn.execute("DELETE FROM security_audit_logs WHERE created_at < ?", (cutoff,))
    except Exception:
        pass


def security_rate_limited(key: str, limit: int, window_seconds: int) -> bool:
    now = time.time()
    with SECURITY_RATE_LOCK:
        bucket = [item for item in SECURITY_RATE_BUCKETS.get(key, []) if now - item < window_seconds]
        if len(bucket) >= limit:
            SECURITY_RATE_BUCKETS[key] = bucket
            return True
        bucket.append(now)
        SECURITY_RATE_BUCKETS[key] = bucket
        return False


def init_auth_db() -> None:
    AUTH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUTH_DB_LOCK, auth_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        migrations = {
            "display_name": "display_name TEXT",
            "avatar_url": "avatar_url TEXT",
            "bio": "bio TEXT",
            "email": "email TEXT",
            "phone": "phone TEXT",
            "google_sub": "google_sub TEXT",
            "email_hash": "email_hash TEXT",
            "phone_hash": "phone_hash TEXT",
            "google_sub_hash": "google_sub_hash TEXT",
            "auth_provider": "auth_provider TEXT NOT NULL DEFAULT 'password'",
            "binance_uid": "binance_uid TEXT",
            "okx_uid": "okx_uid TEXT",
            "bitget_uid": "bitget_uid TEXT",
            "security_version": "security_version INTEGER NOT NULL DEFAULT 1",
        }
        for column, ddl in migrations.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE users ADD COLUMN {ddl}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                user_agent TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_payloads (
                user_id INTEGER NOT NULL,
                scope TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(user_id, scope),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_model_settings (
                user_id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL DEFAULT 'deepseek',
                base_url TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                api_key TEXT NOT NULL DEFAULT '',
                temperature REAL NOT NULL DEFAULT 0.2,
                max_tokens INTEGER NOT NULL DEFAULT 1800,
                max_rows INTEGER NOT NULL DEFAULT 36,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS security_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_user_id INTEGER,
                actor_username TEXT,
                action TEXT NOT NULL,
                object_type TEXT,
                object_id TEXT,
                ip_hash TEXT,
                user_agent TEXT,
                metadata TEXT,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute("DROP INDEX IF EXISTS idx_users_email")
        conn.execute("DROP INDEX IF EXISTS idx_users_phone")
        conn.execute("DROP INDEX IF EXISTS idx_users_google_sub")
        migrate_sensitive_user_fields(conn)
        migrate_model_api_keys(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_payloads_scope ON user_payloads(scope)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_hash ON users(email_hash) WHERE email_hash IS NOT NULL AND email_hash != ''")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_hash ON users(phone_hash) WHERE phone_hash IS NOT NULL AND phone_hash != ''")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub_hash ON users(google_sub_hash) WHERE google_sub_hash IS NOT NULL AND google_sub_hash != ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_security_audit_created_at ON security_audit_logs(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_security_audit_action ON security_audit_logs(action)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_security_audit_actor ON security_audit_logs(actor_user_id)")


def user_count() -> int:
    with AUTH_DB_LOCK, auth_db() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        return int(row["count"] if row else 0)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, AUTH_PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${AUTH_PASSWORD_ITERATIONS}${base64.b64encode(salt).decode('ascii')}${base64.b64encode(digest).decode('ascii')}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, digest_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def clean_username(value: Any) -> str:
    username = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\u4e00-\u9fa5.-]{2,32}", username):
        return ""
    return username


def clean_phone(value: Any) -> str:
    phone = re.sub(r"[\s\-()]+", "", str(value or "").strip())
    if not re.fullmatch(r"\+?\d{8,15}", phone):
        return ""
    return phone


def mask_phone(phone: str | None) -> str:
    value = clean_phone(phone)
    if not value:
        return ""
    prefix = value[:-8] if len(value) > 8 else ""
    return f"{prefix}{value[-8:-4]}****{value[-2:]}"


def clean_email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", email):
        return ""
    local, domain = email.rsplit("@", 1)
    if not local or len(local) > 64 or any(part.startswith("-") or part.endswith("-") for part in domain.split(".")):
        return ""
    return email


def mask_email(email: str | None) -> str:
    value = clean_email(email)
    if not value:
        return ""
    local, domain = value.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = f"{local[:2]}***{local[-1]}"
    return f"{masked_local}@{domain}"


def clean_display_name(value: Any, fallback: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return (text or fallback)[:48]


def clean_avatar_url(value: Any) -> str:
    url = str(value or "").strip()
    if re.fullmatch(r"data:image/(png|jpe?g|webp|gif);base64,[A-Za-z0-9+/=]{32,350000}", url, re.IGNORECASE):
        return url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url[:600]


def clean_profile_bio(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()[:240]


def clean_exchange_uid(value: Any) -> str:
    uid = re.sub(r"\s+", "", str(value or "").strip())
    if not uid:
        return ""
    if len(uid) > 80 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", uid):
        raise ValueError("交易所 UID 只能包含字母、数字、_ . : -，最多 80 位")
    return uid


def exchange_uids_from_row(data: dict[str, Any]) -> dict[str, str]:
    return {
        "binance": decrypt_field(data.get("binance_uid")),
        "okx": decrypt_field(data.get("okx_uid")),
        "bitget": decrypt_field(data.get("bitget_uid")),
    }


def clean_exchange_uids_payload(payload: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("exchangeUids") or payload.get("exchange_uids") or {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "binance": clean_exchange_uid(raw.get("binance") or payload.get("binanceUid") or payload.get("binance_uid")),
        "okx": clean_exchange_uid(raw.get("okx") or payload.get("okxUid") or payload.get("okx_uid")),
        "bitget": clean_exchange_uid(raw.get("bitget") or payload.get("bitgetUid") or payload.get("bitget_uid")),
    }


MODEL_PROVIDER_PRESETS = {
    "deepseek": {
        "name": "DeepSeek",
        "baseUrl": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"],
        "note": "DeepSeek 官方 Chat Completions 兼容接口",
    },
    "openai": {
        "name": "OpenAI",
        "baseUrl": "https://api.openai.com/v1",
        "model": "gpt-5.5",
        "models": ["gpt-5.5", "gpt-5.5-chat-latest", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.2", "gpt-5.2-chat-latest", "gpt-5.1", "gpt-5.1-chat-latest", "gpt-5", "gpt-5-chat-latest", "gpt-5-mini", "gpt-5-nano"],
        "note": "OpenAI Chat Completions 兼容接口",
    },
    "qwen": {
        "name": "通义千问",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3-max-2026-01-23",
        "models": ["qwen3-max-2026-01-23", "qwen3.5-plus", "qwen3.5-plus-2026-02-15", "qwen3-plus", "qwen3-turbo", "qwen-plus-latest", "qwen-plus", "qwen-max", "qwen-turbo"],
        "note": "阿里云 DashScope OpenAI 兼容接口",
    },
    "kimi": {
        "name": "Kimi",
        "baseUrl": "https://api.moonshot.cn/v1",
        "model": "kimi-k2.6",
        "models": ["kimi-k2.6", "kimi-k2.5", "kimi-k2.5-thinking", "moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k"],
        "note": "Moonshot OpenAI 兼容接口",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-5.1",
        "models": ["glm-5.1", "glm-4.7", "glm-4-plus", "glm-4-flash", "glm-4-air"],
        "note": "智谱 OpenAI 兼容接口",
    },
    "doubao": {
        "name": "豆包",
        "baseUrl": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-1-6-flash-250615",
        "models": ["doubao-seed-1-6-flash-250615", "doubao-seed-1-6-250615", "doubao-pro-32k"],
        "note": "火山方舟 OpenAI 兼容接口",
    },
    "gemini": {
        "name": "Gemini",
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-3.5-flash",
        "models": ["gemini-3.5-flash", "gemini-3.1-pro", "gemini-3-flash-preview", "gemini-3.1-flash-lite", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"],
        "note": "Google AI Studio OpenAI 兼容接口",
    },
    "custom": {
        "name": "自定义",
        "baseUrl": "",
        "model": "",
        "models": [],
        "note": "填写任意 OpenAI Chat Completions 兼容网关",
    },
}


def clean_model_provider(value: Any) -> str:
    provider = re.sub(r"[^a-z0-9_-]+", "", str(value or "").strip().lower())[:32]
    return provider if provider in MODEL_PROVIDER_PRESETS else "custom"


def clean_model_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url.rstrip("/")[:260]


def clean_model_name(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())[:120]


MODEL_OPTION_BLOCKED_TOKENS = (
    "embedding",
    "moderation",
    "tts",
    "whisper",
    "audio",
    "image",
    "vision-preview",
    "realtime",
    "transcribe",
    "search-preview",
    "deprecated",
    "legacy",
)

MODEL_PROVIDER_PRIORITY = {
    "openai": ["gpt-5.5", "gpt-5.5-chat-latest", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.2", "gpt-5.2-chat-latest", "gpt-5.1", "gpt-5.1-chat-latest", "gpt-5", "gpt-5-chat-latest", "gpt-5-mini", "gpt-5-nano"],
    "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"],
}


def is_supported_model_option(provider: str, model_id: str) -> bool:
    model_id = clean_model_name(model_id)
    if not model_id:
        return False
    lower = model_id.lower()
    if any(token in lower for token in MODEL_OPTION_BLOCKED_TOKENS):
        return False
    if clean_model_provider(provider) == "openai":
        return lower.startswith("gpt-5")
    return True


def sort_model_options(provider: str, model_ids: list[str]) -> list[str]:
    priority = MODEL_PROVIDER_PRIORITY.get(clean_model_provider(provider), [])
    order = {model.lower(): index for index, model in enumerate(priority)}

    def rank(model_id: str) -> tuple[int, int, str]:
        lower = model_id.lower()
        return (0, order[lower], lower) if lower in order else (1, len(order), lower)

    return sorted(model_ids, key=rank)


def clean_api_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())[:800]


def mask_api_key(value: Any) -> str:
    key = clean_api_key(value)
    if not key:
        return ""
    if len(key) <= 10:
        return f"{key[:2]}***{key[-2:]}"
    return f"{key[:6]}...{key[-4:]}"


def public_model_provider_presets() -> dict[str, dict[str, str]]:
    return {
        key: {
            "name": str(value.get("name") or key),
            "baseUrl": str(value.get("baseUrl") or ""),
            "model": str(value.get("model") or ""),
            "models": [str(item) for item in value.get("models", []) if str(item or "").strip()],
            "note": str(value.get("note") or ""),
        }
        for key, value in MODEL_PROVIDER_PRESETS.items()
    }


def model_provider_default(provider: str) -> dict[str, str]:
    return MODEL_PROVIDER_PRESETS.get(clean_model_provider(provider), MODEL_PROVIDER_PRESETS["custom"])


def system_llm_settings() -> dict[str, Any]:
    provider = clean_model_provider(env_value("LLM_PROVIDER", env_value("DEEPSEEK_PROVIDER", "deepseek")))
    defaults = model_provider_default(provider)
    base_url = clean_model_url(
        env_value("LLM_BASE_URL", env_value("DEEPSEEK_BASE_URL", defaults.get("baseUrl") or ""))
    )
    model = clean_model_name(env_value("LLM_MODEL", env_value("DEEPSEEK_MODEL", defaults.get("model") or "")))
    if not is_supported_model_option(provider, model):
        model = clean_model_name(defaults.get("model") or "")
    api_key = clean_api_key(env_value("LLM_API_KEY", env_value("DEEPSEEK_API_KEY", "")))
    return {
        "provider": provider,
        "providerName": defaults.get("name") or provider,
        "baseUrl": base_url,
        "model": model,
        "apiKey": api_key,
        "temperature": safe_float(env_value("LLM_TEMPERATURE", env_value("DEEPSEEK_TEMPERATURE", "0.2")), 0.2),
        "maxTokens": int(safe_float(env_value("LLM_MAX_TOKENS", env_value("DEEPSEEK_MAX_TOKENS", "1800")), 1800)),
        "maxRows": max(1, min(80, int(safe_float(env_value("LLM_MAX_ROWS", env_value("DEEPSEEK_MAX_ROWS", "36")), 36)))),
        "source": "env",
    }


def get_user_model_settings(user_id: int) -> dict[str, Any] | None:
    with AUTH_DB_LOCK, auth_db() as conn:
        row = conn.execute("SELECT * FROM user_model_settings WHERE user_id = ?", (int(user_id),)).fetchone()
    return row_dict(row) if row else None


def llm_settings_for_user(user_id: int | None = None) -> dict[str, Any]:
    system_settings = system_llm_settings()
    if not user_id:
        return system_settings
    row = get_user_model_settings(int(user_id))
    if not row:
        return system_settings
    provider = clean_model_provider(row.get("provider"))
    defaults = model_provider_default(provider)
    base_url = clean_model_url(row.get("base_url")) or str(defaults.get("baseUrl") or "") or system_settings["baseUrl"]
    model = clean_model_name(row.get("model")) or str(defaults.get("model") or "") or system_settings["model"]
    if not is_supported_model_option(provider, model):
        model = clean_model_name(defaults.get("model") or "") or system_settings["model"]
    api_key = clean_api_key(decrypt_field(row.get("api_key"))) or system_settings["apiKey"]
    return {
        "provider": provider,
        "providerName": defaults.get("name") or provider,
        "baseUrl": base_url,
        "model": model,
        "apiKey": api_key,
        "temperature": safe_float(row.get("temperature"), system_settings["temperature"]),
        "maxTokens": max(256, min(8192, int(safe_float(row.get("max_tokens"), system_settings["maxTokens"])))),
        "maxRows": max(1, min(80, int(safe_float(row.get("max_rows"), system_settings["maxRows"])))),
        "source": "user" if clean_api_key(decrypt_field(row.get("api_key"))) else system_settings["source"],
        "updatedAt": int(row.get("updated_at") or 0),
    }


def public_llm_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": settings.get("provider") or "deepseek",
        "providerName": settings.get("providerName") or settings.get("provider") or "DeepSeek",
        "baseUrl": settings.get("baseUrl") or "",
        "model": settings.get("model") or "",
        "hasApiKey": bool(clean_api_key(settings.get("apiKey"))),
        "maskedApiKey": mask_api_key(settings.get("apiKey")),
        "temperature": safe_float(settings.get("temperature"), 0.2),
        "maxTokens": int(safe_float(settings.get("maxTokens"), 1800)),
        "maxRows": int(safe_float(settings.get("maxRows"), 36)),
        "source": settings.get("source") or "env",
        "updatedAt": int(settings.get("updatedAt") or 0),
    }


def model_options_endpoint(base_url: str) -> str:
    url = clean_model_url(base_url)
    if not url:
        return ""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/models"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    path = f"{path}/models" if path else "/models"
    return parsed._replace(path=path, params="", query="", fragment="").geturl()


def extract_model_ids(payload: Any, provider: str = "") -> list[str]:
    candidates: list[Any] = []
    if isinstance(payload, dict):
        for key in ("data", "models", "model_list", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        if not candidates and isinstance(payload.get("model"), list):
            candidates.extend(payload["model"])
    elif isinstance(payload, list):
        candidates.extend(payload)
    model_ids: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if isinstance(item, dict):
            raw = item.get("id") or item.get("name") or item.get("model")
        else:
            raw = item
        model_id = clean_model_name(raw)
        if not model_id or model_id in seen:
            continue
        if not is_supported_model_option(provider, model_id):
            continue
        seen.add(model_id)
        model_ids.append(model_id)
    return sort_model_options(provider, model_ids)[:200]


def fallback_model_options(provider: str, selected_model: str = "") -> list[str]:
    preset = model_provider_default(provider)
    models: list[str] = []
    for item in [selected_model, preset.get("model"), *(preset.get("models") or [])]:
        model = clean_model_name(item)
        if model and is_supported_model_option(provider, model) and model not in models:
            models.append(model)
    return sort_model_options(provider, models)


def model_options_payload(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    current = llm_settings_for_user(user_id)
    provider = clean_model_provider(payload.get("provider") or current.get("provider"))
    defaults = model_provider_default(provider)
    base_url = clean_model_url(payload.get("baseUrl") or payload.get("base_url")) or current.get("baseUrl") or defaults.get("baseUrl") or ""
    selected_model = clean_model_name(payload.get("model") or current.get("model") or defaults.get("model") or "")
    api_key = clean_api_key(payload.get("apiKey") or payload.get("api_key")) or clean_api_key(current.get("apiKey"))
    fallback = fallback_model_options(provider, selected_model)
    models_url = model_options_endpoint(base_url)
    if not api_key or not models_url:
        return {
            "ok": True,
            "provider": provider,
            "models": fallback,
            "source": "preset",
            "reason": "missing_api_key_or_base_url",
        }
    try:
        response = requests.get(
            models_url,
            headers={
                **HEADERS,
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=max(4, int(safe_float(os.getenv("LLM_MODELS_TIMEOUT", "12"), 12))),
        )
        if response.status_code >= 400:
            return {
                "ok": True,
                "provider": provider,
                "models": fallback,
                "source": "preset",
                "reason": f"remote_http_{response.status_code}",
            }
        remote_models = extract_model_ids(response.json(), provider)
        if selected_model and is_supported_model_option(provider, selected_model) and selected_model not in remote_models:
            remote_models.insert(0, selected_model)
        remote_models = sort_model_options(provider, remote_models)
        if remote_models:
            return {
                "ok": True,
                "provider": provider,
                "models": remote_models,
                "source": "remote",
                "endpoint": models_url,
            }
    except Exception as exc:
        return {
            "ok": True,
            "provider": provider,
            "models": fallback,
            "source": "preset",
            "reason": str(exc)[:180],
        }
    return {
        "ok": True,
        "provider": provider,
        "models": fallback,
        "source": "preset",
        "reason": "empty_remote_models",
    }


def save_user_model_settings(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    provider = clean_model_provider(payload.get("provider"))
    defaults = model_provider_default(provider)
    base_url = clean_model_url(payload.get("baseUrl") or payload.get("base_url")) or str(defaults.get("baseUrl") or "")
    model = clean_model_name(payload.get("model")) or str(defaults.get("model") or "")
    if not is_supported_model_option(provider, model):
        model = clean_model_name(defaults.get("model") or "")
    api_key = clean_api_key(payload.get("apiKey") or payload.get("api_key"))
    clear_api_key = bool(payload.get("clearApiKey") or payload.get("clear_api_key"))
    temperature = max(0, min(2, safe_float(payload.get("temperature"), 0.2)))
    max_tokens = max(256, min(8192, int(safe_float(payload.get("maxTokens") or payload.get("max_tokens"), 1800))))
    max_rows = max(1, min(80, int(safe_float(payload.get("maxRows") or payload.get("max_rows"), 36))))
    now = int(time.time())
    with AUTH_DB_LOCK, auth_db() as conn:
        existing = conn.execute("SELECT api_key FROM user_model_settings WHERE user_id = ?", (int(user_id),)).fetchone()
        existing_key = decrypt_field(existing["api_key"]) if existing else ""
        stored_key = "" if clear_api_key else encrypt_field(api_key or existing_key)
        conn.execute(
            """
            INSERT INTO user_model_settings (
                user_id, provider, base_url, model, api_key, temperature, max_tokens, max_rows, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                provider = excluded.provider,
                base_url = excluded.base_url,
                model = excluded.model,
                api_key = excluded.api_key,
                temperature = excluded.temperature,
                max_tokens = excluded.max_tokens,
                max_rows = excluded.max_rows,
                updated_at = excluded.updated_at
            """,
            (int(user_id), provider, base_url, model, stored_key, temperature, max_tokens, max_rows, now),
        )
    return public_llm_settings(llm_settings_for_user(int(user_id)))


def row_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    if isinstance(row, dict):
        return row
    return {key: row[key] for key in row.keys()}


def unique_username(conn: sqlite3.Connection, preferred: str, fallback_prefix: str = "user") -> str:
    base = clean_username(preferred)
    if not base:
        base = f"{fallback_prefix}{secrets.randbelow(9000) + 1000}"
    base = base[:24]
    candidate = base
    for _ in range(80):
        row = conn.execute("SELECT 1 FROM users WHERE username = ? LIMIT 1", (candidate,)).fetchone()
        if not row:
            return candidate
        suffix = secrets.token_hex(2)
        candidate = clean_username(f"{base[:24]}{suffix}") or f"{fallback_prefix}{suffix}"
    return f"{fallback_prefix}{int(time.time())}"


def create_user(
    username: str,
    password: str,
    role: str = "user",
    *,
    display_name: str = "",
    avatar_url: str = "",
    email: str = "",
    phone: str = "",
    google_sub: str = "",
    auth_provider: str = "password",
) -> dict[str, Any]:
    now = int(time.time())
    email_value, email_hash = encrypted_email_value(email)
    phone_value, phone_hash = encrypted_phone_value(phone)
    google_value, google_hash = encrypted_google_sub_value(google_sub)
    with AUTH_DB_LOCK, auth_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO users (
                username, password_hash, role, created_at, updated_at,
                display_name, avatar_url, email, phone, google_sub,
                email_hash, phone_hash, google_sub_hash, auth_provider
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                hash_password(password) if password else "",
                role,
                now,
                now,
                clean_display_name(display_name, username),
                clean_avatar_url(avatar_url),
                email_value,
                phone_value,
                google_value,
                email_hash,
                phone_hash,
                google_hash,
                str(auth_provider or "password").strip()[:32],
            ),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return public_user(row) or {"id": cursor.lastrowid, "username": username, "role": role}


def public_user(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    data = row_dict(row)
    if not data:
        return None
    username = str(data.get("username") or "")
    display_name = clean_display_name(data.get("display_name"), username)
    email = decrypt_field(data.get("email"))
    phone = decrypt_field(data.get("phone"))
    google_sub = decrypt_field(data.get("google_sub"))
    return {
        "id": int(data["id"]),
        "username": username,
        "displayName": display_name,
        "role": data.get("role") or "user",
        "avatarUrl": clean_avatar_url(data.get("avatar_url")),
        "bio": clean_profile_bio(data.get("bio")),
        "emailMasked": mask_email(email),
        "emailBound": bool(clean_email(email)),
        "phoneMasked": mask_phone(phone),
        "phoneBound": bool(clean_phone(phone)),
        "googleBound": bool(str(google_sub or "").strip()),
        "authProvider": data.get("auth_provider") or "password",
        "exchangeUids": exchange_uids_from_row(data),
    }


def admin_user() -> dict[str, Any] | None:
    with AUTH_DB_LOCK, auth_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1").fetchone()
        return public_user(row)


def create_session(user_id: int, user_agent: str = "") -> tuple[str, int]:
    now = int(time.time())
    expires_at = now + AUTH_SESSION_DAYS * 24 * 60 * 60
    token = secrets.token_urlsafe(36)
    with AUTH_DB_LOCK, auth_db() as conn:
        conn.execute(
            "INSERT INTO sessions (user_id, token_hash, created_at, expires_at, user_agent) VALUES (?, ?, ?, ?, ?)",
            (user_id, token_hash(token), now, expires_at, user_agent[:240]),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    return token, expires_at


def cookie_header(token: str, expires_at: int) -> str:
    max_age = max(0, expires_at - int(time.time()))
    secure = "; Secure" if cookie_secure_enabled() else ""
    return f"{AUTH_SESSION_COOKIE}={token}; Max-Age={max_age}; Path=/; HttpOnly; SameSite=Lax{secure}"


def expired_cookie_header() -> str:
    secure = "; Secure" if cookie_secure_enabled() else ""
    return f"{AUTH_SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax{secure}"


def parse_cookies(header: str | None) -> dict[str, str]:
    if not header:
        return {}
    cookie = SimpleCookie()
    try:
        cookie.load(header)
    except Exception:
        return {}
    return {key: morsel.value for key, morsel in cookie.items()}


def current_user_from_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    now = int(time.time())
    with AUTH_DB_LOCK, auth_db() as conn:
        row = conn.execute(
            """
            SELECT users.*, sessions.expires_at
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """,
            (token_hash(token), now),
        ).fetchone()
        return public_user(row)


def delete_session(token: str) -> None:
    if not token:
        return
    with AUTH_DB_LOCK, auth_db() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))


def login_rate_limited(key: str) -> bool:
    now = time.time()
    with AUTH_LOGIN_LOCK:
        attempts = [item for item in AUTH_LOGIN_ATTEMPTS.get(key, []) if now - item < 300]
        AUTH_LOGIN_ATTEMPTS[key] = attempts
        return len(attempts) >= 8


def record_login_failure(key: str) -> None:
    now = time.time()
    with AUTH_LOGIN_LOCK:
        attempts = [item for item in AUTH_LOGIN_ATTEMPTS.get(key, []) if now - item < 300]
        attempts.append(now)
        AUTH_LOGIN_ATTEMPTS[key] = attempts


def clear_login_failures(key: str) -> None:
    with AUTH_LOGIN_LOCK:
        AUTH_LOGIN_ATTEMPTS.pop(key, None)


def issue_phone_code(phone: str, client_key: str) -> dict[str, Any]:
    if login_rate_limited(f"sms:{client_key}:{phone}"):
        raise ValueError("验证码发送过于频繁，请稍后再试")
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = int(time.time()) + AUTH_PHONE_CODE_TTL_SECONDS
    with AUTH_PHONE_LOCK:
        AUTH_PHONE_CODES[phone] = {"code": code, "expiresAt": expires_at, "attempts": 0}
    record_login_failure(f"sms:{client_key}:{phone}")
    webhook = env_value("SMS_WEBHOOK_URL")
    sent = False
    if webhook:
        response = requests.post(
            webhook,
            json={"phone": phone, "code": code, "scene": "星云社登录", "ttlSeconds": AUTH_PHONE_CODE_TTL_SECONDS},
            timeout=12,
        )
        response.raise_for_status()
        sent = True
    payload = {"ok": True, "expiresIn": AUTH_PHONE_CODE_TTL_SECONDS, "sent": sent}
    if not webhook and expose_dev_code("XINGYUN_EXPOSE_DEV_PHONE_CODE"):
        payload["devCode"] = code
    return payload


def email_smtp_configured() -> bool:
    return bool(env_value("EMAIL_SMTP_HOST") and env_value("EMAIL_SMTP_FROM"))


def send_email_code_message(email: str, code: str) -> bool:
    webhook = env_value("EMAIL_WEBHOOK_URL")
    payload = {"email": email, "code": code, "scene": "星云社登录", "ttlSeconds": AUTH_EMAIL_CODE_TTL_SECONDS}
    if webhook:
        response = requests.post(webhook, json=payload, timeout=12)
        response.raise_for_status()
        return True

    if not email_smtp_configured():
        return False

    host = env_value("EMAIL_SMTP_HOST")
    port = int(env_value("EMAIL_SMTP_PORT", "587") or "587")
    username = env_value("EMAIL_SMTP_USERNAME")
    password = env_value("EMAIL_SMTP_PASSWORD")
    sender = env_value("EMAIL_SMTP_FROM")
    sender_name = env_value("EMAIL_FROM_NAME", "星云社")
    use_ssl = env_value("EMAIL_SMTP_SSL", "0") == "1" or port == 465
    use_tls = env_value("EMAIL_SMTP_TLS", "1") != "0"

    message = EmailMessage()
    message["Subject"] = "星云社验证码"
    message["From"] = f"{sender_name} <{sender}>"
    message["To"] = email
    message.set_content(
        f"你的星云社验证码是：{code}\n\n"
        f"验证码 5 分钟内有效。如果不是你本人操作，可以忽略这封邮件。\n"
    )

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=12) as smtp:
            if username or password:
                smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=12) as smtp:
            if use_tls:
                smtp.starttls()
            if username or password:
                smtp.login(username, password)
            smtp.send_message(message)
    return True


EMAIL_SMTP_FIELDS = {
    "HOST",
    "PORT",
    "USERNAME",
    "PASSWORD",
    "FROM",
    "FROM_NAME",
    "TLS",
    "SSL",
}


def email_smtp_bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def read_repeated_email_smtp_configs() -> list[dict[str, str]]:
    configs: list[dict[str, str]] = []
    env_path = ROOT / ".env"
    if not env_path.exists():
        return configs
    current: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key == "EMAIL_FROM_NAME":
            if current:
                current["FROM_NAME"] = value
            continue
        if not key.startswith("EMAIL_SMTP_"):
            continue
        suffix = key.removeprefix("EMAIL_SMTP_")
        if suffix not in EMAIL_SMTP_FIELDS:
            continue
        if suffix == "HOST" and current.get("HOST"):
            if current.get("HOST") and current.get("FROM"):
                configs.append(current)
            current = {}
        current[suffix] = value
    if current.get("HOST") and current.get("FROM"):
        configs.append(current)
    return configs


def email_smtp_config_from_env(prefix: str = "EMAIL_SMTP_") -> dict[str, str] | None:
    config = {
        "HOST": env_value(f"{prefix}HOST"),
        "PORT": env_value(f"{prefix}PORT", "587"),
        "USERNAME": env_value(f"{prefix}USERNAME"),
        "PASSWORD": env_value(f"{prefix}PASSWORD"),
        "FROM": env_value(f"{prefix}FROM"),
        "FROM_NAME": env_value(f"{prefix}FROM_NAME") or env_value("EMAIL_FROM_NAME", "\u661f\u4e91\u793e"),
        "TLS": env_value(f"{prefix}TLS", "1"),
        "SSL": env_value(f"{prefix}SSL", "0"),
    }
    if not config["HOST"] or not config["FROM"]:
        return None
    return config


def email_smtp_configs() -> list[dict[str, str]]:
    configs: list[dict[str, str]] = []
    configs.extend(read_repeated_email_smtp_configs())
    for prefix in (
        "EMAIL_SMTP_1_",
        "EMAIL_SMTP_2_",
        "EMAIL_SMTP_QQ_",
        "EMAIL_SMTP_GMAIL_",
        "EMAIL_SMTP_GOOGLE_",
        "EMAIL_SMTP_",
    ):
        if config := email_smtp_config_from_env(prefix):
            configs.append(config)

    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, str]] = []
    for config in configs:
        key = (
            str(config.get("HOST") or "").lower(),
            str(config.get("USERNAME") or "").lower(),
            str(config.get("FROM") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(config)
    return unique


def email_smtp_configured() -> bool:
    return bool(email_smtp_configs())


def send_email_with_smtp_config(config: dict[str, str], email: str, code: str) -> None:
    host = str(config.get("HOST") or "")
    port = int(str(config.get("PORT") or "587"))
    username = str(config.get("USERNAME") or "")
    password = str(config.get("PASSWORD") or "")
    sender = str(config.get("FROM") or username)
    sender_name = str(config.get("FROM_NAME") or "\u661f\u4e91\u793e")
    use_ssl = email_smtp_bool(str(config.get("SSL") or ""), port == 465)
    use_tls = email_smtp_bool(str(config.get("TLS") or ""), not use_ssl)

    message = EmailMessage()
    message["Subject"] = "\u661f\u4e91\u793e\u9a8c\u8bc1\u7801"
    message["From"] = f"{sender_name} <{sender}>"
    message["To"] = email
    message.set_content(
        f"\u4f60\u7684\u661f\u4e91\u793e\u9a8c\u8bc1\u7801\u662f\uff1a{code}\n\n"
        "\u9a8c\u8bc1\u7801 5 \u5206\u949f\u5185\u6709\u6548\u3002"
        "\u5982\u679c\u4e0d\u662f\u4f60\u672c\u4eba\u64cd\u4f5c\uff0c\u53ef\u4ee5\u5ffd\u7565\u8fd9\u5c01\u90ae\u4ef6\u3002\n"
    )

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=12) as smtp:
            if username or password:
                smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=12) as smtp:
            if use_tls:
                smtp.starttls()
            if username or password:
                smtp.login(username, password)
            smtp.send_message(message)


def send_email_code_message(email: str, code: str) -> bool:
    webhook = env_value("EMAIL_WEBHOOK_URL")
    payload = {"email": email, "code": code, "scene": "\u661f\u4e91\u793e\u767b\u5f55", "ttlSeconds": AUTH_EMAIL_CODE_TTL_SECONDS}
    if webhook:
        response = requests.post(webhook, json=payload, timeout=12)
        response.raise_for_status()
        return True

    configs = email_smtp_configs()
    if not configs:
        return False

    last_error: Exception | None = None
    for config in configs:
        try:
            send_email_with_smtp_config(config, email, code)
            return True
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return False


def issue_email_code(email: str, client_key: str) -> dict[str, Any]:
    if login_rate_limited(f"email:{client_key}:{email}"):
        raise ValueError("验证码发送过于频繁，请稍后再试")
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = int(time.time()) + AUTH_EMAIL_CODE_TTL_SECONDS
    with AUTH_EMAIL_LOCK:
        AUTH_EMAIL_CODES[email] = {"code": code, "expiresAt": expires_at, "attempts": 0}
    record_login_failure(f"email:{client_key}:{email}")
    sent = send_email_code_message(email, code)
    payload = {"ok": True, "expiresIn": AUTH_EMAIL_CODE_TTL_SECONDS, "sent": sent}
    if not sent and expose_dev_code("XINGYUN_EXPOSE_DEV_EMAIL_CODE"):
        payload["devCode"] = code
    return payload


def verify_email_code(email: str, code: str) -> None:
    now = int(time.time())
    with AUTH_EMAIL_LOCK:
        item = AUTH_EMAIL_CODES.get(email)
        if not item or int(item.get("expiresAt") or 0) < now:
            AUTH_EMAIL_CODES.pop(email, None)
            raise ValueError("验证码已过期，请重新获取")
        attempts = int(item.get("attempts") or 0) + 1
        item["attempts"] = attempts
        if attempts > 6:
            AUTH_EMAIL_CODES.pop(email, None)
            raise ValueError("验证码错误次数过多，请重新获取")
        if not hmac.compare_digest(str(item.get("code") or ""), str(code or "").strip()):
            raise ValueError("验证码不正确")
        AUTH_EMAIL_CODES.pop(email, None)


def verify_phone_code(phone: str, code: str) -> None:
    now = int(time.time())
    with AUTH_PHONE_LOCK:
        item = AUTH_PHONE_CODES.get(phone)
        if not item or int(item.get("expiresAt") or 0) < now:
            AUTH_PHONE_CODES.pop(phone, None)
            raise ValueError("验证码已过期，请重新获取")
        attempts = int(item.get("attempts") or 0) + 1
        item["attempts"] = attempts
        if attempts > 6:
            AUTH_PHONE_CODES.pop(phone, None)
            raise ValueError("验证码错误次数过多，请重新获取")
        if not hmac.compare_digest(str(item.get("code") or ""), str(code or "").strip()):
            raise ValueError("验证码不正确")
        AUTH_PHONE_CODES.pop(phone, None)


def user_from_phone(phone: str) -> dict[str, Any]:
    phone = clean_phone(phone)
    phone_value, phone_hash = encrypted_phone_value(phone)
    now = int(time.time())
    with AUTH_DB_LOCK, auth_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE phone_hash = ? OR phone = ? LIMIT 1",
            (phone_hash, phone),
        ).fetchone()
        if row:
            return public_user(row) or {}
        username = unique_username(conn, f"u{phone[-4:]}", "phone")
        cursor = conn.execute(
            """
            INSERT INTO users (
                username, password_hash, role, created_at, updated_at,
                display_name, avatar_url, phone, phone_hash, google_sub, auth_provider
            )
            VALUES (?, '', 'user', ?, ?, ?, '', ?, ?, '', 'phone')
            """,
            (username, now, now, f"用户{phone[-4:]}", phone_value, phone_hash),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return public_user(row) or {"id": cursor.lastrowid, "username": username, "role": "user"}


def user_from_email(email: str) -> dict[str, Any]:
    email = clean_email(email)
    if not email:
        raise ValueError("请输入有效邮箱")
    local = email.split("@", 1)[0]
    email_value, email_hash = encrypted_email_value(email)
    now = int(time.time())
    with AUTH_DB_LOCK, auth_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email_hash = ? OR email = ? LIMIT 1",
            (email_hash, email),
        ).fetchone()
        if row:
            return public_user(row) or {}
        username = unique_username(conn, local, "email")
        cursor = conn.execute(
            """
            INSERT INTO users (
                username, password_hash, role, created_at, updated_at,
                display_name, avatar_url, email, email_hash, phone, google_sub, auth_provider
            )
            VALUES (?, '', 'user', ?, ?, ?, '', ?, ?, '', '', 'email')
            """,
            (username, now, now, clean_display_name(local, "邮箱用户"), email_value, email_hash),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return public_user(row) or {"id": cursor.lastrowid, "username": username, "role": "user"}


def google_oauth_enabled() -> bool:
    return bool(env_value("GOOGLE_CLIENT_ID") and env_value("GOOGLE_CLIENT_SECRET"))


def safe_next_path(value: Any) -> str:
    text = str(value or "").strip() or "/index.html"
    parsed = urlparse(text)
    if parsed.scheme or parsed.netloc:
        return "/index.html"
    if text.startswith("./"):
        text = "/" + text[2:]
    if not text.startswith("/"):
        text = f"/{text}"
    return text


def google_user_login(info: dict[str, Any]) -> dict[str, Any]:
    sub = str(info.get("sub") or "").strip()
    if not sub:
        raise ValueError("Google 没有返回用户 ID")
    email = clean_email(info.get("email"))
    display_name = clean_display_name(info.get("name") or email or "Google 用户", "Google 用户")
    avatar_url = clean_avatar_url(info.get("picture"))
    preferred = email.split("@", 1)[0] if "@" in email else display_name
    email_value, email_hash = encrypted_email_value(email)
    google_value, google_hash = encrypted_google_sub_value(sub)
    now = int(time.time())
    with AUTH_DB_LOCK, auth_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE google_sub_hash = ? OR google_sub = ? LIMIT 1",
            (google_hash, sub),
        ).fetchone()
        if row:
            row_email = clean_email(decrypt_field(row["email"]))
            next_email = row_email or email
            next_email_value, next_email_hash = encrypted_email_value(next_email)
            owner = conn.execute(
                "SELECT id FROM users WHERE email_hash = ? OR email = ? LIMIT 1",
                (next_email_hash, next_email),
            ).fetchone() if next_email else None
            if owner and int(owner["id"]) != int(row["id"]):
                next_email = row_email
                next_email_value, next_email_hash = encrypted_email_value(next_email)
            conn.execute(
                """
                UPDATE users
                SET display_name = ?, avatar_url = ?, email = ?, email_hash = ?,
                    google_sub = ?, google_sub_hash = ?, auth_provider = 'google', updated_at = ?
                WHERE id = ?
                """,
                (display_name, avatar_url, next_email_value, next_email_hash, google_value, google_hash, now, int(row["id"])),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (int(row["id"]),)).fetchone()
            return public_user(row) or {}
        if email:
            email_row = conn.execute(
                "SELECT * FROM users WHERE email_hash = ? OR email = ? LIMIT 1",
                (email_hash, email),
            ).fetchone()
            if email_row:
                conn.execute(
                    """
                    UPDATE users
                    SET google_sub = ?, google_sub_hash = ?, display_name = ?, avatar_url = ?, auth_provider = 'google', updated_at = ?
                    WHERE id = ?
                    """,
                    (google_value, google_hash, display_name, avatar_url, now, int(email_row["id"])),
                )
                row = conn.execute("SELECT * FROM users WHERE id = ?", (int(email_row["id"]),)).fetchone()
                return public_user(row) or {}
        username = unique_username(conn, preferred, "google")
        cursor = conn.execute(
            """
            INSERT INTO users (
                username, password_hash, role, created_at, updated_at,
                display_name, avatar_url, email, email_hash, phone, google_sub, google_sub_hash, auth_provider
            )
            VALUES (?, '', 'user', ?, ?, ?, ?, ?, ?, '', ?, ?, 'google')
            """,
            (username, now, now, display_name, avatar_url, email_value, email_hash, google_value, google_hash),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return public_user(row) or {"id": cursor.lastrowid, "username": username, "role": "user"}


def bind_phone_to_user(user_id: int, phone: str) -> dict[str, Any]:
    phone = clean_phone(phone)
    if not phone:
        raise ValueError("请输入有效手机号")
    phone_value, phone_hash = encrypted_phone_value(phone)
    now = int(time.time())
    with AUTH_DB_LOCK, auth_db() as conn:
        owner = conn.execute(
            "SELECT id FROM users WHERE phone_hash = ? OR phone = ? LIMIT 1",
            (phone_hash, phone),
        ).fetchone()
        if owner and int(owner["id"]) != int(user_id):
            raise ValueError("这个手机号已绑定其他账号")
        conn.execute(
            "UPDATE users SET phone = ?, phone_hash = ?, updated_at = ? WHERE id = ?",
            (phone_value, phone_hash, now, int(user_id)),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return public_user(row) or {}


def bind_email_to_user(user_id: int, email: str) -> dict[str, Any]:
    email = clean_email(email)
    if not email:
        raise ValueError("请输入有效邮箱")
    email_value, email_hash = encrypted_email_value(email)
    now = int(time.time())
    with AUTH_DB_LOCK, auth_db() as conn:
        owner = conn.execute(
            "SELECT id FROM users WHERE email_hash = ? OR email = ? LIMIT 1",
            (email_hash, email),
        ).fetchone()
        if owner and int(owner["id"]) != int(user_id):
            raise ValueError("这个邮箱已绑定其他账号")
        conn.execute(
            "UPDATE users SET email = ?, email_hash = ?, updated_at = ? WHERE id = ?",
            (email_value, email_hash, now, int(user_id)),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return public_user(row) or {}


def bind_google_to_user(user_id: int, info: dict[str, Any]) -> dict[str, Any]:
    sub = str(info.get("sub") or "").strip()
    if not sub:
        raise ValueError("Google 没有返回用户标识")
    email = clean_email(info.get("email"))
    display_name = clean_display_name(info.get("name") or email or "Google 用户", "Google 用户")
    avatar_url = clean_avatar_url(info.get("picture"))
    google_value, google_hash = encrypted_google_sub_value(sub)
    now = int(time.time())
    with AUTH_DB_LOCK, auth_db() as conn:
        owner = conn.execute(
            "SELECT id FROM users WHERE google_sub_hash = ? OR google_sub = ? LIMIT 1",
            (google_hash, sub),
        ).fetchone()
        if owner and int(owner["id"]) != int(user_id):
            raise ValueError("这个 Google 账号已绑定其他星云社账号")
        row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if not row:
            raise ValueError("当前账号不存在")
        next_display = clean_display_name(row["display_name"], row["username"]) or display_name
        next_avatar = clean_avatar_url(row["avatar_url"]) or avatar_url
        row_email = clean_email(decrypt_field(row["email"]))
        next_email = row_email or email
        next_email_value, next_email_hash = encrypted_email_value(next_email)
        email_owner = conn.execute(
            "SELECT id FROM users WHERE email_hash = ? OR email = ? LIMIT 1",
            (next_email_hash, next_email),
        ).fetchone() if next_email else None
        if email_owner and int(email_owner["id"]) != int(user_id):
            next_email = row_email
            next_email_value, next_email_hash = encrypted_email_value(next_email)
        conn.execute(
            """
            UPDATE users
            SET google_sub = ?, google_sub_hash = ?, display_name = ?, avatar_url = ?,
                email = ?, email_hash = ?, auth_provider = 'google', updated_at = ?
            WHERE id = ?
            """,
            (google_value, google_hash, next_display, next_avatar, next_email_value, next_email_hash, now, int(user_id)),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return public_user(row) or {}


def update_user_profile(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    display_name = clean_display_name(payload.get("displayName") or payload.get("display_name"), "")
    bio = clean_profile_bio(payload.get("bio"))
    avatar_url = clean_avatar_url(payload.get("avatarUrl") or payload.get("avatar_url"))
    exchange_uids = clean_exchange_uids_payload(payload)
    if not display_name:
        raise ValueError("名字不能为空")
    now = int(time.time())
    with AUTH_DB_LOCK, auth_db() as conn:
        conn.execute(
            """
            UPDATE users
            SET display_name = ?, bio = ?, avatar_url = ?,
                binance_uid = ?, okx_uid = ?, bitget_uid = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                display_name,
                bio,
                avatar_url,
                encrypted_exchange_uid(exchange_uids["binance"]),
                encrypted_exchange_uid(exchange_uids["okx"]),
                encrypted_exchange_uid(exchange_uids["bitget"]),
                now,
                int(user_id),
            ),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return public_user(row) or {}


def add_query_params(path: str, params: dict[str, str]) -> str:
    parsed = urlparse(path or "/index.html")
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in params.items():
        query[key] = [value]
    next_query = urlencode(query, doseq=True)
    rebuilt = parsed.path or "/index.html"
    if next_query:
        rebuilt += f"?{next_query}"
    if parsed.fragment:
        rebuilt += f"#{parsed.fragment}"
    return rebuilt


def user_payload_exists(user: dict[str, Any] | None, scope: str) -> bool:
    if not user:
        return False
    user_id = int(safe_float(user.get("id")) or 0)
    if user_id <= 0 or not scope:
        return False
    with AUTH_DB_LOCK, auth_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_payloads WHERE user_id = ? AND scope = ? LIMIT 1",
            (user_id, scope),
        ).fetchone()
        return bool(row)


def load_user_payload(user: dict[str, Any] | None, scope: str) -> dict[str, Any]:
    if not user:
        return {}
    user_id = int(safe_float(user.get("id")) or 0)
    if user_id <= 0 or not scope:
        return {}
    with AUTH_DB_LOCK, auth_db() as conn:
        row = conn.execute(
            "SELECT payload FROM user_payloads WHERE user_id = ? AND scope = ? LIMIT 1",
            (user_id, scope),
        ).fetchone()
    if not row:
        return {}
    try:
        parsed = json.loads(row["payload"])
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def save_user_payload(user: dict[str, Any] | None, scope: str, payload: dict[str, Any]) -> None:
    if not user:
        return
    user_id = int(safe_float(user.get("id")) or 0)
    if user_id <= 0 or not scope:
        return
    now = int(time.time())
    text = json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False, separators=(",", ":"))
    with AUTH_DB_LOCK, auth_db() as conn:
        conn.execute(
            """
            INSERT INTO user_payloads (user_id, scope, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, scope) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (user_id, scope, text, now, now),
        )


def is_admin(user: dict[str, Any] | None) -> bool:
    return bool(user and user.get("role") == "admin")


def admin_count() -> int:
    with AUTH_DB_LOCK, auth_db() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM users WHERE role = 'admin'").fetchone()
        return int(row["count"] if row else 0)


def parse_stored_payload(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def admin_user_rows() -> list[dict[str, Any]]:
    with AUTH_DB_LOCK, auth_db() as conn:
        users = conn.execute(
            """
            SELECT
                id, username, role, created_at, updated_at,
                display_name, avatar_url, bio, email, phone, google_sub, auth_provider,
                binance_uid, okx_uid, bitget_uid
            FROM users
            ORDER BY id ASC
            """
        ).fetchall()
        sessions = {
            int(row["user_id"]): int(row["count"])
            for row in conn.execute(
                "SELECT user_id, COUNT(*) AS count FROM sessions WHERE expires_at > ? GROUP BY user_id",
                (int(time.time()),),
            ).fetchall()
        }
        payload_rows = conn.execute(
            "SELECT user_id, scope, payload, updated_at FROM user_payloads ORDER BY updated_at DESC"
        ).fetchall()

    payload_map: dict[int, dict[str, Any]] = {}
    for row in payload_rows:
        user_id = int(row["user_id"])
        scope = str(row["scope"] or "")
        parsed = parse_stored_payload(row["payload"])
        summary: dict[str, Any] = {"scope": scope, "updatedAt": int(row["updated_at"] or 0)}
        if scope == USER_SCOPE_TODO:
            tasks = parsed.get("tasks") if isinstance(parsed.get("tasks"), list) else []
            projects = parsed.get("projects") if isinstance(parsed.get("projects"), list) else []
            summary.update({"tasks": len(tasks), "projects": len(projects)})
        elif scope == USER_SCOPE_X_KOL_SOURCES:
            sources = parsed.get("sources") if isinstance(parsed.get("sources"), list) else []
            enabled = [item for item in sources if isinstance(item, dict) and item.get("enabled") is not False]
            summary.update({"sources": len(sources), "enabledSources": len(enabled)})
        payload_map.setdefault(user_id, {})[scope] = summary

    result = []
    for row in users:
        user_id = int(row["id"])
        public = public_user(row) or {}
        payloads = payload_map.get(user_id, {})
        todo = payloads.get(USER_SCOPE_TODO, {})
        x_sources = payloads.get(USER_SCOPE_X_KOL_SOURCES, {})
        result.append(
            {
                "id": user_id,
                "username": row["username"],
                "displayName": public.get("displayName") or row["username"],
                "role": row["role"],
                "avatarUrl": public.get("avatarUrl") or "",
                "bio": public.get("bio") or "",
                "emailMasked": public.get("emailMasked") or "",
                "emailBound": bool(public.get("emailBound")),
                "phoneMasked": public.get("phoneMasked") or "",
                "phoneBound": bool(public.get("phoneBound")),
                "googleBound": bool(public.get("googleBound")),
                "authProvider": public.get("authProvider") or "password",
                "exchangeUids": public.get("exchangeUids") or {},
                "createdAt": int(row["created_at"] or 0),
                "updatedAt": int(row["updated_at"] or 0),
                "activeSessions": sessions.get(user_id, 0),
                "payloadScopes": sorted(payloads.keys()),
                "todoTasks": int(todo.get("tasks") or 0),
                "todoProjects": int(todo.get("projects") or 0),
                "xSources": int(x_sources.get("sources") or 0),
                "xEnabledSources": int(x_sources.get("enabledSources") or 0),
            }
        )
    return result


def admin_set_user(payload: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    user_id = int(float(payload.get("id") or payload.get("userId") or 0))
    role = str(payload.get("role") or "").strip().lower()
    password = str(payload.get("password") or "")
    if user_id <= 0:
        raise ValueError("用户不存在")
    if role and role not in {"admin", "user"}:
        raise ValueError("角色只能是 admin 或 user")
    if password and not (8 <= len(password) <= 128):
        raise ValueError("密码至少 8 位")

    now = int(time.time())
    with AUTH_DB_LOCK, auth_db() as conn:
        row = conn.execute("SELECT id, username, role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        if role and row["role"] == "admin" and role != "admin":
            admin_total = conn.execute("SELECT COUNT(*) AS count FROM users WHERE role = 'admin'").fetchone()
            if int(admin_total["count"] if admin_total else 0) <= 1:
                raise ValueError("至少保留一个管理员")
        if role:
            conn.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?", (role, now, user_id))
        if password:
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(password), now, user_id),
            )
            if user_id != int(current.get("id") or 0):
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return {"ok": True, "users": admin_user_rows()}


def admin_create_user(payload: dict[str, Any]) -> dict[str, Any]:
    username = clean_username(payload.get("username"))
    password = str(payload.get("password") or "")
    role = "admin" if str(payload.get("role") or "").strip().lower() == "admin" else "user"
    if not username:
        raise ValueError("用户名需要 2-32 位中文、字母、数字或 _.-")
    if not (8 <= len(password) <= 128):
        raise ValueError("密码至少 8 位")
    try:
        create_user(username, password, role=role)
    except sqlite3.IntegrityError:
        raise ValueError("用户名已存在") from None
    return {"ok": True, "users": admin_user_rows()}


def admin_delete_user(payload: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    user_id = int(float(payload.get("id") or payload.get("userId") or 0))
    if user_id <= 0:
        raise ValueError("用户不存在")
    if user_id == int(current.get("id") or 0):
        raise ValueError("不能删除当前登录的管理员")
    with AUTH_DB_LOCK, auth_db() as conn:
        row = conn.execute("SELECT id, role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        if row["role"] == "admin":
            admin_total = conn.execute("SELECT COUNT(*) AS count FROM users WHERE role = 'admin'").fetchone()
            if int(admin_total["count"] if admin_total else 0) <= 1:
                raise ValueError("至少保留一个管理员")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"ok": True, "users": admin_user_rows()}


def runtime_cache_stats() -> dict[str, Any]:
    json_files = []
    for pattern in ("api_*.json", "*.json"):
        json_files.extend(PERSIST_CACHE_DIR.glob(pattern))
    source_dir = PERSIST_CACHE_DIR / "source-cache"
    if source_dir.exists():
        json_files.extend(source_dir.glob("*.json"))
    unique_files = sorted({path for path in json_files if path.is_file()})
    total_bytes = 0
    latest = 0
    for path in unique_files:
        try:
            stat = path.stat()
        except OSError:
            continue
        total_bytes += int(stat.st_size)
        latest = max(latest, int(stat.st_mtime))
    return {"files": len(unique_files), "bytes": total_bytes, "updatedAt": latest}


def payload_source_overview(key: str) -> dict[str, Any]:
    payload = read_json_cache(api_cache_path(key))
    meta = payload.get("_cache") if isinstance(payload.get("_cache"), dict) else {}
    sources = []
    for source in payload.get("sources") if isinstance(payload.get("sources"), list) else []:
        if not isinstance(source, dict):
            continue
        rows = source.get("rows") if isinstance(source.get("rows"), list) else []
        sources.append(
            {
                "id": source.get("id") or "",
                "title": source.get("title") or source.get("id") or "",
                "group": source.get("group") or "",
                "status": source.get("status") or ("ok" if rows else "empty"),
                "rows": len(rows),
                "sourceName": source.get("sourceName") or "",
            }
        )
    if not sources:
        for list_key, title in (
            ("items", "信息流"),
            ("briefs", "简报"),
            ("sections", "分组"),
            ("events", "事件"),
        ):
            items = payload.get(list_key)
            if isinstance(items, list):
                total = 0
                if list_key == "sections":
                    for section in items:
                        if isinstance(section, dict) and isinstance(section.get("items"), list):
                            total += len(section.get("items") or [])
                    total = total or len(items)
                else:
                    total = len(items)
                sources.append(
                    {
                        "id": list_key,
                        "title": title,
                        "group": key,
                        "status": "ok" if total else "empty",
                        "rows": total,
                        "sourceName": f"{key} cache",
                    }
                )
                break
    return {
        "key": key,
        "updatedAt": int(meta.get("updatedAt") or payload.get("updatedAt") or 0),
        "stale": bool(meta.get("stale")),
        "sources": sources,
    }


def admin_summary_payload() -> dict[str, Any]:
    users = admin_user_rows()
    with AUTH_DB_LOCK, auth_db() as conn:
        session_count = conn.execute("SELECT COUNT(*) AS count FROM sessions WHERE expires_at > ?", (int(time.time()),)).fetchone()
        payload_count = conn.execute("SELECT COUNT(*) AS count FROM user_payloads").fetchone()
    try:
        wechat = wechat_account_status_payload(force_validate=False)
    except Exception as exc:
        wechat = {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "users": {
            "total": len(users),
            "admins": len([item for item in users if item.get("role") == "admin"]),
            "normal": len([item for item in users if item.get("role") != "admin"]),
            "activeSessions": int(session_count["count"] if session_count else 0),
            "payloads": int(payload_count["count"] if payload_count else 0),
        },
        "cache": runtime_cache_stats(),
        "wechat": wechat,
        "monitors": {
            "siteAlert": SITE_ALERT_MONITOR_ACTIVE,
            "siteAlertStartedAt": SITE_ALERT_MONITOR_STARTED_AT,
            "rankMonitorInterval": RANK_MONITOR_INTERVAL,
            "wechatAuthMonitor": WECHAT_AUTH_MONITOR_ACTIVE,
        },
        "sources": [
            payload_source_overview("market-hot"),
            payload_source_overview("gainers-rankings"),
            payload_source_overview("turnover-rankings"),
            payload_source_overview("listing-events-v2"),
            payload_source_overview("new-coin-rankings"),
            payload_source_overview("newsflash"),
            payload_source_overview("automation-briefs"),
        ],
        "updatedAt": int(time.time() * 1000),
    }


def admin_refresh_cache_payload() -> dict[str, Any]:
    refresh_jobs = (
        ("newsflash", fetch_blockbeats_flash),
        ("automation-briefs", automation_briefs_payload),
        ("listing-events-v2", listing_events_payload),
        ("new-coin-rankings", new_coin_rankings_payload),
        ("market-hot", market_payload),
        ("gainers-rankings", gainers_rankings_payload),
        ("turnover-rankings", turnover_rankings_payload),
    )
    for key, fetcher in refresh_jobs:
        trigger_api_refresh(key, fetcher)
    return {"ok": True, "queued": [key for key, _ in refresh_jobs], "updatedAt": int(time.time() * 1000)}


def admin_clear_cache_payload() -> dict[str, Any]:
    deleted = 0
    candidates = list(PERSIST_CACHE_DIR.glob("api_*.json"))
    source_dir = PERSIST_CACHE_DIR / "source-cache"
    if source_dir.exists():
        candidates.extend(source_dir.glob("*.json"))
    for path in candidates:
        try:
            if path.is_file():
                path.unlink()
                deleted += 1
        except OSError:
            continue
    with API_REFRESH_LOCK:
        API_REFRESHING.clear()
    return {"ok": True, "deleted": deleted, "cache": runtime_cache_stats()}


def api_cache_path(key: str) -> Path:
    safe_key = re.sub(r"[^a-z0-9_-]+", "-", key.lower()).strip("-") or "payload"
    return PERSIST_CACHE_DIR / f"api_{safe_key}.json"


def source_has_rows(source: dict[str, Any]) -> bool:
    return isinstance(source, dict) and isinstance(source.get("rows"), list) and bool(source.get("rows"))


def cached_source_copy(source: dict[str, Any], suffix: str) -> dict[str, Any]:
    if not source_has_rows(source):
        return {}
    copied = json.loads(json.dumps(source, ensure_ascii=False))
    source_name = str(copied.get("sourceName") or "").strip()
    copied["status"] = "ok"
    copied["sourceName"] = f"{source_name} · {suffix}" if source_name and suffix not in source_name else (source_name or suffix)
    copied["emptyTitle"] = ""
    copied["emptyMessage"] = ""
    return copied


def cached_source_fallback(source_id: str, source_cache_path: Path, *, api_key: str = "market-hot") -> dict[str, Any]:
    cached_source = cached_source_copy(read_json_cache(source_cache_path), "本地缓存")
    if cached_source:
        return cached_source
    payload = read_json_cache(api_cache_path(api_key))
    for source in payload.get("sources") if isinstance(payload.get("sources"), list) else []:
        if isinstance(source, dict) and source.get("id") == source_id:
            cached_source = cached_source_copy(source, "市场缓存")
            if cached_source:
                return cached_source
    return {}


def source_cache_path(source_id: str, api_key: str = "market-hot") -> Path:
    safe_api = re.sub(r"[^a-z0-9_-]+", "-", api_key.lower()).strip("-") or "api"
    safe_source = re.sub(r"[^a-z0-9_-]+", "-", source_id.lower()).strip("-") or "source"
    return PERSIST_CACHE_DIR / "source-cache" / f"{safe_api}_{safe_source}.json"


def cached_or_fallback_source(
    key: str,
    fetcher,
    *,
    api_key: str,
    fallback_group: str,
    error_title: str,
    error_subtitle: str,
    error_empty_title: str,
) -> dict[str, Any]:
    cache_path = source_cache_path(key, api_key)
    try:
        source = cached(key, fetcher)
        if source_has_rows(source):
            if key == "okx":
                rows = source.get("rows") if isinstance(source.get("rows"), list) else []
                fallback = cached_source_fallback(key, cache_path, api_key=api_key)
                fallback_rows = fallback.get("rows") if isinstance(fallback.get("rows"), list) else []
                if len(rows) < 10 and len(fallback_rows) > len(rows):
                    fallback["sourceName"] = f"{fallback.get('sourceName') or key} · 实时不足 {len(rows)} 条，保留完整缓存"
                    return fallback
            write_json_cache(cache_path, source)
            return source
        fallback = cached_source_fallback(key, cache_path, api_key=api_key)
        if fallback:
            fallback["sourceName"] = f"{fallback.get('sourceName') or key} · 实时返回为空"
            return fallback
        return source if isinstance(source, dict) else {}
    except Exception as exc:
        fallback = cached_source_fallback(key, cache_path, api_key=api_key)
        if fallback:
            fallback["sourceName"] = f"{fallback.get('sourceName') or key} · 实时失败：{alert_text(exc, 60)}"
            return fallback
        return source_template(
            id=key,
            group=fallback_group,
            title=error_title or key,
            subtitle=error_subtitle,
            accent="#777777",
            source_label="ERR",
            source_name=str(exc)[:120],
            rows=[],
            status="unavailable",
            empty_title=error_empty_title,
            empty_message=str(exc)[:180],
        )


def payload_cache_time(payload: dict[str, Any]) -> float:
    meta = payload.get("_cache") if isinstance(payload.get("_cache"), dict) else {}
    value = meta.get("updatedAt") or payload.get("updatedAt") or 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if number > 10_000_000_000:
        return number / 1000
    return number


def with_cache_meta(key: str, payload: dict[str, Any], *, stale: bool = False) -> dict[str, Any]:
    result = dict(payload)
    meta = result.get("_cache") if isinstance(result.get("_cache"), dict) else {}
    cache_updated_at = int(meta.get("updatedAt") or time.time() * 1000)
    result["_cache"] = {
        "key": key,
        "updatedAt": cache_updated_at,
        "stale": stale,
    }
    return result


def refresh_api_cache_now(key: str, fetcher) -> dict[str, Any]:
    payload = fetcher()
    if not isinstance(payload, dict):
        payload = {}
    payload = dict(payload)
    payload["_cache"] = {
        "key": key,
        "updatedAt": int(time.time() * 1000),
        "stale": False,
    }
    write_json_cache(api_cache_path(key), payload)
    return payload


def trigger_api_refresh(key: str, fetcher) -> None:
    with API_REFRESH_LOCK:
        if key in API_REFRESHING:
            return
        API_REFRESHING.add(key)

    def worker() -> None:
        try:
            time.sleep(0.2)
            refresh_api_cache_now(key, fetcher)
        except Exception:
            pass
        finally:
            with API_REFRESH_LOCK:
                API_REFRESHING.discard(key)

    threading.Thread(target=worker, daemon=True).start()


def cached_api_payload(key: str, fetcher, ttl_seconds: int = 60, *, force_refresh: bool = False) -> dict[str, Any]:
    cached_payload = read_json_cache(api_cache_path(key))
    now = time.time()
    if cached_payload:
        age = now - payload_cache_time(cached_payload)
        stale = age > ttl_seconds
        if force_refresh or stale:
            trigger_api_refresh(key, fetcher)
        return with_cache_meta(key, cached_payload, stale=stale)
    return refresh_api_cache_now(key, fetcher)


def clean_feed_text(value: Any, limit: int = 420) -> str:
    if value is None:
        return ""
    text = str(value)
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def feed_node_text(node: Any, *names: str) -> str:
    if node is None:
        return ""
    for name in names:
        found = node.find(name)
        if found is not None:
            return clean_feed_text(found.get_text(" ", strip=True), 1200)
    return ""


def parse_feed_datetime(value: Any) -> int:
    raw = str(value or "").strip()
    if not raw:
        return 0
    for parser in (
        lambda item: parsedate_to_datetime(item),
        lambda item: datetime.fromisoformat(item.replace("Z", "+00:00")),
    ):
        try:
            parsed = parser(raw)
            return int(parsed.timestamp() * 1000)
        except Exception:
            continue
    return 0


def stable_feed_id(*parts: Any) -> str:
    seed = "|".join(clean_feed_text(part, 800) for part in parts if part is not None)
    return hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:24]


def normalize_feed_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("RSS 地址必须是 http 或 https 链接")
    return url


def is_generic_wechat_title(value: Any) -> bool:
    text = clean_feed_text(value, 120).lower()
    return text in {"", "微信公众号", "微信公众账号", "订阅号", "wechat", "wechat official account"}


def is_wechat_platform_mp_id(value: Any, platform: Any = "") -> bool:
    text = clean_feed_text(value, 120)
    platform_text = clean_feed_text(platform, 80).lower()
    return bool(text.upper().startswith("MP_WXS_") or (platform_text == "wewe-platform" and re.fullmatch(r"[A-Fa-f0-9]{16,64}", text)))


def rss_source_identity(source: dict[str, Any]) -> str:
    source_type = clean_feed_text(source.get("type"), 20)
    if source_type == "wechat":
        return clean_feed_text(
            (source.get("mpId") if is_wechat_platform_mp_id(source.get("mpId"), source.get("platform")) else "")
            or source.get("seedUrl")
            or source.get("query")
            or source.get("feedUrl")
            or source.get("title"),
            600,
        )
    return clean_feed_text(source.get("feedUrl") or source.get("url") or source.get("title"), 600)


def normalize_rss_source(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    source_type = clean_feed_text(source.get("type") or "feed", 20).lower()
    if source_type in {"rss", "atom"}:
        source_type = "feed"
    if source_type not in {"feed", "wechat"}:
        source_type = "feed"

    title = clean_feed_text(source.get("title"), 120)
    feed_url = clean_feed_text(source.get("feedUrl") or source.get("url"), 900)
    seed_url = clean_feed_text(source.get("seedUrl"), 900)
    query = clean_feed_text(source.get("query"), 160)
    mp_id = clean_feed_text(source.get("mpId"), 120)
    site_url = clean_feed_text(source.get("siteUrl"), 900)
    cover = clean_feed_text(source.get("cover"), 900)
    platform = clean_feed_text(source.get("platform"), 80)
    etag = clean_feed_text(source.get("etag"), 300)
    last_modified = clean_feed_text(source.get("lastModified"), 300)

    if source_type == "wechat":
        if is_generic_wechat_title(title) and query and not is_generic_wechat_title(query):
            title = query
        if not query and title and not is_generic_wechat_title(title):
            query = title
        if mp_id and not is_wechat_platform_mp_id(mp_id, platform):
            mp_id = ""
        if not mp_id and is_generic_wechat_title(title) and is_generic_wechat_title(query):
            return {}

    if source_type == "feed":
        try:
            feed_url = normalize_feed_url(feed_url)
        except Exception:
            return {}
    else:
        if not (mp_id or seed_url or query or title):
            return {}
        if not feed_url:
            feed_url = f"wechat-mp://{mp_id or stable_feed_id('wechat', seed_url, query, title)}"
        if not mp_id:
            mp_id = ""

    source_id = clean_feed_text(source.get("id"), 120) or f"{source_type}-{stable_feed_id(source_type, feed_url, seed_url, query, title)}"
    created_at = int(safe_float(source.get("createdAt")) or int(time.time() * 1000))
    last_fetched_at = int(safe_float(source.get("lastFetchedAt")) or 0)
    last_new_count = int(safe_float(source.get("lastNewCount")) or 0)
    last_skipped = int(safe_float(source.get("lastSkippedExisting")) or 0)
    return {
        "id": source_id,
        "type": source_type,
        "title": title,
        "feedUrl": feed_url,
        "siteUrl": site_url,
        "description": clean_feed_text(source.get("description"), 240),
        "cover": cover,
        "seedUrl": seed_url,
        "query": query,
        "mpId": mp_id,
        "platform": platform,
        "etag": etag,
        "lastModified": last_modified,
        "lastFetchedAt": last_fetched_at,
        "lastNewCount": last_new_count,
        "lastSkippedExisting": last_skipped,
        "createdAt": created_at,
    }


def normalize_rss_sources_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_sources = payload.get("sources") if isinstance(payload, dict) else []
    if not isinstance(raw_sources, list):
        raw_sources = []
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_source in raw_sources[:300]:
        source = normalize_rss_source(raw_source)
        if not source:
            continue
        identity = rss_source_identity(source) or source["id"]
        if identity in seen:
            continue
        seen.add(identity)
        sources.append(source)
    return {"sources": sources, "updatedAt": int(time.time() * 1000)}


def normalize_rss_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    source_id = clean_feed_text(item.get("sourceId"), 120)
    source_type = clean_feed_text(item.get("sourceType") or "feed", 20).lower()
    if source_type not in {"feed", "wechat"}:
        source_type = "feed"
    title = clean_feed_text(item.get("title"), 220)
    url = clean_feed_text(item.get("url"), 1000)
    item_id = clean_feed_text(item.get("id"), 220)
    if not source_id or not (title or url or item_id):
        return {}
    published_at = int(safe_float(item.get("publishedAt")) or 0)
    fetched_at = int(safe_float(item.get("fetchedAt")) or int(time.time() * 1000))
    key = clean_feed_text(item.get("key"), 260) or stable_feed_id("rss-item", source_id, item_id, url, title)
    return {
        "key": key,
        "id": item_id or key,
        "title": title or url or item_id,
        "url": url,
        "publishedAt": published_at,
        "author": clean_feed_text(item.get("author"), 120),
        "summary": clean_feed_text(item.get("summary"), 520),
        "image": clean_feed_text(item.get("image"), 1000),
        "sourceId": source_id,
        "sourceType": source_type,
        "sourceTitle": clean_feed_text(item.get("sourceTitle"), 160),
        "fetchedAt": fetched_at,
    }


def normalize_rss_items_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_items = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(raw_items, list):
        raw_items = []
    cutoff = int(time.time() * 1000) - RSS_RETENTION_MS
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        item = normalize_rss_item(raw_item)
        if not item:
            continue
        published_at = int(safe_float(item.get("publishedAt")) or 0)
        if published_at and published_at < cutoff:
            continue
        dedupe_key = clean_feed_text(item.get("url") or item.get("id") or item.get("title"), 1000)
        combined_key = f"{item['sourceId']}|{dedupe_key}"
        if combined_key in seen:
            continue
        seen.add(combined_key)
        rows.append(item)
    rows.sort(key=lambda value: int(safe_float(value.get("publishedAt")) or safe_float(value.get("fetchedAt")) or 0), reverse=True)
    return {"items": rows[:RSS_MAX_STORED_ITEMS], "updatedAt": int(time.time() * 1000)}


def recover_rss_sources_from_alert_history() -> list[dict[str, Any]]:
    history_path = PERSIST_CACHE_DIR / "desktop_alert_seen.json"
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    seen = data.get("seen") if isinstance(data, dict) else {}
    if not isinstance(seen, dict):
        return []
    ignored_sources = {
        "blockbeats",
        "okx",
        "okx public instruments",
        "binance",
        "bitget",
        "aicoin",
        "futu",
        "futunn",
        "ths",
    }
    latest_by_source: dict[str, tuple[float, str, str]] = {}
    for raw_key, raw_time in seen.items():
        key = str(raw_key or "")
        if not key.startswith("alert-title-url:"):
            continue
        rest = key.removeprefix("alert-title-url:")
        if "|" not in rest:
            continue
        source = clean_feed_text(rest.split("|", 1)[0], 120)
        source_key = source.lower()
        match = re.search(r"https?://mp\.weixin\.qq\.com/s/[^\s|]+", rest)
        url = clean_feed_text(match.group(0) if match else "", 900)
        if not source or source_key in ignored_sources or is_generic_wechat_title(source) or re.fullmatch(r"[\W_?]+", source):
            continue
        if not url:
            continue
        timestamp = safe_float(raw_time)
        previous = latest_by_source.get(source_key)
        if previous and previous[0] >= timestamp:
            continue
        latest_by_source[source_key] = (timestamp, url, source)

    sources: list[dict[str, Any]] = []
    for _, (_, seed_url, source_title) in sorted(latest_by_source.items(), key=lambda item: item[1][0], reverse=True):
        title = clean_feed_text(source_title, 120)
        recovered = normalize_rss_source(
            {
                "type": "wechat",
                "title": title,
                "seedUrl": seed_url,
                "query": title,
                "createdAt": int(time.time() * 1000),
            }
        )
        if recovered:
            sources.append(recovered)
        if len(sources) >= 120:
            break
    return sources


def wechat_seed_candidates_from_alert_history(source_title: str, limit: int = 12) -> list[str]:
    title_key = clean_feed_text(source_title, 120).lower()
    if not title_key:
        return []
    history_path = PERSIST_CACHE_DIR / "desktop_alert_seen.json"
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    seen = data.get("seen") if isinstance(data, dict) else {}
    if not isinstance(seen, dict):
        return []
    candidates: list[tuple[float, str]] = []
    for raw_key, raw_time in seen.items():
        key = str(raw_key or "")
        if not key.startswith("alert-title-url:"):
            continue
        rest = key.removeprefix("alert-title-url:")
        parts = rest.split("|")
        if len(parts) < 3:
            continue
        source = clean_feed_text(parts[0], 120).lower()
        if source != title_key:
            continue
        match = re.search(r"https?://mp\.weixin\.qq\.com/[^\s|]+", rest)
        if not match:
            continue
        url = clean_feed_text(match.group(0), 900)
        if url:
            candidates.append((safe_float(raw_time), url))
    urls: list[str] = []
    for _, url in sorted(candidates, key=lambda item: item[0], reverse=True):
        if url not in urls:
            urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def wechat_history_items_for_source(source_title: str, cutoff_ms: int = 0, limit: int = 80) -> list[dict[str, Any]]:
    title_key = clean_feed_text(source_title, 120).lower()
    if not title_key:
        return []
    history_path = PERSIST_CACHE_DIR / "desktop_alert_seen.json"
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    seen = data.get("seen") if isinstance(data, dict) else {}
    if not isinstance(seen, dict):
        return []
    summaries: dict[tuple[str, str], str] = {}
    rows: list[tuple[float, dict[str, Any]]] = []
    for raw_key, raw_time in seen.items():
        key = str(raw_key or "")
        if key.startswith("alert-body:"):
            parts = key.removeprefix("alert-body:").split("|", 2)
            if len(parts) >= 3 and clean_feed_text(parts[0], 120).lower() == title_key:
                summaries[(parts[0], parts[1])] = clean_feed_text(parts[2], 420)
            continue
        if not key.startswith("alert-title-url:"):
            continue
        rest = key.removeprefix("alert-title-url:")
        parts = rest.split("|", 2)
        if len(parts) < 3:
            continue
        source = clean_feed_text(parts[0], 120)
        if source.lower() != title_key:
            continue
        article_title = clean_feed_text(parts[1], 180)
        match = re.search(r"https?://mp\.weixin\.qq\.com/[^\s|]+", parts[2])
        if not article_title or not match:
            continue
        timestamp_ms = int(safe_float(raw_time) * 1000)
        if cutoff_ms and timestamp_ms and timestamp_ms < cutoff_ms:
            continue
        url = clean_feed_text(match.group(0), 900)
        rows.append(
            (
                timestamp_ms,
                {
                    "id": stable_feed_id("wechat-history", source, article_title, url),
                    "title": article_title,
                    "url": url,
                    "publishedAt": timestamp_ms,
                    "author": source,
                    "summary": summaries.get((source, article_title), ""),
                    "image": "",
                },
            )
        )
    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for _, item in sorted(rows, key=lambda row: row[0], reverse=True):
        key = clean_feed_text(item.get("url") or item.get("title"), 900)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        items.append(item)
        if len(items) >= limit:
            break
    return items


def rss_sources_payload(user: dict[str, Any] | None) -> dict[str, Any]:
    owner = user
    exists = user_payload_exists(owner, USER_SCOPE_RSS_SOURCES)
    payload = normalize_rss_sources_payload(load_user_payload(owner, USER_SCOPE_RSS_SOURCES))
    if not payload["sources"]:
        shared_owner = admin_user()
        if shared_owner and (not owner or int(safe_float(shared_owner.get("id"))) != int(safe_float(owner.get("id")))):
            shared_payload = normalize_rss_sources_payload(load_user_payload(shared_owner, USER_SCOPE_RSS_SOURCES))
            if shared_payload["sources"]:
                owner = shared_owner
                exists = True
                payload = shared_payload
    if owner and is_admin(owner) and not payload["sources"]:
        recovered_sources = recover_rss_sources_from_alert_history()
        if recovered_sources:
            payload = normalize_rss_sources_payload({"sources": recovered_sources})
            save_user_payload(owner, USER_SCOPE_RSS_SOURCES, payload)
            exists = True
    return {
        "ok": True,
        "authenticated": bool(user),
        "shared": bool(owner and user and int(safe_float(owner.get("id"))) != int(safe_float(user.get("id")))),
        "exists": exists,
        "sources": payload["sources"],
        "updatedAt": payload.get("updatedAt") or 0,
    }


def save_rss_sources_payload(payload: dict[str, Any], user: dict[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_rss_sources_payload(payload)
    owner = user or admin_user()
    if owner:
        save_user_payload(owner, USER_SCOPE_RSS_SOURCES, normalized)
    return {
        "ok": True,
        "authenticated": bool(user),
        "saved": bool(owner),
        "sources": normalized["sources"],
        "updatedAt": normalized["updatedAt"],
    }


def synthesize_rss_items_from_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        if source.get("type") != "wechat":
            continue
        title = clean_feed_text(source.get("title") or source.get("query"), 160)
        if not title:
            continue
        for item in wechat_history_items_for_source(title, limit=80):
            merged = {
                **item,
                "sourceId": source.get("id"),
                "sourceType": "wechat",
                "sourceTitle": title,
                "fetchedAt": int(time.time() * 1000),
            }
            merged["key"] = stable_feed_id("rss-item", merged["sourceId"], merged.get("id"), merged.get("url"), merged.get("title"))
            rows.append(merged)
    return normalize_rss_items_payload({"items": rows})["items"]


def rss_items_payload(user: dict[str, Any] | None) -> dict[str, Any]:
    owner = user
    exists = user_payload_exists(owner, USER_SCOPE_RSS_ITEMS)
    payload = normalize_rss_items_payload(load_user_payload(owner, USER_SCOPE_RSS_ITEMS))
    if not payload["items"]:
        shared_owner = admin_user()
        if shared_owner and (not owner or int(safe_float(shared_owner.get("id"))) != int(safe_float(owner.get("id")))):
            shared_payload = normalize_rss_items_payload(load_user_payload(shared_owner, USER_SCOPE_RSS_ITEMS))
            if shared_payload["items"]:
                owner = shared_owner
                exists = True
                payload = shared_payload
    if not owner:
        owner = admin_user()
    if owner and not payload["items"]:
        sources_payload = rss_sources_payload(owner)
        recovered_items = synthesize_rss_items_from_sources(sources_payload.get("sources") or [])
        if recovered_items:
            payload = normalize_rss_items_payload({"items": recovered_items})
            save_user_payload(owner, USER_SCOPE_RSS_ITEMS, payload)
            exists = True
    return {
        "ok": True,
        "authenticated": bool(user),
        "shared": bool(owner and user and int(safe_float(owner.get("id"))) != int(safe_float(user.get("id")))),
        "exists": exists,
        "items": payload["items"],
        "updatedAt": payload.get("updatedAt") or 0,
    }


def save_rss_items_payload(payload: dict[str, Any], user: dict[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_rss_items_payload(payload)
    owner = user or admin_user()
    if owner:
        save_user_payload(owner, USER_SCOPE_RSS_ITEMS, normalized)
    return {
        "ok": True,
        "authenticated": bool(user),
        "saved": bool(owner),
        "items": normalized["items"],
        "updatedAt": normalized["updatedAt"],
    }


def rss_source_items_for_checkpoint(source_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if clean_feed_text(item.get("sourceId"), 120) == source_id
    ]


def rss_source_checkpoint(source_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    source_items = sorted(
        rss_source_items_for_checkpoint(source_id, items),
        key=lambda item: int(safe_float(item.get("publishedAt")) or safe_float(item.get("fetchedAt")) or 0),
        reverse=True,
    )
    known_ids: list[str] = []
    seen: set[str] = set()
    since = 0
    for item in source_items:
        since = max(since, int(safe_float(item.get("publishedAt")) or 0))
        for value in (item.get("id"), item.get("url"), item.get("title")):
            key = clean_feed_text(value, 260)
            if not key or key in seen:
                continue
            seen.add(key)
            known_ids.append(key)
            if len(known_ids) >= 500:
                break
        if len(known_ids) >= 500:
            break
    return {
        "knownIds": known_ids,
        "since": since,
        "backfill": not bool(source_items),
        "fullSync": not bool(source_items),
    }


def rss_source_from_fetch_result(source: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    feed = payload.get("feed") if isinstance(payload.get("feed"), dict) else {}
    updated = {**source}
    feed_title = clean_feed_text(feed.get("title"), 160)
    if source.get("type") == "wechat":
        if feed_title and not is_generic_wechat_title(feed_title):
            updated["title"] = feed_title
            updated["query"] = updated.get("query") or feed_title
    elif feed_title:
        updated["title"] = feed_title
    for target, field, limit in (
        ("siteUrl", "siteUrl", 900),
        ("description", "description", 240),
        ("cover", "cover", 900),
        ("feedUrl", "feedUrl", 900),
    ):
        value = clean_feed_text(feed.get(field), limit)
        if value:
            updated[target] = value
    feed_id = clean_feed_text(feed.get("id"), 120)
    platform = clean_feed_text(feed.get("platform") or updated.get("platform"), 80)
    if platform:
        updated["platform"] = platform
    if updated.get("type") == "wechat":
        if is_wechat_platform_mp_id(feed_id, platform):
            updated["mpId"] = feed_id
            updated["platform"] = "wewe-platform"
            updated["feedUrl"] = f"wechat-mp://{feed_id}"
        elif not is_wechat_platform_mp_id(updated.get("mpId"), updated.get("platform")):
            updated["mpId"] = ""
    elif feed_id:
        updated["mpId"] = feed_id
    if payload.get("etag"):
        updated["etag"] = clean_feed_text(payload.get("etag"), 300)
    if payload.get("lastModified"):
        updated["lastModified"] = clean_feed_text(payload.get("lastModified"), 300)
    updated["lastFetchedAt"] = int(safe_float(payload.get("fetchedAt")) or int(time.time() * 1000))
    updated["lastNewCount"] = len(payload.get("items") or [])
    updated["lastSkippedExisting"] = int(safe_float(payload.get("skippedExisting")) or 0)
    if payload.get("warning") and not payload.get("items"):
        updated["lastMessage"] = clean_feed_text(payload.get("warning"), 240)
    elif payload.get("notice") and not payload.get("items"):
        updated["lastMessage"] = clean_feed_text(payload.get("notice"), 240)
    else:
        updated["lastMessage"] = ""
    return normalize_rss_source(updated)


def rss_merge_fetched_items(
    current_items: list[dict[str, Any]],
    fetched_items: list[dict[str, Any]],
    source: dict[str, Any],
    fetched_at: int,
) -> list[dict[str, Any]]:
    rows = [*current_items]
    source_id = clean_feed_text(source.get("id"), 120)
    source_type = clean_feed_text(source.get("type") or "feed", 20)
    source_title = clean_feed_text(source.get("title"), 160)
    for item in fetched_items:
        if not isinstance(item, dict):
            continue
        merged = {
            **item,
            "sourceId": source_id,
            "sourceType": source_type,
            "sourceTitle": source_title or clean_feed_text(item.get("sourceTitle"), 160),
            "fetchedAt": fetched_at or int(time.time() * 1000),
        }
        merged["key"] = stable_feed_id("rss-item", source_id, merged.get("id"), merged.get("url"), merged.get("title"))
        rows.append(merged)
    return normalize_rss_items_payload({"items": rows})["items"]


def rss_refresh_one_source(source: dict[str, Any], current_items: list[dict[str, Any]], force_full_sync: bool = False) -> dict[str, Any]:
    source_id = clean_feed_text(source.get("id"), 120)
    checkpoint = rss_source_checkpoint(source_id, current_items)
    checkpoint["backfill"] = bool(force_full_sync or checkpoint["backfill"])
    checkpoint["fullSync"] = bool(force_full_sync or checkpoint["fullSync"])
    try:
        if source.get("type") == "wechat" and not is_legacy_wechat_source(source):
            payload = wechat_mp_fetch_payload(
                {
                    "source": source,
                    **checkpoint,
                    "allowHistoryFallback": False,
                }
            )
        else:
            payload = rss_fetch_payload(
                {
                    "url": source.get("feedUrl") or source.get("url"),
                    "etag": source.get("etag"),
                    "lastModified": source.get("lastModified"),
                    **checkpoint,
                }
            )
        updated_source = rss_source_from_fetch_result(source, payload)
        return {
            "ok": True,
            "source": updated_source or source,
            "items": payload.get("items") or [],
            "fetchedAt": int(safe_float(payload.get("fetchedAt")) or int(time.time() * 1000)),
            "warning": clean_feed_text(payload.get("warning") or payload.get("notice"), 240),
            "platformItems": int(safe_float(payload.get("platformItems")) or 0),
            "skippedExisting": int(safe_float(payload.get("skippedExisting")) or 0),
        }
    except Exception as exc:
        failed_source = normalize_rss_source({**source, "lastFetchedAt": int(time.time() * 1000), "lastMessage": str(exc)})
        return {
            "ok": False,
            "source": failed_source or source,
            "items": [],
            "fetchedAt": int(time.time() * 1000),
            "warning": clean_feed_text(str(exc), 240),
            "platformItems": 0,
            "skippedExisting": 0,
        }


def is_legacy_wechat_source(source: dict[str, Any]) -> bool:
    return (
        source.get("type") == "wechat"
        and bool(re.match(r"^https?://", clean_feed_text(source.get("feedUrl"), 900), re.I))
        and not source.get("mpId")
        and not source.get("seedUrl")
        and not source.get("query")
    )


def rss_refresh_all_payload(payload: dict[str, Any], user: dict[str, Any] | None) -> dict[str, Any]:
    owner = user or admin_user()
    sources_payload = rss_sources_payload(owner)
    sources = sources_payload.get("sources") or []
    items_payload = rss_items_payload(owner)
    current_items = items_payload.get("items") or []
    has_wechat_sources = any((source.get("type") == "wechat") for source in sources if isinstance(source, dict))
    auth_required = bool(has_wechat_sources and not wechat_platform_account())
    if not sources:
        return {
            "ok": True,
            "sources": [],
            "items": current_items,
            "updatedAt": int(time.time() * 1000),
            "stats": {"sources": 0, "newItems": 0, "errors": 0},
        }
    force_full_sync = bool(payload.get("fullSync"))
    max_workers = max(1, min(3, int(safe_float(payload.get("workers"), 3)) or 3, len(sources)))
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(rss_refresh_one_source, source, current_items, force_full_sync) for source in sources]
        for future in as_completed(futures):
            results.append(future.result())

    source_by_id = {clean_feed_text(source.get("id"), 120): source for source in sources}
    for result in results:
        source = result.get("source") if isinstance(result.get("source"), dict) else {}
        source_id = clean_feed_text(source.get("id"), 120)
        if source_id:
            source_by_id[source_id] = source
        current_items = rss_merge_fetched_items(current_items, result.get("items") or [], source or source_by_id.get(source_id, {}), int(safe_float(result.get("fetchedAt")) or 0))

    normalized_sources = normalize_rss_sources_payload({"sources": list(source_by_id.values())})
    normalized_items = normalize_rss_items_payload({"items": current_items})
    if owner:
        save_user_payload(owner, USER_SCOPE_RSS_SOURCES, normalized_sources)
        save_user_payload(owner, USER_SCOPE_RSS_ITEMS, normalized_items)
    return {
        "ok": True,
        "sources": normalized_sources["sources"],
        "items": normalized_items["items"],
        "updatedAt": int(time.time() * 1000),
        "stats": {
            "sources": len(sources),
            "newItems": sum(len(result.get("items") or []) for result in results),
            "errors": sum(1 for result in results if not result.get("ok")),
            "platformItems": sum(int(safe_float(result.get("platformItems")) or 0) for result in results),
            "authRequired": auth_required,
        },
        "warnings": [
            {
                "source": clean_feed_text((result.get("source") or {}).get("title"), 160),
                "message": result.get("warning"),
            }
            for result in results
            if result.get("warning") and not result.get("items")
        ][:20],
    }


def parse_json_feed(payload: dict[str, Any], feed_url: str) -> dict[str, Any]:
    feed = {
        "title": clean_feed_text(payload.get("title") or payload.get("home_page_url") or feed_url, 120),
        "siteUrl": str(payload.get("home_page_url") or payload.get("url") or ""),
        "feedUrl": feed_url,
        "description": clean_feed_text(payload.get("description"), 220),
    }
    items = []
    for entry in payload.get("items") or []:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or entry.get("external_url") or "").strip()
        title = clean_feed_text(entry.get("title") or entry.get("summary") or url, 180)
        published = parse_feed_datetime(entry.get("date_published") or entry.get("date_modified"))
        item_id = str(entry.get("id") or stable_feed_id(feed_url, url, title, published))
        items.append(
            {
                "id": item_id,
                "title": title,
                "url": url,
                "publishedAt": published,
                "author": clean_feed_text((entry.get("author") or {}).get("name") if isinstance(entry.get("author"), dict) else entry.get("author"), 80),
                "summary": clean_feed_text(entry.get("summary") or entry.get("content_text") or entry.get("content_html"), 420),
            }
        )
    return {"feed": feed, "items": items}


def atom_entry_link(entry: Any, base_url: str) -> str:
    link_node = entry.find("link") if entry is not None else None
    if link_node is None:
        return ""
    if link_node.get("href"):
        return urljoin(base_url, link_node.get("href"))
    return urljoin(base_url, clean_feed_text(link_node.get_text(" ", strip=True), 600))


def parse_xml_feed(content: bytes, feed_url: str) -> dict[str, Any]:
    try:
        soup = BeautifulSoup(content, "xml")
    except Exception:
        soup = BeautifulSoup(content, "html.parser")

    channel = soup.find("channel") or soup.find("feed") or soup
    title = feed_node_text(channel, "title") or feed_url
    site_url = atom_entry_link(channel, feed_url) or feed_node_text(channel, "link")
    feed = {
        "title": clean_feed_text(title, 120),
        "siteUrl": urljoin(feed_url, site_url) if site_url else "",
        "feedUrl": feed_url,
        "description": feed_node_text(channel, "description", "subtitle"),
    }

    entries = soup.find_all("item") or soup.find_all("entry")
    items = []
    for entry in entries[:120]:
        title = feed_node_text(entry, "title")
        link = atom_entry_link(entry, feed_url) or feed_node_text(entry, "link")
        guid = feed_node_text(entry, "guid", "id")
        published = parse_feed_datetime(
            feed_node_text(entry, "pubDate", "published", "updated", "dc:date", "date")
        )
        summary = feed_node_text(entry, "description", "summary", "content:encoded", "content")
        author = feed_node_text(entry, "author", "dc:creator", "creator")
        if not title and not link:
            continue
        items.append(
            {
                "id": guid or stable_feed_id(feed_url, link, title, published),
                "title": clean_feed_text(title or link, 180),
                "url": urljoin(feed_url, link) if link else "",
                "publishedAt": published,
                "author": clean_feed_text(author, 80),
                "summary": clean_feed_text(summary, 420),
            }
        )
    return {"feed": feed, "items": items}


def rss_fetch_payload(payload: dict[str, Any]) -> dict[str, Any]:
    feed_url = normalize_feed_url(payload.get("url"))
    headers = {
        **HEADERS,
        "Accept": "application/rss+xml, application/atom+xml, application/feed+json, application/json, text/xml, */*",
        "Cache-Control": "no-cache",
    }
    etag = clean_feed_text(payload.get("etag"), 300)
    last_modified = clean_feed_text(payload.get("lastModified"), 300)
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    response = requests.get(feed_url, headers=headers, timeout=20)
    fetched_at = int(time.time() * 1000)
    if response.status_code == 304:
        return {
            "ok": True,
            "feed": {"title": "", "siteUrl": "", "feedUrl": feed_url, "description": ""},
            "items": [],
            "notModified": True,
            "skippedExisting": 0,
            "incremental": True,
            "fetchedAt": fetched_at,
        }
    response.raise_for_status()
    content = response.content[: RSS_FETCH_MAX_BYTES + 1]
    if len(content) > RSS_FETCH_MAX_BYTES:
        raise ValueError("RSS 响应过大，已停止解析")

    content_type = response.headers.get("Content-Type", "").lower()
    stripped = content.lstrip()
    if "json" in content_type or stripped.startswith(b"{"):
        parsed = parse_json_feed(response.json(), feed_url)
    else:
        parsed = parse_xml_feed(content, feed_url)

    items = []
    for item in parsed["items"]:
        item["sourceTitle"] = parsed["feed"]["title"]
        items.append(item)
    items.sort(key=lambda item: item.get("publishedAt") or 0, reverse=True)
    items = recent_feed_items(items)
    items, skipped_existing = incremental_items(items, payload)
    return {
        "ok": True,
        "feed": parsed["feed"],
        "items": items,
        "skippedExisting": skipped_existing,
        "incremental": bool(payload.get("knownIds") or payload.get("since")),
        "etag": response.headers.get("ETag") or "",
        "lastModified": response.headers.get("Last-Modified") or "",
        "fetchedAt": fetched_at,
    }


def item_incremental_keys(item: dict[str, Any]) -> set[str]:
    keys = set()
    for field in ("id", "url", "title"):
        value = clean_feed_text(item.get(field), 260)
        if value:
            keys.add(value)
    return keys


def recent_feed_items(items: list[dict[str, Any]], cutoff_ms: int | None = None) -> list[dict[str, Any]]:
    cutoff = cutoff_ms or int(time.time() * 1000) - RSS_RETENTION_MS
    recent = []
    for item in items:
        published = int(safe_float(item.get("publishedAt")))
        if published and published < cutoff:
            continue
        recent.append(item)
    return recent


def incremental_items(items: list[dict[str, Any]], payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    raw_known = payload.get("knownIds") if isinstance(payload.get("knownIds"), list) else []
    known = {
        clean_feed_text(value, 260)
        for value in raw_known[:500]
        if clean_feed_text(value, 260)
    }
    since = int(safe_float(payload.get("since")))
    if not known and since <= 0:
        return items, 0

    fresh = []
    skipped = 0
    cutoff = int(time.time() * 1000) - RSS_RETENTION_MS
    for item in items:
        keys = item_incremental_keys(item)
        published = int(safe_float(item.get("publishedAt")))
        if published and published < cutoff:
            skipped += 1
            continue
        if keys and known.intersection(keys):
            skipped += 1
            continue
        if not keys and since > 0 and published > 0 and published <= since:
            skipped += 1
            continue
        fresh.append(item)
    return fresh, skipped


def normalize_x_handle(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        parts = [part for part in parsed.path.split("/") if part]
        raw = parts[0] if parts else ""
    raw = raw.lstrip("@").strip()
    raw = re.sub(r"[^A-Za-z0-9_]", "", raw)
    return raw[:15]


def x_kol_source_id(handle: str) -> str:
    return f"x:{normalize_x_handle(handle).lower()}"


def user_runtime_dir(user: dict[str, Any] | None) -> Path:
    if not user:
        return PERSIST_CACHE_DIR
    user_id = int(safe_float(user.get("id")) or 0)
    if user_id <= 0:
        return PERSIST_CACHE_DIR
    path = PERSIST_CACHE_DIR / "users" / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def x_kol_sources_path(user: dict[str, Any] | None = None) -> Path:
    return user_runtime_dir(user) / "x_kol_sources.json" if user else X_KOL_SOURCES_PATH


def migrate_admin_x_kol_sources(user: dict[str, Any] | None, path: Path) -> None:
    if not user or user.get("role") != "admin" or path.exists() or not X_KOL_SOURCES_PATH.exists():
        return
    payload = read_json_cache(X_KOL_SOURCES_PATH)
    if isinstance(payload.get("sources"), list):
        write_json_cache(path, payload)


def migrate_x_kol_sources_to_db(user: dict[str, Any] | None) -> None:
    if not user or user_payload_exists(user, USER_SCOPE_X_KOL_SOURCES):
        return
    candidates: list[Path] = []
    user_path = x_kol_sources_path(user)
    if user_path.exists():
        candidates.append(user_path)
    if user.get("role") == "admin" and X_KOL_SOURCES_PATH.exists() and X_KOL_SOURCES_PATH not in candidates:
        candidates.append(X_KOL_SOURCES_PATH)
    for path in candidates:
        payload = read_json_cache(path)
        if isinstance(payload.get("sources"), list):
            save_user_payload(user, USER_SCOPE_X_KOL_SOURCES, payload)
            return


def x_kol_avatar_url(handle: str, value: Any = "") -> str:
    raw = str(value or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    normalized = normalize_x_handle(handle)
    if not normalized:
        return ""
    return f"https://unavatar.io/x/{quote(normalized, safe='')}"


def x_kol_token() -> str:
    return os.getenv("X_BEARER_TOKEN") or os.getenv("TWITTER_BEARER_TOKEN") or ""


def x_kol_fetch_limit() -> int:
    value = int(safe_float(os.getenv("X_KOL_FETCH_LIMIT"), X_KOL_FETCH_LIMIT) or X_KOL_FETCH_LIMIT)
    return max(10, min(value, 240))


def x_kol_page_limit() -> int:
    value = int(safe_float(os.getenv("X_KOL_PAGE_LIMIT"), 3) or 3)
    return max(1, min(value, 6))


def x_kol_include_replies() -> bool:
    return str(os.getenv("X_KOL_INCLUDE_REPLIES", "1")).strip().lower() not in {"0", "false", "no"}


def x_kol_include_retweets() -> bool:
    return str(os.getenv("X_KOL_INCLUDE_RETWEETS", "1")).strip().lower() not in {"0", "false", "no"}


def x_kol_strict_keywords() -> bool:
    return str(os.getenv("X_KOL_STRICT_KEYWORDS", "0")).strip().lower() in {"1", "true", "yes"}


def x_rss_templates() -> list[str]:
    raw = os.getenv("X_RSS_BASES") or os.getenv("TWITTER_RSS_BASES") or ""
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if values:
        return values
    return [
        "https://rsshub.app/twitter/user/{handle}",
        "https://nitter.net/{handle}/rss",
        "https://nitter.poast.org/{handle}/rss",
        "https://nitter.privacydev.net/{handle}/rss",
    ]


def normalize_x_keywords(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_parts = value
    else:
        raw_parts = re.split(r"[,，\s]+", str(value or ""))
    keywords = []
    seen = set()
    for part in raw_parts:
        word = clean_feed_text(part, 40).lower()
        if not word or word in seen:
            continue
        seen.add(word)
        keywords.append(word)
    return keywords[:16]


def x_kol_item_matches(source: dict[str, Any], text: str) -> bool:
    if not x_kol_strict_keywords():
        return True
    keywords = normalize_x_keywords(source.get("keywords"))
    if not keywords:
        return True
    haystack = text.lower()
    return any(word in haystack for word in keywords)


def x_kol_keyword_hits(source: dict[str, Any], text: str) -> list[str]:
    keywords = normalize_x_keywords(source.get("keywords"))
    if not keywords:
        return []
    haystack = text.lower()
    return [word for word in keywords if word in haystack]


def normalize_x_source(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    handle = normalize_x_handle(value.get("handle") or value.get("username") or value.get("url"))
    if not handle:
        return None
    display_name = clean_feed_text(value.get("displayName") or value.get("name") or handle, 80)
    source = {
        "id": x_kol_source_id(handle),
        "handle": handle,
        "displayName": display_name or handle,
        "keywords": normalize_x_keywords(value.get("keywords")),
        "enabled": value.get("enabled") is not False,
        "createdAt": int(safe_float(value.get("createdAt")) or time.time() * 1000),
    }
    avatar = x_kol_avatar_url(handle, value.get("avatar") or value.get("avatarUrl"))
    if avatar:
        source["avatar"] = avatar
    return source


def load_x_kol_sources(user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    target_user = user or admin_user()
    if target_user:
        migrate_x_kol_sources_to_db(target_user)
        payload = load_user_payload(target_user, USER_SCOPE_X_KOL_SOURCES)
    else:
        payload = read_json_cache(X_KOL_SOURCES_PATH)
    raw_sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    sources: list[dict[str, Any]] = []
    seen = set()
    for raw in raw_sources:
        source = normalize_x_source(raw)
        if not source or source["id"] in seen:
            continue
        seen.add(source["id"])
        sources.append(source)
    return sources[:80]


def x_kol_sources_payload(user: dict[str, Any] | None = None) -> dict[str, Any]:
    sources = load_x_kol_sources(user)
    return {
        "ok": True,
        "exists": user_payload_exists(user, USER_SCOPE_X_KOL_SOURCES) if user else bool(sources),
        "sources": sources,
        "sourceCount": len(sources),
        "enabledCount": sum(1 for source in sources if source.get("enabled") is not False),
        "hasToken": bool(x_kol_token()),
        "rssFallback": x_rss_templates(),
        "updatedAt": int(time.time() * 1000),
    }


def save_x_kol_sources_payload(payload: dict[str, Any], user: dict[str, Any] | None = None) -> dict[str, Any]:
    raw_sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    sources: list[dict[str, Any]] = []
    seen = set()
    for raw in raw_sources[:80]:
        source = normalize_x_source(raw)
        if not source or source["id"] in seen:
            continue
        seen.add(source["id"])
        sources.append(source)
    saved = {
        "sources": sources,
        "updatedAt": int(time.time() * 1000),
    }
    if user:
        save_user_payload(user, USER_SCOPE_X_KOL_SOURCES, saved)
    else:
        write_json_cache(X_KOL_SOURCES_PATH, saved)
    if user and user.get("role") == "admin":
        write_json_cache(X_KOL_SOURCES_PATH, saved)
    try:
        api_cache_path("x-kol-feed").unlink(missing_ok=True)
        api_cache_path("x-kol-feed-v2").unlink(missing_ok=True)
        api_cache_path("x-kol-feed-v3").unlink(missing_ok=True)
        api_cache_path("x-kol-feed-v4").unlink(missing_ok=True)
        for path in PERSIST_CACHE_DIR.glob("api_x-kol-feed-v4-u*.json"):
            path.unlink(missing_ok=True)
    except Exception:
        pass
    return {
        "ok": True,
        **saved,
        "sourceCount": len(sources),
        "enabledCount": sum(1 for source in sources if source.get("enabled") is not False),
        "hasToken": bool(x_kol_token()),
    }


def normalize_todo_project(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    project_id = clean_feed_text(value.get("id"), 80)
    name = clean_feed_text(value.get("name"), 120)
    if not project_id or not name:
        return None
    return {
        "id": project_id,
        "name": name,
        "createdAt": int(safe_float(value.get("createdAt")) or time.time() * 1000),
    }


def normalize_todo_task(value: Any, project_ids: set[str], fallback_project_id: str = "") -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    task_id = clean_feed_text(value.get("id"), 80)
    title = clean_feed_text(value.get("title"), 220)
    if not task_id or not title:
        return None
    project_id = clean_feed_text(value.get("projectId"), 80)
    if project_ids and project_id not in project_ids:
        project_id = fallback_project_id
    priority = clean_feed_text(value.get("priority"), 20)
    if priority not in {"high", "normal", "low"}:
        priority = "normal"
    task = {
        "id": task_id,
        "projectId": project_id,
        "title": title,
        "note": clean_feed_text(value.get("note"), 1000),
        "dueAt": int(safe_float(value.get("dueAt")) or 0),
        "priority": priority,
        "done": bool(value.get("done")),
        "createdAt": int(safe_float(value.get("createdAt")) or time.time() * 1000),
    }
    updated_at = int(safe_float(value.get("updatedAt")) or 0)
    if updated_at:
        task["updatedAt"] = updated_at
    return task


def normalize_todo_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_projects = payload.get("projects") if isinstance(payload.get("projects"), list) else []
    projects: list[dict[str, Any]] = []
    project_ids: set[str] = set()
    for raw in raw_projects[:100]:
        project = normalize_todo_project(raw)
        if not project or project["id"] in project_ids:
            continue
        project_ids.add(project["id"])
        projects.append(project)
    raw_tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
    tasks: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    for raw in raw_tasks[:2000]:
        task = normalize_todo_task(raw, project_ids, projects[0]["id"] if projects else "")
        if not task or task["id"] in task_ids:
            continue
        task_ids.add(task["id"])
        tasks.append(task)
    return {
        "projects": projects,
        "tasks": tasks,
        "updatedAt": int(safe_float(payload.get("updatedAt")) or time.time() * 1000),
    }


def todo_state_payload(user: dict[str, Any]) -> dict[str, Any]:
    exists = user_payload_exists(user, USER_SCOPE_TODO)
    payload = normalize_todo_payload(load_user_payload(user, USER_SCOPE_TODO))
    return {
        "ok": True,
        "storage": "sqlite",
        "exists": exists,
        "projects": payload["projects"],
        "tasks": payload["tasks"],
        "projectCount": len(payload["projects"]),
        "taskCount": len(payload["tasks"]),
        "empty": not payload["projects"] and not payload["tasks"],
        "updatedAt": payload.get("updatedAt") or int(time.time() * 1000),
    }


def save_todo_state_payload(payload: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    saved = normalize_todo_payload(payload)
    saved["updatedAt"] = int(time.time() * 1000)
    save_user_payload(user, USER_SCOPE_TODO, saved)
    return {
        "ok": True,
        "storage": "sqlite",
        "projects": saved["projects"],
        "tasks": saved["tasks"],
        "projectCount": len(saved["projects"]),
        "taskCount": len(saved["tasks"]),
        "updatedAt": saved["updatedAt"],
    }


def x_kol_tweet_url(handle: str, item_id: Any) -> str:
    tweet_id = clean_feed_text(item_id, 80)
    if not tweet_id:
        return f"https://x.com/{handle}"
    return f"https://x.com/{handle}/status/{tweet_id}"


def x_kol_normalize_url(value: Any, fallback_handle: str = "") -> str:
    raw = str(value or "").strip()
    if not raw:
        return f"https://x.com/{normalize_x_handle(fallback_handle)}" if normalize_x_handle(fallback_handle) else ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw
    host = parsed.netloc.lower()
    if any(name in host for name in ("nitter", "twitter.com", "x.com")):
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 3 and parts[1] == "status":
            return x_kol_tweet_url(normalize_x_handle(parts[0]), parts[2])
        if parts:
            handle = normalize_x_handle(parts[0])
            return f"https://x.com/{handle}" if handle else raw
    return raw


def x_kol_status_url_handle(value: Any) -> str:
    url = str(value or "").strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[1] == "status":
        return normalize_x_handle(parts[0])
    return ""


def x_kol_strip_quote_url(text: str) -> str:
    return clean_feed_text(re.sub(r"\s+https?://t\.co/[A-Za-z0-9_]+$", "", text or ""), 1200)


def x_kol_quote_from_text(value: str, *, fallback_handle: str = "", url: str = "", kind: str = "引用") -> dict[str, Any]:
    text = clean_feed_text(value, 1200)
    if not text:
        return {}
    author = ""
    handle = normalize_x_handle(fallback_handle)
    match = re.match(r"^(.{1,80}?)\s+\(@([A-Za-z0-9_]{1,15})\)\s+(.+)$", text, flags=re.S)
    if match:
        author = clean_feed_text(match.group(1), 80)
        handle = normalize_x_handle(match.group(2))
        text = clean_feed_text(match.group(3), 1200)
    elif handle:
        author = f"@{handle}"
    return {
        "kind": kind,
        "authorName": author,
        "handle": handle,
        "text": text,
        "url": x_kol_normalize_url(url, handle),
    }


def x_kol_quote_display(quote: dict[str, Any], fallback: str = "原作者") -> str:
    author = clean_feed_text(quote.get("authorName"), 80)
    handle = normalize_x_handle(quote.get("handle"))
    if author and not author.startswith("@"):
        return author
    return handle or author.lstrip("@") or fallback


def x_kol_split_rss_item(source: dict[str, Any], item: dict[str, Any], text: str) -> tuple[str, dict[str, Any]]:
    title = clean_feed_text(item.get("title"), 420)
    url = str(item.get("url") or "").strip()
    url_handle = x_kol_status_url_handle(url)
    source_handle = normalize_x_handle(source.get("handle"))
    retweet_match = re.match(r"^RT\s+by\s+@([A-Za-z0-9_]{1,15})\s*:\s*(.+)$", title, flags=re.I | re.S)
    if retweet_match:
        original_handle = url_handle if url_handle and url_handle.lower() != source_handle.lower() else ""
        quote = x_kol_quote_from_text(text, fallback_handle=original_handle, url=url, kind="转推")
        label = f"转推了 {x_kol_quote_display(quote)} 的动态" if quote else "转推了一条动态"
        return label, quote

    if title and text.startswith(title):
        rest = clean_feed_text(text[len(title) :], 1200)
        if len(rest) >= 18:
            return title, x_kol_quote_from_text(rest, fallback_handle=url_handle, url=url, kind="引用")

    marker = re.search(r"\s([^\s@][^()]{1,80})\s+\(@([A-Za-z0-9_]{1,15})\)\s+", text)
    if marker and marker.start() > 12:
        main_text = clean_feed_text(text[: marker.start()], 700)
        quote_text = clean_feed_text(text[marker.start() :], 1200)
        if main_text and quote_text:
            return main_text, x_kol_quote_from_text(quote_text, fallback_handle=url_handle, url=url, kind="引用")

    return clean_feed_text(title or text, 1200), {}


def x_kol_item_key(source: dict[str, Any], item_id: Any, text: Any, published_at: Any) -> str:
    return stable_feed_id("x-kol", source.get("id"), item_id, text, published_at)


def x_kol_fetch_api_source(source: dict[str, Any], token: str) -> dict[str, Any]:
    handle = source["handle"]
    fetch_limit = x_kol_fetch_limit()
    per_page = max(10, min(100, fetch_limit))
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    user_response = requests.get(
        f"https://api.twitter.com/2/users/by/username/{quote(handle)}",
        headers=headers,
        params={"user.fields": "description,profile_image_url,verified"},
        timeout=16,
    )
    user_response.raise_for_status()
    user_payload = user_response.json()
    user = user_payload.get("data") if isinstance(user_payload, dict) else {}
    if not isinstance(user, dict) or not user.get("id"):
        raise ValueError(f"{handle} 未返回用户信息")

    def build_quote(ref: dict[str, Any], tweet_map: dict[str, dict[str, Any]], user_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
        ref_tweet = tweet_map.get(str(ref.get("id") or ""))
        if not ref_tweet:
            return {}
        ref_author = user_map.get(str(ref_tweet.get("author_id") or "")) or {}
        ref_handle = normalize_x_handle(ref_author.get("username"))
        ref_id = ref_tweet.get("id")
        ref_text = clean_feed_text(ref_tweet.get("text"), 1200)
        if not ref_text:
            return {}
        return {
            "kind": {"quoted": "引用", "retweeted": "转推", "replied_to": "回复给"}.get(str(ref.get("type") or ""), "引用"),
            "authorName": ref_author.get("name") or (f"@{ref_handle}" if ref_handle else ""),
            "handle": ref_handle,
            "text": ref_text,
            "url": x_kol_tweet_url(ref_handle or handle, ref_id),
            "avatar": x_kol_avatar_url(ref_handle, ref_author.get("profile_image_url")),
            "publishedAt": parse_feed_datetime(ref_tweet.get("created_at")),
        }

    exclude = []
    if not x_kol_include_retweets():
        exclude.append("retweets")
    if not x_kol_include_replies():
        exclude.append("replies")
    rows = []

    pagination_token = ""
    for _ in range(x_kol_page_limit()):
        params = {
            "max_results": str(per_page),
            "tweet.fields": "author_id,created_at,public_metrics,entities,lang,referenced_tweets",
            "expansions": "referenced_tweets.id,referenced_tweets.id.author_id",
            "user.fields": "username,name,profile_image_url,verified",
        }
        if exclude:
            params["exclude"] = ",".join(exclude)
        if pagination_token:
            params["pagination_token"] = pagination_token
        tweets_response = requests.get(
            f"https://api.twitter.com/2/users/{user['id']}/tweets",
            headers=headers,
            params=params,
            timeout=18,
        )
        tweets_response.raise_for_status()
        tweet_payload = tweets_response.json()
        includes = tweet_payload.get("includes") if isinstance(tweet_payload.get("includes"), dict) else {}
        tweet_map = {
            str(item.get("id")): item
            for item in includes.get("tweets") or []
            if isinstance(item, dict) and item.get("id")
        }
        user_map = {
            str(item.get("id")): item
            for item in includes.get("users") or []
            if isinstance(item, dict) and item.get("id")
        }
        for tweet in tweet_payload.get("data") if isinstance(tweet_payload.get("data"), list) else []:
            if not isinstance(tweet, dict):
                continue
            text = clean_feed_text(tweet.get("text"), 1200)
            if not text or not x_kol_item_matches(source, text):
                continue
            published_at = parse_feed_datetime(tweet.get("created_at")) or int(time.time() * 1000)
            metrics = tweet.get("public_metrics") if isinstance(tweet.get("public_metrics"), dict) else {}
            tweet_id = tweet.get("id")
            ref_types = [
                str(ref.get("type") or "")
                for ref in tweet.get("referenced_tweets") or []
                if isinstance(ref, dict)
            ]
            quote_card = {}
            for ref in tweet.get("referenced_tweets") or []:
                if not isinstance(ref, dict):
                    continue
                quote_card = build_quote(ref, tweet_map, user_map)
                if quote_card:
                    break
            main_text = x_kol_strip_quote_url(text)
            if ref_types and ref_types[0] == "retweeted" and quote_card:
                main_text = f"转推了 {x_kol_quote_display(quote_card)} 的动态"
            rows.append(
                {
                    "id": x_kol_item_key(source, tweet_id, text, published_at),
                    "tweetId": tweet_id,
                    "text": main_text,
                    "fullText": text,
                    "quote": quote_card,
                    "title": clean_feed_text(main_text, 120),
                    "url": x_kol_tweet_url(handle, tweet_id),
                    "publishedAt": published_at,
                    "sourceId": source["id"],
                    "sourceName": user.get("name") or source.get("displayName") or handle,
                    "handle": handle,
                    "avatar": x_kol_avatar_url(handle, user.get("profile_image_url") or source.get("avatar")),
                    "metrics": {
                        "reply": int(safe_float(metrics.get("reply_count"))),
                        "repost": int(safe_float(metrics.get("retweet_count"))),
                        "like": int(safe_float(metrics.get("like_count"))),
                        "quote": int(safe_float(metrics.get("quote_count"))),
                        "view": int(safe_float(metrics.get("impression_count"))),
                    },
                    "matchedKeywords": x_kol_keyword_hits(source, text),
                    "entryType": ref_types[0] if ref_types else "tweet",
                    "provider": "x-api",
                }
            )
        if len(rows) >= fetch_limit:
            break
        meta = tweet_payload.get("meta") if isinstance(tweet_payload.get("meta"), dict) else {}
        pagination_token = str(meta.get("next_token") or "")
        if not pagination_token:
            break

    return {
        "source": {
            **source,
            "displayName": user.get("name") or source.get("displayName") or handle,
            "avatar": x_kol_avatar_url(handle, user.get("profile_image_url") or source.get("avatar")),
            "status": "ok",
            "provider": "x-api",
            "itemsReturned": min(len(rows), fetch_limit),
            "fetchLimit": fetch_limit,
            "includeReplies": x_kol_include_replies(),
            "includeRetweets": x_kol_include_retweets(),
        },
        "items": rows[:fetch_limit],
    }


def x_kol_fetch_rss_source(source: dict[str, Any]) -> dict[str, Any]:
    handle = source["handle"]
    fetch_limit = x_kol_fetch_limit()
    errors = []
    rows = []
    feed_urls = []
    for template in x_rss_templates():
        url = template.replace("{handle}", quote(handle, safe="")) if "{handle}" in template else f"{template.rstrip('/')}/{quote(handle, safe='')}"
        try:
            response = requests.get(
                url,
                headers={
                    **HEADERS,
                    "Accept": "application/rss+xml, application/atom+xml, text/xml, */*",
                },
                timeout=18,
            )
            response.raise_for_status()
            parsed = parse_xml_feed(response.content[: RSS_FETCH_MAX_BYTES + 1], url)
            for item in parsed.get("items") or []:
                text = clean_feed_text(item.get("summary") or item.get("title"), 1200)
                if not text or not x_kol_item_matches(source, text):
                    continue
                main_text, quote_card = x_kol_split_rss_item(source, item, text)
                published_at = int(safe_float(item.get("publishedAt")) or 0)
                rows.append(
                    {
                        "id": x_kol_item_key(source, item.get("id") or item.get("url"), text, published_at),
                        "tweetId": item.get("id") or "",
                        "text": main_text,
                        "fullText": text,
                        "quote": quote_card,
                        "title": clean_feed_text(main_text, 120),
                        "url": x_kol_normalize_url(item.get("url"), handle) or f"https://x.com/{handle}",
                        "publishedAt": published_at,
                        "sourceId": source["id"],
                        "sourceName": source.get("displayName") or parsed.get("feed", {}).get("title") or handle,
                        "handle": handle,
                        "avatar": source.get("avatar") or "",
                        "metrics": {},
                        "matchedKeywords": x_kol_keyword_hits(source, text),
                        "entryType": "rss",
                        "provider": "rss",
                    }
                )
            feed_urls.append(url)
        except Exception as exc:
            errors.append(str(exc))
            continue
    if rows:
        deduped = {}
        for row in rows:
            deduped[row["id"]] = row
        sorted_rows = sorted(deduped.values(), key=lambda row: int(safe_float(row.get("publishedAt"))), reverse=True)
        return {
            "source": {
                **source,
                "status": "ok",
                "provider": "rss",
                "feedUrl": feed_urls[0] if feed_urls else "",
                "feedUrls": feed_urls,
                "itemsReturned": min(len(sorted_rows), fetch_limit),
                "fetchLimit": fetch_limit,
                "limited": True,
            },
            "items": sorted_rows[:fetch_limit],
        }
    return {
        "source": {
            **source,
            "status": "error",
            "provider": "rss",
            "error": clean_feed_text(errors[-1] if errors else "未配置可用 RSS 代理", 160),
        },
        "items": [],
    }


def x_kol_feed_payload(user: dict[str, Any] | None = None) -> dict[str, Any]:
    sources = load_x_kol_sources(user)
    enabled_sources = [source for source in sources if source.get("enabled") is not False]
    token = x_kol_token()
    items: list[dict[str, Any]] = []
    source_states: list[dict[str, Any]] = []
    for source in enabled_sources:
        try:
            result = x_kol_fetch_api_source(source, token) if token else x_kol_fetch_rss_source(source)
        except Exception as exc:
            result = {
                "source": {
                    **source,
                    "status": "error",
                    "provider": "x-api" if token else "rss",
                    "error": clean_feed_text(exc, 160),
                },
                "items": [],
            }
        source_states.append(result["source"])
        items.extend(result["items"])

    cutoff = int(time.time() * 1000) - X_KOL_RETENTION_MS
    unique_items: dict[str, dict[str, Any]] = {}
    for item in items:
        published_at = int(safe_float(item.get("publishedAt")) or 0)
        if published_at and published_at < cutoff:
            continue
        unique_items[item["id"]] = item
    sorted_items = sorted(unique_items.values(), key=lambda item: int(safe_float(item.get("publishedAt"))), reverse=True)[:240]
    return {
        "ok": True,
        "sources": source_states,
        "items": sorted_items,
        "sourceCount": len(sources),
        "enabledCount": len(enabled_sources),
        "provider": "x-api" if token else "rss",
        "hasToken": bool(token),
        "updatedAt": int(time.time() * 1000),
    }


def decode_js_string(value: Any) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    try:
        return html.unescape(json.loads(f'"{raw}"'))
    except Exception:
        try:
            return html.unescape(raw.encode("utf-8").decode("unicode_escape"))
        except Exception:
            return html.unescape(raw)


def extract_js_var(source: str, name: str) -> str:
    patterns = [
        rf"var\s+{re.escape(name)}\s*=\s*htmlDecode\(\s*(['\"])(.*?)(?<!\\)\1\s*\)",
        rf"var\s+{re.escape(name)}\s*=\s*(['\"])(.*?)(?<!\\)\1(?:\.html\(false\))?",
        rf"\b{re.escape(name)}\s*:\s*(['\"])(.*?)(?<!\\)\1",
    ]
    for pattern in patterns:
        match = re.search(pattern, source, flags=re.S)
        if match:
            return clean_feed_text(decode_js_string(match.group(2)), 1200)
    return ""


def soup_text(soup: BeautifulSoup, selector: str, limit: int = 160) -> str:
    node = soup.select_one(selector)
    return clean_feed_text(node.get_text(" ", strip=True), limit) if node else ""


def parse_wechat_timestamp(source: str) -> int:
    for name in ("ct", "publish_time", "createTime"):
        value = extract_js_var(source, name)
        if not value:
            continue
        if value.isdigit():
            number = int(value)
            return number * 1000 if number < 10_000_000_000 else number
        parsed = parse_feed_datetime(value)
        if parsed:
            return parsed
    meta_match = re.search(r'property=["\']article:published_time["\']\s+content=["\']([^"\']+)["\']', source)
    if meta_match:
        return parse_feed_datetime(meta_match.group(1))
    return 0


def normalize_wechat_article_url(value: Any) -> str:
    url = normalize_feed_url(value)
    parsed = urlparse(url)
    if parsed.netloc not in {"mp.weixin.qq.com", "mp.weixin.qq.com.cn"} and not parsed.netloc.endswith(".mp.weixin.qq.com"):
        raise ValueError("请输入微信公众号文章链接，域名应为 mp.weixin.qq.com")
    return url


def wechat_article_id(url: str) -> str:
    parsed = urlparse(url)
    if "/s/" in parsed.path:
        article_id = parsed.path.split("/s/", 1)[1].strip("/")
        if article_id:
            return article_id
    query = parse_qs(parsed.query)
    parts = [query.get(key, [""])[0] for key in ("__biz", "mid", "idx", "sn")]
    return stable_feed_id("wechat-article", url, *parts)


def parse_wechat_article(url: str) -> dict[str, Any]:
    article_url = normalize_wechat_article_url(url)
    headers = {
        **HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://mp.weixin.qq.com/",
    }
    response = requests.get(article_url, headers=headers, timeout=18)
    response.encoding = "utf-8"
    source = response.text
    soup = BeautifulSoup(source, "html.parser")
    meta = {}
    for item in soup.find_all("meta"):
        key = item.get("property") or item.get("name")
        if key:
            meta[key] = item.get("content", "")

    article_author = (
        extract_js_var(source, "author")
        or soup_text(soup, "#js_author_name_text")
        or soup_text(soup, "#js_author_name")
        or clean_feed_text(meta.get("og:article:author") or meta.get("author"), 120)
    )
    mp_name = (
        soup_text(soup, "#js_name")
        or soup_text(soup, ".rich_media_meta_nickname a")
        or extract_js_var(source, "nickname")
        or extract_js_var(source, "nick_name")
        or article_author
        or "微信公众号"
    )
    title = (
        extract_js_var(source, "msg_title")
        or clean_feed_text(meta.get("og:title") or (soup.title.get_text(" ", strip=True) if soup.title else ""), 180)
    )
    summary = extract_js_var(source, "msg_desc") or clean_feed_text(meta.get("og:description") or meta.get("description"), 420)
    cover = extract_js_var(source, "msg_cdn_url") or clean_feed_text(meta.get("og:image"), 600)
    biz = extract_js_var(source, "biz") or parse_qs(urlparse(article_url).query).get("__biz", [""])[0]
    mp_id = stable_feed_id("wechat-mp", biz or mp_name or article_url)
    published = parse_wechat_timestamp(source)
    article_id = wechat_article_id(article_url)
    item = {
        "id": article_id,
        "title": title or article_url,
        "url": article_url,
        "publishedAt": published,
        "author": article_author or mp_name,
        "summary": summary,
        "image": cover,
    }
    return {
        "feed": {
            "id": mp_id,
            "title": mp_name,
            "siteUrl": article_url,
            "feedUrl": f"wechat-mp://{mp_id}",
            "description": summary,
            "cover": cover,
            "platform": "public-article",
            "seedUrl": article_url,
            "query": mp_name,
            "biz": biz,
        },
        "items": [item] if title or summary else [],
    }


def wechat_env_account() -> dict[str, Any]:
    account_id = os.getenv("WECHAT_ACCOUNT_ID") or os.getenv("WEWE_ACCOUNT_ID") or ""
    token = os.getenv("WECHAT_ACCOUNT_TOKEN") or os.getenv("WEWE_ACCOUNT_TOKEN") or ""
    if not account_id or not token:
        return {}
    return {
        "id": str(account_id).strip(),
        "token": str(token).strip(),
        "name": os.getenv("WECHAT_ACCOUNT_NAME") or "env account",
        "source": "env",
        "status": "active",
    }


def read_wechat_account_cache() -> dict[str, Any]:
    payload = read_json_cache(WECHAT_ACCOUNT_CACHE_PATH)
    accounts = payload.get("accounts")
    if not isinstance(accounts, list):
        payload["accounts"] = []
    return payload


def write_wechat_account_cache(payload: dict[str, Any]) -> None:
    payload["updatedAt"] = int(time.time())
    write_json_cache(WECHAT_ACCOUNT_CACHE_PATH, payload)


def wechat_source_alias_key(*values: Any) -> str:
    for value in values:
        text = clean_feed_text(value, 120).lower()
        text = re.sub(r"\s+", "", text)
        if text and not is_generic_wechat_title(text):
            return text
    return ""


def read_wechat_source_alias_cache() -> dict[str, Any]:
    payload = read_json_cache(WECHAT_SOURCE_ALIAS_CACHE_PATH)
    aliases = payload.get("aliases")
    if not isinstance(aliases, dict):
        payload["aliases"] = {}
    return payload


def write_wechat_source_alias_cache(payload: dict[str, Any]) -> None:
    payload["updatedAt"] = int(time.time())
    write_json_cache(WECHAT_SOURCE_ALIAS_CACHE_PATH, payload)


def public_wechat_source_alias(source: dict[str, Any]) -> dict[str, Any]:
    mp_id = clean_feed_text(source.get("mpId") or source.get("id"), 100)
    if not is_wechat_platform_mp_id(mp_id, "wewe-platform"):
        return {}
    return {
        "type": "wechat",
        "title": clean_feed_text(source.get("title") or source.get("name"), 120),
        "mpId": mp_id,
        "query": clean_feed_text(source.get("query") or source.get("title") or source.get("name"), 120),
        "seedUrl": clean_feed_text(source.get("seedUrl"), 900),
        "feedUrl": f"wechat-mp://{mp_id}",
        "cover": clean_feed_text(source.get("cover"), 900),
        "intro": clean_feed_text(source.get("intro") or source.get("description"), 240),
        "platform": "wewe-platform",
    }


def cache_wechat_source_alias(source: dict[str, Any]) -> None:
    alias = public_wechat_source_alias(source)
    if not alias:
        return
    keys = {
        wechat_source_alias_key(alias.get("title")),
        wechat_source_alias_key(alias.get("query")),
    }
    keys = {key for key in keys if key}
    if not keys:
        return
    payload = read_wechat_source_alias_cache()
    aliases = payload.setdefault("aliases", {})
    for key in keys:
        aliases[key] = {**aliases.get(key, {}), **alias, "updatedAt": int(time.time())}
    write_wechat_source_alias_cache(payload)


def cached_wechat_source_alias(*values: Any) -> dict[str, Any]:
    payload = read_wechat_source_alias_cache()
    aliases = payload.get("aliases") if isinstance(payload, dict) else {}
    if not isinstance(aliases, dict):
        return {}
    for value in values:
        key = wechat_source_alias_key(value)
        if not key:
            continue
        alias = aliases.get(key)
        if isinstance(alias, dict):
            public_alias = public_wechat_source_alias(alias)
            if public_alias:
                return public_alias
    return {}


def public_wechat_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": clean_feed_text(account.get("id"), 80),
        "name": clean_feed_text(account.get("name") or account.get("username") or "微信读书账号", 120),
        "source": clean_feed_text(account.get("source") or "cache", 40),
        "status": clean_feed_text(account.get("status") or "active", 40),
        "lastOkAt": int(safe_float(account.get("lastOkAt"))),
        "lastCheckAt": int(safe_float(account.get("lastCheckAt"))),
        "lastError": clean_feed_text(account.get("lastError"), 180),
        "cooldownUntil": int(safe_float(account.get("cooldownUntil"))),
    }


def wechat_platform_accounts(include_unavailable: bool = False) -> list[dict[str, Any]]:
    now = int(time.time())
    payload = read_wechat_account_cache()
    accounts: list[dict[str, Any]] = []
    for raw in payload.get("accounts", []):
        if not isinstance(raw, dict):
            continue
        account_id = clean_feed_text(raw.get("id"), 80)
        token = str(raw.get("token") or "").strip()
        if not account_id or not token:
            continue
        status = clean_feed_text(raw.get("status") or "active", 40)
        cooldown_until = int(safe_float(raw.get("cooldownUntil")))
        if not include_unavailable and (status == "invalid" or cooldown_until > now):
            continue
        accounts.append(
            {
                **raw,
                "id": account_id,
                "token": token,
                "source": "cache",
                "status": status,
                "cooldownUntil": cooldown_until,
            }
        )

    env_account = wechat_env_account()
    if env_account:
        accounts.append(env_account)

    deduped: dict[str, dict[str, Any]] = {}
    for account in accounts:
        deduped.setdefault(account["id"], account)
    return list(deduped.values())


def wechat_platform_account() -> dict[str, Any]:
    accounts = wechat_platform_accounts()
    return accounts[0] if accounts else {}


def upsert_wechat_account(account: dict[str, Any]) -> dict[str, Any]:
    account_id = clean_feed_text(account.get("id") or account.get("vid"), 80)
    token = str(account.get("token") or "").strip()
    if not account_id or not token:
        raise ValueError("微信读书授权结果缺少账号 ID 或 token")
    payload = read_wechat_account_cache()
    accounts = [item for item in payload.get("accounts", []) if isinstance(item, dict)]
    now = int(time.time())
    saved = {
        "id": account_id,
        "token": token,
        "name": clean_feed_text(account.get("name") or account.get("username") or f"WeRead {account_id}", 120),
        "status": "active",
        "source": "cache",
        "createdAt": int(safe_float(account.get("createdAt"), now)),
        "updatedAt": now,
        "lastOkAt": now,
        "lastError": "",
        "cooldownUntil": 0,
    }
    replaced = False
    for index, item in enumerate(accounts):
        if clean_feed_text(item.get("id"), 80) == account_id:
            accounts[index] = {**item, **saved, "createdAt": int(safe_float(item.get("createdAt"), now))}
            replaced = True
            break
    if not replaced:
        accounts.insert(0, saved)
    payload["accounts"] = accounts[:8]
    write_wechat_account_cache(payload)
    return saved


def update_wechat_account_state(account: dict[str, Any], error: Exception | None = None) -> None:
    if account.get("source") != "cache":
        return
    payload = read_wechat_account_cache()
    accounts = [item for item in payload.get("accounts", []) if isinstance(item, dict)]
    account_id = clean_feed_text(account.get("id"), 80)
    now = int(time.time())
    changed = False
    auth_required_message = ""
    for item in accounts:
        if clean_feed_text(item.get("id"), 80) != account_id:
            continue
        item["updatedAt"] = now
        item["lastCheckAt"] = now
        if error is None:
            item["status"] = "active"
            item["lastOkAt"] = now
            item["lastError"] = ""
            item["cooldownUntil"] = 0
        else:
            message = clean_feed_text(str(error), 240)
            item["lastError"] = message
            if wechat_error_requires_auth(message):
                item["status"] = "invalid"
                auth_required_message = message
            elif "429" in message or "WeReadError429" in message:
                item["status"] = "cooldown"
                item["cooldownUntil"] = now + WECHAT_ACCOUNT_COOLDOWN_SECONDS
            else:
                item["status"] = clean_feed_text(item.get("status") or "active", 40)
        changed = True
        break
    if changed:
        payload["accounts"] = accounts
        write_wechat_account_cache(payload)
    if auth_required_message:
        try:
            notify_wechat_auth_required(auth_required_message)
        except Exception:
            pass


def wechat_platform_url() -> str:
    return (os.getenv("WECHAT_PLATFORM_URL") or os.getenv("WEWE_PLATFORM_URL") or "https://weread.111965.xyz").rstrip("/")


def wechat_platform_headers(account: dict[str, Any] | None = None) -> dict[str, str]:
    account = account or wechat_platform_account()
    if not account:
        raise ValueError("需要先完成微信读书授权，才能精确拉取公众号最新文章列表")
    return {
        **HEADERS,
        "xid": account["id"],
        "Authorization": f"Bearer {account['token']}",
        "Content-Type": "application/json",
    }


def wechat_platform_candidate_paths(path: str) -> list[str]:
    clean_path = "/" + str(path or "").lstrip("/")
    if clean_path.startswith("/api/platform/"):
        tail = clean_path.removeprefix("/api/platform/")
        return [clean_path, f"/api/v2/platform/{tail}"]
    if clean_path.startswith("/api/v2/platform/"):
        tail = clean_path.removeprefix("/api/v2/platform/")
        return [f"/api/platform/{tail}", clean_path]
    return [clean_path]


def wechat_platform_route_missing(error: Any) -> bool:
    text = str(error or "").lower()
    return "cannot get" in text or "cannot post" in text or "http 404" in text or "not found" in text


def wechat_platform_source_error(error: Any) -> bool:
    text = str(error or "").lower()
    return "no book found" in text


def wechat_platform_request(
    method: str,
    path: str,
    account: dict[str, Any] | None = None,
    *,
    mark_ok: bool = True,
    state_error: bool = True,
    **kwargs,
) -> Any:
    accounts = [account] if account else wechat_platform_accounts()
    accounts = [item for item in accounts if item]
    if not accounts:
        try:
            notify_wechat_auth_required("没有可用的微信公众号授权账号")
        except Exception:
            pass
        raise ValueError("需要先完成微信读书授权，才能精确拉取公众号最新文章列表")

    last_error: Exception | None = None
    extra_headers = kwargs.pop("headers", {}) or {}
    paths = wechat_platform_candidate_paths(path)
    for item in accounts:
        account_error: Exception | None = None
        for candidate_path in paths:
            request_kwargs = {**kwargs}
            request_kwargs.setdefault("timeout", 18)
            url = f"{wechat_platform_url()}{candidate_path}"
            try:
                response = requests.request(
                    method,
                    url,
                    headers={**wechat_platform_headers(item), **extra_headers},
                    **request_kwargs,
                )
                text = response.text[:500]
                if response.status_code >= 400:
                    try:
                        message = response.json().get("message") or text
                    except Exception:
                        message = text
                    raise RuntimeError(f"WeWe platform HTTP {response.status_code}: {message}")
                payload = response.json()
                if isinstance(payload, dict) and payload.get("message") and not payload.get("token"):
                    raise RuntimeError(str(payload.get("message")))
                if mark_ok:
                    update_wechat_account_state(item, None)
                return payload
            except Exception as exc:
                last_error = exc
                account_error = exc
                continue
        if state_error and account_error and not wechat_platform_route_missing(account_error) and not wechat_platform_source_error(account_error):
            update_wechat_account_state(item, account_error)
    if wechat_error_requires_auth(last_error):
        try:
            notify_wechat_auth_required(last_error)
        except Exception:
            pass
    raise last_error or ValueError("微信公众号平台接口请求失败")


def wechat_platform_resolve_article(url: str, account: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = wechat_platform_request(
        "POST",
        "/api/platform/wxs2mp",
        account=account,
        json={"url": normalize_wechat_article_url(url)},
        mark_ok=False,
    )
    return payload if isinstance(payload, list) else []


def wechat_platform_articles(mp_id: str, page: int = 1, account: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = wechat_platform_request(
        "GET",
        f"/api/platform/mps/{mp_id}/articles",
        account=account,
        params={"page": page},
    )
    return payload if isinstance(payload, list) else []


def wechat_platform_articles_pages(
    mp_id: str,
    page_limit: int = 1,
    cutoff_ms: int = 0,
    account: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in range(1, max(1, page_limit) + 1):
        page_items = wechat_platform_articles(mp_id, page, account=account)
        if not page_items:
            break
        items.extend(page_items)
        if cutoff_ms:
            timestamps = [int(safe_float(item.get("publishTime")) * 1000) for item in page_items]
            known_timestamps = [value for value in timestamps if value > 0]
            if known_timestamps and max(known_timestamps) < cutoff_ms:
                break
    return items


def wechat_login_begin_payload() -> dict[str, Any]:
    response = requests.get(
        f"{wechat_platform_url()}/api/v2/login/platform",
        headers={**HEADERS, "Accept": "application/json"},
        timeout=18,
    )
    response.raise_for_status()
    payload = response.json()
    uuid = clean_feed_text(payload.get("uuid"), 80)
    if not uuid:
        raise ValueError("微信读书登录二维码创建失败")
    qr_data_url = wechat_qr_data_url(uuid)
    qr_image_path = wechat_qr_image_path(uuid, qr_data_url)
    return {
        "ok": True,
        "uuid": uuid,
        "scanUrl": clean_feed_text(payload.get("scanUrl"), 600),
        "qrUrl": f"https://open.weixin.qq.com/connect/qrcode/{uuid}",
        "qrDataUrl": qr_data_url,
        "qrImagePath": qr_image_path,
        "qrImageUrl": wechat_qr_image_public_url(uuid) if qr_image_path else "",
    }


def wechat_qr_data_url(uuid: str) -> str:
    if not uuid:
        return ""
    try:
        response = requests.get(
            f"https://open.weixin.qq.com/connect/qrcode/{quote(uuid, safe='')}",
            headers={**HEADERS, "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"},
            timeout=12,
        )
        response.raise_for_status()
        content = response.content[:900_000]
        content_type = response.headers.get("Content-Type", "image/png").split(";", 1)[0] or "image/png"
        if not content:
            return ""
        return f"data:{content_type};base64,{base64.b64encode(content).decode('ascii')}"
    except Exception:
        return ""


def wechat_qr_image_path(uuid: str, data_url: str = "") -> str:
    uuid = re.sub(r"[^A-Za-z0-9_-]+", "", str(uuid or ""))[:80]
    if not uuid or not data_url:
        return ""
    match = re.match(r"^data:([^;,]+);base64,(.+)$", data_url, flags=re.I | re.S)
    if not match:
        return ""
    content_type = match.group(1).lower()
    raw = base64.b64decode(match.group(2))
    path = PERSIST_CACHE_DIR / f"wechat-auth-qr-{uuid}.png"
    try:
        try:
            from PIL import Image

            image = Image.open(BytesIO(raw))
            image.save(path, format="PNG")
        except Exception:
            suffix = ".png"
            if "jpeg" in content_type or "jpg" in content_type:
                suffix = ".jpg"
            elif "gif" in content_type:
                suffix = ".gif"
            path = PERSIST_CACHE_DIR / f"wechat-auth-qr-{uuid}{suffix}"
            path.write_bytes(raw)
        return str(path)
    except Exception:
        return ""


def wechat_qr_image_file(uuid: str) -> Path | None:
    uuid = re.sub(r"[^A-Za-z0-9_-]+", "", str(uuid or ""))[:80]
    if not uuid:
        return None
    for suffix in (".png", ".jpg", ".jpeg", ".gif"):
        path = PERSIST_CACHE_DIR / f"wechat-auth-qr-{uuid}{suffix}"
        if path.exists() and path.is_file():
            return path
    return None


def runtime_public_base_url() -> str:
    configured = env_value("XINGYUN_PUBLIC_BASE_URL").rstrip("/")
    if configured:
        return configured
    host = env_value("XINGYUN_HOST", "127.0.0.1") or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = env_value("PORT") or env_value("XINGYUN_PORT") or "8765"
    return f"http://{host}:{port}"


def wechat_qr_image_public_url(uuid: str) -> str:
    uuid = re.sub(r"[^A-Za-z0-9_-]+", "", str(uuid or ""))[:80]
    if not uuid:
        return ""
    return f"{runtime_public_base_url()}/api/wechat-qr-image?uuid={quote(uuid, safe='')}"


def jwt_payload(token: Any) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    try:
        body = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = base64.urlsafe_b64decode(body.encode("ascii"))
        data = json.loads(payload.decode("utf-8", errors="ignore"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def iter_nested_dicts(value: Any, limit: int = 80) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    queue: list[Any] = [value]
    while queue and len(found) < limit:
        current = queue.pop(0)
        if isinstance(current, dict):
            found.append(current)
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current[:limit])
    return found


def wechat_login_account_from_result(result: Any) -> dict[str, Any]:
    containers = iter_nested_dicts(result)
    token_keys = ("token", "accessToken", "access_token", "jwt", "jwtToken", "Authorization", "authorization")
    id_keys = ("vid", "id", "userVid", "user_id", "userId", "xid", "unionid", "openid")
    name_keys = ("username", "name", "nickName", "nickname", "displayName")
    tokens: list[str] = []
    account_ids: list[str] = []
    names: list[str] = []

    for item in containers:
        for key in token_keys:
            value = item.get(key)
            if value:
                text = str(value).replace("Bearer ", "").strip()
                if text and text not in tokens:
                    tokens.append(text)
        for key in id_keys:
            value = clean_feed_text(item.get(key), 120)
            if value and value not in account_ids:
                account_ids.append(value)
        for key in name_keys:
            value = clean_feed_text(item.get(key), 120)
            if value and value not in names:
                names.append(value)

    for token in list(tokens):
        claims = jwt_payload(token)
        for key in ("vid", "id", "userVid", "user_id", "userId", "xid"):
            value = clean_feed_text(claims.get(key), 120)
            if value and value not in account_ids:
                account_ids.append(value)
        for key in ("name", "username", "nickname"):
            value = clean_feed_text(claims.get(key), 120)
            if value and value not in names:
                names.append(value)

    if tokens and account_ids:
        return {
            "id": account_ids[0],
            "token": tokens[0],
            "name": names[0] if names else f"WeRead {account_ids[0]}",
        }
    return {}


def wechat_existing_authorized_payload() -> dict[str, Any] | None:
    account = wechat_platform_account()
    if not account:
        return None
    return {"ok": True, "status": "authorized", "account": public_wechat_account(account), "fromCache": True}


def wechat_login_poll_payload(payload: dict[str, Any]) -> dict[str, Any]:
    uuid = clean_feed_text(payload.get("uuid"), 80)
    if not uuid:
        raise ValueError("缺少微信读书登录 uuid")
    try:
        response = requests.get(
            f"{wechat_platform_url()}/api/v2/login/platform/{uuid}",
            headers={**HEADERS, "Accept": "application/json"},
            timeout=35,
        )
        response.raise_for_status()
        result = response.json()
    except Exception as exc:
        cached = wechat_existing_authorized_payload()
        if cached:
            return {**cached, "message": "已检测到本机已有可用授权"}
        raise exc

    login_account = wechat_login_account_from_result(result)
    if login_account:
        account = upsert_wechat_account(login_account)
        return {"ok": True, "status": "authorized", "account": public_wechat_account(account)}
    cached = wechat_existing_authorized_payload()
    if cached:
        return {**cached, "message": "已检测到本机已有可用授权"}
    message = clean_feed_text(result.get("message") if isinstance(result, dict) else "", 160) or "waiting"
    return {
        "ok": True,
        "status": clean_feed_text(message, 80),
        "message": message,
    }


def wechat_error_requires_auth(error: Any) -> bool:
    text = str(error or "").lower()
    if "wereaderror400" in text and "no book found" not in text:
        return True
    return any(marker in text for marker in ("401", "invalid", "unauthorized", "token", "登录", "授权", "wereaderror401"))


def wechat_auth_probe_url() -> str:
    return env_value("WECHAT_AUTH_PROBE_ARTICLE_URL", "https://mp.weixin.qq.com/s/xn5lAIj5iDplUkbnwQSdEA")


def wechat_validate_account(account: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    now = int(time.time())
    status = clean_feed_text(account.get("status") or "active", 40)
    last_check_at = int(safe_float(account.get("lastCheckAt")))
    if not force and status != "invalid" and last_check_at and now - last_check_at < WECHAT_AUTH_VALIDATE_INTERVAL_SECONDS:
        return {**public_wechat_account(account), "valid": status != "invalid", "checked": False}

    try:
        payload = wechat_platform_resolve_article(wechat_auth_probe_url(), account=account)
        probe_mp_id = ""
        if isinstance(payload, list) and payload:
            probe_mp_id = clean_feed_text(payload[0].get("id") if isinstance(payload[0], dict) else "", 80)
        if probe_mp_id:
            wechat_platform_articles(probe_mp_id, 1, account=account)
        update_wechat_account_state(account, None)
        refreshed = public_wechat_account({**account, "status": "active", "lastOkAt": now, "lastCheckAt": now, "lastError": "", "cooldownUntil": 0})
        return {**refreshed, "valid": True, "checked": True}
    except Exception as exc:
        update_wechat_account_state(account, exc)
        requires_auth = wechat_error_requires_auth(exc)
        if requires_auth:
            try:
                notify_wechat_auth_required(exc)
            except Exception:
                pass
        refreshed = public_wechat_account(
            {
                **account,
                "status": "invalid" if requires_auth else status,
                "lastCheckAt": now,
                "lastError": str(exc),
            }
        )
        return {**refreshed, "valid": not requires_auth and status != "invalid", "checked": True}


def wechat_auth_poll_worker(uuid: str) -> None:
    try:
        for _ in range(300):
            time.sleep(2)
            try:
                payload = wechat_login_poll_payload({"uuid": uuid})
            except Exception:
                continue
            if payload.get("status") == "authorized":
                account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
                try:
                    rss_refresh_all_payload({"fullSync": False, "workers": 2}, admin_user())
                except Exception as exc:
                    print(f"WeChat auth refresh failed: {exc}", file=sys.stderr)
                launch_desktop_alert(
                    {
                        "key": f"wechat-auth-ok:{account.get('id') or uuid}",
                        "kind": "公众号授权",
                        "source": "微信公众号订阅",
                        "sourceLabel": "微",
                        "title": f"授权完成：{account.get('name') or '微信读书账号'}",
                        "body": "公众号订阅会在下一轮自动更新时继续拉取最新文章。",
                        "url": "http://127.0.0.1:8765/rss.html",
                        "priority": "已恢复",
                    }
                )
                return
    finally:
        with WECHAT_AUTH_ALERT_LOCK:
            WECHAT_AUTH_POLLING_UUIDS.discard(uuid)


def start_wechat_auth_poll(uuid: str) -> None:
    if not uuid:
        return
    with WECHAT_AUTH_ALERT_LOCK:
        if uuid in WECHAT_AUTH_POLLING_UUIDS:
            return
        WECHAT_AUTH_POLLING_UUIDS.add(uuid)
    threading.Thread(target=wechat_auth_poll_worker, args=(uuid,), daemon=True).start()


def notify_wechat_auth_required(reason: Any = "", *, force: bool = False, slot_label: str = "") -> dict[str, Any]:
    global WECHAT_AUTH_ALERT_LAST_AT
    now = time.time()
    with WECHAT_AUTH_ALERT_LOCK:
        if not force and now - WECHAT_AUTH_ALERT_LAST_AT < WECHAT_AUTH_ALERT_COOLDOWN_SECONDS:
            return {"ok": True, "cooldown": True}
        WECHAT_AUTH_ALERT_LAST_AT = now

    try:
        login_payload = wechat_login_begin_payload()
    except Exception:
        with WECHAT_AUTH_ALERT_LOCK:
            WECHAT_AUTH_ALERT_LAST_AT = 0.0
        raise
    uuid = clean_feed_text(login_payload.get("uuid"), 80)
    qr_url = clean_feed_text(login_payload.get("qrUrl"), 600)
    qr_image_path = clean_feed_text(login_payload.get("qrImagePath"), 600) or wechat_qr_image_path(
        uuid,
        clean_feed_text(login_payload.get("qrDataUrl"), 1_000_000),
    )
    qr_image_url = clean_feed_text(login_payload.get("qrImageUrl"), 600) or wechat_qr_image_public_url(uuid) or qr_url
    start_wechat_auth_poll(uuid)
    message = clean_feed_text(reason, 120) or "授权已失效或缺少可用账号"
    slot_label = clean_feed_text(slot_label, 24)
    title = "微信公众号授权需要更新"
    if slot_label:
        title = f"{title}（{slot_label}）"
    alert_key = f"wechat-auth-required:{uuid if force else int(now // WECHAT_AUTH_ALERT_COOLDOWN_SECONDS)}"
    return launch_desktop_alert(
        {
            "key": alert_key,
            "kind": "公众号授权",
            "source": "微信公众号订阅",
            "sourceLabel": "微",
            "title": title,
            "body": f"{message}。请用微信扫码，成功后会自动恢复订阅更新。",
            "url": "http://127.0.0.1:8765/rss.html",
            "imageUrl": qr_image_url,
            "imagePath": qr_image_path,
            "priority": "扫码授权",
        }
    )


def wechat_account_status_payload(*, force_validate: bool = False) -> dict[str, Any]:
    raw_accounts = wechat_platform_accounts(include_unavailable=True)
    validated: list[dict[str, Any]] = []
    for account in raw_accounts:
        if account.get("token"):
            validated.append(wechat_validate_account(account, force=force_validate))
        else:
            validated.append({**public_wechat_account(account), "valid": False, "checked": False})
    accounts = validated
    cached_accounts = [item for item in accounts if item.get("source") == "cache"]
    active = [item for item in accounts if item.get("valid") and item.get("status") == "active"]
    invalid = [item for item in accounts if item.get("status") == "invalid" or item.get("valid") is False]
    needs_auth = bool(cached_accounts and (not active or invalid))
    if needs_auth:
        try:
            notify_wechat_auth_required("公众号授权已失效或不可用")
        except Exception:
            pass
    return {
        "ok": True,
        "authorized": bool(active),
        "needsAuth": needs_auth,
        "activeCount": len(active),
        "invalidCount": len(invalid),
        "accounts": accounts,
        "platformUrl": wechat_platform_url(),
    }


def source_from_wechat_mp_info(info: dict[str, Any], seed_url: str = "") -> dict[str, Any]:
    mp_id = clean_feed_text(info.get("id"), 80) or stable_feed_id("wechat-mp", info.get("name"), seed_url)
    title = clean_feed_text(info.get("name") or info.get("mpName") or "微信公众号", 120)
    return {
        "type": "wechat",
        "title": title,
        "mpId": mp_id,
        "query": title,
        "seedUrl": seed_url,
        "feedUrl": f"wechat-mp://{mp_id}",
        "cover": clean_feed_text(info.get("cover") or info.get("mpCover"), 600),
        "intro": clean_feed_text(info.get("intro") or info.get("mpIntro"), 300),
        "updateTime": (safe_float(info.get("updateTime")) * 1000) if info.get("updateTime") else 0,
        "platform": "wewe-platform",
    }


def parse_sogou_time(value: str) -> int:
    text = clean_feed_text(value, 80)
    if not text:
        return 0
    now = int(time.time() * 1000)
    number_match = re.search(r"(\d+)", text)
    number = int(number_match.group(1)) if number_match else 0
    if "分钟前" in text:
        return now - number * 60_000
    if "小时前" in text:
        return now - number * 3_600_000
    if "天前" in text:
        return now - number * 86_400_000
    parsed = parse_feed_datetime(text)
    if parsed:
        return parsed
    date_match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if date_match:
        y, m, d = [int(item) for item in date_match.groups()]
        return int(datetime(y, m, d).timestamp() * 1000)
    return 0


def fetch_sogou_wechat_articles(query: str, limit: int = 20) -> list[dict[str, Any]]:
    keyword = clean_feed_text(query, 80)
    if not keyword:
        return []
    url = f"https://weixin.sogou.com/weixin?type=2&ie=utf8&query={quote(keyword)}"
    headers = {
        **HEADERS,
        "Referer": "https://weixin.sogou.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    response = requests.get(url, headers=headers, timeout=18)
    response.encoding = response.apparent_encoding or "utf-8"
    source = response.text
    if "请输入验证码" in source or "antispider" in source.lower():
        raise ValueError("搜狗微信触发验证码，建议改用公众号文章链接并配置 WeWe 平台账号")
    soup = BeautifulSoup(source, "html.parser")
    rows = soup.select(".news-list li")
    items = []
    for row in rows[:limit]:
        link_node = row.select_one(".txt-box h3 a") or row.select_one("h3 a")
        if not link_node:
            continue
        title = clean_feed_text(link_node.get_text(" ", strip=True), 180)
        href = link_node.get("href") or ""
        if href.startswith("/"):
            href = urljoin("https://weixin.sogou.com", href)
        summary = clean_feed_text((row.select_one(".txt-info") or row).get_text(" ", strip=True), 420)
        author = clean_feed_text((row.select_one(".s-p a") or row.select_one(".account")).get_text(" ", strip=True) if row.select_one(".s-p a") or row.select_one(".account") else keyword, 80)
        if author and keyword and keyword not in author and author not in keyword:
            continue
        time_node = row.select_one(".s2") or row.select_one(".time")
        published = parse_sogou_time(time_node.get_text(" ", strip=True) if time_node else "")
        image = ""
        image_node = row.select_one("img")
        if image_node:
            image = image_node.get("src") or image_node.get("data-src") or ""
        items.append(
            {
                "id": stable_feed_id("sogou-wechat", href, title),
                "title": title or href,
                "url": href,
                "publishedAt": published,
                "author": author,
                "summary": summary,
                "image": urljoin("https://weixin.sogou.com", image) if image else "",
            }
        )
    return items


def resolve_wechat_mp_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw = clean_feed_text(payload.get("input") or payload.get("url") or payload.get("query") or payload.get("raw"), 600)
    title_override = clean_feed_text(payload.get("title"), 120)
    if not raw:
        raise ValueError("请填写公众号名称，或粘贴公众号任意一篇文章链接")
    if raw.startswith("mp.weixin.qq.com/"):
        raw = f"https://{raw}"
    if raw.startswith("http"):
        try:
            matches = wechat_platform_resolve_article(raw) if wechat_platform_account() else []
            if matches:
                source = source_from_wechat_mp_info(matches[0], raw)
                cache_wechat_source_alias(source)
            else:
                parsed = parse_wechat_article(raw)
                source = {
                    "type": "wechat",
                    "title": parsed["feed"]["title"],
                    "mpId": parsed["feed"]["id"],
                    "query": parsed["feed"].get("query") or parsed["feed"]["title"],
                    "seedUrl": raw,
                    "feedUrl": parsed["feed"]["feedUrl"],
                    "cover": parsed["feed"].get("cover", ""),
                    "intro": parsed["feed"].get("description", ""),
                    "platform": parsed["feed"].get("platform", "public-article"),
                    "biz": parsed["feed"].get("biz", ""),
                }
        except Exception:
            parsed = parse_wechat_article(raw)
            source = {
                "type": "wechat",
                "title": parsed["feed"]["title"],
                "mpId": parsed["feed"]["id"],
                "query": parsed["feed"].get("query") or parsed["feed"]["title"],
                "seedUrl": raw,
                "feedUrl": parsed["feed"]["feedUrl"],
                "cover": parsed["feed"].get("cover", ""),
                "intro": parsed["feed"].get("description", ""),
                "platform": parsed["feed"].get("platform", "public-article"),
                "biz": parsed["feed"].get("biz", ""),
            }
    else:
        raise ValueError("公众号精确订阅需要粘贴该公众号任意一篇文章链接；不再使用搜索结果作为公众号列表")
    if title_override:
        source["title"] = title_override
    return {"ok": True, "source": source}


def wechat_mp_fetch_payload(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else payload
    mp_id = clean_feed_text(source.get("mpId"), 100)
    seed_url = clean_feed_text(source.get("seedUrl"), 600)
    query = clean_feed_text(source.get("query") or source.get("title"), 120)
    title = clean_feed_text(source.get("title") or query or "微信公众号", 120)
    platform = source.get("platform") or ""
    if is_generic_wechat_title(title) and query and not is_generic_wechat_title(query):
        title = query
    if mp_id and not is_wechat_platform_mp_id(mp_id, platform):
        mp_id = ""
    if not mp_id:
        cached_source = cached_wechat_source_alias(title, query)
        if cached_source:
            mp_id = cached_source.get("mpId") or ""
            title = cached_source.get("title") or title
            query = cached_source.get("query") or query
            seed_url = seed_url or cached_source.get("seedUrl") or ""
            source = {**source, **cached_source}
            platform = "wewe-platform"

    items: list[dict[str, Any]] = []
    warning = ""
    notice = ""
    platform_items_count = 0
    has_wechat_account = bool(wechat_platform_account())
    cutoff_ms = int(time.time() * 1000) - RSS_RETENTION_MS
    allow_history_fallback = payload.get("allowHistoryFallback") is not False
    if not has_wechat_account:
        try:
            notify_wechat_auth_required("当前没有可用的微信公众号授权账号")
        except Exception:
            pass

    if seed_url and has_wechat_account and platform != "wewe-platform":
        resolve_errors: list[str] = []
        candidate_urls = [seed_url]
        for candidate in wechat_seed_candidates_from_alert_history(title):
            if candidate not in candidate_urls:
                candidate_urls.append(candidate)
        for candidate_url in candidate_urls[:12]:
            try:
                matches = wechat_platform_resolve_article(candidate_url)
                if matches:
                    platform_source = source_from_wechat_mp_info(matches[0], candidate_url)
                    cache_wechat_source_alias(platform_source)
                    mp_id = platform_source.get("mpId") or mp_id
                    title = platform_source.get("title") or title
                    query = platform_source.get("query") or query
                    seed_url = candidate_url
                    source = {**source, **platform_source}
                    platform = "wewe-platform"
                    warning = ""
                    break
            except Exception as exc:
                resolve_errors.append(str(exc))
        if platform != "wewe-platform" and resolve_errors:
            warning = f"公众号账号接口解析失败：{resolve_errors[-1]}"

    if mp_id and has_wechat_account and not platform.startswith("sogou"):
        try:
            page_limit = (
                WECHAT_PLATFORM_BACKFILL_PAGE_LIMIT
                if payload.get("backfill") or payload.get("fullSync")
                else WECHAT_PLATFORM_AUTO_PAGE_LIMIT
            )
            platform_items = wechat_platform_articles_pages(mp_id, page_limit=page_limit, cutoff_ms=cutoff_ms)
            platform_items_count = len(platform_items)
            for item in platform_items:
                article_id = clean_feed_text(item.get("id"), 120)
                items.append(
                    {
                        "id": article_id or stable_feed_id("wechat-platform", item.get("title")),
                        "title": clean_feed_text(item.get("title"), 180),
                        "url": f"https://mp.weixin.qq.com/s/{article_id}" if article_id else "",
                        "publishedAt": int(safe_float(item.get("publishTime")) * 1000),
                        "author": title,
                        "summary": "",
                        "image": clean_feed_text(item.get("picUrl"), 600),
                    }
                )
        except Exception as exc:
            warning = f"公众号最新列表拉取失败：{exc}"
            if wechat_error_requires_auth(exc):
                try:
                    notify_wechat_auth_required(exc)
                except Exception:
                    pass

    if seed_url:
        try:
            parsed = parse_wechat_article(seed_url)
            parsed_title = clean_feed_text(parsed["feed"].get("title"), 120)
            if parsed_title and not is_generic_wechat_title(parsed_title):
                title = parsed_title
            items.extend(parsed["items"])
            if not query:
                parsed_query = clean_feed_text(parsed["feed"].get("query") or parsed_title, 120)
                if parsed_query and not is_generic_wechat_title(parsed_query):
                    query = parsed_query
        except Exception:
            pass

    used_history_items = False
    if allow_history_fallback and not items:
        history_items = wechat_history_items_for_source(title or query, cutoff_ms=cutoff_ms)
        if history_items:
            items.extend(history_items)
            used_history_items = True
            warning = ""
            notice = "已使用本地历史内容；重新粘贴该公众号任意一篇原文链接后可恢复实时更新。"

    if not platform_items_count and not warning and not used_history_items:
        if has_wechat_account and not mp_id:
            notice = "该公众号源缺少真实公众号 ID，历史文章链接无法解析；请重新粘贴该公众号任意文章链接修复订阅"
        elif has_wechat_account:
            notice = "公众号接口本轮没有返回列表，已保留本地历史内容"
        else:
            notice = "微信公众号授权已失效或不可用，扫码授权后才能精确拉取公众号最新列表"

    deduped: dict[str, dict[str, Any]] = {}
    for item in items:
        key = clean_feed_text(item.get("id") or item.get("url") or item.get("title"), 160)
        if key and key not in deduped:
            deduped[key] = item
    items = sorted(deduped.values(), key=lambda item: item.get("publishedAt") or 0, reverse=True)
    items = recent_feed_items(items, cutoff_ms)
    items, skipped_existing = incremental_items(items, payload)
    feed_id = mp_id or stable_feed_id("wechat-mp", title, seed_url, query)
    return {
        "ok": True,
        "feed": {
            "id": feed_id,
            "title": title,
            "siteUrl": seed_url,
            "feedUrl": f"wechat-mp://{feed_id}",
            "description": clean_feed_text(source.get("intro"), 220),
            "cover": clean_feed_text(source.get("cover"), 600),
            "platform": platform or "public-article",
        },
        "items": [
            {**item, "sourceTitle": title}
            for item in items
            if item.get("title") or item.get("url")
        ],
        "skippedExisting": skipped_existing,
        "incremental": bool(payload.get("knownIds") or payload.get("since")),
        "warning": warning,
        "notice": notice,
        "platformItems": platform_items_count,
        "fetchedAt": int(time.time() * 1000),
    }


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def signed(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:g}"


def money_usd(value: float) -> str:
    value = safe_float(value)
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"${value / 1_000:.2f}K"
    return f"${value:.0f}"


def money_cny(value: float) -> str:
    value = safe_float(value)
    abs_value = abs(value)
    if abs_value >= 100_000_000:
        return f"¥{value / 100_000_000:.2f}亿"
    if abs_value >= 10_000:
        return f"¥{value / 10_000:.2f}万"
    return f"¥{value:.0f}"


def compact_number(value: float) -> str:
    value = safe_float(value)
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.2f}K"
    return f"{value:g}"


EXCLUDED_CRYPTO_ASSETS = {
    "BTC",
    "ETH",
    "BNB",
    "USDT",
    "USDC",
    "STABLE",
    "FDUSD",
    "TUSD",
    "BUSD",
    "DAI",
    "USDP",
    "USDD",
    "USD1",
    "USDG",
    "USDX",
    "USDY",
    "USDB",
    "USDA",
    "USN",
    "USDE",
    "SUSDE",
    "USDS",
    "PYUSD",
    "RLUSD",
    "GUSD",
    "LUSD",
    "FRAX",
    "MIM",
    "USTC",
    "AEUR",
    "EURC",
    "EURS",
    "EURI",
    "EUR",
    "GBP",
    "AUD",
    "BRL",
    "TRY",
    "BIDR",
    "IDRT",
    "U",
    "SOL",
    "DOGE",
}


def normalize_asset_symbol(value: Any) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(value or "").upper())


def is_excluded_crypto_asset(value: Any) -> bool:
    return normalize_asset_symbol(value) in EXCLUDED_CRYPTO_ASSETS


def crypto_icon_url(value: Any) -> str:
    symbol = normalize_asset_symbol(value)
    if not symbol:
        return ""
    return f"https://assets.coincap.io/assets/icons/{symbol.lower()}@2x.png"


def price_usd(value: Any) -> str:
    price = safe_float(value)
    if not price:
        return ""
    if price >= 1000:
        return f"${price:,.2f}"
    if price >= 1:
        return f"${price:.4f}".rstrip("0").rstrip(".")
    return f"${price:.8f}".rstrip("0").rstrip(".")


def unique_values(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def domain_logo_candidates(domain: str) -> list[str]:
    domain = domain.strip().strip("/")
    if not domain:
        return []
    return unique_values(
        [
            f"https://cdn.tickerlogos.com/{domain}",
            f"https://www.google.com/s2/favicons?sz=128&domain={domain}",
        ]
    )


def allinvest_logo_candidates(query: str, expected_symbol: str = "") -> list[str]:
    query = query.strip().upper()
    expected_symbol = expected_symbol.strip().upper()
    if not query:
        return []

    cache_key = f"allinvest:{query}:{expected_symbol}"
    now = time.time()
    if cache_key in LOGO_CACHE and now - LOGO_CACHE[cache_key][0] < LOGO_CACHE_TTL:
        return LOGO_CACHE[cache_key][1]

    logos: list[str] = []
    try:
        payload = requests.get(
            "https://www.allinvestview.com/api/logo-search/",
            params={"q": query},
            headers=HEADERS,
            timeout=5,
        ).json()
        results = payload.get("results") or []
        for item in results:
            symbol = str(item.get("symbol") or "").upper()
            if expected_symbol and symbol != expected_symbol:
                continue
            website = str(item.get("website") or "").strip()
            if not website:
                continue
            parsed = urlparse(website if "://" in website else f"https://{website}")
            domain = parsed.netloc or parsed.path.split("/")[0]
            domain = domain.strip().strip("/")
            if domain:
                logos.extend(domain_logo_candidates(domain))
                break
    except Exception:
        logos = []

    logos = unique_values(logos)
    LOGO_CACHE[cache_key] = (now, logos)
    return logos


def stock_icon_candidates(symbol: Any, market: str) -> list[str]:
    raw_symbol = str(symbol or "").strip().upper()
    if not raw_symbol:
        return []

    if market == "us":
        fmp_symbol = raw_symbol.replace(".", "-")
        return unique_values(
            [
                f"https://financialmodelingprep.com/image-stock/{fmp_symbol}.png",
                f"https://eodhd.com/img/logos/US/{fmp_symbol.lower()}.png",
                f"https://eodhd.com/img/logos/US/{fmp_symbol}.png",
            ]
        )

    if market == "hk":
        digits = re.sub(r"\D", "", raw_symbol)
        if not digits:
            return []
        yahoo_code = digits[-4:] if len(digits) > 4 else digits.zfill(4)
        overrides = [url for domain in STOCK_LOGO_OVERRIDES.get(f"hk:{digits.zfill(5)}", []) for url in domain_logo_candidates(domain)]
        return unique_values(
            [
                f"https://financialmodelingprep.com/image-stock/{yahoo_code}.HK.png",
                f"https://eodhd.com/img/logos/HK/{yahoo_code}.png",
                *overrides,
            ]
        )

    if market == "cn":
        code = re.sub(r"\D", "", raw_symbol).zfill(6)[-6:]
        if not code:
            return []
        is_shanghai = code.startswith(("5", "6", "9"))
        fmp_suffix = "SS" if is_shanghai else "SZ"
        eodhd_exchange = "SHG" if is_shanghai else "SHE"
        return unique_values(
            [
                *allinvest_logo_candidates(f"{code}.{fmp_suffix}", f"{code}.{fmp_suffix}"),
                f"https://financialmodelingprep.com/image-stock/{code}.{fmp_suffix}.png",
                f"https://eodhd.com/img/logos/{eodhd_exchange}/{code}.png",
            ]
        )

    return []


def exchange_asset_icon_candidates(asset: Any, *, prefer_us_stock: bool = False) -> list[str]:
    symbol = normalize_asset_symbol(asset)
    if not symbol:
        return []
    stock_candidates = stock_icon_candidates(symbol, "us") if prefer_us_stock else []
    crypto_candidates = [crypto_icon_url(symbol)]
    if prefer_us_stock:
        return unique_values([*stock_candidates, *crypto_candidates])
    return unique_values([*crypto_candidates, *stock_candidates])


def change_from_open(last: Any, open_price: Any) -> float:
    last_value = safe_float(last)
    open_value = safe_float(open_price)
    if not last_value or not open_value:
        return 0
    return (last_value - open_value) / open_value * 100


def date_yyyy_mm_dd(timestamp_ms: Any) -> str:
    value = safe_float(timestamp_ms)
    if not value:
        return "--"
    if value > 10_000_000_000:
        value = value / 1000
    try:
        return datetime.fromtimestamp(value).strftime("%Y/%m/%d")
    except Exception:
        return "--"


def source_template(
    *,
    id: str,
    group: str,
    title: str,
    subtitle: str,
    accent: str,
    source_label: str,
    source_name: str,
    rows: list[dict[str, Any]],
    status: str = "ok",
    empty_title: str = "",
    empty_message: str = "",
) -> dict[str, Any]:
    return {
        "id": id,
        "group": group,
        "title": title,
        "subtitle": subtitle,
        "accent": accent,
        "sourceLabel": source_label,
        "sourceName": source_name,
        "updatedAt": int(time.time() * 1000),
        "status": status,
        "emptyTitle": empty_title,
        "emptyMessage": empty_message,
        "rows": rows,
    }


def env_value(name: str, default: str = "") -> str:
    return raw_env_value(name, default)


def okx_web_headers() -> dict[str, str]:
    headers = {
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.okx.com/zh-hans/markets/rankings/futures",
        "x-locale": "zh_CN",
    }
    if user_agent := env_value("OKX_WEB_USER_AGENT"):
        headers["User-Agent"] = user_agent
    if cookie := env_value("OKX_WEB_COOKIE"):
        headers["Cookie"] = cookie
    if token := env_value("OKX_WEB_TOKEN"):
        header_name = env_value("OKX_WEB_TOKEN_HEADER", "Authorization")
        headers[header_name] = token if header_name.lower() != "authorization" or token.lower().startswith("bearer ") else f"Bearer {token}"
    if extra_headers := env_value("OKX_EXTRA_HEADERS"):
        for item in re.split(r";\s*", extra_headers):
            if not item:
                continue
            separator = ":" if ":" in item else "="
            if separator not in item:
                continue
            name, value = item.split(separator, 1)
            if name.strip() and value.strip():
                headers[name.strip()] = value.strip()
    return headers


def bitget_web_headers() -> dict[str, str]:
    headers = {
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://www.bitget.com",
        "Referer": "https://www.bitget.com/zh-CN/markets/rank/hot",
        "sec-ch-ua": '"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "terminalCode": "WEB",
        "languageType": "1",
        "locale": "zh-CN",
    }
    if user_agent := env_value("BITGET_WEB_USER_AGENT"):
        headers["User-Agent"] = user_agent
    if cookie := env_value("BITGET_WEB_COOKIE"):
        headers["Cookie"] = cookie
    if extra_headers := env_value("BITGET_EXTRA_HEADERS"):
        for item in re.split(r";\s*", extra_headers):
            if not item:
                continue
            separator = ":" if ":" in item else "="
            if separator not in item:
                continue
            name, value = item.split(separator, 1)
            if name.strip() and value.strip():
                headers[name.strip()] = value.strip()
    return headers


def fetch_binance() -> dict[str, Any]:
    headers = {
        **HEADERS,
        "Accept": "application/json",
        "clienttype": "web",
        "lang": "zh-CN",
        "bnc-location": "CN",
        "Referer": "https://www.binance.com/zh-CN/markets/trading_data/rankings",
    }
    hot_payload = requests.get(
        "https://www.binance.com/bapi/composite/v1/public/market/hot-coins",
        params={"currency": "USD", "type": "1"},
        headers=headers,
        timeout=15,
    ).json()
    product_payload = requests.get(
        "https://www.binance.com/bapi/asset/v2/public/asset-service/product/get-products?includeEtf=true",
        headers=headers,
        timeout=20,
    ).json()
    products = {item.get("s"): item for item in product_payload.get("data", [])}
    top = [
        item
        for item in hot_payload.get("data", [])
        if item.get("assetCode") and not is_excluded_crypto_asset(item.get("assetCode"))
    ][:10]
    rows = []
    for rank, item in enumerate(top, 1):
        symbol = item.get("assetCode", "")
        product = products.get(f"{symbol}USDT") or {}
        last = safe_float(product.get("c"))
        open_price = safe_float(product.get("o"))
        change = ((last - open_price) / open_price * 100) if open_price else 0
        quote_volume = safe_float(product.get("qv"))
        rows.append(
            {
                "rank": rank,
                "symbol": symbol,
                "name": item.get("assetName") or f"{symbol}/USDT",
                "icon": item.get("logoUrl") or crypto_icon_url(symbol),
                "price": price_usd(last),
                "change": pct(change),
                "heat": max(1, round((len(top) - rank + 1) / max(len(top), 1) * 100)),
                "amount": quote_volume,
                "turnover": money_usd(quote_volume) if quote_volume else "官网热榜",
                "tags": ["官网热门币种", "Binance"],
                "note": f"最新价 {last:g}" if last else "官网热门榜",
            }
        )
    return source_template(
        id="binance",
        group="crypto",
        title="Binance 热门币种",
        subtitle="官网 trading_data/rankings 热门币种，已过滤稳定币",
        accent="#f4b740",
        source_label="BN",
        source_name="Binance bapi hot-coins",
        rows=rows,
    )


def okx_rank_change(value: Any) -> float:
    change = safe_float(value)
    return change * 100 if -1.5 <= change <= 1.5 else change


def okx_symbol_from_item(item: dict[str, Any]) -> str:
    symbol = str(
        item.get("ccyV2")
        or item.get("ccy")
        or item.get("baseCcy")
        or item.get("instFamily")
        or item.get("coin")
        or ""
    ).upper()
    if not symbol:
        inst_id = str(item.get("instId") or item.get("instIdV2") or item.get("uly") or "").upper()
        symbol = re.sub(r"[-_](USDT|USDC|USD)([-_](SWAP|FUTURES))?$", "", inst_id)
        symbol = symbol.split("-")[0].split("_")[0]
    return symbol.replace("/USDT", "").replace("-USDT", "").strip()


def parse_okx_rank_rows(items: list[Any], *, tag: str = "期货热榜") -> list[dict[str, Any]]:
    dict_items = [item for item in items if isinstance(item, dict)]
    max_hot = max(
        [
            safe_float(
                item.get("hotIndex")
                or item.get("rankIndex")
                or item.get("weight")
                or (len(dict_items) - index)
            )
            for index, item in enumerate(dict_items)
        ]
        or [1]
    )
    rows: list[dict[str, Any]] = []
    for rank, item in enumerate(dict_items, 1):
        symbol = okx_symbol_from_item(item)
        if not symbol or is_excluded_crypto_asset(symbol):
            continue
        last = safe_float(
            item.get("lastPrice")
            or item.get("last")
            or item.get("lastPx")
            or item.get("price")
            or item.get("markPx")
        )
        change = okx_rank_change(item.get("changePerDay24h") or item.get("changePerV2") or item.get("changePer") or item.get("chg"))
        turnover = safe_float(
            item.get("turnOver24h")
            or item.get("turnOver")
            or item.get("turnover")
            or item.get("turnOverV2")
            or item.get("quoteVolume24h")
            or item.get("volCcy24h")
            or item.get("volUsd24h")
            or item.get("amount")
        )
        hot_index = safe_float(item.get("hotIndex") or item.get("rankIndex") or item.get("weight") or (len(dict_items) - rank + 1))
        rows.append(
            {
                "rank": len(rows) + 1,
                "symbol": symbol,
                "name": item.get("instId") or item.get("instIdV2") or f"{symbol}-USDT-SWAP",
                "icon": item.get("icon") or item.get("logoUrl") or item.get("coinIcon") or crypto_icon_url(symbol),
                "price": price_usd(last),
                "change": pct(change),
                "heat": max(1, min(100, round(hot_index / max(max_hot, 1) * 100))),
                "amount": turnover,
                "turnover": money_usd(turnover) if turnover else "官网热榜",
                "tags": [tag, "OKX"],
                "note": f"最新价 {last:g}" if last else "OKX 合约热门榜",
            }
        )
        if len(rows) >= 10:
            break
    return rows


def fetch_okx_futures_public_ticker_rows(mode: str = "hot") -> tuple[list[dict[str, Any]], str]:
    payload = requests.get(
        "https://www.okx.com/api/v5/market/tickers",
        params={"instType": "SWAP"},
        headers={"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"},
        timeout=18,
    ).json()
    if str(payload.get("code", "0")) != "0":
        raise RuntimeError(str(payload.get("msg") or payload.get("code") or "OKX ticker error"))

    candidates: list[dict[str, Any]] = []
    for item in payload.get("data", []) or []:
        if not isinstance(item, dict):
            continue
        inst_id = str(item.get("instId") or "").upper()
        parts = inst_id.split("-")
        if len(parts) < 3 or parts[1] != "USDT" or parts[-1] != "SWAP":
            continue
        asset = parts[0]
        if not asset or is_excluded_crypto_asset(asset):
            continue
        last = safe_float(item.get("last"))
        if not last:
            continue
        open_price = safe_float(item.get("open24h"))
        change = change_from_open(last, open_price)
        volume_base = safe_float(item.get("volCcy24h")) or safe_float(item.get("vol24h"))
        turnover = safe_float(item.get("volUsd24h")) or (last * volume_base if volume_base else 0)
        if mode == "gainers" and change <= 0:
            continue
        momentum_boost = 1 + min(abs(change), 80) / 100
        score = turnover * momentum_boost
        candidates.append(
            {
                "asset": asset,
                "instId": inst_id,
                "last": last,
                "change": change,
                "turnover": turnover,
                "score": score,
            }
        )

    if mode == "gainers":
        candidates.sort(key=lambda item: (safe_float(item.get("change")), safe_float(item.get("turnover"))), reverse=True)
        tag = "OKX 合约涨幅榜"
        detail = "public /api/v5/market/tickers SWAP 24h gainers"
    elif mode == "turnover":
        candidates.sort(key=lambda item: safe_float(item.get("turnover")), reverse=True)
        tag = "OKX 合约成交额榜"
        detail = "public /api/v5/market/tickers SWAP 24h turnover"
    else:
        candidates.sort(key=lambda item: safe_float(item.get("score")), reverse=True)
        tag = "OKX 合约公开热度"
        detail = "public /api/v5/market/tickers SWAP hot fallback"

    top = candidates[:10]
    max_score = max([safe_float(item.get("score" if mode == "hot" else "turnover")) for item in top] or [1])
    rows: list[dict[str, Any]] = []
    for item in top:
        heat_basis = safe_float(item.get("score" if mode == "hot" else "turnover"))
        rows.append(
            {
                "rank": len(rows) + 1,
                "symbol": item["asset"],
                "name": item["instId"],
                "icon": crypto_icon_url(item["asset"]),
                "price": price_usd(item["last"]),
                "change": pct(item["change"]),
                "heat": max(1, min(100, round(heat_basis / max(max_score, 1) * 100))),
                "amount": item["turnover"],
                "turnover": money_usd(item["turnover"]) if item["turnover"] else "--",
                "tags": ["OKX", tag],
                "note": f"24h turnover {money_usd(item['turnover']) if item['turnover'] else '--'} · 24h {pct(item['change'])}",
                "url": f"https://www.okx.com/zh-hans/trade-swap/{item['instId'].lower()}",
            }
        )
    return rows, detail


def fetch_okx_futures_hot_from_public_tickers() -> tuple[list[dict[str, Any]], str]:
    return fetch_okx_futures_public_ticker_rows("hot")


def fetch_okx_futures_turnover_from_public_tickers() -> tuple[list[dict[str, Any]], str]:
    return fetch_okx_futures_public_ticker_rows("turnover")


def fetch_okx_futures_ssr_rank() -> tuple[list[dict[str, Any]], str]:
    headers = {
        **okx_web_headers(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    pages = [
        ("https://www.okx.com/zh-hans/markets/rankings/futures/hot-crypto", "official SSR hot-crypto detail"),
        ("https://www.okx.com/zh-hans/markets/rankings/futures", "official SSR hot-crypto overview"),
    ]
    last_detail = "official SSR appState missing"
    for url, label in pages:
        response = requests.get(url, headers=headers, timeout=22)
        response.raise_for_status()
        app_state = BeautifulSoup(response.text, "html.parser").find("script", {"id": "appState"})
        raw_state = (app_state.string or app_state.get_text()) if app_state else ""
        if not raw_state:
            match = re.search(r'<script[^>]+id=["\']appState["\'][^>]*>(.*?)</script>', response.text, re.I | re.S)
            raw_state = html.unescape(match.group(1).strip()) if match else ""
        if not raw_state:
            last_detail = f"{label} appState missing"
            continue

        state = json.loads(raw_state)
        rank_store = (
            state.get("appContext", {})
            .get("initialProps", {})
            .get("ssrData", {})
            .get("rankStore", {})
        )
        hot_block = rank_store.get("rankData", {}).get("hot-crypto", {})
        raw_items = hot_block.get("data") or []
        rows = parse_okx_rank_rows(raw_items, tag="OKX official hot")
        page_size = hot_block.get("pageSize") or len(raw_items) or len(rows)
        detail = f"{label} pageSize={page_size}"
        if rows:
            return rows, detail
        last_detail = f"{detail} empty"
    return [], last_detail


def okx_items_from_payload(payload: dict[str, Any]) -> list[Any]:
    data_block = payload.get("data") or {}
    if isinstance(data_block, dict):
        for key in ("data", "hot-rank", "hotRank", "futuresHotRank", "rankList", "list"):
            value = data_block.get(key)
            if isinstance(value, list):
                return value
        return []
    if isinstance(data_block, list):
        if len(data_block) == 1 and isinstance(data_block[0], dict):
            for key in ("hot-rank", "hotRank", "futuresHotRank", "rankList", "list", "data"):
                value = data_block[0].get(key)
                if isinstance(value, list):
                    return value
        return data_block
    return []


def okx_futures_rank_params(rank_type: str = "hot") -> dict[str, str]:
    return {
        "rankType": rank_type,
        "type": env_value("OKX_FUTURES_TYPE", "USDT"),
        "rank": "0",
        "countryFilter": env_value("OKX_COUNTRY_FILTER", "1"),
        "period": "1D",
        "zone": env_value("OKX_RANK_ZONE", "utc24"),
        "pageSize": env_value("OKX_RANK_PAGE_SIZE", "25"),
        "pageNum": "1",
    }


def save_okx_futures_cache(rows: list[dict[str, Any]], source_detail: str) -> None:
    if len(rows) < 10:
        return
    payload = {
        "updatedAt": int(time.time() * 1000),
        "sourceDetail": source_detail,
        "rows": rows,
    }
    try:
        write_json_cache(OKX_FUTURES_CACHE_PATH, payload)
    except Exception:
        pass


def load_okx_futures_cache() -> tuple[list[dict[str, Any]], str, int]:
    payload = read_json_cache(OKX_FUTURES_CACHE_PATH)
    rows = payload.get("rows")
    updated_at = int(safe_float(payload.get("updatedAt")))
    if not isinstance(rows, list) or not rows:
        return [], "", 0
    source_detail = str(payload.get("sourceDetail") or "cached verified futures ranking")
    if "market/tickers" in source_detail or "fallback" in source_detail.lower():
        return [], "", updated_at

    max_age_hours = safe_float(env_value("OKX_FUTURES_CACHE_MAX_AGE_HOURS", "72"), 72)
    if max_age_hours > 0 and updated_at:
        age_seconds = (time.time() * 1000 - updated_at) / 1000
        if age_seconds > max_age_hours * 3600:
            return [], "", updated_at

    clean_rows = [row for row in rows if isinstance(row, dict)]
    if not clean_rows:
        return [], "", updated_at
    return clean_rows[:10], source_detail, updated_at


def rank_row_identity(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol") or "").strip().upper()
    name = str(row.get("name") or "").strip().upper()
    if symbol:
        return re.sub(r"[-_/](USDT|USDC|USD)([-_](SWAP|FUTURES))?$", "", symbol)
    return re.sub(r"[-_/](USDT|USDC|USD)([-_](SWAP|FUTURES))?$", "", name)


def merge_rank_rows(primary: list[dict[str, Any]], fallback: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in [*primary, *fallback]:
        if not isinstance(row, dict):
            continue
        identity = rank_row_identity(row)
        if not identity or identity in seen:
            continue
        cloned = dict(row)
        cloned["rank"] = len(merged) + 1
        merged.append(cloned)
        seen.add(identity)
        if len(merged) >= limit:
            break
    return merged


def okx_desktop_proxy_ports() -> list[int]:
    ports: list[int] = []
    for value in re.split(r"[,;\s]+", env_value("OKX_DESKTOP_PROXY_PORTS", "17000,17001,17002,17003,17004,17005")):
        if not value:
            continue
        try:
            ports.append(int(value))
        except ValueError:
            continue
    return ports


def okx_desktop_proxy_get(path: str, params: dict[str, str] | None = None) -> tuple[dict[str, Any], int]:
    connect_host = env_value("OKX_DESKTOP_PROXY_CONNECT_HOST", "127.0.0.1") or "127.0.0.1"
    headers = {
        **okx_web_headers(),
        "Host": env_value("OKX_DESKTOP_PROXY_HOST", "www.okx.com"),
        "User-Agent": env_value("OKX_DESKTOP_USER_AGENT", "OKX/2.6.1"),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.okx.com/zh-hans/markets/rankings/futures",
        "x-locale": "zh_CN",
    }
    last_error = "no desktop proxy ports"
    timeout = safe_float(env_value("OKX_DESKTOP_PROXY_TIMEOUT", "8"), 8)
    for port in okx_desktop_proxy_ports():
        try:
            response = requests.get(
                f"https://{connect_host}:{port}{path}",
                params=params,
                headers=headers,
                verify=False,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if str(payload.get("code", "0")) == "0":
                return payload, port
            last_error = f"port {port} code={payload.get('code')} msg={payload.get('msg')}"
        except Exception as exc:
            last_error = f"port {port} {type(exc).__name__}: {str(exc)[:120]}"
    raise RuntimeError(last_error)


def fetch_okx_futures_desktop_proxy() -> tuple[list[dict[str, Any]], str]:
    proxy_mode = env_value("OKX_DESKTOP_PROXY_ENABLED", "auto").lower()
    if proxy_mode in {"0", "false", "no", "off"}:
        return [], "桌面端代理已关闭"

    params = okx_futures_rank_params()
    payload, port = okx_desktop_proxy_get("/priapi/v5/rubik/web/futures/ranking", params)
    rows = parse_okx_rank_rows(okx_items_from_payload(payload), tag="桌面端合约热榜")
    if rows:
        return rows, f"桌面端本地代理 127.0.0.1:{port} futures/ranking 合约组 {params['type']}"

    overview_payload, overview_port = okx_desktop_proxy_get(
        "/priapi/v5/rubik/web/futures/all-rank-list",
        {"type": params["type"], "zone": params["zone"]},
    )
    overview_rows = parse_okx_rank_rows(okx_items_from_payload(overview_payload), tag="桌面端合约总览热榜")
    return overview_rows, f"桌面端本地代理 127.0.0.1:{overview_port} all-rank-list 合约组 {params['type']}"


def fetch_okx_futures_ranking_api() -> tuple[list[dict[str, Any]], str]:
    params = okx_futures_rank_params()
    payload = requests.get(
        "https://www.okx.com/priapi/v5/rubik/web/futures/ranking",
        params=params,
        headers=okx_web_headers(),
        timeout=18,
    ).json()
    rows = parse_okx_rank_rows(okx_items_from_payload(payload), tag="官网合约热榜")
    return rows, f"REST ranking 合约组 {params['type']}"


def fetch_okx_futures_all_rank_list() -> tuple[list[dict[str, Any]], str]:
    params = {
        "type": env_value("OKX_FUTURES_TYPE", "USDT"),
        "zone": env_value("OKX_RANK_ZONE", "utc24"),
    }
    payload = requests.get(
        "https://www.okx.com/priapi/v5/rubik/web/futures/all-rank-list",
        params=params,
        headers=okx_web_headers(),
        timeout=18,
    ).json()
    rows = parse_okx_rank_rows(okx_items_from_payload(payload), tag="官网合约总览热榜")
    return rows, f"REST all-rank-list 合约组 {params['type']}"


def fetch_okx_futures_ws() -> tuple[list[dict[str, Any]], str]:
    if env_value("OKX_ENABLE_WS", "0") != "1":
        return [], "WebSocket 已关闭"
    try:
        import ssl
        import websocket
    except Exception:
        return [], "WebSocket 依赖不可用"

    headers = okx_web_headers()
    ws_headers = [
        f"User-Agent: {headers.get('User-Agent', HEADERS['User-Agent'])}",
        "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control: no-cache",
        "Pragma: no-cache",
    ]
    if cookie := headers.get("Cookie"):
        ws_headers.append(f"Cookie: {cookie}")
    for name, value in headers.items():
        if name.lower() in {"authorization", "token", "x-cdn-token", "devid"}:
            ws_headers.append(f"{name}: {value}")

    urls = [
        item.strip()
        for item in env_value(
            "OKX_WS_URLS",
            "wss://wspri.okx.com/ws/v5/ipublic,wss://wspri.okx.com:8443/ws/v5/ipublic,wss://wspap.okx.com/ws/v5/ipublic",
        ).split(",")
        if item.strip()
    ]
    futures_type = env_value("OKX_FUTURES_TYPE", "USDT")
    last_error = ""
    for url in urls:
        try:
            ws = websocket.create_connection(
                url,
                timeout=10,
                header=ws_headers,
                origin="https://www.okx.com",
                sslopt={"cert_reqs": ssl.CERT_NONE},
            )
            ws.settimeout(6)
            ws.send(
                json.dumps(
                    {
                        "op": "subscribe",
                        "args": [
                            {
                                "channel": "futures-hot-rank-1D",
                                "ccy": futures_type,
                            }
                        ],
                    },
                    separators=(",", ":"),
                )
            )
            for _ in range(6):
                message = ws.recv()
                if isinstance(message, bytes):
                    message = message.decode("utf-8", errors="ignore")
                payload = json.loads(message)
                rows = parse_okx_rank_rows(payload.get("data") or [], tag="WS 合约热榜")
                if rows:
                    ws.close()
                    return rows, f"WebSocket futures-hot-rank-1D {futures_type}"
            ws.close()
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            continue
    return [], f"WebSocket 无数据/连接失败（{last_error or 'empty'}）"


def fetch_okx() -> dict[str, Any]:
    product_type = env_value("OKX_PRODUCT_TYPE", "futures").lower()
    if product_type == "spot":
        return fetch_okx_spot_hot()

    fetchers = [fetch_okx_futures_ssr_rank, fetch_okx_futures_ranking_api, fetch_okx_futures_all_rank_list]
    if env_value("OKX_ENABLE_WS", "0") == "1":
        fetchers.append(fetch_okx_futures_ws)
    if env_value("OKX_DESKTOP_PROXY_ENABLED", "auto").lower() not in {"0", "false", "no", "off"}:
        fetchers.append(fetch_okx_futures_desktop_proxy)
    if env_value("OKX_ALLOW_ESTIMATED_HOT_FALLBACK", "0") == "1":
        fetchers.append(fetch_okx_futures_hot_from_public_tickers)

    attempts: list[str] = []
    best_rows: list[dict[str, Any]] = []
    best_source_detail = ""
    for fetcher in fetchers:
        try:
            rows, source_detail = fetcher()
        except Exception as exc:
            attempts.append(f"{fetcher.__name__}: {type(exc).__name__}")
            continue
        attempts.append(source_detail)
        if rows:
            if len(rows) > len(best_rows):
                best_rows = rows
                best_source_detail = source_detail
            if len(rows) >= 10:
                save_okx_futures_cache(rows, source_detail)
                return source_template(
                    id="okx",
                    group="crypto",
                    title="OKX 合约热门榜",
                    subtitle=f"官网真实合约热门榜，{source_detail}",
                    accent="#f1f0e8",
                    source_label="OK",
                    source_name=f"OKX {source_detail}",
                    rows=rows[:10],
                )

    cached_rows, cached_source_detail, cached_at = load_okx_futures_cache()
    if best_rows:
        merged_rows = merge_rank_rows(best_rows, cached_rows, 10)
        if len(merged_rows) >= 10:
            return source_template(
                id="okx",
                group="crypto",
                title="OKX 合约热门榜",
                subtitle=f"实时 OKX 源只返回 {len(best_rows)} 条，已用最近完整快照补齐；实时源：{best_source_detail}",
                accent="#f1f0e8",
                source_label="OK",
                source_name=f"OKX mixed - {best_source_detail}",
                rows=merged_rows,
                status="stale",
            )
        if len(best_rows) > len(cached_rows):
            return source_template(
                id="okx",
                group="crypto",
                title="OKX 合约热门榜",
                subtitle=f"OKX 实时源暂时只返回 {len(best_rows)} 条，等待下一轮官方源补齐；{best_source_detail}",
                accent="#f1f0e8",
                source_label="OK",
                source_name=f"OKX partial - {best_source_detail}",
                rows=best_rows,
                status="partial",
            )

    if cached_rows:
        source = source_template(
            id="okx",
            group="crypto",
            title="OKX 合约热门榜",
            subtitle=f"上一次真实 OKX 合约热门榜快照。实时尝试：{'; '.join(attempts)}",
            accent="#f1f0e8",
            source_label="OK",
            source_name=f"OKX cached - {cached_source_detail}",
            rows=cached_rows,
            status="stale",
        )
        if cached_at:
            source["updatedAt"] = cached_at
        source["cacheOnly"] = True
        return source

    cookie_hint = "已带网页 Cookie/Token。" if env_value("OKX_WEB_COOKIE") or env_value("OKX_WEB_TOKEN") else "未配置 OKX_WEB_COOKIE / OKX_WEB_TOKEN。"
    attempt_summary = "；".join(attempts)
    return source_template(
        id="okx",
        group="crypto",
        title="OKX 合约热门榜",
        subtitle="只展示官网合约热榜真实源，不使用未登录现货榜兜底",
        accent="#f1f0e8",
        source_label="OK",
        source_name="OKX futures rankings",
        rows=[],
        status="unavailable",
        empty_title="OKX 合约热门榜当前拿不到真实数据",
        empty_message=(
            "已尝试 OKX 桌面端本地代理、futures/ranking、futures/all-rank-list 和 futures-hot-rank-1D websocket。"
            f"{cookie_hint} 当前返回空或连接被重置，因此已停止用现货热榜替代。尝试记录：{attempt_summary}"
        ),
    )


def fetch_okx_spot_hot() -> dict[str, Any]:
    params = {
        "type": env_value("OKX_SPOT_TYPE", "USDT"),
        "countryFilter": env_value("OKX_COUNTRY_FILTER", "1"),
        "num": env_value("OKX_SPOT_PAGE_SIZE", "10"),
    }
    payload = requests.get(
        "https://www.okx.com/priapi/v5/rubik/web/public/hot-rank",
        params=params,
        headers=okx_web_headers(),
        timeout=18,
    ).json()
    data_block = payload.get("data") or {}
    items = data_block.get("data") if isinstance(data_block, dict) else data_block
    items = items or []
    max_hot = max([safe_float(item.get("hotIndex")) for item in items] or [1])
    rows = []
    for rank, item in enumerate(items, 1):
        symbol = item.get("ccyV2") or item.get("instId", "").replace("-USDT", "")
        if not symbol or is_excluded_crypto_asset(symbol):
            continue
        last = safe_float(item.get("lastPrice") or item.get("last"))
        change = safe_float(item.get("changePerDay24h") or item.get("changePerV2") or item.get("changePer")) * 100
        turnover = safe_float(item.get("turnOver24h") or item.get("turnOverV2") or item.get("quoteVolume24h"))
        hot_index = safe_float(item.get("hotIndex") or (len(items) - rank + 1))
        rows.append(
            {
                "rank": len(rows) + 1,
                "symbol": symbol,
                "name": item.get("instId") or f"{symbol}-USDT",
                "icon": item.get("icon") or item.get("logoUrl") or item.get("coinIcon") or crypto_icon_url(symbol),
                "price": price_usd(last),
                "change": pct(change),
                "heat": max(1, min(100, round(hot_index / max(max_hot, 1) * 100))),
                "amount": turnover,
                "turnover": money_usd(turnover) if turnover else "官网热榜",
                "tags": ["官方热门榜", "OKX 现货"],
                "note": f"最新价 {last:g}" if last else "OKX 现货热榜",
            }
        )
        if len(rows) >= 10:
            break

    return source_template(
        id="okx",
        group="crypto",
        title="OKX 现货热门榜",
        subtitle=f"官网 spot/hot-crypto 热门榜，计价 {params['type']}",
        accent="#f1f0e8",
        source_label="OK",
        source_name="OKX spot hot-rank",
        rows=rows,
        status="ok" if rows else "unavailable",
        empty_title="OKX 现货热门榜当前为空",
        empty_message="已请求 OKX 官方 /priapi/v5/rubik/web/public/hot-rank，但没有返回可展示数据。",
    )


def okx_dex_cell_text(cell: Any, separator: str = " ") -> str:
    return re.sub(r"\s+", separator, " ".join(str(item).strip() for item in cell.stripped_strings if str(item).strip())).strip()


def okx_dex_first_percent(value: str) -> str:
    match = re.search(r"[-+]?\d[\d,.]*\s*%", value or "")
    return match.group(0).replace(" ", "") if match else ""


def okx_dex_chain_from_href(href: str) -> str:
    path = urlparse(href or "").path
    parts = [part for part in path.split("/") if part]
    try:
        token_index = parts.index("token")
        return parts[token_index + 1] if len(parts) > token_index + 1 else ""
    except ValueError:
        return ""


def fetch_okx_dex_hot_live(max_rows: int = 10) -> dict[str, Any]:
    url = "https://web3.okx.com/zh-hans/token?ct=30&pt=4"
    headers = {
        **HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://web3.okx.com/zh-hans/token",
    }
    html_text = requests.get(url, headers=headers, timeout=20).text
    soup = BeautifulSoup(html_text, "lxml")
    rows: list[dict[str, Any]] = []
    table_rows = soup.select("tr.dex-table-row")
    if not table_rows:
        table_rows = soup.select("tr")

    for item in table_rows:
        cells = item.select("td")
        if len(cells) < 8:
            continue
        link = cells[0].select_one('a[href*="/token/"]')
        if not link:
            continue
        href = urljoin("https://web3.okx.com", link.get("href") or "")
        link_parts = [part.strip() for part in link.stripped_strings if part.strip()]
        symbol = link_parts[0] if link_parts else okx_dex_cell_text(cells[0]).split(" ")[0]
        asset = normalize_asset_symbol(symbol)
        if not symbol or is_excluded_crypto_asset(asset):
            continue

        img = cells[0].select_one("img[src]")
        icon = urljoin("https://web3.okx.com", img.get("src")) if img else crypto_icon_url(symbol)
        name_text = okx_dex_cell_text(cells[0])
        age = ""
        address = ""
        if len(link_parts) >= 2:
            age = link_parts[1]
        if len(link_parts) >= 3:
            address = link_parts[2]
        chain = okx_dex_chain_from_href(href)
        market_cap_text = okx_dex_cell_text(cells[1])
        price_text = okx_dex_cell_text(cells[2])
        holders_text = okx_dex_cell_text(cells[4])
        liquidity_text = okx_dex_cell_text(cells[5])
        tx_text = okx_dex_cell_text(cells[6])
        volume_text = okx_dex_cell_text(cells[7])
        net_flow_text = okx_dex_cell_text(cells[8]) if len(cells) > 8 else ""
        risk_text = okx_dex_cell_text(cells[9]) if len(cells) > 9 else ""
        change = okx_dex_first_percent(market_cap_text) or okx_dex_first_percent(volume_text)
        tag_parts = ["OKX DEX", "24小时"]
        if chain:
            tag_parts.append(chain)
        note_parts = [
            f"市值 {market_cap_text}" if market_cap_text else "",
            f"流动性 {liquidity_text}" if liquidity_text else "",
            f"交易 {tx_text}" if tx_text else "",
            f"净流入 {net_flow_text}" if net_flow_text else "",
            risk_text,
        ]
        rows.append(
            {
                "rank": len(rows) + 1,
                "symbol": symbol,
                "name": " · ".join(part for part in [age, address] if part) or name_text,
                "icon": icon,
                "icons": [icon] if icon else [],
                "price": price_text or "--",
                "change": change or "--",
                "heat": max(1, round((len(table_rows) - len(rows)) / max(len(table_rows), 1) * 100)),
                "amount": site_amount_from_text(volume_text),
                "turnover": volume_text or "--",
                "tags": tag_parts,
                "note": " · ".join(part for part in note_parts if part),
                "url": href,
            }
        )
        if len(rows) >= max_rows:
            break

    return source_template(
        id="okx-dex",
        group="crypto",
        title="OKX DEX 24小时热门榜",
        subtitle="OKX Wallet token 榜单 ct=30&pt=4，已过滤 BTC / ETH / BNB 与稳定币",
        accent="#b5ff2d",
        source_label="DEX",
        source_name="OKX Wallet token?ct=30&pt=4",
        rows=rows,
        status="ok" if rows else "unavailable",
        empty_title="OKX DEX 24小时热门榜当前为空",
        empty_message="已请求 OKX Wallet token 页面，但没有解析到可展示的 24小时热门榜数据。",
    )


def fetch_okx_dex_hot(max_rows: int = 10) -> dict[str, Any]:
    try:
        source = fetch_okx_dex_hot_live(max_rows=max_rows)
        rows = source.get("rows") if isinstance(source.get("rows"), list) else []
        valid_rows = [row for row in rows if safe_float(row.get("amount")) > 0]
        if len(valid_rows) < min(5, max_rows):
            raise RuntimeError(f"OKX DEX 解析结果不完整，仅 {len(valid_rows)} 条有效成交额")
        write_json_cache(OKX_DEX_SOURCE_CACHE_PATH, {**source, "cachedAt": int(time.time() * 1000)})
        return source
    except Exception as exc:
        fallback = cached_source_fallback("okx-dex", OKX_DEX_SOURCE_CACHE_PATH)
        if fallback:
            rows = fallback.get("rows") if isinstance(fallback.get("rows"), list) else []
            fallback["rows"] = rows[:max_rows]
            fallback["subtitle"] = "OKX Wallet token 榜单 ct=30&pt=4，上一次成功数据"
            fallback["sourceName"] = f"{fallback.get('sourceName') or 'OKX Wallet token?ct=30&pt=4'} · 接口短暂失败：{alert_text(exc, 48)}"
            return fallback
        raise


def fetch_bitget() -> dict[str, Any]:
    index = env_value("BITGET_HOT_INDEX", "8")
    payload = requests.get(
        "https://www.bitget.com/v1/mix/market/indexLeaderboard-get",
        params={"index": index},
        headers=bitget_web_headers(),
        timeout=18,
    ).json()
    items = payload.get("data") or []
    ticker_payload = requests.get(
        "https://api.bitget.com/api/v2/spot/market/tickers",
        headers={"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"},
        timeout=18,
    ).json()
    tickers = {item.get("symbol"): item for item in ticker_payload.get("data", [])}
    rows = []
    ranked = sorted(items, key=lambda item: safe_float(item.get("sort"), 9999))
    for item in ranked:
        symbol_id = str(item.get("symbolId") or "")
        pair = re.sub(r"_(SPBL|UMCBL|DMCBL|CMCBL)$", "", symbol_id)
        asset = pair[:-4] if pair.endswith("USDT") else pair
        if not pair or not asset or is_excluded_crypto_asset(asset):
            continue
        ticker = tickers.get(pair) or {}
        volume = safe_float(ticker.get("usdtVolume") or ticker.get("quoteVolume"))
        last = safe_float(ticker.get("lastPr"))
        rows.append(
            {
                "rank": len(rows) + 1,
                "symbol": asset,
                "name": pair,
                "icon": item.get("icon") or item.get("coinIcon") or item.get("logoUrl") or crypto_icon_url(asset),
                "price": price_usd(last),
                "change": pct(safe_float(ticker.get("change24h")) * 100),
                "heat": max(1, round((len(ranked) - safe_float(item.get("sort"), len(ranked))) / max(len(ranked) - 1, 1) * 99 + 1)),
                "amount": volume,
                "turnover": money_usd(volume) if volume else "官网热榜",
                "tags": ["官网热门榜", "Bitget"],
                "note": f"最新价 {last:g}" if last else "Bitget 热门榜",
            }
        )
        if len(rows) >= 10:
            break

    return source_template(
        id="bitget",
        group="crypto",
        title="Bitget 热门榜",
        subtitle=f"官网 indexLeaderboard-get 热门榜 index={index}，已过滤稳定币",
        accent="#35d2ff",
        source_label="BG",
        source_name="Bitget indexLeaderboard-get",
        rows=rows,
        status="ok" if rows else "unavailable",
        empty_title="Bitget 热门榜当前为空",
        empty_message="已请求 Bitget 官方 indexLeaderboard-get 接口，但没有返回可展示数据。",
    )


def fetch_binance_gainers() -> dict[str, Any]:
    headers = {
        **HEADERS,
        "Accept": "application/json",
        "clienttype": "web",
        "lang": "zh-CN",
        "bnc-location": "CN",
        "Referer": "https://www.binance.com/zh-CN/markets/trading_data/rankings",
    }
    payload = requests.get(
        "https://www.binance.com/bapi/asset/v2/public/asset-service/product/get-products?includeEtf=true",
        headers=headers,
        timeout=20,
    ).json()
    candidates: list[dict[str, Any]] = []
    for item in payload.get("data", []) or []:
        if not isinstance(item, dict) or item.get("q") != "USDT":
            continue
        asset = item.get("b")
        if not asset or is_excluded_crypto_asset(asset):
            continue
        last = safe_float(item.get("c"))
        open_price = safe_float(item.get("o"))
        change = change_from_open(last, open_price)
        quote_volume = safe_float(item.get("qv"))
        if change <= 0:
            continue
        candidates.append(
            {
                "symbol": asset,
                "pair": item.get("s") or f"{asset}USDT",
                "name": item.get("an") or asset,
                "last": last,
                "change": change,
                "amount": quote_volume,
                "icon": item.get("logoUrl") or crypto_icon_url(asset),
            }
        )
    candidates.sort(key=lambda item: (safe_float(item.get("change")), safe_float(item.get("amount"))), reverse=True)
    rows = []
    for item in candidates[:10]:
        rows.append(
            {
                "rank": len(rows) + 1,
                "symbol": item["symbol"],
                "name": item["pair"],
                "icon": item["icon"],
                "price": price_usd(item["last"]),
                "change": pct(item["change"]),
                "heat": max(1, round((len(candidates[:10]) - len(rows)) / max(len(candidates[:10]), 1) * 100)),
                "amount": item["amount"],
                "turnover": money_usd(item["amount"]) if item["amount"] else "--",
                "tags": ["Binance", "24h 涨幅榜"],
                "note": item["name"],
                "url": f"https://www.binance.com/zh-CN/trade/{item['symbol']}_USDT?type=spot",
            }
        )
    return source_template(
        id="binance-gainers",
        group="crypto",
        title="Binance 涨幅榜",
        subtitle="官网 trading_data/rankings 口径，USDT 交易对按 24h 涨幅排序，已过滤 BTC / ETH / BNB / 稳定币",
        accent="#f4b740",
        source_label="BN",
        source_name="Binance get-products 24h gainers",
        rows=rows,
        status="ok" if rows else "unavailable",
        empty_title="Binance 涨幅榜当前为空",
        empty_message="已请求 Binance 官网产品行情，但没有得到可展示的正涨幅 USDT 交易对。",
    )


def fetch_okx_futures_gainers_ranking_api() -> tuple[list[dict[str, Any]], str]:
    params = okx_futures_rank_params("gainers")
    payload = requests.get(
        "https://www.okx.com/priapi/v5/rubik/web/futures/ranking",
        params=params,
        headers=okx_web_headers(),
        timeout=18,
    ).json()
    rows = parse_okx_rank_rows(okx_items_from_payload(payload), tag="OKX 合约涨幅榜")
    return rows, f"REST futures/ranking rankType=gainers {params['type']}"


def fetch_okx_futures_gainers_from_public_tickers() -> tuple[list[dict[str, Any]], str]:
    payload = requests.get(
        "https://www.okx.com/api/v5/market/tickers",
        params={"instType": "SWAP"},
        headers={"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"},
        timeout=18,
    ).json()
    candidates: list[dict[str, Any]] = []
    for item in payload.get("data", []) or []:
        if not isinstance(item, dict):
            continue
        inst_id = str(item.get("instId") or "")
        if not inst_id.endswith("-USDT-SWAP"):
            continue
        asset = inst_id.split("-")[0]
        if not asset or is_excluded_crypto_asset(asset):
            continue
        last = safe_float(item.get("last"))
        open_price = safe_float(item.get("open24h"))
        change = change_from_open(last, open_price)
        if change <= 0:
            continue
        turnover = last * safe_float(item.get("volCcy24h")) if last else 0
        candidates.append(
            {
                "asset": asset,
                "instId": inst_id,
                "last": last,
                "change": change,
                "turnover": turnover,
            }
        )
    candidates.sort(key=lambda item: (safe_float(item.get("change")), safe_float(item.get("turnover"))), reverse=True)
    rows = []
    top = candidates[:10]
    for item in top:
        rows.append(
            {
                "rank": len(rows) + 1,
                "symbol": item["asset"],
                "name": item["instId"],
                "icon": crypto_icon_url(item["asset"]),
                "price": price_usd(item["last"]),
                "change": pct(item["change"]),
                "heat": max(1, round((len(top) - len(rows)) / max(len(top), 1) * 100)),
                "amount": item["turnover"],
                "turnover": money_usd(item["turnover"]) if item["turnover"] else "--",
                "tags": ["OKX", "合约涨幅榜"],
                "note": "public market tickers 24h gainers",
                "url": f"https://www.okx.com/zh-hans/trade-swap/{item['instId'].lower()}",
            }
        )
    return rows, "OKX public market tickers SWAP 24h gainers"


def fetch_okx_gainers() -> dict[str, Any]:
    attempts: list[str] = []
    for fetcher in (fetch_okx_futures_gainers_ranking_api, fetch_okx_futures_gainers_from_public_tickers):
        try:
            rows, source_detail = fetcher()
        except Exception as exc:
            attempts.append(f"{fetcher.__name__}: {type(exc).__name__}")
            continue
        attempts.append(source_detail)
        if rows:
            return source_template(
                id="okx-gainers",
                group="crypto",
                title="OKX 合约涨幅榜",
                subtitle=f"优先请求官网 futures/ranking rankType=gainers；{source_detail}",
                accent="#f1f0e8",
                source_label="OK",
                source_name=f"OKX {source_detail}",
                rows=rows,
            )
    return source_template(
        id="okx-gainers",
        group="crypto",
        title="OKX 合约涨幅榜",
        subtitle="OKX 官方合约涨幅榜当前没有返回可展示数据",
        accent="#f1f0e8",
        source_label="OK",
        source_name="OKX futures gainers",
        rows=[],
        status="unavailable",
        empty_title="OKX 合约涨幅榜当前为空",
        empty_message="; ".join(attempts)[:180],
    )


def fetch_okx_turnover() -> dict[str, Any]:
    attempts: list[str] = []
    try:
        rows, source_detail = fetch_okx_futures_turnover_from_public_tickers()
        attempts.append(source_detail)
    except Exception as exc:
        rows = []
        attempts.append(f"fetch_okx_futures_turnover_from_public_tickers: {type(exc).__name__}")
    if rows:
        return source_template(
            id="okx-turnover",
            group="crypto",
            title="OKX 合约成交额榜",
            subtitle=f"OKX 官方 SWAP 公开行情按 24h 成交额独立排序，{source_detail}",
            accent="#f1f0e8",
            source_label="OK",
            source_name=f"OKX {source_detail}",
            rows=rows,
        )
    return source_template(
        id="okx-turnover",
        group="crypto",
        title="OKX 合约成交额榜",
        subtitle="OKX 官方 SWAP 公开行情当前没有返回可展示数据",
        accent="#f1f0e8",
        source_label="OK",
        source_name="OKX public market tickers SWAP turnover",
        rows=[],
        status="unavailable",
        empty_title="OKX 合约成交额榜当前为空",
        empty_message="; ".join(attempts)[:180],
    )


def fetch_bitget_gainers() -> dict[str, Any]:
    index = env_value("BITGET_GAINERS_INDEX", "1")
    payload = requests.get(
        "https://www.bitget.com/v1/mix/market/indexLeaderboard-get",
        params={"index": index},
        headers=bitget_web_headers(),
        timeout=18,
    ).json()
    rows = []
    for item in payload.get("data", []) or []:
        if not isinstance(item, dict):
            continue
        raw_asset = str(item.get("baseSymbol") or "")
        pair = str(item.get("symbolCode") or item.get("symbolId") or "")
        pair = re.sub(r"_(SPBL|UMCBL|DMCBL|CMCBL)$", "", pair)
        pair = normalize_asset_symbol(pair)
        asset = normalize_asset_symbol(raw_asset)
        if not asset and pair.endswith("USDT"):
            asset = pair[:-4]
        if not pair and asset:
            pair = f"{asset}USDT"
        if not asset or is_excluded_crypto_asset(asset):
            continue
        change = safe_float(item.get("rose") or item.get("roseU8"))
        if change <= 0:
            continue
        last = safe_float(item.get("last"))
        volume = safe_float(item.get("vol") or item.get("volume"))
        turnover = last * volume if last and volume else safe_float(item.get("marketValue"))
        rows.append(
            {
                "rank": len(rows) + 1,
                "symbol": asset.upper(),
                "name": pair,
                "icon": item.get("imgUrl") or item.get("icon") or item.get("coinIcon") or crypto_icon_url(asset),
                "price": price_usd(last),
                "change": pct(change),
                "heat": max(1, round((10 - len(rows)) / 10 * 100)),
                "amount": turnover,
                "turnover": money_usd(turnover) if turnover else "--",
                "tags": ["Bitget", f"index={index}", "涨幅榜"],
                "note": item.get("coinName") or "Bitget indexLeaderboard-get",
                "url": f"https://www.bitget.com/zh-CN/spot/{pair}",
            }
        )
        if len(rows) >= 10:
            break
    return source_template(
        id="bitget-gainers",
        group="crypto",
        title="Bitget 涨幅榜",
        subtitle=f"官网 indexLeaderboard-get 涨幅榜 index={index}，已过滤 BTC / ETH / BNB / 稳定币",
        accent="#35d2ff",
        source_label="BG",
        source_name=f"Bitget indexLeaderboard-get index={index}",
        rows=rows,
        status="ok" if rows else "unavailable",
        empty_title="Bitget 涨幅榜当前为空",
        empty_message="已请求 Bitget 官方 indexLeaderboard-get 涨幅榜接口，但没有返回可展示数据。",
    )


def fetch_okx_dex_gainers() -> dict[str, Any]:
    source = fetch_okx_dex_hot(max_rows=40)
    rows = list(source.get("rows") or [])
    rows.sort(key=lambda row: safe_float(str(row.get("change") or "0").replace("%", "").replace("+", "").replace(",", "")), reverse=True)
    rows = [row for row in rows if safe_float(str(row.get("change") or "0").replace("%", "").replace("+", "").replace(",", "")) > 0][:10]
    for index, row in enumerate(rows, 1):
        row["rank"] = index
        row["tags"] = list(dict.fromkeys([*(row.get("tags") or []), "OKX DEX 涨幅榜"]))
    source.update(
        {
            "id": "okx-dex-gainers",
            "title": "OKX DEX 24h 涨幅榜",
            "subtitle": "OKX Wallet token 24小时链上榜单，按涨幅排序，已过滤 BTC / ETH / BNB / 稳定币",
            "sourceName": "OKX Wallet token 24h gainers",
            "rows": rows,
            "status": "ok" if rows else "unavailable",
            "emptyTitle": "OKX DEX 24h 涨幅榜当前为空",
            "emptyMessage": "已请求 OKX Wallet token 页面，但没有解析到可展示的正涨幅链上数据。",
        }
    )
    return source


def fetch_binance_new_coins() -> dict[str, Any]:
    exchange_payload = requests.get(
        "https://fapi.binance.com/fapi/v1/exchangeInfo",
        headers={"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"},
        timeout=18,
    ).json()
    ticker_payload = requests.get(
        "https://fapi.binance.com/fapi/v1/ticker/24hr",
        headers={"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"},
        timeout=18,
    ).json()
    tickers = {item.get("symbol"): item for item in ticker_payload if isinstance(item, dict)}
    symbols = [
        item
        for item in exchange_payload.get("symbols", [])
        if item.get("status") == "TRADING"
        and item.get("quoteAsset") == "USDT"
        and not is_excluded_crypto_asset(item.get("baseAsset"))
    ]
    symbols.sort(key=lambda item: safe_float(item.get("onboardDate")), reverse=True)
    rows = []
    for item in symbols:
        pair = str(item.get("symbol") or "")
        asset = str(item.get("baseAsset") or pair.removesuffix("USDT"))
        if not pair or not asset:
            continue
        ticker = tickers.get(pair) or {}
        last = safe_float(ticker.get("lastPrice"))
        quote_volume = safe_float(ticker.get("quoteVolume"))
        onboard_ms = int(safe_float(item.get("onboardDate")))
        is_tradfi = "TRADIFI" in str(item.get("contractType") or "").upper()
        rows.append(
            {
                "rank": len(rows) + 1,
                "group": "new-coin",
                "asset": asset,
                "symbol": pair,
                "name": pair,
                "icon": "",
                "iconCandidates": exchange_asset_icon_candidates(asset, prefer_us_stock=is_tradfi),
                "price": price_usd(last),
                "change": pct(safe_float(ticker.get("priceChangePercent"))),
                "amount": quote_volume,
                "turnover": money_usd(quote_volume) if quote_volume else "--",
                "date": onboard_ms,
                "dateLabel": date_yyyy_mm_dd(onboard_ms),
                "status": "永续",
                "tags": ["永续", "TradFi" if is_tradfi else "新合约"],
                "url": f"https://www.binance.com/zh-CN/futures/{pair}",
            }
        )
        if len(rows) >= 10:
            break
    return source_template(
        id="binance-new",
        group="new-coin",
        title="Binance 新币榜",
        subtitle="USDⓈ-M Futures 官方 exchangeInfo onboardDate 排序",
        accent="#f4b740",
        source_label="BN",
        source_name="Binance Futures exchangeInfo",
        rows=rows,
        status="ok" if rows else "unavailable",
        empty_title="Binance 新币榜当前为空",
        empty_message="已请求 Binance USDⓈ-M Futures exchangeInfo，但没有返回可展示的新合约数据。",
    )


def fetch_okx_new_coins() -> dict[str, Any]:
    headers = {"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"}
    instruments_payload = requests.get(
        "https://www.okx.com/api/v5/public/instruments",
        params={"instType": "SWAP"},
        headers=headers,
        timeout=18,
    ).json()
    ticker_payload = requests.get(
        "https://www.okx.com/api/v5/market/tickers",
        params={"instType": "SWAP"},
        headers=headers,
        timeout=18,
    ).json()
    tickers = {item.get("instId"): item for item in ticker_payload.get("data", [])}
    instruments = [
        item
        for item in instruments_payload.get("data", [])
        if item.get("state") == "live" and str(item.get("instId") or "").endswith("-USDT-SWAP")
    ]
    instruments.sort(key=lambda item: safe_float(item.get("listTime")), reverse=True)
    rows = []
    for item in instruments:
        inst_id = str(item.get("instId") or "")
        asset = inst_id.split("-")[0]
        if not asset or is_excluded_crypto_asset(asset):
            continue
        ticker = tickers.get(inst_id) or {}
        last = safe_float(ticker.get("last"))
        base_volume = safe_float(ticker.get("volCcy24h") or ticker.get("vol24h"))
        turnover = last * base_volume if last and base_volume else 0
        list_ms = int(safe_float(item.get("listTime")))
        pair = inst_id.replace("-", "").replace("SWAP", "")
        rows.append(
            {
                "rank": len(rows) + 1,
                "group": "new-coin",
                "asset": asset,
                "symbol": pair,
                "name": pair,
                "icon": "",
                "iconCandidates": exchange_asset_icon_candidates(asset, prefer_us_stock=True),
                "price": price_usd(last),
                "change": pct(change_from_open(last, ticker.get("open24h"))),
                "amount": turnover,
                "turnover": money_usd(turnover) if turnover else "--",
                "date": list_ms,
                "dateLabel": date_yyyy_mm_dd(list_ms),
                "status": "永续",
                "tags": ["永续", "OKX 新合约"],
                "url": f"https://www.okx.com/zh-hans/trade-swap/{inst_id.lower()}",
            }
        )
        if len(rows) >= 10:
            break
    return source_template(
        id="okx-new",
        group="new-coin",
        title="OKX 新币榜",
        subtitle="OKX 官方 public instruments listTime 排序，含最新价与 24h 涨跌",
        accent="#f1f0e8",
        source_label="OK",
        source_name="OKX public instruments",
        rows=rows,
        status="ok" if rows else "unavailable",
        empty_title="OKX 新币榜当前为空",
        empty_message="已请求 OKX 官方 public instruments / market tickers，但没有返回可展示的新合约数据。",
    )


def bitget_new_rows_from_index(index: str) -> list[dict[str, Any]]:
    payload = requests.get(
        "https://www.bitget.com/v1/mix/market/indexLeaderboard-get",
        params={"index": index},
        headers=bitget_web_headers(),
        timeout=18,
    ).json()
    items = payload.get("data") or []
    rows = []
    for item in sorted(items, key=lambda value: safe_float(value.get("sort"), 9999) or 9999):
        asset = str(item.get("baseSymbol") or "")
        pair = str(item.get("symbolCode") or item.get("symbolId") or "")
        pair = re.sub(r"_(SPBL|UMCBL|DMCBL|CMCBL)$", "", pair)
        if not pair and asset:
            pair = f"{asset}USDT"
        if not asset and pair.endswith("USDT"):
            asset = pair[:-4]
        if not asset or is_excluded_crypto_asset(asset):
            continue
        last = safe_float(item.get("last"))
        volume = safe_float(item.get("vol") or item.get("volume"))
        turnover = last * volume if last and volume else safe_float(item.get("marketValue"))
        open_ms = int(safe_float(item.get("openTime")))
        rows.append(
            {
                "rank": len(rows) + 1,
                "group": "new-coin",
                "asset": asset,
                "symbol": pair,
                "name": pair,
                "icon": item.get("imgUrl") or "",
                "iconCandidates": exchange_asset_icon_candidates(asset),
                "price": price_usd(last),
                "change": pct(safe_float(item.get("rose") or item.get("roseU8"))),
                "amount": turnover,
                "turnover": money_usd(turnover) if turnover else "--",
                "date": open_ms,
                "dateLabel": date_yyyy_mm_dd(open_ms),
                "status": "现货",
                "tags": ["现货", "新币榜"],
                "url": f"https://www.bitget.com/zh-CN/spot/{pair}",
            }
        )
        if len(rows) >= 10:
            break
    return rows


def bitget_new_rows_from_symbols() -> list[dict[str, Any]]:
    symbol_payload = requests.get(
        "https://api.bitget.com/api/v2/spot/public/symbols",
        headers={"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"},
        timeout=18,
    ).json()
    ticker_payload = requests.get(
        "https://api.bitget.com/api/v2/spot/market/tickers",
        headers={"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"},
        timeout=18,
    ).json()
    tickers = {item.get("symbol"): item for item in ticker_payload.get("data", [])}
    symbols = [
        item
        for item in symbol_payload.get("data", [])
        if item.get("status") == "online"
        and item.get("quoteCoin") == "USDT"
        and not is_excluded_crypto_asset(item.get("baseCoin"))
    ]
    symbols.sort(key=lambda item: safe_float(item.get("openTime")), reverse=True)
    rows = []
    for item in symbols:
        pair = str(item.get("symbol") or "")
        asset = str(item.get("baseCoin") or pair.removesuffix("USDT"))
        ticker = tickers.get(pair) or {}
        last = safe_float(ticker.get("lastPr"))
        turnover = safe_float(ticker.get("usdtVolume") or ticker.get("quoteVolume"))
        open_ms = int(safe_float(item.get("openTime")))
        rows.append(
            {
                "rank": len(rows) + 1,
                "group": "new-coin",
                "asset": asset,
                "symbol": pair,
                "name": pair,
                "icon": "",
                "iconCandidates": exchange_asset_icon_candidates(asset),
                "price": price_usd(last),
                "change": pct(safe_float(ticker.get("change24h")) * 100),
                "amount": turnover,
                "turnover": money_usd(turnover) if turnover else "--",
                "date": open_ms,
                "dateLabel": date_yyyy_mm_dd(open_ms),
                "status": "现货",
                "tags": ["现货", "openTime"],
                "url": f"https://www.bitget.com/zh-CN/spot/{pair}",
            }
        )
        if len(rows) >= 10:
            break
    return rows


def fetch_bitget_new_coins() -> dict[str, Any]:
    index = env_value("BITGET_NEW_INDEX", "3")
    rows = bitget_new_rows_from_index(index)
    source_name = f"Bitget indexLeaderboard-get index={index}"
    if not rows:
        rows = bitget_new_rows_from_symbols()
        source_name = "Bitget spot public symbols openTime"
    return source_template(
        id="bitget-new",
        group="new-coin",
        title="Bitget 新币榜",
        subtitle=f"官网新币榜 index={index}，失败时回退 spot public symbols openTime",
        accent="#35d2ff",
        source_label="BG",
        source_name=source_name,
        rows=rows,
        status="ok" if rows else "unavailable",
        empty_title="Bitget 新币榜当前为空",
        empty_message="已请求 Bitget 官网 indexLeaderboard-get 和 spot public symbols，但没有返回可展示的新币数据。",
    )


def new_coin_rankings_payload() -> dict[str, Any]:
    sources = []
    for key, fetcher in (
        ("binance-new", fetch_binance_new_coins),
        ("okx-new", fetch_okx_new_coins),
        ("bitget-new", fetch_bitget_new_coins),
    ):
        try:
            sources.append(cached(key, fetcher))
        except Exception as exc:
            sources.append(
                source_template(
                    id=key,
                    group="new-coin",
                    title=key,
                    subtitle="交易所新币榜请求失败",
                    accent="#777777",
                    source_label="ERR",
                    source_name=str(exc)[:120],
                    rows=[],
                    status="unavailable",
                    empty_title="新币榜请求失败",
                    empty_message=str(exc)[:180],
                )
            )
    return {"updatedAt": int(time.time() * 1000), "sections": sources}


def aicoin_price(value: Any) -> str:
    price = safe_float(value)
    if not price:
        return str(value or "--")
    if price >= 1000:
        return f"${price:,.2f}"
    if price >= 1:
        return f"${price:.4f}".rstrip("0").rstrip(".")
    return f"${price:.8f}".rstrip("0").rstrip(".")


def aicoin_app_data_dir() -> Path:
    if configured := env_value("AICOIN_USER_DATA_DIR"):
        return Path(configured)
    if appdata := os.getenv("APPDATA"):
        return Path(appdata) / "AiCoin"
    return Path.home() / "AppData" / "Roaming" / "AiCoin"


def aicoin_cookie_values() -> dict[str, str]:
    db_path = aicoin_app_data_dir() / "Network" / "Cookies"
    if not db_path.exists():
        return {}

    tmp_path = Path(tempfile.gettempdir()) / f"aicoin_cookies_{os.getpid()}_{int(time.time() * 1000)}.sqlite"
    values: dict[str, str] = {}
    try:
        tmp_path.write_bytes(db_path.read_bytes())
        connection = sqlite3.connect(str(tmp_path))
        try:
            cursor = connection.cursor()
            for _host, name, value in cursor.execute("select host_key,name,value from cookies"):
                if name in {"aicoin_token", "aicoin_session", "access_token", "refresh_token"} and value:
                    values[name] = value
        finally:
            connection.close()
    except Exception:
        return values
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    return values


def aicoin_token_candidates() -> list[str]:
    candidates: list[str] = []
    for name in ("AICOIN_TOKEN", "AICOIN_AUTH_TOKEN", "AICOIN_WEB_TOKEN"):
        if value := env_value(name):
            candidates.append(value)

    data_dir = aicoin_app_data_dir()
    token_patterns = [
        rb'"token"\s*:\s*"([^"\\]{20,1000})"',
        rb'token["\x00\x01:=, ]{1,16}([A-Za-z0-9+/_=\-.]{20,1000})',
    ]
    for root in (data_dir / "Local Storage" / "leveldb", data_dir / "IndexedDB" / "file__0.indexeddb.leveldb"):
        if not root.exists():
            continue
        for file_path in root.glob("*"):
            if file_path.suffix.lower() not in {".ldb", ".log"}:
                continue
            try:
                raw = file_path.read_bytes()
            except OSError:
                continue
            for pattern in token_patterns:
                for match in re.finditer(pattern, raw):
                    token = match.group(1).decode("utf-8", errors="ignore").strip()
                    if 20 <= len(token) <= 1000:
                        candidates.append(token)

    cookies = aicoin_cookie_values()
    for name in ("aicoin_token", "aicoin_session", "access_token"):
        if value := cookies.get(name):
            candidates.append(value)

    unique: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        if value and value not in seen:
            unique.append(value)
            seen.add(value)
    return unique


def aicoin_encrypted_headers() -> dict[str, str]:
    return {
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://www.aicoin.com",
        "Referer": "https://www.aicoin.com/",
        "compress": "1",
    }


def aicoin_encrypt_body(body: dict[str, Any]) -> tuple[dict[str, str], bytes, bytes]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7
    except Exception as exc:
        raise RuntimeError("missing cryptography package for AICoin encrypted API") from exc

    conn = requests.post(
        "https://vip-pcapi.aicoin.com/api/conn/load",
        json={"v": ""},
        headers=aicoin_encrypted_headers(),
        timeout=12,
    ).json()
    conn_data = conn.get("data") or {}
    public_key = conn_data.get("info")
    version = conn_data.get("v")
    if not public_key or not version:
        raise RuntimeError("AICoin conn/load did not return encryption metadata")

    key_seed = os.urandom(16)
    iv_seed = os.urandom(8)
    key_text = key_seed.hex()
    iv_text = iv_seed.hex()
    aes_key = key_text.encode("utf-8")
    aes_iv = iv_seed + (b"\x00" * 8)

    padder = PKCS7(128).padder()
    plain = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    padded = padder.update(plain) + padder.finalize()
    encryptor = Cipher(algorithms.AES(aes_key), modes.CBC(aes_iv)).encryptor()
    encrypted_body = encryptor.update(padded) + encryptor.finalize()

    public = serialization.load_pem_public_key(public_key.encode("utf-8"))
    encrypted_key = public.encrypt(key_text.encode("utf-8"), asym_padding.PKCS1v15())
    encrypted_iv = public.encrypt(iv_text.encode("utf-8"), asym_padding.PKCS1v15())
    return (
        {
            "p": base64.b64encode(encrypted_body).decode("ascii"),
            "k": base64.b64encode(encrypted_key).decode("ascii"),
            "v": str(version),
            "iv": base64.b64encode(encrypted_iv).decode("ascii"),
        },
        aes_key,
        aes_iv,
    )


def aicoin_decrypt_response(response: requests.Response, aes_key: bytes, aes_iv: bytes) -> Any:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7
    except Exception as exc:
        raise RuntimeError("missing cryptography package for AICoin encrypted API") from exc

    payload = response.json()
    encrypted_data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(encrypted_data, str):
        return payload

    decryptor = Cipher(algorithms.AES(aes_key), modes.CBC(aes_iv)).decryptor()
    raw = decryptor.update(base64.b64decode(encrypted_data)) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    raw = unpadder.update(raw) + unpadder.finalize()

    if response.headers.get("decompress"):
        try:
            raw = gzip.decompress(bytes.fromhex(raw.decode("ascii")))
        except Exception:
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass

    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


def aicoin_hot_items_from_root(root: Any) -> tuple[list[Any], int]:
    if isinstance(root, list):
        return root, len(root)
    if not isinstance(root, dict):
        return [], 0

    direct_list = root.get("list")
    if isinstance(direct_list, list):
        return direct_list, int(safe_float(root.get("count"), len(direct_list)))

    data = root.get("data")
    if isinstance(data, list):
        return data, len(data)
    if isinstance(data, dict):
        for key in ("list", "items", "records", "hot"):
            value = data.get(key)
            if isinstance(value, list):
                return value, int(safe_float(data.get("count") or data.get("total") or data.get("totalCount"), len(value)))
    return [], 0


def aicoin_response_message(root: Any) -> str:
    if not isinstance(root, dict):
        return type(root).__name__
    for key in ("message", "msg", "status", "code"):
        value = root.get(key)
        if value not in (None, ""):
            return str(value)[:120]
    return ",".join(list(root.keys())[:8])


def aicoin_rest_hot_payload() -> dict[str, Any]:
    size = int(safe_float(env_value("AICOIN_HOT_SIZE", "20"), 20))
    base_body = {
        "size": size,
        "page": 1,
        "currency": env_value("AICOIN_CURRENCY", "usd").lower(),
        "keyWord": "",
        "customGroupIds": [],
        "lan": env_value("AICOIN_LANGUAGE", "zh"),
        "pc_client": env_value("AICOIN_PC_CLIENT", "Windows x64"),
        "pc_client_version": env_value("AICOIN_PC_CLIENT_VERSION", "2.16.3"),
    }
    tokens = aicoin_token_candidates()
    if not tokens:
        raise RuntimeError("AICoin local authorized state was not found")

    cookies = {key: value for key, value in aicoin_cookie_values().items() if key in {"aicoin_token", "aicoin_session", "access_token"}}
    last_message = ""
    for token in tokens[: int(safe_float(env_value("AICOIN_TOKEN_TRY_LIMIT", "8"), 8))]:
        body = {**base_body, "token": token}
        request_body, aes_key, aes_iv = aicoin_encrypt_body(body)
        response = requests.post(
            "https://vip-pcapi.aicoin.com/api/upgrade/billboard/getHotCoinHour",
            json=request_body,
            headers=aicoin_encrypted_headers(),
            cookies=cookies,
            timeout=18,
        )
        response.raise_for_status()
        root = aicoin_decrypt_response(response, aes_key, aes_iv)
        items, count = aicoin_hot_items_from_root(root)
        if items:
            return {
                "ok": True,
                "endpoint": "POST https://vip-pcapi.aicoin.com/api/upgrade/billboard/getHotCoinHour",
                "body": base_body,
                "count": count,
                "list": items[:size],
                "source": "encrypted-rest",
            }
        last_message = aicoin_response_message(root)

    raise RuntimeError(f"AICoin encrypted API returned no hot rows ({last_message or 'empty response'})")


def aicoin_unavailable(message: str) -> dict[str, Any]:
    return source_template(
        id="aicoin",
        group="aicoin",
        title="AIcoin 热门榜",
        subtitle="等待本地 AiCoin 客户端 DevTools 调试口返回真实热门榜",
        accent="#57c7ff",
        source_label="AI",
        source_name="AICoin desktop CDP",
        rows=[],
        status="unavailable",
        empty_title="AIcoin 桌面端热榜暂不可用",
        empty_message=message,
    )


def aicoin_client_hot_payload() -> dict[str, Any]:
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("缺少 websocket-client 依赖，无法连接 AiCoin Electron 调试口。") from exc

    host = env_value("AICOIN_CDP_HOST", "127.0.0.1") or "127.0.0.1"
    port = env_value("AICOIN_CDP_PORT", "9222")
    target_hint = env_value("AICOIN_CDP_TARGET_HINT", "#/main/exchange")
    targets = requests.get(f"http://{host}:{port}/json/list", timeout=3).json()
    pages = [item for item in targets if item.get("type") == "page" and item.get("webSocketDebuggerUrl")]
    target = next((item for item in pages if target_hint in item.get("url", "")), None) or (pages[0] if pages else None)
    if not target:
        raise RuntimeError(f"127.0.0.1:{port} 没有可用的 AiCoin renderer page。")

    currency = env_value("AICOIN_CURRENCY", "usd").lower()
    size = int(safe_float(env_value("AICOIN_HOT_SIZE", "20"), 20))
    body = {
        "size": size,
        "page": 1,
        "currency": currency,
        "keyWord": "",
        "customGroupIds": [],
    }
    endpoint = "upgrade/billboard/getHotCoinHour"
    module_id = env_value("AICOIN_AXIOS_MODULE_ID", "0x25c5")
    if not re.fullmatch(r"(?:0x[0-9a-fA-F]+|\d+)", module_id):
        module_id = "0x25c5"

    expression = f"""
(async () => {{
  try {{
    if (!self.webpackChunkaicoin || !self.webpackChunkaicoin.push) {{
      return JSON.stringify({{ ok: false, message: "webpackChunkaicoin not found" }});
    }}
    self.webpackChunkaicoin.push([[Date.now()], {{}}, r => {{ self.__aicoinReq = r; }}]);
    const axios = self.__aicoinAxios || self.__aicoinReq({module_id});
    if (!axios || !axios.post) {{
      return JSON.stringify({{ ok: false, message: "AiCoin axios module not found" }});
    }}
    self.__aicoinAxios = axios;
    const body = {json.dumps(body, ensure_ascii=False)};
    const res = await axios.post("{endpoint}", body);
    const root = res && res.data ? res.data : res;
    const data = root && root.data ? root.data : root;
    const list = data && (data.list || data.items || data.records || data.hot) || [];
    return JSON.stringify({{
      ok: true,
      endpoint: "POST https://vip-pcapi.aicoin.com/api/{endpoint}",
      body,
      count: data && (data.count || data.total || data.totalCount) || list.length || 0,
      list: Array.isArray(list) ? list.slice(0, {size}).map(x => ({{
        coinShow: x.coinShow,
        coinName: x.coinName,
        coinKey: x.coinKey,
        icon: x.icon || x.logo || x.logoUrl || x.coinLogo || x.coinIcon || x.img || x.image || x.symbolIcon,
        price: x.price,
        degree24H: x.degree24H,
        defaultTpKey: x.defaultTpKey
      }})) : []
    }});
  }} catch (e) {{
    return JSON.stringify({{
      ok: false,
      message: String(e && e.message || e),
      responseStatus: e && e.response && e.response.status
    }});
  }}
}})()
"""

    debugger_url = str(target["webSocketDebuggerUrl"])
    if host not in {"127.0.0.1", "localhost"}:
        debugger_url = re.sub(r"^(ws://)(?:127\.0\.0\.1|localhost)(:\d+)", rf"\1{host}\2", debugger_url)
    ws = websocket.create_connection(debugger_url, timeout=10)
    seq = 0

    def cdp(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        nonlocal seq
        seq += 1
        ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
        while True:
            message = json.loads(ws.recv())
            if message.get("id") == seq:
                return message

    try:
        cdp("Runtime.enable")
        result = cdp(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "timeout": 30000,
            },
        )
    finally:
        ws.close()

    if "exceptionDetails" in result:
        raise RuntimeError("AiCoin renderer 执行热门榜请求失败。")
    value = result.get("result", {}).get("result", {}).get("value")
    payload = json.loads(value or "{}")
    if not payload.get("ok"):
        raise RuntimeError(payload.get("message") or "AiCoin 客户端没有返回可用热门榜。")
    return payload


def aicoin_source_from_payload(payload: dict[str, Any], source_detail: str) -> dict[str, Any]:
    rows = []
    items = payload.get("list") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = item.get("coinShow") or item.get("coinKey") or ""
        if not symbol or is_excluded_crypto_asset(symbol):
            continue
        rows.append(
            {
                "rank": len(rows) + 1,
                "symbol": symbol,
                "name": item.get("coinName") or item.get("coinKey") or item.get("defaultTpKey") or symbol,
                "icon": item.get("icon")
                or item.get("coinLogo")
                or item.get("logo")
                or item.get("logoUrl")
                or item.get("coinIcon")
                or item.get("img")
                or item.get("image")
                or item.get("symbolIcon")
                or crypto_icon_url(symbol),
                "price": aicoin_price(item.get("price")),
                "change": pct(safe_float(item.get("degree24H"))),
                "heat": max(1, round((len(items) - len(rows)) / max(len(items), 1) * 100)),
                "amount": 0,
                "turnover": aicoin_price(item.get("price")),
                "tags": ["real hot list", "AIcoin"],
                "note": item.get("defaultTpKey") or source_detail,
            }
        )
        if len(rows) >= 10:
            break

    return source_template(
        id="aicoin",
        group="aicoin",
        title="AIcoin 热门榜",
        subtitle=f"{source_detail}, count={payload.get('count', len(items))}, currency {env_value('AICOIN_CURRENCY', 'usd').upper()}",
        accent="#57c7ff",
        source_label="AI",
        source_name=source_detail,
        rows=rows,
        status="ok" if rows else "unavailable",
        empty_title="AIcoin 热门榜当前为空",
        empty_message="AICoin getHotCoinHour returned no displayable rows.",
    )


def fetch_aicoin() -> dict[str, Any]:
    try:
        payload = aicoin_rest_hot_payload()
        return aicoin_source_from_payload(payload, "AICoin encrypted getHotCoinHour")
    except Exception as rest_exc:
        try:
            payload = aicoin_client_hot_payload()
            return aicoin_source_from_payload(payload, "AICoin desktop CDP getHotCoinHour")
        except Exception as client_exc:
            return aicoin_unavailable(
                f"official encrypted API: {rest_exc}; desktop fallback: {client_exc}. "
                "Please keep the local authorized AICoin state valid, or start AiCoin with --remote-debugging-port=9222."
            )

    try:
        payload = aicoin_client_hot_payload()
    except Exception as exc:
        return aicoin_unavailable(
            f"{exc} 请确认 AiCoin 已用 --remote-debugging-port=9222 启动，并保持桌面端已登录/已加载热门榜页面。"
        )

    rows = []
    items = payload.get("list") or []
    for item in items:
        symbol = item.get("coinShow") or item.get("coinKey") or ""
        if not symbol or is_excluded_crypto_asset(symbol):
            continue
        rows.append(
            {
                "rank": len(rows) + 1,
                "symbol": symbol,
                "name": item.get("coinName") or item.get("coinKey") or item.get("defaultTpKey") or symbol,
                "icon": item.get("icon") or crypto_icon_url(symbol),
                "price": aicoin_price(item.get("price")),
                "change": pct(safe_float(item.get("degree24H"))),
                "heat": max(1, round((len(items) - len(rows)) / max(len(items), 1) * 100)),
                "amount": 0,
                "turnover": aicoin_price(item.get("price")),
                "tags": ["桌面端热榜", "AIcoin"],
                "note": item.get("defaultTpKey") or "客户端解密数据",
            }
        )
        if len(rows) >= 10:
            break

    return source_template(
        id="aicoin",
        group="aicoin",
        title="AIcoin 热门榜",
        subtitle=f"本地客户端 getHotCoinHour，count={payload.get('count', len(items))}，计价 {env_value('AICOIN_CURRENCY', 'usd').upper()}",
        accent="#57c7ff",
        source_label="AI",
        source_name="AICoin desktop CDP getHotCoinHour",
        rows=rows,
        status="ok" if rows else "unavailable",
        empty_title="AIcoin 热门榜当前为空",
        empty_message="已连接 AiCoin 客户端并调用 getHotCoinHour，但没有返回可展示数据。",
    )


def futu_text(node) -> str:
    return node.get_text(strip=True) if node else ""


def futu_column(item, key: str) -> str:
    node = item.select_one(f".data-column-{key}")
    return futu_text(node)


def market_amount_text(value: Any) -> float:
    text = str(value or "").replace(",", "").strip()
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return 0.0
    amount = safe_float(match.group(0))
    if "亿" in text or re.search(r"\bB\b", text, re.I):
        amount *= 100_000_000
    elif "万" in text or re.search(r"\bW\b", text, re.I):
        amount *= 10_000
    elif re.search(r"\bM\b", text, re.I):
        amount *= 1_000_000
    elif re.search(r"\bK\b", text, re.I):
        amount *= 1_000
    return amount


def is_excluded_futu_hk_hot(code: Any, name: Any) -> bool:
    digits = re.sub(r"\D", "", str(code or "")).zfill(5)
    clean_name = html.unescape(str(name or "")).lower()
    return digits in EXCLUDED_FUTU_HK_HOT_CODES or any(keyword in clean_name for keyword in EXCLUDED_FUTU_HK_HOT_NAMES)


def fetch_futu_hot(market: str) -> dict[str, Any]:
    url = f"https://www.futunn.com/quote/{market}/most-active-stocks"
    html_text = requests.get(url, headers=HEADERS, timeout=20).text
    soup = BeautifulSoup(html_text, "lxml")
    rows = []
    for item in soup.select("div.content-main a.list-item"):
        rank = int(futu_text(item.select_one(".order")) or len(rows) + 1)
        code = futu_text(item.select_one(".code"))
        name = futu_text(item.select_one(".name"))
        if not code or not name:
            continue
        if market == "hk" and is_excluded_futu_hk_hot(code, name):
            continue
        average = safe_float(futu_column(item, "averageIndex"))
        trade = safe_float(futu_column(item, "tradeIndex"))
        search = safe_float(futu_column(item, "searchIndex"))
        news = safe_float(futu_column(item, "newsIndex"))
        price = futu_column(item, "price")
        icons = stock_icon_candidates(code, market)
        rows.append(
            {
                "rank": len(rows) + 1,
                "sourceRank": rank,
                "symbol": code,
                "name": html.unescape(name),
                "icon": (icons or [""])[0],
                "icons": icons,
                "price": price,
                "change": futu_column(item, "changeRatio"),
                "heat": round(average),
                "amount": trade,
                "turnover": f"交易热度 {round(trade)}",
                "tags": [f"搜索 {round(search)}", f"资讯 {round(news)}"],
                "note": f"最新价 {price} · 富途综合热度 {round(average)}",
            }
        )
        if len(rows) >= 10:
            break
    return source_template(
        id=f"futu-{market}",
        group="hk" if market == "hk" else "us",
        title="富途港股热门榜" if market == "hk" else "富途美股热门榜",
        subtitle="富途 most-active-stocks 综合热度",
        accent="#ff6b5f" if market == "hk" else "#7bd88f",
        source_label="HK" if market == "hk" else "US",
        source_name="Futunn most-active-stocks",
        rows=rows,
    )


def fetch_futu_gainers(market: str) -> dict[str, Any]:
    url = f"https://www.futunn.com/quote/{market}/stock-list/main-board/top-gainers"
    html_text = requests.get(url, headers=HEADERS, timeout=20).text
    soup = BeautifulSoup(html_text, "lxml")
    rows = []
    seen: set[str] = set()
    for item in soup.select("div.content-main a.list-item"):
        code = futu_text(item.select_one(".code"))
        name = html.unescape(futu_text(item.select_one(".name")))
        if not code or not name or code in seen:
            continue
        if market == "hk" and is_excluded_futu_hk_hot(code, name):
            continue
        values = [futu_text(node) for node in item.select(".middle .value") if futu_text(node)]
        price = values[0] if len(values) > 0 else futu_column(item, "price")
        change = values[2] if len(values) > 2 else futu_column(item, "changeRatio")
        turnover = values[4] if len(values) > 4 else values[3] if len(values) > 3 else ""
        change_value = safe_float(str(change).replace("%", "").replace("+", ""))
        if change_value <= 0:
            continue
        icons = stock_icon_candidates(code, market)
        href = item.get("href") or f"/quote/{market}/{code}"
        if "stocк" in href:
            href = href.replace("stocк", "stock")
        seen.add(code)
        rows.append(
            {
                "rank": len(rows) + 1,
                "symbol": code,
                "name": name,
                "icon": (icons or [""])[0],
                "icons": icons,
                "price": price,
                "change": change,
                "heat": max(1, min(100, round(change_value))),
                "amount": market_amount_text(turnover),
                "turnover": f"成交额 {turnover}" if turnover else "--",
                "tags": ["富途", "涨幅榜"],
                "note": f"富途 stock-list/top-gainers · {name}",
                "url": urljoin("https://www.futunn.com", href),
            }
        )
        if len(rows) >= 10:
            break
    return source_template(
        id=f"futu-{market}-gainers",
        group="hk" if market == "hk" else "us",
        title="富途港股涨幅榜" if market == "hk" else "富途美股涨幅榜",
        subtitle="富途 stock-list/main-board/top-gainers 官方涨幅榜",
        accent="#ff6b5f" if market == "hk" else "#7bd88f",
        source_label="HK" if market == "hk" else "US",
        source_name="Futunn stock-list top-gainers",
        rows=rows,
        status="ok" if rows else "unavailable",
        empty_title="富途涨幅榜当前为空",
        empty_message=f"已请求富途 {market.upper()} top-gainers 页面，但没有解析到可展示数据。",
    )


def fetch_futu_turnover(market: str) -> dict[str, Any]:
    url = f"https://www.futunn.com/quote/{market}/stock-list/main-board/top-turnover"
    html_text = requests.get(url, headers=HEADERS, timeout=20).text
    soup = BeautifulSoup(html_text, "lxml")
    rows = []
    seen: set[str] = set()
    for item in soup.select("div.content-main a.list-item"):
        href = item.get("href") or ""
        normalized_href = href.replace("stocк", "stock")
        if "/stock/" not in normalized_href:
            continue
        code = futu_text(item.select_one(".code"))
        name = html.unescape(futu_text(item.select_one(".name")))
        if not code or not name or code in seen:
            continue
        if market == "hk" and is_excluded_futu_hk_hot(code, name):
            continue
        values = [futu_text(node) for node in item.select(".middle .value") if futu_text(node)]
        price = values[0] if len(values) > 0 else futu_column(item, "price")
        change = values[2] if len(values) > 2 else futu_column(item, "changeRatio")
        volume = values[3] if len(values) > 3 else ""
        turnover = values[4] if len(values) > 4 else ""
        amount = market_amount_text(turnover)
        if amount <= 0:
            continue
        icons = stock_icon_candidates(code, market)
        seen.add(code)
        rows.append(
            {
                "rank": 0,
                "symbol": code,
                "name": name,
                "icon": (icons or [""])[0],
                "icons": icons,
                "price": price,
                "change": change,
                "heat": 1,
                "amount": amount,
                "turnover": f"成交额 {turnover}",
                "tags": [f"成交量 {volume}" if volume else "成交额榜", f"富途 {market.upper()}"],
                "note": f"最新价 {price} · 成交额 {turnover}",
                "url": urljoin("https://www.futunn.com", normalized_href),
            }
        )
    rows.sort(key=lambda item: safe_float(item.get("amount")), reverse=True)
    max_amount = safe_float(rows[0].get("amount")) if rows else 0
    rows = rows[:10]
    for index, row in enumerate(rows):
        row["rank"] = index + 1
        row["heat"] = max(1, min(100, round(safe_float(row.get("amount")) / max(max_amount, 1) * 100)))
    return source_template(
        id=f"futu-{market}-turnover",
        group="hk" if market == "hk" else "us",
        title="富途港股成交额榜" if market == "hk" else "富途美股成交额榜",
        subtitle="富途 top-turnover 按成交额排序",
        accent="#ff6b5f" if market == "hk" else "#7bd88f",
        source_label="HK" if market == "hk" else "US",
        source_name="Futunn stock-list top-turnover",
        rows=rows,
        status="ok" if rows else "unavailable",
        empty_title="暂无可用成交额数据",
        empty_message="富途成交额榜当前没有返回可展示数据。",
    )


def eastmoney_clist_payload(params: dict[str, Any]) -> dict[str, Any]:
    hosts = [
        "https://push2.eastmoney.com/api/qt/clist/get",
        "https://push2his.eastmoney.com/api/qt/clist/get",
        "https://16.push2.eastmoney.com/api/qt/clist/get",
        "https://88.push2.eastmoney.com/api/qt/clist/get",
    ]
    errors = []
    for url in hosts:
        try:
            response = requests.get(
                url,
                params={**params, "_": str(int(time.time() * 1000))},
                headers={**HEADERS, "Referer": "https://quote.eastmoney.com/center/gridlist.html"},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            if (payload.get("data") or {}).get("diff"):
                return payload
        except Exception as exc:
            errors.append(f"{urlparse(url).netloc}: {exc}")
            continue
    raise RuntimeError("；".join(errors[-3:]) or "东方财富涨幅榜未返回数据")


def fetch_cn_stock_gainers() -> dict[str, Any]:
    params = {
        "pn": "1",
        "pz": "20",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f12,f14,f2,f3,f4,f5,f6,f20",
    }
    try:
        payload = eastmoney_clist_payload(params)
        items = (payload.get("data") or {}).get("diff") or []
        rows = []
        for item in items:
            code = str(item.get("f12") or "")
            name = item.get("f14") or ""
            change = safe_float(item.get("f3"))
            if not code or not name or change <= 0:
                continue
            icons = stock_icon_candidates(code, "cn")
            amount = safe_float(item.get("f6"))
            rows.append(
                {
                    "rank": len(rows) + 1,
                    "symbol": code,
                    "name": name,
                    "icon": (icons or [""])[0],
                    "icons": icons,
                    "price": f"{safe_float(item.get('f2')):g}",
                    "change": pct(change),
                    "heat": max(1, min(100, round(change))),
                    "amount": amount,
                    "turnover": money_usd(amount) if amount else "--",
                    "tags": ["A股", "涨幅榜"],
                    "note": f"成交额 {money_usd(amount) if amount else '--'} · 市值 {money_usd(safe_float(item.get('f20')))}",
                    "url": f"https://stockpage.10jqka.com.cn/{code}/",
                }
            )
            if len(rows) >= 10:
                break
        source = source_template(
            id="cn-stock-gainers",
            group="cn",
            title="A股涨幅榜",
            subtitle="A股全市场按当日涨幅排序",
            accent="#ff9f1c",
            source_label="A",
            source_name="Eastmoney clist f3 gainers",
            rows=rows,
            status="ok" if rows else "unavailable",
            empty_title="A股涨幅榜当前为空",
            empty_message="已请求 A股涨幅榜行情，但没有返回可展示数据。",
        )
        if rows:
            write_json_cache(CN_STOCK_GAINERS_CACHE_PATH, source)
        return source
    except Exception as exc:
        cached_source = cached_source_fallback("cn-stock-gainers", CN_STOCK_GAINERS_CACHE_PATH, api_key="gainers-rankings")
        if cached_source:
            cached_source["subtitle"] = "A股全市场按当日涨幅排序 · 本地缓存"
            cached_source["sourceName"] = clean_feed_text(cached_source.get("sourceName") or "Eastmoney cache", 120)
            return cached_source
        return source_template(
            id="cn-stock-gainers",
            group="cn",
            title="A股涨幅榜",
            subtitle="A股涨幅榜数据源请求失败",
            accent="#ff9f1c",
            source_label="A",
            source_name=clean_feed_text(exc, 140),
            rows=[],
            status="unavailable",
            empty_title="暂无可用涨幅数据",
            empty_message="东方财富接口暂时超时，且本地没有可用缓存。",
        )


def eastmoney_secid(code: Any) -> str:
    digits = re.sub(r"\D", "", str(code or ""))[-6:]
    if not digits:
        return ""
    market = "1" if digits.startswith(("5", "6", "9")) else "0"
    return f"{market}.{digits}"


def eastmoney_stock_quotes(codes: list[str]) -> dict[str, dict[str, float]]:
    quotes: dict[str, dict[str, float]] = {}
    unique_codes = list(dict.fromkeys(re.sub(r"\D", "", str(code or ""))[-6:] for code in codes))
    sina_symbols = [
        f"{'sh' if code.startswith(('5', '6', '9')) else 'sz'}{code}"
        for code in unique_codes
        if code
    ]
    if sina_symbols:
        try:
            response = requests.get(
                "https://hq.sinajs.cn/list=" + ",".join(sina_symbols),
                headers={**HEADERS, "Referer": "https://finance.sina.com.cn/"},
                timeout=8,
            )
            response.encoding = "gbk"
            for match in re.finditer(r'var hq_str_(?:sh|sz)(\d{6})="([^"]*)";', response.text):
                code = match.group(1)
                fields = match.group(2).split(",")
                if len(fields) < 10:
                    continue
                price = safe_float(fields[3])
                previous_close = safe_float(fields[2])
                change = ((price - previous_close) / previous_close * 100) if price and previous_close else 0.0
                amount = safe_float(fields[9])
                if price or change or amount:
                    quotes[code] = {"price": price, "change": change, "amount": amount}
        except Exception:
            pass
    missing_codes = [code for code in unique_codes if code and code not in quotes]
    if not missing_codes:
        return quotes

    def fetch_quote(code: str) -> tuple[str, dict[str, float] | None]:
        digits = re.sub(r"\D", "", str(code or ""))[-6:]
        secid = eastmoney_secid(digits)
        if not secid:
            return digits, None
        try:
            payload = requests.get(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={
                    "secid": secid,
                    "fields": "f43,f48,f57,f58,f170",
                    "fltt": "2",
                    "invt": "2",
                },
                headers={**HEADERS, "Referer": "https://quote.eastmoney.com/"},
                timeout=6,
            ).json()
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            price = safe_float(data.get("f43"))
            change = safe_float(data.get("f170"))
            amount = safe_float(data.get("f48"))
            if price or change or amount:
                return digits, {"price": price, "change": change, "amount": amount}
        except Exception:
            return digits, None
        return digits, None

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(unique_codes)))) as executor:
        futures = [executor.submit(fetch_quote, code) for code in missing_codes if code]
        for future in as_completed(futures):
            code, quote = future.result()
            if code and quote:
                quotes[code] = quote
    return quotes


def fetch_ths_hot_payload() -> dict[str, Any]:
    urls = [
        "https://eq.10jqka.com.cn/open/api/hot_list/v1/hot_stock/a/day/data.txt",
        "http://eq.10jqka.com.cn/open/api/hot_list/v1/hot_stock/a/day/data.txt",
    ]
    last_error: Exception | None = None
    headers = {
        **THS_HEADERS,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    for attempt in range(3):
        for url in urls:
            try:
                response = requests.get(
                    url,
                    params={"_": int(time.time() * 1000) + attempt},
                    headers=headers,
                    timeout=8,
                )
                response.raise_for_status()
                payload = response.json()
                stocks = payload.get("data", {}).get("stock_list", [])
                if isinstance(stocks, list) and stocks:
                    return payload
                last_error = RuntimeError("同花顺接口没有返回榜单")
            except Exception as exc:
                last_error = exc
        time.sleep(0.25)
    req = urllib.request.Request(urls[0], headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
            stocks = payload.get("data", {}).get("stock_list", [])
            if isinstance(stocks, list) and stocks:
                return payload
    except Exception as exc:
        last_error = exc
    raise RuntimeError(f"同花顺接口不稳定：{last_error}")


def build_ths_hot_source(payload: dict[str, Any], source_name: str = "10jqka hot_stock day") -> dict[str, Any]:
    stocks = payload.get("data", {}).get("stock_list", [])[:10]
    if not stocks:
        raise RuntimeError("同花顺接口没有返回可展示股票")
    quotes = eastmoney_stock_quotes([str(item.get("code") or "") for item in stocks])
    rates = [safe_float(item.get("rate")) for item in stocks]
    max_rate = max(rates) if rates else 1
    rows = []
    for item in stocks:
        rate = safe_float(item.get("rate"))
        tag = item.get("tag") or {}
        concept_tags = tag.get("concept_tag") or []
        hot_delta = safe_float(item.get("hot_rank_chg"))
        code = str(item.get("code") or "")
        icons = stock_icon_candidates(code, "cn")
        quote = quotes.get(re.sub(r"\D", "", code)[-6:], {})
        price = safe_float(quote.get("price"))
        change = safe_float(quote.get("change"))
        amount = safe_float(quote.get("amount"))
        rows.append(
            {
                "rank": int(item.get("order") or len(rows) + 1),
                "symbol": code,
                "name": item.get("name") or "",
                "icon": (icons or [""])[0],
                "icons": icons,
                "price": f"{price:g}" if price else "--",
                "change": pct(change) if price or change else signed(hot_delta),
                "heat": max(1, round(rate / max_rate * 100)),
                "amount": amount,
                "turnover": money_cny(amount) if amount else "排名变动",
                "tags": concept_tags[:2],
                "note": f"同花顺24h热度值 {rate:g}",
            }
        )
    return source_template(
        id="ths-cn",
        group="cn",
        title="A股同花顺24h热门榜",
        subtitle="同花顺最近 24h / 日榜人气数据",
        accent="#ff9f1c",
        source_label="THS",
        source_name=source_name,
        rows=rows,
    )


def fetch_ths_hot() -> dict[str, Any]:
    try:
        source = build_ths_hot_source(fetch_ths_hot_payload())
        write_json_cache(THS_SOURCE_CACHE_PATH, {**source, "cachedAt": int(time.time() * 1000)})
        return source
    except Exception as exc:
        fallback = cached_source_fallback("ths-cn", THS_SOURCE_CACHE_PATH)
        if fallback:
            fallback["subtitle"] = "同花顺最近 24h / 上一次成功数据"
            fallback["sourceName"] = f"{fallback.get('sourceName') or '10jqka hot_stock day'} · 接口短暂失败：{alert_text(exc, 48)}"
            return fallback
        raise


def decode_js_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except Exception:
        return value.encode("utf-8", "ignore").decode("unicode_escape", "ignore")


def split_js_args(source: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    quote = ""
    escape = False
    depth = 0
    for char in source:
        if quote:
            current.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue
        if char in "([{":
            depth += 1
            current.append(char)
            continue
        if char in ")]}":
            depth = max(0, depth - 1)
            current.append(char)
            continue
        if char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        args.append("".join(current).strip())
    return args


def decode_js_literal(token: str, var_map: dict[str, Any] | None = None) -> Any:
    token = (token or "").strip()
    if var_map and token in var_map:
        return var_map[token]
    if not token:
        return ""
    if token in {"true", "false"}:
        return token == "true"
    if token in {"null", "undefined", "void 0"}:
        return None
    if token.startswith('"') and token.endswith('"'):
        try:
            return json.loads(token)
        except Exception:
            return decode_js_string(token[1:-1])
    if token.startswith("'") and token.endswith("'"):
        return decode_js_string(token[1:-1].replace('"', '\\"'))
    if re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        return safe_float(token) if "." in token else int(token)
    return token


def extract_nuxt_var_map(script: str) -> dict[str, Any]:
    head_match = re.search(r"window\.__NUXT__=\(function\(([^)]*)\)\{return", script, flags=re.S)
    if not head_match:
        return {}
    names = [name.strip() for name in head_match.group(1).split(",") if name.strip()]
    tail_start = script.rfind("}(")
    tail_end = script.rfind("));")
    if tail_start < 0 or tail_end < tail_start:
        return {}
    values = split_js_args(script[tail_start + 2 : tail_end])
    return {name: decode_js_literal(value) for name, value in zip(names, values)}


def js_object_field(block: str, field: str, var_map: dict[str, Any]) -> Any:
    match = re.search(rf"\b{re.escape(field)}:((?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^,}}]+))", block, flags=re.S)
    if not match:
        return ""
    return decode_js_literal(match.group(1).strip(), var_map)


def clean_html(value: str) -> str:
    return BeautifulSoup(value, "lxml").get_text(" ", strip=True)


def fetch_blockbeats_flash() -> dict[str, Any]:
    html_text = requests.get("https://www.theblockbeats.info/newsflash", headers=HEADERS, timeout=20).text
    start = html_text.find("window.__NUXT__=")
    script = html_text[start:] if start >= 0 else html_text
    var_map = extract_nuxt_var_map(script)
    blocks = re.findall(r"\{id:(?:\d+|[A-Za-z_$][\w$]*),article_id:.*?isSup:[^}]+\}", script, flags=re.S)
    items = []
    seen = set()
    for block in blocks:
        item_id = clean_feed_text(js_object_field(block, "id", var_map), 80)
        add_time = int(safe_float(js_object_field(block, "add_time", var_map)))
        title = clean_feed_text(js_object_field(block, "title", var_map), 240)
        content_raw = str(js_object_field(block, "content", var_map) or "")
        if not (item_id and add_time and title):
            continue
        if item_id in seen:
            continue
        seen.add(item_id)
        content = clean_html(content_raw)
        url = clean_feed_text(js_object_field(block, "url", var_map), 700)
        image = clean_feed_text(js_object_field(block, "img_url", var_map) or js_object_field(block, "c_img_url", var_map), 700)
        items.append(
            {
                "id": item_id,
                "title": title,
                "content": content,
                "url": url,
                "image": image,
                "add_time": add_time,
            }
        )
    items = sorted(items, key=lambda item: item.get("add_time") or 0, reverse=True)[:60]
    return {"updatedAt": int(time.time() * 1000), "items": items}


def parse_date_ms(value: str) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    formats = ("%Y/%m/%d", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%Y年%m月%d日")
    for fmt in formats:
        try:
            return int(datetime.strptime(value, fmt).timestamp() * 1000)
        except Exception:
            continue
    return None


def listing_section(
    *,
    id: str,
    title: str,
    subtitle: str,
    accent: str,
    source_name: str,
    rows: list[dict[str, Any]],
    status: str = "ok",
    empty_message: str = "",
) -> dict[str, Any]:
    return {
        "id": id,
        "title": title,
        "subtitle": subtitle,
        "accent": accent,
        "sourceName": source_name,
        "updatedAt": int(time.time() * 1000),
        "status": status,
        "emptyMessage": empty_message,
        "rows": rows,
    }


def extract_listing_symbols(title: str, limit: int = 4) -> str:
    title = title or ""
    tokens = re.findall(r"\b[A-Z][A-Z0-9]{1,}USDT\b|\b[A-Z][A-Z0-9]{1,}/USDT\b|\b[A-Z][A-Z0-9]{1,}\b", title)
    excluded = {"USDT", "USDC", "USD", "VIP", "BOT", "ETF", "NFT", "OKX"}
    result: list[str] = []
    for token in tokens:
        if token in excluded or token.isdigit():
            continue
        if token not in result:
            result.append(token)
        if len(result) >= limit:
            break
    return ", ".join(result)


def binance_listing_events(limit: int = 8) -> list[dict[str, Any]]:
    payload = requests.get(
        "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
        params={"type": "1", "catalogId": "48", "pageNo": "1", "pageSize": str(max(10, limit))},
        headers={**HEADERS, "clienttype": "web", "lang": "zh-CN", "Referer": "https://www.binance.com/zh-CN/support/announcement/"},
        timeout=18,
    ).json()
    catalogs = payload.get("data", {}).get("catalogs") or []
    articles = catalogs[0].get("articles") if catalogs else []
    rows = []
    for item in (articles or [])[:limit]:
        title = item.get("title") or ""
        code = item.get("code") or ""
        release_ms = int(safe_float(item.get("releaseDate")))
        rows.append(
            {
                "id": f"binance-{item.get('id') or code}",
                "group": "crypto",
                "source": "Binance",
                "sourceLabel": "BN",
                "title": title,
                "symbol": extract_listing_symbols(title),
                "status": "公告",
                "date": release_ms,
                "metric": "数字货币及交易对上新",
                "tags": ["交易所上新", "Binance"],
                "url": f"https://www.binance.com/zh-CN/support/announcement/{code}" if code else "https://www.binance.com/zh-CN/support/announcement",
            }
        )
    return rows


def bitget_listing_events(limit: int = 8) -> list[dict[str, Any]]:
    response = requests.get(
        "https://api.bitget.com/api/v2/public/annoucements",
        params={"language": "zh_CN", "annType": "coin_listings", "limit": str(limit)},
        headers={**HEADERS, "Accept": "application/json"},
        timeout=18,
    )
    response.encoding = "utf-8"
    payload = response.json()
    rows = []
    for item in payload.get("data") or []:
        release_ms = int(safe_float(item.get("cTime")))
        subtype = item.get("annSubType") or item.get("annDesc") or "listing"
        rows.append(
            {
                "id": f"bitget-{item.get('annId')}",
                "group": "crypto",
                "source": "Bitget",
                "sourceLabel": "BG",
                "title": item.get("annTitle") or "",
                "symbol": extract_listing_symbols(item.get("annTitle") or ""),
                "status": subtype,
                "date": release_ms,
                "metric": "coin_listings",
                "tags": ["交易所上新", "Bitget"],
                "url": item.get("annUrl") or "https://www.bitget.com/zh-CN/support/categories/360001510552",
            }
        )
    return rows


def okx_listing_events(limit: int = 8) -> list[dict[str, Any]]:
    html_text = requests.get(
        "https://www.okx.com/zh-hans/help/section/announcements-new-listings",
        headers=HEADERS,
        timeout=18,
    ).text
    soup = BeautifulSoup(html_text, "lxml")
    rows = []
    for link in soup.select('a[class*="articleLink"][href*="/help/"]'):
        title_node = link.select_one('[class*="articleTitle"]') or link
        title = title_node.get_text(" ", strip=True)
        title = re.sub(r"\s*发布于\s*\d{4}年\d{1,2}月\d{1,2}日\s*$", "", title).strip()
        if not title or title in {row["title"] for row in rows}:
            continue
        date_text = ""
        for span in link.select("span"):
            text = span.get_text(" ", strip=True)
            if "发布于" in text:
                date_text = text.replace("发布于", "").strip()
                break
        rows.append(
            {
                "id": f"okx-{len(rows) + 1}",
                "group": "crypto",
                "source": "OKX",
                "sourceLabel": "OK",
                "title": title,
                "symbol": extract_listing_symbols(title),
                "status": "公告",
                "date": parse_date_ms(date_text) or 0,
                "metric": "新币上线公告",
                "tags": ["交易所上新", "OKX"],
                "url": urljoin("https://www.okx.com", link.get("href") or ""),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def crypto_listing_section() -> dict[str, Any]:
    rows_by_source: list[list[dict[str, Any]]] = []
    errors: list[str] = []
    for label, fetcher in (
        ("Binance", binance_listing_events),
        ("Bitget", bitget_listing_events),
        ("OKX", okx_listing_events),
    ):
        try:
            source_rows = fetcher()
            source_rows.sort(key=lambda item: item.get("date") or 0, reverse=True)
            rows_by_source.append(source_rows[:6])
        except Exception as exc:
            errors.append(f"{label}: {exc}")
    rows = [row for group in rows_by_source for row in group]
    return listing_section(
        id="exchange-listings",
        title="交易所上新",
        subtitle="Binance / OKX / Bitget 新币、交易对与合约上新公告",
        accent="#58c7f3",
        source_name="Exchange announcements",
        rows=rows[:18],
        status="ok" if rows else "unavailable",
        empty_message="；".join(errors) if errors else "暂时没有获取到交易所上新公告。",
    )


def nasdaq_ipo_section() -> dict[str, Any]:
    month = datetime.now().strftime("%Y-%m")
    payload = requests.get(
        "https://api.nasdaq.com/api/ipo/calendar",
        params={"date": month},
        headers={
            **HEADERS,
            "Accept": "application/json",
            "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/market-activity/ipos/",
        },
        timeout=20,
    ).json()
    data = payload.get("data") or {}
    rows = []
    upcoming = ((data.get("upcoming") or {}).get("upcomingTable") or {}).get("rows") or []
    priced = (data.get("priced") or {}).get("rows") or []
    for item in upcoming:
        symbol = item.get("proposedTickerSymbol") or "--"
        rows.append(
            {
                "id": f"nasdaq-upcoming-{item.get('dealID') or symbol}",
                "group": "ipo",
                "source": "Nasdaq",
                "sourceLabel": "IPO",
                "title": item.get("companyName") or symbol,
                "symbol": symbol,
                "status": "Upcoming",
                "date": parse_date_ms(item.get("expectedPriceDate") or "") or 0,
                "metric": item.get("dollarValueOfSharesOffered") or item.get("sharesOffered") or "",
                "price": item.get("proposedSharePrice") or "",
                "tags": [item.get("proposedExchange") or "US IPO", "预计定价"],
                "url": "https://www.nasdaq.com/market-activity/ipos",
            }
        )
    for item in priced:
        symbol = item.get("proposedTickerSymbol") or "--"
        rows.append(
            {
                "id": f"nasdaq-priced-{item.get('dealID') or symbol}",
                "group": "ipo",
                "source": "Nasdaq",
                "sourceLabel": "IPO",
                "title": item.get("companyName") or symbol,
                "symbol": symbol,
                "status": "Priced",
                "date": parse_date_ms(item.get("pricedDate") or "") or 0,
                "metric": item.get("dollarValueOfSharesOffered") or item.get("sharesOffered") or "",
                "price": item.get("proposedSharePrice") or "",
                "tags": [item.get("proposedExchange") or "US IPO", "已定价"],
                "url": "https://www.nasdaq.com/market-activity/ipos",
            }
        )
    rows.sort(key=lambda item: item.get("date") or 0, reverse=True)
    return listing_section(
        id="ipo-calendar",
        title="IPO 信息",
        subtitle=f"Nasdaq IPO Calendar，月份 {month}",
        accent="#f6bb48",
        source_name="Nasdaq IPO calendar",
        rows=rows[:18],
        status="ok" if rows else "unavailable",
        empty_message="Nasdaq IPO 日历当前没有返回可展示数据。",
    )


def futu_listing_column(item, key: str) -> str:
    node = item.select_one(f".value-{key}") or item.select_one(f".data-column-{key}")
    return futu_text(node)


def futu_recent_listing_events(market: str, limit: int = 10) -> list[dict[str, Any]]:
    url = f"https://www.futunn.com/quote/{market}/ipo"
    html_text = requests.get(url, headers=HEADERS, timeout=20).text
    soup = BeautifulSoup(html_text, "lxml")
    rows = []
    for item in soup.select("a.list-item"):
        href = item.get("href") or ""
        if "/stock/" not in href:
            continue
        code = futu_text(item.select_one(".code"))
        name = html.unescape(futu_text(item.select_one(".name")))
        listing_date = futu_listing_column(item, "listingDate")
        if not code or not name:
            continue
        rows.append(
            {
                "id": f"futu-{market}-{code}",
                "group": market,
                "source": "富途",
                "sourceLabel": "HK" if market == "hk" else "US",
                "title": name,
                "symbol": code,
                "status": "已上市",
                "date": parse_date_ms(listing_date) or 0,
                "metric": f"市值 {futu_listing_column(item, 'marketVal') or '--'}",
                "price": futu_listing_column(item, "price"),
                "tags": [
                    "港股" if market == "hk" else "美股",
                    f"上市 {listing_date}" if listing_date else "上市日待确认",
                ],
                "note": f"发行量 {futu_listing_column(item, 'ipoIssueVol') or '--'} · 累计涨幅 {futu_listing_column(item, 'ipoPriceChangeRatio') or '--'}",
                "url": urljoin("https://www.futunn.com", href),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def eastmoney_purchase_payload(sort_column: str, page_size: int = 40) -> list[dict[str, Any]]:
    response = requests.get(
        "https://datapc.eastmoney.com/da/purchase/list2",
        params={
            "st": sort_column,
            "sr": "-1",
            "p": "1",
            "ps": str(page_size),
            "stat": "0",
        },
        headers={
            **HEADERS,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://datapc.eastmoney.com/da/purchase/index?color=b",
        },
        timeout=20,
    )
    response.encoding = "utf-8"
    payload = response.json()
    data = (payload.get("result") or {}).get("data") or []
    return data if isinstance(data, list) else []


def short_date_tag(label: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    date_part = text.split(" ", 1)[0].replace("-", "/")
    return f"{label} {date_part[5:]}" if len(date_part) >= 10 else f"{label} {date_part}"


def a_share_new_stock_events(limit: int = 18) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for sort_column in ("LISTING_DATE", "APPLY_DATE"):
        try:
            items.extend(eastmoney_purchase_payload(sort_column, page_size=40))
        except Exception:
            continue

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    today = datetime.now().date()
    for item in items:
        code = re.sub(r"\D", "", str(item.get("SECURITY_CODE") or ""))[-6:]
        name = html.unescape(str(item.get("SECURITY_NAME") or item.get("SECURITY_NAME_ABBR") or code).strip())
        if not code or not name or code in seen:
            continue
        listing_raw = item.get("LISTING_DATE") or item.get("SELECT_LISTING_DATE")
        apply_raw = item.get("APPLY_DATE")
        listing_ms = parse_date_ms(str(listing_raw or "")) or 0
        apply_ms = parse_date_ms(str(apply_raw or "")) or 0
        event_ms = listing_ms or apply_ms
        if not event_ms:
            continue
        try:
            event_date = datetime.fromtimestamp(event_ms / 1000).date()
        except Exception:
            event_date = today
        issue_price = safe_float(item.get("ISSUE_PRICE"))
        latest_price = safe_float(item.get("LATELY_PRICE") or item.get("NEWEST_PRICE"))
        total_change = safe_float(item.get("TOTAL_CHANGE") or item.get("LD_CLOSE_CHANGE") or item.get("FRIST_CLOSE_CHANGE"))
        issue_num_wan = safe_float(item.get("ISSUE_NUM") or item.get("EXPECT_ISSUE_NUM"))
        amount = issue_num_wan * 10_000 * issue_price if issue_num_wan and issue_price else 0
        market_type = str(item.get("MARKET_TYPE") or item.get("TRADE_MARKET") or "A股").replace("非科创板", "沪深主板")
        if listing_ms:
            status = "今日上市" if event_date == today else "已上市"
        elif apply_ms:
            status = "今日申购" if event_date == today else "申购中" if event_date >= today else "待上市"
        else:
            status = "待上市"
        icons = stock_icon_candidates(code, "cn")
        tags = [
            "A股",
            market_type,
            short_date_tag("申购", apply_raw),
            short_date_tag("上市", listing_raw),
        ]
        price_text = f"¥{latest_price:g}" if latest_price else f"¥{issue_price:g}" if issue_price else ""
        metric = f"最新 {price_text}" if latest_price else f"发行价 {price_text}" if price_text else ""
        if amount:
            metric = f"{metric} · 募资 {money_cny(amount)}" if metric else f"募资 {money_cny(amount)}"
        rows.append(
            {
                "id": f"eastmoney-cn-{code}",
                "rank": 0,
                "group": "cn",
                "source": "东方财富",
                "sourceLabel": "CN",
                "title": name,
                "symbol": code,
                "status": status,
                "date": event_ms,
                "metric": metric,
                "price": price_text,
                "change": pct(total_change) if total_change else "",
                "amount": amount,
                "heat": 0,
                "icon": (icons or [""])[0],
                "icons": icons,
                "tags": [tag for tag in tags if tag],
                "note": str(item.get("MAIN_BUSINESS") or "").strip()[:120],
                "url": f"https://datapc.eastmoney.com/da/purchase/detail?color=b&code={quote(code)}&name={quote(name)}",
            }
        )
        seen.add(code)

    rows.sort(key=lambda row: row.get("date") or 0, reverse=True)
    max_amount = max([safe_float(row.get("amount")) for row in rows] or [0])
    for index, row in enumerate(rows[:limit]):
        row["rank"] = index + 1
        if max_amount:
            row["heat"] = max(1, min(100, round(safe_float(row.get("amount")) / max_amount * 100)))
    return rows[:limit]


def a_share_new_stock_section() -> dict[str, Any]:
    rows = a_share_new_stock_events()
    return listing_section(
        id="cn-new-stocks",
        title="A股新股榜",
        subtitle="东方财富新股申购与上市数据",
        accent="#ff9f1c",
        source_name="Eastmoney purchase/list2",
        rows=rows,
        status="ok" if rows else "unavailable",
        empty_message="东方财富新股接口当前没有返回可展示数据。",
    )


def hk_us_listing_section() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for market in ("hk", "us"):
        try:
            rows.extend(futu_recent_listing_events(market))
        except Exception as exc:
            errors.append(f"{market.upper()}: {exc}")
    rows.sort(key=lambda item: item.get("date") or 0, reverse=True)
    return listing_section(
        id="hk-us-listings",
        title="港美股上市信息",
        subtitle="富途港股 / 美股 IPO 中心最近上市时间表",
        accent="#7bd88f",
        source_name="Futunn IPO pages",
        rows=rows[:18],
        status="ok" if rows else "unavailable",
        empty_message="；".join(errors) if errors else "暂时没有获取到港美股上市信息。",
    )


def listing_events_payload() -> dict[str, Any]:
    sections = []
    fetchers = [
        ("exchange-listings", crypto_listing_section),
        ("ipo-calendar", nasdaq_ipo_section),
        ("cn-new-stocks", a_share_new_stock_section),
        ("hk-us-listings", hk_us_listing_section),
    ]
    for key, fetcher in fetchers:
        try:
            sections.append(cached(key, fetcher))
        except Exception as exc:
            sections.append(
                listing_section(
                    id=key,
                    title=key,
                    subtitle="数据源请求失败",
                    accent="#777777",
                    source_name=str(exc)[:120],
                    rows=[],
                    status="unavailable",
                    empty_message=str(exc)[:180],
                )
            )
    return {"updatedAt": int(time.time() * 1000), "sections": sections}


def parse_iso_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None


def clean_automation_message(value: str) -> str:
    value = re.sub(r"\n?::inbox-item\{.*?\}\s*$", "", value or "", flags=re.S).strip()
    value = value.replace("\ue200", "").replace("\ue201", "").replace("\ue202", " ")
    return value


def automation_brief_time_ms(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return int(number if number > 10_000_000_000 else number * 1000)
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        number = float(text)
        return int(number if number > 10_000_000_000 else number * 1000)
    return parse_iso_ms(text)


def automation_brief_has_content(brief: dict[str, Any]) -> bool:
    content = clean_automation_message(str(brief.get("content") or ""))
    return bool(content and content != AUTOMATION_BRIEF_PLACEHOLDER)


def normalize_automation_brief(value: Any, fallback_id: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    automation_id = str(value.get("id") or value.get("automationId") or fallback_id or "").strip()
    if not automation_id:
        return {}
    config = automation_config(automation_id)
    content = clean_automation_message(str(value.get("content") or value.get("body") or value.get("markdown") or ""))
    return {
        "id": automation_id,
        "name": value.get("name") or config.get("name") or automation_id,
        "status": value.get("status") or config.get("status") or "UNKNOWN",
        "rrule": value.get("rrule") or config.get("rrule") or "",
        "updatedAt": automation_brief_time_ms(value.get("updatedAt") or value.get("updated_at") or config.get("updated_at")),
        "completedAt": automation_brief_time_ms(value.get("completedAt") or value.get("completed_at") or value.get("createdAt")),
        "sourceFile": value.get("sourceFile") or value.get("source") or "",
        "content": content or AUTOMATION_BRIEF_PLACEHOLDER,
    }


def normalize_automation_briefs_payload(payload: Any, *, source_name: str) -> dict[str, Any]:
    if isinstance(payload, list):
        raw_briefs = payload
        updated_at = int(time.time() * 1000)
    elif isinstance(payload, dict):
        raw_briefs = payload.get("briefs") if isinstance(payload.get("briefs"), list) else []
        updated_at = automation_brief_time_ms(payload.get("updatedAt") or payload.get("generatedAt")) or int(time.time() * 1000)
    else:
        raw_briefs = []
        updated_at = int(time.time() * 1000)
    briefs = []
    for index, item in enumerate(raw_briefs):
        fallback_id = AUTOMATION_BRIEF_IDS[index] if index < len(AUTOMATION_BRIEF_IDS) else ""
        brief = normalize_automation_brief(item, fallback_id=fallback_id)
        if brief:
            briefs.append(brief)
    return {"updatedAt": updated_at, "source": source_name, "briefs": briefs}


def automation_briefs_remote_url() -> str:
    if raw_env_flag("AUTOMATION_BRIEFS_DISABLE_REMOTE", default=False):
        return ""
    return (
        env_value("AUTOMATION_BRIEFS_REMOTE_URL")
        or env_value("XINGYUN_BRIEFS_GITHUB_RAW_URL")
        or AUTOMATION_BRIEFS_DEFAULT_REMOTE_URL
    ).strip()


def load_bundled_automation_briefs() -> dict[str, Any]:
    if not AUTOMATION_BRIEFS_BUNDLED_PATH.exists():
        return {}
    payload = read_json_cache(AUTOMATION_BRIEFS_BUNDLED_PATH)
    normalized = normalize_automation_briefs_payload(payload, source_name="github-bundled")
    if normalized.get("briefs"):
        normalized["_cache"] = {
            "key": "automation-briefs-bundled",
            "updatedAt": automation_brief_time_ms(normalized.get("updatedAt")) or int(time.time() * 1000),
            "stale": False,
        }
        normalized["remoteUrl"] = str(AUTOMATION_BRIEFS_BUNDLED_PATH)
    return normalized


def prefer_newer_automation_payload(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    if not primary.get("briefs"):
        return secondary
    if not secondary.get("briefs"):
        return primary
    primary_time = automation_brief_time_ms(primary.get("updatedAt")) or 0
    secondary_time = automation_brief_time_ms(secondary.get("updatedAt")) or 0
    return primary if primary_time >= secondary_time else secondary


def load_remote_automation_briefs() -> dict[str, Any]:
    url = automation_briefs_remote_url()
    cached_remote = read_json_cache(AUTOMATION_BRIEFS_REMOTE_CACHE_PATH)
    bundled_remote = load_bundled_automation_briefs()
    if not url:
        return prefer_newer_automation_payload(cached_remote, bundled_remote)
    cached_age = time.time() - payload_cache_time(cached_remote) if cached_remote else 10**9
    if cached_remote and cached_age < int(safe_float(env_value("AUTOMATION_BRIEFS_REMOTE_TTL_SECONDS", "120"), 120)):
        return prefer_newer_automation_payload(cached_remote, bundled_remote)
    try:
        response = requests.get(
            url,
            headers={
                **HEADERS,
                "Accept": "application/json,text/plain,*/*",
                "Cache-Control": "no-cache",
            },
            timeout=int(safe_float(env_value("AUTOMATION_BRIEFS_REMOTE_TIMEOUT", "12"), 12)),
        )
        response.raise_for_status()
        payload = normalize_automation_briefs_payload(response.json(), source_name="github")
        payload["_cache"] = {"key": "automation-briefs-remote", "updatedAt": int(time.time() * 1000), "stale": False}
        payload["remoteUrl"] = url
        if payload.get("briefs"):
            write_json_cache(AUTOMATION_BRIEFS_REMOTE_CACHE_PATH, payload)
            return prefer_newer_automation_payload(payload, bundled_remote)
    except Exception:
        pass
    return prefer_newer_automation_payload(cached_remote, bundled_remote)


def merge_automation_brief(local: dict[str, Any], remote: dict[str, Any] | None) -> dict[str, Any]:
    if not remote or not automation_brief_has_content(remote):
        return local
    if automation_brief_has_content(local):
        local_time = automation_brief_time_ms(local.get("completedAt")) or 0
        remote_time = automation_brief_time_ms(remote.get("completedAt")) or 0
        if local_time >= remote_time:
            return local
    merged = dict(local)
    merged.update({key: value for key, value in remote.items() if value not in (None, "")})
    if local.get("status") and local.get("status") != "UNKNOWN":
        merged["status"] = local["status"]
    if local.get("rrule"):
        merged["rrule"] = local["rrule"]
    return merged


def automation_config(automation_id: str) -> dict[str, Any]:
    path = CODEX_HOME / "automations" / automation_id / "automation.toml"
    if not path.exists():
        return {"id": automation_id, "name": automation_id, "status": "UNKNOWN", "rrule": "", "target_thread_id": ""}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return {
        "id": data.get("id") or automation_id,
        "name": data.get("name") or automation_id,
        "status": data.get("status") or "UNKNOWN",
        "rrule": data.get("rrule") or "",
        "target_thread_id": data.get("target_thread_id") or "",
        "updated_at": data.get("updated_at"),
    }


def automation_run_threads(automation_id: str) -> list[str]:
    db_path = CODEX_HOME / "sqlite" / "codex-dev.db"
    thread_ids: list[str] = []
    if db_path.exists():
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                """
                select thread_id
                from automation_runs
                where automation_id = ?
                order by coalesce(updated_at, created_at) desc
                limit 12
                """,
                (automation_id,),
            ).fetchall()
            thread_ids.extend(row[0] for row in rows if row and row[0])
            conn.close()
        except Exception:
            pass
    config_thread = automation_config(automation_id).get("target_thread_id")
    if config_thread:
        thread_ids.append(config_thread)
    return list(dict.fromkeys(thread_ids))


def session_files_for_threads(thread_ids: list[str]) -> list[Path]:
    files: list[Path] = []
    roots = [CODEX_HOME / "sessions", CODEX_HOME / "archived_sessions"]
    for thread_id in thread_ids:
        for root in roots:
            if not root.exists():
                continue
            files.extend(root.rglob(f"*{thread_id}.jsonl"))
    return sorted(set(files), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)


def message_from_payload(obj: dict[str, Any]) -> tuple[str | None, str]:
    payload = obj.get("payload") or {}
    if obj.get("type") == "response_item" and payload.get("type") == "message":
        parts = payload.get("content") or []
        text = "\n".join(part.get("text") or "" for part in parts if isinstance(part, dict))
        return payload.get("role"), text
    if obj.get("type") == "event_msg":
        event_type = payload.get("type")
        if event_type == "user_message":
            return "user", payload.get("message") or ""
        if event_type == "agent_message":
            return "assistant", payload.get("message") or ""
        if event_type == "task_complete":
            return "task_complete", payload.get("last_agent_message") or ""
    return None, ""


def is_automation_start(text: str, automation_id: str) -> bool:
    return (
        re.search(rf"Automation ID:\s*{re.escape(automation_id)}(?:\s|$)", text) is not None
        or re.search(rf"<automation_id>\s*{re.escape(automation_id)}\s*</automation_id>", text) is not None
    )


def parse_automation_records(path: Path, automation_id: str, name: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return records
    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        role, text = message_from_payload(obj)
        timestamp = obj.get("timestamp")
        if role == "user" and is_automation_start(text, automation_id):
            if current and current.get("content"):
                records.append(current)
            current = {
                "id": automation_id,
                "name": name,
                "startedAt": parse_iso_ms(timestamp),
                "completedAt": None,
                "content": "",
                "sourceFile": str(path),
            }
            first_line = text.splitlines()[0] if text.splitlines() else ""
            if first_line.startswith("Automation:"):
                current["name"] = first_line.split(":", 1)[1].strip() or name
            continue
        if not current:
            continue
        if role == "assistant" and text:
            current["content"] = clean_automation_message(text)
            current["completedAt"] = parse_iso_ms(timestamp)
        elif role == "task_complete" and text:
            current["content"] = clean_automation_message(text)
            current["completedAt"] = parse_iso_ms(timestamp)
            records.append(current)
            current = None
    if current and current.get("content"):
        records.append(current)
    return records


def latest_automation_brief(automation_id: str) -> dict[str, Any]:
    config = automation_config(automation_id)
    thread_ids = automation_run_threads(automation_id)
    records: list[dict[str, Any]] = []
    for path in session_files_for_threads(thread_ids):
        records.extend(parse_automation_records(path, automation_id, config["name"]))
    records = [record for record in records if record.get("content")]
    records.sort(key=lambda record: record.get("completedAt") or record.get("startedAt") or 0, reverse=True)
    latest = records[0] if records else {}
    return {
        "id": automation_id,
        "name": config["name"],
        "status": config["status"],
        "rrule": config["rrule"],
        "updatedAt": config.get("updated_at"),
        "completedAt": latest.get("completedAt"),
        "sourceFile": latest.get("sourceFile"),
        "content": latest.get("content") or AUTOMATION_BRIEF_PLACEHOLDER,
    }


def automation_briefs_payload() -> dict[str, Any]:
    ids = list(AUTOMATION_BRIEF_IDS)
    local_briefs = [latest_automation_brief(automation_id) for automation_id in ids]
    remote_payload = load_remote_automation_briefs()
    remote_by_id = {
        str(item.get("id")): item
        for item in remote_payload.get("briefs", [])
        if isinstance(item, dict) and item.get("id")
    }
    briefs = [merge_automation_brief(brief, remote_by_id.get(brief.get("id"))) for brief in local_briefs]
    updated_at = max(
        [int(time.time() * 1000)]
        + [int(automation_brief_time_ms(item.get("completedAt") or item.get("updatedAt")) or 0) for item in briefs]
        + [int(automation_brief_time_ms(remote_payload.get("updatedAt")) or 0)]
    )
    return {
        "updatedAt": updated_at,
        "source": "local+github" if remote_payload.get("briefs") else "local",
        "remoteLoaded": bool(remote_payload.get("briefs")),
        "remoteUpdatedAt": remote_payload.get("updatedAt"),
        "briefs": briefs,
    }


def market_payload() -> dict[str, Any]:
    sources = []
    fetchers = [
        ("binance", fetch_binance),
        ("okx", fetch_okx),
        ("okx-dex", fetch_okx_dex_hot),
        ("bitget", fetch_bitget),
        ("aicoin", fetch_aicoin),
        ("futu-hk", lambda: fetch_futu_hot("hk")),
        ("futu-us", lambda: fetch_futu_hot("us")),
        ("ths", fetch_ths_hot),
    ]
    for key, fetcher in fetchers:
        fallback_group = "hk" if "futu-hk" in key else "us" if "futu-us" in key else "cn" if key == "ths" else "crypto"
        sources.append(
            cached_or_fallback_source(
                key,
                fetcher,
                api_key="market-hot",
                fallback_group=fallback_group,
                error_title=key,
                error_subtitle="数据源请求失败",
                error_empty_title="数据源请求失败",
            )
        )
    return {"updatedAt": int(time.time() * 1000), "sources": sources}


def deepseek_enabled(settings: dict[str, Any] | None = None) -> bool:
    return bool(clean_api_key((settings or system_llm_settings()).get("apiKey")))


def deepseek_cache_ttl_seconds() -> int:
    return int(safe_float(env_value("DEEPSEEK_INSIGHT_CACHE_TTL", "86400"), 86400))


def deepseek_max_rows(settings: dict[str, Any] | None = None) -> int:
    if settings:
        return max(1, min(80, int(safe_float(settings.get("maxRows"), 36))))
    return max(1, min(80, int(safe_float(env_value("DEEPSEEK_MAX_ROWS", "36"), 36))))


def deepseek_batch_rows(settings: dict[str, Any] | None = None) -> int:
    configured = int(safe_float(env_value("DEEPSEEK_BATCH_ROWS", "10"), 10))
    return max(3, min(12, configured, deepseek_max_rows(settings)))


def deepseek_max_total_rows(settings: dict[str, Any] | None = None) -> int:
    max_rows = deepseek_max_rows(settings)
    return max(max_rows, min(500, int(safe_float(env_value("DEEPSEEK_MAX_TOTAL_ROWS", "300"), 300))))


def deepseek_normalize_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:220]


def deepseek_market_narrative_overrides() -> dict[str, list[str]]:
    defaults = {
        "HYPE": [
            "HYPE / Hyperliquid 近两天市场讨论极高，不能只按短线涨跌判断。",
            "核心明牌原因：价格接近或突破历史高位，24h/7d 动量强，交易所和链上成交都在放大。",
            "机构叙事升温：Bitwise BHYP、21Shares Hyperliquid ETP/相关产品让市场把 HYPE 当成链上交易所股权/平台币重新定价。",
            "真正情绪点：Hyperliquid 从 Perp DEX 扩展为全球链上交易所/交易基础设施叙事，平台收入、回购买盘、交易活跃度和生态资产上链交易预期共同强化。",
            "衍生叙事：trade.xyz / SpaceX pre-IPO 等传统资产上链交易讨论提升了 Hyperliquid 生态想象空间。",
        ],
        "HYPERLIQUID": [
            "Hyperliquid 当前讨论重点是链上交易所/平台币重估、ETF/ETP 机构入口、平台收入和回购买盘，而不只是 Perp DEX。",
        ],
        "SPCX": [
            "SPCX / SpaceX pre-IPO 属于传统资产上链交易叙事，市场关注点在 pre-IPO 资产、链上流动性和 Hyperliquid/trade.xyz 生态想象。",
        ],
        "XAU": [
            "黄金相关标的优先按避险、实际利率、美元和地缘风险判断，不要误归到 AI 或新币热度。",
        ],
    }
    raw = env_value("MARKET_NARRATIVE_OVERRIDES", "")
    if raw:
        try:
            custom = json.loads(raw)
            if isinstance(custom, dict):
                for key, value in custom.items():
                    norm_key = re.sub(r"[^A-Z0-9]", "", str(key or "").upper())
                    if not norm_key:
                        continue
                    if isinstance(value, list):
                        defaults[norm_key] = [clean_feed_text(item, 180) for item in value if clean_feed_text(item, 180)]
                    else:
                        text = clean_feed_text(value, 420)
                        if text:
                            defaults[norm_key] = [text]
        except Exception:
            pass
    return defaults


def deepseek_narrative_taxonomy() -> dict[str, dict[str, Any]]:
    return {
        "HYPE": {"aliases": ["HYPERLIQUID"], "context": "Hyperliquid：链上交易所/平台币重估、永续 DEX、平台收入、回购买盘、机构 ETF/ETP 入口。"},
        "BSB": {"aliases": ["BLOCK STREET TOKEN"], "context": "BSB：社区 Meme/新币资产，关注社区传播、交易所上新后的定价分歧。"},
        "BILL": {"aliases": ["BLOCK STREET TOKEN"], "context": "BILL：社区 Meme/新币资产，关注交易所上新后的社区传播和短线定价分歧。"},
        "UB": {"aliases": ["USELESS", "USELESS COIN"], "context": "UB/Useless：社区 Meme 资产，关注社群传播、Meme 风险偏好和短线换手。"},
        "BEAT": {"aliases": ["AUDIUS", "BEAT"], "context": "BEAT：音乐/创作者经济相关资产，关注娱乐消费、创作者平台和新币定价。"},
        "LAB": {"aliases": ["LAB"], "context": "LAB：新币生态资产，关注项目生态进展、交易所上新后的定价分歧。"},
        "EDEN": {"aliases": ["OPENEDEN"], "context": "OpenEden：RWA/代币化美债，关注链上国债收益、机构 RWA 和合规资产。"},
        "CL": {"aliases": ["WTI", "CRUDE OIL", "OIL"], "context": "CL/原油：能源商品，关注供需、库存、地缘风险和通胀预期。"},
        "SPK": {"aliases": ["SPARK"], "context": "Spark：DeFi 借贷/稳定币收益生态，关注 Maker/Sky 生态、收益率和新币流动性。"},
        "ZEC": {"aliases": ["ZCASH"], "context": "Zcash：隐私币/老牌强势，行情常受隐私叙事、监管避险和老币轮动影响。"},
        "XAU": {"aliases": ["GOLD"], "context": "黄金：避险资产/实际利率/美元指数/地缘风险，不应误归类为 AI 或新币题材。"},
        "XAG": {"aliases": ["SILVER"], "context": "白银：贵金属/工业金属双属性，关注避险、美元、实际利率和工业需求。"},
        "TRX": {"aliases": ["TRON"], "context": "TRON：稳定币结算网络/支付，关注 USDT 结算规模、链上收入和合规讨论。"},
        "BNB": {"aliases": ["BINANCE COIN"], "context": "BNB：交易所平台币/BNB Chain 生态，关注 Launchpool、Megadrop、交易所流量和链上生态。"},
        "BGB": {"aliases": ["BITGET TOKEN"], "context": "BGB：交易所平台币/Bitget 生态，关注交易所增长、合约流量和平台权益。"},
        "OKB": {"aliases": ["OKX"], "context": "OKB：交易所平台币/OKX 生态，关注交易所流量、Web3 钱包和平台权益。"},
        "SOL": {"aliases": ["SOLANA"], "context": "Solana：高性能公链/Meme/DePIN/DEX 生态，关注链上活跃、生态新币和资金轮动。"},
        "SUI": {"aliases": ["SUI NETWORK"], "context": "Sui：Move 公链，关注生态 TVL、游戏/DeFi/支付场景和 Move 板块轮动。"},
        "APT": {"aliases": ["APTOS"], "context": "Aptos：Move 公链，关注生态应用、链游/DeFi 和 Move 板块轮动。"},
        "ONDO": {"aliases": ["ONDO FINANCE"], "context": "Ondo：RWA/美债代币化核心资产，关注机构资金、代币化国债和合规叙事。"},
        "ENA": {"aliases": ["ETHENA"], "context": "Ethena：合成美元/收益型稳定币，关注 USDe 规模、收益率、资金费率和 DeFi 风险。"},
        "PENDLE": {"aliases": ["PENDLE FINANCE"], "context": "Pendle：收益交易/利率交易，关注收益率、积分/空投和 DeFi 收益轮动。"},
        "AAVE": {"aliases": ["AAVE"], "context": "Aave：DeFi 借贷龙头，关注借贷规模、协议收入、清算风险和机构 DeFi。"},
        "UNI": {"aliases": ["UNISWAP"], "context": "Uniswap：DEX 龙头，关注链上交易量、手续费机制、监管和前端/协议收入。"},
        "JUP": {"aliases": ["JUPITER"], "context": "Jupiter：Solana DEX 聚合/Launchpad，关注 Solana 交易活跃和生态新币发行。"},
        "GNS": {"aliases": ["GAINS NETWORK"], "context": "Gains Network：链上衍生品/杠杆交易，关注 DEX 合约成交和交易收入。"},
        "GMX": {"aliases": ["GMX"], "context": "GMX：链上永续合约/真实收益，关注衍生品交易量和费用收入。"},
        "DYDX": {"aliases": ["DYDX"], "context": "dYdX：去中心化合约交易所，关注链上衍生品成交、治理和平台收入。"},
        "AERO": {"aliases": ["AERODROME"], "context": "Aerodrome：Base 生态 DEX/流动性中心，关注 Base 链上交易和激励。"},
        "KAITO": {"aliases": ["KAITO AI"], "context": "Kaito：InfoFi/AI 信息金融，关注注意力市场、Yapper、项目营销和数据网络。"},
        "TAO": {"aliases": ["BITTENSOR"], "context": "Bittensor：AI 算力/去中心化机器学习，关注子网、AI 基础设施和算力叙事。"},
        "RENDER": {"aliases": ["RNDR", "RENDER NETWORK"], "context": "Render：AI/渲染算力/DePIN，关注 GPU 算力、渲染需求和 AI 基建。"},
        "FET": {"aliases": ["ASI", "FETCH"], "context": "FET/ASI：AI Agent/去中心化 AI 联盟，关注 Agent 应用、模型基础设施和合并叙事。"},
        "VIRTUAL": {"aliases": ["VIRTUALS"], "context": "Virtuals：AI Agent 发行平台/AgentFi，关注 Base 生态、Agent 资产发行和社区热度。"},
        "WLD": {"aliases": ["WORLDCOIN"], "context": "Worldcoin：AI 身份/人类证明，关注身份网络、监管和 OpenAI 相关情绪。"},
        "NEAR": {"aliases": ["NEAR PROTOCOL"], "context": "NEAR：AI 公链/基础设施，关注 AI 原生链、数据可用性和生态应用。"},
        "LINK": {"aliases": ["CHAINLINK"], "context": "Chainlink：预言机/RWA/跨链互操作，关注 CCIP、机构数据和代币化资产。"},
        "PYTH": {"aliases": ["PYTH NETWORK"], "context": "Pyth：预言机/Solana 生态，关注交易所数据、DeFi 使用和链上资产定价。"},
        "JTO": {"aliases": ["JITO"], "context": "Jito：Solana MEV/流动性质押，关注 Solana 质押收益和 MEV 收入。"},
        "RAY": {"aliases": ["RAYDIUM"], "context": "Raydium：Solana DEX/Launchpad，关注 Meme 发行、链上成交和流动性。"},
        "BONK": {"aliases": ["BONK"], "context": "Bonk：Solana Meme/社区情绪，关注 Solana 热度、交易所上新和风险偏好。"},
        "WIF": {"aliases": ["DOGWIFHAT"], "context": "WIF：Solana Meme，关注社区情绪、KOL 传播和 Solana Meme 轮动。"},
        "PEPE": {"aliases": ["PEPE"], "context": "PEPE：主流 Meme，关注风险偏好、交易所流量和 Meme 板块轮动。"},
        "FLOKI": {"aliases": ["FLOKI"], "context": "Floki：Meme/游戏/社区营销，关注 Meme 情绪和生态产品推进。"},
        "DOGE": {"aliases": ["DOGECOIN"], "context": "Dogecoin：老牌 Meme/支付/马斯克情绪，关注社区传播和风险偏好。"},
        "SHIB": {"aliases": ["SHIBA"], "context": "Shiba：老牌 Meme/Shibarium，关注社区情绪、生态燃烧和 Meme 板块轮动。"},
        "SEI": {"aliases": ["SEI NETWORK"], "context": "Sei：高性能交易链，关注并行 EVM、链上交易应用和生态流动性。"},
        "TIA": {"aliases": ["CELESTIA"], "context": "Celestia：模块化区块链/数据可用性，关注 Rollup 生态和解锁风险。"},
        "STRK": {"aliases": ["STARKNET"], "context": "Starknet：ZK Layer2，关注生态活跃、解锁和 ZK 板块轮动。"},
        "ARB": {"aliases": ["ARBITRUM"], "context": "Arbitrum：以太坊 Layer2，关注 DeFi TVL、生态收入和 L2 板块。"},
        "OP": {"aliases": ["OPTIMISM"], "context": "Optimism：Superchain/L2，关注生态扩张、OP Stack 和收入。"},
        "MANTA": {"aliases": ["MANTA"], "context": "Manta：ZK/L2，关注 ZK 应用、生态活动和新币流动性。"},
        "ORDI": {"aliases": ["ORDINALS"], "context": "ORDI：Bitcoin 铭文/BRC-20，关注 BTC 生态、铭文成交和资金轮动。"},
        "SATS": {"aliases": ["1000SATS"], "context": "SATS：Bitcoin 铭文/Meme，关注 BTC 生态和小票资金轮动。"},
        "RUNE": {"aliases": ["THORCHAIN"], "context": "THORChain：BTCFi/跨链流动性，关注原生 BTC 交易和跨链收入。"},
        "FIDA": {"aliases": ["BONFIDA"], "context": "Bonfida：Solana 基础设施/域名/DEX 工具，关注 Solana 生态和老币轮动。"},
        "LAYER": {"aliases": ["SOLAYER"], "context": "Solayer：Solana Restaking/再质押，关注 Solana 生态、质押收益和新币流动性。"},
        "PUMP": {"aliases": ["PUMP FUN", "PUMPFUN"], "context": "pump.fun：Meme 发行平台/Launchpad，关注 Solana/Base Meme 发行热度和平台收入。"},
    }


def deepseek_taxonomy_entry(term: str) -> dict[str, Any] | None:
    key = re.sub(r"[^A-Z0-9]", "", str(term or "").upper())
    if not key:
        return None
    taxonomy = deepseek_narrative_taxonomy()
    if key in taxonomy:
        return taxonomy[key]
    for entry_key, entry in taxonomy.items():
        aliases = [re.sub(r"[^A-Z0-9]", "", str(alias or "").upper()) for alias in entry.get("aliases", [])]
        if key in aliases:
            return {"aliases": [entry_key, *entry.get("aliases", [])], "context": entry.get("context", "")}
    return None


def deepseek_asset_terms(row: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    raw_values = [
        row.get("symbol"),
        row.get("name"),
    ]
    for value in raw_values:
        text = clean_feed_text(value, 120)
        if not text:
            continue
        terms.append(text)
        for part in re.split(r"[\s/_\-\(\)\[\]·|,，]+", text):
            part = part.strip()
            if part:
                terms.append(part)
    normalized: list[str] = []
    seen: set[str] = set()
    for term in terms:
        clean = term.strip()
        if not clean:
            continue
        upper = re.sub(r"[^A-Z0-9]", "", clean.upper())
        if upper:
            for suffix in ("PERPETUAL", "SWAP", "USDT", "USDC", "FDUSD", "TUSD", "BUSD", "USD", "EUR"):
                if upper.endswith(suffix) and len(upper) > len(suffix) + 1:
                    upper = upper[: -len(suffix)]
            if 2 <= len(upper) <= 20 and upper not in seen:
                seen.add(upper)
                normalized.append(upper)
        if re.search(r"[\u4e00-\u9fff]", clean) and 2 <= len(clean) <= 24 and clean not in seen:
            seen.add(clean)
            normalized.append(clean)
    return normalized[:10]


def deepseek_asset_terms_with_aliases(row: dict[str, Any]) -> list[str]:
    terms = deepseek_asset_terms(row)
    expanded = list(terms)
    for term in terms:
        entry = deepseek_taxonomy_entry(term)
        if not entry:
            continue
        for alias in entry.get("aliases", []):
            alias_text = str(alias or "").strip()
            if alias_text:
                expanded.append(alias_text)
    deduped: list[str] = []
    seen: set[str] = set()
    for term in expanded:
        clean = term.strip()
        if not clean:
            continue
        marker = re.sub(r"[^A-Z0-9\u4e00-\u9fff]", "", clean.upper())
        if marker and marker not in seen:
            seen.add(marker)
            deduped.append(clean)
    return deduped[:16]


def deepseek_row_taxonomy_context(row: dict[str, Any]) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for term in deepseek_asset_terms(row):
        entry = deepseek_taxonomy_entry(term)
        context = clean_feed_text(entry.get("context") if entry else "", 180)
        if context and context not in seen:
            seen.add(context)
            lines.append(context)
    group = clean_feed_text(row.get("group"), 24)
    source_title = clean_feed_text(row.get("sourceTitle"), 80)
    tags = row.get("tags") if isinstance(row.get("tags"), list) else []
    if group == "crypto" or any(word in source_title.lower() for word in ("binance", "okx", "bitget", "aicoin", "dex")):
        tag_text = clean_feed_text(" / ".join(str(tag) for tag in tags if tag), 160)
        if tag_text:
            lines.append(f"交易所/榜单标签：{tag_text}")
    return clean_feed_text("；".join(lines[:4]), 520)


def deepseek_flat_text(value: Any, limit: int = 800) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return clean_feed_text(value, limit)
    if isinstance(value, dict):
        parts = []
        for key in ("title", "text", "fullText", "content", "summary", "name", "sourceName", "handle"):
            if value.get(key):
                parts.append(clean_feed_text(value.get(key), 260))
        return clean_feed_text(" ".join(parts), limit)
    if isinstance(value, list):
        return clean_feed_text(" ".join(deepseek_flat_text(item, 180) for item in value[:5]), limit)
    return clean_feed_text(value, limit)


def deepseek_discussion_item_time(item: dict[str, Any]) -> int:
    for key in ("publishedAt", "updatedAt", "completedAt", "createdAt"):
        value = safe_float(item.get(key))
        if value:
            return int(value if value > 10_000_000_000 else value * 1000)
    add_time = safe_float(item.get("add_time"))
    if add_time:
        return int(add_time if add_time > 10_000_000_000 else add_time * 1000)
    return 0


def deepseek_load_discussion_items() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - int(safe_float(env_value("MARKET_NARRATIVE_LOOKBACK_DAYS", "7"), 7)) * 24 * 60 * 60 * 1000

    def add_item(source: str, item: dict[str, Any], text: str) -> None:
        text = clean_feed_text(text, 800)
        if not text:
            return
        ts = deepseek_discussion_item_time(item)
        if ts and ts < cutoff_ms:
            return
        candidates.append({"source": source, "time": ts, "text": text})

    news = read_json_cache(PERSIST_CACHE_DIR / "api_newsflash.json")
    for item in news.get("items", []) if isinstance(news.get("items"), list) else []:
        if isinstance(item, dict):
            add_item("律动快讯", item, " ".join([deepseek_flat_text(item.get("title"), 220), deepseek_flat_text(item.get("content"), 520)]))

    x_payloads = [read_json_cache(PERSIST_CACHE_DIR / "api_x-kol-feed-v4.json")]
    for path in PERSIST_CACHE_DIR.glob("api_x-kol-feed-v4-u*.json"):
        x_payloads.append(read_json_cache(path))
    for payload in x_payloads:
        for item in payload.get("items", []) if isinstance(payload.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            quote = deepseek_flat_text(item.get("quote"), 360)
            text = " ".join([
                deepseek_flat_text(item.get("sourceName") or item.get("handle"), 80),
                deepseek_flat_text(item.get("title"), 220),
                deepseek_flat_text(item.get("text") or item.get("fullText"), 420),
                quote,
            ])
            add_item("X/KOL", item, text)

    briefs = read_json_cache(PERSIST_CACHE_DIR / "api_automation-briefs.json")
    for item in briefs.get("briefs", []) if isinstance(briefs.get("briefs"), list) else []:
        if isinstance(item, dict):
            add_item("自动简报", item, deepseek_flat_text(item.get("content"), 900))

    candidates.sort(key=lambda item: item.get("time") or 0, reverse=True)
    return candidates[: int(safe_float(env_value("MARKET_NARRATIVE_CONTEXT_LIMIT", "180"), 180))]


def deepseek_term_in_text(term: str, text: str) -> bool:
    if not term or not text:
        return False
    if re.fullmatch(r"[A-Z0-9]{2,20}", term):
        if len(term) <= 2:
            return False
        return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text.upper()))
    return term in text


def deepseek_row_discussion_context(row: dict[str, Any], discussion_items: list[dict[str, Any]]) -> str:
    terms = deepseek_asset_terms_with_aliases(row)
    overrides = deepseek_market_narrative_overrides()
    lines: list[str] = []
    for term in terms:
        if term in overrides:
            lines.extend(overrides[term])
    for item in discussion_items:
        text = item.get("text") or ""
        if any(deepseek_term_in_text(term, text) for term in terms):
            prefix = item.get("source") or "市场讨论"
            lines.append(f"{prefix}: {text}")
        if len(lines) >= 6:
            break
    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        clean = clean_feed_text(line, 240)
        if clean and clean not in seen:
            seen.add(clean)
            deduped.append(clean)
    return clean_feed_text("；".join(deduped[:6]), 900)


def deepseek_settings_signature(settings: dict[str, Any] | None = None) -> str:
    resolved = settings or system_llm_settings()
    return "|".join(
        [
            clean_model_provider(resolved.get("provider")),
            clean_model_url(resolved.get("baseUrl")),
            clean_model_name(resolved.get("model")),
        ]
    )


def deepseek_row_hash(row: dict[str, Any], mode: str, settings: dict[str, Any] | None = None) -> str:
    facts = {
        "version": 9,
        "mode": mode,
        "model": deepseek_settings_signature(settings),
        "sourceId": row.get("sourceId"),
        "sourceTitle": row.get("sourceTitle"),
        "group": row.get("group"),
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "tags": row.get("tags"),
        "taxonomyContext": row.get("taxonomyContext"),
        "discussionContext": row.get("discussionContext"),
    }
    raw = json.dumps(facts, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def deepseek_load_insight_cache() -> dict[str, Any]:
    payload = read_json_cache(DEEPSEEK_INSIGHTS_CACHE_PATH)
    return payload if isinstance(payload, dict) else {}


def deepseek_save_insight_cache(payload: dict[str, Any]) -> None:
    slim = dict(sorted(payload.items(), key=lambda item: safe_float(item[1].get("updatedAt") if isinstance(item[1], dict) else 0))[-3000:])
    write_json_cache(DEEPSEEK_INSIGHTS_CACHE_PATH, slim)


def deepseek_compact_rows(payload: dict[str, Any], settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    mode = clean_feed_text(payload.get("mode") or "hot", 24)
    discussion_items = deepseek_load_discussion_items()
    for source in payload.get("sources") if isinstance(payload.get("sources"), list) else []:
        if not isinstance(source, dict):
            continue
        source_id = deepseek_normalize_key(source.get("id") or source.get("sourceId") or source.get("title"))
        source_title = clean_feed_text(source.get("title"), 80)
        group = clean_feed_text(source.get("group"), 24)
        source_name = clean_feed_text(source.get("sourceName"), 100)
        for row in source.get("rows") if isinstance(source.get("rows"), list) else []:
            if not isinstance(row, dict):
                continue
            row_key = deepseek_normalize_key(row.get("key"))
            if not row_key:
                row_key = deepseek_normalize_key(
                    "|".join(
                        [
                            mode,
                            source_id,
                            clean_feed_text(row.get("symbol") or row.get("asset") or row.get("code"), 60),
                            clean_feed_text(row.get("name") or row.get("title"), 100),
                            clean_feed_text(row.get("url") or row.get("targetUrl"), 160),
                            str(int(safe_float(row.get("rank"), len(rows) + 1))),
                        ]
                    )
                )
            compact = {
                "key": row_key,
                "sourceId": source_id,
                "sourceTitle": source_title,
                "sourceName": source_name,
                "group": group,
                "rank": int(safe_float(row.get("rank"), len(rows) + 1)),
                "symbol": clean_feed_text(row.get("symbol"), 40),
                "name": clean_feed_text(row.get("name"), 80),
                "price": clean_feed_text(row.get("price"), 40),
                "change": clean_feed_text(row.get("change"), 40),
                "turnover": clean_feed_text(row.get("turnover"), 60),
                "heat": safe_float(row.get("heat")),
                "note": clean_feed_text(row.get("note"), 120),
                "tags": [clean_feed_text(tag, 40) for tag in row.get("tags", [])[:6]] if isinstance(row.get("tags"), list) else [],
                "mode": mode,
            }
            context = deepseek_row_discussion_context(compact, discussion_items)
            taxonomy_context = deepseek_row_taxonomy_context(compact)
            if taxonomy_context:
                compact["taxonomyContext"] = taxonomy_context
            if context:
                compact["discussionContext"] = context
            rows.append(compact)
    rows = [row for row in rows if row.get("key") and (row.get("symbol") or row.get("name"))]
    return rows[: deepseek_max_total_rows(settings)]


def deepseek_extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"items": data}
        return {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                return {"items": data}
        except Exception:
            pass
    match = re.search(r"\[.*\]", text, flags=re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            return {"items": data} if isinstance(data, list) else {}
        except Exception:
            return {}
    return {}


def deepseek_chat(messages: list[dict[str, str]], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = settings or system_llm_settings()
    base_url = clean_model_url(resolved.get("baseUrl")) or "https://api.deepseek.com"
    if not base_url.endswith("/chat/completions"):
        base_url = f"{base_url}/chat/completions"
    body = {
        "model": clean_model_name(resolved.get("model")) or "deepseek-v4-flash",
        "messages": messages,
        "temperature": safe_float(resolved.get("temperature"), 0.2),
        "max_tokens": int(safe_float(resolved.get("maxTokens"), 1800)),
        "response_format": {"type": "json_object"},
    }
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {clean_api_key(resolved.get('apiKey'))}",
        "Content-Type": "application/json",
    }
    timeout = max(6, int(safe_float(env_value("DEEPSEEK_TIMEOUT", "24"), 24)))
    response = requests.post(base_url, headers=headers, json=body, timeout=timeout)
    if response.status_code >= 400 and "response_format" in body:
        body.pop("response_format", None)
        response = requests.post(base_url, headers=headers, json=body, timeout=timeout)
    response.raise_for_status()
    return response.json()


def deepseek_rank_prompt(rows: list[dict[str, Any]], mode: str) -> list[dict[str, str]]:
    system = (
        "你是星云社的交易市场题材分析助手，擅长把榜单变化拆成明牌原因、真实情绪点和当前市场叙事。"
        "请综合交易所、榜单类型、标的名称、价格、涨跌幅、成交额、热度、备注、标签、row.taxonomyContext，以及 row.discussionContext 中的近期快讯/X/KOL/自动简报上下文。"
        "所有标的都必须按同一套逻辑处理：先看近期市场讨论，再看生态/板块/题材，再结合价格和成交额验证。"
        "如果 discussionContext 不为空，必须优先参考它；其次参考 taxonomyContext；不要只按静态板块或旧项目标签判断。"
        "必须谨慎：可以基于输入给出判断，但不要编造没有依据的合作、上市或监管信息。"
        "ETF/ETP、巨鲸、KOL、回购、空头挤压、监管、产品上线、合作、融资、上市等催化词，只有在上下文、标签或备注中明确出现时才能写；没有依据时改写成更保守的题材判断。"
        "输出必须像交易员写给自己看的短评，不能复述交易类型、榜单名、涨跌幅或成交是否活跃。"
        "输出必须是 JSON 对象，不要输出 Markdown。"
    )
    user = {
        "task": "为榜单行生成短提示，展示在网页标的名称旁边。",
        "mode": mode,
        "rules": [
            "必须为每一条输入行返回一个对象；items 数量必须等于 rows 数量，不能返回空数组。",
            "如果没有具体催化、题材、生态、舆论或上下文证据，不要用榜单类型、合约属性、成交额、涨跌幅硬凑分析；该行 reason/theme/detail 返回空字符串。",
            "detail 最多 18 个中文字符，格式优先为“明牌原因 · 情绪点”，不要输出很长句子。",
            "reason 只用短词，但必须有信息增量，例如 机构叙事、平台重估、ETF预期、RWA代币化、Meme轮动、AI算力、链上收入、回购买盘、生态上新、监管催化、巨鲸博弈、空头挤压、传统资产上链。",
            "theme 要参考加密货币生态/板块/题材、股票行业、IPO/新币叙事、近期舆论热点，最多 12 个中文字符。",
            "HYPE / Hyperliquid 只是样例，不是唯一特例；其他标的也要同样按最近讨论、官方板块和交易数据重新判断。",
            "HYPE / Hyperliquid 若出现，优先理解为“价格新高 + ETF/ETP机构叙事 + 链上交易所/平台币重估 + 买盘/收入预期”，不要只写 Perp DEX。",
            "如果 discussionContext 提到 ETF、ETP、pre-IPO、收入、回购、KOL、巨鲸、空头、监管或产品上线，detail 要抓最能解释当下热度的那一个，不要泛化成“高热关注”。",
            "如果 discussionContext 没有命中，也要基于 taxonomyContext、交易所标签、成交额/涨跌幅给出具体题材，避免只写“高热关注”。",
            "不要为了显得专业而硬加没有证据的“巨鲸”“机构”“ETF”“监管”等词；例如隐私币只看到 Zcash/隐私/抗审查，就写“隐私叙事”或“抗审查交易”，不要写“巨鲸博弈”。",
            "禁止输出这些空泛词作为 reason/detail/theme：高热关注、高热上榜、高热度上榜、热度上榜、热度榜首、热门币种、局部异动、成交活跃、成交放量、成交平稳、成交普通、成交密集、成交密度、成交额大、成交额较大、成交额巨大、成交额放大、成交额靠前、成交额领先、成交占优、成交排名、成交榜、成交量大、成交量较大、资金密集、流动性一般、价格波动、短线波动、温和上涨、小幅波动、强势领涨、领涨、补涨、赛道上扬、走强、走高、拉升、冲高、市场热度、板块轮动、榜单异动、资金关注、交易所热度、合约交易、合约标的、合约博弈、合约热炒、合约热度、合约情绪、合约密集、合约拥挤、合约活跃、合约热、热炒、热点炒作、炒作热度、涨幅惊人、涨幅可观、涨幅客观、涨幅明显、涨幅扩大、涨幅较大、涨幅XX%、涨幅12%、24h涨幅。看到这些词要改写成更具体的题材或情绪点。",
            "不要把价格数字本身当作理由：例如“涨幅惊人”“涨幅可观”“涨幅30%”“24h涨幅高”都不合格；必须说明它背后的催化，如 ETF/ETP、产品上线、收入/回购、空头挤压、生态迁移、链上资金、监管变化、KOL/社区叙事等。",
            "tone 只能是 is-hot 或空字符串；仅在确实高热/强异动/重点叙事时用 is-hot。",
            "必须保留输入 index，用于前端把结果映射回正确标的。",
            "key 使用输入里的短 key 原样返回即可，不要生成长链接或新增不存在的 key。",
        ],
        "rows": rows,
        "output_schema": {"items": [{"index": 1, "key": "输入key", "reason": "短原因", "theme": "短题材", "detail": "短提示", "tone": "is-hot或空"}]},
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


DEEPSEEK_EMPTY_INSIGHT_PATTERNS = [
    r"高热关注",
    r"高热上榜",
    r"高热度上榜",
    r"高热.{0,4}上榜",
    r"热度榜首",
    r"热度上榜",
    r"热门币种",
    r"局部异动",
    r"成交活跃",
    r"成交密集",
    r"成交密度",
    r"成交额大",
    r"成交额较大",
    r"成交额巨大",
    r"成交额放大",
    r"成交额靠前",
    r"成交额领先",
    r"成交占优",
    r"成交排名",
    r"成交榜",
    r"成交量大",
    r"成交量较大",
    r"资金密集",
    r"强势领涨",
    r"成交平稳",
    r"成交普通",
    r"流动性一般",
    r"价格波动",
    r"短线波动",
    r"合约交易",
    r"合约标的",
    r"合约博弈",
    r"合约热炒",
    r"合约热度",
    r"合约情绪",
    r"合约密集",
    r"合约拥挤",
    r"合约活跃",
    r"合约热",
    r"热炒",
    r"热点炒作",
    r"炒作热度",
    r"领涨",
    r"补涨",
    r"赛道上扬",
    r"走强",
    r"走高",
    r"拉升",
    r"冲高",
    r"成交放量",
    r"温和上涨",
    r"小幅波动",
    r"市场热度",
    r"板块轮动",
    r"榜单异动",
    r"资金关注",
    r"交易所热度",
    r"涨幅惊人",
    r"涨幅可观",
    r"涨幅客观",
    r"涨幅明显",
    r"涨幅扩大",
    r"涨幅较大",
    r"涨幅\s*[xXｘＸ]+%",
    r"涨幅.{0,6}[+\-]?\d+(\.\d+)?%",
    r"24h\s*涨幅",
    r"24小时\s*涨幅",
    r"\d+(\.\d+)?%\s*涨幅",
]


def deepseek_empty_insight_text(value: str) -> bool:
    text = clean_feed_text(value, 80)
    if not text:
        return False
    compact = re.sub(r"\s+", "", text)
    return any(re.search(pattern, compact, re.I) for pattern in DEEPSEEK_EMPTY_INSIGHT_PATTERNS)


def deepseek_symbol_fallbacks() -> dict[str, tuple[str, str, str, str]]:
    return {
        "HYPE": ("ETF/ETP机构叙事", "链上交易所重估", "机构入口·平台收入买盘", "is-hot"),
        "ZEC": ("隐私叙事", "隐私币", "隐私叙事·抗审查交易", ""),
        "XAU": ("避险情绪", "贵金属", "实际利率·避险资产", ""),
        "XAG": ("避险情绪", "贵金属", "白银·工业金属双属性", ""),
        "CL": ("能源宏观", "原油资产", "原油供需·地缘风险", ""),
        "EDEN": ("RWA美债", "代币化国债", "RWA美债·收益资产", ""),
        "ONDO": ("RWA代币化", "美债资产", "代币化国债·机构入口", "is-hot"),
        "GNS": ("链上收入", "衍生品协议", "Gains收入·链上杠杆", ""),
        "BILL": ("社区Meme", "Meme新币", "社区Meme·新币定价", ""),
        "BSB": ("社区Meme", "Meme新币", "社区Meme·新币定价", ""),
        "UB": ("社区Meme", "Meme新币", "Useless社区Meme", ""),
        "BEAT": ("娱乐资产", "创作者经济", "音乐平台·创作者经济", ""),
        "LAB": ("生态资产", "新币定价", "生态进展·首发定价", ""),
        "SPK": ("Sky生态", "DeFi收益", "Spark收益资产", ""),
        "SUI": ("Move生态", "公链生态", "Move生态·应用扩张", ""),
        "NEAR": ("AI公链", "AI基础设施", "AI公链·数据应用", ""),
        "LAYER": ("Solana再质押", "Restaking", "Solana再质押", ""),
        "FIDA": ("Solana工具", "基础设施", "域名/DEX工具", ""),
        "PUMP": ("Meme发行", "Launchpad", "Meme发行平台", "is-hot"),
        "PEPE": ("Meme轮动", "主流Meme", "主流Meme·风险偏好", ""),
        "ASTER": ("新币定价", "衍生品生态", "衍生品平台叙事", ""),
        "ALT": ("模块化生态", "Rollup服务", "Rollup服务·再质押", ""),
        "ALLO": ("RWA代币化", "资产发行", "链上资产发行", ""),
        "PROVE": ("证明网络", "基础设施", "证明网络·隐私计算", ""),
        "QI": ("BNB生态", "DeFi借贷", "BNB生态借贷", ""),
    }


def deepseek_context_fallback(row: dict[str, Any], mode: str) -> dict[str, str] | None:
    key = deepseek_normalize_key(row.get("key"))
    if not key:
        return None
    terms = deepseek_asset_terms(row)
    fallbacks = deepseek_symbol_fallbacks()
    for term in terms:
        compact = re.sub(r"[^A-Z0-9]", "", term.upper())
        if compact in fallbacks:
            reason, theme, detail, tone = fallbacks[compact]
            return {"key": key, "reason": reason, "theme": theme, "detail": detail, "tone": tone, "provider": "taxonomy"}

    context = " ".join(
        clean_feed_text(row.get(field), 260)
        for field in ("discussionContext", "taxonomyContext", "note", "sourceTitle", "sourceName")
        if row.get(field)
    )
    tag_text = " ".join(str(tag) for tag in row.get("tags", []) if tag) if isinstance(row.get("tags"), list) else ""
    text = f"{context} {tag_text}"
    patterns: list[tuple[str, str, str, str]] = [
        (r"ETF|ETP|机构入口|平台币重估", "机构叙事", "平台重估", "机构入口·平台重估"),
        (r"RWA|美债|国债|代币化", "RWA代币化", "现实资产", "RWA资产·合规入口"),
        (r"Meme|MEME|社区", "社区传播", "Meme资产", "社区传播·Meme轮动"),
        (r"AI|Agent|算力|模型", "AI叙事", "AI基础设施", "AI叙事·应用扩散"),
        (r"隐私|抗审查|Zcash", "隐私叙事", "隐私币", "隐私叙事·抗审查交易"),
        (r"黄金|白银|贵金属|避险", "避险情绪", "贵金属", "实际利率·避险资产"),
        (r"原油|能源|WTI|库存|地缘", "能源宏观", "原油资产", "原油供需·地缘风险"),
        (r"Solana|SOL", "Solana生态", "生态资产", "Solana生态轮动"),
        (r"Move|Sui|Aptos", "Move生态", "公链生态", "Move生态·应用扩张"),
        (r"Layer2|ZK|Rollup", "扩容叙事", "Layer2", "扩容生态·应用推进"),
        (r"Launchpad|Launchpool|Megadrop|首发|上新", "首发定价", "新币资产", "首发定价·筹码分歧"),
    ]
    for pattern, reason, theme, detail in patterns:
        if re.search(pattern, text, re.I):
            return {"key": key, "reason": reason, "theme": theme, "detail": detail, "tone": "", "provider": "taxonomy"}

    source_title = clean_feed_text(row.get("sourceTitle") or row.get("sourceName"), 120).lower()
    if "dex" in source_title or "链上" in source_title:
        return {"key": key, "reason": "链上资金", "theme": "链上资产", "detail": "链上资金·社区传播", "tone": "", "provider": "taxonomy"}
    if row.get("group") == "crypto" or re.search(r"binance|okx|bitget|aicoin|币圈", source_title, re.I):
        return {"key": key, "reason": "题材待核验", "theme": "新币资产", "detail": "题材待核验·首发定价", "tone": "", "provider": "taxonomy"}
    return None


def normalize_deepseek_insight(item: dict[str, Any]) -> dict[str, str] | None:
    key = deepseek_normalize_key(item.get("key"))
    reason = clean_feed_text(item.get("reason"), 20)
    theme = clean_feed_text(item.get("theme"), 24)
    detail = clean_feed_text(item.get("detail"), 32)
    if deepseek_empty_insight_text(reason):
        reason = ""
    if deepseek_empty_insight_text(theme):
        theme = ""
    if deepseek_empty_insight_text(detail):
        detail = ""
    if not detail:
        detail = f"{reason} · {theme}" if reason and theme else reason or theme
    detail = detail.replace(" - ", " · ").replace(" / / ", " / ")[:32]
    if not detail:
        return None
    tone = "is-hot" if str(item.get("tone") or "").strip() == "is-hot" else ""
    provider = clean_feed_text(item.get("provider") or "deepseek", 20)
    return {"key": key, "reason": reason, "theme": theme, "detail": detail, "tone": tone, "provider": provider}


def deepseek_rank_insights_payload(payload: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved_settings = settings or system_llm_settings()
    mode = clean_feed_text(payload.get("mode") or "hot", 24)
    rows = deepseek_compact_rows(payload, resolved_settings)
    provider = clean_model_provider(resolved_settings.get("provider"))
    if not deepseek_enabled(resolved_settings):
        return {"ok": False, "enabled": False, "provider": provider, "insights": {}, "error": "missing API_KEY"}
    if not rows:
        return {"ok": True, "enabled": True, "provider": provider, "insights": {}}

    now = int(time.time() * 1000)
    ttl_ms = deepseek_cache_ttl_seconds() * 1000
    with DEEPSEEK_INSIGHTS_LOCK:
        cache = deepseek_load_insight_cache()

    insights: dict[str, dict[str, str]] = {}
    missing: list[dict[str, Any]] = []
    cache_dirty = False
    for row in rows:
        row_hash = deepseek_row_hash(row, mode, resolved_settings)
        cached_item = cache.get(row_hash) if isinstance(cache.get(row_hash), dict) else None
        if cached_item and now - safe_float(cached_item.get("updatedAt")) < ttl_ms and isinstance(cached_item.get("insight"), dict):
            insight = normalize_deepseek_insight({**cached_item["insight"], "key": row["key"]})
            if insight:
                insight["key"] = row["key"]
                insights[row["key"]] = insight
                normalized_cache_item = {k: v for k, v in insight.items() if k != "key"}
                if normalized_cache_item != cached_item.get("insight"):
                    cache[row_hash] = {"updatedAt": cached_item.get("updatedAt") or now, "insight": normalized_cache_item}
                    cache_dirty = True
            else:
                cache.pop(row_hash, None)
                cache_dirty = True
                missing.append({**row, "_hash": row_hash})
        else:
            missing.append({**row, "_hash": row_hash})

    if missing:
        batch_size = deepseek_batch_rows(resolved_settings)
        for batch_start in range(0, len(missing), batch_size):
            batch = missing[batch_start: batch_start + batch_size]
            model_rows = []
            for index, row in enumerate(batch, start=1):
                compact = {key: value for key, value in row.items() if key != "_hash"}
                compact["key"] = f"row-{index}"
                compact["index"] = index
                model_rows.append(compact)
            items = []
            for attempt in range(4):
                response = deepseek_chat(deepseek_rank_prompt(model_rows, mode), resolved_settings)
                content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed = deepseek_extract_json(content)
                items = parsed.get("items") if isinstance(parsed.get("items"), list) else []
                if len(items) >= len(batch):
                    break
                if attempt < 3:
                    time.sleep(0.35)
            row_by_key = {row["key"]: row for row in batch}
            used_hashes: set[str] = set()
            for item_index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                insight = normalize_deepseek_insight(item)
                if not insight:
                    continue
                target_row = row_by_key.get(insight["key"])
                if not target_row:
                    output_index = int(safe_float(item.get("index"), 0))
                    if 1 <= output_index <= len(batch):
                        target_row = batch[output_index - 1]
                    elif item_index < len(batch):
                        target_row = batch[item_index]
                if not target_row or target_row["_hash"] in used_hashes:
                    continue
                insight["key"] = target_row["key"]
                insights[target_row["key"]] = insight
                cache[target_row["_hash"]] = {"updatedAt": now, "insight": {k: v for k, v in insight.items() if k != "key"}}
                used_hashes.add(target_row["_hash"])
            for row in batch:
                if row["key"] in insights:
                    continue
                fallback = deepseek_context_fallback(row, mode)
                insight = normalize_deepseek_insight(fallback or {})
                if not insight:
                    continue
                insight["key"] = row["key"]
                insights[row["key"]] = insight
                cache[row["_hash"]] = {"updatedAt": now, "insight": {k: v for k, v in insight.items() if k != "key"}}
        with DEEPSEEK_INSIGHTS_LOCK:
            deepseek_save_insight_cache(cache)
    elif cache_dirty:
        with DEEPSEEK_INSIGHTS_LOCK:
            deepseek_save_insight_cache(cache)

    return {
        "ok": True,
        "enabled": True,
        "provider": provider,
        "model": clean_model_name(resolved_settings.get("model")) or "deepseek-v4-flash",
        "updatedAt": now,
        "insights": insights,
    }


def gainers_rankings_payload() -> dict[str, Any]:
    sources = []
    fetchers = [
        ("binance-gainers", fetch_binance_gainers),
        ("okx-gainers", fetch_okx_gainers),
        ("okx-dex-gainers", fetch_okx_dex_gainers),
        ("bitget-gainers", fetch_bitget_gainers),
        ("futu-hk-gainers", lambda: fetch_futu_gainers("hk")),
        ("futu-us-gainers", lambda: fetch_futu_gainers("us")),
        ("cn-stock-gainers", fetch_cn_stock_gainers),
    ]
    for key, fetcher in fetchers:
        fallback_group = "cn" if key.startswith("cn-") else "hk" if "-hk-" in key else "us" if "-us-" in key else "crypto"
        sources.append(
            cached_or_fallback_source(
                key,
                fetcher,
                api_key="gainers-rankings",
                fallback_group=fallback_group,
                error_title=key,
                error_subtitle="涨幅榜数据源请求失败",
                error_empty_title="涨幅榜请求失败",
            )
        )
    return {"updatedAt": int(time.time() * 1000), "sources": sources}


def turnover_rankings_payload() -> dict[str, Any]:
    sources = []
    fetchers = [
        ("binance", fetch_binance),
        ("okx-turnover", fetch_okx_turnover),
        ("okx-dex", fetch_okx_dex_hot),
        ("bitget", fetch_bitget),
        ("aicoin", fetch_aicoin),
        ("futu-hk-turnover", lambda: fetch_futu_turnover("hk")),
        ("futu-us-turnover", lambda: fetch_futu_turnover("us")),
        ("ths", fetch_ths_hot),
    ]
    for key, fetcher in fetchers:
        fallback_group = "hk" if "futu-hk" in key else "us" if "futu-us" in key else "cn" if key == "ths" else "crypto"
        sources.append(
            cached_or_fallback_source(
                key,
                fetcher,
                api_key="turnover-rankings",
                fallback_group=fallback_group,
                error_title=key,
                error_subtitle="成交额榜数据源请求失败",
                error_empty_title="成交额榜请求失败",
            )
        )
    return {"updatedAt": int(time.time() * 1000), "sources": sources}


def alert_text(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def alert_body_join(*parts: Any, limit: int = 220) -> str:
    result: list[str] = []
    normalized_parts: list[str] = []
    for part in parts:
        text = alert_text(part, 120)
        if not text or text in {"--", "None", "null"}:
            continue
        normalized = re.sub(r"[\s,/·|：:;；，,$%+_-]+", "", text.lower())
        if not normalized:
            continue
        duplicate = False
        for seen in normalized_parts:
            if normalized == seen or (len(normalized) >= 4 and normalized in seen) or (len(seen) >= 4 and seen in normalized):
                duplicate = True
                break
        if duplicate:
            continue
        normalized_parts.append(normalized)
        result.append(text)
    return alert_text(" / ".join(result), limit)


def market_display_name(source: dict[str, Any], row: dict[str, Any], fallback: str = "--") -> str:
    group = str(source.get("group") or row.get("group") or "").lower()
    if group in {"hk", "us", "cn"}:
        return str(row.get("name") or row.get("symbol") or fallback).strip() or fallback
    return str(row.get("symbol") or row.get("asset") or row.get("name") or fallback).strip() or fallback


def alert_event_ms(value: Any) -> int:
    numeric = safe_float(value)
    if numeric > 0:
        return int(numeric * 1000) if numeric < 10_000_000_000 else int(numeric)
    parsed = parse_feed_datetime(value)
    return parsed or int(time.time() * 1000)


def js_stable_part(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())[:180]


def js_stable_key(prefix: str, *parts: Any) -> str:
    body = "|".join(part for part in (js_stable_part(item) for item in parts) if part)
    return f"{prefix}:{body}"


def alert_dedupe_title(value: Any) -> str:
    return re.sub(r"^高热提醒[：:]\s*", "", js_stable_part(value))


def alert_dedupe_keys(item: dict[str, Any]) -> list[str]:
    primary = alert_text(item.get("key"), 260)
    kind = alert_text(item.get("kind"), 40)
    source_label = alert_text(item.get("source") or item.get("sourceLabel"), 80)
    if "公众号授权" in kind or "公众号授权" in source_label or primary.startswith(("wechat-auth-required:", "wechat-auth-qr:")):
        return [primary] if primary else []
    title = alert_dedupe_title(item.get("title"))
    source = item.get("source") or item.get("sourceLabel")
    url = item.get("url")
    body = alert_text(item.get("body"), 100)
    return list(
        dict.fromkeys(
            key
            for key in [
                primary,
                js_stable_key("alert-global-title-url", title, url),
                js_stable_key("alert-global-title", title),
                js_stable_key("alert-title-url", source, title, url),
                js_stable_key("alert-title", source, title),
                js_stable_key("alert-body", source, title, body),
            ]
            if key
            and key
            not in {
                "alert-global-title-url:",
                "alert-global-title:",
                "alert-title-url:",
                "alert-title:",
                "alert-body:",
            }
        )
    )


def expand_legacy_desktop_alert_seen_keys(seen: dict[str, float]) -> dict[str, float]:
    expanded = dict(seen)
    for raw_key, seen_at in seen.items():
        key = str(raw_key or "")
        timestamp = safe_float(seen_at)
        if key.startswith("alert-title-url:"):
            rest = key.removeprefix("alert-title-url:")
            if "|" not in rest:
                continue
            remainder = rest.split("|", 1)[1]
            match = re.search(r"https?://[^\s|]+", remainder)
            if match:
                title = remainder[: match.start()].rstrip("| ")
                url = match.group(0)
            else:
                parts = remainder.rsplit("|", 1)
                title = parts[0]
                url = parts[1] if len(parts) > 1 else ""
            deduped_title = alert_dedupe_title(title)
            if deduped_title:
                expanded.setdefault(js_stable_key("alert-global-title", deduped_title), timestamp)
            if deduped_title and url:
                expanded.setdefault(js_stable_key("alert-global-title-url", deduped_title, url), timestamp)
        elif key.startswith("alert-title:"):
            rest = key.removeprefix("alert-title:")
            if "|" not in rest:
                continue
            deduped_title = alert_dedupe_title(rest.split("|", 1)[1])
            if deduped_title:
                expanded.setdefault(js_stable_key("alert-global-title", deduped_title), timestamp)
    return expanded


def site_amount_from_text(value: Any) -> float:
    text = str(value or "").replace(",", "").strip()
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return 0.0
    amount = safe_float(match.group(0))
    if re.search(r"[亿B]", text, re.I):
        amount *= 100_000_000
    elif "万" in text:
        amount *= 10_000
    elif re.search(r"M", text, re.I):
        amount *= 1_000_000
    elif re.search(r"K", text, re.I):
        amount *= 1_000
    return amount


def site_heat_info(row: dict[str, Any], rank: int = 99) -> dict[str, Any]:
    tags = row.get("tags") if isinstance(row.get("tags"), list) else []
    amount = safe_float(row.get("amount")) or site_amount_from_text(
        row.get("turnover") or row.get("metric") or row.get("note") or row.get("price")
    )
    heat = safe_float(row.get("heat"))
    change = abs(safe_float(str(row.get("change") or "0").replace("%", "").replace("+", "")))
    hint_text = " ".join(str(item or "") for item in [
        row.get("group"),
        row.get("status"),
        row.get("source"),
        row.get("sourceLabel"),
        row.get("metric"),
        row.get("title"),
        row.get("symbol"),
        row.get("note"),
        *tags,
    ]).lower()
    is_ipo = bool(re.search(r"ipo|nasdaq|nyse|上市|招股|申购|upcoming|priced", hint_text, re.I)) or row.get("group") in {"ipo", "cn"}
    is_contract = bool(re.search(r"合约|永续|上线|上新|will list|perpetual|usdt", hint_text, re.I)) or row.get("group") == "crypto"
    score = 0
    if rank <= 3:
        score += 8
    elif rank <= 5:
        score += 4
    if amount >= 1_000_000_000:
        score += 46
    elif amount >= 300_000_000:
        score += 34
    elif amount >= 100_000_000:
        score += 24
    elif amount >= 30_000_000:
        score += 14
    elif amount >= 5_000_000:
        score += 6
    if heat >= 90:
        score += 30
    elif heat >= 80:
        score += 20
    elif heat >= 70:
        score += 10
    if change >= 40:
        score += 18
    elif change >= 25:
        score += 10
    elif change >= 15:
        score += 5
    if re.search(r"高热|热门|热度|讨论|关注|超购|融资|首日|ipo|新币|合约", hint_text, re.I):
        score += 6
    if is_contract:
        if re.search(r"binance|币安|okx|欧易|bitget", hint_text, re.I):
            score += 10
        if re.search(r"合约|永续|perpetual|will list|上线|上新|usdt", hint_text, re.I):
            score += 12
        if re.search(r"ai|人工智能|meme|memecoin|defi|rwa|gamefi|launchpool|空投|热门", hint_text, re.I):
            score += 12
    if is_ipo:
        if amount >= 1_000_000_000:
            score += 40
        elif amount >= 300_000_000:
            score += 32
        elif amount >= 100_000_000:
            score += 22
        elif amount >= 50_000_000:
            score += 14
        elif amount >= 20_000_000:
            score += 8
        if re.search(r"ai|人工智能|智能|芯片|半导体|robot|机器人|digital|infrastructure|crypto|bitcoin|区块链|web3|cerebras|blackstone", hint_text, re.I):
            score += 24
        if re.search(r"global|select|nasdaq|nyse|首发|申购|招股|upcoming|priced", hint_text, re.I):
            score += 4
    threshold = 104 if is_ipo else 60 if is_contract else 76
    return {"high": score >= threshold, "score": min(100, round(score)), "amount": amount}


def normalize_desktop_alert(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": alert_text(payload.get("key"), 220),
        "kind": alert_text(payload.get("kind") or "市场信息", 24),
        "source": alert_text(payload.get("source") or "星云社", 42),
        "sourceLabel": alert_text(payload.get("sourceLabel") or "NX", 8),
        "title": alert_text(payload.get("title") or "市场信息", 92),
        "body": alert_text(payload.get("body"), 180),
        "url": alert_text(payload.get("url"), 600),
        "imageUrl": alert_text(payload.get("imageUrl"), 600),
        "imagePath": alert_text(payload.get("imagePath"), 600),
        "time": payload.get("time") or int(time.time() * 1000),
        "priority": alert_text(payload.get("priority") or "实时", 18),
        "clientMode": alert_text(payload.get("clientMode") or "web", 18),
        "sound": payload.get("sound") is not False,
    }


def desktop_alert_bridge_url() -> str:
    return env_value("XINGYUN_DESKTOP_ALERT_BRIDGE_URL", "").strip()


def forward_desktop_alert_to_bridge(normalized: dict[str, Any]) -> None:
    bridge_url = desktop_alert_bridge_url()
    if not bridge_url:
        raise RuntimeError("desktop alert bridge url is not configured")
    data = json.dumps(normalized, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        bridge_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "XingyunSociety/desktop-alert"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        if response.status >= 400:
            raise RuntimeError(f"desktop alert bridge returned HTTP {response.status}")


def spawn_desktop_alert_process(normalized: dict[str, Any], slot: int) -> None:
    if os.name != "nt":
        forward_desktop_alert_to_bridge(normalized)
        return

    encoded = base64.b64encode(json.dumps(normalized, ensure_ascii=False).encode("utf-8")).decode("ascii")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log_file = DESKTOP_ALERT_LOG_PATH.open("a", encoding="utf-8")
    try:
        subprocess.Popen(
            [sys.executable, str(ROOT / "desktop_alert.py"), "--payload", encoded, "--slot", str(slot)],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            creationflags=creationflags,
            close_fds=True,
        )
    finally:
        log_file.close()


def desktop_alert_queue_worker() -> None:
    global DESKTOP_ALERT_QUEUE_ACTIVE, DESKTOP_ALERT_LAST_LAUNCHED_AT
    while True:
        with DESKTOP_ALERT_LOCK:
            if not DESKTOP_ALERT_QUEUE:
                DESKTOP_ALERT_QUEUE_ACTIVE = False
                return
            normalized = DESKTOP_ALERT_QUEUE.popleft()
            now = time.time()
            wait_seconds = max(0.0, DESKTOP_ALERT_MIN_INTERVAL_SECONDS - (now - DESKTOP_ALERT_LAST_LAUNCHED_AT))
        if wait_seconds:
            time.sleep(wait_seconds)
        try:
            spawn_desktop_alert_process(normalized, 0)
        except Exception as exc:
            print(f"Desktop alert spawn failed: {exc}", file=sys.stderr)
        with DESKTOP_ALERT_LOCK:
            DESKTOP_ALERT_LAST_LAUNCHED_AT = time.time()


def desktop_alert_marker_id(keys: list[str]) -> str:
    material = "\n".join(sorted(str(key) for key in keys if key))
    if not material:
        return ""
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]


def cleanup_desktop_alert_markers(now: float) -> None:
    try:
        for marker_path in DESKTOP_ALERT_MARKER_DIR.glob("*.lock"):
            try:
                if now - marker_path.stat().st_mtime > DESKTOP_ALERT_MARKER_TTL_SECONDS:
                    marker_path.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        return


def claim_desktop_alert_marker(keys: list[str], now: float) -> bool:
    marker = desktop_alert_marker_id(keys)
    if not marker:
        return True
    marker_path = DESKTOP_ALERT_MARKER_DIR / f"{marker}.lock"
    payload = json.dumps({"createdAt": now, "keys": keys[:4]}, ensure_ascii=False)
    try:
        fd = os.open(str(marker_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if now - marker_path.stat().st_mtime <= DESKTOP_ALERT_MARKER_TTL_SECONDS:
                return False
            marker_path.unlink(missing_ok=True)
            fd = os.open(str(marker_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, OSError):
            return False
    except OSError:
        return True

    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
    return True


def launch_desktop_alert(payload: dict[str, Any]) -> dict[str, Any]:
    global DESKTOP_ALERT_SEEN_LOADED, DESKTOP_ALERT_QUEUE_ACTIVE
    if env_flag("XINGYUN_DISABLE_DESKTOP_ALERT", default=is_production_mode()):
        return {"ok": True, "skipped": True, "reason": "desktop alerts disabled"}
    if os.name != "nt" and not desktop_alert_bridge_url():
        return {"ok": False, "error": "desktop alerts are only enabled on Windows"}

    normalized = normalize_desktop_alert(payload)
    keys = alert_dedupe_keys(normalized)
    now = time.time()
    should_start_worker = False
    with DESKTOP_ALERT_LOCK:
        if not DESKTOP_ALERT_SEEN_LOADED:
            cached_seen = read_json_cache(DESKTOP_ALERT_STATE_PATH).get("seen")
            if isinstance(cached_seen, dict):
                loaded_seen = {str(key): safe_float(value) for key, value in cached_seen.items()}
                DESKTOP_ALERT_SEEN.update(expand_legacy_desktop_alert_seen_keys(loaded_seen))
            DESKTOP_ALERT_SEEN_LOADED = True
        stale = [item_key for item_key, seen_at in DESKTOP_ALERT_SEEN.items() if now - seen_at > DESKTOP_ALERT_TTL]
        for item_key in stale:
            DESKTOP_ALERT_SEEN.pop(item_key, None)
        if any(key in DESKTOP_ALERT_SEEN for key in keys):
            return {"ok": True, "deduped": True}
        cleanup_desktop_alert_markers(now)
        if not claim_desktop_alert_marker(keys, now):
            return {"ok": True, "deduped": True, "marker": True}
        queued_keys = {key for queued_item in DESKTOP_ALERT_QUEUE for key in alert_dedupe_keys(queued_item)}
        if any(key in queued_keys for key in keys):
            return {"ok": True, "deduped": True, "queuedDuplicate": True}
        for key in keys:
            DESKTOP_ALERT_SEEN[key] = now
        recent_seen = dict(sorted(DESKTOP_ALERT_SEEN.items(), key=lambda item: item[1])[-DESKTOP_ALERT_SEEN_LIMIT:])
        DESKTOP_ALERT_SEEN.clear()
        DESKTOP_ALERT_SEEN.update(recent_seen)
        write_json_cache(DESKTOP_ALERT_STATE_PATH, {"seen": recent_seen})
        while len(DESKTOP_ALERT_QUEUE) >= DESKTOP_ALERT_QUEUE_LIMIT:
            DESKTOP_ALERT_QUEUE.popleft()
        DESKTOP_ALERT_QUEUE.append(normalized)
        if not DESKTOP_ALERT_QUEUE_ACTIVE:
            DESKTOP_ALERT_QUEUE_ACTIVE = True
            should_start_worker = True

    if should_start_worker:
        threading.Thread(target=desktop_alert_queue_worker, daemon=True).start()
    return {"ok": True, "deduped": False, "queued": True, "queueSize": len(DESKTOP_ALERT_QUEUE)}


def load_site_alert_state() -> dict[str, Any]:
    global SITE_ALERT_STATE_LOADED, SITE_ALERT_STATE
    with SITE_ALERT_LOCK:
        if SITE_ALERT_STATE_LOADED:
            return SITE_ALERT_STATE
        payload = read_json_cache(SITE_ALERT_STATE_PATH)
        seen = payload.get("seen") if isinstance(payload.get("seen"), dict) else {}
        ready = payload.get("ready") if isinstance(payload.get("ready"), list) else []
        SITE_ALERT_STATE = {"seen": seen, "ready": ready}
        SITE_ALERT_STATE_LOADED = True
        return SITE_ALERT_STATE


def save_site_alert_state() -> None:
    with SITE_ALERT_LOCK:
        seen = SITE_ALERT_STATE.get("seen") if isinstance(SITE_ALERT_STATE.get("seen"), dict) else {}
        recent_seen = dict(sorted(seen.items(), key=lambda item: safe_float(item[1]))[-SITE_ALERT_SEEN_LIMIT:])
        SITE_ALERT_STATE["seen"] = recent_seen
        write_json_cache(
            SITE_ALERT_STATE_PATH,
            {"seen": recent_seen, "ready": list(dict.fromkeys(SITE_ALERT_STATE.get("ready") or []))},
        )


def site_event_is_fresh(event: dict[str, Any], max_age_ms: int) -> bool:
    if event.get("allowStale"):
        return True
    timestamp = alert_event_ms(event.get("time"))
    now_ms = int(time.time() * 1000)
    if timestamp - now_ms > SITE_ALERT_FUTURE_TOLERANCE_MS:
        return False
    return now_ms - timestamp <= max_age_ms


def site_event_seen(event: dict[str, Any], seen: dict[str, Any]) -> bool:
    return any(key in seen for key in alert_dedupe_keys(event))


def parse_site_listing_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
    events: list[dict[str, Any]] = []
    for section in sections:
        rows = section.get("rows") if isinstance(section.get("rows"), list) else []
        for index, row in enumerate(rows):
            heat = site_heat_info(row, int(safe_float(row.get("rank"), index + 1) or index + 1))
            base_kind = "IPO / 上市" if row.get("group") == "ipo" else "A股上市" if row.get("group") == "cn" else "港美股上市" if row.get("group") in {"hk", "us"} else "交易所上新"
            title = row.get("title") or row.get("symbol") or "新的上新事件"
            body_parts = [
                row.get("symbol"),
                row.get("metric"),
                row.get("price"),
                row.get("note"),
                f"综合热度 {heat['score']}" if heat.get("high") else "",
            ]
            events.append(
                {
                    "key": js_stable_key("listing", row.get("id"), row.get("source"), row.get("title"), row.get("symbol"), row.get("date"), row.get("url")),
                    "kind": f"{base_kind}高热" if heat.get("high") else base_kind,
                    "source": row.get("source") or section.get("sourceName") or "Listing",
                    "sourceLabel": row.get("sourceLabel") or "NEW",
                    "title": f"高热提醒：{title}" if heat.get("high") else title,
                    "body": alert_body_join(*body_parts),
                    "url": row.get("url") or "./listings.html",
                    "time": row.get("date") or payload.get("updatedAt") or int(time.time() * 1000),
                    "priority": "高热重点" if heat.get("high") else "交易所上新" if row.get("group") == "crypto" else "上市信息",
                }
            )
    return events


def parse_site_newboard_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
    events: list[dict[str, Any]] = []
    for section in sections:
        rows = section.get("rows") if isinstance(section.get("rows"), list) else []
        for index, row in enumerate(rows):
            rank = int(safe_float(row.get("rank"), index + 1) or index + 1)
            heat = site_heat_info(row, rank)
            symbol = row.get("symbol") or row.get("asset") or row.get("title") or row.get("name") or "新标的"
            body_parts = [
                f"价格 {row.get('price')}" if row.get("price") else "",
                f"涨跌 {row.get('change')}" if row.get("change") else "",
                row.get("turnover") or row.get("metric"),
                f"综合热度 {heat['score']}" if heat.get("high") else "",
            ]
            events.append(
                {
                    "key": js_stable_key("newboard", section.get("id"), row.get("id"), symbol, row.get("date"), row.get("url")),
                    "kind": "新币新股高热" if heat.get("high") else "新币新股",
                    "source": row.get("source") or section.get("sourceName") or section.get("title") or "新币新股榜",
                    "sourceLabel": row.get("sourceLabel") or section.get("sourceLabel") or "NEW",
                    "title": f"高热提醒：{section.get('title') or '新币新股'} {symbol}" if heat.get("high") else f"{section.get('title') or '新币新股'} {symbol}",
                    "body": alert_body_join(*body_parts),
                    "url": row.get("url") or "./newboards.html",
                    "time": row.get("date") or payload.get("updatedAt") or int(time.time() * 1000),
                    "priority": "高热重点" if heat.get("high") else "新币新股",
                }
            )
    return events


def parse_site_newsflash_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    return [
        {
            "key": js_stable_key("flash", item.get("id"), item.get("title"), item.get("add_time")),
            "kind": "律动快讯",
            "source": "BlockBeats",
            "sourceLabel": "BB",
            "title": item.get("title") or "市场快讯",
            "body": item.get("content") or "",
            "url": item.get("url") or "https://www.theblockbeats.info/newsflash",
            "time": item.get("add_time") or int(time.time() * 1000),
            "priority": "市场信息",
        }
        for item in items
    ]


def parse_site_market_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    events: list[dict[str, Any]] = []
    for source in sources:
        rows = source.get("rows") if isinstance(source.get("rows"), list) else []
        if source.get("status") == "unavailable" or not rows:
            continue
        leader = rows[0]
        change = abs(safe_float(str(leader.get("change") or "0").replace("%", "").replace("+", "")))
        symbol = leader.get("symbol") or leader.get("name") or "--"
        display = market_display_name(source, leader, symbol)
        events.append(
            {
                "key": js_stable_key("market-leader", source.get("id"), symbol, leader.get("rank")),
                "kind": "榜首换手",
                "source": source.get("title") or source.get("sourceName") or "Market",
                "sourceLabel": source.get("sourceLabel") or "MR",
                "title": f"{source.get('sourceLabel') or '榜单'} 榜首变为 {display}",
                "body": alert_body_join(leader.get("name"), leader.get("price"), leader.get("change"), leader.get("turnover") or leader.get("note")),
                "url": "./index.html",
                "time": source.get("updatedAt") or payload.get("updatedAt") or int(time.time() * 1000),
                "priority": "资金切换" if change >= 8 else "榜首换手",
            }
        )
    return events


def parse_site_gainers_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    events: list[dict[str, Any]] = []
    for source in sources:
        rows = source.get("rows") if isinstance(source.get("rows"), list) else []
        if source.get("status") == "unavailable" or not rows:
            continue
        leader = rows[0]
        symbol = leader.get("symbol") or leader.get("name") or "--"
        display = market_display_name(source, leader, symbol)
        change_text = leader.get("change") or leader.get("changeText") or ""
        change = abs(safe_float(str(change_text).replace("%", "").replace("+", "")))
        parts = [
            leader.get("name"),
            leader.get("price"),
            change_text,
            leader.get("turnover") or leader.get("note"),
        ]
        events.append(
            {
                "key": js_stable_key("gainers-leader", source.get("id"), symbol),
                "kind": "涨幅榜异动",
                "source": source.get("title") or source.get("sourceName") or "涨幅榜",
                "sourceLabel": source.get("sourceLabel") or "UP",
                "title": f"{source.get('sourceLabel') or '涨幅榜'} 涨幅榜榜首变为 {display}",
                "body": alert_body_join(*parts),
                "url": "./gainers.html",
                "time": source.get("updatedAt") or payload.get("updatedAt") or int(time.time() * 1000),
                "priority": "催化重定价" if change >= 15 else "榜首换手",
            }
        )
    return events


def rank_monitor_asset_key(source: dict[str, Any], row: dict[str, Any]) -> str:
    group = str(source.get("group") or row.get("group") or "").lower()
    raw_symbol = str(row.get("symbol") or row.get("asset") or row.get("name") or "").strip()
    if group == "hk":
        code = re.sub(r"\D", "", raw_symbol).zfill(5)[-5:]
        return f"HK:{code}" if code and code != "00000" else ""
    if group == "cn":
        code = re.sub(r"\D", "", raw_symbol)[-6:]
        return f"CN:{code}" if code else ""
    if group == "us":
        symbol = re.sub(r"[^A-Z0-9.]", "", raw_symbol.upper())
        return f"US:{symbol}" if symbol else ""
    asset = normalize_asset_symbol(re.sub(r"(USDT|USDC|USD)$", "", raw_symbol.upper()))
    if not asset and row.get("name"):
        asset = normalize_asset_symbol(str(row.get("name")).split("-")[0].split("/")[0])
    if not asset or is_excluded_crypto_asset(asset):
        return ""
    return f"CRYPTO:{asset}"


def rank_monitor_amount(row: dict[str, Any]) -> float:
    return safe_float(row.get("amount")) or site_amount_from_text(row.get("turnover") or row.get("metricLabel") or row.get("note"))


def rank_monitor_change(row: dict[str, Any]) -> float:
    return safe_float(str(row.get("change") or "0").replace("%", "").replace("+", "").replace(",", ""))


def rank_monitor_rank(row: dict[str, Any], fallback: int = 99) -> int:
    value = int(safe_float(row.get("rank"), fallback) or fallback)
    return value if value > 0 else fallback


def rank_monitor_display(source: dict[str, Any], row: dict[str, Any], asset_key: str) -> str:
    label = market_display_name(source, row, asset_key.split(":", 1)[-1])
    return label or asset_key


def rank_monitor_snapshot(source: dict[str, Any], row: dict[str, Any], index: int, board: str) -> dict[str, Any]:
    asset_key = rank_monitor_asset_key(source, row)
    source_id = str(source.get("id") or source.get("sourceLabel") or source.get("title") or board)
    rank = rank_monitor_rank(row, index + 1)
    amount = rank_monitor_amount(row)
    change = rank_monitor_change(row)
    heat = safe_float(row.get("heat"))
    return {
        "key": f"{source_id}:{asset_key}",
        "assetKey": asset_key,
        "sourceId": source_id,
        "sourceTitle": source.get("title") or source.get("sourceName") or source_id,
        "sourceLabel": source.get("sourceLabel") or "",
        "group": source.get("group") or "",
        "symbol": rank_monitor_display(source, row, asset_key),
        "name": row.get("name") or "",
        "rank": rank,
        "price": row.get("price") or "",
        "change": change,
        "amount": amount,
        "heat": heat,
        "turnover": row.get("turnover") or "",
        "note": row.get("note") or "",
        "url": row.get("url") or ("./turnover.html" if board == "turnover" else "./index.html"),
    }


def rank_monitor_market_rows(payload: dict[str, Any], board: str = "hot") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in payload.get("sources") if isinstance(payload.get("sources"), list) else []:
        if source.get("status") == "unavailable":
            continue
        for index, row in enumerate(source.get("rows") if isinstance(source.get("rows"), list) else []):
            if not isinstance(row, dict):
                continue
            snapshot = rank_monitor_snapshot(source, row, index, board)
            if snapshot.get("assetKey"):
                rows.append(snapshot)
    return rows


def rank_monitor_gainers_assets(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    for source in payload.get("sources") if isinstance(payload.get("sources"), list) else []:
        if source.get("status") == "unavailable":
            continue
        if str(source.get("id") or "") in RANK_MONITOR_SKIP_UPDATE_SOURCES:
            continue
        for index, row in enumerate(source.get("rows") if isinstance(source.get("rows"), list) else []):
            if not isinstance(row, dict):
                continue
            snapshot = rank_monitor_snapshot(source, row, index, "gainers")
            asset_key = snapshot.get("assetKey")
            if asset_key and asset_key not in assets:
                assets[asset_key] = snapshot
    return assets


def rank_monitor_watch_rows(board: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    watched = [
        row
        for row in rows
        if str(row.get("sourceId") or "") not in RANK_MONITOR_SKIP_UPDATE_SOURCES
    ]
    if board == "hot":
        result: list[dict[str, Any]] = []
        for row in watched:
            group = str(row.get("group") or "").lower()
            limit = RANK_MONITOR_STOCK_HOT_WATCH_RANK if group in {"hk", "us"} else RANK_MONITOR_HOT_WATCH_RANK
            if rank_monitor_rank(row) <= limit:
                result.append(row)
        return result
    if board == "turnover":
        return watched[:RANK_MONITOR_TURNOVER_WATCH_LIMIT]
    return watched


def rank_monitor_changed(previous: dict[str, Any] | None, current: dict[str, Any], board: str) -> tuple[bool, str]:
    if not previous:
        return True, "new"
    return False, ""


def rank_monitor_board_label(board: str, current: dict[str, Any]) -> str:
    if board == "turnover":
        return "成交额榜"
    group = str(current.get("group") or "").lower()
    if group == "hk":
        return "港股热门榜"
    if group == "us":
        return "美股热门榜"
    if group == "cn":
        return "A股热门榜"
    return "热门榜"


def rank_monitor_metric_text(board: str, current: dict[str, Any]) -> str:
    group = str(current.get("group") or "").lower()
    amount = safe_float(current.get("amount"))
    if board == "hot" and group in {"hk", "us", "cn"}:
        return str(current.get("turnover") or current.get("note") or "").strip()
    return money_usd(amount) if amount else str(current.get("turnover") or current.get("note") or "").strip()


def rank_monitor_event(board: str, current: dict[str, Any], reason: str) -> dict[str, Any]:
    board_label = rank_monitor_board_label(board, current)
    reason_label = {
        "new": "新进",
        "rank": "排名更新",
        "amount": "成交额更新",
        "change": "涨跌更新",
        "heat": "热度更新",
    }.get(reason, "更新")
    symbol = current.get("symbol") or current.get("assetKey") or "标的"
    amount = safe_float(current.get("amount"))
    body_parts = [
        current.get("sourceTitle"),
        f"排名 #{current.get('rank')}" if current.get("rank") else "",
        current.get("price"),
        pct(current.get("change")) if current.get("change") else "",
        rank_monitor_metric_text(board, current),
    ]
    signal = js_stable_key(
        "rank-monitor",
        board,
        current.get("key"),
        reason,
        current.get("rank"),
        round(safe_float(current.get("amount")) / 1_000_000),
        round(safe_float(current.get("change")), 2),
        round(safe_float(current.get("heat"))),
    )
    return {
        "key": signal,
        "kind": f"{board_label}{reason_label}",
        "source": current.get("sourceTitle") or board_label,
        "sourceLabel": current.get("sourceLabel") or ("AMT" if board == "turnover" else "HOT"),
        "title": f"{board_label}{reason_label}：{symbol}",
        "body": alert_body_join(*body_parts),
        "url": "./turnover.html" if board == "turnover" else "./index.html",
        "time": int(time.time() * 1000),
        "priority": reason_label,
        "_assetKey": current.get("assetKey"),
    }


def rank_monitor_cross_events(
    hot_rows: list[dict[str, Any]],
    turnover_rows: list[dict[str, Any]],
    gainers_assets: dict[str, dict[str, Any]],
    cross_seen: dict[str, Any],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if limit is not None and limit <= 0:
        return []
    hot_assets = {row["assetKey"]: row for row in hot_rows if row.get("assetKey")}
    turnover_assets = {row["assetKey"]: row for row in turnover_rows if row.get("assetKey")}
    events: list[dict[str, Any]] = []
    for asset_key in sorted(set(hot_assets) | set(turnover_assets) | set(gainers_assets)):
        boards = []
        examples = []
        if asset_key in hot_assets:
            boards.append("热门榜")
            examples.append(hot_assets[asset_key])
        if asset_key in gainers_assets:
            boards.append("涨幅榜")
            examples.append(gainers_assets[asset_key])
        if asset_key in turnover_assets:
            boards.append("成交额榜")
            examples.append(turnover_assets[asset_key])
        if len(boards) < 2 or cross_seen.get(asset_key):
            continue
        symbol = next((item.get("symbol") for item in examples if item.get("symbol")), asset_key.split(":", 1)[-1])
        body_parts = [
            "、".join(boards),
            next((item.get("sourceTitle") for item in examples if item.get("sourceTitle")), ""),
            next((item.get("price") for item in examples if item.get("price")), ""),
            next((item.get("turnover") for item in examples if item.get("turnover")), ""),
            next((pct(item.get("change")) for item in examples if item.get("change")), ""),
        ]
        events.append(
            {
                "key": js_stable_key("rank-cross", asset_key),
                "kind": "多榜共振",
                "source": "热门 / 涨幅 / 成交额",
                "sourceLabel": "HOT",
                "title": f"多榜共振：{symbol} 同时出现在{'、'.join(boards)}",
                "body": alert_body_join(*body_parts),
                "url": "./index.html",
                "time": int(time.time() * 1000),
                "priority": "多榜共振",
                "_assetKey": asset_key,
            }
        )
        cross_seen[asset_key] = time.time()
        if limit is not None and len(events) >= limit:
            break
    return events


def sync_rank_monitor_feed() -> None:
    try:
        market = market_payload()
        turnover = turnover_rankings_payload()
        gainers = gainers_rankings_payload()
    except Exception as exc:
        print(f"Rank monitor failed: {exc}", file=sys.stderr)
        return

    hot_rows = rank_monitor_market_rows(market)
    turnover_all_rows = sorted(
        rank_monitor_market_rows(turnover, "turnover"),
        key=lambda item: safe_float(item.get("amount")),
        reverse=True,
    )
    turnover_rows = turnover_all_rows[:40]
    gainers_assets = rank_monitor_gainers_assets(gainers)
    now = time.time()

    with RANK_MONITOR_LOCK:
        state = read_json_cache(RANK_MONITOR_STATE_PATH)
        ready = bool(state.get("ready")) and int(safe_float(state.get("version"))) == RANK_MONITOR_STATE_VERSION
        snapshots = state.get("snapshots") if isinstance(state.get("snapshots"), dict) else {}
        last_alerts = state.get("lastAlerts") if isinstance(state.get("lastAlerts"), dict) else {}
        cross_seen = state.get("crossSeen") if isinstance(state.get("crossSeen"), dict) else {}
        known_rows = state.get("knownRows") if isinstance(state.get("knownRows"), dict) else {}
        previous_known = {
            "hot": set(known_rows.get("hot") or []),
            "turnover": set(known_rows.get("turnover") or []),
        }
        current_known = {
            "hot": sorted({str(row.get("key") or "") for row in hot_rows if row.get("key")}),
            "turnover": sorted({str(row.get("key") or "") for row in turnover_all_rows if row.get("key")}),
        }
        events: list[dict[str, Any]] = []
        emitted_assets: set[str] = set()

        next_snapshots: dict[str, dict[str, Any]] = {"hot": {}, "turnover": {}}
        if ready:
            remaining = max(0, RANK_MONITOR_MAX_EVENTS_PER_RUN - len(events))
            cross_events = rank_monitor_cross_events(
                rank_monitor_watch_rows("hot", hot_rows),
                rank_monitor_watch_rows("turnover", turnover_rows),
                gainers_assets,
                cross_seen,
                limit=remaining,
            )
            events.extend(cross_events)
            emitted_assets.update(str(event.get("_assetKey") or "") for event in cross_events if event.get("_assetKey"))

        for board, rows in (("hot", hot_rows), ("turnover", turnover_rows)):
            for row in rank_monitor_watch_rows(board, rows):
                key = row.get("key")
                if not key:
                    continue
                next_snapshots[board][key] = row
                if len(events) >= RANK_MONITOR_MAX_EVENTS_PER_RUN:
                    break
                if not ready or key in previous_known.get(board, set()):
                    continue
                asset_key = str(row.get("assetKey") or "")
                if asset_key and asset_key in emitted_assets:
                    continue
                changed, reason = rank_monitor_changed(snapshots.get(board, {}).get(key) if isinstance(snapshots.get(board), dict) else None, row, board)
                if not changed or reason != "new":
                    continue
                alert_key = f"{board}:new:{key}"
                if now - safe_float(last_alerts.get(alert_key)) < RANK_MONITOR_COOLDOWN_SECONDS:
                    continue
                event = rank_monitor_event(board, row, "new")
                events.append(event)
                if asset_key:
                    emitted_assets.add(asset_key)
                last_alerts[alert_key] = now

        if not ready:
            hot_assets = {row["assetKey"] for row in rank_monitor_watch_rows("hot", hot_rows) if row.get("assetKey")}
            turnover_assets = {row["assetKey"] for row in rank_monitor_watch_rows("turnover", turnover_rows) if row.get("assetKey")}
            gainers_keys = set(gainers_assets)
            for asset_key in hot_assets | turnover_assets | gainers_keys:
                hits = int(asset_key in hot_assets) + int(asset_key in turnover_assets) + int(asset_key in gainers_keys)
                if hits >= 2:
                    cross_seen[asset_key] = now

        last_alerts = dict(sorted(last_alerts.items(), key=lambda item: safe_float(item[1]))[-5000:])
        cross_seen = dict(sorted(cross_seen.items(), key=lambda item: safe_float(item[1]))[-5000:])
        write_json_cache(
            RANK_MONITOR_STATE_PATH,
            {
                "version": RANK_MONITOR_STATE_VERSION,
                "ready": True,
                "updatedAt": int(now * 1000),
                "snapshots": next_snapshots,
                "knownRows": current_known,
                "lastAlerts": last_alerts,
                "crossSeen": cross_seen,
            },
        )

    for event in events:
        try:
            launch_desktop_alert(event)
        except Exception as exc:
            print(f"Rank monitor popup failed: {exc}", file=sys.stderr)


def parse_site_brief_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    briefs = payload.get("briefs") if isinstance(payload.get("briefs"), list) else []
    return [
        {
            "key": js_stable_key("brief", brief.get("id"), brief.get("completedAt"), brief.get("name")),
            "kind": "自动简报",
            "source": brief.get("name") or "自动化简报",
            "sourceLabel": "AI",
            "title": brief.get("name") or "新的自动化简报",
            "body": alert_text(brief.get("content"), 180),
            "url": "./briefs.html",
            "time": brief.get("completedAt") or int(time.time() * 1000),
            "priority": "简报",
        }
        for brief in briefs
        if brief.get("completedAt")
    ]


def parse_site_x_kol_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    source_created_at = {
        source.get("id"): int(safe_float(source.get("createdAt")))
        for source in sources
        if isinstance(source, dict) and source.get("id")
    }
    events: list[dict[str, Any]] = []
    for item in items:
        published_at = int(safe_float(item.get("publishedAt")))
        created_at = source_created_at.get(item.get("sourceId")) or 0
        if published_at and created_at and published_at + 60_000 < created_at:
            continue
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        metric_parts = []
        for label, field in (("赞", "like"), ("转", "repost"), ("评", "reply"), ("引", "quote")):
            value = int(safe_float(metrics.get(field)))
            if value:
                metric_parts.append(f"{label} {value}")
        handle = item.get("handle") or item.get("sourceName") or "KOL"
        display_name = clean_feed_text(item.get("sourceName") or handle, 80)
        title = clean_feed_text(item.get("text") or item.get("title") or "新的 X 动态", 86)
        events.append(
            {
                "key": js_stable_key("x-kol", item.get("id"), item.get("url"), item.get("publishedAt")),
                "kind": "X KOL动态",
                "source": display_name,
                "sourceLabel": "X",
                "title": f"{display_name}：{title}",
                "body": alert_body_join(item.get("text"), " / ".join(metric_parts)),
                "url": x_kol_normalize_url(item.get("url"), handle) or "./xwatch.html",
                "time": published_at or payload.get("updatedAt") or int(time.time() * 1000),
                "priority": "KOL更新",
            }
        )
    return events


def site_alert_feeds() -> list[dict[str, Any]]:
    return [
        {"name": "newsflash", "interval": 12, "maxAgeMs": 6 * 60 * 60 * 1000, "fetch": fetch_blockbeats_flash, "parse": parse_site_newsflash_events},
        {"name": "listings", "interval": 18, "maxAgeMs": 24 * 60 * 60 * 1000, "fetch": listing_events_payload, "parse": parse_site_listing_events},
        {"name": "newboards", "interval": 18, "maxAgeMs": 48 * 60 * 60 * 1000, "fetch": new_coin_rankings_payload, "parse": parse_site_newboard_events},
        {"name": "market", "interval": 30, "maxAgeMs": 15 * 60 * 1000, "fetch": market_payload, "parse": parse_site_market_events},
        {"name": "gainers", "interval": 30, "maxAgeMs": 15 * 60 * 1000, "fetch": gainers_rankings_payload, "parse": parse_site_gainers_events},
        {"name": "briefs", "interval": 60, "maxAgeMs": 24 * 60 * 60 * 1000, "fetch": automation_briefs_payload, "parse": parse_site_brief_events},
        {"name": "x-kol", "interval": 45, "maxAgeMs": 48 * 60 * 60 * 1000, "fetch": x_kol_feed_payload, "parse": parse_site_x_kol_events},
    ]


def sync_site_alert_feed(feed: dict[str, Any]) -> None:
    state = load_site_alert_state()
    try:
        payload = feed["fetch"]()
        events = [event for event in feed["parse"](payload) if event.get("key") and event.get("title")]
    except Exception as exc:
        print(f"Site alert feed failed: {feed.get('name')} {exc}", file=sys.stderr)
        return

    now = time.time()
    ready = set(state.get("ready") or [])
    seen = state.get("seen") if isinstance(state.get("seen"), dict) else {}
    is_ready = feed["name"] in ready
    fresh = [
        event
        for event in events
        if not site_event_seen(event, seen) and site_event_is_fresh(event, int(feed.get("maxAgeMs") or 0))
    ]
    for event in events:
        for key in alert_dedupe_keys(event):
            seen[key] = now
    ready.add(feed["name"])
    state["seen"] = seen
    state["ready"] = list(ready)
    save_site_alert_state()

    if not is_ready:
        return
    for event in fresh:
        try:
            launch_desktop_alert(event)
        except Exception as exc:
            print(f"Site alert popup failed: {exc}", file=sys.stderr)


def site_alert_monitor_loop() -> None:
    feeds = site_alert_feeds()
    next_run = {feed["name"]: 0.0 for feed in feeds}
    rank_monitor_next_run = 0.0
    while True:
        now = time.time()
        for feed in feeds:
            if now < next_run.get(feed["name"], 0):
                continue
            sync_site_alert_feed(feed)
            next_run[feed["name"]] = now + float(feed.get("interval") or 30)
        if now >= rank_monitor_next_run:
            sync_rank_monitor_feed()
            rank_monitor_next_run = now + RANK_MONITOR_INTERVAL
        time.sleep(1)


def start_site_alert_monitor() -> None:
    global SITE_ALERT_MONITOR_ACTIVE, SITE_ALERT_MONITOR_STARTED_AT
    if os.getenv("XINGYUN_DISABLE_SITE_ALERT_MONITOR") == "1":
        SITE_ALERT_MONITOR_ACTIVE = False
        SITE_ALERT_MONITOR_STARTED_AT = 0
        return
    SITE_ALERT_MONITOR_ACTIVE = True
    SITE_ALERT_MONITOR_STARTED_AT = int(time.time() * 1000)
    threading.Thread(target=site_alert_monitor_loop, daemon=True).start()


def wechat_auth_schedule_hours() -> tuple[int, ...]:
    raw = env_value("WECHAT_AUTH_CHECK_HOURS", "")
    hours: list[int] = []
    for item in re.split(r"[,，\s]+", raw):
        if not item:
            continue
        try:
            hour = int(item)
        except ValueError:
            continue
        if 0 <= hour <= 23 and hour not in hours:
            hours.append(hour)
    return tuple(sorted(hours)) or WECHAT_AUTH_SCHEDULE_HOURS


def next_wechat_auth_check_time(now: datetime | None = None) -> datetime:
    current = now or datetime.now()
    for hour in wechat_auth_schedule_hours():
        candidate = current.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate > current:
            return candidate
    tomorrow = current + timedelta(days=1)
    first_hour = wechat_auth_schedule_hours()[0]
    return tomorrow.replace(hour=first_hour, minute=0, second=0, microsecond=0)


def scheduled_wechat_auth_check(run_at: datetime | None = None) -> dict[str, Any]:
    checked_at = run_at or datetime.now()
    slot_label = checked_at.strftime("%m/%d %H:%M")
    status = wechat_account_status_payload(force_validate=True)
    if status.get("authorized"):
        return {**status, "alerted": False, "slot": slot_label}
    reason = "定时检查发现微信公众号授权缺失或已失效"
    try:
        alert_result = notify_wechat_auth_required(reason, force=True, slot_label=slot_label)
    except Exception as exc:
        return {**status, "alerted": False, "slot": slot_label, "alertError": str(exc)[:180]}
    return {**status, "alerted": True, "slot": slot_label, "alert": alert_result}


def wechat_auth_monitor_loop() -> None:
    next_run = next_wechat_auth_check_time()
    while True:
        now_dt = datetime.now()
        sleep_seconds = max(1.0, min(300.0, (next_run - now_dt).total_seconds()))
        time.sleep(sleep_seconds)
        if datetime.now() < next_run:
            continue
        try:
            scheduled_wechat_auth_check(next_run)
        except Exception as exc:
            print(f"WeChat auth scheduled check failed: {exc}", file=sys.stderr)
        finally:
            next_run = next_wechat_auth_check_time(datetime.now())


def start_wechat_auth_monitor() -> None:
    global WECHAT_AUTH_MONITOR_ACTIVE
    if os.getenv("XINGYUN_DISABLE_WECHAT_AUTH_MONITOR") == "1":
        WECHAT_AUTH_MONITOR_ACTIVE = False
        return
    WECHAT_AUTH_MONITOR_ACTIVE = True
    threading.Thread(target=wechat_auth_monitor_loop, daemon=True).start()


def health_payload() -> dict[str, Any]:
    checks: dict[str, Any] = {}
    ok = True
    try:
        init_auth_db()
        with AUTH_DB_LOCK, auth_db() as conn:
            conn.execute("SELECT 1").fetchone()
        checks["database"] = {"ok": True}
    except Exception as exc:
        ok = False
        checks["database"] = {"ok": False, "error": str(exc)[:180]}

    try:
        PERSIST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        probe = PERSIST_CACHE_DIR / ".healthcheck"
        probe.write_text(str(time.time()), encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["runtimeCache"] = {"ok": True, "path": str(PERSIST_CACHE_DIR)}
    except Exception as exc:
        ok = False
        checks["runtimeCache"] = {"ok": False, "error": str(exc)[:180]}

    return {
        "ok": ok,
        "service": "xingyunshe-market-hot-dashboard",
        "env": env_value("XINGYUN_ENV", "development") or "development",
        "time": int(time.time() * 1000),
        "version": env_value("XINGYUN_VERSION", "local"),
        "checks": checks,
        "monitors": {
            "siteAlerts": SITE_ALERT_MONITOR_ACTIVE,
            "wechatAuth": WECHAT_AUTH_MONITOR_ACTIVE,
            "desktopAlerts": not env_flag("XINGYUN_DISABLE_DESKTOP_ALERT", default=is_production_mode()),
        },
    }


SEO_PUBLIC_PAGES: dict[str, dict[str, str]] = {
    "/index.html": {
        "title": "星云社 - 加密货币、港股、美股、A股跨市场热门榜",
        "description": "星云社聚合 Binance、OKX、Bitget、AICoin、富途、同花顺等市场热门榜，追踪加密货币、港股、美股、A股热点异动。",
        "keywords": "加密货币热门榜,币圈热门榜,港股热门榜,美股热门榜,A股热门榜,OKX热门榜,Binance热门榜,Bitget热门榜",
    },
    "/gainers.html": {
        "title": "涨幅榜 - Binance / OKX / Bitget / 港美 A 股实时涨幅排行",
        "description": "查看加密货币交易所、港股、美股和 A 股的实时涨幅榜，快速发现市场强势标的和异动机会。",
        "keywords": "涨幅榜,币安涨幅榜,OKX涨幅榜,Bitget涨幅榜,港股涨幅榜,美股涨幅榜,A股涨幅榜",
    },
    "/turnover.html": {
        "title": "成交额榜 - 加密货币、港股、美股、A股成交额排行",
        "description": "按交易所和市场查看成交额榜，聚焦资金最集中的加密货币、港股、美股和 A 股标的。",
        "keywords": "成交额榜,加密货币成交额榜,港股成交额榜,美股成交额榜,A股成交额榜,交易热度",
    },
    "/newboards.html": {
        "title": "新币新股榜 - 交易所新币榜与港美 A 股新股榜",
        "description": "追踪 Binance、OKX、Bitget 新币榜，以及港股、美股和 A 股新股上市动态。",
        "keywords": "新币榜,新股榜,交易所新币,港股新股,美股新股,A股新股",
    },
    "/listings.html": {
        "title": "上新 IPO - 交易所上新、IPO 日历、港美 A 股上市信息",
        "description": "集中查看交易所新币上线、IPO 日历、港股美股上市与 A 股新股动态。",
        "keywords": "交易所上新,新币上线,IPO日历,港股上市,美股上市,A股IPO",
    },
    "/newsflash.html": {
        "title": "律动快讯 - 币圈快讯与市场新闻时间线",
        "description": "聚合 BlockBeats 律动快讯，追踪加密货币、宏观、项目和交易所相关的重要市场信息。",
        "keywords": "律动快讯,币圈快讯,加密货币新闻,BlockBeats,市场快讯",
    },
    "/briefs.html": {
        "title": "自动简报 - 星云社市场热点与交易情报简报",
        "description": "自动汇总市场热门榜、成交额、涨幅异动和核心票热度，形成可快速阅读的交易情报简报。",
        "keywords": "市场简报,交易简报,币圈简报,热点简报,自动简报",
    },
    "/rss.html": {
        "title": "RSS 订阅 - 微信公众号与市场信息源聚合",
        "description": "订阅 RSS 和微信公众号信息源，聚合市场、科技、IPO、宏观和加密货币相关内容。",
        "keywords": "RSS订阅,微信公众号订阅,信息流,市场信息源,WeWe RSS",
    },
    "/legal.html": {
        "title": "星云社站务说明 - 用户协议、隐私政策、风险提示与数据来源",
        "description": "查看星云社用户协议、隐私政策、风险提示、免责声明、数据来源说明、关于我们和联系方式。",
        "keywords": "星云社用户协议,星云社隐私政策,风险提示,免责声明,数据来源说明,关于星云社,联系我们",
    },
}

SEO_NOINDEX_PATHS = {
    "/admin.html",
    "/login.html",
    "/profile.html",
    "/todo.html",
    "/xwatch.html",
}


def normalized_page_path(path: str) -> str:
    if path in {"", "/"}:
        return "/index.html"
    return path if path.startswith("/") else f"/{path}"


def request_base_url(handler: SimpleHTTPRequestHandler) -> str:
    configured = env_value("XINGYUN_PUBLIC_BASE_URL").rstrip("/")
    if configured:
        return configured
    host = handler.headers.get("Host") or "127.0.0.1:8765"
    scheme = "https" if handler.headers.get("X-Forwarded-Proto") == "https" else "http"
    return f"{scheme}://{host}"


def canonical_url(handler: SimpleHTTPRequestHandler, page_path: str) -> str:
    path = "/" if page_path == "/index.html" else page_path
    return f"{request_base_url(handler)}{path}"


def strip_runtime_seo_tags(html_text: str) -> str:
    patterns = [
        r"<title\b[^>]*>.*?</title>",
        r"<meta\s+[^>]*(?:name|property)=[\"'](?:description|keywords|robots|og:[^\"']+|twitter:[^\"']+)[\"'][^>]*>",
        r"<link\s+[^>]*rel=[\"']canonical[\"'][^>]*>",
        r"<script\s+[^>]*type=[\"']application/ld\+json[\"'][^>]*>.*?</script>",
    ]
    cleaned = html_text
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.I | re.S)
    return cleaned


def seo_schema_for_page(page_path: str, meta: dict[str, str], url: str) -> list[dict[str, Any]]:
    base = url.rsplit("/", 1)[0] if page_path != "/index.html" else url.rstrip("/")
    schema: list[dict[str, Any]] = [
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": "星云社",
            "url": request_base_url_for_schema(url),
            "logo": f"{request_base_url_for_schema(url)}/assets/xingyunshe-logo.png",
        },
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": "星云社",
            "url": request_base_url_for_schema(url),
            "description": SEO_PUBLIC_PAGES["/index.html"]["description"],
        },
    ]
    page_type = "CollectionPage" if page_path in SEO_PUBLIC_PAGES else "WebPage"
    schema.append(
        {
            "@context": "https://schema.org",
            "@type": page_type,
            "name": meta["title"],
            "description": meta["description"],
            "url": url,
            "isPartOf": {"@type": "WebSite", "name": "星云社", "url": request_base_url_for_schema(url)},
        }
    )
    if page_path in {"/index.html", "/gainers.html", "/turnover.html", "/newboards.html", "/listings.html"}:
        schema.append(
            {
                "@context": "https://schema.org",
                "@type": "ItemList",
                "name": meta["title"],
                "description": meta["description"],
                "itemListOrder": "https://schema.org/ItemListOrderDescending",
                "url": url,
            }
        )
    return schema


def request_base_url_for_schema(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def seo_head_block(handler: SimpleHTTPRequestHandler, page_path: str) -> str:
    is_noindex = page_path in SEO_NOINDEX_PATHS
    meta = SEO_PUBLIC_PAGES.get(
        page_path,
        {
            "title": "星云社 - 跨市场热点雷达",
            "description": "星云社提供加密货币、港股、美股、A股市场热点榜单、快讯、简报和信息源聚合工具。",
            "keywords": "星云社,市场热点,交易榜单,加密货币,港股,美股,A股",
        },
    )
    url = canonical_url(handler, page_path)
    og_image = f"{request_base_url(handler)}/assets/xingyunshe-logo.png"
    robots = "noindex,nofollow" if is_noindex else "index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1"
    lines = [
        f"<title>{html.escape(meta['title'])}</title>",
        f"<meta name=\"description\" content=\"{html.escape(meta['description'], quote=True)}\">",
        f"<meta name=\"keywords\" content=\"{html.escape(meta.get('keywords', ''), quote=True)}\">",
        f"<meta name=\"robots\" content=\"{robots}\">",
        f"<link rel=\"canonical\" href=\"{html.escape(url, quote=True)}\">",
        f"<meta property=\"og:title\" content=\"{html.escape(meta['title'], quote=True)}\">",
        f"<meta property=\"og:description\" content=\"{html.escape(meta['description'], quote=True)}\">",
        f"<meta property=\"og:url\" content=\"{html.escape(url, quote=True)}\">",
        "<meta property=\"og:type\" content=\"website\">",
        f"<meta property=\"og:image\" content=\"{html.escape(og_image, quote=True)}\">",
        "<meta name=\"twitter:card\" content=\"summary_large_image\">",
        f"<meta name=\"twitter:title\" content=\"{html.escape(meta['title'], quote=True)}\">",
        f"<meta name=\"twitter:description\" content=\"{html.escape(meta['description'], quote=True)}\">",
    ]
    if not is_noindex:
        lines.append(
            "<script type=\"application/ld+json\">"
            + json.dumps(seo_schema_for_page(page_path, meta, url), ensure_ascii=False, separators=(",", ":"))
            + "</script>"
        )
    return "\n".join(lines)


def site_footer_html() -> str:
    return """
<footer class="site-footer" aria-label="站点底部信息">
  <div class="site-footer-inner">
    <nav class="site-footer-columns" aria-label="站务链接">
      <section>
        <h2>关于</h2>
        <a href="./legal.html#about">关于我们</a>
        <a href="./legal.html#contact">联系我们</a>
        <a href="https://discord.gg/mKyCwtHW" target="_blank" rel="noreferrer noopener">社区</a>
        <a href="https://github.com/whitestar224/market-hot-dashboard" target="_blank" rel="noreferrer noopener">GitHub</a>
      </section>
      <section>
        <h2>条款与政策</h2>
        <a href="./legal.html#terms">用户协议</a>
        <a href="./legal.html#privacy">隐私政策</a>
        <a href="./legal.html#disclaimer">免责声明</a>
      </section>
      <section>
        <h2>数据与风险</h2>
        <a href="./legal.html#risk">风险提示</a>
        <a href="./legal.html#sources">数据来源说明</a>
        <a href="./briefs.html">自动简报</a>
        <a href="./newsflash.html">律动快讯</a>
      </section>
      <section>
        <h2>产品</h2>
        <a href="./index.html">热门榜</a>
        <a href="./gainers.html">涨幅榜</a>
        <a href="./turnover.html">成交额榜</a>
        <a href="./newboards.html">新币新股</a>
        <a href="./rss.html">RSS 订阅</a>
      </section>
    </nav>
  </div>
  <div class="site-footer-bottom">
    <span>© 2026 星云社</span>
    <span>仅供信息研究，不构成投资建议。</span>
  </div>
</footer>
""".strip()


def inject_site_footer(html_text: str) -> str:
    if 'class="site-footer"' in html_text:
        return html_text
    footer = site_footer_html()
    if re.search(r"</body>", html_text, flags=re.I):
        return re.sub(r"</body>", f"{footer}\n</body>", html_text, count=1, flags=re.I)
    return f"{html_text}\n{footer}"


def refresh_stylesheet_version(html_text: str) -> str:
    try:
        version = str(int((ROOT / "styles.css").stat().st_mtime))
    except Exception:
        version = str(int(time.time()))
    return re.sub(
        r'(href=["\'][^"\']*styles\.css)(?:\?v=[^"\']*)?(["\'])',
        rf"\1?v={version}\2",
        html_text,
        flags=re.I,
    )


def inject_seo_into_html(handler: SimpleHTTPRequestHandler, html_text: str, page_path: str) -> str:
    cleaned = strip_runtime_seo_tags(html_text)
    block = seo_head_block(handler, page_path)
    if re.search(r"</head>", cleaned, flags=re.I):
        cleaned = re.sub(r"</head>", f"{block}\n</head>", cleaned, count=1, flags=re.I)
    else:
        cleaned = f"{block}\n{cleaned}"
    return refresh_stylesheet_version(inject_site_footer(cleaned))


def robots_txt(handler: SimpleHTTPRequestHandler) -> str:
    base = request_base_url(handler)
    disallow_lines = "\n".join(f"Disallow: {path}" for path in sorted(SEO_NOINDEX_PATHS))
    return (
        "User-agent: *\n"
        "Allow: /\n"
        f"{disallow_lines}\n\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )


def sitemap_xml(handler: SimpleHTTPRequestHandler) -> str:
    base = request_base_url(handler)
    now = datetime.now().strftime("%Y-%m-%d")
    items = []
    for path in ["/index.html", "/gainers.html", "/turnover.html", "/newboards.html", "/listings.html", "/newsflash.html", "/briefs.html", "/rss.html", "/legal.html"]:
        loc = f"{base}{'/' if path == '/index.html' else path}"
        priority = "1.0" if path == "/index.html" else "0.8"
        changefreq = "hourly" if path in {"/index.html", "/gainers.html", "/turnover.html", "/newsflash.html"} else "daily"
        items.append(
            f"  <url><loc>{html.escape(loc)}</loc><lastmod>{now}</lastmod><changefreq>{changefreq}</changefreq><priority>{priority}</priority></url>"
        )
    return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n" + "\n".join(items) + "\n</urlset>\n"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_text(self, text: str, content_type: str, status: int = 200, cache_control: str = "no-cache"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_html_with_runtime_seo(self, page_path: str) -> bool:
        safe_path = normalized_page_path(page_path)
        if safe_path not in SEO_PUBLIC_PAGES and safe_path not in SEO_NOINDEX_PATHS:
            return False
        file_path = (ROOT / safe_path.lstrip("/")).resolve()
        if not str(file_path).startswith(str(ROOT)) or not file_path.exists() or file_path.suffix.lower() not in {".html", ".htm"}:
            return False
        html_text = file_path.read_text(encoding="utf-8", errors="replace")
        body = inject_seo_into_html(self, html_text, safe_path).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def end_headers(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            suffix = Path(parsed.path).suffix.lower()
            if suffix in {"", ".html", ".htm"}:
                self.send_header("Cache-Control", "no-store")
            elif suffix in {".js", ".css"}:
                cache_value = "public, max-age=604800, immutable" if parsed.query else "no-cache"
                self.send_header("Cache-Control", cache_value)
            elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".ico", ".svg", ".json", ".webmanifest"}:
                self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https: http://127.0.0.1:* http://localhost:*; "
            "font-src 'self' data:; "
            "connect-src 'self' https: http://127.0.0.1:* http://localhost:* ws://127.0.0.1:* ws://localhost:*; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        if cookie_secure_enabled():
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        super().end_headers()

    def send_json(self, payload: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None):
        if isinstance(payload, dict) and "error" in payload:
            payload = {**payload, "error": safe_error_text(payload.get("error"))}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        should_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "").lower() and len(body) > 1024
        if should_gzip:
            body = gzip.compress(body, compresslevel=5)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if should_gzip:
            self.send_header("Content-Encoding", "gzip")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_binary(self, body: bytes, content_type: str, status: int = 200, cache_control: str = "no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_JSON_BODY_BYTES:
            raise ValueError(f"JSON 请求体过大：{length} bytes，当前上限 {MAX_JSON_BODY_BYTES} bytes")
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def request_ip(self) -> str:
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
        return forwarded or (self.client_address[0] if self.client_address else "")

    def request_origin_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        candidates = [self.headers.get("Origin") or "", self.headers.get("Referer") or ""]
        for candidate in candidates:
            if not candidate:
                continue
            parsed = urlparse(candidate)
            if parsed.netloc and parsed.netloc.lower() != host:
                return False
            return True
        return True

    def validate_post_request(self, route: str) -> bool:
        if not route.startswith("/api/"):
            return True
        route_key = route.split("/", 3)[-1] or route
        ip = self.request_ip()
        if security_rate_limited(f"post:{ip}:{route_key}", 160, 60):
            self.send_json({"ok": False, "error": "请求过于频繁，请稍后再试"}, status=429)
            return False
        if route.startswith("/api/auth/") and security_rate_limited(f"auth-post:{ip}", 60, 300):
            self.send_json({"ok": False, "error": "认证请求过于频繁，请稍后再试"}, status=429)
            return False
        if not self.request_origin_allowed():
            write_audit_log(
                "post_origin_blocked",
                ip=ip,
                user_agent=self.headers.get("User-Agent") or "",
                metadata={"route": route, "origin": self.headers.get("Origin") or "", "referer": self.headers.get("Referer") or ""},
            )
            self.send_json({"ok": False, "error": "请求来源不合法"}, status=403)
            return False
        length = int(self.headers.get("Content-Length") or 0)
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if length > 0 and content_type not in {"application/json", "text/json"}:
            self.send_json({"ok": False, "error": "接口只接受 JSON 请求"}, status=415)
            return False
        return True

    def audit(self, action: str, *, actor: dict[str, Any] | None = None, object_type: str = "", object_id: str | int = "", metadata: dict[str, Any] | None = None) -> None:
        write_audit_log(
            action,
            actor=actor if actor is not None else self.current_user(),
            object_type=object_type,
            object_id=object_id,
            ip=self.request_ip(),
            user_agent=self.headers.get("User-Agent") or "",
            metadata=metadata,
        )

    def current_user(self) -> dict[str, Any] | None:
        token = parse_cookies(self.headers.get("Cookie")).get(AUTH_SESSION_COOKIE, "")
        return current_user_from_token(token)

    def require_auth(self) -> dict[str, Any] | None:
        user = self.current_user()
        if user:
            return user
        self.send_json({"ok": False, "error": "unauthorized", "loginUrl": "/login.html"}, status=401)
        return None

    def require_admin(self) -> dict[str, Any] | None:
        user = self.require_auth()
        if not user:
            return None
        if not is_admin(user):
            self.send_json({"ok": False, "error": "admin only"}, status=403)
            return None
        return user

    def redirect_to_login(self) -> None:
        target = quote(self.path or "/todo.html", safe="")
        self.send_response(302)
        self.send_header("Location", f"/login.html?next={target}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_auth_status(self) -> None:
        user = self.current_user()
        self.send_json({"ok": True, "authenticated": bool(user), "user": user, "needsSetup": False})

    def do_auth_register(self) -> None:
        payload = self.read_json()
        username = clean_username(payload.get("username"))
        password = str(payload.get("password") or "")
        if not username:
            self.send_json({"ok": False, "error": "用户名需为 2-32 位中文、字母、数字或 _.-"}, status=400)
            return
        if len(password) < 8 or len(password) > 128:
            self.send_json({"ok": False, "error": "密码至少 8 位"}, status=400)
            return
        try:
            user = create_user(username, password, role="user")
        except sqlite3.IntegrityError:
            self.send_json({"ok": False, "error": "用户名已存在"}, status=409)
            return
        token, expires_at = create_session(int(user["id"]), self.headers.get("User-Agent") or "")
        self.audit("user_register", actor=user, object_type="user", object_id=int(user["id"]))
        self.send_json(
            {"ok": True, "authenticated": True, "user": user},
            headers={"Set-Cookie": cookie_header(token, expires_at)},
        )

    def do_auth_signup(self) -> None:
        payload = self.read_json()
        username = clean_username(payload.get("username"))
        password = str(payload.get("password") or "")
        if not username:
            self.send_json({"ok": False, "error": "用户名需为 2-32 位中文、字母、数字或 _.-"}, status=400)
            return
        if len(password) < 8 or len(password) > 128:
            self.send_json({"ok": False, "error": "密码至少 8 位"}, status=400)
            return
        try:
            user = create_user(username, password, role="user")
        except sqlite3.IntegrityError:
            self.send_json({"ok": False, "error": "用户名已存在"}, status=409)
            return
        token, expires_at = create_session(int(user["id"]), self.headers.get("User-Agent") or "")
        self.audit("user_signup", actor=user, object_type="user", object_id=int(user["id"]))
        self.send_json(
            {"ok": True, "authenticated": True, "user": user},
            headers={"Set-Cookie": cookie_header(token, expires_at)},
        )

    def do_auth_login(self) -> None:
        payload = self.read_json()
        username = clean_username(payload.get("username"))
        password = str(payload.get("password") or "")
        client_key = f"{self.client_address[0]}:{username or 'unknown'}"
        if login_rate_limited(client_key):
            self.send_json({"ok": False, "error": "尝试过于频繁，稍后再试"}, status=429)
            return
        if not username or not password:
            self.send_json({"ok": False, "error": "请输入用户名和密码"}, status=400)
            return
        with AUTH_DB_LOCK, auth_db() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            record_login_failure(client_key)
            self.send_json({"ok": False, "error": "用户名或密码不正确"}, status=401)
            return
        clear_login_failures(client_key)
        user = public_user(row)
        token, expires_at = create_session(int(row["id"]), self.headers.get("User-Agent") or "")
        self.send_json(
            {"ok": True, "authenticated": True, "user": user},
            headers={"Set-Cookie": cookie_header(token, expires_at)},
        )

    def do_auth_logout(self) -> None:
        token = parse_cookies(self.headers.get("Cookie")).get(AUTH_SESSION_COOKIE, "")
        delete_session(token)
        self.send_json({"ok": True}, headers={"Set-Cookie": expired_cookie_header()})

    def do_auth_status(self) -> None:
        user = self.current_user()
        self.send_json(
            {
                "ok": True,
                "authenticated": bool(user),
                "user": user,
                "needsSetup": False,
                "googleEnabled": google_oauth_enabled(),
            }
        )

    def do_auth_register(self) -> None:
        payload = self.read_json()
        username = clean_username(payload.get("username"))
        password = str(payload.get("password") or "")
        if not username:
            self.send_json({"ok": False, "error": "用户名需为 2-32 位中文、字母、数字或 _.-"}, status=400)
            return
        if len(password) < 8 or len(password) > 128:
            self.send_json({"ok": False, "error": "密码至少 8 位"}, status=400)
            return
        try:
            user = create_user(username, password, role="user", display_name=username)
        except sqlite3.IntegrityError:
            self.send_json({"ok": False, "error": "用户名已存在"}, status=409)
            return
        token, expires_at = create_session(int(user["id"]), self.headers.get("User-Agent") or "")
        self.audit("user_register", actor=user, object_type="user", object_id=int(user["id"]))
        self.send_json(
            {"ok": True, "authenticated": True, "user": user},
            headers={"Set-Cookie": cookie_header(token, expires_at)},
        )

    def do_auth_signup(self) -> None:
        payload = self.read_json()
        username = clean_username(payload.get("username"))
        password = str(payload.get("password") or "")
        if not username:
            self.send_json({"ok": False, "error": "用户名需为 2-32 位中文、字母、数字或 _.-"}, status=400)
            return
        if len(password) < 8 or len(password) > 128:
            self.send_json({"ok": False, "error": "密码至少 8 位"}, status=400)
            return
        try:
            user = create_user(username, password, role="user", display_name=username)
        except sqlite3.IntegrityError:
            self.send_json({"ok": False, "error": "用户名已存在"}, status=409)
            return
        token, expires_at = create_session(int(user["id"]), self.headers.get("User-Agent") or "")
        self.audit("user_signup", actor=user, object_type="user", object_id=int(user["id"]))
        self.send_json(
            {"ok": True, "authenticated": True, "user": user},
            headers={"Set-Cookie": cookie_header(token, expires_at)},
        )

    def do_auth_login(self) -> None:
        payload = self.read_json()
        username = clean_username(payload.get("username"))
        password = str(payload.get("password") or "")
        client_key = f"{self.client_address[0]}:{username or 'unknown'}"
        if login_rate_limited(client_key):
            self.audit("login_rate_limited", metadata={"username": username})
            self.send_json({"ok": False, "error": "尝试过于频繁，请稍后再试"}, status=429)
            return
        if not username or not password:
            self.send_json({"ok": False, "error": "请输入用户名和密码"}, status=400)
            return
        with AUTH_DB_LOCK, auth_db() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            record_login_failure(client_key)
            self.audit("login_failed", metadata={"username": username})
            self.send_json({"ok": False, "error": "用户名或密码不正确"}, status=401)
            return
        clear_login_failures(client_key)
        user = public_user(row)
        token, expires_at = create_session(int(row["id"]), self.headers.get("User-Agent") or "")
        self.audit("login_success", actor=user, object_type="user", object_id=int(row["id"]))
        self.send_json(
            {"ok": True, "authenticated": True, "user": user},
            headers={"Set-Cookie": cookie_header(token, expires_at)},
        )

    def do_auth_logout(self) -> None:
        user = self.current_user()
        token = parse_cookies(self.headers.get("Cookie")).get(AUTH_SESSION_COOKIE, "")
        delete_session(token)
        self.audit("logout", actor=user, object_type="user", object_id=(user or {}).get("id", ""))
        self.send_json({"ok": True}, headers={"Set-Cookie": expired_cookie_header()})

    def do_auth_phone_send(self) -> None:
        payload = self.read_json()
        phone = clean_phone(payload.get("phone"))
        if not phone:
            self.send_json({"ok": False, "error": "请输入有效手机号"}, status=400)
            return
        try:
            self.send_json(issue_phone_code(phone, self.client_address[0]))
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=429)
        except Exception as exc:
            self.send_json({"ok": False, "error": f"验证码发送失败：{exc}"}, status=502)

    def do_auth_email_send(self) -> None:
        payload = self.read_json()
        email = clean_email(payload.get("email"))
        if not email:
            self.send_json({"ok": False, "error": "请输入有效邮箱"}, status=400)
            return
        try:
            self.send_json(issue_email_code(email, self.client_address[0]))
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=429)
        except Exception as exc:
            self.send_json({"ok": False, "error": f"验证码发送失败：{exc}"}, status=502)

    def do_auth_email_login(self) -> None:
        payload = self.read_json()
        email = clean_email(payload.get("email"))
        code = str(payload.get("code") or "").strip()
        client_key = f"email:{self.client_address[0]}:{email or 'unknown'}"
        if login_rate_limited(client_key):
            self.audit("email_login_rate_limited", metadata={"email": mask_email(email)})
            self.send_json({"ok": False, "error": "尝试过于频繁，请稍后再试"}, status=429)
            return
        if not email or not re.fullmatch(r"\d{6}", code):
            self.send_json({"ok": False, "error": "请输入邮箱和 6 位验证码"}, status=400)
            return
        try:
            verify_email_code(email, code)
        except ValueError as exc:
            record_login_failure(client_key)
            self.audit("email_login_failed", metadata={"email": mask_email(email), "reason": str(exc)})
            self.send_json({"ok": False, "error": str(exc)}, status=401)
            return
        clear_login_failures(client_key)
        user = user_from_email(email)
        token, expires_at = create_session(int(user["id"]), self.headers.get("User-Agent") or "")
        self.audit("email_login_success", actor=user, object_type="user", object_id=int(user["id"]))
        self.send_json(
            {"ok": True, "authenticated": True, "user": user},
            headers={"Set-Cookie": cookie_header(token, expires_at)},
        )

    def do_auth_email_bind(self) -> None:
        user = self.require_auth()
        if not user:
            return
        payload = self.read_json()
        email = clean_email(payload.get("email"))
        code = str(payload.get("code") or "").strip()
        if not email or not re.fullmatch(r"\d{6}", code):
            self.send_json({"ok": False, "error": "请输入邮箱和 6 位验证码"}, status=400)
            return
        try:
            verify_email_code(email, code)
            next_user = bind_email_to_user(int(user["id"]), email)
            self.audit("email_bind", actor=next_user, object_type="user", object_id=int(user["id"]), metadata={"email": mask_email(email)})
            self.send_json({"ok": True, "user": next_user})
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
        except sqlite3.IntegrityError:
            self.send_json({"ok": False, "error": "这个邮箱已绑定其他账号"}, status=409)

    def do_auth_phone_login(self) -> None:
        payload = self.read_json()
        phone = clean_phone(payload.get("phone"))
        code = str(payload.get("code") or "").strip()
        client_key = f"phone:{self.client_address[0]}:{phone or 'unknown'}"
        if login_rate_limited(client_key):
            self.audit("phone_login_rate_limited", metadata={"phone": mask_phone(phone)})
            self.send_json({"ok": False, "error": "尝试过于频繁，请稍后再试"}, status=429)
            return
        if not phone or not re.fullmatch(r"\d{6}", code):
            self.send_json({"ok": False, "error": "请输入手机号和 6 位验证码"}, status=400)
            return
        try:
            verify_phone_code(phone, code)
        except ValueError as exc:
            record_login_failure(client_key)
            self.audit("phone_login_failed", metadata={"phone": mask_phone(phone), "reason": str(exc)})
            self.send_json({"ok": False, "error": str(exc)}, status=401)
            return
        clear_login_failures(client_key)
        user = user_from_phone(phone)
        token, expires_at = create_session(int(user["id"]), self.headers.get("User-Agent") or "")
        self.audit("phone_login_success", actor=user, object_type="user", object_id=int(user["id"]))
        self.send_json(
            {"ok": True, "authenticated": True, "user": user},
            headers={"Set-Cookie": cookie_header(token, expires_at)},
        )

    def do_auth_phone_bind(self) -> None:
        user = self.require_auth()
        if not user:
            return
        payload = self.read_json()
        phone = clean_phone(payload.get("phone"))
        code = str(payload.get("code") or "").strip()
        if not phone or not re.fullmatch(r"\d{6}", code):
            self.send_json({"ok": False, "error": "请输入手机号和 6 位验证码"}, status=400)
            return
        try:
            verify_phone_code(phone, code)
            next_user = bind_phone_to_user(int(user["id"]), phone)
            self.audit("phone_bind", actor=next_user, object_type="user", object_id=int(user["id"]), metadata={"phone": mask_phone(phone)})
            self.send_json({"ok": True, "user": next_user})
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
        except sqlite3.IntegrityError:
            self.send_json({"ok": False, "error": "这个手机号已绑定其他账号"}, status=409)

    def do_auth_profile_update(self) -> None:
        user = self.require_auth()
        if not user:
            return
        try:
            next_user = update_user_profile(int(user["id"]), self.read_json())
            self.audit("profile_update", actor=next_user, object_type="user", object_id=int(user["id"]))
            self.send_json({"ok": True, "user": next_user})
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def google_redirect_uri(self) -> str:
        explicit = env_value("GOOGLE_REDIRECT_URI")
        if explicit:
            return explicit
        base = env_value("XINGYUN_PUBLIC_BASE_URL")
        if not base:
            host = self.headers.get("Host") or "127.0.0.1:8765"
            scheme = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
            base = f"{scheme}://{host}"
        return f"{base.rstrip('/')}/api/auth/google/callback"

    def do_auth_google_start(self, query: dict[str, list[str]]) -> None:
        next_path = safe_next_path((query.get("next") or ["/index.html"])[0])
        login_hint = str((query.get("login_hint") or [""])[0]).strip()[:160]
        mode = str((query.get("mode") or ["login"])[0]).strip().lower()
        current_user = self.current_user()
        is_bind_mode = mode == "bind"
        if is_bind_mode and not current_user:
            self.send_response(302)
            self.send_header("Location", "/login.html?next=%2Fprofile.html")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if not google_oauth_enabled():
            target = f"/login.html?next={quote(next_path, safe='')}&error=google_not_configured"
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        state = secrets.token_urlsafe(24)
        with AUTH_OAUTH_LOCK:
            AUTH_OAUTH_STATES[state] = {
                "next": next_path,
                "expiresAt": int(time.time()) + 10 * 60,
                "mode": "bind" if is_bind_mode else "login",
                "userId": int(current_user["id"]) if current_user else None,
            }
        oauth_params = {
            "client_id": env_value("GOOGLE_CLIENT_ID"),
            "redirect_uri": self.google_redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
        if login_hint and "@" in login_hint:
            oauth_params["login_hint"] = login_hint
        params = urlencode(oauth_params)
        self.send_response(302)
        self.send_header("Location", f"https://accounts.google.com/o/oauth2/v2/auth?{params}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_auth_google_callback(self, query: dict[str, list[str]]) -> None:
        state = (query.get("state") or [""])[0]
        code = (query.get("code") or [""])[0]
        with AUTH_OAUTH_LOCK:
            state_payload = AUTH_OAUTH_STATES.pop(state, None)
        next_path = safe_next_path((state_payload or {}).get("next"))
        if not state_payload or int(state_payload.get("expiresAt") or 0) < int(time.time()) or not code:
            self.audit("google_oauth_invalid_state", metadata={"next": next_path})
            target = f"/login.html?next={quote(next_path, safe='')}&error=google_state_invalid"
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        try:
            token_response = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": env_value("GOOGLE_CLIENT_ID"),
                    "client_secret": env_value("GOOGLE_CLIENT_SECRET"),
                    "redirect_uri": self.google_redirect_uri(),
                    "grant_type": "authorization_code",
                },
                timeout=15,
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token")
            if not access_token:
                raise RuntimeError("Google 没有返回 access_token")
            user_response = requests.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
            user_response.raise_for_status()
            user_info = user_response.json()
            if (state_payload or {}).get("mode") == "bind":
                current_user = self.current_user()
                state_user_id = int((state_payload or {}).get("userId") or 0)
                if not current_user or int(current_user["id"]) != state_user_id:
                    raise RuntimeError("登录状态已变化，请重新绑定 Google")
                user = bind_google_to_user(state_user_id, user_info)
                self.audit("google_bind", actor=user, object_type="user", object_id=state_user_id)
                self.send_response(302)
                self.send_header("Location", add_query_params(next_path, {"profile": "1", "bound": "google"}))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            user = google_user_login(user_info)
            token, expires_at = create_session(int(user["id"]), self.headers.get("User-Agent") or "")
            self.audit("google_login_success", actor=user, object_type="user", object_id=int(user["id"]))
            self.send_response(302)
            self.send_header("Location", next_path)
            self.send_header("Set-Cookie", cookie_header(token, expires_at))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        except Exception as exc:
            self.audit("google_oauth_failed", metadata={"reason": safe_error_text(str(exc))[:120]})
            if (state_payload or {}).get("mode") == "bind":
                target = add_query_params(next_path, {"profile": "1", "error": str(exc)[:120]})
            else:
                target = f"/login.html?next={quote(next_path, safe='')}&error={quote(str(exc)[:120], safe='')}"
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        force_refresh = (query.get("refresh") or ["0"])[0].lower() in {"1", "true", "yes"}
        if parsed.path == "/robots.txt":
            self.send_text(robots_txt(self), "text/plain", cache_control="public, max-age=3600")
            return
        if parsed.path == "/sitemap.xml":
            self.send_text(sitemap_xml(self), "application/xml", cache_control="public, max-age=3600")
            return
        if parsed.path in {"/api/health", "/healthz"}:
            payload = health_payload()
            self.send_json(payload, status=200 if payload.get("ok") else 503)
            return
        protected_pages = {"/todo.html", "/xwatch.html", "/profile.html"}
        if parsed.path == "/admin.html":
            user = self.current_user()
            if not user:
                self.redirect_to_login()
                return
            if not is_admin(user):
                self.send_error(403, "admin only")
                return
        if parsed.path in protected_pages and not self.current_user():
            self.redirect_to_login()
            return
        if parsed.path == "/api/auth/status":
            self.do_auth_status()
            return
        if parsed.path == "/api/auth/google/config":
            self.send_json({"ok": True, "enabled": google_oauth_enabled()})
            return
        if parsed.path == "/api/auth/model-settings":
            user = self.require_auth()
            if not user:
                return
            self.send_json(
                {
                    "ok": True,
                    "settings": public_llm_settings(llm_settings_for_user(int(user["id"]))),
                    "presets": public_model_provider_presets(),
                }
            )
            return
        if parsed.path == "/api/auth/google/start":
            self.do_auth_google_start(query)
            return
        if parsed.path == "/api/auth/google/callback":
            self.do_auth_google_callback(query)
            return
        if parsed.path == "/api/admin/summary":
            user = self.require_admin()
            if not user:
                return
            try:
                self.send_json(admin_summary_payload())
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/admin/users":
            user = self.require_admin()
            if not user:
                return
            try:
                self.send_json({"ok": True, "users": admin_user_rows()})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/market-hot":
            self.send_json(cached_api_payload("market-hot", market_payload, 60, force_refresh=force_refresh))
            return
        if parsed.path == "/api/gainers-rankings":
            self.send_json(cached_api_payload("gainers-rankings", gainers_rankings_payload, 60, force_refresh=force_refresh))
            return
        if parsed.path == "/api/turnover-rankings":
            self.send_json(cached_api_payload("turnover-rankings", turnover_rankings_payload, 60, force_refresh=force_refresh))
            return
        if parsed.path == "/api/newsflash":
            try:
                payload = refresh_api_cache_now("newsflash", fetch_blockbeats_flash) if force_refresh else cached_api_payload("newsflash", fetch_blockbeats_flash, 20)
                self.send_json(payload)
            except Exception as exc:
                self.send_json({"items": [], "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/automation-briefs":
            try:
                self.send_json(cached_api_payload("automation-briefs", automation_briefs_payload, 60, force_refresh=force_refresh))
            except Exception as exc:
                self.send_json({"briefs": [], "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/listing-events":
            try:
                self.send_json(cached_api_payload("listing-events-v2", listing_events_payload, 180, force_refresh=force_refresh))
            except Exception as exc:
                self.send_json({"sections": [], "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/new-coin-rankings":
            try:
                self.send_json(cached_api_payload("new-coin-rankings", new_coin_rankings_payload, 180, force_refresh=force_refresh))
            except Exception as exc:
                self.send_json({"sections": [], "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/x-kol-sources":
            user = self.require_auth()
            if not user:
                return
            try:
                self.send_json(x_kol_sources_payload(user))
            except Exception as exc:
                self.send_json({"ok": False, "sources": [], "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/x-kol-feed":
            user = self.require_auth()
            if not user:
                return
            try:
                self.send_json(cached_api_payload(f"x-kol-feed-v4-u{user['id']}", lambda: x_kol_feed_payload(user), 60, force_refresh=force_refresh))
            except Exception as exc:
                self.send_json({"ok": False, "sources": [], "items": [], "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/todo-state":
            user = self.require_auth()
            if not user:
                return
            try:
                self.send_json(todo_state_payload(user))
            except Exception as exc:
                self.send_json({"ok": False, "projects": [], "tasks": [], "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/rss-sources":
            try:
                self.send_json(rss_sources_payload(self.current_user()))
            except Exception as exc:
                self.send_json({"ok": False, "sources": [], "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/rss-items":
            try:
                self.send_json(rss_items_payload(self.current_user()))
            except Exception as exc:
                self.send_json({"ok": False, "items": [], "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/wechat-account-status":
            try:
                self.send_json(wechat_account_status_payload(force_validate=force_refresh))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/wechat-qr-image":
            uuid = clean_feed_text((query.get("uuid") or [""])[0], 100)
            image_file = wechat_qr_image_file(uuid)
            if not image_file:
                self.send_json({"ok": False, "error": "qr image not found"}, status=404)
                return
            suffix = image_file.suffix.lower()
            content_type = "image/png"
            if suffix in {".jpg", ".jpeg"}:
                content_type = "image/jpeg"
            elif suffix == ".gif":
                content_type = "image/gif"
            self.send_binary(image_file.read_bytes(), content_type, cache_control="no-store")
            return
        if parsed.path == "/api/site-alert-monitor-status":
            self.send_json(
                {
                    "ok": True,
                    "active": SITE_ALERT_MONITOR_ACTIVE,
                    "startedAt": SITE_ALERT_MONITOR_STARTED_AT,
                }
            )
            return
        if self.serve_html_with_runtime_seo(parsed.path):
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/")
        if not self.validate_post_request(route):
            return
        if route == "/api/auth/register":
            self.do_auth_register()
            return
        if route == "/api/auth/signup":
            self.do_auth_signup()
            return
        if route == "/api/auth/login":
            self.do_auth_login()
            return
        if route == "/api/auth/email-code/send":
            self.do_auth_email_send()
            return
        if route == "/api/auth/email-code/login":
            self.do_auth_email_login()
            return
        if route == "/api/auth/email/bind":
            self.do_auth_email_bind()
            return
        if route == "/api/auth/phone-code/send":
            self.do_auth_phone_send()
            return
        if route == "/api/auth/phone-code/login":
            self.do_auth_phone_login()
            return
        if route == "/api/auth/phone/bind":
            self.do_auth_phone_bind()
            return
        if route == "/api/auth/profile":
            self.do_auth_profile_update()
            return
        if route == "/api/auth/model-settings":
            user = self.require_auth()
            if not user:
                return
            try:
                payload = self.read_json()
                settings = save_user_model_settings(int(user["id"]), payload)
                self.audit(
                    "model_settings_update",
                    actor=user,
                    object_type="user_model_settings",
                    object_id=int(user["id"]),
                    metadata={
                        "provider": settings.get("provider"),
                        "model": settings.get("model"),
                        "hasApiKey": settings.get("hasApiKey"),
                    },
                )
                self.send_json(
                    {
                        "ok": True,
                        "settings": settings,
                        "presets": public_model_provider_presets(),
                    }
                )
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if route == "/api/auth/model-options":
            user = self.require_auth()
            if not user:
                return
            try:
                self.send_json(model_options_payload(int(user["id"]), self.read_json()))
            except Exception as exc:
                self.send_json({"ok": False, "models": [], "error": str(exc)}, status=502)
            return
        if route == "/api/auth/logout":
            self.do_auth_logout()
            return
        if route == "/api/admin/users/create":
            user = self.require_admin()
            if not user:
                return
            try:
                payload = self.read_json()
                result = admin_create_user(payload)
                self.audit(
                    "admin_user_create",
                    actor=user,
                    object_type="user",
                    metadata={"username": clean_username(payload.get("username")), "role": str(payload.get("role") or "user")},
                )
                self.send_json(result)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if route == "/api/admin/users/update":
            user = self.require_admin()
            if not user:
                return
            try:
                payload = self.read_json()
                result = admin_set_user(payload, user)
                self.audit(
                    "admin_user_update",
                    actor=user,
                    object_type="user",
                    object_id=payload.get("id", ""),
                    metadata={"role": payload.get("role"), "disabled": payload.get("disabled")},
                )
                self.send_json(result)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if route == "/api/admin/users/delete":
            user = self.require_admin()
            if not user:
                return
            try:
                payload = self.read_json()
                result = admin_delete_user(payload, user)
                self.audit("admin_user_delete", actor=user, object_type="user", object_id=payload.get("id", ""))
                self.send_json(result)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if route == "/api/admin/cache/refresh":
            user = self.require_admin()
            if not user:
                return
            try:
                result = admin_refresh_cache_payload()
                self.audit("admin_cache_refresh", actor=user, object_type="cache")
                self.send_json(result)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if route == "/api/admin/cache/clear":
            user = self.require_admin()
            if not user:
                return
            try:
                result = admin_clear_cache_payload()
                self.audit("admin_cache_clear", actor=user, object_type="cache")
                self.send_json(result)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if route == "/api/rss-sources" or route.endswith("/api/rss-sources"):
            try:
                self.send_json(save_rss_sources_payload(self.read_json(), self.current_user()))
            except Exception as exc:
                self.send_json({"ok": False, "sources": [], "error": str(exc)}, status=502)
            return
        if route == "/api/rss-items" or route.endswith("/api/rss-items"):
            try:
                self.send_json(save_rss_items_payload(self.read_json(), self.current_user()))
            except Exception as exc:
                self.send_json({"ok": False, "items": [], "error": str(exc)}, status=502)
            return
        if route == "/api/rss-refresh-all" or route.endswith("/api/rss-refresh-all"):
            try:
                self.send_json(rss_refresh_all_payload(self.read_json(), self.current_user()))
            except Exception as exc:
                self.send_json({"ok": False, "sources": [], "items": [], "error": str(exc)}, status=502)
            return
        if route == "/api/rss-fetch" or route.endswith("/api/rss-fetch"):
            try:
                self.send_json(rss_fetch_payload(self.read_json()))
            except Exception as exc:
                self.send_json({"ok": False, "items": [], "error": str(exc)}, status=502)
            return
        if route == "/api/wechat-mp-resolve" or route.endswith("/api/wechat-mp-resolve"):
            try:
                self.send_json(resolve_wechat_mp_payload(self.read_json()))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if route == "/api/wechat-mp-fetch" or route.endswith("/api/wechat-mp-fetch"):
            try:
                self.send_json(wechat_mp_fetch_payload(self.read_json()))
            except Exception as exc:
                self.send_json({"ok": False, "items": [], "error": str(exc)}, status=502)
            return
        if route == "/api/wechat-login-begin" or route.endswith("/api/wechat-login-begin"):
            try:
                self.send_json(wechat_login_begin_payload())
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if route == "/api/wechat-login-poll" or route.endswith("/api/wechat-login-poll"):
            try:
                self.send_json(wechat_login_poll_payload(self.read_json()))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if route == "/api/wechat-auth-alert" or route.endswith("/api/wechat-auth-alert"):
            try:
                payload = self.read_json()
                self.send_json(notify_wechat_auth_required(payload.get("reason") or "请重新完成微信公众号授权", force=bool(payload.get("force"))))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if route == "/api/x-kol-sources" or route.endswith("/api/x-kol-sources"):
            user = self.require_auth()
            if not user:
                return
            try:
                self.send_json(save_x_kol_sources_payload(self.read_json(), user))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if route == "/api/todo-state" or route.endswith("/api/todo-state"):
            user = self.require_auth()
            if not user:
                return
            try:
                self.send_json(save_todo_state_payload(self.read_json(), user))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if route == "/api/desktop-alert" or route.endswith("/api/desktop-alert"):
            try:
                self.send_json(launch_desktop_alert(self.read_json()))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
            return
        if route == "/api/ai/rank-insights" or route.endswith("/api/ai/rank-insights"):
            try:
                user = self.current_user()
                settings = llm_settings_for_user(int(user["id"])) if user else system_llm_settings()
                self.send_json(deepseek_rank_insights_payload(self.read_json(), settings=settings))
            except Exception as exc:
                self.send_json({"ok": False, "enabled": deepseek_enabled(), "provider": "deepseek", "insights": {}, "error": str(exc)}, status=502)
            return
        self.send_json({"error": "not found", "path": parsed.path}, status=404)


def warm_api_response_cache() -> None:
    for key, fetcher in (
        ("newsflash", fetch_blockbeats_flash),
        ("automation-briefs", automation_briefs_payload),
        ("listing-events", listing_events_payload),
        ("new-coin-rankings", new_coin_rankings_payload),
        ("market-hot", market_payload),
        ("gainers-rankings", gainers_rankings_payload),
        ("turnover-rankings", turnover_rankings_payload),
        ("x-kol-feed-v4", x_kol_feed_payload),
    ):
        trigger_api_refresh(key, fetcher)


def main():
    parser = argparse.ArgumentParser()
    default_host = env_value("XINGYUN_HOST", "127.0.0.1") or "127.0.0.1"
    try:
        default_port = int(env_value("PORT") or env_value("XINGYUN_PORT") or "8765")
    except ValueError:
        default_port = 8765
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=default_port)
    args = parser.parse_args()
    init_auth_db()
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Market hot dashboard: http://{args.host}:{args.port}/")
    print(f"BlockBeats newsflash: http://{args.host}:{args.port}/newsflash.html")
    print(f"Automation briefs: http://{args.host}:{args.port}/briefs.html")
    print(f"Listings and IPO: http://{args.host}:{args.port}/listings.html")
    print(f"RSS subscriptions: http://{args.host}:{args.port}/rss.html")
    print(f"X KOL watch: http://{args.host}:{args.port}/xwatch.html")
    print(f"Admin console: http://{args.host}:{args.port}/admin.html")
    threading.Thread(target=warm_runtime_cache, daemon=True).start()
    warm_api_response_cache()
    start_site_alert_monitor()
    start_wechat_auth_monitor()
    server.serve_forever()


if __name__ == "__main__":
    main()
