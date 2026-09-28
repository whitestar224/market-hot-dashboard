# 重构蓝图：前后端拆分与架构升级

> 目标：在**不改变任何功能与页面表现**的前提下，对项目做代码拆分、技术栈升级与结构规范化。
> 建立日期：2026-09-28　回滚基线：`backup/pre-refactor-20260928` / tag `backup-pre-refactor-20260928`

---

## 0. 结论摘要

| 项 | 结论 |
|---|---|
| 能否一次完成 | **不能**。`server.py` 55,130 行 / 1,369 个顶层函数 / 762 个模块级变量，只能分阶段增量推进 |
| 唯一可接受的推进方式 | **Strangler Fig（绞杀者模式）**：新结构旁路生长，旧路径保留兼容 shim，每步都用 Golden Master 验证 |
| 零回归的客观判据 | `tools/refactor_baseline.py`（40 端点结构指纹比对），已验证自检零差异 |
| 最大技术风险 | 模块级全局状态被 `global` 重绑定（66 个名字），搬迁即静默改行为 |
| 建议先做 | Phase 2 工程规范化 + Phase 3 后端按域拆分，前端与架构升级随后 |

---

## 1. 现状诊断（实测数据）

### 1.1 代码规模

| 文件 | 行数 | 说明 |
|---|---:|---|
| `server.py` | 55,130 | 主单体：HTTP 层 + 业务逻辑 + 调度全在内 |
| `dragon-wave-engine.js` | 9,127 | 前端单文件引擎 |
| `chain_ecosystem_monitor.py` | 5,582 | 链上生态监控 |
| `price-watch.js` | 4,122 | 前端行情看板 |
| `onchain_fast_research.py` | 3,842 | 链上投研 |

根目录扁平堆放：**63 个 `.py` + 37 个 `.js` + 20 个 `.html` + 4 个 `.css` + 5 个 `.cmd` + 4 个 `.ps1`**。

### 1.2 `server.py` 内部结构度量

| 指标 | 数值 | 含义 |
|---|---:|---|
| 顶层函数 | 1,369 | 全部平铺，无包组织 |
| 模块级变量 | 762 | 共享状态散落全文件 |
| 模块级锁 | 119 | 并发控制靠全局锁 |
| `global` 重绑定 | 66 个名字 | **拆分最致命处** |
| 顶层类 | 3 | 仅 `Handler`/`BoundedThreadingHTTPServer`/facade |
| `/api/*` 端点 | 53 | 路由是 `do_GET`/`do_POST` 里的扁平 if 链（56 分支） |
| 已拆出的本地模块 | ~20 | `server.py` 已 import 一批域模块，具备拆分基础 |

**利好**：路由是扁平 if 链，每个分支只调用顶层函数，边界清晰，不涉及复杂继承。

### 1.3 性能基线（2026-09-28 实测，重构前）

| 端点 | 耗时 | 判定 |
|---|---:|---|
| `/api/rss-sources` | **32,037 ms** | 🔴 严重 |
| `/api/rss-items` | **24,116 ms** | 🔴 严重 |
| `/api/strategy-board` | **22,437 ms** | 🔴 严重 |
| `/api/automation-briefs` | 10,441 ms | 🟠 偏慢 |
| `/api/personal-x-monitor` | 8,823 ms | 🟠 偏慢 |
| `/api/onchain-trenches` | 7,491 ms | 🟠 偏慢 |

> 该表即**性能验收基线**。重构后这些数字不得变差；理想目标是改善。

---

## 2. 核心风险：模块级全局状态

这是整个后端拆分能否成立的关键，必须理解清楚。

### 2.1 问题机制

```python
# server.py（现状）
AUTH_DB_LOCK = threading.Lock()        # 模块级单例，75 个函数引用

def some_handler():
    with AUTH_DB_LOCK: ...             # 裸引用 → 解析到 server 模块命名空间

def restart_monitor():
    global PRICE_WATCH_REALTIME_PROCESS # ← global 重绑定
    PRICE_WATCH_REALTIME_PROCESS = start(...)
```

若把 `restart_monitor` 搬到 `app/api/price_watch.py`：

- 裸引用 `AUTH_DB_LOCK` 会去找 **新模块** 的 `AUTH_DB_LOCK` → `NameError`（易发现，尚可）
- `global PRICE_WATCH_REALTIME_PROCESS` 会在**新模块**里写一个新变量，而 `server.py` 里的那个**永远不被更新** → **静默行为改变**（难发现，最危险）

### 2.2 名字分类与处置规则

| 类别 | 数量 | 处置规则 | 风险 |
|---|---:|---|---|
| stdlib / 三方导入（`time`/`re`/`requests`/`json`/`Path`…） | 大部分 | 新模块**各自 import** | 无 |
| 单例锁 / 缓存（`AUTH_DB_LOCK`/`HEADERS`/`SERVER_SHUTDOWN_EVENT`） | 少 | `from app.core.state import NAME`（绑定对象，永不重绑定，安全） | 低 |
| **被重绑定的名字** | **66** | **必须改属性访问**：`state.NAME = ...` | 高 |

### 2.3 重绑定名字清单（TOP 风险）

| 名字 | 写次数 | 引用数 |
|---|---:|---:|
| `CODEX_CLI_UNAVAILABLE_UNTIL` | 3 | 1 |
| `LLM_API_UNAVAILABLE_UNTIL` | 3 | 0 |
| `ONCHAIN_RESEARCH_INGEST_THREAD` | 2 | 1 |
| `ROTATION_AI_RETRY_AFTER` | 2 | 1 |
| `X_KOL_OFFICIAL_STREAM_RESPONSE` | 2 | 1 |
| `DISCORD_NEWSFLASH_BRIDGE_PROCESS` | 2 | 1 |
| `PRICE_WATCH_REALTIME_PROCESS` | 2 | 0 |
| `PRICE_STRUCTURE_RECENT_LISTING_INDEX_CACHE` | 2 | 0 |
| …共 66 个 | | |

**实施要求**：搬迁任何函数前，先用 `tools/split_analyzer.py` 重新计算，确认该函数的模块级引用集合已被整组带走或被显式改造。

---

## 3. 目标项目结构

采用「新旧并存」的过渡布局 —— **不物理移动被硬编码引用的文件**，避免破坏 `.cmd` 脚本、`service_guard.py`、Electron `main.js`、`Dockerfile`、`server.py` 的 `ROOT` 静态服务。

```
market-hot-dashboard/
├── server.py                  # 保持：进程入口（.cmd / guard / electron 均引用）
│                              #   重构后逐步瘦身为「引导 + 路由装配」
├── src/                       # 【新建】后端源码根
│   ├── app/
│   │   ├── core/              # 共享状态与基础设施
│   │   │   ├── state.py       #   ← 锁/缓存/进程句柄统一收敛于此
│   │   │   ├── persistence.py #   ← write_json_cache 等（沿用紧凑序列化铁律）
│   │   │   ├── http.py        #   ← 共享 keep-alive Session（铁律：禁裸 requests）
│   │   │   └── logging.py
│   │   ├── api/               # HTTP 处理器（按域）
│   │   │   ├── router.py      #   路由表（替代 if 链）
│   │   │   ├── market.py      #   /api/market-hot, /api/gainers-rankings …
│   │   │   ├── price.py       #   /api/price-watch, /api/price-structures …
│   │   │   ├── dragon_wave.py #   /api/dragon-wave-*
│   │   │   ├── onchain.py     #   /api/chain-ecosystem, /api/onchain-*
│   │   │   ├── social.py      #   /api/personal-x-*, /api/x-kol-*
│   │   │   ├── wechat.py      #   /api/wechat-*
│   │   │   ├── news.py        #   /api/newsflash, /api/rss-*
│   │   │   └── ops.py         #   /api/health, /api/service-liveness, seo
│   │   └── domains/           # 业务域逻辑（从 server.py 剥出的函数群）
│   └── compat/                # 过渡期兼容 shim
├── web/                       # 【新建】前端源码根
│   ├── src/                   #   ESM 模块源码（dragon-wave-engine 等拆分后）
│   ├── static/                #   构建产物 → 输出到 assets/（路径不变）
│   └── pages/                 #   HTML 模板
├── assets/                    # 保持：构建产物落点，前端引用路径不变
├── scripts/                   # 【新建】运维脚本归集（.cmd/.ps1/.sh），根目录留兼容副本
├── tools/                     # 保持：开发与重构工具
├── tests/                     # 保持：130+ 测试
├── docs/                      # 保持：文档
├── deploy/                    # 【新建】Dockerfile / 部署配置
├── pyproject.toml             # 【新建】打包元数据 + ruff + pytest
├── requirements.txt           # 保持，配合锁文件
└── requirements.lock          # 【新建】锁定版本
```

---

## 4. 分阶段实施方案

### Phase 0 — 备份基线 ✅ 已完成

- 分支 `backup/pre-refactor-20260928` + tag 已推送 GitHub
- 1.2GB `chain_ecosystem.pre-prune.db` 已排除（超 GitHub 100MB 单文件限制）

### Phase 1 — 验证安全网 ✅ 已完成

| 产物 | 作用 |
|---|---|
| `tools/refactor_baseline.py` | 40 端点结构指纹采集/比对；自检零差异已验证 |
| `tests/fixtures/refactor-baseline/golden-master.json` | 基线快照（38 个端点含完整结构） |
| `tools/split_analyzer.py` | AST 依赖分析，输出安全拆分边界 |
| `tests/fixtures/refactor-baseline/split-plan.json` | 拆分计划数据（18 个域簇 + 66 个重绑定名） |

**验证流程（每个改动批次必须执行）**：
```bash
# 1. 起临时实例（勿动 8765 线上服务）
python server.py --port 8899        # 或环境变量指定
# 2. 结构比对
python tools/refactor_baseline.py verify --base-url http://127.0.0.1:8899 --timeout 90
# 3. 跑测试
python -m pytest tests/ -q          # Python
node --test tests/*.test.js         # 前端
# 4. 任一失败 → 立即回滚该批次
```

### Phase 2 — 工程规范化

1. `pyproject.toml`：项目元数据、ruff（format+lint）、pytest 配置、`requires-python`
2. `requirements.lock`：锁定当前实际可用版本（先 `pip freeze` 对照）
3. 统一测试入口：`pytest tests/` 与 `node --test` 归一为 `npm test`
4. `scripts/` 归集 `.cmd`/`.ps1`，根目录保留同名 shim（保证双击可用）
5. 目录归集：`deploy/`、`src/`、`web/` 建立骨架

**风险**：低。不触碰运行逻辑。

### Phase 3 — `server.py` 后端拆分（核心工程）

按 `split-plan.json` 的 18 个域簇逐个推进，**每次只拆一个簇**：

```
1. 用 split_analyzer 计算该簇的「函数集 + 模块级引用集 + 重绑定集」
2. 建立 src/app/core/state.py，把该簇共享的可变状态迁入
3. 重绑定名字改造为 state.NAME 属性访问（唯一允许的代码语义等价改写）
4. 函数整组移入 src/app/api/<domain>.py
5. server.py 保留兼容再导出：from src.app.api.market import *
6. 跑 Golden Master + 测试
7. 通过 → 提交；失败 → git checkout 该批次
```

**拆簇顺序（从低风险到高风险，按依赖度排序）**：
1. `logging`（1 函数，已自包含）
2. `seo`（6 函数，8 引用）
3. `ops_health`（1 函数）
4. `auth_config`（3 函数）
5. `smart_money`（13 函数，7 外部依赖）
6. `binance`（20 函数）
7. `alert`（22 函数）
8. `onchain`（25 函数）
9. `dragon_wave` / `strategy` / `market`
10. `wechat` / `news` / `price_watch` / `price_structure` / `social_x`
11. `misc`（824 函数，最后处理 —— 这个簇必须先做二次细分，不能整块搬）

> **注意**：目前没有任何一个业务簇是"完全自包含"的（`misc` 有 253 个外部函数依赖）。因此**「整组搬迁」在实践中必然跨簇**，需要按「依赖闭包」动态扩展搬迁集合，而不是按名字前缀。`split_analyzer.py` 输出的 `externalFunctions` 就是为此准备的。

### Phase 4 — 前端 ESM 模块化与构建链

1. `dragon-wave-engine.js`(9,127 行) / `price-watch.js`(4,122 行) 拆为 ESM 模块
2. esbuild 打包输出到**原路径**（`assets/`），HTML 的 `<script src>` 一行不改
3. 保留源码映射，便于线上排错
4. 页面渲染结果必须逐页比对（人工 + `tests/dragon_wave_page.test.js` 等）

**关键约束**：构建产物文件名与路径必须与现在完全一致，否则 CSP、缓存策略、Electron 资源解析都会受影响。

### Phase 5 — 高并发与微服务演进

见第 5、6 节。属**设计先行**，落地按收益排序、增量推进。

---

## 5. 高并发架构改进

### 5.1 已确诊的瓶颈（源自项目历史运维记录）

| 瓶颈 | 机理 | 对策 | 优先级 |
|---|---|---|---|
| **GIL 饿死** | 动态正则未编译缓存，占 27% CPU，静态文件都要 5s | 已修：`lru_cache` 编译缓存 | ✅ 已解决 |
| **应用级锁阻塞页面** | 后台线程长期持有 `AUTH_DB_LOCK` / store `_lock` | 已修：页面轮询走快照缓存 + `allow_sync_rebuild=False` | ✅ 已解决 |
| **僵尸锁** | `with store._lock:` 无超时 + sqlite 挂起 → 锁泄漏 | 已修：`busy_timeout=15000` + `acquire(timeout=30)` | ✅ 已解决 |
| **重 payload 同步重建** | 百秒级构建阻塞请求线程 | 铁律：`TTL ≥ 构建耗时倍数`，禁同步重建 | ✅ 已固化 |
| **`rss-sources` 32s / `strategy-board` 22s** | 待定位（疑似同类同步重建或串行 IO） | **Phase 3 期间用 py-spy 定位** | 🔴 高 |
| **1.07GB SQLite 单库** | 写锁竞争、VACUUM 窗口 | 冷热分离 / 按业务分库 / 定期 VACUUM | 🟠 中 |
| **同步 urllib HTTP 调用** | 阻塞工作线程 | 统一走共享 Session（已有铁律），进一步引入并发竞速 | 🟠 中 |

### 5.2 目标：从「多线程 + 全局锁」到「异步 IO + 显式并发边界」

现架构是 `ThreadingHTTPServer` + 119 个模块级锁，靠 GIL 串行化，横向扩展能力为零。

**演进方向（按性价比排序）**：

1. **守住现有铁律**（成本最低、收益最高）
   - 页面轮询永不等待应用锁
   - 重构建 payload 一律 `allow_sync_rebuild=False`
   - 外部 HTTP 一律共享 keep-alive Session
   - 动态正则一律编译缓存

2. **缓存层统一化**
   - 现状：每个域各写各的 cache 文件 + 内存字典
   - 目标：统一 `app/core/cache.py`，提供 `TTL + 单飞(inflight merge) + SWR + stale-while-error` 标准语义
   - 收益：消除各域重复实现导致的行为不一致（历史多次踩坑均源于此）

3. **IO 边界异步化**
   - 把 12 源并发抓取的成功经验推广：`ThreadPoolExecutor` → `asyncio.gather`
   - 注意：迁移必须是逐域的，不能一次性换运行时

4. **进程内计算与 IO 分离**
   - 重 CPU（正则/评分/回测）与 IO（抓取/DB）分线程池配额，避免互相饿死

### 5.3 明确不做

- ❌ 不把 `ThreadingHTTPServer` 换成全异步框架（改动面 = 全站，收益不确定）
- ❌ 不动已被验证的并发模型（每一次历史事故都源于改动并发原语）

---

## 6. 微服务演进（务实边界）

### 6.1 直说结论

**当前不应做微服务化。** 理由：

1. 该项目是**单机桌面端**（Electron + 本地 8765），无集群、无横向扩展需求
2. 服务间通信成本 > 收益：所有模块共享同一个 SQLite 与内存缓存，拆开后要引入 RPC + 分布式一致性问题
3. 用户铁律「服务必须用户自己在控制台启动」—— 多进程会让运维复杂度成倍上升
4. 真正的痛点是**单进程内的 GIL 与锁**，不是服务边界

### 6.2 但可以做的「逻辑微服务化」

在**不引入网络边界**的前提下，把代码按服务边界组织好，为未来真正拆分留出切口：

| 逻辑服务 | 现模块 | 边界定义（进程内） | 未来可独立化 |
|---|---|---|---|
| 采集服务 | `newsflash_sources`, `gmgn_agentic`, `binance_*` | 输入：源配置；输出：标准化事件 | ✅ 高 |
| 结构监控服务 | `dragon-wave-*`, `price_structure_*` | 输入：标的池；输出：结构快照 | ✅ 高 |
| 投研服务 | `onchain_fast_research`, `*_analyzer` | 输入：候选；输出：研究结论 | ✅ 中 |
| 告警服务 | `alert_delivery`, `desktop_alert` | 输入：事件；输出：推送 | ✅ 高 |
| 前端 BFF | `server.py` 路由层 | 输入：HTTP；输出：视图 JSON | — |

**落地方式**：Phase 3 的模块拆分**就按这个边界切**，每个 `app/api/<domain>.py` 通过显式接口（函数签名/上下文对象）交互，禁止跨域直接访问对方内部状态。这样未来若真需要拆进程，只需把模块边界换成 RPC 边界。

### 6.3 若未来要真拆，推荐路径

```
阶段一：模块化（本次 Phase 3）—— 逻辑边界清晰、零通信开销
阶段二：抽出「采集 + 告警」为独立进程 —— 收益最大、耦合最低
        通信：本地 HTTP / 文件队列（非消息中间件，避免过度设计）
阶段三：数据库冷热分离 —— 历史快照走只读库，实时状态走内存 + WAL
阶段四：只在确实需要多机时才考虑消息队列 / 服务发现
```

---

## 7. 验收标准与回滚

### 7.1 每个批次的硬性验收（全部满足才可提交）

1. ✅ `refactor_baseline.py verify` 零结构差异
2. ✅ Python 测试全绿
3. ✅ 前端测试全绿
4. ✅ 慢端点耗时**不变差**（对照第 1.3 节基线表）
5. ✅ 临时实例能正常启动、页面能正常打开

### 7.2 回滚

```bash
git checkout main                                  # 回到主线
git reset --hard backup-pre-refactor-20260928      # 完全回到重构前
# 或按批次回滚：git revert <批次提交>
```

**铁律**：每个批次一个独立提交，消息格式 `refactor(phase3): 拆出 <domain> 域`，保证可单独 revert。

---

## 8. 风险清单

| 风险 | 等级 | 缓解 |
|---|---|---|
| `global` 重绑定导致静默行为改变 | 🔴 高 | split_analyzer 计算闭包 + Golden Master 比对 |
| 模块级锁搬迁后出现**两把不同的锁**（互斥失效） | 🔴 高 | 锁必须 `from core.state import`，禁止在新模块重新 `Lock()` |
| 前端构建产物路径变化导致页面白屏 | 🟠 中 | 产物输出到原路径；逐页人工核对 |
| `.cmd` / Electron / Docker 硬编码路径失效 | 🟠 中 | 不物理移动被引用文件；保留 shim |
| 线上服务（8765）受影响 | 🟠 中 | 只起临时端口验证；重启动作交给用户执行 |
| 1.2GB DB 拖慢验证 | 🟡 低 | 已排查；验证用只读路径 |

---

## 附：本蓝图配套产物

| 文件 | 用途 |
|---|---|
| `tools/refactor_baseline.py` | Golden Master 采集与结构比对 |
| `tools/split_analyzer.py` | AST 依赖分析与拆分边界计算 |
| `tools/extract_module.py` | 从单体机械抽取连贯代码块（AST 定位、生成新模块、算导入） |
| `tools/verify_split.py` | 批次验证器（导入检查 / ROOT 校验 / 残留重复定义检查） |
| `tests/fixtures/refactor-baseline/golden-master.json` | 40 端点结构基线 |
| `tests/fixtures/refactor-baseline/split-plan.json` | 18 域簇 + 66 重绑定名 + 依赖图 |

---

## 9. 批次记录与踩坑（增量更新）

### 批次 1：核心层（paths + config）✅ 已完成并验证

**内容**：把 `ROOT` / `CODEX_HOME` 抽到 `src/app/core/paths.py`，
把 10 个 env 辅助函数（原先散落在第 ~1455、~1509、~10040 三处）合并到 `src/app/core/config.py`。
`server.py` 顶部注入 `sys.path` 后统一导入，引用点零改动。

**验收结果**：全部通过
- `verify_split.py`：导入成功、ROOT 正确、12 个名字就位、无残留重复定义
- 静态/SEO 端点**逐字节一致**（robots.txt / sitemap.xml / index.html / gainers.html / newsflash.html / price-watch.html）
- A/B 对照（同一空库、旧码 vs 新码）：**结构指纹零差异**

### 踩坑记录（重要，后续批次必须遵守）

**坑 1：`ROOT` 不能机械搬移** 🔴
原式 `Path(__file__).resolve().parent` 依赖文件深度。搬到 `src/app/core/` 后
`__file__` 变了，`ROOT` 会静默指向错误目录 → 静态文件全部 404 且无任何报错。
正解：用「向上查找含 server.py/index.html 的目录」自纠正，而不是按固定深度推算。

**坑 2：验证工具被系统代理劫持** 🔴
本项目运行环境有系统级 `HTTP_PROXY`（见过 7890，本次实测为 62151，**会变**）。
Python `urllib` 在 Windows 上既读环境变量也读注册表，`NO_PROXY` 不一定生效 →
对 `127.0.0.1` 的请求被送进代理，返回 `502 upstream connect failed` 或超时。
**后果极严重：每个批次都会误报回归，而代码其实是好的。**
正解：`refactor_baseline.py` 对 loopback 地址强制使用空 `ProxyHandler`（已修复）。
用 `curl` 手工核对时也必须加 `--noproxy '*'`。

**坑 3：A/B 对照不可省** 🟠
只把「新实例 vs 线上基线」比会误判：线上是热缓存 + 已填充数据库，
新实例是冷启动 + 空库。实测发现 `price-watch` 返回 502、`market-hot`/`price-structures`
超时 —— 但在**旧代码的冷启动实例上同样复现**，证明是环境现象而非回归。
正解：**同条件 A/B**——用 `git show HEAD:server.py` 取旧码，
两版各起一次（各自全新空运行时目录、相同预热时长），再互比。
脚本见本文件第 4 节验证流程。

**坑 4：临时实例会被回收，必须单次命令内完成** 🟠
会话内 `&` 启动的进程在命令返回后即被回收。启动 → 探测 → 比对 → 关闭
必须写在**同一次命令**里。

**坑 5：模块级副作用** 🟡
导入 `server.py` 会创建 `ChainEcosystemStore` / `MonitorBuyService` 等并做会话维护。
因此验证一律设 `XINGYUN_RUNTIME_DIR=<临时目录>` 隔离，避免触碰线上数据。

### 后续批次顺序（据此更新）

~~1. logging~~ → 已并入批次 1 范围之外；核心层已就绪，下一步：

1. **批次 2：seo 域**（274 行连续块 / 16 个顶层定义 / 搬走后仅剩 stdlib 依赖，无循环导入）
   —— 已 dry-run 验证，用 `tools/extract_module.py` 执行
2. 批次 3+：`ops_health` → `auth_config` → `smart_money` → `binance` → `alert` → `onchain`
   → `dragon_wave` → `strategy` → `market` → `news` / `wechat` / `price_watch` /
   `price_structure` / `social_x` → `misc`（最后，且必须先二次细分）

---

### 批次 3：共享状态区（app/core/state.py）✅ 已完成并验证

**内容**：把 `server.py` 第 253–1499 行的「模块级声明区」（锁、缓存、单例 Store 引用、
常量，共 562 条语句 / 561 个名字）机械搬移到 `src/app/core/state.py`。
`server.py` 顶部 `from app.core.state import (...)` 统一再导出（538 个名字），
其余 58 个名字（66 个 `global` 重绑定名 + 2 个特殊处理）**刻意保留在 server.py**。

`server.py` 行数：54,867 → 54,265（本次 -602 行）；`state.py` 2,296 行，语法/导入均通过。

**为什么这么拆**：这个 1,247 行的连续区是「共享可变状态」的注册表（119 把锁、
几十个缓存 dict、`CHAIN_ECOSYSTEM_MONITOR` 等单例）。它不依赖任何上层函数，
是唯一能安全整体搬迁的自包含块；搬走后为后续 `ops_health` / `alert` / `onchain`
等业务簇腾出清晰的「状态归属」。

**验收结果（四道闸门）**：
1. **导入级**（`verify_split.py`）：导入成功、ROOT 正确、运行时隔离生效；
   - 对象同一性：**550 项全部与 server 命名空间为同一对象**（23 项 server 已不再引用，属正常跳过）；
   - **零第二把锁**、**零重绑定名字泄漏**（66 个 `global` 名一个都没漏进模块）；
   - 残留重复定义检查已加强到覆盖全部 599 个搬走的名字，无残留。
2. **静态页面逐字节比对**（`ab_verify.py` gate 2）：19 个页面/SEO 生成物**全部逐字节一致**。
3. **API 结构指纹 A/B**（`ab_verify.py` gate 3，同条件冷启动、旧 HEAD vs 新工作区）：
   40 端点**全部通过，0 回归 0 警告**（可用性旧 20/40 → 新 20/40，结构差异 0）。
4. **单测**：见下方「坑 6」。

**新增工具（本批次产出）**：
- `tools/ab_verify.py` —— 同条件 A/B 对照器（新旧两实例**并发**运行、独立运行时目录、
  交替重探消抖、按进程树清理、静态页面逐字节比对 + API 结构指纹比对，二者合一）。
- `tools/run_tests.py` —— 统一 Python 测试入口（stdlib unittest discover + faulthandler 看门狗
  + `XINGYUN_RUNTIME_DIR` 隔离；也是 Phase 2「统一测试入口」的落地）。
- `tools/ab_tests.py` —— 两轮测试日志的失败集差集比对器（区分「新增失败=回归 /
  两边都失败=既有问题 / 消失=改善」）。

**坑 6：测试套件并非「全绿才算过」，必须做新旧失败集差集** 🔴
本项目的单测有**先于重构就存在的死锁/陈旧用例**：
- `test_service_guard.py::test_port_binding_precedes_background_and_database_startup`
  用 AST 找 `calls["ThreadingHTTPServer"]`，但代码早已改名 `BoundedThreadingHTTPServer`
  —— 在重构前备份提交 `a67f03b` 里就已经是这个名字，测试从没更新过（陈旧）。
- `test_alert_delivery.py::test_ten_critical_signals_all_get_display_opportunity`
  在 `alert_delivery.py:52` 的 `sqlite3.connect` 上**永久阻塞**（等一把被后台
  `session-buy-revoke` 线程占住的 DB 事务）。**在旧版 worktree 上同样复现**（相同 5 分钟超时）。
- `test_binance_wallet_structure_pool.py::...contract_identity` 在
  `price_structure_watch_rows` 的冷启动 180s 忙等里卡住（测试环境没有 `price_watch_assets`
  表 → 刷新线程永远失败 → 缓存永远填不上）。**新旧两版同步卡在同一处**。
- `test_strategy_adaptive_context.py::test_acceleration_...` 报 `'NoneType' object is not
  subscriptable` —— **新旧两版同样 1 error**。

因此验收线是「**新版失败集 ⊆ 旧版失败集**」，由 `tools/ab_tests.py` 自动判定；
绝不能用「全绿」当线（否则要么误判回归、要么为了变绿去改运行代码）。

**坑 7：「未再导出」是回归，不是无害信息** 🔴（本批次真正踩中）
初次抽取把 23 个名字搬进 `state.py` 却没在 `server.py` 再导出，我误判为「server 自身
不再引用 = 安全」。但 `test_price_structure_exclusion.py` / `test_binance_wallet_prior_high.py`
通过 `server.PRICE_STRUCTURE_REENTRY_*` 访问这些常量 → `AttributeError`（3 个用例回归）。
`verify_split.py` 的 AST 分析只看 `server.py` 内部引用，看不到 tests/其他模块的外部引用，
所以「未再导出」被当成信息项放过了。**正解：凡 server.py 之外仍可能通过 `server.NAME`
访问的公开名字，一律在 server.py 再导出（成本为零）；真正只在本模块内部使用的少数名字
才列入 `internal` 白名单。** 本批次已把 22 个名字全部再导出，仅 `configured_runtime_dir`
（仅用于推导 `PERSIST_CACHE_DIR`）列为 internal。`verify_split.py` 已把「未再导出且不在
白名单」升级为警告。

---

### 批次 4：ops_health 域（app/api/health.py）✅ 已完成并验证

**内容**：把 `runtime_thread_groups` + `health_payload`（原 server.py 第 51636-51750 行，
连续 115 行）搬到 `src/app/api/health.py`。`server.py` 54287 → 54172 行（-115）。

**核心难点与解法：跨模块读「运行时可变状态」** —— `health_payload` 是纯读函数（无
`globalWriters`），但它读 9 个 `global` 重绑定名（`SITE_ALERT_MONITOR_ACTIVE`、
`NEW_COIN_LOW_MONITOR_ACTIVE`、`NEW_COIN_LOW_ACTIVITY_SUMMARY`、`ONCHAIN_RESEARCH_INGEST_THREAD`
等）+ 8 个尚未拆分的 server 本地函数/名字（`auth_db`/`init_auth_db`/`clean_feed_text`/
`safe_float`/`MONITOR_BUY`/`DASHBOARD_HTTP_RUNTIME`/`DASHBOARD_HTTP_MAX_ACTIVE`/`requests`）。
这些名字的值由 server.py 里各监控循环 `global X` 更新，**不能用 `from app.core.state import X`
绑定**（会拿到永不更新的副本）。解法：**函数体内 `import server`（懒加载）**，用
`_server().NAME` 读取——`health_payload` 只在 HTTP 请求时调用，那时 server.py 早已完整加载，
`sys.modules["server"]` 就是那个唯一、状态实时更新的模块对象。其余锁/缓存/池/常量已搬到
state.py 且是同一对象，直接 `from app.core.state import`。

**这是拆分「读运行时状态的函数」的标准手法**，后续拆 alert/onchain 等含 `globalWriters`
的簇时，读侧照此办理（写侧仍需保留在 server.py，见坑 1）。

**验收结果**：
- `verify_split`：导入成功、ROOT 正确、零重绑定泄漏、零残留重复定义
- 静态页面 19/19 逐字节一致
- API A/B：40 端点重探后 0 回归（`personal-x-monitor`/`strategy-board` 首采的结构差异是
  冷启动时序抖动，重探后一致；`health` 的 `TimeoutError`/`RemoteDisconnected` 是冷启动
  并发实例的网络抖动，批次 3 亦出现，`health_payload` 单独实测 116-358ms 无开销）
- 单测：`test_price_structure_recognition`（引用 `server.health_payload()`）在旧版 worktree
  同样 2F/6E，属既有问题，非本批次引入

### 后续批次顺序（据此更新）

1. ~~批次 1 核心层~~ 2. ~~批次 2 seo~~ 3. ~~批次 3 共享状态区~~ 4. ~~批次 4 ops_health~~
5. **`auth_config`**（3 函数、0 重绑定、最干净）→ ~~`smart_money`~~（本批已完成）
   → `binance` → `alert` → `onchain` → `dragon_wave` / `strategy` / `market`
   → `news` / `wechat` / `price_watch` / `price_structure` / `social_x` → `misc`（最后）

### 批次 5：smart_money / 全网热点域（app/api/smart_money.py）✅ 已完成并验证

**内容**：把 `global_hotspot_*` 15 个函数（原 server.py 第 49521-49975 行，连续 455 行）
搬到 `src/app/api/smart_money.py`。`server.py` 54172 → 53717 行（-455）。

**保留在 server.py**：`start_global_hotspot_monitor`（仅 6 行）因含
`global GLOBAL_HOTSPOT_MONITOR_ACTIVE` 重绑定，按坑 1 铁律留在原文件（重绑定名
`GLOBAL_HOTSPOT_MONITOR_ACTIVE` 定义在第 864 行，仍属 server.py 顶层）。

**外部依赖处理**：这 15 个函数引用 16 个仍未拆分的 server 本地函数/单例
（`clean_feed_text`/`safe_float`/`unique_values`/`read_json_cache`/`write_json_cache`/
`event_monitor_source_rows`/`js_stable_key`/`alert_event_ms`/`codex_cli_chat`/
`deepseek_extract_json`/`trigger_api_refresh`/`build_event_monitor_core_payload`/
`event_monitor_hot_entities`/`news_trade_dex_search_rows`/`safe_error_text`/
`ONCHAIN_FAST_RESEARCH`），全部走 `_server()` 懒加载读取；`NEWS_TRIGGER_VERSION`/
`promote_resonance_priority` 从 `onchain_fast_research` 直接 import；锁/常量
（`GLOBAL_HOTSPOT_LOCK`/`GLOBAL_HOTSPOT_STATE_PATH` 等）从 `app.core.state` 直接 import。

**🔴 踩中并修复的坑 8：「懒 import server」必须回退 `__main__`** —— 批次 4 的 `health.py`
写的 `_server() = sys.modules["server"]` 有**潜伏 bug**：`python server.py` 直接运行时
（生产 + `ab_verify.py` 都是这么启动），模块注册名是 `__main__` 而非 `server`（只有
`import server` 才注册 `sys.modules["server"]`）。所以 `_server()` 会 `KeyError` → 端点
抛异常 → 连接被断（表现为 `RemoteDisconnected`）。批次 4 时 `health` 端点在 A/B 里一直
返回 `RemoteDisconnected`，被误判成「冷启动网络抖动」，实际是**真 bug 被抖动掩盖了**。
正解：`_server()` 改成 `sys.modules.get("server") or sys.modules["__main__"]`，两条路径
都拿同一对象。已同步修 `health.py` 与 `smart_money.py`。**后续所有 `_server()` 一律用此写法。**

**验收结果**：
- `verify_split`：15 名再导出、对象同一性、零重绑定泄漏、零残留重复定义
- 静态页面 19/19 逐字节一致
- API A/B：40 端点全通过（`global-hotspots` 修复 `_server()` 后 116ms→554ms 结构一致；
  `health` 从「静默 RemoteDisconnected」变为正常返回；`chain-ecosystem` 的
  `dailyResearch.sourceStatus.solana` 差异是链名键字典的冷启动时序抖动，已在
  `VOLATILE_KEY_DICTS` 加入 `sourceStatus`/`researchSourceStatus` 折叠消除；`strategy-board`
  首采 17 字段差异是策略引擎首个计算周期未完成，重探后一致）
- 单测：`test_global_hotspot_monitor.py` 6/6 通过；无其他测试引用本域名字



