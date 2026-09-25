# 金狗潜力判断器（Laya 微调特训）

把本地 `convaiinnovations/laya`（System-1 决策模型，基于 mmBERT-base encoder + 决策头）
微调为**金狗潜力判断器**：判断一个新发行 token 是否具备「大金狗」（暴涨潜力的 MEME 币）
结构特征。

## 一、背景与定位

- **laya** 是一个非自回归的 System-1 决策模型，架构为
  `encoder(mmBERT-base) → type_emb → head(TransformerEncoder) → scorer(选项 logits) + act_head(动作)`。
  本地已有 `laya_decision.py` / `laya_decision_worker.py` 通过 `RAPID_PRIORITY_QUESTIONS`
  的 `research_priority`（deep-research / watch / reject）三分类调用它。
- **本任务**：在「最新投研体系 V4.9」（`onchain_research_framework.py`，
  `FRAMEWORK_VERSION = xmind-v4.9-hotspot-derived-ca-three-ledgers-1`）的基础上，
  新增一个 `golden_dog_potential`（yes/no）判断头，并微调 `scorer` 权重，
  使其学会识别「金狗潜力」而非泛泛的研究优先级。

## 二、标签体系（多信号融合）

历史大金狗（DOGE / SHIB / PEPE / BONK / WIF）的共性特征：
1. 极低市值起步（多在 <$500 万微型市值区间）
2. 强 MEME 文化符号 + 极简叙事
3. 公平发射 / 锁池弃权（本地映射为可交易流动性 + 可验证身份）
4. 社区广度（持有者 / 买盘）
5. CEX 上架 + KOL 催化（本地映射为高选择分 / 高机会分）

本地可得的量化信号（来自 `chain_ecosystem.db` 的 V4.9 标注样本）：
- `memeScore`（13.5–90）：MEME 传播力
- `selectedScore`（19.5–100）：综合选择分
- `leaderScore`（0–25）：龙头潜力分
- `opportunityScore`（0–42）：机会分
- `verdict`：strong / watch / weak / avoid

**金狗潜力分融合公式**：

```
goldenDogScore = 0.40*memeScore + 0.20*(leaderScore/25*100)
               + 0.20*(opportunityScore/42*100) + 0.20*verdictScore
               + mc_bonus（市值 <$5M 加 10，<$20M 加 5）
```

二分类标签：`goldenDogScore >= 60 → yes（有金狗潜力）`，否则 `no`。
同时保留连续 `goldenDogScore` 作为回归校准软目标。

## 三、文件说明

| 文件 | 作用 |
|---|---|
| `historical_golden_dogs.py` | 历史大金狗正样本档案（18 个真实暴涨币的早期结构化快照） |
| `build_dataset.py` | 从 db 提取 V4.9 样本 + 注入历史金狗正样本，输出 JSONL |
| `train_golden_dog.py` | 两阶段微调：①预计算 marker hidden 缓存 ②轻量头训练 |
| `infer_golden_dog.py` | 加载微调后 checkpoint 做金狗潜力推理 |

## 三·一、历史大金狗正样本（关键补充）

本地 V4.9 标注里 `potentialTier` 的 leader/golden-dog 正样本为 **0**（战壕数据全是"初筛新币"），
仅靠 `goldenDogScore` 阈值构造的伪正样本信号太弱。因此额外联网检索了
2023–2025 年 Solana/Base 链上真实暴涨百倍/千倍的 MEME 币（BONK / WIF / BOME / SLERF /
POPCAT / GOAT / MOODENG / PNUT / FARTCOIN / TRUMP / BRETT / TOSHI / DEGEN / MIGGLES /
SPX6900 / PEPE / SHIB / DOGE），逆向构造成「早期爆发前」的 `rapid_candidate_state`
结构化快照，作为**已知结局**的强正样本注入训练集。

这些金狗与本地负样本的核心区分度（可量化的早期特征）：
1. **极低发射市值**（Pump.fun 系毕业约 $6.9 万，老牌 IP 开盘 <$0.002，建池 <$500 万）
2. **强叙事/外部事件/AI/IP 绑定**（动物 IP、AI Agent、政治事件、艺术家 IP）
3. **公平发射 + LP 锁池/弃权**（信任基线）
4. **社区买盘广度**（高 transactions / buys 领先）
5. **快速 CEX 上币催化**（BOME 3 天、PNUT 11 天上 Binance）

> 注意幸存者偏差：真实金狗占比 <1%（Pump.fun 上 98.6% 拉高出货），
> 这批样本仅作为稀缺正样本的信号注入，不代表分布估计。

## 四、微调策略（纯 CPU 可行）

- **冻结** `encoder`（3.07 亿参数）+ `head`，**只训练** `scorer` + `act_head` + `type_emb`
  （约 79 万可训练参数）。
- 两阶段：先冻结 encoder 预计算所有样本的 marker 位置 hidden（约 1.5s/条），
  再在缓存 hidden 上做轻量头训练（毫秒级/步），避免 CPU 上反复过 mmBERT。
- 损失 = `w_ce * CrossEntropy(yes/no)` + `w_rl * (-proper_reward(soft target))`，
  其中 `proper_reward` 是 laya 自带的严格 proper scoring rule（log + spherical + RPS），
  用于让概率输出对 `goldenDogScore` 校准。

## 五、运行方式

```bash
# 1. 构造数据集
python build_dataset.py

# 2. 微调
HF_HUB_OFFLINE=1 python train_golden_dog.py --epochs 12 --batch-size 32

# 3. 推理
python infer_golden_dog.py \
  --checkpoint .runtime-cache/laya-golden-dog-checkpoint \
  --state '{"symbol":"TAKO","meme_score":62,"selected_score":62,"metrics":{"marketCapUsd":454273}}'
```

## 六、产物

- `.runtime-cache/laya-golden-dog-checkpoint/model.safetensors` — 微调后全量权重（encoder 未变）
- `.runtime-cache/laya-golden-dog-checkpoint/rl_agent_config.json` — 带训练元信息的配置
- `.runtime-cache/training/golden-dog-labels.jsonl` — 训练/验证/测试数据集（含 18 历史金狗正样本）

## 七、重要诊断结论（务必先读）

注入 18 个历史金狗正样本后，微调链路完全跑通（数据→训练→checkpoint→推理），
但端到端微调 laya 做「金狗二分类」在现有数据下**不可行**，关键证据：

| 模型 | 历史金狗正样本(18) | 测试集负样本(40) |
|---|---|---|
| base（未微调） | 判对 **18/18**（yes 均值 0.937） | **全误报**（yes 均值 0.873） |
| finetuned（均衡采样） | 判对（全 yes） | 全误报（全 yes） |
| finetuned（类别加权） | 漏报（全 no） | 判对（全 no） |

**根因**：历史金狗正样本（极低市值 + 强叙事 + 公平发射）与本地负样本
（低市值 + 高热度 + 强买盘的新币）在 laya 的 state 特征空间**数值高度重叠**。
这是真实的幸存者偏差——金狗占比 <1%，早期无法可靠区分「哪个会成为金狗、哪个归零」。
两种微调策略各走向一个极端（全 yes 或全 no），不存在中间解。

**可行替代**（按性价比）：
1. 退化为 `leaderScore` 回归/排序（输出连续分而非二分类），避开正负边界难题；
2. 直接用 `goldenDogScore` 融合公式做确定性规则打分器（已在 `build_dataset.py` 实现）；
3. 治本：引入「已知结局 + 早期快照」成对样本（正=真金狗早期，负=同期归零币早期）。

> 说明：本次按用户要求「仅保存权重和训练脚本，暂不接入」，
> 未替换现有 `laya_decision` 生产链路的 `research_priority` 三分类。
