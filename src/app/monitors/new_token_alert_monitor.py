"""战壕新币的关键词弹窗（用户 2026-11-25）。

需求原话：「做一个弹窗提示，如果 bsc，sol 和 robinhood 上出现了命中关键词的新币，
第一时间提示」，随后补充「也就是战壕里这种新出来的币，命中了的话，加进我的弹窗提示里」。

数据源是 GMGN ``/v1/trenches`` 的 ``new_creation`` 段（就是战壕页「新创建」那一列）。
为什么不用 ``completed`` 段：那个是「已开盘」，一个新币从创建到开盘常常只要几分钟，
等到那时候再报就晚了。

**取数走浏览器，不走后端**（用户 2026-11-25：「能用我页面的数据吗，通过油猴插件回传
给项目」「不然币太多了」）。油猴脚本挂在 GMGN 页面上，嗅探页面自己发的 ``/v1/trenches``
响应，把 ``new_creation`` 段原样 POST 回 ``/api/new-token-feed``。好处三条：

* 页面请求本来就是**按用户筛选条件**过滤过的（目标设置 / P1 P2 P3 / 平台白名单），
  币比后端全量拉少一个数量级 —— 直接解决了「币太多」；
* 零 GMGN 配额消耗、零额外延迟（页面自己几秒一轮）；
* 后端不需要知道 DOM 结构，也不需要解析卡片文本，拿到的就是结构化 JSON。

实测（2026-11-25，后端全量各 60 行的口径，仅用于说明为什么不必自己抓）：BSC 60 行只
覆盖 **227 秒**、Solana **134 秒**、Robinhood **5107 秒**。全量抓的话关键词一命中就是
几十条/小时，所以默认 ``NEW_TOKEN_SOURCE=feed``；``=api`` 只在明确要跑无浏览器环境时才开。

三个降噪闸，缺一个都会刷屏：

1. **只看刚创建的**（:data:`NEW_TOKEN_MAX_AGE_SECONDS`）—— 进程重启后首次轮询会把
   历史 60 行全算成「新」，必须用创建时间挡住，否则一启动就炸 60 条；
2. **同一个 symbol 一小时内只报一次**（:data:`NEW_TOKEN_SYMBOL_DEDUP_SECONDS`）——
   实测同一个名字会被反复发币（那次 ``FBTC / FunBitCoin`` 在 90 秒里发了 5 次），
   按合约去重不够；
3. **每小时总量上限**（:data:`NEW_TOKEN_MAX_PER_HOUR`）—— 安全阀，超出的只入库不弹。

关键词表存在自己的 sqlite 里（``keywords`` 表），首次启动写入默认词，之后走
``/api/new-token-keywords`` 增删 —— 用户随口说「加个词 xxx」时不用改代码、不用重启。
默认词表刻意**只放人物 / 机构 / 大事件**这类低频强词：实测把 ``ai`` / ``bitcoin`` /
``稳定币`` 这类泛词也放进去的话，BSC+Solana 会到 **80 条/小时**，等于把提醒废掉。
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

#: 盯这三条链（用户点名）。链代号用内部叫法，GMGN slug 在 :func:`_gmgn_chain` 里换算。
NEW_TOKEN_CHAINS: tuple[str, ...] = ("bsc", "solana", "robinhood")

#: 轮询间隔（秒）。实测 BSC 60 行 227 秒、Solana 134 秒，20 秒不会漏。
NEW_TOKEN_POLL_SECONDS = max(8.0, float(os.getenv("NEW_TOKEN_POLL_SECONDS", "20") or 20))

#: 只报这么久以内创建的币（秒）。重启后不会把历史列表当新币炸出来。
NEW_TOKEN_MAX_AGE_SECONDS = max(60.0, float(
    os.getenv("NEW_TOKEN_MAX_AGE_SECONDS", "900") or 900
))

#: 只看这么久以内**打满/开盘**的币（秒）。用户 2026-11-25 的截图是战壕「已开盘」列里
#: 的 `grok / Grok Coin`（卡片上写着 1m）—— 这种币的 ``created_timestamp`` 往往已经是
#: 几小时前（内盘磨了很久才打满），只盯创建时间就会被判成「太老」而静默。
#: 所以新鲜度取「创建」和「开盘」两个时钟里**更新的那个**，各自一个窗口。
NEW_TOKEN_MAX_OPEN_AGE_SECONDS = max(60.0, float(
    os.getenv("NEW_TOKEN_MAX_OPEN_AGE_SECONDS", "900") or 900
))

#: 每小时最多弹多少条（安全阀）。超出的仍然入库，只是不弹。
NEW_TOKEN_MAX_PER_HOUR = max(1, int(os.getenv("NEW_TOKEN_MAX_PER_HOUR", "20") or 20))

#: 同名币的静默期（秒）。实测同名会被反复发，按合约去重挡不住。
NEW_TOKEN_SYMBOL_DEDUP_SECONDS = max(60.0, float(
    os.getenv("NEW_TOKEN_SYMBOL_DEDUP_SECONDS", "3600") or 3600
))

#: 每轮每链取多少行（GMGN 上游上限就是 60）。
NEW_TOKEN_TRENCH_LIMIT = 60

#: 回传快照的有效期（秒）。油猴脚本跟着页面走，页面几秒就请求一次；超过这么久没回传
#: 就说明他大概率关页面了。**但别设太短**：DOM 扫描是按「卡片集合变了才发」加 60 秒心跳，
#: 行情安静三分钟没有新币很正常 —— 设短了会把还在 15 分钟窗口里的币一起丢掉（2026-11-25
#: 实测 `stale` 之后 `rows()` 返回空，等于整条链路瞎了）。
NEW_TOKEN_FEED_TTL_SECONDS = max(120.0, float(
    os.getenv("NEW_TOKEN_FEED_TTL_SECONDS", "900") or 900
))

#: 判「页面还开着」的时间（秒），只影响 ``status.active`` 这个诊断字段，不影响判定。
NEW_TOKEN_FEED_ACTIVE_SECONDS = max(60.0, float(
    os.getenv("NEW_TOKEN_FEED_ACTIVE_SECONDS", "180") or 180
))

#: 数据源：``feed``（默认，油猴脚本回传的页面数据）/ ``api``（后端自己抓，
#: 只有在明确要跑无浏览器环境时才开 —— 全量 60 行/链就是「币太多」的来源）。
NEW_TOKEN_SOURCE = str(os.getenv("NEW_TOKEN_SOURCE", "feed") or "feed").strip().lower()

#: 默认关键词：人物 / 机构 / 大事件。**不要**往里塞 ``ai`` / ``bitcoin`` / ``meme``
#: 这类泛词 —— 那是 80 条/小时的量级，等于把提醒废掉。泛词让用户自己加
#: （``/api/new-token-keywords``），他知道自己想被什么打扰。
DEFAULT_NEW_TOKEN_KEYWORDS: tuple[str, ...] = (
    # 人物
    "musk", "马斯克", "elon",
    "trump", "特朗普",
    "cz", "赵长鹏", "changpeng",
    "vitalik", "v神", "buterin",
    "saylor", "塞勒",
    "孙宇晨", "justin sun",
    "巴菲特", "buffett",
    # 机构 / 产品
    "binance", "币安",
    "openai", "奥特曼", "altman",
    "grok", "xai", "格罗克",
    "nvidia", "英伟达", "黄仁勋",
    "deepseek",
    # 大事件
    "美联储", "降息", "加息", "cpi",
    "世界杯", "worldcup",
    "奥运", "olympic",
    "大选", "选举", "election",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _text(value: Any, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def normalize_keyword(word: Any) -> str:
    """关键词归一化：去空白、压缩内部空格、转小写。

    中文原样保留；ASCII 一律小写（匹配时两侧都小写，避免 ``Musk`` / ``musk`` 漏判）。
    """
    return re.sub(r"\s+", " ", str(word or "").strip()).casefold()[:40]


def keyword_matcher(word: str) -> re.Pattern[str] | None:
    """把关键词编译成匹配器。

    * 中文（含非 ASCII）：直接子串，中文没有词边界这回事；
    * 1~3 个 ASCII 字符（``ai`` / ``cz`` / ``sol``）：**必须整词**，否则 ``said``
      会命中 ``ai``、``solana`` 会命中 ``sol``；
    * 4 个字符以上（``musk`` / ``bitcoin``）：子串即可，``MUSKMAN`` 应该算命中。
    """
    normalized = normalize_keyword(word)
    if not normalized:
        return None
    if any(ord(char) > 127 for char in normalized):
        return re.compile(re.escape(normalized))
    if len(normalized) <= 3:
        return re.compile(rf"(?<![0-9a-z]){re.escape(normalized)}(?![0-9a-z])")
    return re.compile(re.escape(normalized))


def compile_keywords(words: Iterable[str]) -> list[tuple[str, re.Pattern[str]]]:
    """词表 → ``[(原词, 匹配器)]``，重复的归一化结果只留第一次出现。"""
    compiled: list[tuple[str, re.Pattern[str]]] = []
    seen: set[str] = set()
    for word in words:
        normalized = normalize_keyword(word)
        if not normalized or normalized in seen:
            continue
        matcher = keyword_matcher(normalized)
        if matcher is None:
            continue
        seen.add(normalized)
        compiled.append((str(word).strip(), matcher))
    return compiled


#: 参与匹配的字段：只有**币名原文**。**不匹配** twitter / website —— 那些是链接，
#: 一个普通网址里的 ``binance`` 会把整条提醒变成噪音。
#:
#: **也不要匹配 ``trans_symbol_zhcn`` / ``trans_name_zhcn``**（2026-11-26 用户反馈
#: 「战壕里都没有出现这个币，怎么又弹窗」）：那两个是 GMGN 的**机器翻译**，会把任何
#: 长得像品牌名的土狗硬翻成中文 —— 实测 ``NBIANCE`` / ``BIANCEBIANCE`` 被译成
#: 「币安」，于是靠一把假名字打满了「币安」这个关键词。翻译是模型臆造出来的内容，
#: 不能当作命中依据；词表里每个中文词都配了英文形式（``binance``/``币安``、
#: ``grok``/``格罗克``），原文匹配足够用。
NEW_TOKEN_MATCH_FIELDS = ("symbol", "name")

#: 战壕页一次请求 `/v1/trenches` 会同时返回三段，但页面**只显示当前选中的那一列**。
#: 弹窗不写清是哪一列，用户翻「新创建」列找半天找不到，就会以为「战壕里根本没这个币」
#: （2026-11-26 实测：``NBIANCE`` / ``BIANCEBIANCE`` 在「即将打满」段，用户只看「新创建」）。
NEW_TOKEN_SEGMENT_LABELS = {
    "new_creation": "新创建",
    "near_completion": "即将打满",
    "completed": "已开盘",
}


def segment_label(value: Any) -> str:
    """``near_completion`` → 「即将打满」。``dom``（DOM 兜底，不知道在哪一列）返回空串。"""

    return NEW_TOKEN_SEGMENT_LABELS.get(_text(value, 24).strip().lower(), "")

#: 战壕卡片正文里的图标字形（GMGN 用私有区字体画图标）。解析前必须换成分隔符，
#: 否则 ``\ue06e0xd4…7777 皮皮皮皮船长`` 连成一坨没法切。
_CARD_GLYPH_RE = re.compile(r"[\ue000-\uf8ff]")
#: 卡片上的缩写地址：EVM ``0xd4…7777``、Solana ``7xKX…gAsU``。
_CARD_SHORT_ADDR_RE = re.compile(r"(?:0x)?[0-9A-Za-z]{2,12}(?:\.{2,3}|…)[0-9A-Za-z]{2,12}")
#: 社交 handle。实测卡片上就有 ``@ancobinance`` —— 那里的 ``binance`` **不该**算命中。
_CARD_HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{2,20}")
#: 卡片首行是「短地址 + 空格 + 币名」，这是 DOM 里唯一拿得到币名的地方。
#: 前缀允许任意字符：有的卡前面还挂一个「D」（DemoStaking Pool 那类）。
_CARD_NAME_RE = re.compile(
    r"^.*?(?:0x)?[0-9A-Za-z]{2,12}(?:\.{2,3}|…)[0-9A-Za-z]{2,12}\s+(\S.{0,40})$"
)


#: 卡片正文里币名之后紧跟着的就是数据区，``Tax 2%`` 是最稳的分界。
_CARD_TAX_SPLIT_RE = re.compile(r"\s*Tax\s*\d", re.I)


def display_name_from_card_text(value: Any) -> str:
    """把「币名 + 标签」拼成的一坨切回币名。

    2026-11-26 用户：「弹窗里的币名，要用前面这个，而不是全部那一长串的，
    比如刚刚弹的 CICI」。实测卡片 ``textContent`` 是
    ``0xc6…7777 CiCiBinance AI Act Tax 2%BNB…`` —— symbol 和标签之间**没有任何
    分隔符**（是两个并列 ``<span>``），原样截断就成了 ``CiCiBinance AI Act``。

    只做**两种不会误伤**的剥离：

    1. ``Tax 2%`` 之后一定是数据区（MC / 人数 / 税），直接切掉。
    2. **整串重复**：``ELONCOINELONCOIN`` / ``猫猫币猫猫币`` / ``BinBin`` —— 卡片把
       symbol 和 name 都画了一遍，两边相同时就是同一个词重复。取**最长的那个周期**
       （``CiCiCiCi`` → ``CiCi``，不是 ``Ci``）。

    **不做驼峰切分**。``CiCi`` **Binance AI Act`` 看着能切，但同一套规则会把
    ``希望HOPE`` 切成 ``希望``、把 ``Giko Neko爆火IP`` 切成 ``Giko Neko爆火``、
    把 ``DemoStaking Pool (USD)`` 切成 ``Demo`` —— 那几条都是真页面上的正确币名
    （见 ``CardTextTests.SAMPLES``）。币名和标签在 ``textContent`` 里没有任何分隔，
    纯字符串无法区分。

    所以「名字 + 标签粘连」这条由**脚本侧**解决：DOM 扫描时用字号最大的叶子节点
    提取 ``name_text`` 一起回传（1.10.5），后端优先用它。这里只兜底。
    """

    text = str(value or "").strip()
    if not text:
        return ""
    text = _CARD_TAX_SPLIT_RE.split(text, maxsplit=1)[0].strip()
    if not text:
        return ""
    # 整串 = 同一个词重复 k 次（k 越小 → 周期越长，先试 k=2）
    length = len(text)
    for times in range(2, 7):
        if length % times:
            continue
        unit = text[: length // times]
        if unit and unit * times == text:
            return unit
    return text


def clean_card_text(value: Any) -> str:
    """战壕卡片正文 → 能拿去做关键词匹配的干净文本（去图标、去地址、去 @handle）。"""
    text = _CARD_GLYPH_RE.sub(" ", str(value or ""))
    text = _CARD_HANDLE_RE.sub(" ", text)
    text = _CARD_SHORT_ADDR_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:200]


def card_text_name(value: Any) -> str:
    """从战壕卡片正文里抠币名。

    DOM 兜底回传没有 ``symbol`` 字段可用（实测 ``img[alt]`` 全是 ``logo``），
    卡片正文里紧跟在缩写地址后面的那一段就是币名 —— 但那一段通常还粘着标签和
    MC/税/人数，所以再过一遍 `display_name_from_card_text()` 切干净。
    """
    text = _CARD_GLYPH_RE.sub("|", str(value or ""))
    for chunk in text.split("|"):
        matched = _CARD_NAME_RE.match(chunk.strip())
        if matched:
            return _text(display_name_from_card_text(matched.group(1)), 40)
    return ""


def match_row(row: Mapping[str, Any], compiled: Sequence[tuple[str, re.Pattern[str]]]) -> list[str]:
    """这一行命中了哪些关键词（按词表顺序，去重）。

    ``raw_text`` 只在**一个名字都没取到**时才参与匹配 —— DOM 兜底抓不出 symbol/name，
    卡片正文就是最后一点线索。反过来，能拿到 symbol 的行绝不拿正文兜底：正文里有
    ``ChatGPT`` / ``Grok`` 这类侧栏字样会把整列都变成命中。
    """
    haystack = " ".join(_text(row.get(field), 120) for field in NEW_TOKEN_MATCH_FIELDS)
    if not haystack.strip():
        haystack = clean_card_text(row.get("raw_text"))
    haystack = haystack.casefold()
    if not haystack.strip():
        return []
    hits: list[str] = []
    for word, matcher in compiled:
        if matcher.search(haystack) and word not in hits:
            hits.append(word)
    return hits


def row_identity(chain: str, row: Mapping[str, Any]) -> tuple[str, str]:
    """(链, 合约)。EVM 小写、Solana base58 原样 —— 和 ``wallet_key`` 同一条规矩。"""
    address = _text(row.get("address") or row.get("token_address"), 160)
    if address.lower().startswith("0x"):
        address = address.lower()
    return chain, address


def _seconds(value: Any) -> int:
    """GMGN 的时间戳是**秒**（实测 ``created_timestamp=1795592520``）。"""
    try:
        number = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    return number


#: 卡片上那串年龄文案（``42s`` / ``1m`` / ``2h`` / ``3d`` / ``刚刚`` / ``just now``）。
#: DOM 兜底回传拿不到时间戳，只有这串字 —— 反推成秒数太值了：
#: 它让「已开盘 1m」的币不必依赖 ``open_timestamp`` 也能判新鲜度。
#: **必须锚在结尾**：卡片正文里 ``28.84 SOL`` 这类数字后面跟着字母的量纲，
#: 不锚尾会把 ``84 S`` 读成 84 秒。
_AGE_TEXT_RE = re.compile(
    r"(\d+)\s*(s|sec|secs|second|seconds|秒|m|min|mins|minute|minutes|分|分钟"
    r"|h|hr|hrs|hour|hours|时|小时|d|day|days|天)(?:前|ago)?$",
    re.I,
)
_AGE_UNIT_SECONDS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "秒": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "分": 60, "分钟": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600, "时": 3600, "小时": 3600,
    "d": 86400, "day": 86400, "days": 86400, "天": 86400,
}


def age_seconds_from_text(value: Any) -> int | None:
    """把卡片上的年龄文案折成秒数；认不出来返回 ``None``（**不要**当成 0 秒）。"""
    text = str(value or "").strip().casefold()
    if not text:
        return None
    if text in ("刚刚", "刚才", "just now", "now", "new"):
        return 0
    matched = _AGE_TEXT_RE.search(text)
    if not matched:
        return None
    amount = int(matched.group(1))
    unit = (matched.group(2) or "s").lower()
    return amount * _AGE_UNIT_SECONDS.get(unit, 1)


def new_token_context(
    chain: str,
    row: Mapping[str, Any],
    hits: Sequence[str],
    *,
    now_seconds: int,
) -> dict[str, Any]:
    """把 GMGN 的一行折成弹窗要用的字段。

    ``ageSeconds`` 取「创建」和「打满/开盘」两个时钟里**更新的那个**，``freshKind``
    说明赢的是哪边 —— 战壕「已开盘」列的币常常是两小时前创建、一分钟前才打满，
    只按创建时间判会被误杀（用户 2026-11-25 反馈的 ``grok`` 就是这种）。
    """
    created = _seconds(row.get("created_timestamp") or row.get("creation_timestamp"))
    opened = _seconds(row.get("open_timestamp"))
    if not created and not opened:
        # DOM 兜底只拿得到卡片上的「1m / 42s」，反推一个创建时间。
        from_text = age_seconds_from_text(row.get("age_text"))
        if from_text is not None:
            created = max(1, now_seconds - from_text)
    symbol = _text(row.get("symbol"), 40) or _text(row.get("name_text"), 40)
    name = _text(row.get("name"), 80)
    if not symbol:
        # DOM 兜底：卡片正文里的「短地址 + 空格 + 币名」。
        symbol = card_text_name(row.get("raw_text")) or _text(
            display_name_from_card_text(clean_card_text(row.get("raw_text")).split(" ")[0]), 40
        )
    clocks: list[tuple[int, str]] = []
    if created > 0:
        clocks.append((now_seconds - created, "created"))
    if opened > 0:
        clocks.append((now_seconds - opened, "opened"))
    if clocks:
        age_seconds, fresh_kind = min(clocks, key=lambda item: item[0])
        age_seconds = max(0, age_seconds)
    else:
        age_seconds, fresh_kind = 0, ""
    age_limit = (
        NEW_TOKEN_MAX_OPEN_AGE_SECONDS if fresh_kind == "opened" else NEW_TOKEN_MAX_AGE_SECONDS
    )
    return {
        "chain": chain,
        "tokenAddress": _text(row.get("address") or row.get("token_address"), 160),
        "symbol": symbol or name,
        "name": name,
        "nameZh": _text(row.get("trans_name_zhcn"), 80),
        "keywords": list(hits),
        "createdAt": created * 1000 if created else 0,
        "openedAt": opened * 1000 if opened else 0,
        "ageSeconds": age_seconds,
        "ageLimit": age_limit,
        "freshKind": fresh_kind,
        "segment": _text(row.get("segment"), 24),
        "marketCap": float(row.get("market_cap") or row.get("usd_market_cap") or 0),
        "liquidity": float(row.get("liquidity") or 0),
        "volume24h": float(row.get("volume_24h") or 0),
        "swaps": float(row.get("swaps_24h") or 0),
        "holders": float(row.get("holder_count") or 0),
        "platform": _text(row.get("launchpad_platform") or row.get("launchpad"), 40),
        "twitter": _text(row.get("twitter"), 200),
        "observedAt": _now_ms(),
    }


class NewTokenFeed:
    """油猴脚本回传的战壕「新创建」快照（纯内存，进程重启就没了 —— 页面会再传）。

    为什么不直接抓 GMGN 接口（用户 2026-11-25：「能用我页面的数据吗，通过油猴插件回传
    给项目」「不然币太多了」）：

    * 后端抓的是**全量**：BSC + Solana + Robinhood 各 60 行、两分钟就滚完，
      关键词一命中就是几十条/小时；
    * 他的页面上有自己调好的筛选（目标设置 / P1 P2 P3 预设 / 平台白名单），
      页面请求就是**按他的口径过滤之后**的结果，币天然少一个数量级；
    * 而且零 GMGN 配额、零延迟（页面自己几秒一轮，脚本顺手把响应递过来）。
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = NEW_TOKEN_FEED_TTL_SECONDS,
        active_seconds: float = NEW_TOKEN_FEED_ACTIVE_SECONDS,
        max_rows: int = 400,
    ) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self.active_seconds = float(active_seconds)
        self.max_rows = max(1, int(max_rows))
        self.lock = threading.RLock()
        self._by_chain: dict[str, dict[str, Any]] = {}
        self.batches = 0
        self.last_received_at = 0

    @staticmethod
    def _row_key(row: Mapping[str, Any]) -> str:
        address = str(row.get("address") or row.get("token_address") or "").strip()
        return address.lower() if address.lower().startswith("0x") else address

    @staticmethod
    def _merge_row(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
        """同一枚币的两份数据合并：**谁有值用谁的**，DOM 的 ``segment='dom'`` 不覆盖真段位。

        页面嗅探和 DOM 兜底常常同报一枚币，各有所长：嗅探有 ``created_timestamp`` /
        ``market_cap``，DOM 只有 ``raw_text`` / ``age_text``。整行替换会把有用的字段冲掉。
        """
        merged = dict(old)
        for key, value in new.items():
            if value is None or value == "":
                continue
            if key == "segment" and value == "dom" and merged.get("segment") not in ("", None, "dom"):
                continue
            merged[key] = value
        return merged

    def ingest(
        self,
        chain: Any,
        rows: Iterable[Mapping[str, Any]],
        *,
        received_at: float | None = None,
        source_url: str = "",
    ) -> int:
        """收一批页面数据。返回收下的行数（不合法就 0）。

        **合并而不是替换**：DOM 扫描只报「当前这一屏」，整批替换会让刚滚出视口、
        但还在 15 分钟窗口里的币凭空消失（那正是「没弹」的一种）。
        """
        chain_key = str(chain or "").strip().lower()
        clean = [dict(row) for row in rows if isinstance(row, Mapping)]
        if not chain_key or not clean:
            return 0
        now = int(received_at if received_at is not None else time.time())
        for row in clean:
            row.setdefault("chain", chain_key)
        with self.lock:
            previous = (self._by_chain.get(chain_key) or {}).get("rows") or []
            merged: dict[str, dict[str, Any]] = {}
            for row in previous:
                merged[self._row_key(row)] = dict(row)
            for row in clean:
                key = self._row_key(row)
                if not key:
                    continue
                merged[key] = self._merge_row(merged.get(key) or {}, row)
            self._by_chain[chain_key] = {
                "receivedAt": now,
                "rows": list(merged.values())[-self.max_rows:],
                "sourceUrl": _text(source_url, 300),
            }
            self.batches += 1
            self.last_received_at = now
        return len(clean)

    def rows(
        self, chain: Any, limit: int | None = None, *, include_stale: bool = False
    ) -> list[dict[str, Any]]:
        """该链最近一批数据。

        ``include_stale=True`` 是给诊断接口用的（看「为什么这条没弹」要看得到实际那批）；
        正常判定走默认：太久没回传（他关页面了）就不拿旧数据凑数。
        """
        key = str(chain or "").strip().lower()
        with self.lock:
            entry = self._by_chain.get(key)
            if not entry:
                return []
            if not include_stale and time.time() - float(entry["receivedAt"]) > self.ttl_seconds:
                return []
            rows = list(entry["rows"])
        return rows[:limit] if limit else rows

    def status(self) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            chains = {
                chain: {
                    "rows": len(entry["rows"]),
                    "ageSeconds": round(now - float(entry["receivedAt"]), 1),
                    "receivedAt": int(float(entry["receivedAt"]) * 1000),
                    "stale": now - float(entry["receivedAt"]) > self.active_seconds,
                    "sourceUrl": entry.get("sourceUrl", ""),
                }
                for chain, entry in self._by_chain.items()
            }
        return {
            "batches": self.batches,
            "ttlSeconds": self.ttl_seconds,
            "activeSeconds": self.active_seconds,
            "active": any(not item["stale"] for item in chains.values()),
            "chains": chains,
        }


class NewTokenAlertStore:
    """独立的 sqlite：关键词表 + 已报记录。不碰主库，省掉 ``AUTH_DB_LOCK`` 那套。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS keywords (
                    word        TEXT PRIMARY KEY,
                    enabled     INTEGER NOT NULL DEFAULT 1,
                    added_at    INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    chain       TEXT NOT NULL,
                    address     TEXT NOT NULL,
                    symbol      TEXT NOT NULL DEFAULT '',
                    name        TEXT NOT NULL DEFAULT '',
                    keyword     TEXT NOT NULL DEFAULT '',
                    created_at  INTEGER NOT NULL DEFAULT 0,
                    alerted_at  INTEGER NOT NULL DEFAULT 0,
                    popped      INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chain, address)
                );
                CREATE INDEX IF NOT EXISTS idx_alerts_symbol ON alerts(symbol, alerted_at);
                CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts(alerted_at);
                CREATE TABLE IF NOT EXISTS settings (
                    key         TEXT PRIMARY KEY,
                    value       TEXT NOT NULL DEFAULT ''
                );
                """
            )
            self.conn.commit()

    # -- 关键词 --------------------------------------------------------
    def seed_keywords(self, words: Iterable[str]) -> int:
        """写入默认词（``INSERT OR IGNORE``，不会把用户删掉的词加回来）。"""
        added = 0
        now = _now_ms()
        with self.lock:
            for word in words:
                normalized = normalize_keyword(word)
                if not normalized:
                    continue
                cursor = self.conn.execute(
                    "INSERT OR IGNORE INTO keywords (word, enabled, added_at) VALUES (?, 1, ?)",
                    (normalized, now),
                )
                added += cursor.rowcount or 0
            self.conn.commit()
        return added

    def list_keywords(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT word, enabled, added_at FROM keywords ORDER BY added_at ASC, word ASC"
            ).fetchall()
        return [
            {"word": row["word"], "enabled": bool(row["enabled"]), "addedAt": int(row["added_at"])}
            for row in rows
        ]

    def enabled_keywords(self) -> list[str]:
        return [row["word"] for row in self.list_keywords() if row["enabled"]]

    def add_keyword(self, word: Any) -> dict[str, Any]:
        normalized = normalize_keyword(word)
        if not normalized:
            raise ValueError("关键词不能为空")
        with self.lock:
            self.conn.execute(
                "INSERT INTO keywords (word, enabled, added_at) VALUES (?, 1, ?) "
                "ON CONFLICT(word) DO UPDATE SET enabled = 1",
                (normalized, _now_ms()),
            )
            self.conn.commit()
        return {"word": normalized, "enabled": True}

    def remove_keyword(self, word: Any) -> bool:
        normalized = normalize_keyword(word)
        if not normalized:
            return False
        with self.lock:
            cursor = self.conn.execute("DELETE FROM keywords WHERE word = ?", (normalized,))
            self.conn.commit()
        return bool(cursor.rowcount)

    def set_keyword_enabled(self, word: Any, enabled: bool) -> bool:
        normalized = normalize_keyword(word)
        if not normalized:
            return False
        with self.lock:
            cursor = self.conn.execute(
                "UPDATE keywords SET enabled = ? WHERE word = ?", (1 if enabled else 0, normalized)
            )
            self.conn.commit()
        return bool(cursor.rowcount)

    # -- 设置 ----------------------------------------------------------
    def setting(self, key: str, default: str = "") -> str:
        with self.lock:
            row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self.conn.commit()

    # -- 已报记录 ------------------------------------------------------
    def was_alerted(self, chain: str, address: str) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT 1 FROM alerts WHERE chain = ? AND address = ?", (chain, address)
            ).fetchone()
        return row is not None

    def symbol_hit_since(self, symbol: str, since_ms: int) -> bool:
        """同名币在窗口内是否已经报过（实测同名会被反复发币）。"""
        key = _text(symbol, 40).upper()
        if not key:
            return False
        with self.lock:
            row = self.conn.execute(
                "SELECT 1 FROM alerts WHERE upper(symbol) = ? AND alerted_at >= ? LIMIT 1",
                (key, int(since_ms)),
            ).fetchone()
        return row is not None

    def popped_since(self, since_ms: int) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM alerts WHERE popped = 1 AND alerted_at >= ?",
                (int(since_ms),),
            ).fetchone()
        return int(row["n"] or 0)

    def record(self, context: Mapping[str, Any], *, popped: bool) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO alerts "
                "(chain, address, symbol, name, keyword, created_at, alerted_at, popped) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _text(context.get("chain"), 20),
                    _text(context.get("tokenAddress"), 160),
                    _text(context.get("symbol"), 40),
                    _text(context.get("name"), 80),
                    ",".join(context.get("keywords") or [])[:200],
                    int(context.get("createdAt") or 0),
                    _now_ms(),
                    1 if popped else 0,
                ),
            )
            self.conn.commit()

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM alerts ORDER BY alerted_at DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self.lock:
            try:
                self.conn.close()
            except Exception:
                pass


class NewTokenAlertMonitor:
    """后台轮询战壕「新创建」，命中关键词就回调（弹窗在 ``server.py`` 里排版）。"""

    def __init__(
        self,
        path: str | Path,
        *,
        keywords: Iterable[str] | None = None,
        fetcher: Callable[[str, int], list[dict[str, Any]]] | None = None,
        feed: NewTokenFeed | None = None,
    ) -> None:
        self.store = NewTokenAlertStore(path)
        self.store.seed_keywords(keywords if keywords is not None else DEFAULT_NEW_TOKEN_KEYWORDS)
        # ``fetcher`` 只给测试用；生产走 :attr:`feed`（浏览器回传的页面数据）。
        self.fetcher = fetcher
        self.feed = feed if feed is not None else NewTokenFeed()
        self.source = NEW_TOKEN_SOURCE
        self.last_source = ""
        self.alert_callback: Callable[[dict[str, Any]], Any] | None = None
        self.poll_seconds = NEW_TOKEN_POLL_SECONDS
        self.chains: tuple[str, ...] = NEW_TOKEN_CHAINS
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.RLock()
        # 一轮判定只允许一个线程跑：页面每几秒回传一批，后台又有 20 秒轮询，两者会撞。
        # 用 try-lock（不是阻塞锁）—— 撞上就直接放弃这一轮，别让浏览器那边等我们。
        self._poll_lock = threading.Lock()
        self.last_error = ""
        self.last_poll_at = 0
        #: 上一轮是不是因为「另一轮正在跑」被跳过的（页面回传 vs 后台轮询撞车）。
        self.last_poll_skipped = False
        # 关键词编译结果按词表快照缓存，避免每行都重编正则（动态正则是很贵的）。
        self._compiled: list[tuple[str, re.Pattern[str]]] = []
        self._compiled_signature = ""

    # -- 回调 / 开关 ---------------------------------------------------
    def set_alert_callback(self, callback: Callable[[dict[str, Any]], Any] | None) -> None:
        self.alert_callback = callback

    def alerts_enabled(self) -> bool:
        return self.store.setting("alerts_enabled", "1") != "0"

    def set_alerts_enabled(self, enabled: bool) -> None:
        self.store.set_setting("alerts_enabled", "1" if enabled else "0")

    # -- 关键词 --------------------------------------------------------
    def keywords(self) -> list[dict[str, Any]]:
        return self.store.list_keywords()

    def compiled_keywords(self) -> list[tuple[str, re.Pattern[str]]]:
        words = self.store.enabled_keywords()
        signature = "\n".join(words)
        if signature != self._compiled_signature:
            self._compiled = compile_keywords(words)
            self._compiled_signature = signature
        return self._compiled

    # -- 取数 ----------------------------------------------------------
    def _fetch_rows(self, chain: str) -> list[dict[str, Any]]:
        """这一轮的原始行。默认读浏览器回传的快照（用户 2026-11-25 的口径）。"""
        if self.fetcher is not None:
            self.last_source = "injected"
            return self.fetcher(chain, NEW_TOKEN_TRENCH_LIMIT) or []
        if self.source == "api":
            self.last_source = "api"
            return fetch_trench_rows(chain, NEW_TOKEN_TRENCH_LIMIT) or []
        self.last_source = "feed"
        # 页面回传的一批里同时有「新创建 / 即将打满 / 已开盘」三段，**不要**再按
        # 单段 60 行截断（截断会把「已开盘」整段砍掉，用户说的「没有第一时间」
        # 正是这一列的币）。
        return self.feed.rows(chain, None)

    # -- 主判定 --------------------------------------------------------
    def poll_once(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """拉一轮三条链，返回**这一轮该弹**的 context 列表（已入库/去重/限流）。"""
        if not self._poll_lock.acquire(blocking=False):
            # 另一轮正在跑（页面回传和后台轮询撞上了）。这一轮直接放弃，
            # 绝不排队 —— 浏览器那边的 POST 不能等我们。
            self.last_poll_skipped = True
            return []
        self.last_poll_skipped = False
        try:
            return self._poll_once_locked(now=now)
        finally:
            self._poll_lock.release()

    def poll_now(self, *, attempts: int = 3, wait: float = 0.35, now: float | None = None) -> list[dict[str, Any]]:
        """收到页面回传后立刻判一轮；撞上后台轮询就**短暂重试**。

        ``poll_once()`` 是 try-lock（浏览器不能等我们），一撞车就放弃那一轮。
        但用户 2026-11-25 的要求是「一识别命中到就弹」—— 命中不能等下一轮 20 秒，
        所以这里最多重试 ``attempts`` 次，每次隔 ``wait`` 秒（默认总预算 ≈0.7 秒）。
        """
        hits: list[dict[str, Any]] = []
        for index in range(max(1, int(attempts))):
            hits = self.poll_once(now=now)
            if not self.last_poll_skipped:
                return hits
            if index + 1 < max(1, int(attempts)):
                time.sleep(max(0.05, float(wait)))
        return hits

    def _poll_once_locked(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """拉一轮三条链，返回**这一轮该弹**的 context 列表（已入库/去重/限流）。"""
        now_seconds = int(now if now is not None else time.time())
        compiled = self.compiled_keywords()
        self.last_poll_at = now_seconds
        if not compiled:
            return []
        hour_ago = (now_seconds - 3600) * 1000
        popped_this_hour = self.store.popped_since(hour_ago)
        contexts: list[dict[str, Any]] = []
        errors: list[str] = []
        for chain in self.chains:
            try:
                rows = self._fetch_rows(chain) or []
            except Exception as exc:  # 单链失败不拖垮另外两条
                errors.append(f"{chain}: {type(exc).__name__}: {str(exc)[:160]}")
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                identity_chain, address = row_identity(chain, row)
                if not address or self.store.was_alerted(identity_chain, address):
                    continue
                hits = match_row(row, compiled)
                if not hits:
                    continue
                context = new_token_context(identity_chain, row, hits, now_seconds=now_seconds)
                # ① 只看刚创建 / 刚打满的：重启后不会把历史 60 行当新币炸出来。
                #    `ageLimit` 已经按「赢的那个时钟」选好了窗口（创建 900s / 开盘 900s）。
                if not context["createdAt"] and not context["openedAt"]:
                    continue
                if context["ageSeconds"] > context["ageLimit"]:
                    continue
                # ② 同名一小时内只报一次。
                if self.store.symbol_hit_since(
                    context["symbol"], (now_seconds - NEW_TOKEN_SYMBOL_DEDUP_SECONDS) * 1000
                ):
                    self.store.record(context, popped=False)
                    continue
                # ③ 每小时总量安全阀。
                if popped_this_hour >= NEW_TOKEN_MAX_PER_HOUR:
                    self.store.record(context, popped=False)
                    continue
                if not self.alerts_enabled():
                    self.store.record(context, popped=False)
                    continue
                self.store.record(context, popped=True)
                popped_this_hour += 1
                contexts.append(context)
        self.last_error = "；".join(errors)[:400]
        for context in contexts:
            callback = self.alert_callback
            if callback is None:
                continue
            try:
                callback(context)
            except Exception:
                pass
        return contexts

    # -- 线程 ----------------------------------------------------------
    def start(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(
                target=self._worker, daemon=True, name="new-token-keyword-alerts"
            )
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        thread = self.thread
        self.thread = None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)

    def _worker(self) -> None:
        # 第一轮之前等 5 秒：让 HTTP 端口先起来，别和启动时的其它抓取抢 GMGN 配额。
        if self.stop_event.wait(5.0):
            return
        while not self.stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                self.last_error = f"poll: {type(exc).__name__}: {str(exc)[:200]}"
            self.stop_event.wait(self.poll_seconds)

    def close(self) -> None:
        self.stop()
        self.store.close()


def _gmgn_chain(chain: str) -> str:
    from app.sources.gmgn_agentic import GMGN_TRENCHES_NETWORKS

    return GMGN_TRENCHES_NETWORKS.get(str(chain or "").strip().lower(), "")


def fetch_new_creation_rows(chain: str, limit: int = NEW_TOKEN_TRENCH_LIMIT) -> list[dict[str, Any]]:
    """从 GMGN ``/v1/trenches`` 的 ``new_creation`` 段拉「战壕页新创建」那一列。

    缓存 TTL 给 8 秒（默认 60 秒太慢，会把这个 20 秒一轮的循环拖成 60 秒一轮）；
    ``cache_key`` 带 ``nc`` 前缀，和 ``completed`` 段（已开盘）的缓存互不污染。
    """
    return _fetch_trench_segment(chain, "new_creation", limit=limit)


#: 战壕页三列。用户 2026-11-25 的截图是「已开盘」列（Pooled 28.84 SOL、age 1m），
#: 所以三段全收 —— 新鲜度交给 ``open_timestamp`` 那个时钟去筛。
NEW_TOKEN_TRENCH_SEGMENTS: tuple[str, ...] = ("new_creation", "near_completion", "completed")


def _fetch_trench_segment(
    chain: str, segment: str, limit: int = NEW_TOKEN_TRENCH_LIMIT
) -> list[dict[str, Any]]:
    from app.sources.gmgn_agentic import GMGN_TRENCHES_PATH, gmgn_readonly_post

    gmgn_chain = _gmgn_chain(chain)
    if not gmgn_chain:
        return []
    payload = gmgn_readonly_post(
        GMGN_TRENCHES_PATH,
        cache_key=f"trenches:{segment}:{gmgn_chain}",
        params={"chain": gmgn_chain},
        body={"version": "v2", segment: {"limit": max(1, min(60, int(limit)))}},
        cache_ttl_seconds=8.0,
    )
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        return []
    rows = data.get(segment)
    if not isinstance(rows, list):
        return []
    return [{**dict(row), "segment": segment} for row in rows if isinstance(row, Mapping)]


def fetch_trench_rows(chain: str, limit: int = NEW_TOKEN_TRENCH_LIMIT) -> list[dict[str, Any]]:
    """``api`` 兜底模式：三段全抓（无浏览器时才有用，正常走页面回传）。"""
    rows: list[dict[str, Any]] = []
    for segment in NEW_TOKEN_TRENCH_SEGMENTS:
        try:
            rows.extend(_fetch_trench_segment(chain, segment, limit=limit))
        except Exception:
            continue
    return rows
