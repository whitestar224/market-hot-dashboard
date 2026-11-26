# -*- coding: utf-8 -*-
"""生成 DeepSeek 盘感二次排名 HTML 报告（全量 1405 条，可筛选/搜索/排序）"""
import json, html

SRC = 'deliverables/deepseek-ranking-2026-09-24.json'
OUT = 'deliverables/deepseek-ranking-2026-09-24.html'

d = json.load(open(SRC, encoding='utf-8'))
ranking = d['ranking']
grade_dist = d['gradeDist']

# 安全嵌入：转义 </script> 防注入，& 由 json 处理
payload = json.dumps(ranking, ensure_ascii=False).replace('</', '<\\/')

def money(v):
    if v is None:
        return '—'
    v = float(v)
    if v >= 1e9:
        return f'${v/1e9:.2f}B'
    if v >= 1e6:
        return f'${v/1e6:.2f}M'
    if v >= 1e3:
        return f'${v/1e3:.1f}K'
    return f'${v:.0f}'

def age_str(h):
    if h is None:
        return '—'
    h = float(h)
    if h < 1:
        return f'{h*60:.0f}分'
    if h < 24:
        return f'{h:.1f}时'
    return f'{h/24:.1f}天'

GRADE_COLOR = {'S': '#0F6E56', 'A': '#185FA5', 'B': '#854F0B', 'C': '#A32D2D'}
CHAIN_LIST = ['solana', 'robinhood', 'bsc', 'eth', 'arc']

rows_html = []
for x in ranking:
    grade = x['grade']
    tags = x.get('tags') or []
    red = x.get('redFlags') or []
    signals = x.get('signals') or []
    og_badge = '<span class="og">OG</span>' if x.get('isOg') else ''
    tag_html = ''.join(f'<span class="tag">{html.escape(t)}</span>' for t in tags)
    red_html = ''.join(f'<span class="red">{html.escape(r)}</span>' for r in red)
    sig_html = ' · '.join(html.escape(s) for s in signals)
    rows_html.append(f'''<tr data-grade="{grade}" data-chain="{x['chain']}" data-og="{'1' if x.get('isOg') else '0'}" data-search="{html.escape((x['symbol'] or '') + ' ' + (x['name'] or ''))}">
<td class="c-rank">{x['rank']}</td>
<td class="c-grade" style="color:{GRADE_COLOR[grade]}">{grade}</td>
<td class="c-sym"><span class="sym">{html.escape(x['symbol'] or '')}</span>{og_badge}<div class="nm">{html.escape(x['name'] or '')}</div></td>
<td class="c-chain">{x['chain']}</td>
<td class="c-type">{x['candidateType']}</td>
<td class="c-score">{x['dsScore']:.0f}</td>
<td class="c-num">{x['smartMoneyHolders']}</td>
<td class="c-num">{x['kolHolders']}</td>
<td class="c-num">{money(x['liquidityUsd'])}</td>
<td class="c-num">{money(x['marketCapUsd'])}</td>
<td class="c-num">{money(x['volumeH24Usd'])}</td>
<td class="c-num">{x['holders']}</td>
<td class="c-num">{x['top10Percent']:.1f}%</td>
<td class="c-num">{x['rugRatio']:.3f}</td>
<td class="c-num">{x['maxTax']:.0f}%</td>
<td class="c-num">{age_str(x['ageHours'])}</td>
<td class="c-tags">{tag_html}{red_html}<div class="sig">{sig_html}</div></td>
</tr>''')

html_doc = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DeepSeek 盘感二次排名 · 战壕榜 promising 全量</title>
<style>
:root {{
  --bg: #f5f4f0; --card: #ffffff; --border: #e3e1da; --text: #2c2c2a; --muted: #8a887f;
  --s: #0F6E56; --a: #185FA5; --b: #854F0B; --c: #A32D2D;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #17171a; --card: #1f1f24; --border: #33333a; --text: #e8e6e0; --muted: #9a9890; }}
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text); padding: 20px; }}
h1 {{ font-size: 20px; font-weight: 600; margin-bottom: 4px; }}
.sub {{ color: var(--muted); font-size: 13px; margin-bottom: 18px; }}
.cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }}
.card {{ flex: 1; min-width: 120px; background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }}
.card .num {{ font-size: 26px; font-weight: 700; }}
.card .lbl {{ font-size: 12px; color: var(--muted); margin-top: 2px; }}
.toolbar {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }}
.fbtn {{ padding: 7px 14px; border-radius: 20px; border: 1px solid var(--border); background: var(--card); color: var(--text); cursor: pointer; font-size: 13px; }}
.fbtn.active {{ color: #fff; border-color: transparent; }}
.fbtn[data-g="S"].active {{ background: var(--s); }} .fbtn[data-g="A"].active {{ background: var(--a); }}
.fbtn[data-g="B"].active {{ background: var(--b); }} .fbtn[data-g="C"].active {{ background: var(--c); }}
.fbtn[data-g="ALL"].active {{ background: #5F5E5A; }}
select, input {{ padding: 7px 10px; border-radius: 8px; border: 1px solid var(--border); background: var(--card); color: var(--text); font-size: 13px; }}
input {{ width: 200px; }}
.count {{ margin-left: auto; color: var(--muted); font-size: 13px; }}
table {{ width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; font-size: 12px; }}
thead th {{ position: sticky; top: 0; background: var(--card); border-bottom: 2px solid var(--border); padding: 10px 8px; text-align: left; cursor: pointer; white-space: nowrap; font-weight: 600; color: var(--muted); }}
thead th:hover {{ color: var(--text); }}
tbody td {{ padding: 9px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }}
tbody tr:hover {{ background: rgba(127,119,221,0.06); }}
.c-rank {{ color: var(--muted); width: 36px; }}
.c-grade {{ font-weight: 700; width: 30px; }}
.sym {{ font-weight: 600; font-size: 13px; }}
.nm {{ color: var(--muted); font-size: 11px; }}
.og {{ display: inline-block; margin-left: 5px; padding: 1px 5px; border-radius: 4px; background: #EEEDFE; color: #534AB7; font-size: 10px; font-weight: 700; }}
.tag {{ display: inline-block; margin: 1px 3px 1px 0; padding: 1px 6px; border-radius: 4px; background: #E1F5EE; color: #0F6E56; font-size: 10px; }}
.red {{ display: inline-block; margin: 1px 3px 1px 0; padding: 1px 6px; border-radius: 4px; background: #FCEBEB; color: #A32D2D; font-size: 10px; }}
.sig {{ color: var(--muted); font-size: 10px; margin-top: 2px; }}
.c-num {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
.c-score {{ font-weight: 700; text-align: right; }}
.c-tags {{ min-width: 180px; }}
</style>
</head>
<body>
<h1>DeepSeek 盘感二次排名</h1>
<div class="sub">来源：评分器 promising 全量 {d['total']} 条 · 盘感评分：黄金窗口 + 聪明钱/KOL/OG/流动性加权 − 刷量/老项目/rug/庄控降级 · 生成于 {d['generatedAt']}</div>
<div class="cards">
<div class="card"><div class="num" style="color:var(--s)">{grade_dist['S']}</div><div class="lbl">S 值得看</div></div>
<div class="card"><div class="num" style="color:var(--a)">{grade_dist['A']}</div><div class="lbl">A 关注</div></div>
<div class="card"><div class="num" style="color:var(--b)">{grade_dist['B']}</div><div class="lbl">B 观察</div></div>
<div class="card"><div class="num" style="color:var(--c)">{grade_dist['C']}</div><div class="lbl">C 略过</div></div>
</div>
<div class="toolbar">
<button class="fbtn active" data-g="ALL" onclick="setGrade('ALL',this)">全部</button>
<button class="fbtn" data-g="S" onclick="setGrade('S',this)">S</button>
<button class="fbtn" data-g="A" onclick="setGrade('A',this)">A</button>
<button class="fbtn" data-g="B" onclick="setGrade('B',this)">B</button>
<button class="fbtn" data-g="C" onclick="setGrade('C',this)">C</button>
<select id="chainSel" onchange="apply()">
<option value="ALL">全部链</option>
{''.join(f'<option value="{c}">{c}</option>' for c in CHAIN_LIST)}
</select>
<select id="ogSel" onchange="apply()">
<option value="ALL">全部</option>
<option value="1">仅 OG</option>
<option value="0">非 OG</option>
</select>
<input id="q" placeholder="搜索 symbol / 名称" oninput="apply()">
<span class="count" id="count"></span>
</div>
<div style="max-height:70vh; overflow:auto; border-radius:12px;">
<table>
<thead><tr>
<th onclick="sortBy('rank')">#</th>
<th onclick="sortBy('grade')">档</th>
<th>币种</th>
<th onclick="sortBy('chain')">链</th>
<th>类型</th>
<th onclick="sortBy('dsScore')">盘感分</th>
<th onclick="sortBy('smartMoneyHolders')">聪明钱</th>
<th onclick="sortBy('kolHolders')">KOL</th>
<th onclick="sortBy('liquidityUsd')">流动性</th>
<th onclick="sortBy('marketCapUsd')">市值</th>
<th onclick="sortBy('volumeH24Usd')">24h成交</th>
<th onclick="sortBy('holders')">持有人</th>
<th onclick="sortBy('top10Percent')">Top10%</th>
<th onclick="sortBy('rugRatio')">rug</th>
<th onclick="sortBy('maxTax')">税</th>
<th onclick="sortBy('ageHours')">年龄</th>
<th>信号 / 红旗</th>
</tr></thead>
<tbody id="tbody">{''.join(rows_html)}</tbody>
</table>
</div>
<script>
var DATA = {payload};
var curGrade = 'ALL', sortKey = 'dsScore', sortDir = -1;
function setGrade(g, btn) {{
  curGrade = g;
  document.querySelectorAll('.fbtn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  apply();
}}
function apply() {{
  var chain = document.getElementById('chainSel').value;
  var og = document.getElementById('ogSel').value;
  var q = document.getElementById('q').value.toLowerCase();
  var n = 0;
  document.querySelectorAll('#tbody tr').forEach(tr => {{
    var ok = (curGrade === 'ALL' || tr.dataset.grade === curGrade)
      && (chain === 'ALL' || tr.dataset.chain === chain)
      && (og === 'ALL' || tr.dataset.og === og)
      && (!q || tr.dataset.search.toLowerCase().indexOf(q) >= 0);
    tr.style.display = ok ? '' : 'none';
    if (ok) n++;
  }});
  document.getElementById('count').textContent = '显示 ' + n + ' 条';
}}
function sortBy(key) {{
  if (sortKey === key) sortDir = -sortDir; else {{ sortKey = key; sortDir = -1; }}
  var tbody = document.getElementById('tbody');
  var rows = Array.from(tbody.querySelectorAll('tr'));
  var idx = {{rank:0, dsScore:5, smartMoneyHolders:6, kolHolders:7, liquidityUsd:8, marketCapUsd:9, volumeH24Usd:10, holders:11, top10Percent:12, rugRatio:13, maxTax:14, ageHours:15}};
  var ci = idx[key] !== undefined ? idx[key] : key;
  rows.sort((a,b) => {{
    var va = a.children[ci].textContent.replace(/[$%,]/g,'').replace(/时|分|天/g,'');
    var vb = b.children[ci].textContent.replace(/[$%,]/g,'').replace(/时|分|天/g,'');
    if (key === 'grade' || key === 'chain') return va.localeCompare(vb) * sortDir;
    return (parseFloat(va) - parseFloat(vb)) * sortDir;
  }});
  rows.forEach(r => tbody.appendChild(r));
}}
apply();
</script>
</body>
</html>'''

open(OUT, 'w', encoding='utf-8').write(html_doc)
print('已生成', OUT, f'({len(html_doc)} 字节)')
