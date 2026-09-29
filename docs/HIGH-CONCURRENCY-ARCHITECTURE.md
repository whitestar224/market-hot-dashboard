# 高并发架构演进方案（Phase 5）

> 状态：方案文档（尚未实施）
> 日期：2026-09-29
> 范围：server.py 单进程服务的并发模型现状梳理 + 演进路径设计

## 1. 现状：单进程「GIL + 细粒度锁 + 有界线程池」

星云社后端是**单进程**服务，`server.py` 用 `BoundedThreadingHTTPServer` 承载 HTTP，业务层靠
「大量模块级锁 + 若干 ThreadPoolExecutor + 常驻守护线程」串行化共享状态。

### 1.1 规模盘点（2026-09-29 实测）

| 维度 | 数量 | 说明 |
|---|---|---|
| 全局锁（`threading.Lock/RLock/Semaphore/Event/Condition`） | **~120** | 107 个在 `src/app/core/state.py` + 13 个仍在 server.py |
| `ThreadPoolExecutor` | 12 | timeframe/quote/market-source/X-source 等各有界池 |
| 常驻后台线程（`threading.Thread(target=...)`） | 17 | 各监控循环 + desktop-alert worker + ingest flusher |
| 独立进程 | ~2 | Codex CLI（短生命周期）、ChatGPT 外部通道 |

### 1.2 并发模型分层

```
HTTP 请求线程（BoundedThreadingHTTPServer，有界）
   │
   ├─ 内存型快照接口（/api/price-watch 等）
   │     └─ 后台线程预构建 payload + join 硬超时 + stale 降级（页面轮询永不等待应用锁）
   │
   ├─ DB 型接口
   │     ├─ SQLite WAL 并发读（不阻塞）
   │     └─ 写路径：busy_timeout=15s + 应用层写锁超时 30s
   │
   └─ 重构建型接口（market-hot 等 12 源）
         └─ 并发抓取（MARKET_SOURCE_POOL）+ allow_sync_rebuild=False + TTL 缓存
```

### 1.3 已落地的关键防护（历史复盘沉淀）

- **页面轮询永不等待应用锁**：快照层（`price_watch_snapshot_payload` 等）后台构建，请求线程只读快照。
- **重构建永不阻塞请求线程**：`cached_api_payload(allow_sync_rebuild=False)`，超龄只返回 stale + 后台刷。
- **写锁全部加超时**：`busy_timeout` / `lock.acquire(timeout=30)` / `_store_write_lock` 超时快速失败，杜绝僵尸锁。
- **快讯 ingest 全部后台化 + 单飞**：`spawn_background_news_ingest`，缓存写入永不同步等 chain-store 锁。
- **DB 批量缓冲写**：`buffer_ingest()` + 5s flusher，减少持锁次数。

## 2. 根本瓶颈分析

### 2.1 GIL 是「饿死」放大器，不是唯一瓶颈

单进程模型下，一个慢的后台线程（如 300s 级的大构建、1.24GB `chain_ecosystem.db` 的写）会
**周期性饿死全进程**——这正是「首页卡死复盘」的根因。已通过四刀（缓存降级 + TTL 调整 +
后台化 + 轮询间隔对齐）缓解，但**结构性上限仍在**：所有 CPU 密集计算（排序、序列化、正则）
都受 GIL 串行约束，横向扩展能力为零。

### 2.2 三大结构性瓶颈

1. **单进程单 GIL**：无论多少核，Python 字节码执行都是串行的。监控循环的 CPU 计算（价格
   结构计算、榜单排序、热点正则匹配）与请求处理争抢 GIL。
2. **SQLite 单写者**：`chain_ecosystem.db` 1.24GB，WAL 模式下多读者单写者，写放大 + 大表
   VACUUM 会长时间占写锁。
3. **进程内锁 + 状态耦合**：120 个锁、17 个后台线程、12 个线程池全部共享一个进程内状态，
   任何一处 `with lock:` 里的慢操作都可能连锁阻塞（锁顺序倒挂、僵尸锁都是此模型的固有风险）。

## 3. 演进路径（分三档，逐档可独立评估）

### 档 A：单进程内加固（低风险，收益中，推荐优先）

**目标**：在不改变部署形态的前提下，消除剩余的结构性卡点。

1. **锁分级与顺序约定**：把 120 个锁按「全局大锁（AUTH_DB_LOCK）/ 组件锁 / 本地锁」分级，
   用 `verify_split` 式的静态检查禁止「持组件锁再抢大锁」的倒挂（已有先例：`NEW_COIN_LOW_LOCK`
   倒挂曾拖死页面数分钟）。
2. **慢构建全部下沉到独立进程**：把 300s 级的大构建（event-monitor-core、market-hot）从
   线程池改为 `subprocess` 短进程，隔离 GIL 争抢（先例：Codex CLI 已是独立进程）。
3. **SQLite 分片/归档**：`chain_ecosystem.db` 按时间分片，热数据留在主库，冷数据归档到只读
   侧库，避免单库膨胀拖慢写路径。
4. **GIL 释放审计**：对 CPU 密集段（大 JSON 序列化、正则批量匹配）评估 `multiprocessing` 或
   移到 C 扩展/`orjson`。

### 档 B：进程级横向拆分（中风险，收益高，需业务切分）

**目标**：把 CPU 密集的监控计算从请求服务剥离。

1. **监控 worker 独立进程**：把 dragon-wave 结构计算、price-structure 预计算、榜单排序等
   CPU 密集模块拆成独立 worker 进程，通过本地队列/管道与主服务通信，主服务只做「读快照 +
   写持久化」。
2. **读写分离**：主进程只承接 HTTP + 内存快照读；写路径（ingest、评分回写）由专用 writer
   进程串行处理，天然消除「读被写饿死」。

### 档 C：微服务化（高风险，收益取决于部署规模）

**目标**：按业务域拆成独立服务（binance / onchain / news / wechat / alert）。

- 仅在「单机已无法满足延迟/吞吐」时启动。每拆一个服务都要复制运行时代码、解决跨服务状态
  一致性与鉴权，迁移风险与当前单机本地部署定位（桌面端 + 单机监控）不匹配，**不建议在
  桌面端阶段推进**。

## 4. 建议的落地顺序

1. **档 A.2**（慢构建下沉进程）——收益最直接，直接消除「饿死」根因。
2. **档 A.3**（DB 分片）——解决 1.24GB 单库的性能天花板。
3. **档 A.1**（锁分级静态检查）——把历史踩坑固化成工具。
4. 档 B 仅在档 A 后仍有吞吐瓶颈时评估。

## 5. 与重构的关系

Phase 3 已完成的拆分（`src/app/core/state.py` 集中 107 个锁、`src/app/api/*` 抽出纯函数域）
**为档 A/B 铺了路**：锁已集中、可静态审计；纯函数域（smart_money/dragon_wave/login_rate）
已与运行时状态解耦，天然是「下沉进程」的候选模块。但拆分本身不改变并发模型——真正的
横向扩展需要档 A.2/A.3 的进程/存储层改造。
