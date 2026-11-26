# -*- coding: utf-8 -*-
"""生成《DeepSeek 链上投研分析报告》HTML（分析结果，非评分器代码）"""
import json, html as H

d = json.load(open('deliverables/deepseek-final-ranking-v7-2026-09-24.json', encoding='utf-8'))
r = d['ranking']
S = [x for x in r if x['grade']=='S']
A = [x for x in r if x['grade']=='A']
B = [x for x in r if x['grade']=='B']
C = [x for x in r if x['grade']=='C']

def money(v):
    if v is None: return '—'
    v=float(v)
    if v>=1e9: return f'${v/1e9:.2f}B'
    if v>=1e6: return f'${v/1e6:.2f}M'
    if v>=1e3: return f'${v/1e3:.1f}K'
    return f'${v:.0f}'
def age(h):
    if h is None: return '—'
    h=float(h)
    return f'{h/24:.1f}天' if h>=24 else f'{h:.1f}时'

NAR_COLOR = {
    'Robinhood股票代币化':'#0F6E56','股票流动性网络':'#0F6E56','Robinhood生态':'#0F6E56','Muse生态':'#0F6E56','Robinhood龙头meme':'#0F6E56',
    'AI agent':'#185FA5','名人AI':'#185FA5','AI+股票配对':'#185FA5','genius.fun':'#185FA5','AI/智能体':'#185FA5','Solana热点':'#185FA5',
    '中文meme':'#854F0B','中文打工meme':'#854F0B','动物meme':'#854F0B','社区/文化梗':'#854F0B',
    '股票对标':'#534AB7','金融/股票对标':'#534AB7','预测市场':'#534AB7',
    '电影叙事':'#993556','CZ点名':'#993556','特朗普言论':'#993556','政治':'#993556','电影/影视':'#993556',
    '游戏/电竞':'#1D9E75','科学/物理梗':'#1D9E75','一般':'#888780',
}
GRADE_COLOR={'S':'#0F6E56','A':'#185FA5','B':'#854F0B','C':'#888780'}

def row(x):
    og='<span class="og">OG</span>' if x.get('isOg') else ''
    c=NAR_COLOR.get(x['narrative'],'#888780')
    detail = f'流动性{x["liquidityScore"]} · 机制{x["mechanismScore"]}' if x['assetClass']=='项目/协议' else f'{x["unitType"]} · {x["resonance"]} · 映射{x["mapLevel"]}'
    return f'''<tr>
<td class="rank">{x["rank"]}</td>
<td style="color:{GRADE_COLOR[x["grade"]]};font-weight:700">{x["grade"]}</td>
<td class="sym">{H.escape(x["symbol"] or "")}{og}<div class="nm">{H.escape(x["name"] or "")}</div></td>
<td>{x["chain"]}</td>
<td style="color:{c}">{H.escape(x["narrative"])}</td>
<td>{x["assetClass"]}</td>
<td class="dim">{detail}</td>
<td class="num">{x["smartMoneyHolders"]}</td>
<td class="num">{x["kolHolders"]}</td>
<td class="num">{money(x["liquidityUsd"])}</td>
<td class="num">{money(x["volumeH24Usd"])}</td>
<td class="num">{x["top10Percent"]:.0f}%</td>
<td class="num">{x["rugRatio"]:.3f}</td>
<td class="num">{age(x["ageHours"])}</td>
</tr>'''

proj_SA = [x for x in S+A if x['assetClass']=='项目/协议']
meme_SA = [x for x in S+A if x['assetClass']=='meme']
proj_B = [x for x in B if x['assetClass']=='项目/协议']
meme_B = [x for x in B if x['assetClass']=='meme']
proj_C = [x for x in C if x['assetClass']=='项目/协议']
meme_C = [x for x in C if x['assetClass']=='meme']

html_doc = f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>DeepSeek 链上投研分析报告 · 2026-09-24</title><style>
:root{{--bg:#faf9f6;--card:#fff;--border:#e8e5dd;--text:#26241f;--muted:#8a8678;--accent:#0F6E56;}}
@media (prefers-color-scheme:dark){{:root{{--bg:#17171a;--card:#1f1f24;--border:#33333a;--text:#e8e6e0;--muted:#9a9890;--accent:#3fb98a;}}}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);padding:32px;line-height:1.65}}
.wrap{{max-width:960px;margin:0 auto}}
h1{{font-size:26px;font-weight:700;letter-spacing:-0.3px;margin-bottom:6px}}
.sub{{color:var(--muted);font-size:14px;margin-bottom:28px}}
h2{{font-size:18px;font-weight:600;margin:36px 0 14px;padding-left:12px;border-left:4px solid var(--accent)}}
h3{{font-size:15px;font-weight:600;margin:22px 0 10px;color:var(--accent)}}
p{{font-size:14px;margin-bottom:12px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:20px 22px;margin-bottom:16px}}
.big{{font-size:20px;font-weight:700}}
.grid{{display:flex;gap:14px;flex-wrap:wrap;margin:16px 0}}
.stat{{flex:1;min-width:150px;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 18px}}
.stat .v{{font-size:24px;font-weight:700}}
.stat .l{{font-size:12px;color:var(--muted);margin-top:2px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;font-size:13px}}
th{{background:var(--bg);border-bottom:2px solid var(--border);padding:10px 10px;text-align:left;font-weight:600;color:var(--muted);white-space:nowrap}}
td{{padding:9px 10px;border-bottom:1px solid var(--border);vertical-align:top}}
tr:last-child td{{border-bottom:none}}
.rank{{color:var(--muted);width:36px}}
.sym{{font-weight:600}}
.nm{{color:var(--muted);font-size:11px;font-weight:400}}
.dim{{color:var(--muted);font-size:12px}}
.num{{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}
.og{{display:inline-block;margin-left:5px;padding:1px 5px;border-radius:4px;background:#EEEDFE;color:#534AB7;font-size:10px;font-weight:700}}
.hl{{color:var(--accent);font-weight:700}}
.warn{{background:#FCEBEB;border:1px solid #F5C1C1;border-radius:10px;padding:14px 18px;margin:16px 0;font-size:13px;color:#791F1F}}
.warn b{{color:#A32D2D}}
.note{{color:var(--muted);font-size:12px;margin-top:6px}}
ul{{margin:8px 0 12px 22px}}
li{{font-size:14px;margin-bottom:6px}}
</style></head><body><div class="wrap">

<h1>DeepSeek 链上投研分析报告</h1>
<div class="sub">战壕榜新币早期投研 · 2026-09-24 · 基于链上投研体系 V5.0 · 数据截至 09-24 22:00</div>

<h2>一、核心结论（一句话）</h2>
<div class="card">
<p class="big" style="color:var(--accent)">「新币刚被扫链发现那一刻」的流动性 ≥ $50K 或 24h成交 ≥ $50K，是预测它后来上币安热榜的强信号 —— 命中率 67%~81%，是对照组（2.5%）的 20 倍以上。</p>
<p style="margin-top:10px">这是经过<b>无偏回测</b>验证的结论（见第三节），不是拍脑袋，也不是用"火了之后的数据"反推出来的。</p>
</div>

<h2>二、方法：投研体系 V5.0 资产分流</h2>
<div class="card">
<p>按《链上投研体系 V5.0》，候选币分两条独立评分路径，<b>不能混成一个总分</b>：</p>
<ul>
<li><b>项目/协议类</b>（49 个）：PONS、SHROOM、CONVICTION、NAUTILO 这类有真实产品/协议/经济机制的，评「流动性 + 激励机制 + 协议新颖度」。体系原文：「优先权重 Liquidity / Incentive / PONS > Meme Firstness」。</li>
<li><b>meme 类</b>（807 个）：评「最小注意力单元 + 情绪共鸣 + 映射直接度」。体系原文：炒作潜力账本六问——热点强度、映射直接度、传播性、情绪共鸣、资金承接、竞争格局。</li>
</ul>
<p>全量 1405 去重后 1139，过滤掉 283 个（274 rug盘 + 6 蹭名仿盘 + 3 真实资产凭证），保留 856 个进入评分。</p>
</div>

<div class="grid">
<div class="stat"><div class="v" style="color:#0F6E56">{len(S)}</div><div class="l">S 值得看</div></div>
<div class="stat"><div class="v" style="color:#185FA5">{len(A)}</div><div class="l">A 关注</div></div>
<div class="stat"><div class="v" style="color:#534AB7">{len(proj_SA)}</div><div class="l">其中项目/协议类</div></div>
<div class="stat"><div class="v" style="color:#185FA5">{len(meme_SA)}</div><div class="l">其中 meme 类</div></div>
</div>

<h2>三、无偏回测：早期信号预测力</h2>
<div class="warn"><b>前视偏差已被识破并修正。</b>此前用"9-24 当前快照的聪明钱/流动性"回测，是拿"火了之后的数据"判断"能否早期发现"，属于循环论证、结论作废。本报告的回测，<b>严格用每个币「首次被扫链发现那一刻」的快照</b>（<code>onchain_research_snapshots</code> 表，first_seen_at 与最早快照中位数差 0 分钟），是真正的事前（look-ahead-free）数据。</div>

<div class="card">
<p><b>回测设计</b>：正样本 = 后来上币安热榜的 152 个币；负样本 = 同时期从未上热榜的 5000 个币。两者都取「首次发现时点」的快照字段对比。</p>
<table>
<tr><th>早期信号（首次发现时点）</th><th>上热榜组命中率</th><th>对照组命中率</th><th>区分度</th></tr>
<tr><td>流动性 ≥ $10K</td><td>85.5%</td><td>13.6%</td><td class="hl">+72.0pp</td></tr>
<tr><td>流动性 ≥ $50K</td><td class="hl">67.1%</td><td>2.5%</td><td class="hl">+64.6pp</td></tr>
<tr><td>流动性 ≥ $100K</td><td>53.9%</td><td>1.6%</td><td>+52.4pp</td></tr>
<tr><td>24h成交 ≥ $50K</td><td class="hl">80.9%</td><td>3.9%</td><td class="hl">+77.0pp</td></tr>
<tr><td>6h成交 ≥ $50K</td><td>76.3%</td><td>3.1%</td><td>+73.2pp</td></tr>
<tr><td>6h成交 ≥ $100K</td><td>71.7%</td><td>1.9%</td><td>+69.9pp</td></tr>
</table>
<p style="margin-top:12px" class="note">基线（随机挑一个币上热榜）≈ 2.95%。早期流动性/成交信号能把命中率拉到 67%~81%，是基线的 20+ 倍。</p>
<p class="note"><b>结论</b>：<b>流动性本身是最早的信号，比聪明钱/KOL 更早</b>——刚迁移的币流动性到位 = 项目方认真做盘。这条此前是经验判断，本次用无偏数据坐实。</p>
</div>

<h2>四、S 级 · 值得重点看（11 个）</h2>
<table>
<tr><th>#</th><th>档</th><th>币种</th><th>链</th><th>叙事</th><th>类别</th><th>评分维度</th><th>聪明钱</th><th>KOL</th><th>流动性</th><th>24h成交</th><th>Top10%</th><th>rug</th><th>年龄</th></tr>
{''.join(row(x) for x in S)}
</table>

<h2>五、A 级 · 关注（38 个，按类别）</h2>
<h3>项目/协议类（{len([x for x in proj_SA if x['grade']=='A'])} 个）</h3>
<table>
<tr><th>#</th><th>档</th><th>币种</th><th>链</th><th>叙事</th><th>类别</th><th>评分维度</th><th>聪明钱</th><th>KOL</th><th>流动性</th><th>24h成交</th><th>Top10%</th><th>rug</th><th>年龄</th></tr>
{''.join(row(x) for x in proj_SA if x['grade']=='A')}
</table>
<h3>meme 类（{len([x for x in meme_SA if x['grade']=='A'])} 个）</h3>
<table>
<tr><th>#</th><th>档</th><th>币种</th><th>链</th><th>叙事</th><th>类别</th><th>评分维度</th><th>聪明钱</th><th>KOL</th><th>流动性</th><th>24h成交</th><th>Top10%</th><th>rug</th><th>年龄</th></tr>
{''.join(row(x) for x in meme_SA if x['grade']=='A')}
</table>

<h2>六、B 级 · 观察（{len(B)} 个）</h2>
<h3>项目/协议类（{len(proj_B)} 个）</h3>
<table>
<tr><th>#</th><th>档</th><th>币种</th><th>链</th><th>叙事</th><th>类别</th><th>评分维度</th><th>聪明钱</th><th>KOL</th><th>流动性</th><th>24h成交</th><th>Top10%</th><th>rug</th><th>年龄</th></tr>
{''.join(row(x) for x in proj_B)}
</table>
<h3>meme 类（{len(meme_B)} 个）</h3>
<table>
<tr><th>#</th><th>档</th><th>币种</th><th>链</th><th>叙事</th><th>类别</th><th>评分维度</th><th>聪明钱</th><th>KOL</th><th>流动性</th><th>24h成交</th><th>Top10%</th><th>rug</th><th>年龄</th></tr>
{''.join(row(x) for x in meme_B)}
</table>

<h2>七、C 级 · 一般（{len(C)} 个）</h2>
<h3>项目/协议类（{len(proj_C)} 个）</h3>
<table>
<tr><th>#</th><th>档</th><th>币种</th><th>链</th><th>叙事</th><th>类别</th><th>评分维度</th><th>聪明钱</th><th>KOL</th><th>流动性</th><th>24h成交</th><th>Top10%</th><th>rug</th><th>年龄</th></tr>
{''.join(row(x) for x in proj_C)}
</table>
<h3>meme 类（{len(meme_C)} 个）</h3>
<table>
<tr><th>#</th><th>档</th><th>币种</th><th>链</th><th>叙事</th><th>类别</th><th>评分维度</th><th>聪明钱</th><th>KOL</th><th>流动性</th><th>24h成交</th><th>Top10%</th><th>rug</th><th>年龄</th></tr>
{''.join(row(x) for x in meme_C)}
</table>

<h2>八、边界与风险</h2>
<div class="card">
<ul>
<li><b>回测覆盖有限</b>：热榜与战壕榜的 contractAddress 精确交集仅 152 个（两个数据源 CA 大量对不上），本结论基于这 152 个可严格认定的样本，不代表全市场。</li>
<li><b>早期信号是必要条件非充分条件</b>：流动性≥$50K 命中率 67% 意味着还有 33% 的高流动性币没上热榜。信号用于"缩小范围"，不是"保证会火"。</li>
<li><b>「盘感」不可替代</b>：情绪拐点、时点选择、庄家意图这些第三层判断，仍是交易经验的内化，机器写不成规则。本报告输出的是"研究优先级"，不是"买入建议"。</li>
<li><b>热点每日轮换</b>：本报告的热点叙事（Robinhood 股票代币化、CZ 果蝇、AI agent）基于 9-24 联网确认，会随时间失效。</li>
<li><b>数据边界</b>：本地数据库只积累到约 9-07 起的数据（约 18 天），完整周期需持续滚动积累历史快照。</li>
</ul>
</div>

<div class="note" style="margin-top:20px">本报告为研究分析结果，不构成投资建议。meme 币波动剧烈，注意风险。</div>
</div></body></html>'''

open('deliverables/deepseek-research-report-2026-09-24.html','w',encoding='utf-8').write(html_doc)
print('报告已生成:', len(html_doc), '字节')
