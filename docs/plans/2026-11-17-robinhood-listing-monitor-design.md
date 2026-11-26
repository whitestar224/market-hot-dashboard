# Robinhood 上币早期信号监控（只读）

> 状态：已实现并通过验收（2026-11-17）
> 代码：`robinhood_listing_monitor.py`（独立脚本，未接入 server）
> 测试：`tests/test_robinhood_listing.py`（83 例）· 夹具 `tests/fixtures/`

## 1. 为什么这条路能提前

Robinhood Chain（chainId 4663）的托管方是 **BitGo**、做市商是 **Wintermute**。一个非股票
代币真正上线之前，通常按这个顺序留下公开痕迹：

1. BitGo 仓库 `BitGo/BitGoJS` 的 PR 里新增 `hoodeth:<symbol>` 代币定义（最早）；
2. 合并进 master → `@bitgo/statics` 发版 / npm 发布；
3. Wintermute 权限注册合约批准该币的交易池；
4. Wintermute 金库 `approveTokens` 把币加进 `getAllApprovedTokens()`；
5. Robinhood 上线。

实测提前量（相对上线时间）：

| 信号 | cashcat（08-06 13:31 UTC） | PONS（约 09-02 13:29 UTC） |
|---|---|---|
| BitGo PR | 244h（#9357，07-27 09:28） | 53h（#9603，08-31 08:10） |
| 合并 master | 222h | 53h |
| npm 发版 | 209h（59.1.0） | 21h（59.11.0） |
| 池子批准 | 67.7h（08-03 17:47:19） | 48.1h（08-31 13:20:33） |
| 金库加入 | 27.0h（08-05 10:32:17） | 26.7h（09-01 10:45:33） |

## 2. 分级

| 情况 | 严重度 |
|---|---|
| 只有 BitGo（PR / master / tag / npm） | `high`，需人工确认 |
| Wintermute 批准池子 / 金库加入 / 白名单新增 | `high` |
| **同一合约两类都命中** | `critical`（最可信） |
| 名称带 `• Robinhood` 的股票代币 | `low`，且**不参与** critical 升级 |
| 新的允许转出地址 / 新合约接入 / 多签新目标 | `medium` |
| 以太坊上 Wintermute → Robinhood 转账 | `medium`（低置信） |

「BitGo 单独出现噪音大」不是猜的：stonkbroker / index / up / ram / tendies / goose / ai /
pipedog / orbio 9 个币 BitGo 加过，Robinhood 一个都没上。

## 3. 关键实现决定

- **基线按源分开记**（`state["baseline"]`）。只有一个全局标记时，某源第一轮挂了会被
  标成「已建基线」，它拿着空 `known_tokens` 把全部存量代币当新币报一遍——几百条误报。
  老状态文件（只有 `baseline_done`）按「分区里有没有游标」反推，迁移不会重报。
- **失败 ≠ 没有新信号**。单源失败只记 stderr、不推进状态；`run()` 里 per-source try。
- **`--backtest` 是报告模式**，唯一往 stdout 打表的命令；其余情况 stdout 恒定 JSON 数组。
- **`symbol()` / `name()` 必须清洗**。那是别人合约上的函数，可能返回空、一串 `\x00`
  或控制字符；清洗后为空就展示短地址 `0x020bfc…18b4`，脏字符串绝不进 token 字段。
- **PR 抓取并发 6 路**。每个 PR 要读 2 个文件，串行 40 个 ≈ 80 秒；并发后 24~29 秒。
- **只有真读过的 PR 才记 head**。基线一轮最多处理 40 个，剩下的留到后面几轮；否则
  「没看过」被标成「看过了」，这些 PR 以后永远不被检查。
- **`getAllApprovedTokens` 失败只跳过这条腿**，不能 `raise` 掉整个 chain 源——那样本轮
  事件里已经解出来的代币全丢，下一轮重扫又丢一遍。
- **`ROBINHOOD_RPC_URLS` 是覆盖不是追加**，否则故障演练/切换自建节点永远轮不到它。

## 4. 验收实测（2026-11-17）

| 项 | 结果 |
|---|---|
| 首跑建基线 | `[]`，116,199,996 块扫到 2,025 条日志，github 296 个 PR 待处理 |
| 二跑 | `[]`，chain 8.6s（增量 5k 块） |
| 三/四跑 | `[]`，github 收敛 296→256→216→176，并发后 29s |
| `--backtest` | 数值与上表一致（cashcat 67.7h/27.0h、PONS 48.1h/26.7h） |
| 单源失败 | RPC 指向死端口：chain 失败、github 照常 23.9s 完成、`chain.last_block` 不动、退出码 0 |

## 5. 已知限制

- **方法在贬值**：原作者说 PONS 之后这套路数已被很多人学会。PONS 的「上线」更像
  Robinhood 设的抓内鬼局——它至今不在支持列表里。
- **GitHub 腿没用 git 协议部分克隆**：改成 `git ls-remote` + `raw.githubusercontent`，
  功能等价、零 key、不写磁盘缓存；代价是 PR 只改别的文件时也要把代币文件拉下来看一眼。
- **四个「疑似交易合约」没进 getLogs 地址过滤**：真身若是高流量池子能吐回几十万条日志，
  而当前没有任何话题用到它们。
- **信号源 4（底部卖压枯竭）未实现**：只有 PONS 一个成功案例（8/16、8/18、8/22，此前
  18 天 0 误报），买卖比 1.6 很可能过拟合（cashcat 上从未达到）。先在别的币上测误报率。
- **待核实**：Wintermute 在 Arkham/Blockscout 的直接标签；注册合约里 6 个未标注的允许
  转出地址；Robinhood 在链上的热钱包。Wintermute 换合约/多签时靠
  `wintermute_safe_new_target` / `wintermute_new_contract_subscribed` 发现。

## 6. 尚未接入

模块目前**独立运行**，没有接 `server.py` 的 worker，也没有弹窗。若要接入，最小改动是：

1. `--worker-market` 里加一个低频任务（5 分钟）跑一次 `run()`；
2. 把 `severity in {critical, high}` 的信号转成 `desktop_alert`，`critical` 报红；
3. `token` 字段可直接做榜单/池子符号匹配（已保证清洗过）。
