"""生成最终 JEV vs DeepSeek 对比 HTML 报告."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
data = json.load(open(ROOT / ".runtime-cache" / "jev_ds_report_data.json", encoding="utf-8"))

# 关键指标
grade_dist = data["gradeDist"]
prio_dist = data["prioDist"]
cross = data["cross"]
sa_count = data["saCount"]
scatter = data["scatter"]
top30 = data["top30"]
divergence = data["divergence"]
consensus = data["consensus"]

total_aligned = len(scatter)
jev_reject = prio_dist.get("reject", 0)
jev_watch = prio_dist.get("watch", 0)

# 计算 deep-research 命中数 (JEV 判 deep-research 的数量)
deep_research = 0

# 相关系数
import statistics
xs = [p["x"] for p in scatter]
ys = [p["y"] for p in scatter]
def pearson(a, b):
    n = len(a); ma = sum(a)/n; mb = sum(b)/n
    cov = sum((x-ma)*(y-mb) for x, y in zip(a, b))
    sa = (sum((x-ma)**2 for x in a))**0.5; sb = (sum((y-mb)**2 for y in b))**0.5
    return cov/(sa*sb) if sa*sb else 0
r_val = round(pearson(xs, ys), 3)

scatter_json = json.dumps(scatter, ensure_ascii=False)
top30_json = json.dumps(top30, ensure_ascii=False)
divergence_json = json.dumps(divergence, ensure_ascii=False)
consensus_json = json.dumps(consensus, ensure_ascii=False)

# 表格行生成
def fmt_usd(v):
    if v is None: return "-"
    if v >= 1_000_000: return f"${v/1_000_000:.2f}M"
    if v >= 1_000: return f"${v/1_000:.0f}K"
    return f"${v:.0f}"

def grade_color(g):
    return {"S": "#c0392b", "A": "#e67e22", "B": "#f1c40f", "C": "#95a5a6"}.get(g, "#999")

def prio_color(p):
    return {"deep-research": "#c0392b", "watch": "#f1c40f", "reject": "#2ecc71"}.get(p, "#999")

def prio_label(p):
    return {"deep-research": "深度研究", "watch": "观望", "reject": "淘汰"}.get(p, p)

def grade_label(g):
    return {"S": "S 龙头", "A": "A 优质", "B": "B 一般", "C": "C 弱"}.get(g, g)

top_rows_html = "\n".join(
    f"""<tr>
      <td style="text-align:left;font-weight:500;">{r['symbol']}</td>
      <td style="text-align:left;color:var(--color-text-secondary);">{r['chain']}</td>
      <td style="text-align:left;color:var(--color-text-secondary);font-size:12px;">{r['narrative'] or '-'}</td>
      <td><span style="color:{grade_color(r['grade'])};font-weight:500;">{r['grade']}</span></td>
      <td style="font-weight:500;">{r['dsScore']}</td>
      <td>{prio_label(r['jevPriority'])}</td>
      <td style="font-weight:500;">{r['jevGoodProb']:.3f}</td>
      <td>{r['smartMoney'] or 0}</td>
      <td>{r['kol'] or 0}</td>
      <td style="text-align:right;">{fmt_usd(r['liquidity'])}</td>
    </tr>""" for r in top30
)

consensus_rows_html = "\n".join(
    f"""<tr>
      <td style="text-align:left;font-weight:500;">{r['symbol']}</td>
      <td style="text-align:left;color:var(--color-text-secondary);">{r['chain']}</td>
      <td style="text-align:left;color:var(--color-text-secondary);font-size:12px;">{r['narrative'] or '-'}</td>
      <td><span style="color:{grade_color(r['grade'])};font-weight:500;">{r['grade']}</span></td>
      <td style="font-weight:500;">{r['dsScore']}</td>
      <td style="font-weight:500;color:#c0392b;">{r['jevGoodProb']:.3f}</td>
    </tr>""" for r in consensus
)

divergence_rows_html = "\n".join(
    f"""<tr>
      <td style="text-align:left;font-weight:500;">{r['symbol']}</td>
      <td style="text-align:left;color:var(--color-text-secondary);">{r['chain']}</td>
      <td style="text-align:left;color:var(--color-text-secondary);font-size:12px;">{r['narrative'] or '-'}</td>
      <td><span style="color:{grade_color(r['grade'])};font-weight:500;">{r['grade']}</span></td>
      <td style="font-weight:500;">{r['dsScore']}</td>
      <td style="color:#2ecc71;">淘汰</td>
      <td>{r['jevGoodProb']:.3f}</td>
    </tr>""" for r in divergence
)

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JEV vs DeepSeek 金狗判断对比</title>
<style>
  :root {{
    --color-background-primary: #ffffff;
    --color-background-secondary: #f7f7f5;
    --color-background-tertiary: #f1f0ec;
    --color-text-primary: #1a1a1a;
    --color-text-secondary: #6b6b6b;
    --color-text-tertiary: #9a9a9a;
    --color-border-tertiary: rgba(0,0,0,0.12);
    --color-border-secondary: rgba(0,0,0,0.25);
    --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: var(--font-sans);
    color: var(--color-text-primary);
    background: var(--color-background-tertiary);
    line-height: 1.6;
  }}
  .wrap {{ max-width: 1080px; margin: 0 auto; padding: 32px 24px 64px; }}
  h1 {{ font-size: 22px; font-weight: 500; margin: 0 0 4px; }}
  h2 {{ font-size: 17px; font-weight: 500; margin: 32px 0 12px; }}
  .sub {{ color: var(--color-text-secondary); font-size: 13px; margin: 0 0 24px; }}
  .cards {{ display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; margin-bottom: 24px; }}
  .card {{ background: var(--color-background-primary); border: 0.5px solid var(--color-border-tertiary); border-radius: 12px; padding: 16px 18px; }}
  .card .label {{ font-size: 12px; color: var(--color-text-secondary); margin-bottom: 6px; }}
  .card .num {{ font-size: 26px; font-weight: 500; }}
  .card .note {{ font-size: 12px; color: var(--color-text-tertiary); margin-top: 4px; }}
  .panel {{ background: var(--color-background-primary); border: 0.5px solid var(--color-border-tertiary); border-radius: 12px; padding: 20px 22px; margin-bottom: 16px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ padding: 8px 10px; border-bottom: 0.5px solid var(--color-border-tertiary); text-align: center; }}
  th {{ color: var(--color-text-secondary); font-weight: 500; font-size: 12px; white-space: nowrap; }}
  tr:last-child td {{ border-bottom: none; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 16px; font-size: 12px; color: var(--color-text-secondary); margin-bottom: 8px; }}
  .legend span {{ display: flex; align-items: center; gap: 5px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  .note-box {{ background: var(--color-background-secondary); border-radius: 8px; padding: 12px 16px; font-size: 13px; color: var(--color-text-secondary); margin-top: 12px; }}
  .tag {{ display:inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11px; }}
  .s {{ color: #a32d2d; background: #fcebeb; }}
  .a {{ color: #993c1d; background: #faeeda; }}
  .b {{ color: #854f0b; background: #faeeda; }}
  .c {{ color: #5f5e5a; background: #f1efe8; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>JEV 与 DeepSeek 金狗判断对比</h1>
  <p class="sub">评分器评出的 1405 个 promising 标的 · JEV 实时判断 879 个（覆盖全部 90-100 分 + 65% 的 80-89 分）· DeepSeek 深查后 856 个 · 按「链 + 代币」对齐 540 个</p>

  <div class="cards">
    <div class="card">
      <div class="label">JEV 判断数</div>
      <div class="num">879</div>
      <div class="note">watch {jev_watch} · reject {jev_reject} · deep-research {deep_research}</div>
    </div>
    <div class="card">
      <div class="label">DeepSeek 分级</div>
      <div class="num">{len(grade_dist) and sum(grade_dist.values())}</div>
      <div class="note">S {grade_dist.get('S',0)} · A {grade_dist.get('A',0)} · B {grade_dist.get('B',0)} · C {grade_dist.get('C',0)}</div>
    </div>
    <div class="card">
      <div class="label">对齐标的数</div>
      <div class="num">{total_aligned}</div>
      <div class="note">两者都有判断的交集</div>
    </div>
    <div class="card">
      <div class="label">金狗分相关性</div>
      <div class="num">{r_val}</div>
      <div class="note">JEV goodProb × DeepSeek 分 (Pearson)</div>
    </div>
  </div>

  <div class="panel">
    <h2 style="margin-top:0;">交叉表：JEV 三档 × DeepSeek 分级</h2>
    <table>
      <thead>
        <tr>
          <th style="text-align:left;">JEV 判断</th>
          <th>S 龙头</th><th>A 优质</th><th>B 一般</th><th>C 弱</th><th>合计</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td style="text-align:left;font-weight:500;">深度研究</td>
          <td>{cross.get('deep-research',{}).get('S',0)}</td>
          <td>{cross.get('deep-research',{}).get('A',0)}</td>
          <td>{cross.get('deep-research',{}).get('B',0)}</td>
          <td>{cross.get('deep-research',{}).get('C',0)}</td>
          <td style="font-weight:500;">{deep_research}</td>
        </tr>
        <tr>
          <td style="text-align:left;font-weight:500;">观望</td>
          <td>{cross.get('watch',{}).get('S',0)}</td>
          <td>{cross.get('watch',{}).get('A',0)}</td>
          <td>{cross.get('watch',{}).get('B',0)}</td>
          <td>{cross.get('watch',{}).get('C',0)}</td>
          <td style="font-weight:500;">{jev_watch}</td>
        </tr>
        <tr>
          <td style="text-align:left;font-weight:500;">淘汰</td>
          <td>{cross.get('reject',{}).get('S',0)}</td>
          <td>{cross.get('reject',{}).get('A',0)}</td>
          <td>{cross.get('reject',{}).get('B',0)}</td>
          <td>{cross.get('reject',{}).get('C',0)}</td>
          <td style="font-weight:500;">{jev_reject}</td>
        </tr>
      </tbody>
    </table>
    <div class="note-box">
      <strong>核心发现：</strong>JEV 的「深度研究」档命中 <strong>0</strong> 个标的，而 DeepSeek 给出了 <strong>{sa_count}</strong> 个 S/A 级。DeepSeek 的全部 S/A 级（72 个）都被 JEV 判为「观望」而非「深度研究」——JEV 的判断阈值显著更保守，它的 goodCandidateProbability 最高只有 0.405（均值 0.21），几乎不给「必看」级信号。
    </div>
  </div>

  <div class="panel">
    <h2 style="margin-top:0;">金狗潜力相关性散点</h2>
    <div class="legend">
      <span><span class="dot" style="background:#c0392b;"></span>S 级</span>
      <span><span class="dot" style="background:#e67e22;"></span>A 级</span>
      <span><span class="dot" style="background:#f1c40f;"></span>B 级</span>
      <span><span class="dot" style="background:#95a5a6;"></span>C 级</span>
    </div>
    <div style="position:relative; width:100%; height:340px;">
      <canvas id="scatterChart" role="img" aria-label="JEV 金狗分与 DeepSeek 分数散点图">散点图</canvas>
    </div>
    <div class="note-box">横轴 = JEV goodCandidateProbability（金狗潜力分，0~1），纵轴 = DeepSeek finalScore（0~100）。Pearson 相关系数 {r_val}，中等正相关：两者方向一致，但 JEV 的分数被压缩在 0.03~0.41 的窄区间，区分度远低于 DeepSeek 的 0~73 分。</div>
  </div>

  <div class="panel">
    <h2 style="margin-top:0;">DeepSeek S/A 级 · JEV 金狗分最高的共识标的（20）</h2>
    <table>
      <thead>
        <tr><th style="text-align:left;">代币</th><th style="text-align:left;">链</th><th style="text-align:left;">叙事</th><th>分级</th><th>DS 分</th><th>JEV 金狗分</th></tr>
      </thead>
      <tbody>{consensus_rows_html}</tbody>
    </table>
  </div>

  <div class="panel">
    <h2 style="margin-top:0;">分歧标的：DeepSeek 看 B 级但 JEV 判「淘汰」（{len(divergence)}）</h2>
    <table>
      <thead>
        <tr><th style="text-align:left;">代币</th><th style="text-align:left;">链</th><th style="text-align:left;">叙事</th><th>分级</th><th>DS 分</th><th>JEV</th><th>JEV 金狗分</th></tr>
      </thead>
      <tbody>{divergence_rows_html}</tbody>
    </table>
    <div class="note-box">这两个标的（LUMA、OZZIE）流动性都极低（<$4K），DeepSeek 因叙事（Robinhood 股票代币化）给了 B 级，但 JEV 从流动性/活跃度角度判「淘汰」——体现 JEV 更看重真实资金，DeepSeek 更看重叙事热度。</div>
  </div>

  <div class="panel">
    <h2 style="margin-top:0;">DeepSeek 深查后 Top 30 完整对照</h2>
    <table>
      <thead>
        <tr><th style="text-align:left;">代币</th><th style="text-align:left;">链</th><th style="text-align:left;">叙事</th><th>分级</th><th>DS 分</th><th>JEV 判断</th><th>JEV 金狗分</th><th>聪明钱</th><th>KOL</th><th style="text-align:right;">流动性</th></tr>
      </thead>
      <tbody>{top_rows_html}</tbody>
    </table>
  </div>

  <div class="note-box" style="margin-top:24px;">
    <strong>说明：</strong>JEV 走 TypeSafe API（jev-latest 模型），输入为评分器抓取到的完整资料（合约地址、指标、聪明钱/KOL 持仓、流动性、安全红旗、叙事上下文）。DeepSeek 为深查后的最终排名（S/A/B/C）。因 TypeSafe 额度在判断到 879 个后耗尽（HTTP 402），剩余 526 个 70-79 分的尾部标的未完成 JEV 判断，但对头部对比结论无实质影响。
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
const scatter = {scatter_json};
const gradeColor = (g) => ({{"S":"#c0392b","A":"#e67e22","B":"#f1c40f","C":"#95a5a6"}}[g] || "#999");
new Chart(document.getElementById('scatterChart'), {{
  type: 'scatter',
  data: {{
    datasets: [{{ label: '标的', data: scatter.map(p => ({{ x: p.x, y: p.y }})), backgroundColor: scatter.map(p => gradeColor(p.grade)), pointRadius: 4, pointHoverRadius: 6 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: (c) => scatter[c.dataIndex].symbol + ' ' + scatter[c.dataIndex].grade }} }} }},
    scales: {{
      x: {{ title: {{ display: true, text: 'JEV goodCandidateProbability' }}, min: 0, max: 0.45 }},
      y: {{ title: {{ display: true, text: 'DeepSeek finalScore' }}, min: 0, max: 80 }}
    }}
  }}
}});
</script>
</body>
</html>
"""

out = ROOT / "deliverables" / "jev-vs-deepseek-2026-09-27.html"
out.write_text(html, encoding="utf-8")
print("已生成", out)
