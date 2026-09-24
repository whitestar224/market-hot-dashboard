# 回测数据备份

本目录存放链上投研系统的回测依赖数据备份，用于在本地数据库清理后仍可恢复历史回测。

## 文件说明

`backtest-data.tar.gz`（约 87MB，解压后 ~820MB）包含：

| 文件 | 内容 | 行数 | 用途 |
|---|---|---|---|
| `candidates.jsonl` | `onchain_research_candidates` 全量导出 | 187K | 回测候选标的 |
| `snapshots.jsonl` | `onchain_research_snapshots` 全量导出 | 542K | 无偏回测（look-ahead-free）的分时快照 |
| `snapshots_sample.jsonl` | `onchain_research_snapshots` 最近 10 万条 | 100K | 快速回测验证 |

## 解压与恢复

```bash
tar -xzf backtest-data.tar.gz
```

解压后得到三个 `.jsonl` 文件，每行一个 JSON 对象，字段与数据库表列一一对应：

- `onchain_research_candidates` 列：`id, network, contract_address, symbol, name, ...`
- `onchain_research_snapshots` 列：`id, candidate_id, observed_at, observed_bucket, decision, candidate_type, score_version, meme_score, project_score, selected_score, confidence, metrics_json, reasons_json, risks_json, created_at`

## 回测脚本依赖

以下脚本读取这两张表的历史数据做无偏回测（严格用每个币「首次被发现」时刻的快照，避免前视偏差）：

- `backtest_unbiased.py`
- `backtest_unbiased2.py`
- `backtest_unbiased_v3.py`
- `build_research_report.py`

## 备份时间

2026-09-25（DB VACUUM 前导出）
