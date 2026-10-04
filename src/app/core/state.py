"""从 server.py 抽取的模块（Phase 3 拆分）。

来源: server.py 第 253-1499 行（区间抽取）
本文件内容由 tools/extract_module.py 机械搬移，未做任何语义修改。

注意：这是【共享可变状态】的集中地。上层模块一律 `from app.core.state import X`
引用同名对象，绝不可重新 Lock()/新建 dict —— 否则会出现第二把锁，
互斥静默失效（表现为偶发而非必现，极难排查）。

为什么这里 import 了业务模块（chain_ecosystem_monitor / event_flow / ...）：
这些单例（Store / Monitor / Queue）在 server.py 里就是在模块级构造的，
搬过来就必须连构造一起搬，否则 server 那边会重新构造一份 —— 那就不是同一个
对象了。这是 Strangler Fig 阶段的过渡形态，见 app/core/__init__.py 的说明。

```python
# 正确的引用方式（server.py 与后续拆分出的模块都照此办理）
from app.core.state import CACHE, CACHE_LOCK, CHAIN_ECOSYSTEM_MONITOR

# 错误：这会在本模块新建一个完全无关的锁/字典，原对象不再被共享
CACHE_LOCK = threading.Lock()
```
"""

from __future__ import annotations
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
import httpx
from alert_delivery import AlertDeliveryStore
from app.core.paths import ROOT
from chain_ecosystem_monitor import ChainEcosystemMonitor, ChainEcosystemStore
from event_flow import EventFlowStore
from smart_money_monitor import SmartMoneyMonitor
from trench_person_signals import important_person_sources
from x_tweet_analysis import XTweetAnalysisQueue


CACHE: dict[str, tuple[float, Any]] = {}


CACHE_LOCK = threading.Lock()


CACHE_TTL = 15


CACHE_MAX_ENTRIES = 96


SERVER_SHUTDOWN_EVENT = threading.Event()


# The dashboard is intentionally I/O-heavy and keeps many mostly-idle workers.
# A 1 MiB stack is ample for these non-recursive tasks and avoids reserving the
# platform default stack for every pool/request thread.
THREAD_STACK_SIZE_BYTES = max(
    512 * 1024,
    min(4 * 1024 * 1024, int(float(os.getenv("XINGYUN_THREAD_STACK_KB", "1024") or "1024")) * 1024),
)


try:
    threading.stack_size(THREAD_STACK_SIZE_BYTES)
except (RuntimeError, ValueError):
    THREAD_STACK_SIZE_BYTES = 0


EVENT_MONITOR_DEX_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


EVENT_MONITOR_DEX_CACHE_LOCK = threading.Lock()


EVENT_MONITOR_DEX_CACHE_TTL_SECONDS = 120


NEWS_TRADE_DISCOVERY_CACHE: dict[str, tuple[float, Any]] = {}


NEWS_TRADE_DISCOVERY_CACHE_LOCK = threading.Lock()


NEWS_TRADE_DISCOVERY_CACHE_TTL_SECONDS = max(
    60,
    int(float(os.getenv("NEWS_TRADE_DISCOVERY_CACHE_SECONDS", "300") or "300")),
)


NEWS_TRADE_SECURITY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


NEWS_TRADE_SECURITY_CACHE_LOCK = threading.Lock()


NEWS_TRADE_SECURITY_INFLIGHT: set[str] = set()


NEWS_TRADE_SECURITY_POOL = ThreadPoolExecutor(
    max_workers=max(2, min(6, int(os.getenv("NEWS_TRADE_SECURITY_WORKERS", "3") or "3"))),
    thread_name_prefix="news-trade-security",
)


NEWS_TRADE_SECURITY_CACHE_TTL_SECONDS = max(
    120,
    int(float(os.getenv("NEWS_TRADE_SECURITY_CACHE_SECONDS", "900") or "900")),
)


NEWS_TRADE_SECURITY_NEGATIVE_TTL_SECONDS = max(
    30,
    int(float(os.getenv("NEWS_TRADE_SECURITY_NEGATIVE_CACHE_SECONDS", "180") or "180")),
)


NEWS_TRADE_DISCOVERY_CACHE_MAX_ENTRIES = 256


NEWS_TRADE_SECURITY_CACHE_MAX_ENTRIES = 512


EVENT_MONITOR_CORE_CACHE_TTL_SECONDS = max(
    15,
    int(float(os.getenv("EVENT_MONITOR_CORE_CACHE_SECONDS", "120") or "120")),
)


EVENT_MONITOR_CORE_BUILD_LOCK = threading.Lock()


NEWS_TRADE_SEARCH_LOCK = threading.Lock()


NEWS_TRADE_SEARCH_PREVIEWS: dict[str, dict[str, Any]] = {}


NEWS_TRADE_SEARCH_PREVIEW_TTL_SECONDS = 15 * 60


NEWS_TRADE_TOPIC_POOL_LOCK = threading.Lock()


NEWS_TRADE_AI_LOCK = threading.Lock()


EVENT_FLOW_NEWS_RECORD_LOCK = threading.Lock()


NEWSFLASH_SEMANTIC_AI_LOCK = threading.Lock()


NEWS_TRADE_AI_INFLIGHT: set[str] = set()


NEWS_TRADE_AI_RETRY_AFTER: dict[str, float] = {}


NEWS_TRADE_AI_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="news-trade-ai")


ROTATION_AI_LOCK = threading.Lock()


ROTATION_AI_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rotation-ai")


ROTATION_ALERT_STATE_LOCK = threading.Lock()


CHAIN_ECOSYSTEM_AI_LOCK = threading.Lock()


CHAIN_ECOSYSTEM_AI_INFLIGHT: set[str] = set()


CHAIN_ECOSYSTEM_AI_RETRY_AFTER: dict[str, float] = {}


CHAIN_ECOSYSTEM_AI_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chain-ecosystem-ai")


BINANCE_AI_NARRATIVE_POOL = ThreadPoolExecutor(
    max_workers=max(2, min(6, int(os.getenv("BINANCE_AI_NARRATIVE_WORKERS", "3") or "3"))),
    thread_name_prefix="binance-ai-narrative",
)


EXCHANGE_AI_TRENCH_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="exchange-ai-trench")


AVE_CHAIN_POOL = ThreadPoolExecutor(
    max_workers=max(2, min(5, int(os.getenv("AVE_CHAIN_WORKERS", "3") or "3"))),
    thread_name_prefix="ave-chain-hot",
)


NEWS_TRADE_ALERT_STATE_LOCK = threading.Lock()


NEWS_TRADE_DESKTOP_INTAKE_LOCK = threading.Lock()


ASTER_ANNOUNCEMENT_LOCK = threading.Lock()


ASTER_X_LISTING_LOCK = threading.Lock()


API_REFRESH_LOCK = threading.Lock()


# key -> wall-clock start of the one in-flight refresh worker.  Python cannot
# safely kill a blocked thread.  Starting a "replacement" only leaks the old
# thread and its stack, so every cache key stays strict single-flight until its
# owner actually exits.
API_REFRESHING: dict[str, float] = {}


API_REFRESH_STALL_LOGGED: set[str] = set()


API_REFRESH_STUCK_SECONDS = max(
    60.0,
    float(os.getenv("API_REFRESH_STUCK_SECONDS", "300") or 300),
)


API_REFRESH_POOL = ThreadPoolExecutor(
    max_workers=max(2, min(8, int(float(os.getenv("API_REFRESH_WORKERS", "4") or "4")))),
    thread_name_prefix="api-refresh",
)


API_CACHE_WRITE_LOCK = threading.Lock()


DESKTOP_ALERT_LOCK = threading.Lock()


DESKTOP_ALERT_SEEN: dict[str, float] = {}


DESKTOP_ALERT_SLOT = 0


DESKTOP_ALERT_TTL = 90 * 24 * 60 * 60


DESKTOP_ALERT_SEEN_LIMIT = 20000


DESKTOP_ALERT_QUEUE = deque()


DESKTOP_ALERT_DELIVERY_TICK_LOCK = threading.Lock()


DESKTOP_ALERT_MIN_INTERVAL_SECONDS = 10


DESKTOP_ALERT_URGENT_INTERVAL_SECONDS = 4


DESKTOP_ALERT_CRITICAL_PRIORITY = 900


DESKTOP_ALERT_CRITICAL_INTERVAL_SECONDS = max(
    4.0,
    float(os.getenv("DESKTOP_ALERT_CRITICAL_INTERVAL_SECONDS", "6.5") or "6.5"),
)


DESKTOP_ALERT_CRITICAL_MAX_QUEUE_AGE_SECONDS = max(
    DESKTOP_ALERT_CRITICAL_INTERVAL_SECONDS,
    float(os.getenv("DESKTOP_ALERT_CRITICAL_MAX_QUEUE_AGE_SECONDS", "45") or "45"),
)


DESKTOP_ALERT_TRADING_SIGNAL_PRIORITY = 1000


DESKTOP_ALERT_TRADING_PREARM_PRIORITY = 999


DESKTOP_ALERT_QUEUE_LIMIT = 80


DESKTOP_ALERT_STRUCTURE_PROCESSES: dict[int, tuple[Any, float]] = {}


DESKTOP_ALERT_GENERAL_PROCESSES: dict[int, tuple[Any, float]] = {}


DESKTOP_ALERT_MAX_CONCURRENT_SLOTS = max(
    2,
    min(16, int(float(os.getenv("DESKTOP_ALERT_MAX_CONCURRENT_SLOTS", "8") or "8"))),
)


DESKTOP_ALERT_STRUCTURE_AUTO_CLOSE_MS = max(
    45 * 1000,
    int(float(os.getenv("DESKTOP_ALERT_STRUCTURE_AUTO_CLOSE_SECONDS", "120") or "120") * 1000),
)


DESKTOP_ALERT_PRICE_WATCH_AUTO_CLOSE_MS = max(
    DESKTOP_ALERT_STRUCTURE_AUTO_CLOSE_MS,
    int(float(os.getenv("DESKTOP_ALERT_PRICE_WATCH_AUTO_CLOSE_SECONDS", "120") or "120") * 1000),
)


DESKTOP_ALERT_RECLAIM_VISIBLE_MS = max(
    60 * 1000,
    int(float(os.getenv("DESKTOP_ALERT_RECLAIM_SECONDS", "60") or "60") * 1000),
)


DESKTOP_ALERT_QUEUE_WAKE = threading.Event()


DESKTOP_ALERT_MILITARY_PATTERN = re.compile(
    r"(?:军事|军方|军队|美军|俄军|乌军|以军|伊军|英军|法军|日军|韩军|朝鲜军|解放军|"
    r"海军|空军|陆军|火箭军|驻军|部队|士兵|军舰|战舰|航母|驱逐舰|护卫舰|潜艇|"
    r"战机|轰炸机|舰队|军事基地|战区|战争|战事|开战|武装冲突|军事冲突|交火|停火|空袭|炮击|"
    r"导弹|无人机袭击|军演|核武|核打击|入侵|撤军|增兵|伤亡|阵亡|战俘|国防部|"
    r"五角大楼|领空|领海|边境冲突|封锁海峡|封锁领空|封锁领海|"
    r"\bmilitary\b|\bwar(?:fare)?\b|\barmed conflict\b|\bairstrikes?\b|"
    r"\bmissiles?\b|\bdrone attacks?\b|\bceasefires?\b|\btroops?\b|"
    r"\binvasion\b|\bwithdrawal of forces\b|\bdefen[cs]e ministry\b|"
    r"\bpentagon\b|\bnuclear weapons?\b)",
    re.I,
)


DESKTOP_ALERT_POLITICAL_PATTERN = re.compile(
    r"(?:地缘政治|政治局势|政局|政治危机|国际局势|选举|大选|竞选|政党|弹劾|政变|"
    r"政府停摆|政府倒台|内阁倒台|外交危机|外交关系|外交争端|示威|抗议活动|"
    r"贸易战|关税战|关税争端|国际制裁|经济制裁|"
    r"\bgeopolitic(?:al|s)?\b|\bpolitical (?:crisis|tensions?|party|parties)\b|"
    r"\belections?\b|\belection campaign\b|\bimpeachment\b|\bcoup\b|"
    r"\bgovernment shutdown\b|\bcabinet collapse\b|\bdiplomatic (?:crisis|tensions?|relations?)\b|"
    r"\bprotests?\b)",
    re.I,
)


DESKTOP_ALERT_POLITICAL_ACTOR_PATTERN = re.compile(
    r"(?:(?:总统|首相|总理|国家元首|政府|内阁).{0,16}(?:会晤|会见|通话|访问|辞职|任命|"
    r"支持率|倒台|停摆|组阁)|(?:会晤|会见|通话|访问|辞职|任命|组阁).{0,16}"
    r"(?:总统|首相|总理|国家元首|政府|内阁)|"
    r"\b(?:president|prime minister|head of state|cabinet)\b.{0,40}"
    r"\b(?:meets?|meeting|talks?|visits?|resigns?|appoints?|approval rating|coalition)\b)",
    re.I,
)


DESKTOP_ALERT_POLITICAL_FIGURE_PATTERN = re.compile(
    r"(?:(?:特朗普|川普|普京|泽连斯基|内塔尼亚胡|哈梅内伊|拜登|马克龙|金正恩).{0,24}"
    r"(?:会晤|会见|通话|访问|竞选|大选|制裁|关税|外交|峰会|会谈|停火|战争|军事)|"
    r"(?:会晤|会见|通话|访问|竞选|大选|制裁|关税|外交|峰会|会谈|停火|战争|军事).{0,24}"
    r"(?:特朗普|川普|普京|泽连斯基|内塔尼亚胡|哈梅内伊|拜登|马克龙|金正恩)|"
    r"\b(?:trump|putin|zelensky|netanyahu|khamenei|biden|macron|kim jong un)\b.{0,60}"
    r"\b(?:meeting|talks?|visit|election|campaign|sanctions?|tariffs?|diplomatic|summit|ceasefire|war|military)\b)",
    re.I,
)


DESKTOP_ALERT_GEOPOLITICAL_ACTOR_PATTERN = re.compile(
    r"(?:(?:俄乌|乌克兰|俄罗斯|以色列|伊朗|加沙|巴勒斯坦|黎巴嫩|叙利亚|朝鲜|台海|"
    r"红海|北约).{0,20}(?:局势|冲突|袭击|制裁|会谈|停火|战争|军事)|"
    r"(?:局势|冲突|袭击|制裁|会谈|停火|战争|军事).{0,20}"
    r"(?:俄乌|乌克兰|俄罗斯|以色列|伊朗|加沙|巴勒斯坦|黎巴嫩|叙利亚|朝鲜|台海|"
    r"红海|北约)|"
    r"\b(?:russia|ukraine|israel|iran|gaza|palestine|lebanon|syria|north korea|"
    r"taiwan strait|red sea|nato)\b.{0,60}"
    r"\b(?:conflict|attack|sanctions?|talks?|ceasefire|war|military|tensions?)\b)",
    re.I,
)


DESKTOP_ALERT_WHALE_PNL_PATTERN = re.compile(
    r"(?:(?:巨鲸|鲸鱼|大户|聪明钱|某地址|某账户).{0,80}"
    r"(?:赚(?:取|得)?|获利|盈利|浮盈|亏损|浮亏|止盈|止损|爆仓|清算|实现亏损|累计亏损)|"
    r"(?:赚(?:取|得)?|获利|盈利|浮盈|亏损|浮亏|止盈|止损|爆仓|清算|实现亏损|累计亏损).{0,80}"
    r"(?:巨鲸|鲸鱼|大户|聪明钱|某地址|某账户)|"
    r"\b(?:whale|large holder|smart money|wallet|address)\b.{0,120}"
    r"\b(?:profit|loss|pnl|stop[- ]?loss|take[- ]?profit|liquidat(?:ed|ion))\b|"
    r"\b(?:profit|loss|pnl|stop[- ]?loss|take[- ]?profit|liquidat(?:ed|ion))\b.{0,120}"
    r"\b(?:whale|large holder|smart money|wallet|address)\b)",
    re.I,
)


DESKTOP_ALERT_PERSONAL_PNL_ACTOR = (
    r"(?:交易者|交易员|投资者|散户|用户|持仓者|专业户|钻石手|赢家|"
    r"盈利榜(?:一|前排|前列)?|获利榜(?:一|前排|前列)?|KOL|大V|博主|顾问|开发者|"
    r"某(?:人|用户|交易者|交易员|投资者|散户|账户|地址|钱包|实体)|"
    r"关联钱包|团队钱包|地址|钱包|账户|"
    r"0x[0-9a-f…\.]{4,}|[a-z0-9_.-]+\.sol(?![a-z0-9_.-])|"
    r"\b(?:trader|investor|user|holder|wallet|address|kol|influencer|insider|winner)\b)"
)


DESKTOP_ALERT_PERSONAL_PNL_RESULT = (
    r"(?:赚(?:取|得|了|到)?|斩获|获利|盈利|浮盈|亏损|浮亏|止盈|止损|"
    r"爆仓|清算|实现亏损|累计亏损|回报率(?:达|为|超|约)?|晒战绩|战绩|"
    r"\b(?:made|earned|netted|realized|unrealized|profit|loss|pnl)\b)"
)


DESKTOP_ALERT_PERSONAL_PNL_PATTERN = re.compile(
    rf"(?:{DESKTOP_ALERT_PERSONAL_PNL_ACTOR}.{{0,320}}{DESKTOP_ALERT_PERSONAL_PNL_RESULT}|"
    rf"{DESKTOP_ALERT_PERSONAL_PNL_RESULT}.{{0,320}}{DESKTOP_ALERT_PERSONAL_PNL_ACTOR})",
    re.I | re.S,
)


DESKTOP_ALERT_POSITION_CHANGE_PATTERN = re.compile(
    r"(?:"
    r"(?:增持|减持|加仓|减仓|建仓|清仓|调仓)|"
    r"(?:总)?持仓(?:量)?(?:已)?(?:升至|增至|降至|减至|增加|减少|上升|下降|变化|变动)|"
    r"持有量(?:已)?(?:升至|增至|降至|减至|增加|减少|上升|下降|变化|变动)|"
    r"(?:(?:巨鲸|鲸鱼|大户|聪明钱|某地址|某账户|某钱包|机构|基金|资管|上市公司|矿企|矿商).{0,80}"
    r"(?:买入|卖出|购入|出售|抛售|扫货|囤积)|"
    r"(?:买入|卖出|购入|出售|抛售|扫货|囤积).{0,80}"
    r"(?:巨鲸|鲸鱼|大户|聪明钱|某地址|某账户|某钱包|机构|基金|资管|上市公司|矿企|矿商))|"
    r"\b(?:whale|large holder|smart money|wallet|address|institution|fund|company|firm|miner)\b.{0,120}"
    r"\b(?:bought|sold|buying|selling|accumulat(?:e|ed|ing)|dump(?:ed|ing)|trim(?:s|med|ming))\b|"
    r"\b(?:increas(?:e|ed|ing)|reduc(?:e|ed|ing)|decreas(?:e|ed|ing)|cut|cuts|trim(?:s|med|ming))\b"
    r".{0,40}\b(?:holdings?|positions?|treasury|reserves?)\b"
    r")",
    re.I,
)


WECHAT_GROUP_MONITOR_LOCK = threading.Lock()


CHAT_HOURLY_SUMMARY_LOCK = threading.Lock()


WECHAT_GROUP_ANALYSIS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wechat-opportunity")


WECHAT_GROUP_AI_BACKFILL_LOCK = threading.Lock()


WECHAT_GROUP_AI_BACKFILL_INFLIGHT: set[int] = set()


WECHAT_GROUP_OPPORTUNITY_DEDUP_SECONDS = 48 * 60 * 60


DEFAULT_QQ_GROUP_NAME = "地表最强bsc eth"


DEFAULT_QQ_SENDER_FILTER = "鲸鱼🐳PP"


DEFAULT_QQ_WECHAT_FORWARD_TARGET = "文件传输助手"


CHAT_FORWARD_RETRY_SECONDS = (15, 60, 5 * 60, 15 * 60, 60 * 60)


CHAT_FORWARD_LOCK = threading.Lock()


CHAT_CONTRACT_MARKET_CACHE_LOCK = threading.Lock()


CHAT_CONTRACT_MARKET_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


SITE_ALERT_LOCK = threading.Lock()


SITE_ALERT_SEEN_LIMIT = 10000


DEEPSEEK_INSIGHTS_LOCK = threading.Lock()


CODEX_CLI_FALLBACK_LOCK = threading.BoundedSemaphore(1)


CODEX_CLI_FAST_ANALYSIS_LOCK = threading.BoundedSemaphore(1)


CODEX_CLI_CHAT_CA_LOCK = threading.BoundedSemaphore(2)


CODEX_CLI_WALLET_ANALYSIS_LOCK = threading.BoundedSemaphore(1)


CODEX_CLI_ONCHAIN_LIVE_LOCK = threading.BoundedSemaphore(2)


CODEX_CLI_ONCHAIN_HISTORY_LOCK = threading.BoundedSemaphore(1)


CODEX_CLI_ONCHAIN_HOURLY_LOCK = threading.BoundedSemaphore(5)


CODEX_CLI_EXPLANATION_SEARCH_LOCK = threading.BoundedSemaphore(1)


CODEX_CLI_EXPLANATION_REVIEW_LOCK = threading.BoundedSemaphore(1)


CODEX_CLI_GLOBAL_HOTSPOT_LOCK = threading.BoundedSemaphore(1)


CODEX_CLI_STATE_LOCK = threading.Lock()


AI_STARTUP_RECONNECT_LOCK = threading.Lock()


AI_STARTUP_RECONNECT_STATE: dict[str, Any] = {
    "status": "waiting",
    "provider": "",
    "attemptedAt": 0,
    "completedAt": 0,
    "requeued": 0,
    "error": "",
}


SELF_OPTIMIZATION_STATE_LOCK = threading.Lock()


SELF_OPTIMIZATION_CHECK_LOCK = threading.Lock()


SELF_OPTIMIZATION_EXECUTION_LOCK = threading.Lock()


LLM_API_STATE_LOCK = threading.Lock()


X_KOL_TRANSLATION_LOCK = threading.Lock()


X_KOL_REALTIME_CONDITION = threading.Condition()


X_KOL_REALTIME_SNAPSHOTS: dict[int, dict[str, Any]] = {}


X_KOL_REALTIME_DISK_HYDRATED: set[int] = set()


X_KOL_REALTIME_WORKERS: set[int] = set()


X_KOL_REALTIME_WAKE_EVENTS: dict[int, threading.Event] = {}


X_KOL_REALTIME_RSS_INTERVAL_SECONDS = max(2.0, float(os.getenv("X_KOL_REALTIME_RSS_INTERVAL_SECONDS", "3") or "3"))


X_KOL_REALTIME_API_INTERVAL_SECONDS = max(5.0, float(os.getenv("X_KOL_REALTIME_API_INTERVAL_SECONDS", "15") or "15"))


X_KOL_RSS_TIMEOUT_SECONDS = max(2.0, float(os.getenv("X_KOL_RSS_TIMEOUT_SECONDS", "4") or "4"))


# 个人 X 监控 · 未来事件自动入 todolist 的去重与并发保护
FUTURE_EVENT_LOCK = threading.Lock()


FUTURE_EVENT_SEEN: set[str] = set()


X_KOL_RSS_MIRROR_WORKERS = max(1, min(8, int(os.getenv("X_KOL_RSS_MIRROR_WORKERS", "3") or "3")))


X_KOL_RSS_SOURCE_WORKERS = max(1, min(6, int(os.getenv("X_KOL_RSS_SOURCE_WORKERS", "3") or "3")))


X_KOL_RSS_MIRROR_ATTEMPTS = max(1, min(4, int(os.getenv("X_KOL_RSS_MIRROR_ATTEMPTS", "2") or "2")))


X_KOL_RSS_USER_AGENT = str(os.getenv(
    "X_KOL_RSS_USER_AGENT",
    "Mozilla/5.0 (compatible; Miniflux/2.1.3; +https://miniflux.app)",
) or "").strip()


X_KOL_PUBLIC_TIMELINE_TIMEOUT_SECONDS = max(
    3.0,
    float(os.getenv("X_KOL_PUBLIC_TIMELINE_TIMEOUT_SECONDS", "12") or "12"),
)


X_KOL_FXTWITTER_BASE_URL = str(
    os.getenv("X_KOL_FXTWITTER_BASE_URL", "https://api.fxtwitter.com") or ""
).strip().rstrip("/")


X_KOL_FXTWITTER_TIMEOUT_SECONDS = max(
    3.0,
    float(os.getenv("X_KOL_FXTWITTER_TIMEOUT_SECONDS", "12") or "12"),
)


X_KOL_HTTP_ROUTE_LOCK = threading.Lock()


X_KOL_HTTP_ROUTE: dict[str, Any] = {
    "resolved": False,
    "proxyUrl": "",
    "expiresAt": 0.0,
}


X_KOL_HTTP_ROUTE_TTL_SECONDS = max(
    30.0,
    float(os.getenv("X_KOL_HTTP_ROUTE_TTL_SECONDS", "600") or "600"),
)


X_KOL_HTTP_THREAD_LOCAL = threading.local()


GMGN_TRENCH_X_POST_CACHE_TTL_SECONDS = max(
    60.0,
    float(os.getenv("GMGN_TRENCH_X_POST_CACHE_TTL_SECONDS", str(24 * 60 * 60)) or str(24 * 60 * 60)),
)


GMGN_TRENCH_X_POST_NEGATIVE_TTL_SECONDS = max(
    60.0,
    float(os.getenv("GMGN_TRENCH_X_POST_NEGATIVE_TTL_SECONDS", "600") or "600"),
)


GMGN_TRENCH_X_POST_MIN_INTERVAL_SECONDS = max(
    0.25,
    float(os.getenv("GMGN_TRENCH_X_POST_MIN_INTERVAL_SECONDS", "0.35") or "0.35"),
)


GMGN_TRENCH_X_POST_CACHE_LOCK = threading.Lock()


GMGN_TRENCH_X_POST_REQUEST_LOCK = threading.Lock()


GMGN_TRENCH_X_POST_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


X_KOL_RSS_CIRCUIT_BASE_SECONDS = max(5.0, float(os.getenv("X_KOL_RSS_CIRCUIT_BASE_SECONDS", "15") or "15"))


X_KOL_RSS_CIRCUIT_MAX_SECONDS = max(
    X_KOL_RSS_CIRCUIT_BASE_SECONDS,
    float(os.getenv("X_KOL_RSS_CIRCUIT_MAX_SECONDS", "300") or "300"),
)


X_KOL_RSS_PREFERENCE_LOCK = threading.Lock()


X_KOL_RSS_PREFERRED_TEMPLATES: dict[str, str] = {}


X_KOL_RSS_MIRROR_HEALTH_LOCK = threading.Lock()


X_KOL_RSS_MIRROR_HEALTH: dict[str, dict[str, Any]] = {}


X_KOL_OFFICIAL_STREAM_LOCK = threading.Lock()


X_KOL_API_COST_LOCK = threading.RLock()


X_KOL_OFFICIAL_STREAM_USERS: dict[int, dict[str, Any]] = {}


X_KOL_OFFICIAL_STREAM_WAKE = threading.Event()


X_KOL_OFFICIAL_STREAM_HEALTH: dict[str, Any] = {
    "connected": False,
    "rulesReady": False,
    "targetCount": 0,
    "lastConnectedAt": 0,
    "lastEventAt": 0,
    "lastErrorAt": 0,
    "lastError": "",
    "updatedAt": 0,
}


X_KOL_PRIORITY_RSS_INTERVAL_SECONDS = max(
    0.75,
    float(os.getenv("X_KOL_PRIORITY_RSS_INTERVAL_SECONDS", "1") or "1"),
)


X_KOL_PRIORITY_API_INTERVAL_SECONDS = max(
    5.0,
    float(os.getenv("X_KOL_PRIORITY_API_INTERVAL_SECONDS", "15") or "15"),
)


X_KOL_PRIORITY_RSS_TIMEOUT_SECONDS = max(
    1.0,
    float(os.getenv("X_KOL_PRIORITY_RSS_TIMEOUT_SECONDS", "2") or "2"),
)


X_KOL_PRIORITY_RSS_MIRRORS = max(
    1,
    min(4, int(os.getenv("X_KOL_PRIORITY_RSS_MIRRORS", "3") or "3")),
)


X_KOL_PRIORITY_LOCK = threading.Lock()


X_KOL_PRIORITY_WAKE = threading.Event()


TRENCH_PERSON_SIGNAL_LOCK = threading.RLock()


TRENCH_PERSON_WATCH_LOCK = threading.Lock()


TRENCH_PERSON_SEMANTIC_LOCK = threading.RLock()


TRENCH_PERSON_WATCH_NEXT_DUE: dict[str, float] = {}


TRENCH_PERSON_WATCH_FAILURES: dict[str, int] = {}


TRENCH_PERSON_LAST_ALERT_AT: dict[str, int] = {}


TRENCH_PERSON_ALERT_NOT_BEFORE_MS = int(time.time() * 1000)


TRENCH_PERSON_ALERT_MAX_AGE_MS = max(
    60_000,
    int(float(os.getenv("TRENCH_PERSON_ALERT_MAX_AGE_SECONDS", "600") or "600") * 1000),
)


TRENCH_PERSON_ALERT_PERSON_COOLDOWN_MS = max(
    60_000,
    int(float(os.getenv("TRENCH_PERSON_ALERT_PERSON_COOLDOWN_SECONDS", "600") or "600") * 1000),
)


TRENCH_PERSON_REQUEST_MIN_INTERVAL_SECONDS = max(
    2.0,
    float(os.getenv("TRENCH_PERSON_REQUEST_MIN_INTERVAL_SECONDS", "3") or "3"),
)


TRENCH_PERSON_BATCH_SIZE = max(
    1,
    min(8, int(os.getenv("TRENCH_PERSON_BATCH_SIZE", "6") or "6")),
)


TRENCH_PERSON_FETCH_WORKERS = max(
    1,
    min(8, int(os.getenv("TRENCH_PERSON_FETCH_WORKERS", str(TRENCH_PERSON_BATCH_SIZE)) or TRENCH_PERSON_BATCH_SIZE)),
)


TRENCH_PERSON_BATCH_TIMEOUT_SECONDS = max(
    8.0,
    float(os.getenv("TRENCH_PERSON_BATCH_TIMEOUT_SECONDS", "25") or "25"),
)


TRENCH_PERSON_SOURCE_LIMIT = max(
    80,
    min(500, int(os.getenv("TRENCH_PERSON_SOURCE_LIMIT", "300") or "300")),
)


TRENCH_PERSON_DEFAULT_REFRESH_SECONDS = max(
    30.0,
    float(os.getenv("TRENCH_PERSON_DEFAULT_REFRESH_SECONDS", "75") or "75"),
)


TRENCH_PERSON_SECONDARY_REFRESH_SECONDS = max(
    5 * 60.0,
    float(os.getenv("TRENCH_PERSON_SECONDARY_REFRESH_SECONDS", "600") or "600"),
)


TRENCH_PERSON_OFFICIAL_REFRESH_SECONDS = max(
    2 * 60.0,
    float(os.getenv("TRENCH_PERSON_OFFICIAL_REFRESH_SECONDS", "300") or "300"),
)


TRENCH_PERSON_RELEVANT_REFRESH_SECONDS = max(
    20.0,
    float(os.getenv("TRENCH_PERSON_RELEVANT_REFRESH_SECONDS", "30") or "30"),
)


TRENCH_PERSON_POST_MAX_AGE_MS = max(
    60 * 60_000,
    int(float(os.getenv("TRENCH_PERSON_POST_MAX_AGE_SECONDS", "21600") or "21600") * 1000),
)


TRENCH_PERSON_TOKEN_LOOKBACK_MS = max(
    6 * 60 * 60_000,
    int(float(os.getenv("TRENCH_PERSON_TOKEN_LOOKBACK_HOURS", "72") or "72") * 60 * 60_000),
)


TRENCH_PERSON_SEMANTIC_CACHE_TTL_MS = max(
    60 * 60_000,
    int(float(os.getenv("TRENCH_PERSON_SEMANTIC_CACHE_HOURS", "168") or "168") * 60 * 60_000),
)


TRENCH_PERSON_SEMANTIC_RETRY_MS = max(
    30_000,
    int(float(os.getenv("TRENCH_PERSON_SEMANTIC_RETRY_SECONDS", "120") or "120") * 1000),
)


TRENCH_PERSON_SEMANTIC_MIN_CONFIDENCE = max(
    0.5,
    min(0.99, float(os.getenv("TRENCH_PERSON_SEMANTIC_MIN_CONFIDENCE", "0.72") or "0.72")),
)


AICOIN_CDP_RELAUNCH_LOCK = threading.Lock()


SITE_ALERT_FUTURE_TOLERANCE_MS = 10 * 60 * 1000


RANK_MONITOR_LOCK = threading.Lock()


RANK_MONITOR_INTERVAL = 30


RANK_MONITOR_COOLDOWN_SECONDS = 20 * 60


RANK_MONITOR_MAX_EVENTS_PER_RUN = 1


RANK_MONITOR_HOT_WATCH_RANK = 10


RANK_MONITOR_STOCK_HOT_WATCH_RANK = 10


RANK_MONITOR_TURNOVER_WATCH_LIMIT = 20


RANK_MONITOR_SILENT_HOT_NEW_SOURCES = {"binance", "okx", "bitget", "aicoin"}


RANK_BROADCAST_MUTED_SOURCE_IDS = {"gmgn-hot-search"}


RANK_MONITOR_SILENT_HOT_NEW_EXCHANGE_MARKERS = (
    "binance", "币安", "okx", "欧易", "bitget", "gate", "htx",
    "kucoin", "bybit", "aster", "hyperliquid", "weex", "aicoin",
)


ONCHAIN_RANK_DESKTOP_ALERT_SOURCE_IDS = {
    "okx-dex",
    "okx-dex-gainers",
    "binance-wallet-hot",
    "ave",
    "gmgn-trenches",
    "gmgn-hot-search",
}


SECONDARY_RANK_LEADER_ALLOWED_SOURCE_IDS = {
    "binance",
    "binance-gainers",
    "binance-futures-gainers",
    "okx",
    "okx-gainers",
    "okx-turnover",
}


ONCHAIN_RANK_SOURCE_PATTERN = re.compile(
    r"(?:\bDEX\b|链上|币安钱包|Binance\s+Wallet|AVE(?:\.ai)?|GMGN)",
    re.I,
)


RANK_LEADER_ALERT_PATTERN = re.compile(
    r"(?:榜首|第一名|首位|排名\s*#?\s*1(?:\D|$)|\brank\s*#?\s*1\b|\btop\s*1\b)",
    re.I,
)


STOCK_RANK_DESKTOP_ALERT_SOURCE_IDS = {
    "futu-hk",
    "futu-us",
    "ths-cn",
    "futu-hk-gainers",
    "futu-us-gainers",
    "cn-stock-gainers",
    "futu-hk-turnover",
    "futu-us-turnover",
    "ths",
}


RANK_MONITOR_SKIP_UPDATE_SOURCES = {
    "okx-dex",
    "okx-dex-gainers",
    "binance-wallet-hot",
    "ave",
    "gmgn-hot-search",
} | STOCK_RANK_DESKTOP_ALERT_SOURCE_IDS


RANK_MONITOR_STATE_VERSION = 3


BINANCE_WALLET_HOT_ALERT_STATE_VERSION = 2


BINANCE_WALLET_HOT_REENTRY_SECONDS = 7 * 86400


AVE_HOT_ALERT_STATE_VERSION = 1


AVE_HOT_PERIODS = ("1h", "4h", "24h")


AVE_HOT_DEFAULT_PERIOD = "4h"


AVE_HOT_CHAINS = (
    ("robinhood", "Robinhood"),
    ("solana", "SOL"),
    ("eth", "ETH"),
    ("bsc", "BSC"),
    ("base", "Base"),
)


GMGN_HOT_PERIODS = ("1m", "5m", "1h", "6h", "24h")


GMGN_HOT_DEFAULT_PERIOD = "1h"


GMGN_HOT_CHAINS = (
    ("sol", "SOL"),
    ("bsc", "BSC"),
    ("base", "Base"),
    ("eth", "ETH"),
    ("robinhood", "Robinhood"),
    ("arc", "ARC"),
    ("stable", "Stable"),
)


GMGN_OPENAPI_BASE = "https://openapi.gmgn.ai"


# GMGN requests are routed through gmgn_agentic's public-read-only request
# manager. The dashboard never consumes the personal key configured for CLI.
BINANCE_WALLET_HOT_ALERT_INTERVAL_SECONDS = max(
    15,
    int(float(os.getenv("BINANCE_WALLET_HOT_ALERT_INTERVAL_SECONDS", "30") or "30")),
)


AVE_HOT_ALERT_INTERVAL_SECONDS = max(
    15,
    int(float(os.getenv("AVE_HOT_ALERT_INTERVAL_SECONDS", "30") or "30")),
)


GLOBAL_HOTSPOT_INTERVAL_SECONDS = max(
    3600,
    int(float(os.getenv("GLOBAL_HOTSPOT_INTERVAL_SECONDS", "3600") or "3600")),
)


GLOBAL_HOTSPOT_DAILY_MAX_BATCHES = min(
    24,
    max(1, int(float(os.getenv("GLOBAL_HOTSPOT_DAILY_MAX_BATCHES", "24") or "24"))),
)


BINANCE_WALLET_HOT_ALERT_LOCK = threading.Lock()


FIRST_LISTING_ALERT_LOCK = threading.Lock()


AVE_HOT_ALERT_LOCK = threading.Lock()


GLOBAL_HOTSPOT_LOCK = threading.Lock()


GMGN_TRENCH_HISTORY_LOCK = threading.Lock()


ONCHAIN_RESEARCH_INGEST_LOCK = threading.Lock()


ONCHAIN_RESEARCH_INGEST_PENDING: dict[str, tuple[dict[str, Any], bool]] = {}


ONCHAIN_RESEARCH_INGEST_PENDING_LIMIT = max(
    300,
    min(3000, int(float(os.getenv("ONCHAIN_RESEARCH_INGEST_PENDING_LIMIT", "1200") or "1200"))),
)


TRENCH_PERSON_REPLAY_LOCK = threading.Lock()


TRENCH_PERSON_ACTIVITY_LOCK = threading.Lock()


TRENCH_PERSON_ACTIVITY_PENDING: dict[str, dict[str, Any]] = {}


TRENCH_PERSON_ACTIVITY_PENDING_LIMIT = 320


GMGN_TRENCH_BOARD_REFRESH_SECONDS = max(
    120,
    int(float(os.getenv("GMGN_TRENCH_BOARD_REFRESH_SECONDS", "180") or "180")),
)


GMGN_TRENCH_HISTORY_RETENTION_MS = max(
    24 * 60 * 60 * 1000,
    int(float(os.getenv("GMGN_TRENCH_HISTORY_RETENTION_DAYS", "30") or "30")) * 24 * 60 * 60 * 1000,
)


GMGN_TRENCH_HISTORY_MAX_ROWS = max(
    100,
    min(5000, int(float(os.getenv("GMGN_TRENCH_HISTORY_MAX_ROWS", "2000") or "2000"))),
)


GMGN_TRENCH_RESPONSE_MAX_ROWS = max(
    50,
    min(500, int(float(os.getenv("GMGN_TRENCH_RESPONSE_MAX_ROWS", "300") or "300"))),
)


MARKET_PRIORITY_WINDOWS = {"1h": 60 * 60, "6h": 6 * 60 * 60, "24h": 24 * 60 * 60}


MARKET_PRIORITY_HISTORY_SECONDS = 25 * 60 * 60


MARKET_PRIORITY_SNAPSHOT_INTERVAL_SECONDS = 5 * 60


PRICE_WATCH_WINDOW_SECONDS = 7 * 24 * 60 * 60


PRICE_WATCH_RETENTION_SECONDS = 30 * 24 * 60 * 60


BINANCE_WALLET_4H_STRUCTURE_RETENTION_SECONDS = PRICE_WATCH_RETENTION_SECONDS


# GMGN's 5-minute hot search is a fast discovery board: a token surfacing there
# is worth watching right away, but the board churns and most entries never
# graduate.  A GMGN-only discovery therefore keeps its monitor slot for a short
# probation window, and the clock follows the *latest* GMGN appearance -- a token
# that keeps re-entering the board keeps refreshing its slot, while one that
# falls off can still return later.  The only way to outlive the probation is to
# graduate to a stronger feed: the Binance Wallet 4h hot ranking keeps its own
# 30-day lifecycle (BINANCE_WALLET_4H_STRUCTURE_RETENTION_SECONDS).
GMGN_HOT_SEARCH_POOL_RETENTION_SECONDS = max(
    24 * 60 * 60,
    int(float(os.getenv("GMGN_HOT_SEARCH_POOL_RETENTION_DAYS", "3") or "3")) * 24 * 60 * 60,
)


BINANCE_WALLET_4H_STRUCTURE_SYNC_SECONDS = 5 * 60


# A Binance/OKX gainer gets a short discovery window.  It graduates into the
# normal 30-day lifecycle only after AiCoin also observes it.
GAINERS_MONITOR_PROMOTION_SECONDS = 3 * 24 * 60 * 60


# Discovery history remains available for 30 days, but a historical ranking
# must not reserve high-frequency monitoring capacity for that entire window.
# Fresh source evidence gets a short grace window; ordinary retirement needs
# two daily confirmations so transient provider failures cannot churn the pool.
PRICE_MONITOR_FRESH_SOURCE_SECONDS = 3 * 24 * 60 * 60


PRICE_MONITOR_STALE_SOURCE_SECONDS = 7 * 24 * 60 * 60


PRICE_MONITOR_RETENTION_CONFIRMATIONS = 2


PRICE_MONITOR_RETENTION_CONFIRM_INTERVAL_SECONDS = 24 * 60 * 60


PRICE_MONITOR_RETENTION_MIN_STRUCTURE_CONFIDENCE = 60.0


PRICE_MONITOR_RETENTION_DEEP_DRAWDOWN_PCT = 70.0


PRICE_MONITOR_ACTIVITY_FALLBACK_SECONDS = 24 * 60 * 60


# A technical shape is useful as a short bridge, not as permanent proof that
# the market still cares.  This prevents an old prior high or structure label
# from keeping a dead symbol in every fast monitor indefinitely.
PRICE_MONITOR_TECHNICAL_EDGE_GRACE_SECONDS = 24 * 60 * 60


# Ordinary automatic retirement is deliberately gradual.  Invalid identities
# may leave immediately, while attention/turnover decay is capped per local day
# so a temporary provider outage cannot empty the monitor pool in one sweep.
PRICE_MONITOR_DAILY_RETIREMENT_FRACTION = min(
    0.30,
    max(0.01, float(os.getenv("PRICE_MONITOR_DAILY_RETIREMENT_FRACTION", "0.15") or "0.15")),
)


# Ranking feeds churn much faster than the 30-day discovery history.  A row
# that has left one of those boards and has no tactical edge must not reserve
# high-frequency quote capacity for days merely because it was once ranked.
PRICE_MONITOR_SOURCE_GRACE_SECONDS = {
    "aicoin": 24 * 60 * 60,
    "ave": 2 * 60 * 60,
    "binance-wallet-4h": 6 * 60 * 60,
    "gmgn-hot-search-5m": GMGN_HOT_SEARCH_POOL_RETENTION_SECONDS,
    "gainers": 12 * 60 * 60,
    "personal-x": 3 * 24 * 60 * 60,
    "new-contract": 3 * 24 * 60 * 60,
    "opportunity": 3 * 24 * 60 * 60,
}


PRICE_MONITOR_FAST_RETENTION_CONFIRM_INTERVAL_SECONDS = 6 * 60 * 60


PRICE_STRUCTURE_REENTRY_ABSENT_MIN_SECONDS = max(
    5 * 60,
    int(float(os.getenv("PRICE_STRUCTURE_REENTRY_ABSENT_MIN_SECONDS", "1800") or "1800")),
)


PRICE_STRUCTURE_REENTRY_ABSENT_CONFIRMATIONS = max(
    2,
    int(float(os.getenv("PRICE_STRUCTURE_REENTRY_ABSENT_CONFIRMATIONS", "3") or "3")),
)


PRICE_STRUCTURE_REENTRY_CONFIRM_INTERVAL_SECONDS = max(
    30,
    int(float(os.getenv("PRICE_STRUCTURE_REENTRY_CONFIRM_INTERVAL_SECONDS", "60") or "60")),
)


PRICE_STRUCTURE_REENTRY_RECENCY_SECONDS = max(
    PRICE_STRUCTURE_REENTRY_CONFIRM_INTERVAL_SECONDS * 2,
    int(float(os.getenv("PRICE_STRUCTURE_REENTRY_RECENCY_SECONDS", "600") or "600")),
)


PERSONAL_X_MONITOR_RETENTION_SECONDS = PRICE_WATCH_RETENTION_SECONDS


PERSONAL_X_MONITOR_INGEST_MAX_AGE_SECONDS = 8 * 60 * 60


PERSONAL_X_MONITOR_BACKFILL_DEFAULT_DAYS = 3


NEW_CONTRACT_MONITOR_RETENTION_SECONDS = PRICE_WATCH_RETENTION_SECONDS


PRICE_WATCH_THRESHOLD_PCT = 3.0


PRICE_WATCH_RESET_PCT = 3.25


PRICE_WATCH_REENTRY_COOLDOWN_SECONDS = 15 * 60


PRICE_WATCH_NEW_HIGH_DELTA_PCT = 0.5


PRICE_WATCH_STRUCTURE_BARS = 96


PRICE_WATCH_RETEST_MIN_BARS = 12


PRICE_WATCH_RETEST_DEPTH_PCT = 4.0


PRICE_WATCH_RETEST_HOLD_BARS = 8


PRICE_WATCH_CONSOLIDATION_BARS = 12


PRICE_WATCH_CONSOLIDATION_RANGE_PCT = 2.2


PRICE_WATCH_CONSOLIDATION_NET_PCT = 1.5


PRICE_WATCH_OVERSOLD_DRAWDOWN_PCT = 50.0


PRICE_WATCH_OVERSOLD_THRESHOLD_PCT = 3.0


PRICE_WATCH_OVERSOLD_REARM_PCT = 6.0


PRICE_WATCH_OVERSOLD_MIN_BARS_SINCE_LOW = 3


PRICE_WATCH_OVERSOLD_MIN_STAGE_GAIN_PCT = 5.0


PRICE_WATCH_OVERSOLD_RETEST_DEPTH_PCT = 4.0


PRICE_WATCH_OVERSOLD_NEW_LOW_DELTA_PCT = 5.0


PRICE_WATCH_FIB_DAILY_LIMIT = 240


PRICE_WATCH_FIB_RECENT_HIGH_DAYS = 14


PRICE_WATCH_FIB_MAX_IMPULSE_DAYS = 210


PRICE_WATCH_FIB_MIN_IMPULSE_DAYS = 3


PRICE_WATCH_FIB_MIN_IMPULSE_GAIN_PCT = 100.0


PRICE_WATCH_FIB_MIN_TREND_EFFICIENCY = 0.35


PRICE_WATCH_FIB_MIN_POSITIVE_DAY_RATIO = 0.50


PRICE_WATCH_FIB_EXPLOSIVE_GAIN_PCT = 300.0


PRICE_WATCH_FIB_EXPLOSIVE_MIN_EFFICIENCY = 0.18


PRICE_WATCH_FIB_MIN_EARLY_LIFT_PCT = 6.0


PRICE_WATCH_FIB_BASE_LOOKBACK_DAYS = 30


PRICE_WATCH_FIB_BASE_TOLERANCE_PCT = 4.0


PRICE_WATCH_FIB_BASE_CLOSE_RANGE_PCT = 35.0


PRICE_WATCH_FIB_MAX_PATH_DRAWDOWN_PCT = 15.0


PRICE_WATCH_FIB_SUSTAINED_BASE_DAYS = 7


PRICE_WATCH_FIB_DEPARTURE_PCT = 8.0


PRICE_WATCH_FIB_STRONG_DEPARTURE_PCT = 20.0


PRICE_WATCH_FIB_BREAKOUT_CANDLE_GAIN_PCT = 80.0


PRICE_WATCH_FIB_PRE_BREAKOUT_MAX_GAIN_PCT = 75.0


PRICE_WATCH_FIB_ACCEL_WINDOW_DAYS = 5


PRICE_WATCH_FIB_ACCEL_MIN_WINDOW_DAYS = 3


PRICE_WATCH_FIB_ACCEL_MIN_BULL_RATIO = 0.75


PRICE_WATCH_FIB_ACCEL_MIN_RATIO_JUMP = 0.35


PRICE_WATCH_FIB_ACCEL_MIN_GAIN_PCT = 18.0


PRICE_WATCH_FIB_ACCEL_MIN_EFFICIENCY = 0.55


PRICE_WATCH_FIB_ACCEL_NEW_LEG_CORRECTION_PCT = 30.0


PRICE_WATCH_FIB_CYCLE_RESET_DRAWDOWN_PCT = 50.0


PRICE_WATCH_FIB_CYCLE_RESET_CLOSE_PCT = 65.0


PRICE_WATCH_FIB_CYCLE_RECOVERY_PCT = 75.0


PRICE_WATCH_FIB_THRESHOLD_PCT = 3.0


PRICE_WATCH_FIB_REARM_PCT = 6.0


PRICE_WATCH_FIB_REFERENCE_DELTA_PCT = 2.0


PRICE_WATCH_INTERVAL_SECONDS = max(
    3.0,
    float(os.getenv("PRICE_WATCH_INTERVAL_SECONDS", "5") or "5"),
)


PRICE_WATCH_REALTIME_INTERVAL_SECONDS = max(
    1.0,
    float(os.getenv("PRICE_WATCH_REALTIME_INTERVAL_SECONDS", "3") or "3"),
)


PRICE_WATCH_REALTIME_TIMEOUT_SECONDS = max(
    1.0,
    min(5.0, float(os.getenv("PRICE_WATCH_REALTIME_TIMEOUT_SECONDS", "2.5") or "2.5")),
)


PRICE_WATCH_REALTIME_CYCLE_DEADLINE_SECONDS = max(
    2.0,
    min(10.0, float(os.getenv("PRICE_WATCH_REALTIME_CYCLE_DEADLINE_SECONDS", "8") or "8")),
)


PRICE_WATCH_LIVE_SAMPLE_MAX_GAP_MS = max(
    5_000,
    int(float(os.getenv("PRICE_WATCH_LIVE_SAMPLE_MAX_GAP_SECONDS", "15") or "15") * 1000),
)


PRICE_WATCH_LOCK = threading.Lock()


PRICE_WATCH_PROCESS_BASELINE_LOCK = threading.Lock()


PRICE_WATCH_PROCESS_BASELINED_SYMBOLS: set[str] = set()


BINANCE_FUTURES_STATUS_LOCK = threading.Lock()


BINANCE_FUTURES_STATUS_CACHE: dict[str, str] = {}


BINANCE_FUTURES_STATUS_TTL_SECONDS = 5 * 60


ROTATION_LEADER_MIN_GAIN_PCT = 300.0


ROTATION_REFRESH_SECONDS = max(120, int(float(os.getenv("ROTATION_REFRESH_SECONDS", "300") or "300")))


ROTATION_MAX_LEADERS = 40


RANK_GAINER_LEADER_HISTORY_SECONDS = 30 * 24 * 60 * 60


ROTATION_MAX_CANDIDATES = 8


ROTATION_AI_MIN_CONFIDENCE = max(0, min(100, int(float(os.getenv("ROTATION_AI_MIN_CONFIDENCE", "60") or "60"))))


ROTATION_AI_INCREMENT_BATCH_SIZE = max(1, min(30, int(float(os.getenv("ROTATION_AI_INCREMENT_BATCH_SIZE", "20") or "20"))))


PRICE_STRUCTURE_CACHE_TTL_SECONDS = 180


PRICE_STRUCTURE_STRATEGY_VERSION = "v91"


PRICE_STRUCTURE_NEW_COIN_MAX_AGE_DAYS = 14


PRICE_STRUCTURE_DEEP_DRAWDOWN_PCT = 70.0


PRICE_STRUCTURE_REPRICING_LOOKBACK_BARS = 192


PRICE_STRUCTURE_REPRICING_PEAK_MAX_AGE_BARS = 144


PRICE_STRUCTURE_REGIME_RESET_MIN_SCORE = 75


PRICE_STRUCTURE_EXTREME_HEAT_MIN = 80.0


PRICE_STRUCTURE_EXTREME_HEAT_MAX_RANK = 3


PRICE_STRUCTURE_EVENT_CONTEXT_LOCK = threading.Lock()


PRICE_STRUCTURE_EVENT_CONTEXTS: dict[str, dict[str, Any]] = {}


PRICE_STRUCTURE_MONITOR_INTERVAL_SECONDS = max(
    1.0,
    float(os.getenv("PRICE_STRUCTURE_MONITOR_INTERVAL_SECONDS", "3") or "3"),
)


PRICE_STRUCTURE_MONITOR_WORKERS = max(
    2,
    min(16, int(os.getenv("PRICE_STRUCTURE_MONITOR_WORKERS", "6") or "6")),
)


PRICE_STRUCTURE_PRIORITY_REFRESH_SECONDS = max(
    3.0,
    float(os.getenv("PRICE_STRUCTURE_PRIORITY_REFRESH_SECONDS", "10") or "10"),
)


PRICE_STRUCTURE_REGULAR_REFRESH_SECONDS = max(
    PRICE_STRUCTURE_PRIORITY_REFRESH_SECONDS,
    float(os.getenv("PRICE_STRUCTURE_REGULAR_REFRESH_SECONDS", "45") or "45"),
)


# A worker future that stays incomplete this long is treated as hung (usually a
# stalled socket inside one provider). Reap it so the symbol becomes schedulable
# again and the worker slot returns to the pool; the abandoned thread, if it
# ever wakes, still commits through the serialized snapshot lock.
PRICE_STRUCTURE_MONITOR_HUNG_FUTURE_SECONDS = max(
    60.0,
    float(os.getenv("PRICE_STRUCTURE_MONITOR_HUNG_FUTURE_SECONDS", "300") or "300"),
)


# Symbols whose last snapshot has no frames are probed again after this delay
# instead of waiting for the regular full-rotation interval.
PRICE_STRUCTURE_UNAVAILABLE_RETRY_SECONDS = max(
    30.0,
    float(os.getenv("PRICE_STRUCTURE_UNAVAILABLE_RETRY_SECONDS", "90") or "90"),
)


PRICE_STRUCTURE_SHORT_HISTORY_MAX_AGE_DAYS = max(
    30,
    int(os.getenv("PRICE_STRUCTURE_SHORT_HISTORY_MAX_AGE_DAYS", "45") or "45"),
)


PRICE_STRUCTURE_RECENT_LISTING_CACHE_SECONDS = 5 * 60


PRICE_STRUCTURE_FAST_PROVIDER_LIMIT = max(
    3,
    min(10, int(os.getenv("PRICE_STRUCTURE_FAST_PROVIDER_LIMIT", "6") or "6")),
)


PRICE_STRUCTURE_PREARM_INTERVAL_SECONDS = max(
    0.5,
    float(os.getenv("PRICE_STRUCTURE_PREARM_INTERVAL_SECONDS", "1") or "1"),
)


PRICE_STRUCTURE_PREARM_DISTANCE_PCT = max(
    0.05,
    float(os.getenv("PRICE_STRUCTURE_PREARM_DISTANCE_PCT", "3") or "3"),
)


PRICE_STRUCTURE_PREARM_MAX_OVERSHOOT_PCT = max(
    0.05,
    float(os.getenv("PRICE_STRUCTURE_PREARM_MAX_OVERSHOOT_PCT", "0.35") or "0.35"),
)


PRICE_STRUCTURE_OBSERVATION_MAX_OVERSHOOT_PCT = max(
    PRICE_STRUCTURE_PREARM_MAX_OVERSHOOT_PCT,
    float(os.getenv("PRICE_STRUCTURE_OBSERVATION_MAX_OVERSHOOT_PCT", "1.5") or "1.5"),
)


PRICE_STRUCTURE_SIGNAL_MAX_OVERSHOOT_PCT = max(
    PRICE_STRUCTURE_OBSERVATION_MAX_OVERSHOOT_PCT,
    float(os.getenv("PRICE_STRUCTURE_SIGNAL_MAX_OVERSHOOT_PCT", "2") or "2"),
)


PRICE_STRUCTURE_PREARM_FORECAST_MINUTES = max(
    1.0,
    float(os.getenv("PRICE_STRUCTURE_PREARM_FORECAST_MINUTES", "10") or "10"),
)


PRICE_STRUCTURE_PREARM_MAX_FORECAST_DISTANCE_PCT = max(
    PRICE_STRUCTURE_PREARM_DISTANCE_PCT,
    float(os.getenv("PRICE_STRUCTURE_PREARM_MAX_FORECAST_DISTANCE_PCT", "4") or "4"),
)


PRICE_STRUCTURE_PREARM_MIN_SPEED_PCT_PER_MINUTE = max(
    0.001,
    float(os.getenv("PRICE_STRUCTURE_PREARM_MIN_SPEED_PCT_PER_MINUTE", "0.02") or "0.02"),
)


PRICE_STRUCTURE_PREARM_MIN_UP_RATIO = min(
    1.0,
    max(0.5, float(os.getenv("PRICE_STRUCTURE_PREARM_MIN_UP_RATIO", "0.6") or "0.6")),
)


PRICE_STRUCTURE_PREARM_MAX_SNAPSHOT_AGE_SECONDS = max(
    30.0,
    float(os.getenv("PRICE_STRUCTURE_PREARM_MAX_SNAPSHOT_AGE_SECONDS", "180") or "180"),
)


PRICE_STRUCTURE_SIGNAL_CLOSE_GRACE_MS = max(
    15_000,
    int(float(os.getenv("PRICE_STRUCTURE_SIGNAL_CLOSE_GRACE_SECONDS", "120") or "120") * 1000),
)


PRICE_STRUCTURE_OBSERVATION_COOLDOWN_MS = max(
    60_000,
    int(float(os.getenv("PRICE_STRUCTURE_OBSERVATION_COOLDOWN_SECONDS", "3600") or "3600") * 1000),
)


MONITOR_ALERT_REPLAY_MAX_GAP_MS = max(
    60_000,
    int(float(os.getenv("MONITOR_ALERT_REPLAY_MAX_GAP_SECONDS", "600") or "600") * 1000),
)


NEW_COIN_LOW_MAX_AGE_SECONDS = 365 * 24 * 60 * 60


NEW_COIN_LOW_INVENTORY_CACHE_SECONDS = 60 * 60


NEW_COIN_LOW_MONITOR_INTERVAL_SECONDS = max(
    5.0,
    float(os.getenv("NEW_COIN_LOW_MONITOR_INTERVAL_SECONDS", "10") or "10"),
)


NEW_COIN_LOW_MONITOR_WORKERS = max(
    2,
    min(16, int(os.getenv("NEW_COIN_LOW_MONITOR_WORKERS", "4") or "4")),
)


NEW_COIN_LOW_ACTIVITY_GRACE_DAYS = max(
    0.0,
    float(os.getenv("NEW_COIN_LOW_ACTIVITY_GRACE_DAYS", "0.25") or "0.25"),
)


NEW_COIN_LOW_UNAVAILABLE_MAX_AGE_DAYS = max(
    NEW_COIN_LOW_ACTIVITY_GRACE_DAYS,
    float(os.getenv("NEW_COIN_LOW_UNAVAILABLE_MAX_AGE_DAYS", "7") or "7"),
)


NEW_COIN_LOW_MIN_TURNOVER_24H_USD = max(
    0.0,
    float(os.getenv("NEW_COIN_LOW_MIN_TURNOVER_24H_USD", "10000000") or "10000000"),
)


NEW_COIN_LOW_ACTIVITY_CACHE_SECONDS = max(
    60.0,
    float(os.getenv("NEW_COIN_LOW_ACTIVITY_CACHE_SECONDS", "600") or "600"),
)


NEW_COIN_LOW_ACTIVITY_LOCK = threading.Lock()


PRICE_STRUCTURE_RECENT_LISTING_INDEX_LOCK = threading.Lock()


PRICE_MONITOR_RETENTION_ARCHIVES_LOCK = threading.Lock()


PRICE_MONITOR_RETENTION_ARCHIVES: dict[str, dict[str, Any]] = {}


NEW_COIN_LOW_ITEMS: dict[str, dict[str, Any]] = {}


NEW_COIN_LOW_LOCK = threading.Lock()


PRICE_STRUCTURE_REPLAY_SUPPRESSED_KEYS: set[str] = set()


PRICE_STRUCTURE_REPLAY_SUPPRESSED_LOCK = threading.Lock()


PRICE_STRUCTURE_PREARM_STATUS_LOCK = threading.Lock()


PRICE_STRUCTURE_PREARM_STATUS: dict[str, Any] = {
    "lastRunAt": 0,
    "candidates": 0,
    "quotes": 0,
    "alerts": 0,
    "candidateSources": {},
    "quoteFailures": 0,
    "skipReasons": {},
    "elapsedMs": 0,
    "lastDecisions": [],
}


PRICE_STRUCTURE_PROVIDER_PREFERENCE: dict[str, str] = {}


PRICE_STRUCTURE_PROVIDER_PROBE_CURSOR: dict[str, int] = {}


PRICE_STRUCTURE_PROVIDER_PREFERENCE_LOCK = threading.Lock()


PRICE_STRUCTURE_ONCHAIN_POOL_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


PRICE_STRUCTURE_ONCHAIN_POOL_CACHE_LOCK = threading.Lock()


PRICE_STRUCTURE_ONCHAIN_CANDLE_CACHE: dict[str, tuple[float, list[tuple[int, float, float, float, float, float]]]] = {}


PRICE_STRUCTURE_ONCHAIN_CANDLE_CACHE_LOCK = threading.Lock()


PRICE_STRUCTURE_ONCHAIN_CANDLE_INFLIGHT: dict[str, threading.Event] = {}


PRICE_STRUCTURE_ONCHAIN_CANDLE_ERROR_CACHE: dict[str, tuple[float, str]] = {}


BINANCE_WALLET_4H_STRUCTURE_LOCK = threading.Lock()


PRICE_STRUCTURE_ONCHAIN_POOL_CACHE_TTL_SECONDS = 10 * 60


PRICE_STRUCTURE_ONCHAIN_CANDLE_CACHE_TTL_SECONDS = 20


PRICE_STRUCTURE_ONCHAIN_CANDLE_STALE_TTL_SECONDS = 10 * 60


PRICE_STRUCTURE_ONCHAIN_CANDLE_ERROR_TTL_SECONDS = 10


# Symbol-search onchain resolution is expensive and rate-limited. Once a pool
# (and therefore a contract + chain) has been resolved for a symbol, remember it
# so the contract-bound providers (Binance Wallet / OKX DEX / 链上 K线) can use
# the exact identity on every later pass instead of re-searching the ticker.
PRICE_STRUCTURE_RESOLVED_IDENTITIES: dict[str, tuple[float, dict[str, Any]]] = {}


PRICE_STRUCTURE_RESOLVED_IDENTITIES_LOCK = threading.Lock()


PRICE_STRUCTURE_RESOLVED_IDENTITY_TTL_SECONDS = 24 * 3600


PRICE_STRUCTURE_RESOLVED_IDENTITY_MAX_ENTRIES = 512


# GeckoTerminal network slug -> chain label understood by Binance Wallet klines.
PRICE_STRUCTURE_CHAIN_FROM_GT_NETWORK = {
    "eth": "ethereum",
    "bsc": "bsc",
    "base": "base",
    "solana": "solana",
    "robinhood": "robinhood",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "polygon_pos": "polygon",
    "avax": "avalanche",
    "sui-network": "sui",
    "aptos": "aptos",
}


PRICE_STRUCTURE_ONCHAIN_POOL_CACHE_MAX_ENTRIES = 256


PRICE_STRUCTURE_ONCHAIN_CANDLE_CACHE_MAX_ENTRIES = 720


# Same TTL/inflight/stale-error discipline the GeckoTerminal fetcher already
# uses, applied to Binance Wallet and OKX DEX so parallel monitor workers,
# prior-high refreshes and onchain fallback fills coalesce into one request.
PRICE_STRUCTURE_ONCHAIN_KLINE_CACHE: dict[str, tuple[float, list]] = {}


PRICE_STRUCTURE_ONCHAIN_KLINE_ERROR_CACHE: dict[str, tuple[float, str]] = {}


PRICE_STRUCTURE_ONCHAIN_KLINE_INFLIGHT: dict[str, threading.Event] = {}


PRICE_STRUCTURE_ONCHAIN_KLINE_CACHE_LOCK = threading.Lock()


PRICE_STRUCTURE_ONCHAIN_KLINE_CACHE_TTL_SECONDS = 15


PRICE_STRUCTURE_ONCHAIN_KLINE_STALE_TTL_SECONDS = 10 * 60


PRICE_STRUCTURE_ONCHAIN_KLINE_ERROR_TTL_SECONDS = 8


PRICE_STRUCTURE_ONCHAIN_KLINE_CACHE_MAX_ENTRIES = 1024


PRICE_STRUCTURE_MOMENTUM_CACHE_MAX_ENTRIES = 512


PRICE_STRUCTURE_MOMENTUM_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


PRICE_STRUCTURE_MOMENTUM_CACHE_LOCK = threading.Lock()


PRICE_STRUCTURE_MOMENTUM_CACHE_TTL_SECONDS = 10


PRICE_STRUCTURE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


PRICE_STRUCTURE_CACHE_LOCK = threading.Lock()


PRICE_STRUCTURE_REFRESH_LOCK = threading.Lock()


STRATEGY_ADAPTIVE_CONTEXT_LOCK = threading.Lock()


PRICE_STRUCTURE_TIMEFRAMES = (
    ("1m", "1分钟", "1m", "1m", "1m", "1m", "1min", "1m", "1m", "1m"),
    ("5m", "5分钟", "5m", "5m", "5m", "5m", "5min", "5m", "5m", "5m"),
    ("15m", "15分钟", "15m", "15m", "15m", "15m", "15min", "15m", "15m", "15m"),
    ("1h", "1小时", "1h", "1H", "1H", "1h", "60min", "1h", "1h", "1h"),
    ("4h", "4小时", "4h", "4H", "4H", "4h", "4hour", "4h", "4h", "4h"),
    ("1d", "日线", "1d", "1D", "1D", "1d", "1day", "1d", "1d", "1d"),
)


PRICE_STRUCTURE_INTERVAL_LABELS = {key: label for key, label, *_rest in PRICE_STRUCTURE_TIMEFRAMES}


PRICE_STRUCTURE_CANDLE_LIMIT = 300


DRAGON_WAVE_MONITOR_BRIDGE = ROOT / "tools" / "dragon_wave_monitor_bridge.js"


STRATEGY_FEE_BUFFER_PCT = 0.12


STRATEGY_DEFAULT_STOP_PCT = 3.0


STRATEGY_MIN_STOP_PCT = 0.35


STRATEGY_POSITION_REFRESH_SECONDS = 20


STRATEGY_LIVE_TRADING_ENABLED = str(os.getenv("STRATEGY_LIVE_TRADING_ENABLED", "")).strip().lower() in {
    "1", "true", "yes", "on"
}


STRATEGY_MAX_ORDER_NOTIONAL_USDT = float(os.getenv("STRATEGY_MAX_ORDER_NOTIONAL_USDT", "25000") or "25000")


STRATEGY_EXCHANGE_CACHE: dict[str, tuple[float, Any]] = {}


STRATEGY_EXCHANGE_CACHE_LOCK = threading.Lock()


STRATEGY_POSITION_CACHE_SECONDS = 15


LOGO_CACHE: dict[str, tuple[float, list[str]]] = {}


LOGO_CACHE_LOCK = threading.Lock()


LOGO_CACHE_TTL = 24 * 60 * 60


LOGO_CACHE_MAX_ENTRIES = 1200


X_KOL_RSS_SOURCE_POOL = ThreadPoolExecutor(
    max_workers=X_KOL_RSS_SOURCE_WORKERS,
    thread_name_prefix="x-kol-source",
)


X_KOL_RSS_MIRROR_POOL = ThreadPoolExecutor(
    max_workers=X_KOL_RSS_MIRROR_WORKERS,
    thread_name_prefix="x-kol-mirror",
)


TRENCH_PERSON_FETCH_POOL = ThreadPoolExecutor(
    max_workers=TRENCH_PERSON_FETCH_WORKERS,
    thread_name_prefix="trench-person-fast",
)


PRICE_STRUCTURE_TIMEFRAME_POOL = ThreadPoolExecutor(
    max_workers=max(
        4,
        min(24, int(os.getenv("PRICE_STRUCTURE_TIMEFRAME_WORKERS", "6") or "6")),
    ),
    thread_name_prefix="market-timeframe",
)


PRICE_STRUCTURE_ENTRY_DAY_TIMEFRAME_POOL = ThreadPoolExecutor(
    max_workers=max(
        3,
        min(16, int(os.getenv("PRICE_STRUCTURE_ENTRY_DAY_TIMEFRAME_WORKERS", "4") or "4")),
    ),
    thread_name_prefix="entry-day-timeframe",
)


PRICE_STRUCTURE_QUOTE_POOL = ThreadPoolExecutor(
    max_workers=max(
        2,
        min(8, int(os.getenv("PRICE_STRUCTURE_QUOTE_WORKERS", "4") or "4")),
    ),
    thread_name_prefix="market-quote",
)


PRICE_WATCH_REALTIME_POOL = ThreadPoolExecutor(
    max_workers=max(
        8,
        min(32, int(os.getenv("PRICE_WATCH_REALTIME_WORKERS", "24") or "24")),
    ),
    thread_name_prefix="price-watch-live",
)


PRICE_WATCH_REALTIME_SOURCE_POOL_LOCK = threading.Lock()


PRICE_WATCH_REALTIME_SOURCE_POOLS: dict[tuple[str, str], ThreadPoolExecutor] = {}


PRICE_WATCH_REALTIME_HTTP_CLIENTS: dict[str, httpx.Client] = {}


PRICE_WATCH_REALTIME_HTTP_CLIENT_GENERATIONS: dict[str, int] = {}


PRICE_WATCH_REALTIME_HTTP_SEMAPHORE = threading.BoundedSemaphore(4)


PRICE_WATCH_REALTIME_PROXY_LOCK = threading.Lock()


PRICE_WATCH_REALTIME_PROXY_STATE: dict[str, Any] = {"resolved": False, "url": "", "generation": -1}


MARKET_SOURCE_POOL = ThreadPoolExecutor(
    max_workers=max(
        4,
        min(12, int(os.getenv("MARKET_SOURCE_WORKERS", "6") or "6")),
    ),
    thread_name_prefix="market-source",
)


SHARED_EXECUTORS = (
    API_REFRESH_POOL,
    NEWS_TRADE_SECURITY_POOL,
    NEWS_TRADE_AI_POOL,
    ROTATION_AI_POOL,
    CHAIN_ECOSYSTEM_AI_POOL,
    BINANCE_AI_NARRATIVE_POOL,
    EXCHANGE_AI_TRENCH_POOL,
    AVE_CHAIN_POOL,
    WECHAT_GROUP_ANALYSIS_POOL,
    X_KOL_RSS_SOURCE_POOL,
    X_KOL_RSS_MIRROR_POOL,
    TRENCH_PERSON_FETCH_POOL,
    PRICE_STRUCTURE_TIMEFRAME_POOL,
    PRICE_STRUCTURE_ENTRY_DAY_TIMEFRAME_POOL,
    PRICE_STRUCTURE_QUOTE_POOL,
    PRICE_WATCH_REALTIME_POOL,
    MARKET_SOURCE_POOL,
)


configured_runtime_dir = os.getenv("XINGYUN_RUNTIME_DIR", "").strip()


PERSIST_CACHE_DIR = Path(configured_runtime_dir).expanduser().resolve() if configured_runtime_dir else ROOT / ".runtime-cache"


X_TWEET_ANALYSIS_QUEUE = XTweetAnalysisQueue(PERSIST_CACHE_DIR / "x_tweet_analysis.sqlite")


X_TWEET_ANALYSIS_INCREMENTAL_STATE = X_TWEET_ANALYSIS_QUEUE.enable_incremental_only()


X_TWEET_ANALYSIS_PERSON_HANDLES = frozenset(
    str(source.get("handle") or "").strip().casefold().lstrip("@")[:80]
    for source in important_person_sources()
    if str(source.get("handle") or "").strip()
)


SMART_MONEY_MONITOR = SmartMoneyMonitor(
    PERSIST_CACHE_DIR / "smart_money_monitor.sqlite",
    seed_defaults=True,
)


RUNTIME_QR_MAX_AGE_SECONDS = 30 * 60


RUNTIME_QR_MAX_FILES = 8


RUNTIME_TEMP_MAX_AGE_SECONDS = 60 * 60


# 磁盘上 api_*.json / source-cache/*.json 缓存键的长尾淘汰阈值（秒）。
# 一个缓存键若超过该时长未被任何源刷新，说明它对应的数据源已下线/改名，
# 属于废弃残留，运行期后台清理会将其从磁盘删除，避免永久累积。
# 活跃源每 10–300s 就会刷新一次，24h 远大于任何活跃源的最长刷新周期，
# 因此该阈值不会误删仍在使用中的缓存。
STALE_API_CACHE_FILE_MAX_AGE_SECONDS = 24 * 60 * 60


DESKTOP_ALERT_LOG_PATH = PERSIST_CACHE_DIR / "desktop_alert.log"


DESKTOP_ALERT_STATE_PATH = PERSIST_CACHE_DIR / "desktop_alert_seen.json"


DESKTOP_ALERT_MARKER_DIR = PERSIST_CACHE_DIR / "desktop_alert_markers"


DESKTOP_ALERT_MARKER_TTL_SECONDS = 10 * 60


SITE_ALERT_STATE_PATH = PERSIST_CACHE_DIR / "site_alert_seen.json"


NEWS_TRADE_ALERT_STATE_PATH = PERSIST_CACHE_DIR / "news_trade_alert_state.json"


NEWS_TRADE_DESKTOP_INTAKE_PATH = PERSIST_CACHE_DIR / "news_trade_desktop_intake.json"


ASTER_ANNOUNCEMENT_STATE_PATH = PERSIST_CACHE_DIR / "aster_listing_announcements.json"


ASTER_X_LISTING_CACHE_PATH = PERSIST_CACHE_DIR / "aster_x_listing_announcements.json"


RANK_MONITOR_STATE_PATH = PERSIST_CACHE_DIR / "rank_monitor_state.json"


BINANCE_WALLET_HOT_ALERT_STATE_PATH = PERSIST_CACHE_DIR / "binance_wallet_hot_alert_state.json"


AVE_HOT_ALERT_STATE_PATH = PERSIST_CACHE_DIR / "ave_hot_alert_state.json"


GMGN_HOT_SEARCH_ALERT_STATE_VERSION = 1


GMGN_HOT_SEARCH_ALERT_PERIOD = "5m"


GMGN_HOT_SEARCH_REENTRY_SECONDS = 24 * 60 * 60


GMGN_HOT_SEARCH_ALERT_INTERVAL_SECONDS = max(
    15,
    int(float(os.getenv("GMGN_HOT_SEARCH_ALERT_INTERVAL_SECONDS", "30") or "30")),
)


GMGN_HOT_SEARCH_ALERT_LOCK = threading.Lock()


GMGN_HOT_SEARCH_ALERT_STATE_PATH = PERSIST_CACHE_DIR / "gmgn_hot_search_alert_state.json"


# 进程启动标记：同一进程内恒定，重启后必变。榜单类「新进」告警用它区分
# 「本次启动的第一次轮询」（只重建基线，不补弹停机期间积压的历史新进）与
# 正常运行中的轮询（照常弹窗）。见 sync_gmgn_hot_search_alert_feed。
SERVER_BOOT_AT_MS = int(time.time() * 1000)


GLOBAL_HOTSPOT_STATE_PATH = PERSIST_CACHE_DIR / "global_hotspot_monitor.json"


BINANCE_WALLET_4H_STRUCTURE_PATH = PERSIST_CACHE_DIR / "binance_wallet_4h_structure_history.json"


OKX_FUTURES_CACHE_PATH = PERSIST_CACHE_DIR / "okx_futures_hot.json"


OKX_DEX_SOURCE_CACHE_PATH = PERSIST_CACHE_DIR / "okx_dex_source.json"


GMGN_TRENCH_HISTORY_PATH = PERSIST_CACHE_DIR / "gmgn_trenches_received_history.json"


GMGN_TRENCH_HISTORY_VERSION = 3


TRENCH_PERSON_SIGNAL_PATH = PERSIST_CACHE_DIR / "gmgn_trench_person_signals.json"


TRENCH_PERSON_WATCH_STATE_PATH = PERSIST_CACHE_DIR / "gmgn_trench_person_watch.json"


TRENCH_PERSON_SEMANTIC_CACHE_PATH = PERSIST_CACHE_DIR / "gmgn_trench_person_semantic_cache.json"


THS_SOURCE_CACHE_PATH = PERSIST_CACHE_DIR / "ths_hot_source.json"


WECHAT_ACCOUNT_CACHE_PATH = PERSIST_CACHE_DIR / "wechat_accounts.json"


WECHAT_SOURCE_ALIAS_CACHE_PATH = PERSIST_CACHE_DIR / "wechat_source_aliases.json"


X_KOL_SOURCES_PATH = PERSIST_CACHE_DIR / "x_kol_sources.json"


X_KOL_TRANSLATION_CACHE_PATH = PERSIST_CACHE_DIR / "x_kol_translations.json"


X_KOL_AI_FILTER_CACHE_PATH = PERSIST_CACHE_DIR / "x_kol_ai_filter.json"


X_KOL_API_COST_STATE_PATH = PERSIST_CACHE_DIR / "x_kol_api_cost_state.json"


NEWSFLASH_SEMANTIC_AI_CACHE_PATH = PERSIST_CACHE_DIR / "newsflash_semantic_dedupe.json"


STRATEGY_ADAPTIVE_CONTEXT_PATH = PERSIST_CACHE_DIR / "strategy_adaptive_context.json"


PRICE_STRUCTURE_SNAPSHOT_PATH = PERSIST_CACHE_DIR / "price_structure_snapshot.json"


NEW_COIN_LOW_SNAPSHOT_PATH = PERSIST_CACHE_DIR / "new_coin_low_structure_snapshot.json"


NEW_COIN_LOW_LISTING_HISTORY_PATH = PERSIST_CACHE_DIR / "new_coin_low_listing_history.json"


FIRST_LISTING_ALERT_STATE_PATH = PERSIST_CACHE_DIR / "first_listing_alert_state.json"


TRANSLATION_CACHE_MAX_ENTRIES = 20000


CN_STOCK_GAINERS_CACHE_PATH = PERSIST_CACHE_DIR / "cn_stock_gainers_source.json"


DEEPSEEK_INSIGHTS_CACHE_PATH = PERSIST_CACHE_DIR / "deepseek_rank_insights.json"


NEWS_TRADE_AI_CACHE_PATH = PERSIST_CACHE_DIR / "news_trade_ai_analysis.json"


EVENT_FLOW_STORE = EventFlowStore(PERSIST_CACHE_DIR / "event_flow.sqlite")


ALERT_DELIVERY_STORE = AlertDeliveryStore(PERSIST_CACHE_DIR / "alert_delivery.sqlite")


DESKTOP_ALERT_DELIVERIES: dict[int, dict[str, Any]] = {}


ROTATION_AI_CACHE_PATH = PERSIST_CACHE_DIR / "rotation_ai_leaders.json"


ROTATION_ALERT_STATE_PATH = PERSIST_CACHE_DIR / "rotation_alert_state.json"


CHAIN_ECOSYSTEM_AI_CACHE_PATH = PERSIST_CACHE_DIR / "chain_ecosystem_ai_analysis.json"


ONCHAIN_RESEARCH_MODE_STATE_PATH = PERSIST_CACHE_DIR / "onchain_research_mode.json"


ONCHAIN_RESEARCH_MODE_LOCK = threading.Lock()


SELF_OPTIMIZATION_STATE_PATH = PERSIST_CACHE_DIR / "self_optimization_state.json"


SELF_OPTIMIZATION_WORK_ROOT = PERSIST_CACHE_DIR / "self-optimization"


AICOIN_PAYLOAD_TOKEN_PATH = PERSIST_CACHE_DIR / "aicoin_payload_token.txt"


AVE_TOKEN_CACHE_PATH = PERSIST_CACHE_DIR / "ave_token.json"


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


WECHAT_AUTH_POLLING_UUIDS: set[str] = set()


WECHAT_AUTH_VALIDATE_INTERVAL_SECONDS = 5 * 60


WECHAT_AUTH_MONITOR_INTERVAL_SECONDS = 3 * 60


WECHAT_AUTH_SCHEDULE_HOURS = (9, 12, 18, 22)


WECHAT_PLATFORM_AUTO_PAGE_LIMIT = 1


WECHAT_PLATFORM_BACKFILL_PAGE_LIMIT = 24


X_KOL_RETENTION_MS = 14 * 24 * 60 * 60 * 1000


X_KOL_FETCH_LIMIT = 80


X_KOL_DESKTOP_ALERT_MAX_AGE_MS = max(
    60_000,
    int(float(os.getenv("X_KOL_DESKTOP_ALERT_MAX_AGE_SECONDS", "900") or "900") * 1000),
)


AUTH_DB_PATH = PERSIST_CACHE_DIR / "xingyunshe_auth.db"


CHAIN_ECOSYSTEM_DB_PATH = PERSIST_CACHE_DIR / "chain_ecosystem.db"


CHAIN_ECOSYSTEM_MONITOR = ChainEcosystemMonitor(ChainEcosystemStore(CHAIN_ECOSYSTEM_DB_PATH))


AUTH_SESSION_COOKIE = "xys_session"


AUTH_SESSION_DAYS = 7


AUTH_PASSWORD_ITERATIONS = 240_000


AUTH_DB_LOCK = threading.RLock()


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


FIELD_KEY_PATH = PERSIST_CACHE_DIR / "field_encryption.key"


SECURITY_AUDIT_RETENTION_DAYS = 180


SECURITY_RATE_LOCK = threading.Lock()


SECURITY_RATE_BUCKETS: dict[str, list[float]] = {}


USER_SCOPE_TODO = "todo"


USER_SCOPE_X_KOL_SOURCES = "x_kol_sources"


USER_SCOPE_RSS_SOURCES = "rss_sources"


USER_SCOPE_DRAGON_WAVE_FEEDBACK = "dragon_wave_feedback_v1"


USER_SCOPE_RSS_ITEMS = "rss_items"


STOCK_LOGO_OVERRIDES = {
    "hk:00100": ["minimax.io"],
    "hk:01236": ["ldrobot.com"],
}


EXCLUDED_FUTU_HK_HOT_CODES = {"00700", "09988", "01810", "03690"}


EXCLUDED_FUTU_HK_HOT_NAMES = ("腾讯", "阿里", "小米", "美团", "tencent", "alibaba", "xiaomi", "meituan")
