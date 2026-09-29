# 微服务化设计蓝图（进程级拆分）

> 状态：**阶段 2/3 已落地**（market 12 源已下沉为独立 market-worker 子进程）；阶段 4（ingest）待做
> 日期：2026-09-29
> 约束：进程级拆分 + 保持桌面端单机部署（不引入 Docker/K8s/云服务/消息中间件）
> 铁律：**不改变任何页面、任何 API 契约、任何用户可见行为**

### 已落地（2026-09-29）

- **market-worker 子进程**（阶段 2/3 合流）：`server.py --worker-market` 复用主进程
  完全相同的 `market_payload()`，把 12 源并发抓取 + `smartPriority` 结果写到
  `api_cache_path("market-hot")`（各源仍各自写独立 `source-cache/market-hot_*.json`）。
  - 主进程 `market_hot_response_payload` 通过 `market_source_worker_enabled()`
    （读 `XINGYUN_MARKET_WORKER`）决定走「读快照」还是「原单进程路径」。
  - **默认关闭**：无 env 时 `market_source_worker_enabled()` 返回 False，行为与重构前
    逐字节一致；`启动后台服务.cmd` 已 `set XINGYUN_MARKET_WORKER=1` 开启。
  - `service_guard.py` 新增 `supervise_market_worker()`：随主进程常驻拉起 worker、
    意外退出立即重启、日志抽到 `market-worker.log`。
- **为什么第一刀选 market 而非 event-monitor-core**：market 12 源各自写独立缓存文件、
  `market_priority_*` 状态已落盘（`rank_monitor_state.json`），拆分最干净、无损；
  event-monitor-core 依赖其他监控循环维护的进程内内存行，需先重设计数据流（风险更高）。

### 阶段 4（ingest 下沉）—— 调查结论：不可无损拆，改做 DB 瘦身（2026-09-29）

**为什么不硬拆 ingest**：`ingest` 是读-改-写原子事务，评分器
（`evaluate_onchain_candidate`）、enricher 回调（`global_hotspot_enrich_candidate`，
读主进程内存热点缓存）、chat 交叉验证（读 `onchain_chat_evidence`）、DeepSeek 分析器、
弹窗 sink 全在同一把 `_store_write_lock` 里，且都依赖主进程内存态。拆成子进程要么复制
半个 server.py，要么把「评分」与「落库」解耦——但评分本身就要读库（old jobs/candidates/
chat evidence），无法只交「写」。加上日志显示 `buffer_ingest`（2026-09-25 已落地）已让
写锁不再报超时，硬拆收益不确定、回归风险高。

**真正的瓶颈是 DB 膨胀，不是锁**：`chain_ecosystem.db` 617MB，其中
`onchain_fast_jobs.candidate_json` 占 228MB（`screened` 状态 6.3 万行占 159MB）。
根因是 `screened`（已判定不值得投研）候选币**没有运行时淘汰机制**，市场刷新循环每次
UPSERT 都重写它们完整的 candidate_json，导致 14 天前就该淘汰的 5.7 万行僵尸数据持续累积。

**落地（治标，确定收益 ~200MB）**：`prune_chain_ecosystem.py`（14 天淘汰 + 分批删除 +
VACUUM）已存在；本次完善了相对路径 + 服务运行时 `immutable` 安全 dry-run，并新增
`清理数据库.cmd` 一键脚本（停服 → 备份 → prune --backup → 重启）。需停服窗口执行。

**治本（待做，需谨慎）**：在 `ingest` 加短路——`status='screened'` 且 `first_seen_at`
超 N 天且无新证据的老候选跳过重写 candidate_json（只轻量更新 updated_at/quote_due_at），
从源头阻止僵尸数据累积。属核心写路径，需四闸门验收 + 充分回测。

## 1. 目标与边界

把 `server.py` 单进程里的「CPU 密集 / 易阻塞」模块拆成**独立子进程 worker**，主进程
退化为「HTTP 服务 + 快照读 + 编排」，通过**本地文件系统（磁盘快照）+ 本地 IPC** 通信。

**不做的**（明确排除，避免过度设计）：
- ❌ 不按业务域拆成多端口独立服务（binance/onchain/news 各自 HTTP 端口）——会复制
  运行时代码、引入跨服务鉴权与状态一致问题，与单机桌面端定位不符。
- ❌ 不引入 Redis/RabbitMQ/K8s——单机无此基础设施，增加部署负担。
- ❌ 不把 SQLite 换成网络数据库——WAL 已支持多进程，换库是大迁移。

## 2. 现状约束（拆分的前提，必须尊重）

| 事实 | 影响 |
|---|---|
| 1328 个顶层函数/类、106 个端点、~120 个锁、43+12 个模块级内存缓存 | 状态极度耦合，不能整体搬移 |
| 4 个 SQLite 库（chain_ecosystem 639MB 是瓶颈） | SQLite WAL 天然支持多进程读 + 单写者 |
| 5 个模块 `import server`（循环依赖面） | 拆分边界必须避开这 5 个模块 |
| `service_guard.py` 单进程守护（Popen server.py） | 需扩展为「主进程 + worker 进程」的守护 |
| Electron/PyInstaller 打包 | 新增 worker 进程必须进打包 filter |
| 17 个后台监控器（start_*_monitor） | 是「下沉独立进程」的候选池 |

## 3. 服务边界设计

### 3.1 目标架构

```
┌─────────────────────────────────────────────────────────┐
│ service_guard（守护，扩展为多进程监督）                    │
├─────────────────────────────────────────────────────────┤
│ 主进程（server.py，端口 8765）                            │
│   ├─ HTTP（BoundedThreadingHTTPServer）                  │
│   ├─ 内存快照读（/api/price-watch 等，永不等待应用锁）     │
│   ├─ 磁盘快照读（event-monitor-core 等，按 mtime 失效）    │
│   └─ 编排：启动/监控 worker 子进程                        │
├─────────────────────────────────────────────────────────┤
│ Worker 进程池（独立 Python 进程，无 HTTP）                 │
│   ├─ build-worker：event-monitor-core 重构建              │
│   ├─ market-worker：market_payload 12 源并发抓取          │
│   ├─ ingest-worker：chain_ecosystem 写路径（缓冲+flusher） │
│   └─ （后续按需）price-structure / dragon-wave 计算        │
├─────────────────────────────────────────────────────────┤
│ 共享存储（本地文件系统）                                   │
│   ├─ .runtime-cache/*.json（磁盘快照，跨进程共享）          │
│   ├─ .runtime-cache/chain_ecosystem.db（SQLite WAL）       │
│   └─ .runtime-cache/service/status.json（进程状态）         │
└─────────────────────────────────────────────────────────┘
```

### 3.2 通信协议（只用本地文件系统 + 轻量 IPC，零新依赖）

1. **结果传递 → 磁盘快照**：worker 构建结果写 `api_cache_path(key)`，主进程读文件、
   按 `st_mtime_ns + st_size` 签名失效（已有 `read_event_monitor_core_snapshot` 范式）。
2. **任务触发 → 触发文件/信号**：主进程写 `*.request` 标记文件，worker 轮询发现后执行
   （已有 `precomputed` 的 `*.requests` 范式）。
3. **进程状态 → status.json**：复用 `.runtime-cache/service/status.json`，每个 worker 一段。
4. **日志 → 独立日志文件**：worker 的 stdout/stderr 由 service_guard 抽到各自日志。

## 4. 数据归属

| 数据 | 归属进程 | 跨进程共享方式 |
|---|---|---|
| 磁盘快照（event-monitor-core / market-hot 等） | build/market worker 写，主进程读 | 文件 + mtime 签名 |
| chain_ecosystem.db（写） | ingest-worker 独占写 | SQLite WAL 单写者 |
| chain_ecosystem.db（读） | 主进程任意读 | WAL 多读者 |
| auth 库（xingyunshe_auth.db） | 主进程独占 | 不拆（鉴权属请求路径，非瓶颈） |
| 进程内内存缓存（CACHE 等） | 各自进程内 | 不共享，各 worker 独立 |

## 5. 迁移路线（分阶段，每阶段四闸门验收）

### 阶段 1：基建（零行为变化）
1. 抽取 `worker_runner.py`：通用「子进程 worker」骨架（启动/心跳/优雅退出/日志）。
2. 扩展 `service_guard.py`：支持监督「1 主进程 + N worker」，状态写入 status.json。
3. 打包配置补 worker 入口（`package.json` filter + PyInstaller `--paths`）。
4. 验收：主进程 + worker 同机启动，页面/API 逐字节不变。

### 阶段 2：第一刀 —— event-monitor-core 构建下沉（收益最直接）
1. 把 `build_event_monitor_core_payload` 从主进程线程改为 build-worker 执行，
   结果写磁盘快照；主进程 `cached_event_monitor_core_payload` 读快照（已有范式）。
2. 验收：`/api/event-monitor` 结构指纹 A/B 一致；冷启动首刷延迟不劣化。

### 阶段 3：market_payload 12 源下沉
1. market-worker 执行 12 源并发抓取，写各源磁盘快照。
2. 验收：`/api/market-hot` 等结构一致，源顺序不变。

### 阶段 4：chain_ecosystem ingest 下沉（最难，最后做）
1. ingest-worker 独占写 `chain_ecosystem.db`，主进程评分后把「待写行」通过快照/队列
   交给 worker 落库。
2. **难点**：`ingest` 是读-改-写原子事务 + 评分器/enricher 回调耦合，需先把「评分」
   与「落库」解耦（评分留在主进程或评分 worker，落库交 ingest-worker）。
3. 验收：写锁不再饿死读；`chain_ecosystem.db` 写延迟不劣化。

## 6. 风险清单

| 风险 | 缓解 |
|---|---|
| Windows 文件锁死锁（历史僵尸锁根因） | worker 写锁超时 + busy_timeout，沿用现有防护 |
| 进程生命周期（service_guard 需监督多进程） | 阶段 1 先做基建，逐进程灰度 |
| 快照写一半被读（读到脏数据） | 原子写（tmp + rename），沿用 `write_json_cache` |
| worker 崩溃导致快照 stale | 主进程按 mtime 超龄判断 stale + 降级（已有 `allow_sync_rebuild=False`） |
| 打包漏 worker | 阶段 1 就补 package.json/PyInstaller |
| 沙箱/Agent 会话进程被回收 | 常驻服务仍由用户控制台 `启动后台服务.cmd` 启动（铁律不变） |

## 7. 与已完成工作的关系

- Phase 3 已把 107 个锁集中到 `state.py`、纯函数域（smart_money/dragon_wave/login_rate）
  解耦——**worker 进程可直接 import 这些纯模块**，天然是「下沉候选」。
- `read_event_monitor_core_snapshot` / `api_cache_path` 的「磁盘快照 + mtime 失效」范式
  是阶段 2/3 的现成通信基础。
- 本蓝图是 `docs/HIGH-CONCURRENCY-ARCHITECTURE.md` 档 A.2 + 档 B 的具体化。

## 8. 决策点（实施前需确认）

1. 阶段 2 的 build-worker 是否要在冷启动就拉起，还是惰性拉起（首次请求时）？
2. worker 崩溃后的重试策略：立即重启（service_guard 兜底）还是仅记录 + 等下一轮触发？
3. 是否接受「worker 进程各自持有独立内存缓存」（内存会略有上升，换取 GIL 隔离）？
