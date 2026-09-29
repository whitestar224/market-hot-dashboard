# 工程目录索引（tools/ 与一次性脚本）

> 状态：说明文档（Phase 2 收尾）
> 日期：2026-09-29

## 1. 目的

项目根目录与 `tools/` 下混有「常驻工具」与「一次性分析/验证脚本」。本文档建立索引，
说明每个文件的角色，避免后续维护时误删仍在使用的脚本，或把一次性脚本误当常驻工具。

## 2. tools/ 目录（常驻工具，勿删）

### 2.1 重构安全网（Phase 3 核心，勿动）

| 文件 | 角色 |
|---|---|
| `refactor_baseline.py` | Golden Master 结构指纹基线（端点表 + shape_of + VOLATILE_KEY_DICTS） |
| `extract_module.py` | 单体机械抽取工具（--names / --range 两种模式） |
| `split_analyzer.py` | 作用域分析（extract_module 复用） |
| `verify_split.py` | 批次验收闸门 1：导入/ROOT/对象同一性/重绑定泄漏/残留 |
| `ab_verify.py` | 同条件 A/B 对照（静态字节 + API 结构指纹） |
| `ab_tests.py` | 两轮测试失败集差集比对 |
| `run_tests.py` | 统一测试入口（stdlib unittest discover + 看门狗） |

### 2.2 运营工具

| 文件 | 角色 |
|---|---|
| `publish_automation_briefs.py` | 发布自动化简报（被 electron 打包引用） |
| `upgrade_research_xmind_v48.py` | 研报 xmind 升级迁移 |
| `render_static_kline.py` | 静态 K 线渲染 |
| `check_monitor_buy_quotes.py` | 校验 monitor-buy 报价 |
| `audit_popup_delivery.py` | 弹窗投递审计 |

### 2.3 前端一次性验证脚本（Node，跑完即弃，可归档）

| 文件 | 角色 |
|---|---|
| `dragon_wave_*.js`（7 个） | dragon-wave 预计算/缓存兼容/因果回放/结构审查验证 |
| `verify_dragon_wave_*.js`（3 个） | 同上，性能/因果/结构验证 |
| `audit_*.js` / `compare_*.js` / `replay_*.js` / `report_*.js` | 一次性审计/对比/回放 |
| `aicoin_probe.js` | AICOIN 接口探测 |
| `monitor-buy-executor-entry.js` | monitor-buy esbuild 打包入口（**被 package.json 引用，勿删**） |

### 2.4 一次性 PowerShell（跑完即弃）

`fetch_confirmed_audit_candles.ps1` / `fetch_husdt_5m_regression.ps1` /
`optimize_workspace.ps1` / `recover_napcat_bridge.ps1` / `run_dragon_wave_full_batch.ps1` /
`start_dragon_wave_precompute.ps1`

## 3. 根目录一次性脚本（注意：不可贸然搬移/删除）

### 3.1 版本迭代脚本（明显旧版本，疑似可归档）

| 文件 | 说明 |
|---|---|
| `backtest_unbiased2.py` / `backtest_unbiased_v3.py` | 回测无偏版本迭代 |
| `deepseek_final_rank_v6.py` / `deepseek_final_rank_v7.py` | DeepSeek 排序版本迭代 |
| `online_research_rank_v4.py` | 在线研报排序版本迭代 |

### 3.2 🔴 看似一次性、实为被依赖的脚本

`score_trench_board_batch.py` 被 `gmgn_local_trench_analyzer.py` 在**函数体内懒 import**
（第 434 行 `_load_binance_wallet_hot_symbols`、第 450 行 `score_trench_row`）。这类「懒 import」
让依赖关系不可见，**搬移前必须逐个 grep 核实**。教训：根目录 .py 的 import 依赖不可靠，
不能凭文件名判断是否一次性。

## 4. 已知临时垃圾文件（建议清理，已 gitignore）

- `tmp_chunk_809501.js` / `tmp_gmgn_app.js`（各 3,263 行）：GMGN 前端 webpack 打包产物
  （含 RainbowKit/WalletConnect 钱包连接器代码），某次抓取 GMGN 页面误存，未被任何 HTML
  引用，已被 `.gitignore` 的 `tmp_*.js` 规则忽略。可安全删除。
- `.__ab_server_old.py`（约 2.4MB）：A/B 验证工具的旧版临时副本，已被 gitignore。

## 5. 维护约定

1. 新增一次性脚本优先放 `tools/`，并在本文档登记。
2. 根目录 .py 是「扁平 import」模块（`import X` 引用同目录 sibling），搬移会破坏 import，
   除非先 grep 全仓确认无引用。
3. 常驻工具（2.1/2.2）禁止删除或改名，它们是重构验收与运营的依赖。
