"""Persistent new-launch intake, first-screen timings and bounded background AI.

The 60-second target starts when a source is received. Creation-to-discovery
latency is recorded separately; neither provider indexing nor AI is guaranteed.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from chain_ecosystem_monitor import (
    _now_ms, _onchain_address, evaluate_onchain_candidate,
    fetch_dexscreener_assets, normalize_onchain_dexscreener,
    fetch_dexscreener_token,
)
from onchain_research_framework import (
    FRAMEWORK_VERSION,
    build_candidate_framework_snapshot,
    framework_assessment_complete,
    golden_leader_alert_decision,
    promote_framework_candidate,
)

FLAP_PORTAL = "0xe2ce6ab80874fa9fa2aae65d277dd6b8e65c9de0"
FOUR_MANAGER = "0x5c952063c7fc8610ffdb798152d69f0b9550762b"
NARRATIVE_VERSION = 6
DISCORD_MONITOR_PURGE_MIGRATION = "remove-discord-monitor-input-v1"
BREAKOUT_VERSION = 2
BREAKOUT_MAX_POOL_AGE_MINUTES = 180
BREAKOUT_VISIBLE_MS = 2 * 60 * 60_000
BREAKOUT_PER_NETWORK_LIMIT = 2
NEWS_TRIGGER_VERSION = 1
NEWS_TRIGGER_VISIBLE_MS = 72 * 60 * 60_000
NEWS_RESONANCE_VERSION = 1
CHAT_EVIDENCE_VISIBLE_MS = 48 * 60 * 60_000
CHAT_EVIDENCE_MAX_ITEMS = 8
AI_RECONNECT_RETRY_LIMIT = 24
AI_RECONNECT_PENDING_ERROR = "AI 已重新连接，等待重新分析"
AI_RECONNECT_DEFERRED_ERROR = "AI 重连批次已满，保留到后续启动再分析"
NEWS_TRIGGER_SYMBOL_STOPWORDS = {
    "ABOUT", "AFTER", "AI", "ALPHA", "ANTHROPIC", "API", "BEFORE", "BINANCE", "BITCOIN", "BNB", "BSC", "BTC",
    "BLOCKBEATS", "CHAIN", "COIN", "COINBASE", "CONTRACT", "CRYPTO", "DISCORD", "ETH",
    "ETF", "FORMER", "IPO", "LAUNCH", "LISTING", "MARKET", "MEME", "NEWS", "OPENAI", "ROBINHOOD", "RWA",
    "SOL", "SOLANA", "STOCK", "TOKEN", "TRADING", "UPDATE", "USD", "USDC", "USDT", "WITH",
}
CHAT_EVIDENCE_SYMBOL_STOPWORDS = NEWS_TRIGGER_SYMBOL_STOPWORDS | {
    "CA", "DAI", "DEX", "FDUSD", "GM", "MC", "NFT", "PAIREX", "TUSD", "USDG",
}
NEWS_TRIGGER_EVENT_RE = re.compile(
    r"(?:meme|热点|热度|热议|爆火|走红|刷屏|叙事|事件|宣布|发布|推出|上线|上币|发币|"
    r"辞职|离职|争议|声明|合作|收购|投资|融资|空投|回购|launch|listing|listed|"
    r"viral|trend|quit|resign|announce|release)",
    re.I,
)


def _news_timestamp_ms(value):
    try:
        timestamp = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    return timestamp * 1000 if 0 < timestamp < 10_000_000_000 else timestamp


def _news_identity_terms(news):
    """Build bounded exact lookup keys before the stricter context matcher."""
    title = str(news.get("title") or "")[:240]
    content = str(news.get("content") or "")[:400]
    terms = {
        token.casefold()
        for token in re.findall(r"(?<![A-Za-z0-9])([A-Za-z0-9][A-Za-z0-9._-]{1,23})(?![A-Za-z0-9])", f"{title} {content}")
    }
    cjk_noise = {"代币", "币种", "快讯", "消息", "热点", "热度", "热议", "事件", "市场", "交易", "发布", "推出", "上线", "上币", "宣布"}
    for span in re.findall(r"[\u3400-\u9fff]{2,24}", title):
        for width in range(2, min(16, len(span)) + 1):
            for start in range(0, len(span) - width + 1):
                term = span[start:start + width]
                if term not in cjk_noise:
                    terms.add(term)
    return sorted(
        (term for term in terms if 2 <= len(term) <= 24),
        key=lambda term: (len(term), term),
    )[:2000]


def _candidate_news_identity(row, news):
    """Return an explicit, deterministic news/candidate identity match."""
    title = str(news.get("title") or "")
    content = str(news.get("content") or "")
    text = f"{title} {content}".strip()
    if not text:
        return ""
    contract = _onchain_address(row.get("contractAddress"), row.get("network"))
    contracts = {
        _onchain_address(value, row.get("network"))
        for value in (news.get("contracts") or [])
        if _onchain_address(value, row.get("network"))
    }
    if contract and contract in contracts:
        return "news-contract-explicit"
    if not NEWS_TRIGGER_EVENT_RE.search(text):
        return ""

    symbol = str(row.get("symbol") or "").strip()
    name = str(row.get("name") or "").strip()

    def mentioned(value, *, ticker=False):
        value = str(value or "").strip()
        if len(value) < 2:
            return False
        if re.search(r"[\u3400-\u9fff]", value):
            return value.casefold() in text.casefold()
        if not re.search(r"[A-Za-z]", value):
            return False
        escaped = re.escape(value)
        if not re.search(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", text, re.I):
            return False
        upper = value.upper()
        if ticker and upper in NEWS_TRIGGER_SYMBOL_STOPWORDS:
            explicit = (
                re.search(rf"\${escaped}(?![A-Za-z0-9])", text, re.I)
                or re.search(rf"(?<![A-Za-z0-9]){escaped}\s*(?:/|-)?\s*(?:USDT|USDC|USD|WETH|ETH|BNB|SOL)(?![A-Za-z0-9])", text, re.I)
                or re.search(rf"(?<![A-Za-z0-9]){escaped}\s*(?:币|代币)", text, re.I)
            )
            return bool(explicit)
        return bool(ticker or len(value) >= 4)

    def explicit_ticker(value):
        value = str(value or "").strip()
        if not value or re.search(r"[\u3400-\u9fff]", value):
            return False
        escaped = re.escape(value)
        return bool(
            re.search(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", text)
            or re.search(rf"\${escaped}(?![A-Za-z0-9])", text, re.I)
            or re.search(rf"(?<![A-Za-z0-9]){escaped}\s*(?:/|-)?\s*(?:USDT|USDC|USD|WETH|ETH|BNB|SOL)(?![A-Za-z0-9])", text, re.I)
        )

    if mentioned(symbol, ticker=True):
        return (
            "same-chain-symbol-unverified"
            if len(news.get("networks") or []) == 1 or explicit_ticker(symbol)
            else "news-name-contract-unverified"
        )
    if name.casefold() != symbol.casefold() and mentioned(name):
        return "news-name-contract-unverified"
    return ""


def _news_resonance_confidence(identity_status, published_at, now):
    age_hours = max(0.0, (int(now) - int(published_at or now)) / 3_600_000)
    base = 100 if identity_status == "news-contract-explicit" else 92 if identity_status == "same-chain-symbol-unverified" else 84
    return max(60, int(round(base - age_hours * 0.15)))


class ResearchCapacityBusy(TimeoutError):
    """No model work began: keep queued without consuming a failure attempt."""


def story_text(evidence):
    return str(evidence.get("content") or evidence.get("text") or "")


def chat_evidence_symbol(value):
    symbol = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,19}", symbol):
        return ""
    if symbol in CHAT_EVIDENCE_SYMBOL_STOPWORDS or symbol == "FE0F" or re.fullmatch(r"1F[0-9A-F]{3,6}", symbol):
        return ""
    return symbol


def _research_ranking_number(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and abs(number) != float("inf") else 0.0


def research_recommendation_sort_key(row):
    """Rank formal recommendations from strongest evidence to weakest."""
    analysis = row.get("aiAnalysis") if isinstance(row.get("aiAnalysis"), dict) else {}
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    news_signal = row.get("newsSignal") if isinstance(row.get("newsSignal"), dict) else {}
    tier_rank = {"ai-recommended": 0, "news-triggered": 1}.get(str(row.get("researchTier") or ""), 2)
    verdict_rank = {"strong": 0, "watch": 1}.get(str(analysis.get("verdict") or ""), 2)
    evidence_rank = {"supported": 0, "partial": 1}.get(str(analysis.get("evidenceStatus") or ""), 2)
    return (
        tier_rank,
        verdict_rank,
        evidence_rank,
        -_research_ranking_number(analysis.get("narrativeStrength")),
        -_research_ranking_number(analysis.get("confidence")),
        -_research_ranking_number(row.get("selectedScore")),
        -_research_ranking_number(metrics.get("liquidityUsd")),
        -_research_ranking_number(metrics.get("volumeH1Usd")),
        -int(news_signal.get("publishedAt") or row.get("firstSeenAt") or 0),
        candidate_key(row),
    )


def sort_research_recommendations(rows):
    return sorted((row for row in rows if isinstance(row, dict)), key=research_recommendation_sort_key)


SAME_SYMBOL_IDENTITY_STATUSES = {
    "official-ca-confirmed", "personal-x-explicit", "news-contract-explicit",
    "same-chain-symbol-unverified", "event-name-contract-unverified",
    "news-name-contract-unverified", "news-contract-chain-unverified",
}


def resolve_same_symbol_leaders(rows):
    """Keep one evidence-backed leader when one story maps to same-name contracts."""
    materialized = [dict(row) for row in rows if isinstance(row, dict)]
    groups = {}
    for row in materialized:
        symbol = str(row.get("symbol") or "").strip().upper()
        network = str(row.get("network") or "").strip().lower()
        evidence = row.get("researchEvidence") if isinstance(row.get("researchEvidence"), dict) else {}
        story_identity = str(evidence.get("url") or "").strip().lower()
        if not story_identity and evidence:
            story_identity = hashlib.sha1(json.dumps({
                "source": evidence.get("source"),
                "publishedAt": evidence.get("publishedAt"),
                "text": story_text(evidence)[:500],
            }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if symbol and network:
            groups.setdefault((network, symbol, story_identity), []).append(row)
    suppressed = []
    visible_ids = {id(row) for row in materialized}
    strong_identity = {"official-ca-confirmed", "personal-x-explicit", "news-contract-explicit"}
    for group in groups.values():
        for row in group:
            row["sameSymbolContractCount"] = len(group)
            status = str((row.get("researchEvidence") or {}).get("identityStatus") or "")
            row["identityAmbiguous"] = status in {
                "same-chain-symbol-unverified", "event-name-contract-unverified",
                "news-name-contract-unverified", "news-contract-chain-unverified",
            } or (len(group) > 1 and status not in strong_identity)
        if len(group) < 2:
            continue
        statuses = {
            str((row.get("researchEvidence") or {}).get("identityStatus") or "")
            for row in group
        }
        # Pure market-only breakouts retain every contract with an ambiguity
        # warning.  Suppression starts only when a shared story/name is present.
        if not statuses & SAME_SYMBOL_IDENTITY_STATUSES:
            continue

        def rank(row):
            evidence = row.get("researchEvidence") if isinstance(row.get("researchEvidence"), dict) else {}
            status = str(evidence.get("identityStatus") or "")
            identity_rank = 3 if status == "official-ca-confirmed" else 2 if status in {
                "personal-x-explicit", "news-contract-explicit"
            } else 0
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            wallet = row.get("walletProfile") if isinstance(row.get("walletProfile"), dict) else {}
            return (
                identity_rank,
                _research_ranking_number(metrics.get("marketCapUsd") or metrics.get("fdvUsd")),
                _research_ranking_number(metrics.get("liquidityUsd")),
                _research_ranking_number(metrics.get("volumeH1Usd") or metrics.get("volumeH24Usd")),
                _research_ranking_number(wallet.get("holders") or metrics.get("holders")),
                -int(row.get("poolCreatedAt") or row.get("firstSeenAt") or 0),
                candidate_key(row),
            )

        leader = max(group, key=rank)
        leader["sameSymbolRole"] = "leader-candidate"
        leader_status = str((leader.get("researchEvidence") or {}).get("identityStatus") or "")
        leader["sameSymbolLeaderReason"] = (
            "原始来源已明确给出当前 CA，优先作为龙一候选"
            if leader_status in strong_identity
            else "按市值、流动性、成交、持币广度与成立时间综合选出龙一候选"
        )
        for row in group:
            if row is leader:
                continue
            row["sameSymbolRole"] = "challenger"
            row["sameSymbolLeaderReason"] = "同名竞争中暂未领先，隐藏以避免重复噪音"
            visible_ids.discard(id(row))
            suppressed.append(row)
    return [row for row in materialized if id(row) in visible_ids], suppressed


def paginate_research(research, page=1, page_size=12):
    """A page bounds transfer/rendering only, never screening or AI eligibility."""
    result = dict(research)
    # Re-sort at response time as well as during research generation. This keeps
    # persisted snapshots and older caches from leaking a stale ranking into UI.
    selected = sort_research_recommendations(research.get("selected") or [])
    size = max(1, min(50, int(page_size)))
    pages = max(1, (len(selected) + size - 1) // size)
    current = max(1, min(pages, int(page)))
    result["selected"] = selected[(current - 1) * size:current * size]
    result["pagination"] = {"page": current, "pageSize": size, "pages": pages, "total": len(selected)}
    result["selectedTotal"] = len(selected)
    provisional = research.get("provisional") or []
    # This lane is deliberately small and always stays on the first screen. It
    # is not mixed into the formal AI recommendation pagination.
    result["provisional"] = provisional[:8]
    result["provisionalTotal"] = len(provisional)
    # Observation/AI backlogs are counts, not a wall of low-information coins.
    result["watchingCount"] = research.get("watchingCount", len(research.get("watching") or []))
    result["watching"] = []
    history = research.get("recommendationHistory") or []
    # Recommendation history has its own requested page, independent of selections.
    history_page = max(1, min(max(1, (len(history) + size - 1) // size), int(research.get("historyPage") or 1)))
    result["recommendationHistory"] = history[(history_page - 1) * size:history_page * size]
    result["historyPagination"] = {"page": history_page, "pages": max(1, (len(history) + size - 1) // size), "total": len(history)}
    return result


def research_worthy(analysis):
    return (analysis.get("verdict") in {"strong", "watch"}
            and analysis.get("evidenceStatus") in {"partial", "supported"}
            and bool(analysis.get("evidenceRefs"))
            and all((analysis.get("narrative") or {}).get(field)
                    for field in ("thesis", "attention", "evidence", "invalidation")))


def formal_research_worthy(analysis):
    """A formal Today's Research recommendation must finish the V4.4 review."""
    return research_worthy(analysis) and framework_assessment_complete(analysis)


def news_trigger_signal(row, *, now_ms=None):
    """Return a fresh event-first signal without pretending AI review is complete."""
    signal = row.get("newsSignal") if isinstance(row.get("newsSignal"), dict) else {}
    observed_at = int(row.get("newsObservedAt") or signal.get("observedAt") or 0)
    now = int(now_ms or _now_ms())
    if int(signal.get("version") or 0) != NEWS_TRIGGER_VERSION:
        return {}
    if not observed_at or not 0 <= now - observed_at <= NEWS_TRIGGER_VISIBLE_MS:
        return {}
    if row.get("decision") != "shortlisted":
        return {}
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    liquidity = float(metrics.get("liquidityUsd") or 0)
    volume_h1 = float(metrics.get("volumeH1Usd") or 0)
    transactions_h1 = int(float(metrics.get("transactionsH1") or 0))
    identity_status = str(signal.get("identityStatus") or (row.get("researchEvidence") or {}).get("identityStatus") or "")
    exact_contract = identity_status == "news-contract-explicit"
    minimum_liquidity = 5_000 if exact_contract else 20_000
    minimum_volume = 5_000 if exact_contract else 10_000
    minimum_transactions = 8 if exact_contract else 50
    if liquidity < minimum_liquidity or (volume_h1 < minimum_volume and transactions_h1 < minimum_transactions):
        return {}
    return signal


def activate_news_trigger(row):
    """Let a real news event promote a tradable candidate before AI finishes."""
    result = dict(row)
    if result.get("decision") == "filtered" or not isinstance(result.get("newsSignal"), dict):
        return result
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    signal = result.get("newsSignal") or {}
    identity_status = str(signal.get("identityStatus") or (result.get("researchEvidence") or {}).get("identityStatus") or "")
    exact_contract = identity_status == "news-contract-explicit"
    liquid = float(metrics.get("liquidityUsd") or 0) >= (5_000 if exact_contract else 20_000)
    active = (float(metrics.get("volumeH1Usd") or 0) >= (5_000 if exact_contract else 10_000)
              or int(float(metrics.get("transactionsH1") or 0)) >= (8 if exact_contract else 50))
    if liquid and active:
        result["decision"] = "shortlisted"
        result["selectedScore"] = max(62, float(result.get("selectedScore") or 0))
    return result


def breakout_research_signal(row):
    """Return a conservative market-only signal while narrative identity is pending.

    This is intentionally much stricter than the ordinary quantitative
    shortlist. It makes an exceptional launch visible without presenting it as
    an AI recommendation or sending a desktop trade alert.
    """
    if row.get("decision") != "shortlisted":
        return {}
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    score = float(row.get("selectedScore") or 0)
    confidence = float(row.get("confidence") or 0)
    age_minutes = float(row.get("ageMinutes") or 0)
    liquidity = float(metrics.get("liquidityUsd") or 0)
    volume_h1 = float(metrics.get("volumeH1Usd") or 0)
    transactions_h1 = int(float(metrics.get("transactionsH1") or 0))
    buys_h1 = int(float(metrics.get("buysH1") or 0))
    sells_h1 = int(float(metrics.get("sellsH1") or 0))
    market_providers = sorted(set(row.get("providers") or []) & {"geckoterminal", "dexscreener"})
    if not (0 <= age_minutes <= BREAKOUT_MAX_POOL_AGE_MINUTES):
        return {}
    if score < 85 or confidence < 85 or liquidity < 30_000:
        return {}
    if volume_h1 < max(200_000, liquidity * 1.5) or transactions_h1 < 500:
        return {}
    if len(market_providers) < 2:
        return {}
    if buys_h1 + sells_h1 and sells_h1 > max(20, buys_h1 * 3):
        return {}
    return {
        "version": BREAKOUT_VERSION,
        "tier": "quantitative-breakout",
        "score": round(score, 1),
        "liquidityUsd": round(liquidity, 2),
        "volumeH1Usd": round(volume_h1, 2),
        "transactionsH1": transactions_h1,
        "providers": market_providers,
        "reason": "双行情源确认量价显著爆发，事件起因与合约身份仍待核验",
    }


def evidence_digest(row):
    evidence = row.get("researchEvidence") or {}
    chat = row.get("crossValidation") or {}
    framework = row.get("frameworkSnapshot") or {}
    content = {"story": {"content": story_text(evidence), **{key: evidence.get(key) for key in ("url", "identityStatus")}},
               "description": (row.get("narrativeContext") or {}).get("description") or "",
               "clues": [reason for reason in row.get("reasons", []) if reason.startswith("外部线索：") and "链上创建事件" not in reason],
               "chat": {
                   "status": chat.get("status"),
                   "evidenceKeys": [item.get("id") for item in chat.get("evidence", [])],
                   "independentSourceCount": chat.get("independentSourceCount"),
               },
               "framework": {
                   "version": framework.get("version"),
                   "currentStage": framework.get("currentStage"),
                   "quoteSymbol": (framework.get("quoteAsset") or {}).get("symbol"),
                   "identity": (framework.get("assetIdentity") or {}).get("canonicality"),
               }}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
# Keccak256 of the event signatures in the platforms' published ABIs.
FLAP_CREATED = "0x504e7f360b2e5fe33cbaaae4c593bc55305328341bf79009e43e0e3b7f699603"
FOUR_CREATED = "0x396d5e902b675b032348d3d2e9517ee8f0c4a926603fbc075d3d282ff00cad20"


def candidate_key(row):
    network = str(row.get("network") or "")
    return f"{network}:{_onchain_address(row.get('contractAddress'), network)}"


def decode_launch_log(log, *, received_at=None):
    """Decode only the two known, non-indexed creation events (not staged tokens)."""
    if log.get("removed") or len(log.get("topics") or []) != 1:
        return None
    topic = log["topics"][0].lower()
    emitter = str(log.get("address") or "").lower()
    if (emitter, topic) not in {(FLAP_PORTAL, FLAP_CREATED), (FOUR_MANAGER, FOUR_CREATED)}:
        return None
    raw = bytes.fromhex(str(log.get("data") or "")[2:])
    def word(index):
        if len(raw) < (index + 1) * 32:
            raise ValueError("truncated launch event")
        return raw[index * 32:(index + 1) * 32]
    def number(index):
        return int.from_bytes(word(index), "big")
    def string(index):
        offset = number(index)
        if offset % 32 or offset + 32 > len(raw):
            raise ValueError("invalid ABI string offset")
        length = int.from_bytes(raw[offset:offset + 32], "big")
        if length > 4096 or offset + 32 + length > len(raw):
            raise ValueError("invalid ABI string length")
        return raw[offset + 32:offset + 32 + length].decode("utf-8", errors="replace")[:160]
    flap = topic == FLAP_CREATED
    address = "0x" + word(3 if flap else 1)[12:].hex()
    created_at = number(0 if flap else 6) * 1000
    now = int(received_at or _now_ms())
    return {
        "network": "bsc", "contractAddress": address,
        "symbol": string(5 if flap else 4), "name": string(4 if flap else 3),
        "poolCreatedAt": created_at, "firstSeenAt": now, "observedAt": now,
        "provider": "flap-event" if flap else "fourmeme-event",
        "providers": ["flap-event" if flap else "fourmeme-event"],
        "dexId": "", "metrics": {},
        "tradeUrl": f"https://web3.binance.com/en/token/bsc/{address}",
        "reasons": ["外部线索：链上创建事件；发行名称与合约已记录，题材及交易条件待核验"],
    }


class FastResearch:
    def __init__(self, store, analyzer, alert_sink, *, quote_fetcher=None, rpc=None, realtime_analyzer=None,
                 candidate_enricher=None, resonance_sink=None):
        self.store, self.analyzer, self.alert_sink = store, analyzer, alert_sink
        self.realtime_analyzer = realtime_analyzer or analyzer
        self.candidate_enricher = candidate_enricher
        self.resonance_sink = resonance_sink
        self.quote_fetcher = quote_fetcher or fetch_dexscreener_assets
        self.rpc = rpc or self._rpc
        self._stop = threading.Event()
        self._thread = None
        self._pools = None
        self._futures = {}
        self._next_launch = 0
        self._rpc_index = 0
        self._rpc_failures = 0
        self._initialized = False

    def initialize(self):
        with self.store._lock:
            conn = self.store._connect()
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS onchain_fast_jobs (
                        key TEXT PRIMARY KEY, network TEXT NOT NULL, contract TEXT NOT NULL,
                        symbol TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '',
                        candidate_json TEXT NOT NULL, first_seen_at INTEGER NOT NULL,
                        screened_at INTEGER NOT NULL, status TEXT NOT NULL,
                        started_at INTEGER NOT NULL DEFAULT 0, analyzed_at INTEGER NOT NULL DEFAULT 0,
                        next_due_at INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                        quote_due_at INTEGER NOT NULL DEFAULT 0,
                        review_requested_at INTEGER NOT NULL DEFAULT 0,
                        analysis_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                        alerted_at INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS onchain_fast_due ON onchain_fast_jobs(status, next_due_at);
                    CREATE INDEX IF NOT EXISTS onchain_fast_quote ON onchain_fast_jobs(quote_due_at);
                    CREATE INDEX IF NOT EXISTS onchain_fast_discovery ON onchain_fast_jobs(first_seen_at);
                    CREATE TABLE IF NOT EXISTS onchain_research_recommendations (
                        key TEXT PRIMARY KEY, first_recommended_at INTEGER NOT NULL,
                        candidate_json TEXT NOT NULL, analysis_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS onchain_launch_cursor (
                        source TEXT PRIMARY KEY, block INTEGER NOT NULL DEFAULT 0,
                        updated_at INTEGER NOT NULL, error TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE IF NOT EXISTS onchain_contract_clues (
                        contract TEXT PRIMARY KEY, source TEXT NOT NULL, content TEXT NOT NULL,
                        received_at INTEGER NOT NULL, next_due_at INTEGER NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0, resolved INTEGER NOT NULL DEFAULT 0,
                        event_json TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE TABLE IF NOT EXISTS onchain_research_story_matches (
                        story_key TEXT PRIMARY KEY, candidate_key TEXT NOT NULL,
                        content TEXT NOT NULL, url TEXT NOT NULL, created_at INTEGER NOT NULL,
                        match_json TEXT NOT NULL DEFAULT '{}', alerted_at INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE IF NOT EXISTS onchain_recent_news (
                        news_key TEXT PRIMARY KEY, item_id TEXT NOT NULL, source TEXT NOT NULL,
                        title TEXT NOT NULL, content TEXT NOT NULL, url TEXT NOT NULL,
                        published_at INTEGER NOT NULL, networks_json TEXT NOT NULL DEFAULT '[]',
                        contracts_json TEXT NOT NULL DEFAULT '[]', expires_at INTEGER NOT NULL,
                        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS onchain_recent_news_expiry
                        ON onchain_recent_news(expires_at, published_at);
                    CREATE INDEX IF NOT EXISTS onchain_research_candidates_symbol_ci
                        ON onchain_research_candidates(symbol COLLATE NOCASE);
                    CREATE INDEX IF NOT EXISTS onchain_research_candidates_name_ci
                        ON onchain_research_candidates(name COLLATE NOCASE);
                    CREATE TABLE IF NOT EXISTS onchain_chat_evidence (
                        evidence_key TEXT PRIMARY KEY,
                        identity_type TEXT NOT NULL,
                        identity_value TEXT NOT NULL,
                        platform TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL DEFAULT '',
                        sender TEXT NOT NULL DEFAULT '',
                        content TEXT NOT NULL DEFAULT '',
                        observed_at INTEGER NOT NULL,
                        last_seen_at INTEGER NOT NULL,
                        seen_count INTEGER NOT NULL DEFAULT 1,
                        created_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS onchain_chat_evidence_identity
                        ON onchain_chat_evidence(identity_type, identity_value, observed_at);
                    CREATE TABLE IF NOT EXISTS onchain_data_migrations (
                        name TEXT PRIMARY KEY,
                        applied_at INTEGER NOT NULL,
                        details_json TEXT NOT NULL DEFAULT '{}'
                    );
                """)
                columns = {row[1] for row in conn.execute("PRAGMA table_info(onchain_fast_jobs)")}
                # Existing reanalyses do not reveal the historic first result;
                # leave unknown timings at zero instead of inventing history.
                for column in ("first_analyzed_at", "first_started_at"):
                    if column not in columns:
                        conn.execute(f"ALTER TABLE onchain_fast_jobs ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
                identity_columns_added = False
                for column in ("symbol", "name"):
                    if column not in columns:
                        conn.execute(f"ALTER TABLE onchain_fast_jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
                        identity_columns_added = True
                if identity_columns_added:
                    conn.execute("""UPDATE onchain_fast_jobs
                        SET symbol=coalesce(json_extract(candidate_json,'$.symbol'),''),
                            name=coalesce(json_extract(candidate_json,'$.name'),'')""")
                for index_name in ("onchain_fast_jobs_symbol_ci", "onchain_fast_jobs_name_ci"):
                    index_row = conn.execute(
                        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index_name,)
                    ).fetchone()
                    if index_row and "json_extract" in str(index_row[0] or "").casefold():
                        conn.execute(f"DROP INDEX {index_name}")
                conn.execute("CREATE INDEX IF NOT EXISTS onchain_fast_jobs_symbol_ci ON onchain_fast_jobs(symbol COLLATE NOCASE)")
                conn.execute("CREATE INDEX IF NOT EXISTS onchain_fast_jobs_name_ci ON onchain_fast_jobs(name COLLATE NOCASE)")
                clue_columns = {row[1] for row in conn.execute("PRAGMA table_info(onchain_contract_clues)")}
                if "event_json" not in clue_columns:
                    conn.execute("ALTER TABLE onchain_contract_clues ADD COLUMN event_json TEXT NOT NULL DEFAULT '{}'")
                story_columns = {row[1] for row in conn.execute("PRAGMA table_info(onchain_research_story_matches)")}
                if "match_json" not in story_columns:
                    conn.execute("ALTER TABLE onchain_research_story_matches ADD COLUMN match_json TEXT NOT NULL DEFAULT '{}'")
                if "alerted_at" not in story_columns:
                    conn.execute("ALTER TABLE onchain_research_story_matches ADD COLUMN alerted_at INTEGER NOT NULL DEFAULT 0")
                self._purge_legacy_discord_monitor_data(conn)
                conn.execute("DELETE FROM onchain_recent_news WHERE expires_at<?", (_now_ms(),))
                noise_symbols = sorted(CHAT_EVIDENCE_SYMBOL_STOPWORDS)
                placeholders = ",".join("?" for _ in noise_symbols)
                removed_noise = conn.execute(
                    f"""DELETE FROM onchain_chat_evidence
                        WHERE identity_type='symbol'
                          AND (upper(identity_value) IN ({placeholders})
                               OR upper(identity_value)='FE0F'
                               OR upper(identity_value) GLOB '1F[0-9A-F]*')""",
                    noise_symbols,
                ).rowcount
                if removed_noise:
                    # Rebuild compact summaries from the remaining evidence on
                    # the normal candidate restore/refresh path.
                    conn.execute("""UPDATE onchain_fast_jobs
                        SET candidate_json=json_remove(candidate_json,'$.crossValidation')
                        WHERE json_extract(candidate_json,'$.crossValidation.status') IS NOT NULL""")
                conn.commit()
            finally:
                conn.close()
        self._initialized = True

    @staticmethod
    def _purge_legacy_discord_monitor_data(conn):
        """Remove inbound Discord evidence and every persisted row derived from it once."""
        applied = conn.execute(
            "SELECT 1 FROM onchain_data_migrations WHERE name=?",
            (DISCORD_MONITOR_PURGE_MIGRATION,),
        ).fetchone()
        if applied:
            return {}

        conn.execute("DROP TABLE IF EXISTS temp._discord_monitor_keys")
        conn.execute("DROP TABLE IF EXISTS temp._discord_monitor_candidate_ids")
        conn.execute("CREATE TEMP TABLE _discord_monitor_keys(key TEXT PRIMARY KEY)")
        conn.execute("CREATE TEMP TABLE _discord_monitor_candidate_ids(id INTEGER PRIMARY KEY)")
        conn.execute("""INSERT OR IGNORE INTO _discord_monitor_keys
            SELECT key FROM onchain_fast_jobs
            WHERE lower(coalesce(candidate_json,'')) LIKE '%discord%'
               OR lower(coalesce(analysis_json,'')) LIKE '%discord%'""")
        conn.execute("""INSERT OR IGNORE INTO _discord_monitor_candidate_ids
            SELECT id FROM onchain_research_candidates
            WHERE lower(coalesce(providers_json,'')) LIKE '%discord%'
               OR lower(coalesce(metrics_json,'')) LIKE '%discord%'
               OR lower(coalesce(reasons_json,'')) LIKE '%discord%'
               OR lower(coalesce(risks_json,'')) LIKE '%discord%'
               OR lower(coalesce(trade_url,'')) LIKE '%discord%'""")
        conn.execute("""INSERT OR IGNORE INTO _discord_monitor_candidate_ids
            SELECT c.id FROM onchain_research_candidates c
            JOIN _discord_monitor_keys d
              ON d.key=c.network || ':' || c.contract_address""")
        conn.execute("""INSERT OR IGNORE INTO _discord_monitor_keys
            SELECT c.network || ':' || c.contract_address
            FROM onchain_research_candidates c
            JOIN _discord_monitor_candidate_ids d ON d.id=c.id""")

        counts = {}

        def remove(table, where, params=()):
            counts[table] = conn.execute(f"DELETE FROM {table} WHERE {where}", params).rowcount

        remove(
            "onchain_research_story_matches",
            "candidate_key IN (SELECT key FROM _discord_monitor_keys) "
            "OR lower(coalesce(content,'')) LIKE '%discord%' "
            "OR lower(coalesce(url,'')) LIKE '%discord%' "
            "OR lower(coalesce(match_json,'')) LIKE '%discord%'",
        )
        remove(
            "onchain_research_recommendations",
            "key IN (SELECT key FROM _discord_monitor_keys) "
            "OR lower(coalesce(candidate_json,'')) LIKE '%discord%' "
            "OR lower(coalesce(analysis_json,'')) LIKE '%discord%'",
        )
        remove(
            "onchain_research_snapshots",
            "candidate_id IN (SELECT id FROM _discord_monitor_candidate_ids) "
            "OR lower(coalesce(metrics_json,'')) LIKE '%discord%' "
            "OR lower(coalesce(reasons_json,'')) LIKE '%discord%' "
            "OR lower(coalesce(risks_json,'')) LIKE '%discord%'",
        )
        remove("onchain_research_candidates", "id IN (SELECT id FROM _discord_monitor_candidate_ids)")
        remove(
            "onchain_fast_jobs",
            "key IN (SELECT key FROM _discord_monitor_keys) "
            "OR lower(coalesce(candidate_json,'')) LIKE '%discord%' "
            "OR lower(coalesce(analysis_json,'')) LIKE '%discord%'",
        )
        remove(
            "onchain_contract_clues",
            "lower(coalesce(source,'')) LIKE '%discord%' "
            "OR lower(coalesce(content,'')) LIKE '%discord%' "
            "OR lower(coalesce(event_json,'')) LIKE '%discord%'",
        )
        remove(
            "onchain_recent_news",
            "lower(coalesce(source,'')) LIKE '%discord%' "
            "OR lower(coalesce(content,'')) LIKE '%discord%' "
            "OR lower(coalesce(url,'')) LIKE '%discord%'",
        )
        remove(
            "onchain_chat_evidence",
            "lower(coalesce(platform,''))='discord' "
            "OR lower(coalesce(source,'')) LIKE '%discord%' "
            "OR lower(coalesce(content,'')) LIKE '%discord.com/channels/%'",
        )
        conn.execute(
            "INSERT INTO onchain_data_migrations(name,applied_at,details_json) VALUES(?,?,?)",
            (DISCORD_MONITOR_PURGE_MIGRATION, _now_ms(), json.dumps(counts, ensure_ascii=False, sort_keys=True)),
        )
        conn.execute("DROP TABLE IF EXISTS temp._discord_monitor_keys")
        conn.execute("DROP TABLE IF EXISTS temp._discord_monitor_candidate_ids")
        return counts

    def restore_review_queue(self):
        """Recover today's previously truncated candidates, without resetting AI caches."""
        day = time.strftime("%Y-%m-%d", time.localtime(_now_ms() / 1000))
        # A running row belongs to the previous process: recover it once during
        # startup. Live workers are never reclaimed by a wall-clock timeout.
        self._write("""UPDATE onchain_fast_jobs SET status='pending',attempts=MAX(0,attempts-1),
            next_due_at=?,error='服务重启，继续等待 AI 分析' WHERE status='running'""", (_now_ms(),))
        rows = self._query("SELECT * FROM onchain_research_candidates WHERE research_day=? AND decision='shortlisted'", (day,))
        self.ingest([self.store._onchain_candidate_row(row) for row in rows])
        self._restore_breakout_markers(day)
        # One-time upgrade of brief cached conclusions. Keep the old result while
        # enriching it, and never reset exhausted retries on subsequent restarts.
        for job in self._query("SELECT * FROM onchain_fast_jobs WHERE status='ready' AND first_seen_at>=?", (_now_ms() - 86_400_000,)):
            analysis = json.loads(job["analysis_json"])
            if int(analysis.get("narrativeVersion") or 0) < NARRATIVE_VERSION:
                self._write("UPDATE onchain_fast_jobs SET status='pending',attempts=0,next_due_at=?,review_requested_at=? WHERE key=?",
                            (_now_ms(), _now_ms(), job["key"]))
        for job in self._query("SELECT * FROM onchain_fast_jobs WHERE alerted_at>0"):
            self._write("INSERT OR IGNORE INTO onchain_research_recommendations VALUES(?,?,?,?)",
                        (job["key"], job["alerted_at"], job["candidate_json"], job["analysis_json"]))

    def retry_unavailable_after_ai_reconnect(self, limit=AI_RECONNECT_RETRY_LIMIT):
        """Retry only a small, high-priority batch after a real AI probe succeeds."""
        self.initialize()
        now = _now_ms()
        bounded_limit = max(1, min(48, int(limit or AI_RECONNECT_RETRY_LIMIT)))
        with self.store._lock:
            conn = self.store._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                # A previous process may have opened a much larger reconnect batch.
                # Put its unclaimed rows back before selecting today's bounded batch.
                conn.execute("""UPDATE onchain_fast_jobs
                    SET status='unavailable',attempts=MAX(attempts,3),next_due_at=0,
                        error=?,updated_at=?
                    WHERE status='pending' AND error=?""",
                    (AI_RECONNECT_DEFERRED_ERROR, now, AI_RECONNECT_PENDING_ERROR))
                rows = conn.execute("""SELECT key FROM onchain_fast_jobs
                    WHERE status='unavailable' AND first_seen_at>=?
                      AND coalesce(json_extract(candidate_json,'$.decision'),'')='shortlisted'
                    ORDER BY
                      CAST(coalesce(json_extract(candidate_json,'$.selectedScore'),0) AS REAL) DESC,
                      CAST(coalesce(json_extract(candidate_json,'$.observedAt'),first_seen_at,0) AS INTEGER) DESC,
                      review_requested_at DESC,key
                    LIMIT ?""", (now - 86_400_000, bounded_limit)).fetchall()
                keys = [row["key"] for row in rows]
                if keys:
                    placeholders = ",".join("?" for _ in keys)
                    conn.execute(f"""UPDATE onchain_fast_jobs
                        SET status='pending',attempts=0,next_due_at=?,review_requested_at=?,
                            error=?,updated_at=?
                        WHERE key IN ({placeholders})""",
                        (now, now, AI_RECONNECT_PENDING_ERROR, now, *keys))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return len(keys)

    def _restore_breakout_markers(self, day):
        """Backfill the first qualifying snapshot after an upgrade or restart."""
        snapshots = self._query("""SELECT c.network,c.contract_address,c.pool_created_at,c.providers_json,
            s.observed_at,s.decision,s.selected_score,s.confidence,s.metrics_json
            FROM onchain_research_snapshots s
            JOIN onchain_research_candidates c ON c.id=s.candidate_id
            WHERE c.research_day=? AND s.decision='shortlisted'
            ORDER BY s.observed_at""", (day,))
        by_key = {}
        for snapshot in snapshots:
            key = f"{snapshot['network']}:{_onchain_address(snapshot['contract_address'], snapshot['network'])}"
            if key in by_key:
                continue
            created_at = int(snapshot.get("pool_created_at") or snapshot["observed_at"])
            signal = breakout_research_signal({
                "decision": snapshot["decision"],
                "selectedScore": snapshot["selected_score"],
                "confidence": snapshot["confidence"],
                "ageMinutes": max(0, (int(snapshot["observed_at"]) - created_at) / 60_000),
                "metrics": json.loads(snapshot["metrics_json"] or "{}"),
                "providers": json.loads(snapshot["providers_json"] or "[]"),
            })
            if signal:
                by_key[key] = (int(snapshot["observed_at"]), signal)
        for job in self._query("SELECT key,candidate_json FROM onchain_fast_jobs WHERE first_seen_at>=? AND first_seen_at<?", (
                int(time.mktime(time.strptime(day, "%Y-%m-%d")) * 1000),
                int(time.mktime(time.strptime(day, "%Y-%m-%d")) * 1000) + 86_400_000)):
            row = json.loads(job["candidate_json"])
            if ((row.get("breakoutSignal") or {}).get("version") == BREAKOUT_VERSION
                    or job["key"] not in by_key):
                continue
            observed_at, signal = by_key[job["key"]]
            row.update({"breakoutObservedAt": observed_at, "breakoutSignal": signal})
            self._write("UPDATE onchain_fast_jobs SET candidate_json=?,updated_at=? WHERE key=?",
                        (json.dumps(row, ensure_ascii=False), _now_ms(), job["key"]))

    def ingest(self, rows, *, match_recent_news=True):
        """Persist all qualified candidates; queue age and page rank never expire a job."""
        now = _now_ms()
        recent_candidates = []
        with self.store._lock:
            conn = self.store._connect()
            try:
                for row in rows:
                    if not row.get("network") or not _onchain_address(row.get("contractAddress"), row.get("network")):
                        continue
                    key = candidate_key(row)
                    old = conn.execute("SELECT * FROM onchain_fast_jobs WHERE key=?", (key,)).fetchone()
                    previous = {}
                    if not old:
                        saved = conn.execute("SELECT first_seen_at FROM onchain_research_candidates WHERE network=? AND contract_address=?",
                                             (row["network"], _onchain_address(row["contractAddress"], row["network"]))).fetchone()
                        if saved:
                            row = {**row, "firstSeenAt": int(saved["first_seen_at"])}
                    if old:
                        previous = json.loads(old["candidate_json"])
                        incoming_at = int(row.get("observedAt") or row.get("lastSeenAt") or 0)
                        previous_at = int(previous.get("observedAt") or previous.get("lastSeenAt") or 0)
                        if incoming_at and incoming_at < previous_at and not row.get("researchEvidence"):
                            continue
                        row = {**row, "firstSeenAt": old["first_seen_at"],
                               "reasons": list(dict.fromkeys([
                                   *[r for r in previous.get("reasons", []) if r.startswith("外部线索：")],
                                   *row.get("reasons", [])]))[:5]}
                        if previous.get("researchEvidence") and not row.get("researchEvidence"):
                            row["researchEvidence"] = previous["researchEvidence"]
                        if previous.get("crossValidation") and not row.get("crossValidation"):
                            row["crossValidation"] = previous["crossValidation"]
                        if not row.get("narrativeContext") and previous.get("narrativeContext"):
                            row["narrativeContext"] = previous["narrativeContext"]
                        if (previous.get("breakoutObservedAt")
                                and (previous.get("breakoutSignal") or {}).get("version") == BREAKOUT_VERSION):
                            row["breakoutObservedAt"] = previous["breakoutObservedAt"]
                            row["breakoutSignal"] = previous.get("breakoutSignal") or {}
                        if (previous.get("newsObservedAt")
                                and (previous.get("newsSignal") or {}).get("version") == NEWS_TRIGGER_VERSION
                                and not row.get("newsSignal")):
                            row["newsObservedAt"] = previous["newsObservedAt"]
                            row["newsSignal"] = previous.get("newsSignal") or {}
                        if ((previous.get("newsResonance") or {}).get("version") == NEWS_RESONANCE_VERSION
                                and not row.get("newsResonance")):
                            row["newsResonance"] = previous.get("newsResonance") or {}
                        # Metadata-only repeated events must not erase acquired quotes.
                        if not row.get("metrics") and previous.get("metrics"):
                            continue
                    row = evaluate_onchain_candidate(row, now_ms=now)
                    if self.candidate_enricher:
                        try:
                            enriched = self.candidate_enricher(dict(row))
                            if isinstance(enriched, dict):
                                row = enriched
                        except Exception:
                            # External narrative enrichment is additive. A stale
                            # hotspot cache must never break the quantitative scan.
                            pass
                    row = self._attach_chat_cross_validation(row, conn, now)
                    row = activate_news_trigger(row)
                    row["frameworkSnapshot"] = build_candidate_framework_snapshot(
                        row,
                        previous=previous.get("frameworkSnapshot") if previous else None,
                        observed_at=now,
                    )
                    row = promote_framework_candidate(row)
                    breakout = breakout_research_signal(row)
                    if breakout and not row.get("breakoutObservedAt"):
                        row["breakoutObservedAt"] = now
                        row["breakoutSignal"] = breakout
                    first = int(old["first_seen_at"] if old else row.get("firstSeenAt") or now)
                    status = "pending" if row["decision"] == "shortlisted" else "screened"
                    if old and (old["status"] in {"running", "ready"} or old["attempts"] >= 3):
                        status = old["status"]
                    new_evidence = bool(old and evidence_digest(row) != evidence_digest(previous))
                    if new_evidence and row["decision"] == "shortlisted" and old["status"] != "running":
                        status = "pending"
                        conn.execute("UPDATE onchain_fast_jobs SET attempts=0,next_due_at=?,review_requested_at=? WHERE key=?", (now, now, key))
                    quote_due = now + (15_000 if now - first < 180_000 else 60_000 if now - first < 15 * 60_000 else 300_000)
                    if now - first >= 24 * 60 * 60_000:
                        quote_due = 0
                    conn.execute("""
                        INSERT INTO onchain_fast_jobs
                          (key,network,contract,symbol,name,candidate_json,first_seen_at,screened_at,status,next_due_at,quote_due_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(key) DO UPDATE SET symbol=excluded.symbol,name=excluded.name,candidate_json=excluded.candidate_json,
                          status=excluded.status, quote_due_at=CASE WHEN onchain_fast_jobs.quote_due_at>0
                            THEN MIN(onchain_fast_jobs.quote_due_at,excluded.quote_due_at) ELSE excluded.quote_due_at END,
                          updated_at=excluded.updated_at
                    """, (key, row["network"], row["contractAddress"], str(row.get("symbol") or "")[:80],
                          str(row.get("name") or "")[:180], json.dumps(row, ensure_ascii=False),
                          first, now, status, now, quote_due, now))
                    if not old or row.get("newsResonance"):
                        recent_candidates.append(dict(row))
                conn.commit()
            finally:
                conn.close()
        if match_recent_news:
            for row in recent_candidates:
                self._match_recent_news_for_candidate(row)

    @staticmethod
    def _chat_text_fingerprint(content):
        normalized = re.sub(r"\s+", " ", str(content or "")).strip().casefold()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _attach_chat_cross_validation(self, row, conn, now=None):
        """Attach compact chat corroboration without changing the market-screen decision."""
        result = dict(row)
        current = int(now or _now_ms())
        contract = _onchain_address(result.get("contractAddress"), result.get("network"))
        symbol = str(result.get("symbol") or "").strip().upper()
        conditions, params = [], []
        if contract:
            conditions.append("(identity_type='contract' AND identity_value=?)")
            params.append(contract)
        if symbol:
            conditions.append("(identity_type='symbol' AND upper(identity_value)=?)")
            params.append(symbol)
        if not conditions:
            result.pop("crossValidation", None)
            return result
        rows = [dict(item) for item in conn.execute(
            f"""SELECT * FROM onchain_chat_evidence
                WHERE ({' OR '.join(conditions)}) AND observed_at>=?
                  AND lower(coalesce(platform,''))!='discord'
                  AND lower(coalesce(source,'')) NOT LIKE '%discord%'
                ORDER BY observed_at DESC,evidence_key LIMIT ?""",
            (*params, current - CHAT_EVIDENCE_VISIBLE_MS, CHAT_EVIDENCE_MAX_ITEMS),
        ).fetchall()]
        if not rows:
            result.pop("crossValidation", None)
            return result
        source_keys = {
            "|".join((str(item.get("platform") or ""), str(item.get("source") or ""),
                      str(item.get("sender") or ""))).casefold()
            for item in rows
        }
        exact = sum(item.get("identity_type") == "contract" for item in rows)
        symbols = sum(item.get("identity_type") == "symbol" for item in rows)
        duplicates = sum(max(0, int(item.get("seen_count") or 1) - 1) for item in rows)
        independent = len(source_keys)
        corroborated = bool(exact and independent >= 2)
        if exact and independent >= 2:
            status = "multi-source-contract"
            summary = f"群聊交叉验证：{independent} 条独立讨论明确提及该 CA"
        elif exact:
            status = "single-source-contract"
            summary = f"群聊旁证：{independent} 条独立讨论明确提及该 CA，尚未形成多源确认"
        elif independent >= 2:
            status = "multi-source-symbol"
            summary = f"群聊旁证：{independent} 条独立讨论提及同名标的，CA 仍待核验"
        else:
            status = "single-source-symbol"
            summary = "群聊出现同名标的讨论，但只有单一来源且 CA 未核验"
        result["crossValidation"] = {
            "status": status,
            "corroborated": corroborated,
            "independentSourceCount": independent,
            "exactContractMentions": exact,
            "symbolOnlyMentions": symbols,
            "duplicateMentions": duplicates,
            "summary": summary,
            "latestObservedAt": max(int(item.get("last_seen_at") or item.get("observed_at") or 0) for item in rows),
            "evidence": [{
                "id": item["evidence_key"],
                "platform": item.get("platform") or "chat",
                "source": item.get("source") or "群聊",
                "sender": item.get("sender") or "群成员",
                "content": str(item.get("content") or "")[:600],
                "observedAt": int(item.get("observed_at") or 0),
                "matchType": "exact-contract" if item.get("identity_type") == "contract" else "symbol-only",
                "repeatedCount": max(1, int(item.get("seen_count") or 1)),
            } for item in rows],
        }
        return result

    def ingest_chat_context(
        self, content, source, *, platform="chat", sender="", observed_at=None,
        message_id="", contracts=None, symbols=None,
    ):
        """Store group discussion as bounded, deduped corroboration—not as a buy trigger."""
        if not self._initialized:
            return {"recorded": 0, "matchedCandidates": 0}
        platform_text = str(platform or "chat").strip().lower()[:24]
        if platform_text == "discord" or platform_text.startswith("discord-"):
            return {"recorded": 0, "matchedCandidates": 0, "ignored": "discord-monitor-disabled"}
        text = re.sub(r"\s+", " ", str(content or "")).strip()[:4000]
        if not text:
            return {"recorded": 0, "matchedCandidates": 0}
        now = _now_ms()
        observed = int(observed_at or now)
        if observed < 10_000_000_000:
            observed *= 1000
        if observed > now + 5 * 60_000 or now - observed > CHAT_EVIDENCE_VISIBLE_MS:
            return {"recorded": 0, "matchedCandidates": 0, "ignored": "stale"}
        raw_contracts = contracts if isinstance(contracts, list) else re.findall(
            r"(?<![0-9A-Za-z])(?:0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})(?![0-9A-Za-z])",
            text,
        )
        contract_values = []
        for item in raw_contracts:
            value = item.get("contractAddress") if isinstance(item, dict) else item
            address = _onchain_address(value)
            if address:
                contract_values.append(address)
        contract_values = list(dict.fromkeys(contract_values))
        symbol_values = [chat_evidence_symbol(item) for item in (symbols or [])]
        if not symbols:
            symbol_values = [chat_evidence_symbol(item) for item in re.findall(r"\$([A-Za-z][A-Za-z0-9]{1,14})", text)]
        symbol_values = list(dict.fromkeys(item for item in symbol_values if item))[:8]
        # When a message names an exact address, do not also spread its ticker
        # evidence across every unrelated same-name contract.
        identities = (
            [("contract", value) for value in contract_values]
            if contract_values else [("symbol", value) for value in symbol_values]
        )
        if not identities:
            return {"recorded": 0, "matchedCandidates": 0}
        content_hash = self._chat_text_fingerprint(text)
        source_text = str(source or "群聊")[:160]
        sender_text = str(sender or "群成员")[:120]
        recorded = 0
        matched_keys = set()
        with self.store._lock:
            conn = self.store._connect()
            try:
                for identity_type, identity_value in identities:
                    evidence_key = hashlib.sha256(
                        f"{content_hash}\n{identity_type}\n{identity_value}".encode("utf-8")
                    ).hexdigest()
                    cursor = conn.execute("""
                        INSERT INTO onchain_chat_evidence
                            (evidence_key,identity_type,identity_value,platform,source,sender,content,
                             observed_at,last_seen_at,seen_count,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,1,?)
                        ON CONFLICT(evidence_key) DO UPDATE SET
                            last_seen_at=MAX(onchain_chat_evidence.last_seen_at,excluded.last_seen_at),
                            seen_count=onchain_chat_evidence.seen_count+1
                    """, (evidence_key, identity_type, identity_value, platform_text, source_text,
                          sender_text, text, observed, observed, now))
                    recorded += int(cursor.rowcount > 0)
                if contract_values:
                    placeholders = ",".join("?" for _ in contract_values)
                    jobs = [dict(item) for item in conn.execute(
                        f"""SELECT * FROM onchain_fast_jobs
                            WHERE first_seen_at>=? AND contract IN ({placeholders})""",
                        (now - CHAT_EVIDENCE_VISIBLE_MS, *contract_values),
                    ).fetchall()]
                else:
                    placeholders = ",".join("?" for _ in symbol_values)
                    jobs = [dict(item) for item in conn.execute(
                        f"""SELECT * FROM onchain_fast_jobs
                            WHERE first_seen_at>=?
                              AND upper(json_extract(candidate_json,'$.symbol')) IN ({placeholders})""",
                        (now - CHAT_EVIDENCE_VISIBLE_MS, *symbol_values),
                    ).fetchall()]
                for job in jobs:
                    candidate = json.loads(job["candidate_json"])
                    candidate_contract = _onchain_address(candidate.get("contractAddress"), candidate.get("network"))
                    candidate_symbol = str(candidate.get("symbol") or "").strip().upper()
                    if not (
                        candidate_contract in contract_values
                        or (not contract_values and candidate_symbol in symbol_values)
                    ):
                        continue
                    updated = self._attach_chat_cross_validation(candidate, conn, now)
                    if updated == candidate:
                        continue
                    matched_keys.add(job["key"])
                    status = job["status"]
                    attempts = int(job.get("attempts") or 0)
                    has_new_evidence = evidence_digest(updated) != evidence_digest(candidate)
                    if has_new_evidence and candidate.get("decision") == "shortlisted" and status != "running":
                        status, attempts = "pending", 0
                    conn.execute("""UPDATE onchain_fast_jobs
                        SET candidate_json=?,status=?,attempts=?,next_due_at=?,review_requested_at=?,updated_at=?
                        WHERE key=?""",
                        (json.dumps(updated, ensure_ascii=False), status, attempts, now, now, now, job["key"]),
                    )
                conn.commit()
            finally:
                conn.close()
        if contract_values and not matched_keys:
            self.ingest_text(text, f"chat:{platform_text}:{source_text}")
        return {"recorded": recorded, "matchedCandidates": len(matched_keys),
                "contracts": len(contract_values), "symbols": len(symbol_values)}

    def _normalize_news(self, item):
        now = _now_ms()
        platform = str(item.get("platform") or item.get("originPlatform") or "").strip().casefold()
        source_id = str(item.get("sourceId") or "").strip().casefold()
        inbound_urls = [item.get("url"), *(item.get("links") or [])]
        if (
            platform == "discord"
            or source_id.startswith("discord")
            or any("discord.com/channels/" in str(value or "").casefold() for value in inbound_urls)
        ):
            return {}
        published = _news_timestamp_ms(item.get("add_time") or item.get("publishedAt") or item.get("timestamp"))
        if not published or not 0 <= now - published <= NEWS_TRIGGER_VISIBLE_MS:
            return {}
        title = str(item.get("title") or "").strip()
        body = str(item.get("content") or item.get("body") or "").strip()
        content = f"{title} {body}".strip()
        if not title or not content:
            return {}
        source = str(item.get("source") or item.get("sourceId") or "聚合快讯")[:80]
        url = str(item.get("url") or next(iter(item.get("links") or []), ""))[:700]
        item_id = str(item.get("id") or hashlib.sha1(f"{source}\n{url}\n{title}".encode()).hexdigest())[:160]
        lowered = content.casefold()
        networks = list(dict.fromkeys(
            network for pattern, network in (
                (r"\brobinhood\b", "robinhood"), (r"\bsolana\b|索拉纳", "solana"),
                (r"\bbsc\b|\bbnb\s*chain\b|币安智能链", "bsc"),
                (r"\bbase\b|base\s*链", "base"), (r"\bethereum\b|以太坊", "eth"),
            ) if re.search(pattern, lowered, re.I)
        ))
        address_text = content + " " + " ".join(str(value) for value in (item.get("links") or []))
        raw_contracts = re.findall(
            r"(?<![0-9A-Za-z])(?:0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})(?![0-9A-Za-z])",
            address_text,
        )
        contracts = list(dict.fromkeys(_onchain_address(value) for value in raw_contracts if _onchain_address(value)))
        news_identity = re.sub(r"\s+", " ", f"{source}\n{url}\n{title}".casefold()).strip()
        return {
            "newsKey": "recent-news:" + hashlib.sha1(news_identity.encode()).hexdigest(),
            "itemId": item_id, "source": source, "title": title[:500], "content": content[:2400],
            "url": url, "publishedAt": published, "networks": networks, "contracts": contracts,
        }

    def _store_recent_news(self, news):
        now = _now_ms()
        existing = self._query("SELECT news_key FROM onchain_recent_news WHERE news_key=?", (news["newsKey"],))
        self._write(
            """INSERT INTO onchain_recent_news
               (news_key,item_id,source,title,content,url,published_at,networks_json,contracts_json,expires_at,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(news_key) DO UPDATE SET title=excluded.title,content=excluded.content,
                 url=excluded.url,published_at=excluded.published_at,networks_json=excluded.networks_json,
                 contracts_json=excluded.contracts_json,expires_at=excluded.expires_at,updated_at=excluded.updated_at""",
            (news["newsKey"], news["itemId"], news["source"], news["title"], news["content"], news["url"],
             news["publishedAt"], json.dumps(news["networks"], ensure_ascii=False),
             json.dumps(news["contracts"], ensure_ascii=False), news["publishedAt"] + NEWS_TRIGGER_VISIBLE_MS, now, now),
        )
        return not existing

    @staticmethod
    def _recent_news_row(saved):
        return {
            "newsKey": saved["news_key"], "itemId": saved["item_id"], "source": saved["source"],
            "title": saved["title"], "content": saved["content"], "url": saved["url"],
            "publishedAt": int(saved["published_at"]),
            "networks": json.loads(saved["networks_json"] or "[]"),
            "contracts": json.loads(saved["contracts_json"] or "[]"),
        }

    def _news_candidate_rows(self, news):
        now = _now_ms()
        contracts = [
            _onchain_address(value)
            for value in (news.get("contracts") or [])
            if _onchain_address(value)
        ]
        terms = _news_identity_terms(news)
        rows = []
        if contracts:
            placeholders = ",".join("?" for _ in contracts)
            rows.extend(self.store._onchain_candidate_row(saved) for saved in self._query(
                f"SELECT * FROM onchain_research_candidates WHERE contract_address IN ({placeholders}) AND decision!='filtered' AND first_seen_at<=?",
                (*contracts, now),
            ))
        for offset in range(0, len(terms), 350):
            chunk = terms[offset:offset + 350]
            placeholders = ",".join("?" for _ in chunk)
            rows.extend(self.store._onchain_candidate_row(saved) for saved in self._query(
                f"""SELECT * FROM (
                    SELECT candidate.*, row_number() OVER (
                        PARTITION BY network, lower(symbol)
                        ORDER BY coalesce(CAST(json_extract(metrics_json,'$.liquidityUsd') AS REAL),0) DESC,
                                 coalesce(CAST(json_extract(metrics_json,'$.volumeH1Usd') AS REAL),0) DESC
                    ) AS identity_rank
                    FROM onchain_research_candidates AS candidate
                    WHERE decision!='filtered' AND first_seen_at<=? AND symbol COLLATE NOCASE IN ({placeholders})
                ) WHERE identity_rank<=3""",
                (now, *chunk),
            ))
            rows.extend(self.store._onchain_candidate_row(saved) for saved in self._query(
                f"""SELECT * FROM (
                    SELECT candidate.*, row_number() OVER (
                        PARTITION BY network, lower(name)
                        ORDER BY coalesce(CAST(json_extract(metrics_json,'$.liquidityUsd') AS REAL),0) DESC,
                                 coalesce(CAST(json_extract(metrics_json,'$.volumeH1Usd') AS REAL),0) DESC
                    ) AS identity_rank
                    FROM onchain_research_candidates AS candidate
                    WHERE decision!='filtered' AND first_seen_at<=? AND name COLLATE NOCASE IN ({placeholders})
                ) WHERE identity_rank<=3""",
                (now, *chunk),
            ))

        job_rows = []
        if contracts:
            placeholders = ",".join("?" for _ in contracts)
            job_rows.extend(self._query(
                f"SELECT candidate_json FROM onchain_fast_jobs WHERE contract IN ({placeholders})", tuple(contracts)
            ))
        for offset in range(0, len(terms), 350):
            chunk = terms[offset:offset + 350]
            placeholders = ",".join("?" for _ in chunk)
            job_rows.extend(self._query(
                f"""SELECT candidate_json FROM (
                    SELECT candidate_json, row_number() OVER (
                        PARTITION BY network, symbol COLLATE NOCASE
                        ORDER BY coalesce(CAST(json_extract(candidate_json,'$.metrics.liquidityUsd') AS REAL),0) DESC,
                                 coalesce(CAST(json_extract(candidate_json,'$.metrics.volumeH1Usd') AS REAL),0) DESC
                    ) AS identity_rank
                    FROM onchain_fast_jobs
                    WHERE symbol COLLATE NOCASE IN ({placeholders})
                ) WHERE identity_rank<=3""",
                tuple(chunk),
            ))
            job_rows.extend(self._query(
                f"""SELECT candidate_json FROM (
                    SELECT candidate_json, row_number() OVER (
                        PARTITION BY network, name COLLATE NOCASE
                        ORDER BY coalesce(CAST(json_extract(candidate_json,'$.metrics.liquidityUsd') AS REAL),0) DESC,
                                 coalesce(CAST(json_extract(candidate_json,'$.metrics.volumeH1Usd') AS REAL),0) DESC
                    ) AS identity_rank
                    FROM onchain_fast_jobs
                    WHERE name COLLATE NOCASE IN ({placeholders})
                ) WHERE identity_rank<=3""",
                tuple(chunk),
            ))
        for job in job_rows:
            try:
                rows.append(json.loads(job["candidate_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        by_key = {}
        for row in rows:
            if row.get("decision") == "filtered":
                continue
            key = candidate_key(row)
            previous = by_key.get(key)
            row_seen = int(row.get("observedAt") or row.get("lastSeenAt") or 0)
            previous_seen = int((previous or {}).get("observedAt") or (previous or {}).get("lastSeenAt") or 0)
            if not previous or row_seen >= previous_seen:
                by_key[key] = row
        candidates = list(by_key.values())
        if len(news.get("networks") or []) == 1:
            candidates = [row for row in candidates if row.get("network") == news["networks"][0]]
        return candidates

    @staticmethod
    def _candidate_market_rank(row):
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        return (
            float(metrics.get("liquidityUsd") or 0), float(metrics.get("volumeH1Usd") or 0),
            int(float(metrics.get("transactionsH1") or 0)), -int(row.get("poolCreatedAt") or 0),
        )

    def _match_candidates_for_news(self, news):
        matched = []
        for row in self._news_candidate_rows(news):
            identity = _candidate_news_identity(row, news)
            if identity:
                matched.append((row, identity))
        best = {}
        for row, identity in matched:
            if identity == "news-contract-explicit":
                group = ("contract", str(row.get("contractAddress") or "").casefold())
            elif identity == "news-name-contract-unverified":
                group = ("identity", str(row.get("name") or row.get("symbol") or "").casefold())
            else:
                group = ("identity", str(row.get("symbol") or row.get("name") or "").casefold())
            current = best.get(group)
            rank = (1 if identity == "news-contract-explicit" else 0, *self._candidate_market_rank(row))
            if not current or rank > current[0]:
                best[group] = (rank, row, identity)
        ranked = sorted(best.values(), key=lambda value: value[0], reverse=True)
        return [(value[1], value[2]) for value in ranked]

    def _apply_news_match(self, row, news, identity_status):
        now = _now_ms()
        key = candidate_key(row)
        story_identity = f"{news['source']}:{news['itemId']}:{key}"
        story_key = "news:" + hashlib.sha1(story_identity.encode()).hexdigest()
        previous_match = self._query(
            "SELECT story_key,alerted_at FROM onchain_research_story_matches WHERE story_key=?", (story_key,)
        )
        if previous_match and (int(previous_match[0].get("alerted_at") or 0) > 0 or self.resonance_sink is None):
            return False
        identity_note = (
            "快讯原文明确给出该 CA；仍需核验事件方是否为官方发行"
            if identity_status == "news-contract-explicit"
            else "新闻未给出可确认 CA；按币名/简称与行情强度消歧，买入前仍需核验当前合约"
        )
        confidence = _news_resonance_confidence(identity_status, news["publishedAt"], now)
        resonance = {
            "version": NEWS_RESONANCE_VERSION, "source": news["source"], "title": news["title"],
            "url": news["url"], "publishedAt": news["publishedAt"], "observedAt": now,
            "identityStatus": identity_status, "confidence": confidence,
            "matchType": "exact-contract" if identity_status == "news-contract-explicit" else "exact-symbol" if identity_status == "same-chain-symbol-unverified" else "exact-name",
            "reason": f"{news['source']}的近期快讯与该 CA 对应币形成交叉共振",
        }
        row = dict(row)
        row["researchEvidence"] = {
            "source": news["source"], "url": news["url"], "publishedAt": news["publishedAt"],
            "text": news["content"][:900], "identityStatus": identity_status, "identityNote": identity_note,
        }
        row["newsObservedAt"] = now
        row["newsSignal"] = {
            "version": NEWS_TRIGGER_VERSION, "tier": "news-triggered", "source": news["source"],
            "title": news["title"][:300], "url": news["url"], "publishedAt": news["publishedAt"],
            "observedAt": now, "identityStatus": identity_status,
            "reason": f"{news['source']}出现直接相关事件，已先进入精选视野，AI 正在补充起因与机会分析",
        }
        row["newsResonance"] = resonance
        row["reasons"] = list(dict.fromkeys([
            f"外部线索：{news['source']}「{news['title'][:120]}」", *row.get("reasons", []),
        ]))[:5]
        self.ingest([row], match_recent_news=False)
        self._write(
            "UPDATE onchain_fast_jobs SET status='pending',review_requested_at=?,next_due_at=?,attempts=0 WHERE key=? AND status!='running'",
            (now, now, key),
        )
        self._write(
            """INSERT INTO onchain_research_story_matches
               (story_key,candidate_key,content,url,created_at,match_json,alerted_at)
               VALUES(?,?,?,?,?,?,0)
               ON CONFLICT(story_key) DO UPDATE SET content=excluded.content,url=excluded.url,match_json=excluded.match_json""",
            (story_key, key, news["content"][:1200], news["url"], now, json.dumps(resonance, ensure_ascii=False)),
        )
        jobs = self._query("SELECT candidate_json FROM onchain_fast_jobs WHERE key=?", (key,))
        stored = json.loads(jobs[0]["candidate_json"]) if jobs else row
        if self.resonance_sink and news_trigger_signal(stored, now_ms=now) and confidence >= 70:
            try:
                result = self.resonance_sink(stored, resonance, news)
                if not isinstance(result, dict) or result.get("ok", True):
                    self._write("UPDATE onchain_research_story_matches SET alerted_at=? WHERE story_key=?", (now, story_key))
            except Exception:
                return False
        return True

    def _match_recent_news_for_candidate(self, row):
        now = _now_ms()
        for saved in self._query(
            "SELECT * FROM onchain_recent_news WHERE expires_at>=? ORDER BY published_at DESC LIMIT 240", (now,)
        ):
            news = self._recent_news_row(saved)
            if len(news.get("networks") or []) == 1 and row.get("network") != news["networks"][0]:
                continue
            identity = _candidate_news_identity(row, news)
            if identity:
                self._apply_news_match(row, news, identity)
                break

    def ingest_news(self, item):
        """Persist recent news and surface a matching tradable CA without waiting for AI."""
        if not self._initialized or not isinstance(item, dict):
            return
        news = self._normalize_news(item)
        if not news:
            return
        is_new = self._store_recent_news(news)
        if not is_new:
            return
        clue_event = {
            "source": news["source"], "title": news["title"], "url": news["url"],
            "publishedAt": news["publishedAt"], "identityStatus": "news-contract-explicit",
        }
        self.ingest_text(
            news["content"] + " " + " ".join(str(value) for value in (item.get("links") or [])),
            f"news:{news['source']}", event=clue_event,
        )
        for row, identity_status in self._match_candidates_for_news(news):
            self._apply_news_match(row, news, identity_status)

    def ingest_text(self, content, source="external", *, event=None):
        if not self._initialized:
            return
        text = str(content or "")[:6000]
        source_text = str(source or "external")[:80]
        if "discord" in source_text.casefold() or "discord.com/channels/" in text.casefold():
            return
        addresses = re.findall(r"(?<![0-9A-Za-z])(?:0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})(?![0-9A-Za-z])", text)
        now = _now_ms()
        event_json = json.dumps(event, ensure_ascii=False, sort_keys=True) if isinstance(event, dict) else "{}"
        for address in dict.fromkeys(_onchain_address(value) for value in addresses):
            if not address:
                continue
            if event_json != "{}":
                self._write("""INSERT INTO onchain_contract_clues
                    (contract,source,content,received_at,next_due_at,event_json) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(contract) DO UPDATE SET
                      source=excluded.source,content=excluded.content,
                      received_at=CASE WHEN onchain_contract_clues.event_json!=excluded.event_json THEN excluded.received_at ELSE onchain_contract_clues.received_at END,
                      next_due_at=CASE WHEN onchain_contract_clues.event_json!=excluded.event_json THEN excluded.next_due_at ELSE onchain_contract_clues.next_due_at END,
                      attempts=CASE WHEN onchain_contract_clues.event_json!=excluded.event_json THEN 0 ELSE onchain_contract_clues.attempts END,
                      resolved=CASE WHEN onchain_contract_clues.event_json!=excluded.event_json THEN 0 ELSE onchain_contract_clues.resolved END,
                      event_json=excluded.event_json""",
                    (address, source_text, text[:500], now, now, event_json),
                )
            else:
                self._write("""INSERT INTO onchain_contract_clues(contract,source,content,received_at,next_due_at)
                    VALUES(?,?,?,?,?) ON CONFLICT(contract) DO NOTHING""", (address, source_text, text[:500], now, now))

    def resolve_clues(self):
        now = _now_ms()
        jobs = self._query("""SELECT * FROM onchain_contract_clues WHERE resolved=0 AND next_due_at<=?
            AND received_at>=? ORDER BY next_due_at LIMIT 2""", (now, now - 24 * 60 * 60_000))
        for job in jobs:
            self._write("UPDATE onchain_contract_clues SET attempts=attempts+1,next_due_at=? WHERE contract=?",
                        (now + (15_000 if job["attempts"] < 12 else 300_000), job["contract"]))
            try:
                rows = normalize_onchain_dexscreener(fetch_dexscreener_token(job["contract"]), "", observed_at=_now_ms())
                rows = [row for row in rows if _onchain_address(row.get("contractAddress"), row.get("network")) == job["contract"]]
                try:
                    event = json.loads(job.get("event_json") or "{}")
                except (TypeError, ValueError):
                    event = {}
                for row in rows:
                    row["firstSeenAt"] = job["received_at"]
                    row["reasons"] = [f"外部线索：{job['source']}：{job['content'][:180]}"]
                    row["providers"].append(job["source"])
                    if event:
                        row["researchEvidence"] = {
                            "source": event.get("source") or job["source"], "url": event.get("url") or "",
                            "publishedAt": event.get("publishedAt") or job["received_at"],
                            "text": job["content"][:900],
                            "identityStatus": event.get("identityStatus") or "news-contract-explicit",
                            "identityNote": "快讯原文明确给出该 CA；仍需核验事件方是否为官方发行",
                        }
                        row["newsObservedAt"] = job["received_at"]
                        row["newsSignal"] = {
                            "version": NEWS_TRIGGER_VERSION, "tier": "news-triggered",
                            "source": event.get("source") or job["source"], "title": event.get("title") or job["content"][:180],
                            "url": event.get("url") or "", "publishedAt": event.get("publishedAt") or job["received_at"],
                            "observedAt": job["received_at"], "identityStatus": event.get("identityStatus") or "news-contract-explicit",
                            "reason": "新闻明确给出该合约，已先进入精选视野，AI 正在补充起因与机会分析",
                        }
                if rows:
                    evaluated = [evaluate_onchain_candidate(row) for row in rows]
                    self.store.save_onchain_research_scan(evaluated)
                    self.ingest(evaluated)
                    self._write("UPDATE onchain_contract_clues SET resolved=1 WHERE contract=?", (job["contract"],))
            except Exception:
                continue

    def _query(self, sql, params=()):
        conn = self.store._connect()
        try:
            return [dict(row) for row in conn.execute(sql, params)]
        finally:
            conn.close()

    def _write(self, sql, params=()):
        with self.store._lock:
            conn = self.store._connect()
            try:
                conn.execute(sql, params)
                conn.commit()
            finally:
                conn.close()

    def _claim_analysis_jobs(self, lane):
        if lane not in {"mixed", "live", "history"}:
            raise ValueError("unknown research lane")
        now = _now_ms()
        # One short write transaction owns selection and claim across workers.
        # The model runs only after the transaction/lock have been released.
        with self.store._lock:
            conn = self.store._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""UPDATE onchain_fast_jobs SET status='screened' WHERE status='pending'
                    AND coalesce(json_extract(candidate_json,'$.decision'),'')!='shortlisted'""")
                where = "status='pending' AND next_due_at<=? AND attempts<3"
                params = [now]
                fresh = "(analyzed_at=0 AND first_seen_at>=?)"
                if lane != "mixed":
                    where += " AND " + (fresh if lane == "live" else "NOT " + fresh)
                    params.append(now-300_000)
                order = "first_seen_at,key" if lane == "live" else "next_due_at,first_seen_at,key"
                jobs = [dict(row) for row in conn.execute(f"SELECT * FROM onchain_fast_jobs WHERE {where} ORDER BY {order} LIMIT ?", (*params, 2 if lane == "live" else 1))]
                if jobs and lane != "live":
                    jobs += [dict(row) for row in conn.execute(f"""SELECT * FROM onchain_fast_jobs WHERE {where} AND key<>?
                        ORDER BY review_requested_at DESC,first_seen_at DESC,key LIMIT 1""", (*params, jobs[0]["key"]))]
                for job in jobs:
                    conn.execute("""UPDATE onchain_fast_jobs SET status='running',started_at=?,attempts=attempts+1,
                        first_started_at=CASE WHEN first_started_at=0 AND analyzed_at=0 THEN ? ELSE first_started_at END
                        WHERE key=?""", (now, now, job["key"]))
                    job["started_at"] = now
                conn.commit()
            finally:
                conn.close()
        return jobs

    def analyze_batch(self, lane="mixed"):
        jobs = self._claim_analysis_jobs(lane)
        if not jobs:
            return
        try:
            analyzer = self.realtime_analyzer if lane == "live" else self.analyzer
            results = analyzer([json.loads(job["candidate_json"]) for job in jobs])
        except ResearchCapacityBusy:
            for job in jobs:
                self._write("""UPDATE onchain_fast_jobs SET status='pending',attempts=MAX(0,attempts-1),
                    next_due_at=?,error='AI 通道忙碌，保留排队' WHERE key=? AND status='running' AND started_at=?""",
                    (_now_ms()+2_000, job["key"], job["started_at"]))
            return
        except Exception as exc:
            results = {}
            error = str(exc)[:180]
        else:
            error = "AI 未返回此合约的有效结果"
        for job in jobs:
            result = results.get(job["key"]) if isinstance(results, dict) else None
            done = _now_ms()
            if isinstance(result, dict) and result.get("verdict") in {"strong", "watch", "weak", "avoid"} and result.get("summary"):
                with self.store._lock:
                    conn = self.store._connect()
                    try:
                        changed = conn.execute("""UPDATE onchain_fast_jobs SET status='ready',analysis_json=?,
                            first_analyzed_at=CASE WHEN first_analyzed_at=0 AND analyzed_at=0 THEN ? ELSE first_analyzed_at END,
                            analyzed_at=?,error='',updated_at=? WHERE key=? AND status='running' AND started_at=?""",
                            (json.dumps(result, ensure_ascii=False), done, done, done, job["key"], job["started_at"])).rowcount
                        conn.commit()
                    finally:
                        conn.close()
                if not changed:
                    continue  # A recovered/newer run owns the job now.
                if formal_research_worthy(result):
                    self._write("INSERT OR IGNORE INTO onchain_research_recommendations VALUES(?,?,?,?)",
                                (job["key"], done, job["candidate_json"], json.dumps(result, ensure_ascii=False)))
                latest = self._query("SELECT candidate_json FROM onchain_fast_jobs WHERE key=?", (job["key"],))[0]
                if evidence_digest(json.loads(latest["candidate_json"])) != evidence_digest(json.loads(job["candidate_json"])):
                    self._write("UPDATE onchain_fast_jobs SET status='pending',attempts=0,next_due_at=?,review_requested_at=? WHERE key=?", (done, done, job["key"]))
            else:
                self._write("""UPDATE onchain_fast_jobs SET status=?,next_due_at=?,error=?,updated_at=?
                    WHERE key=? AND status='running' AND started_at=?""",
                    ("pending" if job["attempts"] < 2 else "unavailable", done + 10_000, error, done, job["key"], job["started_at"]))

    def emit_alerts(self):
        now = _now_ms()
        for job in self._query("""SELECT * FROM onchain_fast_jobs WHERE status='ready' AND alerted_at=0
                                  AND first_seen_at>=? ORDER BY analyzed_at LIMIT 20""", (now - 5 * 60_000,)):
            analysis = json.loads(job["analysis_json"])
            row = json.loads(job["candidate_json"])
            if row.get("decision") != "shortlisted":
                continue
            if row.get("researchEvidence", {}).get("identityStatus") == "same-chain-symbol-unverified":
                continue
            # Only an explicit whole-framework golden-dog/leader conclusion may
            # interrupt the user. Audit risk remains a separate ledger: BLOCK
            # can still be a research alert, but the sink must not present it as
            # an executable buy signal.
            if not formal_research_worthy(analysis) or not golden_leader_alert_decision(row, analysis)["eligible"]:
                continue
            result = self.alert_sink(row, analysis, job)
            if result and result.get("ok"):
                self._write("UPDATE onchain_fast_jobs SET alerted_at=? WHERE key=?", (now, job["key"]))

    def refresh_quotes(self):
        now = _now_ms()
        jobs = self._query("""SELECT * FROM onchain_fast_jobs WHERE quote_due_at>0 AND quote_due_at<=?
                             AND first_seen_at>=? ORDER BY CASE WHEN first_seen_at>=? THEN 0 ELSE 1 END,
                             quote_due_at LIMIT 60""", (now, now - 24 * 60 * 60_000, now - 15 * 60_000))
        groups = {}
        for job in jobs:
            groups.setdefault(job["network"], []).append(job)
            age = now - job["first_seen_at"]
            self._write("UPDATE onchain_fast_jobs SET quote_due_at=? WHERE key=?", (now + (15_000 if age < 180_000 else 60_000 if age < 15 * 60_000 else 300_000), job["key"]))
        for network, group in groups.items():
            for offset in range(0, len(group), 30):
                batch = group[offset:offset + 30]
                try:
                    payload = self.quote_fetcher(network, [job["contract"] for job in batch])
                    observed = _now_ms()
                    rows = normalize_onchain_dexscreener(payload, network, observed_at=observed)
                    expected = {job["key"]: json.loads(job["candidate_json"]) for job in batch}
                    combined = {}
                    for row in rows:
                        key = candidate_key(row)
                        if key not in expected:
                            continue
                        previous = expected[key]
                        row = {**row, "firstSeenAt": previous["firstSeenAt"],
                               "poolCreatedAt": previous.get("poolCreatedAt") or row.get("poolCreatedAt"),
                               "reasons": previous.get("reasons", []),
                               "providers": list(dict.fromkeys([*previous.get("providers", []), "dexscreener"]))}
                        if key not in combined or (row.get("metrics", {}).get("liquidityUsd") or 0) > (combined[key].get("metrics", {}).get("liquidityUsd") or 0):
                            combined[key] = evaluate_onchain_candidate(row, now_ms=observed)
                    if combined:
                        self.store.save_onchain_research_scan(combined.values(), observed_at=observed)
                        self.ingest(combined.values())
                except Exception:
                    # Keep the durable contract and retry; an empty quote is not a discarded launch.
                    continue

    def _rpc(self, method, params):
        urls = [value.strip() for value in os.getenv("ONCHAIN_BSC_RPC_URLS", "https://bsc-rpc.publicnode.com,https://bsc-dataseed.binance.org").split(",") if value.strip()]
        if not urls:
            raise RuntimeError("BSC RPC 未配置")
        url = urls[self._rpc_index % len(urls)]
        try:
            response = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=(3, 6))
            response.raise_for_status()
            payload = response.json()
            if payload.get("error") or "result" not in payload:
                raise RuntimeError(str(payload.get("error") or "RPC missing result")[:180])
            return payload["result"]
        except Exception:
            self._rpc_index += 1
            raise

    def poll_launches(self):
        now = _now_ms()
        if now < self._next_launch:
            return
        self._next_launch = now + 5_000
        try:
            head = int(self.rpc("eth_blockNumber", []), 16) - 2
            saved = self._query("SELECT block FROM onchain_launch_cursor WHERE source='bsc-launch'")
            cursor = int(saved[0]["block"]) if saved and saved[0]["block"] else max(0, head - 80)
            # Never move a persisted cursor over an outage gap. Catch up in bounded batches.
            end = min(head, cursor + 200)
            if end <= cursor:
                return
            logs = self.rpc("eth_getLogs", [{"address": [FLAP_PORTAL, FOUR_MANAGER],
                "fromBlock": hex(max(0, cursor - 2)), "toBlock": hex(end),
                "topics": [[FLAP_CREATED, FOUR_CREATED]]}])
            rows = []
            for log in logs:
                row = decode_launch_log(log, received_at=_now_ms())
                if not row:
                    continue
                # Overlap replay is idempotent and cannot erase an acquired market quote.
                if self._query("SELECT key FROM onchain_fast_jobs WHERE key=?", (candidate_key(row),)):
                    continue
                existing = self._query("SELECT * FROM onchain_research_candidates WHERE network=? AND contract_address=?", (row["network"], row["contractAddress"]))
                if existing:
                    self.ingest([self.store._onchain_candidate_row(existing[0])])
                else:
                    rows.append(row)
            if rows:
                evaluated = [evaluate_onchain_candidate(row) for row in rows]
                self.store.save_onchain_research_scan(evaluated, observed_at=_now_ms())
                self.ingest(evaluated)
            self._write("""INSERT INTO onchain_launch_cursor(source,block,updated_at) VALUES('bsc-launch',?,?)
                ON CONFLICT(source) DO UPDATE SET block=excluded.block,updated_at=excluded.updated_at,error=''""", (end, _now_ms()))
            self._rpc_failures = 0
        except Exception as exc:
            self._rpc_failures = min(5, self._rpc_failures + 1)
            self._next_launch = _now_ms() + min(60_000, 5_000 * 2 ** self._rpc_failures)
            self._write("""INSERT INTO onchain_launch_cursor(source,block,updated_at,error) VALUES('bsc-launch',0,0,?)
                ON CONFLICT(source) DO UPDATE SET error=excluded.error""", (type(exc).__name__ + ": " + str(exc)[:120],))

    def attach(self, research):
        if not self._initialized:
            return research
        research = dict(research)
        day = research.get("day") or research.get("researchDay")
        try:
            start = int(time.mktime(time.strptime(day, "%Y-%m-%d")) * 1000)
        except (ValueError, TypeError):
            return research
        # Load only rows that can affect the attention UI. Queue counts are
        # aggregated in SQLite, so a day with tens of thousands of screened
        # launches does not block the page.
        formal_sql = f"""json_extract(analysis_json,'$.verdict') IN ('strong','watch')
            AND json_extract(analysis_json,'$.evidenceStatus') IN ('partial','supported')
            AND coalesce(json_array_length(json_extract(analysis_json,'$.evidenceRefs')),0)>0
            AND coalesce(json_extract(analysis_json,'$.narrative.thesis'),'')!=''
            AND coalesce(json_extract(analysis_json,'$.narrative.attention'),'')!=''
            AND coalesce(json_extract(analysis_json,'$.narrative.evidence'),'')!=''
            AND coalesce(json_extract(analysis_json,'$.narrative.invalidation'),'')!=''
            AND json_extract(analysis_json,'$.frameworkAssessment.version')='{FRAMEWORK_VERSION}'
            AND coalesce(json_extract(analysis_json,'$.frameworkAssessment.attentionState'),'')!=''
            AND coalesce(json_extract(analysis_json,'$.frameworkAssessment.attentionTransition'),'')!=''
            AND coalesce(json_extract(analysis_json,'$.frameworkAssessment.transitionTrigger'),'')!=''
            AND coalesce(json_extract(analysis_json,'$.frameworkAssessment.narrativeDiscovery'),'')!=''
            AND coalesce(json_extract(analysis_json,'$.frameworkAssessment.mappingFit'),'')!=''
            AND coalesce(json_extract(analysis_json,'$.frameworkAssessment.leaderElection'),'')!=''
            AND coalesce(json_extract(analysis_json,'$.frameworkAssessment.nextTrigger'),'')!=''
            AND coalesce(json_extract(analysis_json,'$.frameworkAssessment.invalidation'),'')!=''"""
        managed = bool(research.get("fastResearchManaged"))
        if managed:
            job_rows = self._query(
                f"""SELECT * FROM onchain_fast_jobs j WHERE first_seen_at>=? AND first_seen_at<?
                AND (({formal_sql})
                     OR (json_extract(candidate_json,'$.breakoutSignal.version')=?
                         AND json_extract(candidate_json,'$.breakoutObservedAt')>=?)
                     OR (json_extract(candidate_json,'$.newsSignal.version')=?
                         AND json_extract(candidate_json,'$.newsObservedAt')>=?)
                     OR EXISTS(SELECT 1 FROM onchain_research_recommendations r WHERE r.key=j.key))""",
                (start, start + 86_400_000,
                 BREAKOUT_VERSION, _now_ms() - BREAKOUT_VISIBLE_MS,
                 NEWS_TRIGGER_VERSION, _now_ms() - NEWS_TRIGGER_VISIBLE_MS))
        else:
            job_rows = self._query(
                "SELECT * FROM onchain_fast_jobs WHERE first_seen_at>=? AND first_seen_at<?",
                (start, start + 86_400_000))
        jobs = {job["key"]: job for job in job_rows}
        unique = {} if managed else {candidate_key(row): row for row in research.get("selected", [])}
        for key, job in jobs.items():
            row = json.loads(job["candidate_json"])
            if row.get("decision") == "shortlisted":
                unique[key] = row
            else:
                unique.pop(key, None)
        result = dict(research)
        result["selected"] = []
        result["provisional"] = []
        result["watchingCount"] = int(research.get("watchingCount") or len(research.get("watching") or []))
        result["watching"] = []
        review = {"eligible": int((research.get("funnel") or {}).get("selected") or len(unique)),
                  "pending": 0, "unavailable": 0, "filteredByAi": 0,
                  "needsEvidence": 0, "upgrading": 0, "provisional": 0, "newsTriggered": 0}
        if managed:
            counts = self._query(f"""SELECT
                COALESCE(SUM(CASE WHEN status IN ('pending','running') AND analyzed_at=0 THEN 1 ELSE 0 END),0) AS pending,
                COALESCE(SUM(CASE WHEN status='unavailable' THEN 1 ELSE 0 END),0) AS unavailable,
                COALESCE(SUM(CASE WHEN status='ready' AND json_extract(analysis_json,'$.verdict') IN ('weak','avoid') THEN 1 ELSE 0 END),0) AS filteredByAi,
                COALESCE(SUM(CASE WHEN status='ready' AND json_extract(analysis_json,'$.verdict') NOT IN ('weak','avoid') AND NOT ({formal_sql}) THEN 1 ELSE 0 END),0) AS needsEvidence,
                COALESCE(SUM(CASE WHEN status IN ('pending','running') AND analyzed_at>0 THEN 1 ELSE 0 END),0) AS upgrading
                FROM onchain_fast_jobs WHERE first_seen_at>=? AND first_seen_at<?
                AND coalesce(json_extract(candidate_json,'$.decision'),'')='shortlisted'""",
                (start, start + 86_400_000))[0]
            review.update({key: int(counts.get(key) or 0) for key in
                           ("pending", "unavailable", "filteredByAi", "needsEvidence", "upgrading")})
        for raw in unique.values():
                row = dict(raw)
                job = jobs.get(candidate_key(row))
                if job:
                    row["fastResearch"] = True
                    row["screenedAt"] = job["screened_at"]
                    row["firstScreenMs"] = max(0, job["screened_at"] - job["first_seen_at"])
                    row["discoveryDelayMs"] = max(0, job["first_seen_at"] - int(row.get("poolCreatedAt") or job["first_seen_at"]))
                    row["aiAnalysisStatus"] = "ready" if job["status"] == "ready" else "unavailable" if job["status"] == "unavailable" else "pending" if job["status"] in {"running", "pending"} else "observing"
                    if job["analyzed_at"]:
                        row["aiAnalysis"] = json.loads(job["analysis_json"])
                        row["aiAnalysisStatus"] = "ready"
                        row["analysisRefreshing"] = job["status"] in {"pending", "running"}
                        row["aiAnalysisUpdatedAt"] = job["analyzed_at"]
                        row["analysisLatencyMs"] = max(0, job["analyzed_at"] - job["first_seen_at"])
                        row["aiAnalysisProvider"] = row["aiAnalysis"].get("provider", "ai")
                analysis = row.get("aiAnalysis") or {}
                if not analysis and not managed:
                    review["unavailable" if job and job["status"] == "unavailable" else "pending"] += 1
                elif analysis.get("verdict") in {"weak", "avoid"} and not managed:
                    review["filteredByAi"] += 1
                elif not formal_research_worthy(analysis) and not managed:
                    review["needsEvidence"] += 1
                else:
                    if formal_research_worthy(analysis):
                        if not managed:
                            review["upgrading"] += int(bool(row.get("analysisRefreshing")))
                        row["researchTier"] = "ai-recommended"
                        result["selected"].append(row)
                        continue
                news_signal = news_trigger_signal(row)
                if news_signal and analysis.get("verdict") not in {"weak", "avoid"}:
                    row["researchTier"] = "news-triggered"
                    row["newsTriggerPending"] = not formal_research_worthy(analysis)
                    row["newsTriggerReason"] = news_signal.get("reason") or "新闻催化已出现，AI 正在补充分析"
                    result["selected"].append(row)
                    review["newsTriggered"] += 1
                    continue
                if (row.get("breakoutObservedAt")
                        and (row.get("breakoutSignal") or {}).get("version") == BREAKOUT_VERSION
                        and analysis.get("verdict") not in {"weak", "avoid"}):
                    row["researchTier"] = "quantitative-breakout"
                    row["provisionalReason"] = (row.get("breakoutSignal") or {}).get("reason") or "链上量价爆发，叙事待核验"
                    result["provisional"].append(row)
                    review["provisional"] += 1
        result["selected"] = sort_research_recommendations(result["selected"])
        result["provisional"].sort(key=lambda row: (
            -float((row.get("breakoutSignal") or {}).get("score") or row.get("selectedScore") or 0),
            -float((row.get("metrics") or {}).get("liquidityUsd") or 0),
            -float((row.get("metrics") or {}).get("volumeH1Usd") or 0),
            int(row.get("breakoutObservedAt") or row.get("firstSeenAt") or 0), candidate_key(row)))
        resolved_rows, same_symbol_suppressed = resolve_same_symbol_leaders([
            *result["selected"], *result["provisional"],
        ])
        result["selected"] = sort_research_recommendations([
            row for row in resolved_rows if row.get("researchTier") != "quantitative-breakout"
        ])
        result["provisional"] = [
            row for row in resolved_rows if row.get("researchTier") == "quantitative-breakout"
        ]
        result["sameSymbolSuppressed"] = len(same_symbol_suppressed)
        review["sameSymbolSuppressed"] = len(same_symbol_suppressed)
        active_provisional = [row for row in result["provisional"]
                              if 0 <= _now_ms() - int(row.get("breakoutObservedAt") or 0) <= BREAKOUT_VISIBLE_MS]
        visible_provisional = []
        per_network = {}
        for row in active_provisional:
            network = str(row.get("network") or "").lower()
            if per_network.get(network, 0) >= BREAKOUT_PER_NETWORK_LIMIT:
                continue
            per_network[network] = per_network.get(network, 0) + 1
            visible_provisional.append(row)
        visible_provisional.sort(key=lambda row: (
            -int(row.get("breakoutObservedAt") or 0),
            -float((row.get("breakoutSignal") or {}).get("score") or 0), candidate_key(row)))
        result["provisional"] = visible_provisional
        review["provisionalEligible"] = len(active_provisional)
        review["provisional"] = len(visible_provisional)
        result["selectedTotal"] = len(result["selected"])
        result["provisionalTotal"] = len(result["provisional"])
        result["reviewQueue"] = review
        result["funnel"] = {**research.get("funnel", {}),
                            "newsTriggered": review["newsTriggered"],
                            "provisional": len(result["provisional"]),
                            "selected": len(result["selected"])}
        active_keys = {candidate_key(row) for row in result["selected"]}
        result["recommendationHistory"] = []
        for saved in self._query("""SELECT r.* FROM onchain_research_recommendations r
            JOIN onchain_fast_jobs j ON j.key=r.key WHERE j.first_seen_at>=? AND j.first_seen_at<?
            ORDER BY first_recommended_at DESC,r.key""", (start, start + 86_400_000)):
            if saved["key"] in active_keys:
                continue
            job = jobs.get(saved["key"]) or {}
            current = json.loads(job.get("candidate_json") or saved["candidate_json"])
            analysis = json.loads(job.get("analysis_json") or "{}")
            if current.get("decision") != "shortlisted":
                reason = "当前初筛条件已不满足：" + "；".join(current.get("risks") or ["成交或流动性低于研究门槛"])
            elif analysis.get("verdict") in {"weak", "avoid"}:
                reason = "AI 已降级：" + str(analysis.get("risk") or analysis.get("summary") or "研究依据不成立")
            else:
                reason = "叙事证据不足，已撤回推荐；原结论保留供核对"
            result["recommendationHistory"].append({**json.loads(saved["candidate_json"]),
                "previousAnalysis": json.loads(saved["analysis_json"]), "withdrawalReason": reason,
                "firstRecommendedAt": saved["first_recommended_at"]})
        result["launchSources"] = self._query("SELECT source,block,updated_at AS updatedAt,error FROM onchain_launch_cursor")
        result["analysisTargetMs"] = 60_000
        now = _now_ms()
        result["analysisTargetMet"] = self._query("""SELECT COUNT(*) AS analyzed,
            COALESCE(SUM(CASE WHEN first_analyzed_at-first_seen_at<=60000 THEN 1 ELSE 0 END),0) AS withinMinute
            FROM onchain_fast_jobs WHERE first_analyzed_at>=?""", (now-86_400_000,))[0]
        result["analysisTargetMet"].update(self._query("""SELECT COUNT(*) AS pendingInitial,
            COALESCE(SUM(CASE WHEN first_seen_at<? THEN 1 ELSE 0 END),0) AS pendingOverMinute,
            COALESCE(MIN(first_seen_at),0) AS oldestPendingAt
            FROM onchain_fast_jobs WHERE analyzed_at=0 AND status IN ('pending','running')""", (now-60_000,))[0])
        return result

    def start(self):
        if self._thread and self._thread.is_alive():
            return False
        self.initialize()
        self.restore_review_queue()
        self._stop.clear()
        self._pools = ThreadPoolExecutor(max_workers=6, thread_name_prefix="onchain-fast")
        def loop():
            while not self._stop.is_set():
                for name, action in (("launch", self.poll_launches), ("quotes", self.refresh_quotes), ("clues", self.resolve_clues),
                    ("ai-live-1", lambda: self._analyze_and_alert("live")),
                    ("ai-live-2", lambda: self._analyze_and_alert("live")),
                    ("ai-history", lambda: self._analyze_and_alert("history"))):
                    if self._stop.is_set():
                        break
                    future = self._futures.get(name)
                    if future is None or future.done():
                        try:
                            self._futures[name] = self._pools.submit(action)
                        except RuntimeError:
                            # Stop won the race with submit, or Python is exiting.
                            self._stop.set()
                            return
                self._stop.wait(2)
        self._thread = threading.Thread(target=loop, daemon=True, name="onchain-fast-scheduler")
        self._thread.start()
        return True

    def _analyze_and_alert(self, lane="mixed"):
        self.analyze_batch(lane)
        self.emit_alerts()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self._pools:
            self._pools.shutdown(wait=False, cancel_futures=True)
