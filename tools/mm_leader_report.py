"""做市商操盘标的总览：把 ``mm_positions`` 变成一份能看的榜单。

数据源只有本地库 —— 做市商监控轮询 264 个地址（8 家做市商、ethereum + robinhood），
把「哪家的哪个地址动过哪只币、多少笔、多少美元」落进 ``mm_positions``。这份报告是
那批流水的汇总，不是猜的。

## 清洗口径（不洗的话前三名永远是 USDC / USDT / 假币）

* **稳定币**剔除 —— 那是在做流动性兑换，不是「操盘」。
* **封装/质押凭证**（WETH / stETH / awETH…）剔除 —— 它们是同一条资产的凭证。
* **非 ASCII 符号**剔除 —— `U⁯S⁯DΤ` 这种是同形字假币，链上天天有人在发。
* 成交额和笔数都是**双向累计**（in + out）。

    python tools/mm_leader_report.py            # 生成 HTML
    python tools/mm_leader_report.py --top 60   # 榜单条数
"""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".runtime-cache"
# 产出放 reports/，不跟 .runtime-cache 混（那边会被清理）；缓存才留 runtime
REPORTS = ROOT / "reports"
MM_DB = RUNTIME / "market_maker_monitor.sqlite"
AUTH_DB = RUNTIME / "xingyunshe_auth.db"
OUT_HTML = REPORTS / "做市商操盘标的.html"
OUT_JSON = REPORTS / "_mm_leader_report.json"

FIRM_LABELS = {
    "wintermute": "Wintermute", "jump": "Jump Trading", "gsr": "GSR Markets",
    "dwf": "DWF Labs", "amber": "Amber Group", "b2c2": "B2C2",
    "cumberland": "Cumberland (DRW)", "flow": "Flow Traders",
}
STABLE = {
    "USDC", "USDT", "USDE", "DAI", "SAI", "USDG", "FDUSD", "TUSD", "BUSD", "USDS",
    "PYUSD", "USDD", "RLUSD", "USD1", "USDY", "SUSDE", "CRVUSD", "GHO", "USDB",
    "USDP", "FRAX", "LUSD", "MIM", "DOLA", "ALUSD", "USDX", "UST", "USTC", "SUSD",
    "GUSD", "USDR", "USD0", "USDF", "EURC", "EURT", "XSGD", "BUCK", "DEUSD", "USR",
}
WRAPPED_PREFIX = ("W", "ST", "AW", "CB", "RE", "WE", "SF", "PUF", "E")
WRAPPED = {
    "WETH", "WBTC", "WBNB", "WSOL", "STETH", "WSTETH", "RETH", "CBETH", "WEETH",
    "AWETH", "PUFETH", "CBBTC", "EZETH", "RSETH", "SFRAX", "SFRXETH",
    # 质押/再质押凭证：ether.fi 的 eETH、Lido 的 ETHx、各类 BTC 凭证
    "EETH", "ETHX", "RSWETH", "TBTC", "LBTC", "SOLVBTC", "SUSDS", "SUSDC", "SDAI",
}
CHAIN_LABELS = {"ethereum": "Ethereum", "robinhood": "Robinhood"}
MCAP_CACHE = RUNTIME / "_mm_market_cache.json"
MCAP_TTL = 6 * 3600
DEXSCREENER_BATCH = 30
# 一家在这一只币上的双向成交额低于这个数，算「顺手过了一笔」，不算「在做这只币」。
# 不设这条线，HEX 那种总额 $0.002 的灰尘转账会把参与家数虚报到 6 家。
MIN_FIRM_USD = 10_000
# 整只币的做市成交额下限，低于它就是灰尘流水（yRise / ChatGpt 之类），不进报告。
# 但**取不到价格的币金额是 0**（ETH 就踩过），不能只按金额砍 —— 带够笔数的一律留下，
# 金额一栏显示「金额未知」。
MIN_TOKEN_USD = 1_000
MIN_TOKEN_TRANSFERS = 5
# 「龙头」的市值门槛：$100M 以上才算一只币的级别。
LEADER_MCAP = 100_000_000
# 池子流动性低于这个数的「市值」不可信，当没取到。
# 别调高：POL 在以太坊上的池子只有 $3.8 万，但它是真票；而 Re Protocol 的 $416M 市值
# 只配了 $3,299 流动性 —— 这道闸就是为它设的。
MIN_MCAP_LIQUIDITY = 10_000
# 「多家共振」的门槛。取不到价的币金额是 0、厂家数靠笔数兜底，不能凭它进共振段
# （MATIC 那种会被列成「5 家在做」但其实一分钱都没算出来）。
MIN_RESONANCE_USD = 100_000


def is_noise(symbol: str) -> bool:
    text = (symbol or "").strip()
    if not text or text.upper() in STABLE or text.upper() in WRAPPED:
        return True
    # 同形字假币：`U⁯S⁯DΤ`、`U⁯S⁯Dꓚ` …… 只要有一个非 ASCII 字符就丢掉。
    return any(ord(ch) > 127 for ch in text)


def load_positions() -> list[dict]:
    conn = sqlite3.connect(f"file:{MM_DB.as_posix()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT firm, chain, token, symbol, usd_in, usd_out, transfers,"
            " last_seen_at, price_usd FROM mm_positions"
        )]
    finally:
        conn.close()
    return rows


def load_board_flags() -> dict[str, str]:
    """历史上过榜的标的 → 出现过哪些榜（用来标「龙头/热门」）。"""
    if not AUTH_DB.exists():
        return {}
    conn = sqlite3.connect(f"file:{AUTH_DB.as_posix()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT symbol, binance_gainers_last_seen_at, okx_gainers_last_seen_at,"
            " ave_hot_last_seen_at, binance_wallet_hot_last_seen_at,"
            " gmgn_hot_search_last_seen_at FROM price_watch_assets"
        )]
    finally:
        conn.close()
    flags: dict[str, list[str]] = {}
    mapping = (
        ("binance_gainers_last_seen_at", "币安涨幅"),
        ("okx_gainers_last_seen_at", "OKX涨幅"),
        ("ave_hot_last_seen_at", "AIcoin"),
        ("binance_wallet_hot_last_seen_at", "钱包热榜"),
        ("gmgn_hot_search_last_seen_at", "GMGN热搜"),
    )
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        hits = [label for key, label in mapping if row.get(key)]
        if symbol and hits:
            flags[symbol] = hits
    return {k: "、".join(v) for k, v in flags.items()}


def load_mcap(rows: list[dict], *, force: bool = False) -> dict[str, dict]:
    """按合约地址取真实市值 —— 「龙头」必须按市值判，不能只按做市成交额。

    DexScreener 只索引 EVM 主流链，**Robinhood 链回 ``pairs: null``**，所以那条链的
    市值永远空缺，报告里显示「—」而不是 0。缓存 6 小时，缺的键下次全量重取。
    """
    cache: dict[str, dict] = {}
    if MCAP_CACHE.exists() and not force:
        try:
            data = json.loads(MCAP_CACHE.read_text(encoding="utf-8"))
            if time.time() - float(data.get("fetchedAt") or 0) < MCAP_TTL:
                cache = dict(data.get("market") or {})
        except (OSError, ValueError):
            cache = {}

    wanted: list[tuple[str, str]] = []
    for item in rows:
        for chain, contract in item["contracts"].items():
            address = str(contract or "").strip()
            if chain != "ethereum" or not address.startswith("0x"):
                continue
            key = f"{chain}:{address.lower()}"
            if key not in cache:
                wanted.append((key, address))

    if wanted:
        try:
            import requests

            session = requests.Session()
            session.trust_env = True  # 跟随系统代理，禁固化直连
            headers = {"User-Agent": "Mozilla/5.0 (compatible; mm-leader-report/1.0)"}
            for start in range(0, len(wanted), DEXSCREENER_BATCH):
                chunk = wanted[start:start + DEXSCREENER_BATCH]
                # /tokens/v1/<chain>/<addrs> 每个地址精确回 1 条主交易对；
                # 老的 /latest/dex/tokens/ 批量会漏（ONDO/MKR/POL 直接回空）。
                url = f"https://api.dexscreener.com/tokens/v1/ethereum/" + ",".join(
                    address for _, address in chunk
                )
                try:
                    response = session.get(url, headers=headers, timeout=20)
                    response.raise_for_status()
                    pairs = response.json() or []
                except Exception as exc:  # noqa: BLE001 - 单批失败不该毁掉整份报告
                    print(f"  mcap batch {start // DEXSCREENER_BATCH + 1} 失败：{exc}")
                    continue
                if isinstance(pairs, dict):
                    pairs = pairs.get("pairs") or []
                best: dict[str, dict] = {}
                for pair in pairs:
                    base = (pair.get("baseToken") or {})
                    address = str(base.get("address") or "").lower()
                    if not address:
                        continue
                    mcap = float(pair.get("marketCap") or 0)
                    if mcap <= 0:
                        continue
                    if mcap > float((best.get(address) or {}).get("marketCap") or 0):
                        best[address] = {
                            "marketCap": mcap,
                            "priceUsd": float(pair.get("priceUsd") or 0),
                            "liquidity": float((pair.get("liquidity") or {}).get("usd") or 0),
                            "symbol": str(base.get("symbol") or ""),
                        }
                for key, address in chunk:
                    found = best.get(address.lower())
                    if found:
                        cache[key] = found
        except ImportError:
            print("  没有 requests，跳过市值补全")

    try:
        MCAP_CACHE.write_text(json.dumps(
            {"fetchedAt": int(time.time()), "market": cache},
            ensure_ascii=False,
        ), encoding="utf-8")
    except OSError:
        pass
    return cache


def aggregate(rows: list[dict], board: dict[str, str], market: dict[str, dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if is_noise(symbol):
            continue
        chain = str(row.get("chain") or "")
        token = str(row.get("token") or "").strip().lower()
        # 合并键必须是「链 + 合约」，不能只按 symbol：Robinhood 上有个假 SHIB，
        # 按 symbol 合并会把它并进以太坊真 SHIB 那行（$5.18B 市值 + 新链流水）。
        key = f"{chain}:{token}" if token else f"{chain}:{symbol.upper()}"
        item = merged.setdefault(key, {
            "symbol": symbol, "usd": 0.0, "transfers": 0, "firms": {}, "firmTransfers": {},
            "chains": set(), "contracts": {}, "lastSeenAt": 0,
        })
        usd = float(row.get("usd_in") or 0) + float(row.get("usd_out") or 0)
        item["usd"] += usd
        transfers = int(row.get("transfers") or 0)
        item["transfers"] += transfers
        firm = str(row.get("firm") or "")
        item["firms"][firm] = item["firms"].get(firm, 0.0) + usd
        item["firmTransfers"][firm] = item["firmTransfers"].get(firm, 0) + transfers
        item["chains"].add(chain)
        item["contracts"][chain] = str(row.get("token") or "")
        item["lastSeenAt"] = max(item["lastSeenAt"], int(row.get("last_seen_at") or 0))
    out = []
    for item in merged.values():
        item["firms"] = dict(sorted(item["firms"].items(), key=lambda kv: -kv[1]))
        item["chainList"] = sorted(item["chains"])
        item["board"] = board.get(item["symbol"].upper(), "")
        # 「参与家数」只数真正在做的：单家金额到线才算，灰尘转账不算；
        # 整只币都取不到价（usd=0）时退化成按笔数判，不然 ETH 会显示「0 家」。
        unpriced = item["usd"] <= 0
        item["firmCount"] = sum(
            1
            for firm, usd in item["firms"].items()
            if usd >= MIN_FIRM_USD or (unpriced and item["firmTransfers"].get(firm, 0) >= 3)
        )
        item["firmCountAll"] = len(item["firms"])
        mcap = 0.0
        for chain, contract in item["contracts"].items():
            hit = market.get(f"{chain}:{str(contract).lower()}") or {}
            # 池子太浅的「市值」是 DexScreener 拿一个几乎没量的价 × 总量反推出来的
            # （Re Protocol 就是 $416M 市值配 $3,299 流动性），当没取到处理。
            if float(hit.get("liquidity") or 0) < MIN_MCAP_LIQUIDITY:
                continue
            mcap = max(mcap, float(hit.get("marketCap") or 0))
        item["marketCap"] = mcap
        out.append(item)
    out = [
        item for item in out
        if item["usd"] >= MIN_TOKEN_USD
        or item["transfers"] >= MIN_TOKEN_TRANSFERS
        # 上过我们榜的、以及 Robinhood 新链上的标的，哪怕笔数少也留下 ——
        # 那边的「一笔」往往就是做市商第一次进场，正是要看的东西。
        or item["board"]
        or "robinhood" in item["chains"]
    ]
    # 同链同名不同合约（Robinhood 上 CASHCAT 就有两个）：给短地址尾巴，否则看着像重复行。
    # 只在**同一条链**内算重复 —— LINK 在以太坊和 Robinhood 各有一份，那不算重复。
    seen: dict[tuple[str, str], int] = {}
    for item in out:
        pair = (item["chainList"][0] if item["chainList"] else "", item["symbol"].upper())
        seen[pair] = seen.get(pair, 0) + 1
    for item in out:
        chain = item["chainList"][0] if item["chainList"] else ""
        if seen[(chain, item["symbol"].upper())] > 1:
            address = next(iter(item["contracts"].values()), "")
            item["dupHint"] = address[:10] if address else ""
        else:
            item["dupHint"] = ""
    out.sort(key=lambda x: -x["usd"])
    return out


def money(value: float) -> str:
    if value >= 1e9:
        return f"${value / 1e9:,.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:,.1f}M"
    if value >= 1e3:
        return f"${value / 1e3:,.0f}K"
    return f"${value:,.0f}" if value else "—"


def firm_chips(firms: dict[str, float]) -> str:
    parts = []
    for firm, usd in list(firms.items())[:6]:
        label = FIRM_LABELS.get(firm, firm)
        parts.append(f'<span class="chip">{html.escape(label)}<i>{money(usd)}</i></span>')
    if len(firms) > 6:
        parts.append(f'<span class="chip more">+{len(firms) - 6}</span>')
    return "".join(parts)


def render(rows: list[dict], top: int) -> str:
    total_usd = sum(item["usd"] for item in rows)
    top_rows = rows[:top]
    # 历史龙头：市值到线的才是「币的级别」，再按市值排
    leaders = sorted(
        (item for item in rows if item["marketCap"] >= LEADER_MCAP),
        key=lambda x: -x["marketCap"],
    )
    multi = [
        item for item in rows
        if item["firmCount"] >= 4 and item["usd"] >= MIN_RESONANCE_USD
    ][:24]
    on_board = [item for item in rows if item["board"]][:24]
    robinhood = [item for item in rows if "robinhood" in item["chains"]][:20]
    by_firm: dict[str, list[dict]] = {}
    for item in rows:
        for firm in item["firms"]:
            by_firm.setdefault(firm, []).append(item)

    def table(items: list[dict], *, show_board: bool = True) -> str:
        if not items:
            return '<p class="empty">没有数据</p>'
        head = "<tr><th>#</th><th>标的</th><th>链</th><th>市值</th><th>做市成交额</th>" \
               "<th>笔数</th><th>参与厂家</th>" + ("<th>历史榜单</th>" if show_board else "") + "</tr>"
        body = []
        for index, item in enumerate(items, 1):
            chains = " · ".join(CHAIN_LABELS.get(c, c) for c in item["chainList"])
            board_cell = f'<td class="board">{html.escape(item["board"])}</td>' if show_board else ""
            # Robinhood 链 DexScreener 不索引，市值一栏老实显示「—」，不写 0 也不猜
            mcap = f"<td class='num'>{money(item['marketCap'])}</td>" if item["marketCap"] \
                else "<td class='num dim'>—</td>"
            # 取不到价的币金额是 0，不能印成 $0 让人以为没量
            usd_cell = f"<td class='num'>{money(item['usd'])}</td>" if item["usd"] > 0 \
                else "<td class='num dim'>金额未知</td>"
            hint = item.get("dupHint") or ""
            name_cell = f"<b>{html.escape(item['symbol'])}</b>"
            if hint:
                name_cell += f"<span class='dupe'> {html.escape(hint)}</span>"
            body.append(
                f"<tr><td class='rank'>{index}</td>"
                f"<td>{name_cell}</td>"
                f"<td class='dim'>{chains}</td>"
                f"{mcap}"
                f"{usd_cell}"
                f"<td class='num'>{item['transfers']:,}</td>"
                f"<td class='firms'>{firm_chips(item['firms'])}</td>"
                f"{board_cell}</tr>"
            )
        return f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"

    firm_cards = []
    for firm, items in sorted(by_firm.items(), key=lambda kv: -sum(i["usd"] for i in kv[1])):
        items = sorted(items, key=lambda i: -i["usd"])[:12]
        chips = "".join(
            f'<span class="tok">{html.escape(i["symbol"])}<i>{money(i["usd"])}</i></span>' for i in items
        )
        firm_cards.append(
            f'<section class="firm"><header><h3>{html.escape(FIRM_LABELS.get(firm, firm))}</h3>'
            f'<em>{len(by_firm[firm])} 个标的</em></header><div class="toks">{chips}</div></section>'
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>做市商操盘标的总览</title>
<style>
:root{{--bg:#0d1117;--panel:#151b24;--line:#232b36;--text:#e6edf3;--dim:#8b949e;--accent:#3fb950;--hot:#f0883e}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:32px 20px 60px}}
h1{{font-size:22px;margin:0 0 6px}}
.sub{{color:var(--dim);font-size:13px;margin-bottom:22px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:26px}}
.stat{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
.stat b{{display:block;font-size:22px;line-height:1.2}}
.stat em{{color:var(--dim);font-size:12px;font-style:normal}}
h2{{font-size:16px;margin:32px 0 6px;display:flex;align-items:baseline;gap:8px}}
h2 em{{color:var(--dim);font-size:12px;font-style:normal;font-weight:400}}
.note{{color:var(--dim);font-size:12px;margin:0 0 12px}}
table{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th{{text-align:left;font-size:12px;color:var(--dim);font-weight:500;padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap}}
td{{padding:9px 12px;border-bottom:1px solid rgba(35,43,54,.6);vertical-align:middle}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:rgba(63,185,80,.05)}}
.rank{{color:var(--dim);font-variant-numeric:tabular-nums;width:38px}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.dim{{color:var(--dim);font-size:12px}}
.dupe{{color:var(--dim);font-size:10px;font-family:ui-monospace,Consolas,monospace}}
.board{{color:var(--hot);font-size:12px}}
.firms{{display:flex;flex-wrap:wrap;gap:5px}}
.chip{{display:inline-flex;align-items:baseline;gap:5px;background:#1c2430;border:1px solid var(--line);border-radius:6px;padding:2px 7px;font-size:12px;white-space:nowrap}}
.chip i{{color:var(--accent);font-style:normal;font-size:11px}}
.chip.more{{color:var(--dim)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}}
.firm{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
.firm header{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px}}
.firm h3{{font-size:14px;margin:0}}
.firm em{{color:var(--dim);font-size:12px;font-style:normal}}
.toks{{display:flex;flex-wrap:wrap;gap:6px}}
.tok{{display:inline-flex;align-items:baseline;gap:5px;background:#1c2430;border-radius:6px;padding:3px 8px;font-size:12px}}
.tok i{{color:var(--accent);font-style:normal;font-size:11px}}
.empty{{color:var(--dim);font-size:13px}}
footer{{margin-top:34px;color:var(--dim);font-size:12px;border-top:1px solid var(--line);padding-top:14px}}
</style></head><body><div class="wrap">
<h1>做市商操盘标的总览</h1>
<p class="sub">数据来自做市商监控对 264 个链上地址（8 家做市商 · Ethereum + Robinhood）的轮询流水，
双向成交额累计。生成时间 {time.strftime("%Y-%m-%d %H:%M")}</p>
<div class="stats">
  <div class="stat"><b>{len(rows)}</b><em>操盘过的标的</em></div>
  <div class="stat"><b>{money(total_usd)}</b><em>做市成交额累计</em></div>
  <div class="stat"><b>{len(leaders)}</b><em>市值 ≥ $1 亿的龙头</em></div>
  <div class="stat"><b>{sum(1 for i in rows if i["firmCount"] >= 4 and i["usd"] >= MIN_RESONANCE_USD)}</b><em>≥4 家同时在做</em></div>
  <div class="stat"><b>{sum(1 for i in rows if i["board"])}</b><em>历史上过榜</em></div>
</div>

<h2>历史龙头 <em>市值 ≥ $1 亿 共 {len(leaders)} 个 · 按市值降序展示前 {min(40, len(leaders))} —— 这就是「做市商在托哪些大票」</em></h2>
<p class="note">大市值币的做市是常态化业务，成交额只反映活跃度，不反映「刚进场」；要抓进场看监控页的进场弹窗。</p>
{table(leaders[:40])}

<h2>成交额榜 <em>前 {len(top_rows)} 名 · 剔除稳定币/封装凭证/同形字假币</em></h2>
<p class="note">「做市成交额」是该币在所有厂家地址上的买入+卖出累计，笔数越多、金额越大说明这家在里面越活跃。</p>
{table(top_rows)}

<h2>多家共振 <em>≥4 家做市商同时在做 —— 兵家必争</em></h2>
<p class="note">单家金额 ≥ $1 万才算「在做」，且整只币做市额要 ≥ $10 万（取不到价的币不参与，否则会靠笔数虚报）。一家在做可能是合作，四家以上同时在做通常是当期流动性争夺的焦点。</p>
{table(multi)}

<h2>历史过榜 ∩ 做市商 <em>上过我们的榜、又被做市商动过</em></h2>
<p class="note">榜单标记取自价格监控库里该币的首次上榜记录（钱包热榜 / GMGN 热搜 / 涨幅榜 / AIcoin）。</p>
{table(on_board)}

<h2>Robinhood 链 <em>新链上的标的 · DexScreener 未索引该链，市值空缺</em></h2>
{table(robinhood)}

<h2>按厂家看</h2>
<div class="grid">{''.join(firm_cards)}</div>

<footer>
口径：只统计已经轮询过的地址；地址簿刚扩容到 264 条（原 77 条），新地址要转完一圈（约 44 分钟）才会补全。
稳定币（USDC/USDT/USDG…）、封装/质押凭证（WETH/stETH…）、含非 ASCII 字符的同形字假币、以及整只币做市额
不足 $1,000 且笔数不足 5 的灰尘流水都已剔除。市值来自 DexScreener，取同一合约下市值最大的交易对
（部分标的的流通量它算不出，会回落到完全稀释口径，量级可参考、别当精确值）。
</footer>
</div></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--refresh-mcap", action="store_true", help="忽略市值缓存，全量重取")
    parser.add_argument("--open", action="store_true", help="生成完直接用默认浏览器打开")
    args = parser.parse_args()

    positions = load_positions()
    board = load_board_flags()
    rough = aggregate(positions, board, {})
    print(f"标的 {len(rough)} 个，补全市值…")
    market = load_mcap(rough, force=args.refresh_mcap)
    rows = aggregate(positions, board, market)
    REPORTS.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(render(rows, args.top), encoding="utf-8")
    OUT_JSON.write_text(json.dumps({
        "generatedAt": int(time.time() * 1000),
        "tokens": [
            {k: v for k, v in item.items() if k not in {"chains"}}
            for item in rows
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"标的 {len(rows)} 个 · 总成交额 {money(sum(i['usd'] for i in rows))} "
          f"· 有市值 {sum(1 for i in rows if i['marketCap'])} 个")
    print(f"HTML → {OUT_HTML}")
    if args.open:
        try:
            import os
            os.startfile(OUT_HTML)  # noqa: S606 - 本机报告，直接用默认浏览器打开
        except (OSError, AttributeError):
            pass
    print("\n龙头（市值 ≥ $1 亿）：")
    leaders = sorted((i for i in rows if i["marketCap"] >= LEADER_MCAP), key=lambda x: -x["marketCap"])
    for index, item in enumerate(leaders[:15], 1):
        firms = "、".join(FIRM_LABELS.get(f, f) for f in list(item["firms"])[:4])
        print(f"  {index:2d}. {item['symbol']:10s} 市值 {money(item['marketCap']):>8s} "
              f"做市 {money(item['usd']):>8s} {item['firmCount']}家/{item['firmCountAll']} "
              f"{firms}")
    print("\n成交额前 10：")
    for index, item in enumerate(rows[:10], 1):
        firms = "、".join(FIRM_LABELS.get(f, f) for f in list(item["firms"])[:4])
        print(f"  {index:2d}. {item['symbol']:10s} {money(item['usd']):>8s} {item['transfers']:5d}笔 "
              f"{item['firmCount']}家/{item['firmCountAll']} {firms}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
