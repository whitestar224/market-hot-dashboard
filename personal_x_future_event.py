"""个人 X 监控 · 未来事件识别与自动入 todolist。

当被监控的个人 X 账号发布了一条包含「未来事件」的帖子时：
1. 用关键词 + 日期规则识别（不消耗 LLM 额度）；
2. 自动把事件写入 admin 用户的 todolist（复用/新建「X未来事件」项目）；
3. 触发页面弹窗 + 桌面通知。

本模块保持纯函数为主，DB 写入与弹窗触发通过注入的钩子完成，
便于单测与复用到 server.py 的调度循环中。
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any

# 未来事件触发关键词（中文 + 英文）。命中任一即可进入日期提取流程。
_FUTURE_KEYWORDS: tuple[str, ...] = (
    "即将", "将上线", "即将上线", "即将发布", "即将上市", "即将开启", "即将空投",
    "上线", "上市", "发布", "空投", "快照", "快照时间", "开放申领", "开放领取",
    "开启", "开始", "预售", "打新", "申购", "上币", "上架", "主网", "迁移",
    "升级", "减半", "解锁", "解锁时间", "到期", "交割", "直播", "会议", "发布会",
    "财报", "财报日", "股东会", "听证会", "决议", "投票", "截止", "快照日",
    "launch", "listing", "tge", "airdrop", "snapshot", "unlock", "halving",
    "mainnet", "upgrade", "migration", "presale", "ido", "ieo", "ipo",
    "earnings", "ama", "date", "soon", "upcoming", "scheduled", "go live",
)

# 未来事件倾向的弱信号词（本身不确定，但配合日期则倾向判定）。
_FUTURE_HINT_KEYWORDS: tuple[str, ...] = (
    "下周", "下月", "明年", "月底", "月初", "年末", "月底前", "周末", "周五", "周末前",
    "next week", "next month", "next year", "end of",
)

# 交易/金融领域词：弱信号词必须配合这些主题之一才判定为未来事件，
# 避免把「下周出去吃饭」这类生活内容误判为交易事件。
_DOMAIN_KEYWORDS: tuple[str, ...] = (
    "币", "上市", "空投", "快照", "主网", "合约", "现货", "期货", "杠杆", "仓位",
    "减半", "解锁", "打新", "申购", "上币", "上架", "代币", "token", "coin",
    "tge", "airdrop", "listing", "mainnet", "合约", "行情", "盘", "涨", "跌",
    "btc", "eth", "sol", "bnb", "etf", "ipo", "新股", "股票", "股",
)


# 明确「已发生/过去」的排除词，命中则不当未来事件。
_PAST_KEYWORDS: tuple[str, ...] = (
    "已经", "已上线", "已上市", "已发布", "已完成", "昨天", "前天", "上周", "上个月",
    "刚刚", "回顾", "复盘", "总结", "纪念", "一周年", "过去",
    "already", "yesterday", "last week", "last month", "recap", "review",
)

# 中文数字 → 阿拉伯数字
_CN_NUM: dict[str, int] = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _cn_num_to_int(text: str) -> int | None:
    """把「十」「十五」「二十三」等中文数字转成整数，失败返回 None。"""
    text = text.strip()
    if not text:
        return None
    if re.fullmatch(r"\d{1,4}", text):
        return int(text)
    if "十" in text:
        parts = text.split("十")
        tens = _CN_NUM.get(parts[0], 1) if parts[0] else 1
        ones = _CN_NUM.get(parts[1], 0) if parts[1] else 0
        return tens * 10 + ones
    if len(text) == 1 and text in _CN_NUM:
        return _CN_NUM[text]
    return None


def _extract_explicit_date(text: str, now: time.struct_time) -> tuple[int, str] | None:
    """从文本提取显式未来日期，返回 (epoch_ms, 人类可读描述) 或 None。

    支持：
    - 绝对日期：「9月28日」「9月28号」「2026/9/28」「2026-09-28」「9.28」
    - 相对日期：「明天」「后天」「3天后」「下周」「下周三」
    """
    year = now.tm_year
    # 绝对日期（带年份）
    m = re.search(r"(20\d{2})[年/\-.](\d{1,2})[月/\-.](\d{1,2})", text)
    if m:
        yy, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            return _mk_epoch(yy, mm, dd), f"{yy}年{mm}月{dd}日"
    # 「X月Y日(号)」或「X.Y」或「X/Y」
    m = re.search(r"(\d{1,2})[月/\-.](?:(\d{1,2})[日号]?)", text)
    if m:
        mm, dd = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            yy = year
            epoch = _mk_epoch(yy, mm, dd)
            # 若该月日已过，视为明年
            if epoch < int(time.time() * 1000) - 86400_000:
                yy += 1
                epoch = _mk_epoch(yy, mm, dd)
            return epoch, f"{yy}年{mm}月{dd}日"
    # 相对日期
    if re.search(r"后天", text):
        return _mk_epoch_from_days(now, 2), "后天"
    if re.search(r"明天|明日", text):
        return _mk_epoch_from_days(now, 1), "明天"
    m = re.search(r"(\d{1,3})\s*[天日]后", text)
    if m:
        days = int(m.group(1))
        return _mk_epoch_from_days(now, days), f"{days}天后"
    # 下周 / 下月
    m = re.search(r"下周([一二三四五六日天])", text)
    if m:
        weekday_cn = m.group(1)
        return _mk_next_weekday(now, weekday_cn), f"下周{m.group(1)}"
    if re.search(r"下周|next week", text, flags=re.I):
        return _mk_epoch_from_days(now, 7), "下周"
    if re.search(r"下月|next month", text, flags=re.I):
        return _mk_epoch_from_days(now, 30), "下月"
    return None


def _mk_epoch(yy: int, mm: int, dd: int) -> int:
    try:
        return int(time.mktime((yy, mm, dd, 9, 0, 0, 0, 0, -1)) * 1000)
    except Exception:
        return 0


def _mk_epoch_from_days(now: time.struct_time, days: int) -> int:
    base = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 9, 0, 0, 0, 0, -1))
    return int((base + days * 86400) * 1000)


def _mk_next_weekday(now: time.struct_time, weekday_cn: str) -> int:
    cn_to_iso = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    target = cn_to_iso.get(weekday_cn)
    if target is None:
        return _mk_epoch_from_days(now, 7)
    current = now.tm_wday  # 0=周一
    delta = (target - current) % 7
    if delta == 0:
        delta = 7
    return _mk_epoch_from_days(now, delta)


def detect_future_event(text: str, now_ms: int | None = None) -> dict[str, Any] | None:
    """识别一段 X 帖子文本是否为「未来事件」。

    返回 dict（含 due_at 毫秒时间戳、事件标题、来源描述），非未来事件返回 None。
    """
    text = (text or "").strip()
    if not text:
        return None
    lower = text.lower()

    # 明确过去 → 排除
    if any(kw in lower for kw in _PAST_KEYWORDS):
        return None

    # 必须命中未来关键词（或弱信号 + 日期）
    has_future_kw = any(kw in lower for kw in _FUTURE_KEYWORDS)
    has_hint_kw = any(kw in lower for kw in _FUTURE_HINT_KEYWORDS)
    if not has_future_kw and not has_hint_kw:
        return None

    now = time.localtime((now_ms or int(time.time() * 1000)) / 1000)
    date_result = _extract_explicit_date(text, now)

    # 弱信号词必须配合显式日期才判定
    if not has_future_kw and not date_result:
        return None

    # 仅命中弱信号词（无明确未来关键词）时，还必须命中交易/金融领域词
    if not has_future_kw and not any(kw in lower for kw in _DOMAIN_KEYWORDS):
        return None

    # 必须有明确时间信息才算未来事件（无日期则一律排除）
    # 避免把「它可能成为公开链负责价格发现…」这类概念讨论误判为未来事件
    if not date_result:
        return None

    due_at, date_label = date_result
    title = _build_event_title(text)

    return {
        "due_at": due_at,
        "date_label": date_label,
        "title": title,
        "source_text": text,
    }

    title = _build_event_title(text)

    return {
        "due_at": due_at,
        "date_label": date_label,
        "title": title,
        "source_text": text,
    }


def _build_event_title(text: str) -> str:
    """从帖子文本生成简短事件标题。"""
    cleaned = re.sub(r"\s+", " ", text).strip()
    # 去掉 URL
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    # 去掉 @提及 和 #话题 前缀保留可读性
    cleaned = re.sub(r"@\w+", "", cleaned).strip()
    if len(cleaned) > 60:
        cleaned = cleaned[:60] + "…"
    return cleaned or "X 未来事件"


def event_dedupe_key(title: str, source_text: str) -> str:
    """稳定去重键：同一事件标题+来源文本 → 相同 key，避免重复入 todo / 重复弹窗。"""
    digest = hashlib.sha1(f"{title}|{source_text}".encode("utf-8")).hexdigest()[:16]
    return f"personal-x-future:{digest}"
