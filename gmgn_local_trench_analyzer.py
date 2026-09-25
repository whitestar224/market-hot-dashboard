"""GMGN skill 本地分析引擎 — 战壕新币实时通道.

职责:
  1. 监听 onchain_fast_jobs 中 status='pending' 且来自 GMGN 战壕榜的新币
  2. 用 gmgn-cli 只读接口拉取: token info/security、holders/traders smart_degen、
     binance meme rush、AIXBT 市场主线、Nansen 聪明钱流
  3. 【本地规则评分器主力】套用 GMGN contract-dd 0-100 安全评分框架 + 聪明钱/热点信号,
     纯本地打分, 零 AI 消耗, 直接淘汰 honeypot/黑名单/高税/高集中度等红旗币
  4. 【DeepSeek 仅辅助】只有本地评分判定「有苗头」(安全过关 + 聪明钱/热点信号) 的少数币,
     才调用 DeepSeek+V4.9 做一次辅助确认, 控制额度消耗
  5. 回写 onchain_fast_jobs status='ready'; 只有 AI 确认 strong 且完整 framework 才弹窗

两条投研通道并行:
  - 通道 A (已存在): bridge -> 外部 ChatGPT 聊天 -> 回写 -> 弹窗
  - 通道 B (本模块): gmgn-cli 数据 -> 本地评分(主力) + DeepSeek(辅助) -> 回写 -> 弹窗
两条通道以 onchain_fast_jobs.key 去重: pending 且 no-chatgpt-result 才入通道 B.
遵守项目铁律:
  - 写路径后台化, 不持组件锁抢 AUTH_DB_LOCK
  - 纯读 DB 调用绕过应用锁
  - 失败快速降级, 不阻塞主循环
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import contextlib
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from onchain_research_framework import normalize_framework_assessment

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
# 是否启用本地 GMGN 通道 (默认 True; 可通过环境变量关闭)
GMGN_LOCAL_ENABLED = os.getenv("GMGN_LOCAL_RESEARCH_ENABLED", "1") == "1"
# 单轮最多本地分析的新币数
GMGN_LOCAL_BATCH = int(os.getenv("GMGN_LOCAL_BATCH_SIZE", "3"))
# 两次本地分析轮之间的冷却时间 (秒) — 避免高频轮询
GMGN_LOCAL_INTERVAL_S = int(os.getenv("GMGN_LOCAL_INTERVAL_SECONDS", "25"))
# promising 缓冲：攒够 N 个才一次性送 DeepSeek，摊薄固定开销（框架规则 + schema ≈ 3000 token/次）
PROMISING_BATCH_SIZE = int(os.getenv("GMGN_LOCAL_PROMISING_BATCH_SIZE", "50"))
_PROMISING_BUFFER: list[tuple[dict, dict, dict]] = []  # (job, base_row, local)
_PROMISING_BUFFERED_KEYS: set[str] = set()
_PROMISING_BUFFER_LOCK = threading.Lock()
# 对候选加哪些 gmgn-cli 数据源
GMGN_DATA_SOURCES = [
    "info",       # token 基础信息 + 实时价格
    "security",   # 安全指标 (mint/auth/LP/流动性锁等)
    "holders",    # top holders, smart_degen 标签
    "traders",    # top traders
    "signal",     # 聪明钱买入/价格异动信号
]
# binance meme rush 额外启用 (已有 fetch 函数)
BINANCE_MEME_RUSH_ENABLED = os.getenv("BINANCE_MEME_RUSH_LOCAL", "1") == "1"

# ---------------------------------------------------------------------------
# gmgn-cli 执行 (Windows .cmd 兼容)
# ---------------------------------------------------------------------------

_GMGN_INDEX_JS = r"C:\Users\ZhuanZ1\AppData\Roaming\npm\node_modules\gmgn-cli\dist\index.js"


def _gmgn_node_args() -> list[str]:
    """Invoke gmgn-cli by running node on index.js directly.

    Previously we executed gmgn-cli.cmd with shell=True. On Windows that is a
    hang hazard: subprocess.run's timeout only kills the cmd.exe wrapper, not
    the real node child, so communicate() blocks forever waiting on the child's
    still-open stdout pipe and the timeout never fires. Calling node directly
    (no shell) lets the timeout actually kill the process.
    """
    node = shutil.which("node") or "node"
    return [node, _GMGN_INDEX_JS]


def _run_gmgn(subcommand: list[str], *, timeout: int = 12) -> dict[str, Any]:
    """Run gmgn-cli subcommand and return parsed JSON. Falls back to {} on error.

    Executed via node directly (no .cmd shell) so the timeout is enforceable.
    """
    try:
        args = [*_gmgn_node_args(), *subcommand, "--raw"]
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout or "{}")
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"items": data}
        return {}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError, OSError, ValueError):
        return {}


def _gmgn_token_info(chain: str, address: str) -> dict[str, Any]:
    return _run_gmgn(["token", "info", "--chain", chain, "--address", address])


def _gmgn_security(chain: str, address: str) -> dict[str, Any]:
    return _run_gmgn(["token", "security", "--chain", chain, "--address", address])


def _gmgn_holders(chain: str, address: str, tag: str = "smart_degen", limit: int = 20) -> list[dict[str, Any]]:
    data = _run_gmgn(["token", "holders", "--chain", chain, "--address", address,
                      "--tag", tag, "--limit", str(limit), "--order-by", "amount_percentage", "--direction", "desc"])
    # gmgn-cli returns {"list": [...]} for holders
    if isinstance(data, dict):
        return data.get("list") or []
    if isinstance(data, list):
        return data
    return []


def _gmgn_traders(chain: str, address: str, tag: str = "smart_degen", limit: int = 20) -> list[dict[str, Any]]:
    data = _run_gmgn(["token", "traders", "--chain", chain, "--address", address,
                      "--tag", tag, "--limit", str(limit), "--order-by", "buy_volume_cur", "--direction", "desc"])
    # gmgn-cli returns a raw list for traders
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("list") or data.get("traders") or []
    return []


def _gmgn_signal(chain: str, address: str) -> list[dict[str, Any]]:
    data = _run_gmgn(["market", "signal", "--chain", chain])
    # signal returns a raw list of signal objects
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("list") or data.get("signals") or []
    return []


# ---------------------------------------------------------------------------
# Binance Meme Rush 增强
# ---------------------------------------------------------------------------

def _fetch_binance_meme_rush_for_network(network: str) -> list[dict[str, Any]]:
    """复用项目内已有的 binance_agentic.fetch_binance_meme_rush."""
    try:
        from binance_agentic import fetch_binance_meme_rush  # noqa: PLC0415
        payload = fetch_binance_meme_rush(network, rank_types=(30,))
        if isinstance(payload, dict):
            return payload.get("data") or payload.get("items") or []
        return []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# AIXBT 市场主线/叙事/注意力增强层 (免费 topics 端点, 10 分钟缓存)
# ---------------------------------------------------------------------------

AIXBT_ENABLED = os.getenv("AIXBT_LOCAL_ENABLED", "1") == "1"
AIXBT_API_KEY = os.getenv("AIXBT_API_KEY", "").strip()
AIXBT_CACHE_TTL_S = int(os.getenv("AIXBT_CACHE_TTL_SECONDS", "600"))
_aixbt_cache: dict[str, Any] = {"payload": None, "fetched_at": 0}
# AIXBT topics 端点返回 25 个主题, 比 grounding 的 top6 丰富得多 (含 clusters + frontierReport)
AIXBT_TOPICS_URL = "https://api.aixbt.tech/v2/topics"


def _aixbt_topics() -> dict[str, Any]:
    """Fetch AIXBT market topics (25, incl. clusters + frontierReport) with 10-min cache.

    Free endpoint (no key needed). Falls back to /v2/grounding if topics fails.
    Uses project's http_get_race (proxy-rotation resistant), else urllib direct.
    """
    now = time.time()
    if _aixbt_cache["payload"] and (now - _aixbt_cache["fetched_at"]) < AIXBT_CACHE_TTL_S:
        return _aixbt_cache["payload"] or {}
    headers = {"User-Agent": "market-hot-dashboard/1.0"}
    if AIXBT_API_KEY:
        headers["x-api-key"] = AIXBT_API_KEY
    for url in (AIXBT_TOPICS_URL, "https://api.aixbt.tech/v2/grounding"):
        try:
            try:
                from newsflash_sources import http_get_race  # noqa: PLC0415
                resp = http_get_race(url, headers=headers, timeout=12.0)
                payload = resp.json()
            except ImportError:
                import urllib.request
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=12) as r:
                    payload = json.loads(r.read().decode("utf-8"))
            if isinstance(payload, dict) and payload.get("data"):
                _aixbt_cache["payload"] = payload
                _aixbt_cache["fetched_at"] = now
                return payload
        except Exception:
            continue
    return _aixbt_cache["payload"] or {}


def _aixbt_topic_list() -> list[dict[str, Any]]:
    payload = _aixbt_topics()
    data = payload.get("data") if isinstance(payload, dict) else {}
    return (data or {}).get("topics") or []


def _aixbt_market_mainline_source() -> dict[str, Any]:
    """Build an AIXBT market-mainline source for AI prompt context (25 topics, incl. 情绪集群)."""
    if not AIXBT_ENABLED:
        return {}
    topics = _aixbt_topic_list()
    if not topics:
        return {}
    lines = []
    for t in topics[:12]:
        name = str(t.get("name") or "")[:100]
        summary = str(t.get("summary") or "")[:160]
        score = float(t.get("score") or 0)
        clusters = [f"{c.get('name')}:{round(float(c.get('share') or 0)*100)}%" for c in (t.get("clusters") or [])[:4]]
        fr = (t.get("frontierReport") or {}).get("preview") or ""
        lines.append(f"#{t.get('rank')} {name} (热度={score:.0f} 推文={t.get('tweetCount',0)} 作者={t.get('authorCount',0)} 情绪集群={','.join(clusters)}) {summary}")
        if fr:
            lines.append(f"    叙事: {fr[:120]}")
    content = "\n".join(lines)[:3200]
    completed_at = str((_aixbt_topics().get("data") or {}).get("completedAt") or "")[:30]
    return {
        "id": "aixbt-market-mainline",
        "source": f"aixbt-topics@{completed_at[:16] if completed_at else 'unknown'}",
        "content": content,
    }


# AIXBT clusters 到情绪维度的映射 — 注意力汇聚不只 FOMO, 猎奇/好奇/兴奋/燃/震惊/吃瓜都算
_AIXBT_EMOTION_CLUSTERS: dict[str, str] = {
    # FOMO / 追涨冲动 / 燃
    "shitposter": "FOMO/玩梗",
    "shiller": "FOMO/喊单",
    "trencher": "FOMO/战壕党",
    "perma bull": "FOMO/多头",
    "btc maxi": "FOMO/信仰",
    "airdrop farmer": "FOMO/空投",
    "yield farmer": "FOMO/挖矿",
    # 猎奇 / 好奇 / 研究驱动
    "researcher": "好奇/研究",
    "quant": "好奇/量化",
    "educator": "好奇/科普",
    "developer": "好奇/开发",
    "designer": "好奇/设计",
    "analyst": "好奇/分析",
    # 吃瓜 / 围观 / 震惊
    "media": "吃瓜/媒体",
    "event": "震惊/事件",
    "automated feed": "吃瓜/信息流",
    "nft collector": "吃瓜/收藏",
    # 权威 / 背书 / 兴奋
    "founder": "兴奋/创始人",
    "og": "兴奋/OG",
    "vc": "兴奋/VC",
    "exchange": "兴奋/交易所",
    "service provider": "兴奋/服务方",
}


def _aixbt_topic_attention_signal(symbol: str, name: str) -> dict[str, Any]:
    """判断某币是否命中 AIXBT 热点主题, 并量化注意力汇聚 + 情绪类型.

    情绪不单指 FOMO: 猎奇/好奇/兴奋/燃/震惊/吃瓜 — 任何能让人聚焦、注意力汇聚的情绪都算。
    判断核心是「注意力是否汇聚」, 而非情绪是不是 FOMO。

    返回:
      matched       是否命中热点
      attention     注意力状态: converging(汇聚中) / formed(已聚焦) / cold(未命中)
      emotionTypes  命中的情绪类型列表 (可多个)
      dominant      主导情绪类型
      reason        一句话理由
    """
    if not AIXBT_ENABLED:
        return {"matched": False}
    symbol_u = str(symbol or "").upper()
    name_u = str(name or "").upper()
    for t in _aixbt_topic_list():
        topic_name = str(t.get("name") or "")
        summary = str(t.get("summary") or "")
        blob = (topic_name + " " + summary).upper()
        hit = False
        for key in (symbol_u, name_u):
            if key and len(key) >= 2 and re.search(rf"\b{re.escape(key)}\b", blob):
                hit = True
                break
        if not hit:
            continue
        clusters = {str(c.get("name") or "").lower(): float(c.get("share") or 0) for c in (t.get("clusters") or [])}
        author_count = int(float(t.get("authorCount") or 0))
        tweet_count = int(float(t.get("tweetCount") or 0))
        # 识别命中的情绪类型 (占比 > 3% 的集群都算, 一个主题可带多种情绪)
        emotion_types: list[str] = []
        dominant = "吃瓜/围观"
        dominant_share = 0.0
        for cluster_key, label in _AIXBT_EMOTION_CLUSTERS.items():
            share = clusters.get(cluster_key, 0.0)
            if share >= 0.03:
                if label not in emotion_types:
                    emotion_types.append(label)
            if share > dominant_share:
                dominant_share = share
                dominant = label
        if not emotion_types:
            emotion_types = ["吃瓜/围观"]
        # 注意力汇聚判断: 独立作者数 + 推文量 + 热度分综合
        if author_count >= 20 and tweet_count >= 30:
            attention = "formed"  # 已聚焦 (足够多独立作者在讨论)
        elif author_count >= 5 or tweet_count >= 10:
            attention = "converging"  # 汇聚中
        else:
            attention = "converging"
        return {
            "matched": True,
            "topicName": topic_name[:120],
            "attention": attention,
            "emotion": "ignited" if attention == "formed" else "forming",  # 兼容旧字段
            "emotionTypes": emotion_types,
            "dominant": dominant,
            "score": round(float(t.get("score") or 0), 1),
            "tweetCount": tweet_count,
            "authorCount": author_count,
            "clusters": {k: round(v * 100) for k, v in clusters.items()},
            "reason": f"命中 AIXBT 热点 #{t.get('rank')}「{topic_name[:50]}」：{dominant}驱动，{author_count} 个独立作者聚焦",
        }
    return {"matched": False}


# ---------------------------------------------------------------------------
# Nansen 聪明钱增强层 (需要 NANSEN_API_KEY, 无 key 自动跳过)
# ---------------------------------------------------------------------------

NANSEN_ENABLED = os.getenv("NANSEN_LOCAL_ENABLED", "1") == "1"
NANSEN_API_KEY = os.getenv("NANSEN_API_KEY", "").strip()


def _nansen_token_flow(chain: str, address: str) -> dict[str, Any]:
    """Fetch Nansen TGM flow intelligence for a token. Returns {} if no key or error.

    Nansen chain names differ from gmgn's: bsc/eth/base/sol use their own slugs.
    """
    if not (NANSEN_ENABLED and NANSEN_API_KEY):
        return {}
    chain_map = {"bsc": "bsc", "eth": "ethereum", "base": "base", "solana": "solana", "sol": "solana"}
    nchain = chain_map.get(chain)
    if not nchain:
        return {}
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.nansen.ai/api/v1/tgm/flow-intelligence",
            data=json.dumps({"chain": nchain, "token_address": address, "timeframe": "1d"}).encode(),
            headers={"Content-Type": "application/json", "apikey": NANSEN_API_KEY},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}


def _nansen_source(chain: str, address: str) -> dict[str, Any]:
    """Build a Nansen smart-money source entry; empty dict when unavailable."""
    data = _nansen_token_flow(chain, address)
    if not data:
        return {}
    lines = []
    for item in (data.get("data") or [])[:6]:
        lines.append(json.dumps({k: item.get(k) for k in
            ("holder_type", "inflow_usd", "outflow_usd", "netflow_usd", "holders")
            if k in item}, ensure_ascii=False)[:200])
    content = "\n".join(lines)[:1200]
    if not content.strip():
        return {}
    return {"id": "nansen-flow", "source": "nansen-tgm-flow-intelligence", "content": content}


# ---------------------------------------------------------------------------
# 核心: 对单条候选做 GMGN 数据增强 + AI 分析
# ---------------------------------------------------------------------------

# 本地评分器开关与 AI 辅助门槛
LOCAL_SCORE_ENABLED = os.getenv("GMGN_LOCAL_SCORE_ENABLED", "1") == "1"
# selectedScore（昨天评分器，流动性+成交额加权）>= 此值才算「有苗头」，才允许调 DeepSeek 辅助
AI_ASSIST_MIN_LOCAL_SCORE = int(os.getenv("GMGN_LOCAL_AI_ASSIST_MIN_SCORE", "60"))
# 有苗头还必须有至少一个正向信号 (聪明钱/AIXBT 热点), 才真正调 AI
AI_ASSIST_REQUIRE_SIGNAL = os.getenv("GMGN_LOCAL_AI_ASSIST_REQUIRE_SIGNAL", "1") == "1"


def _collect_gmgn_data(chain: str, address: str) -> dict[str, Any]:
    """一次性拉取战壕新币的 GMGN 只读数据, 返回结构化结果 (供评分 + sources 共用).

    仅调用核心接口: info + security。holders/traders/dev_holders 对评分贡献有限，
    跳过它们可大幅减少 gmgn-cli 调用次数，加快分析速度。
    """
    # 使用线程池并发调用 core interfaces（info + security 对评分足够）
    result = {"info": {}, "security": {}, "smart_holders": [], "smart_traders": [], "dev_holders": []}
    with ThreadPoolExecutor(max_workers=2) as executor:
        f_info = executor.submit(lambda: _gmgn_token_info(chain, address))
        f_sec = executor.submit(lambda: _gmgn_security(chain, address))
        try:
            result["info"] = f_info.result(timeout=15) or {}
        except Exception:
            result["info"] = {}
        try:
            result["security"] = f_sec.result(timeout=15) or {}
        except Exception:
            result["security"] = {}
    return result


def _fraction(v: Any) -> float:
    """GMGN 税率/占比字段是 [0,1] 小数, 转成百分比数值; 空/非法返回 0."""
    try:
        return float(v) * 100.0
    except (TypeError, ValueError):
        return 0.0


_bw_hot_cache: dict[str, Any] = {"symbols": None, "loaded_at": 0.0}


def _binance_wallet_hot_symbols_cached() -> set[str]:
    """缓存币安钱包热榜 symbol 集合 (60s TTL, 避免每批重复读文件)。"""
    now = time.time()
    if _bw_hot_cache["symbols"] is not None and (now - _bw_hot_cache["loaded_at"]) < 60:
        return _bw_hot_cache["symbols"]
    try:
        from score_trench_board_batch import _load_binance_wallet_hot_symbols  # noqa: PLC0415
        _bw_hot_cache["symbols"] = _load_binance_wallet_hot_symbols()
    except Exception:
        _bw_hot_cache["symbols"] = set()
    _bw_hot_cache["loaded_at"] = now
    return _bw_hot_cache["symbols"]


def score_trench_from_candidate(row: dict[str, Any], *, chain: str, symbol: str) -> dict[str, Any]:
    """复用昨天评分器 score_trench_board_batch.score_trench_row。

    昨天的评分器综合了叙事(gmgnNarrative/narrativeContext)、情绪(AIXBT)、流动性、
    聪明钱、KOL、热度(币安钱包热榜)、生态币背书(OG/官网/社交) 等全部维度，数据都来自
    战壕榜 candidate 已有字段 (launchFacts/metrics/filterSignals/walletProfile)，
    零 gmgn-cli 调用。这里直接复用，不再重写评分逻辑。
    """
    from score_trench_board_batch import score_trench_row  # noqa: PLC0415
    bw_hot = _binance_wallet_hot_symbols_cached()
    return score_trench_row(row, bw_hot)


def score_trench_locally(data: dict[str, Any], *, chain: str, symbol: str) -> dict[str, Any]:
    """纯本地评分器 — 套用 GMGN contract-dd 的 0-100 安全评分框架 + 信号加分.

    零 AI 消耗。返回:
      score     0-100 安全分 (从 100 扣)
      red_flags 红旗列表
      signals   正向信号列表 (聪明钱/热点/情绪点燃)
      verdict   本地结论: avoid / weak / watch
      promising 是否「有苗头」— 安全过关且有正向信号, 值得 DeepSeek 辅助
      narrative 叙事/注意力信号 (AIXBT 命中 + 情绪状态)
    """
    security = data.get("security") or {}
    info = data.get("info") or {}
    stat = info.get("stat") if isinstance(info.get("stat"), dict) else {}
    pool = info.get("pool") if isinstance(info.get("pool"), dict) else {}
    smart_holders = data.get("smart_holders") or []
    smart_traders = data.get("smart_traders") or []
    name = str(info.get("name") or symbol or "")

    score = 100
    red_flags: list[str] = []
    signals: list[str] = []

    # --- 硬闸: 蜜罐 / 黑名单 直接 0 分 ---
    is_honeypot = bool(security.get("is_honeypot"))
    is_blacklist = bool(security.get("is_blacklist"))
    if is_honeypot:
        red_flags.append("疑似 Honeypot (只买不卖)")
        score = 0
    if is_blacklist:
        red_flags.append("黑名单拦截")
        score = 0

    # --- 合约安全 (Step 3) ---
    buy_tax = _fraction(security.get("buy_tax"))
    sell_tax = _fraction(security.get("sell_tax"))
    max_tax = max(buy_tax, sell_tax)
    if max_tax > 10:
        score -= 25
        red_flags.append(f"交易税过高 {max_tax:.0f}%")
    elif max_tax > 5:
        score -= 10
        red_flags.append(f"交易税偏高 {max_tax:.0f}%")

    open_source = security.get("is_open_source")
    if open_source is False:
        score -= 6
        red_flags.append("非开源合约")

    # 流动性 (0 且 holder 也为 0 时是数据缺失, 不是真死池 — 不扣流动性分, 单独标记)
    liquidity = float(info.get("liquidity") or pool.get("liquidity") or 0)
    if liquidity < 10_000:
        score -= 15
        red_flags.append(f"流动性过低 ${liquidity:,.0f}")
    elif liquidity < 50_000:
        score -= 6

    # 开发者发币数量
    creator_count = int(float(stat.get("creator_created_count") or 0))
    if creator_count >= 500:
        score -= 18
        red_flags.append(f"开发者已发行 {creator_count} 个币")
    elif creator_count >= 200:
        score -= 14
    elif creator_count >= 50:
        score -= 10
        red_flags.append(f"开发者多币 {creator_count}")
    elif creator_count >= 10:
        score -= 6
    elif creator_count >= 3:
        score -= 3

    # --- 持仓结构 (Step 4) ---
    top10 = max(_fraction(security.get("top_10_holder_rate")), _fraction(stat.get("top_10_holder_rate")))
    if top10 > 50:
        score -= 25
        red_flags.append(f"前10持仓集中 {top10:.0f}%")
    elif top10 > 30:
        score -= 14
        red_flags.append(f"前10持仓 {top10:.0f}%")
    elif top10 > 20:
        score -= 6

    holder_count = int(float(info.get("holder_count") or stat.get("holder_count") or 0))
    if holder_count and holder_count < 200:
        score -= 12
        red_flags.append(f"持有人极少 {holder_count}")
    elif holder_count and holder_count < 500:
        score -= 5

    # 老鼠仓/抢跑/捆绑/夹子/机器人占比 (stat 块)
    stat_checks = [
        ("creator_hold_rate", 5, 20, "开发者持仓过高"),
        ("top_bundler_trader_percentage", 30, 20, "捆绑买入占比过高"),
        ("top_rat_trader_percentage", 5, 12, "老鼠仓占比过高"),
        ("top_entrapment_trader_percentage", 50, 22, "夹子交易占比过高"),
        ("bot_degen_rate", 70, 12, "机器人交易占比过高"),
        ("fresh_wallet_rate", 50, 8, "新钱包占比过高"),
    ]
    for field, threshold, penalty, label in stat_checks:
        value = _fraction(stat.get(field))
        if value > threshold:
            score -= penalty
            red_flags.append(f"{label} {value:.0f}%")

    # --- 正向信号 (不加分, 只标记「有苗头」) ---
    if smart_holders:
        signals.append(f"聪明钱持仓 {len(smart_holders)} 个地址")
    smart_buys = [t for t in smart_traders if float(t.get("buyVolumeCur") or 0) > 0]
    if smart_buys:
        signals.append(f"聪明钱买入 {len(smart_buys)} 笔")

    # AIXBT 叙事/情绪/注意力信号 (判断链外注意力状态是否改变)
    # 情绪不单指 FOMO: 猎奇/好奇/兴奋/燃/震惊/吃瓜 — 任何聚焦情绪都算
    narrative = _aixbt_topic_attention_signal(symbol, name)
    if narrative.get("matched"):
        if narrative.get("attention") == "formed":
            signals.append(f"AIXBT注意力聚焦「{narrative.get('topicName','')[:30]}」({narrative.get('dominant','')})")
        else:
            signals.append(f"AIXBT注意力汇聚中「{narrative.get('topicName','')[:30]}」({narrative.get('dominant','')})")

    score = max(0, min(100, score))

    # --- 本地结论 ---
    # 致命红旗: 蜜罐/黑名单/夹子/捆绑/老鼠仓/开发者巨量发币/极高集中 — 即使有聪明钱也不放行 AI
    fatal_flags = any(f in "".join(red_flags) for f in (
        "Honeypot", "黑名单", "夹子", "捆绑", "老鼠仓", "开发者已发行", "前10持仓集中",
    ))
    if score <= 0 or fatal_flags:
        verdict = "avoid"
    elif score < 45 or (red_flags and score < 55):
        verdict = "avoid"
    elif score < 70:
        verdict = "weak"
    else:
        verdict = "watch"

    promising = (
        verdict == "watch"
        and not fatal_flags
        and score >= AI_ASSIST_MIN_LOCAL_SCORE
        and (bool(signals) or not AI_ASSIST_REQUIRE_SIGNAL)
    )

    return {
        "score": score,
        "redFlags": red_flags,
        "signals": signals,
        "verdict": verdict,
        "promising": promising,
        "narrative": narrative,
        "maxTax": round(max_tax, 1),
        "liquidity": round(liquidity, 2),
        "holderCount": holder_count,
        "top10Rate": round(top10, 1),
        "smartHolderCount": len(smart_holders),
        "smartBuyCount": len(smart_buys),
    }


def build_local_analysis(local: dict[str, Any], *, chain: str, symbol: str) -> dict[str, Any]:
    """把本地评分结论组装成对齐 normalize_chain_ecosystem_ai_analysis 的 analysis dict.

    本地淘汰/观望的结论不含完整 frameworkAssessment (version 为空), 所以不会触发
    formal_research_worthy 弹窗 — 只落盘供页面展示, 不打扰用户。
    """
    verdict = local["verdict"]
    red = "；".join(local["redFlags"]) or "无"
    sig = "；".join(local["signals"]) or "无"
    if verdict == "avoid":
        summary = f"本地尽调 {local['score']} 分，红旗：{red}"
    elif verdict == "weak":
        summary = f"本地尽调 {local['score']} 分，信号不足，观望"
    else:
        summary = f"本地尽调 {local['score']} 分，信号：{sig}，待深度研判"
    # 本地结论不带完整 V4.9 framework version (置空), 避免被 framework_assessment_complete
    # 误判为"已完成完整框架"而触发弹窗 — 弹窗只由 DeepSeek 确认结果触发。
    framework = normalize_framework_assessment({})
    framework["version"] = ""
    return {
        "verdict": verdict,
        "confidence": min(100, max(0, local["score"])),
        "narrativeStrength": 0,
        "importance": 0,
        "identitySummary": f"{symbol} · {chain} · 本地尽调 {local['score']} 分",
        "summary": summary,
        "catalyst": sig if verdict == "watch" else "",
        "risk": red,
        "nextFocus": "等待 DeepSeek 深度研判" if local["promising"] else "",
        "tags": [symbol, chain],
        "narrative": {"thesis": "", "attention": "", "evidence": "", "invalidation": ""},
        "evidenceStatus": "insufficient",
        "evidenceRefs": [],
        "narrativeVersion": 0,
        "frameworkAssessment": framework,
        "provider": "GMGN 本地尽调",
        "researchRoute": "gmgn-local-score",
        "localScore": local,
        "alertDecision": "silent",
        "alertReason": "本地尽调结论，非 AI 确认，不弹窗",
    }


def enrich_candidate_with_gmgn(row: dict[str, Any], data: dict[str, Any] | None = None) -> dict[str, Any]:
    """用 gmgn-cli 数据增强候选 row, 返回带额外 sources 的 row (不修改原对象).

    传入 data (由 _collect_gmgn_data 抓取) 可避免重复调用 gmgn-cli。
    """
    chain = str(row.get("network") or "").lower()
    address = str(row.get("contractAddress") or "")
    symbol = str(row.get("symbol") or "")
    if not address or not chain:
        return row

    if data is None:
        data = _collect_gmgn_data(chain, address)
    gsec = data.get("security") or {}
    ginfo = data.get("info") or {}
    smart_holders = data.get("smart_holders") or []
    smart_dev_holders = data.get("dev_holders") or []
    smart_traders = data.get("smart_traders") or []

    enriched = {**row}
    extra_sources = []
    if gsec:
        risks_list = list(dict.fromkeys(row.get("risks") or []))
        # Security red flags: add risk ONLY when the flag indicates danger
        # gmgn uses boolean/numeric: is_honeypot=1/True=danger, renounced=0/False=not safe
        is_honeypot = bool(gsec.get("is_honeypot"))
        is_blacklist = bool(gsec.get("is_blacklist"))
        renounced = bool(gsec.get("renounced"))
        open_source = bool(gsec.get("is_open_source"))
        if is_honeypot:
            tag = "GMGN安全:疑似Honeypot"
            if tag not in risks_list:
                risks_list.append(tag)
        if is_blacklist:
            tag = "GMGN安全:黑名单拦截"
            if tag not in risks_list:
                risks_list.append(tag)
        if not open_source:
            tag = "GMGN安全:非开源合约"
            if tag not in risks_list:
                risks_list.append(tag)
        # Top 10 holder concentration risk
        top10 = float(gsec.get("top_10_holder_rate") or 0)
        if top10 > 0.15:
            tag = f"GMGN安全:前10持仓占比{top10*100:.1f}%"
            if tag not in risks_list:
                risks_list.append(tag)
        # Tax risk
        buy_tax = float(gsec.get("buy_tax") or 0)
        sell_tax = float(gsec.get("sell_tax") or 0)
        if buy_tax > 5 or sell_tax > 5:
            tag = f"GMGN安全:交易税买{buy_tax:.1f}%卖{sell_tax:.1f}%"
            if tag not in risks_list:
                risks_list.append(tag)
        enriched["risks"] = risks_list
        extra_sources.append({
            "id": "gmgn-security",
            "source": "gmgn-security",
            "content": json.dumps({
                "honeypot": is_honeypot,
                "blacklist": is_blacklist,
                "renounced": renounced,
                "openSource": open_source,
                "top10HolderRate": top10,
                "buyTax": buy_tax,
                "sellTax": sell_tax,
            }, ensure_ascii=False)[:800],
        })

    if ginfo:
        extra_sources.append({
            "id": "gmgn-info",
            "source": "gmgn-token-info",
            "content": json.dumps(ginfo, ensure_ascii=False)[:2000],
        })

    # smart_degen 持仓
    if smart_holders:
        summary = [
            f"{h.get('label') or h.get('address','?')}:{int(float(h.get('amount_percentage',0))*100)}% "
            f"PnL={h.get('profit',0)}  Unrealized={h.get('unrealizedProfit',0)}"
            for h in smart_holders[:8]
        ]
        extra_sources.append({
            "id": "gmgn-smart-holders",
            "source": "gmgn-holders-smart-degen",
            "content": "\n".join(summary)[:1200],
        })
    if smart_dev_holders:
        total = sum(float(h.get("amount_percentage") or 0) for h in smart_dev_holders)
        if total > 0.01:
            dev_risk = f"GMGN安全:开发者持仓占比{total*100:.1f}%"
            existing_risks = enriched.get("risks") or []
            enriched["risks"] = list(dict.fromkeys([*existing_risks, dev_risk]))
        extra_sources.append({
            "id": "gmgn-dev-holders",
            "source": "gmgn-holders-dev",
            "content": json.dumps(smart_dev_holders[:5], ensure_ascii=False)[:600],
        })

    # 聪明钱交易者
    if smart_traders:
        buys = [t for t in smart_traders if float(t.get("buyVolumeCur") or 0) > 0]
        if buys:
            extra_sources.append({
                "id": "gmgn-smart-traders",
                "source": "gmgn-traders-smart-degen",
                "content": "\n".join(
                    f"{t.get('label') or t.get('address','?')}: buy={t.get('buyVolumeCur',0)} USD"
                    for t in buys[:6]
                )[:800],
            })

    # 聪明钱持仓 + 交易合并到 walletProfile
    profile = list(dict.fromkeys(enriched.get("walletProfile") or []))
    if smart_holders:
        profile.append(f"聪明钱持仓:{len(smart_holders)}个地址,总占比{sum(float(h.get('amount_percentage',0))*100 for h in smart_holders[:10]):.1f}%")
    if smart_traders:
        profile.append(f"聪明钱交易:{len(smart_traders)}笔,其中买入{len([t for t in smart_traders if float(t.get('buyVolumeCur') or 0)>0])}笔")
    enriched["walletProfile"] = profile

    # Binance Meme Rush 热点增强
    if BINANCE_MEME_RUSH_ENABLED:
        meme_items = _fetch_binance_meme_rush_for_network(chain)
        meme_symbols = {str(item.get("symbol") or "").upper() for item in meme_items if item.get("symbol")}
        if symbol.upper() in meme_symbols:
            tag = f"热点:Binance {chain} Meme Rush 榜单"
            reasons = list(dict.fromkeys(enriched.get("reasons") or []))
            if tag not in reasons:
                reasons.append(tag)
            enriched["reasons"] = reasons
        extra_sources.append({
            "id": "gmgn-meme-rush",
            "source": "binance-meme-rush",
            "content": json.dumps(meme_items[:10], ensure_ascii=False)[:1500] if meme_items else "",
        })

    # AIXBT 市场主线 / 催化剂情报层 (免费, 10分钟缓存; 自动注入每个候选)
    aixbt_source = _aixbt_market_mainline_source()
    if aixbt_source:
        extra_sources.append(aixbt_source)

    # Nansen 聪明钱资金流层 (需要 NANSEN_API_KEY, 无 key 静默跳过)
    nansen_source = _nansen_source(chain, address)
    if nansen_source:
        extra_sources.append(nansen_source)

    # 附加 sources (list concat, not dict.fromkeys since values are dicts)
    existing_sources = enriched.get("sources") or []
    enriched["sources"] = list(existing_sources) + list(extra_sources)
    return enriched


def analyze_gmgn_candidates(rows: list[dict[str, Any]], *, lane: str = "gmgn-local") -> dict[str, dict[str, Any]]:
    """增强候选数据后, 复用 analyze_fast_onchain_candidates 做完整 V4.9 AI 分析.

    注意: 真正的回写入口是 run_gmgn_local_batch (由 FastResearch.run_gmgn_local_batch
    调度循环驱动), 本函数仅做数据增强, 返回 {key: enhanced_row}。
    """
    enhanced = {}
    for row in rows:
        e = enrich_candidate_with_gmgn(row)
        key = str(e.get("key") or f"{e.get('network')}:{e.get('contractAddress')}")
        enhanced[key] = e
    return enhanced


# ---------------------------------------------------------------------------
# 单条回写 (供 server.py 的后台线程调用)
# ---------------------------------------------------------------------------

def _rebuild_candidate_row(job: dict[str, Any]) -> dict[str, Any]:
    """从 DB 行重建 AI 分析器期望的候选 row (contractAddress 等字段)."""
    try:
        cand = json.loads(job.get("candidate_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        cand = {}
    row = dict(cand)
    # DB 列名 contract -> 候选字段 contractAddress (与 server 的 ingest 一致)
    row.setdefault("contractAddress", job.get("contract") or cand.get("contractAddress", ""))
    row["network"] = job.get("network") or row.get("network") or ""
    row["symbol"] = job.get("symbol") or row.get("symbol") or ""
    row["firstSeenAt"] = row.get("firstSeenAt") or job.get("first_seen_at") or 0
    return row


def _write_back_scored(store, items, analysis_map, now):
    """回写 (job, base_row, local) 列表；analysis_map 有有效结论用 AI 结果，否则本地结论。"""
    written = 0
    lock = getattr(store, "_lock", None)
    _lock_ctx = lock if lock is not None else contextlib.nullcontext()
    with _lock_ctx:
        conn = store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for job, base_row, local in items:
                key = job["key"]
                chain = str(base_row.get("network") or "").lower()
                symbol = str(base_row.get("symbol") or "")
                ai_analysis = analysis_map.get(key)
                if isinstance(ai_analysis, dict) and ai_analysis.get("verdict") in {"strong", "watch", "weak", "avoid"} and ai_analysis.get("summary"):
                    analysis = {**ai_analysis, "researchRoute": "gmgn-local", "provider": "GMGN 本地 + DeepSeek 辅助"}
                else:
                    analysis = build_local_analysis(local, chain=chain, symbol=symbol)
                candidate = json.loads(job["candidate_json"] or "{}")
                candidate["decision"] = "shortlisted" if (
                    analysis.get("verdict") in {"strong", "watch"}
                    and analysis.get("evidenceStatus") in {"partial", "supported"}
                ) else candidate.get("decision")
                candidate["gmgnLocalScore"] = local
                candidate_json = json.dumps(candidate, ensure_ascii=False)
                analysis_json = json.dumps(analysis, ensure_ascii=False)
                conn.execute(
                    """UPDATE onchain_fast_jobs SET status='ready',analysis_json=?,candidate_json=?,
                       analyzed_at=?,error='',updated_at=? WHERE key=?""",
                    (analysis_json, candidate_json, now, now, key),
                )
                written += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return written


def _flush_promising_buffer(store, analyzer_fn, alert_sink_fn, now):
    """缓冲攒够 PROMISING_BATCH_SIZE 个 promising 时，一次性送 DeepSeek 并回写。"""
    with _PROMISING_BUFFER_LOCK:
        if len(_PROMISING_BUFFER) < PROMISING_BATCH_SIZE:
            return 0
        batch = list(_PROMISING_BUFFER[:PROMISING_BATCH_SIZE])
        del _PROMISING_BUFFER[:PROMISING_BATCH_SIZE]
        for job, _r, _l in batch:
            _PROMISING_BUFFERED_KEYS.discard(job["key"])
    promising_rows = []
    for job, base_row, _local in batch:
        e = dict(base_row)
        e["key"] = job["key"]
        promising_rows.append(e)
    analysis_map: dict[str, dict[str, Any]] = {}
    try:
        result = analyzer_fn(promising_rows)
        if isinstance(result, dict):
            analysis_map = result
        print(f"[GMGN-Local] DeepSeek batch flush: {len(batch)} promising -> {len(analysis_map)} analyzed", flush=True)
    except Exception as exc:
        print(f"[GMGN-Local] DeepSeek analysis error: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        analysis_map = {}
    written = _write_back_scored(store, batch, analysis_map, now)
    try:
        alert_sink_fn()
    except Exception:
        pass
    return written


def run_gmgn_local_batch(
    store: Any,
    analyzer_fn,
    alert_sink_fn,
    *,
    batch_size: int | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """核心入口: 本地评分主力初筛 + DeepSeek 仅对有苗头的辅助.

    流程:
      1. 扫 pending 战壕新币 (不限制年龄, 本地评分零成本, 可扫更多)
      2. 对每条拉 GMGN 数据 -> score_trench_locally 纯本地打分
      3. 分流:
         - avoid/weak  (红旗/无信号) -> 直接本地回写, 零 AI 消耗
         - promising   (安全过关 + 聪明钱/热点信号) -> 收集, 调 DeepSeek 辅助确认
      4. 回写 status='ready'; 只有 DeepSeek 确认 strong 才可能弹窗
      5. emit_alerts()

    返回统计字典: {selected, localScored, aiAssisted, written, errors}.
    """
    now = int(now_ms or time.time() * 1000)
    batch = int(batch_size or GMGN_LOCAL_BATCH)
    # 本地评分零 AI 成本, 扫的量可以比 AI 批次更大。
    # 批量放大摊薄 DeepSeek 固定开销（26 条框架规则 + schema ≈ 3000 token/次）：
    # 单轮扫 50 个 pending，本地评分后把 promising 一起送 DeepSeek，固定开销摊到几十个币上。
    scan_limit = max(batch, 50)
    conn = store._connect()
    try:
        rows = [dict(r) for r in conn.execute("""
            SELECT key, network, contract, symbol, candidate_json, status,
                   analysis_json, first_seen_at, analyzed_at, attempts
            FROM onchain_fast_jobs
            WHERE status='pending'
              AND coalesce(json_extract(candidate_json,'$.gmgnTrenchBoardMember'),0)=1
              AND NOT EXISTS (
                  SELECT 1 FROM onchain_chatgpt_research_items i
                  JOIN onchain_chatgpt_research_batches b ON b.batch_id=i.batch_id
                  WHERE i.job_key=onchain_fast_jobs.key
                    AND b.status IN ('pending','claimed','sent')
              )
              AND NOT (status='ready' AND analyzed_at>0
                       AND json_extract(analysis_json,'$.frameworkAssessment.version')!='')
            ORDER BY first_seen_at ASC
            LIMIT ?
        """, (scan_limit,))]
    finally:
        conn.close()

    # 排除已入 promising 缓冲的 key（避免重复扫描同一批）
    with _PROMISING_BUFFER_LOCK:
        buffered_keys = set(_PROMISING_BUFFERED_KEYS)
    if buffered_keys:
        rows = [r for r in rows if r["key"] not in buffered_keys]

    if not rows:
        # 没有新 pending，但缓冲可能已攒够，尝试 flush
        flushed = _flush_promising_buffer(store, analyzer_fn, alert_sink_fn, now)
        return {"selected": 0, "localScored": 0, "aiAssisted": 0, "written": flushed, "errors": 0}

    selected = len(rows)
    with _PROMISING_BUFFER_LOCK:
        buffered_now = len(_PROMISING_BUFFER)
    print(f"[GMGN-Local] Processing {selected} rows (buffer {buffered_now}/{PROMISING_BATCH_SIZE})", flush=True)
    local_scored = 0
    written = 0
    errors = 0

    # --- 阶段 1: 逐条本地评分 (零 AI)，promising 入缓冲，其余收集待本地回写 ---
    scored: list[tuple[dict, dict, dict]] = []  # (job, base_row, local) 不 promising 的
    for idx, job in enumerate(rows):
        base_row = _rebuild_candidate_row(job)
        chain = str(base_row.get("network") or "").lower()
        address = str(base_row.get("contractAddress") or "")
        symbol = str(base_row.get("symbol") or "")
        if not address or not chain:
            continue
        print(f"[GMGN-Local] [{idx+1}/{selected}] Scoring {symbol}...", flush=True)
        # 直接读战壕榜 candidate 已有数据评分，零 gmgn-cli 调用（不再拉 info/security/holders/traders）
        local = score_trench_from_candidate(base_row, chain=chain, symbol=symbol) if LOCAL_SCORE_ENABLED else None
        if local is None:
            local = {"score": 50, "redFlags": [], "signals": [], "verdict": "weak", "promising": False}
        local_scored += 1
        if local.get("promising"):
            # 有苗头的进缓冲，攒够 PROMISING_BATCH_SIZE 才一次性送 DeepSeek（摊薄固定开销）
            with _PROMISING_BUFFER_LOCK:
                _PROMISING_BUFFER.append((job, base_row, local))
                _PROMISING_BUFFERED_KEYS.add(job["key"])
        else:
            scored.append((job, base_row, local))
        print(f"[GMGN-Local] [{idx+1}/{selected}] {symbol}: score={local.get('score')}, promising={local.get('promising')}", flush=True)

    # --- 阶段 2: 回写不 promising 的（本地结论，零 AI） ---
    if scored:
        written = _write_back_scored(store, scored, {}, now)

    # --- 阶段 3: 缓冲攒够就 flush 送 DeepSeek 一批 ---
    flushed = _flush_promising_buffer(store, analyzer_fn, alert_sink_fn, now)

    return {
        "selected": selected,
        "localScored": local_scored,
        "aiAssisted": flushed,
        "written": written + flushed,
        "errors": errors,
    }


def should_run_gmgn_local(now_ms: int, last_run_ms: int, interval_s: int = GMGN_LOCAL_INTERVAL_S) -> bool:
    """冷却计时器, 避免高频调用."""
    return (now_ms - last_run_ms) >= interval_s * 1000
