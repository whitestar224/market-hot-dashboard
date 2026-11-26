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

## 3. 分层目录（2026-11-26 起）

根目录**只留入口**：`server.py`、`service_guard.py`、`worker_runner.py`、双击启动用的
`*.cmd` / `*.ps1`、`README.md`、`requirements.txt`。其余全部按模块分层：

| 目录 | 内容 |
|---|---|
| `src/app/api/` | 已抽出的路由（seo / dragon_wave / gigax / health / smart_money / login_rate） |
| `src/app/core/` | 基础设施：`paths`、`state`、`config`、网络代理、`quiet_http_server`、微信 / QQ / 钉钉通道 |
| `src/app/sources/` | 上游客户端：GMGN、Arkham、币安 Web3 / Agentic、RootData、空投、快讯、OpenNews… |
| `src/app/monitors/` | 监控器：聪明钱、做市商、结构、前高、夹子、战壕、Dev Launch、CEX 上新… |
| `src/app/alerts/` | 弹窗与事件流：`desktop_alert`、`alert_stream_overlay`、`trench_overlay`、`event_flow` |
| `src/app/research/` | 链上投研与快速决策（含 `score_trench_board_batch`） |
| `web/` | 全部前端 html / css / js / assets |
| `scripts/` | 离线脚本（回测、报告生成、榜单构建），不属于运行期 |

### 3.1 路径铁律

`app.core.paths` 是唯一真源：`ROOT` / `WEB_DIR` / `APP_DIR` / `runtime_dir()`。

- 搬进 `src/app/<pkg>/` 之后，`Path(__file__).resolve().parent` **不再等于项目根**，
  以前那套 `Path(__file__).parent / ".runtime-cache"` 会写出 `src/app/<pkg>/.runtime-cache`，
  缓存与 SQLite 全部写丢且不报错。一律改用 `runtime_dir()`。
- `server.py` 里以子进程拉起 `.py` 的地方（`desktop_alert.py`、`price_watch_live_worker.py`）
  必须用 `APP_DIR` 拼路径。
- 独立运行入口（`desktop_alert`、`desktop_alert_bridge`、`alert_stream_overlay`、
  `quiet_http_server`、`dingtalk_price_watch_bot`、`prune_chain_ecosystem`、
  `wechat_group_monitor`、`qq_onebot_bridge`）自带 `sys.path` 兜底块，
  因为它们会被 `.cmd` / 计划任务 / `sys.executable` 直接当脚本跑。

### 3.2 跨模块依赖的教训（保留原文）

`score_trench_board_batch.py` 曾被 `gmgn_local_trench_analyzer.py` 在**函数体内懒 import**
（`_load_binance_wallet_hot_symbols` / `score_trench_row`）。这类「懒 import」让依赖关系不可见，
**搬移前必须逐个 grep 核实**。现两者同在 `src/app/research`、`src/app/monitors`，
懒 import 已改成绝对导入。

## 4. 已知临时垃圾文件（建议清理，已 gitignore）

- `tmp_chunk_809501.js` / `tmp_gmgn_app.js`（各 3,263 行）：GMGN 前端 webpack 打包产物
  （含 RainbowKit/WalletConnect 钱包连接器代码），某次抓取 GMGN 页面误存，未被任何 HTML
  引用，已被 `.gitignore` 的 `tmp_*.js` 规则忽略。可安全删除。
- `.__ab_server_old.py`（约 2.4MB）：A/B 验证工具的旧版临时副本，已被 gitignore。

## 5. 维护约定

1. 新增模块一律放 `src/app/<pkg>/`，并在本文档登记；新增一次性脚本放 `scripts/`。
2. 不要再把业务 `.py` 放回根目录：根目录只留入口，`src` 由入口注入 `sys.path`。
3. 路径一律走 `app.core.paths`，禁止 `Path(__file__).parent` 推导项目根。
4. 常驻工具（2.1/2.2）禁止删除或改名，它们是重构验收与运营的依赖。
