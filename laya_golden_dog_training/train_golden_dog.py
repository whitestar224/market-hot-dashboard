"""微调 laya 决策头，训练成「金狗潜力判断器」（两阶段：encoder 预计算缓存 + 轻量头训练）。

纯 CPU 优化：mmBERT encoder（3.07 亿参数，22 层）冻结后单条前向约 1.5s，
直接训练会极慢。因此：
  阶段1：冻结 encoder+head，预计算所有训练样本的 marker 位置 hidden 并缓存到磁盘；
  阶段2：训练循环只在缓存的 hidden 上跑 scorer + act_head（毫秒级/步）。

训练目标：CE 硬标签（yes/no）+ proper_reward 软目标（goldenDogScore 校准）。

最终导出 model.safetensors（全量权重，encoder 未变）+ rl_agent_config.json。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from laya import load
from laya.common import QTYPES, build_sequence, collate_items, proper_reward

from build_dataset import GOLDEN_DOG_QUESTIONS

ROOT = Path(__file__).resolve().parents[1]
MODEL_REPO = os.getenv("LAYA_MODEL_REPO", "convaiinnovations/laya")
MODEL_SUBFOLDER = os.getenv("LAYA_MODEL_SUBFOLDER", "multilingual")


def _num(value, default=0.0):
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)


def load_examples(path: Path, split: str):
    examples = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [e for e in examples if e.get("split") == split]


@torch.no_grad()
def precompute_marker_hidden(agent, examples, max_len, head_max_len):
    """阶段1：冻结 encoder，预计算每个样本 marker 位置上的 hidden、pooled、feats。

    返回 list[dict]：m[K,d] / marker_mask[K] / pooled[d] / feats[4] / label / target。
    其中 feats 依赖当前 scorer 权重（act_head 输入），训练时 scorer 会更新，
    但 act_head 不参与 yes/no 判定（由 scorer logits 决定），故差异可忽略。
    """
    model = agent.model
    model.eval()
    question = agent._to_internal(GOLDEN_DOG_QUESTIONS["golden_dog_potential"])
    keys = list(question["crit"].keys())
    out = []
    for ex in examples:
        state = ex.get("state") or {}
        seq, markers = build_sequence(agent.tok, state, question, max_len, head_max_len)
        items = [{"ids": seq, "markers": markers, "qtype": QTYPES[question["t"]]}]
        b = collate_items([items], agent.tok.pad_token_id)
        h = model.encoder(input_ids=b["input_ids"], attention_mask=b["attention_mask"]).last_hidden_state
        h = h + model.type_emb(b["qtype"])[:, None, :]
        if model.head is not None:
            pad = ~b["attention_mask"].bool()
            for layer in model.head.layers:
                h = layer(h, src_key_padding_mask=pad)
        idx = b["marker_pos"].clamp(min=0)[:, :, None].expand(-1, -1, h.size(-1))
        m = torch.gather(h, 1, idx)  # [1, K, d]
        pooled = h[:, 0]             # [1, d]
        logits = model.scorer(m).squeeze(-1).float()
        logits = logits.masked_fill(~b["marker_mask"], -1e4)
        p = torch.softmax(logits.detach(), -1)
        k = b["marker_mask"].sum(-1).clamp(min=2).float()
        ent = -(p * torch.log(p.clamp_min(1e-9))).sum(-1) / torch.log(k)
        if p.size(-1) >= 2:
            top2 = p.topk(2, -1).values
        else:
            top1 = p.topk(1, -1).values
            top2 = torch.cat([top1, torch.zeros_like(top1)], -1)
        feats = torch.stack([top2[:, 0], top2[:, 0] - top2[:, 1], ent, k / 255.0], -1)

        choice = ex["target"]["golden_dog_potential"]
        label_idx = keys.index(choice) if choice in keys else 0
        gd = max(0.0, min(1.0, _num(ex["target"].get("goldenDogScore")) / 100.0))
        out.append({
            "m": m[0].clone(),                 # [K, d]
            "marker_mask": b["marker_mask"][0].clone(),
            "pooled": pooled[0].clone(),       # [d]
            "feats": feats[0].clone(),         # [4]
            "label": label_idx,
            "target": np.array([1.0 - gd, gd], dtype=np.float32),
        })
    return out


@torch.no_grad()
def evaluate(agent, examples, max_len, head_max_len):
    """完整模型前向，评估 yes/no 准确率（区分正负样本）。"""
    if not examples:
        return 0.0
    model = agent.model
    model.eval()
    question = agent._to_internal(GOLDEN_DOG_QUESTIONS["golden_dog_potential"])
    keys = list(question["crit"].keys())
    tp = fn = tn = fp = 0
    for ex in examples:
        state = ex.get("state") or {}
        seq, markers = build_sequence(agent.tok, state, question, max_len, head_max_len)
        items = [{"ids": seq, "markers": markers, "qtype": QTYPES[question["t"]]}]
        b = collate_items([items], agent.tok.pad_token_id)
        logits, _ = model(b["input_ids"], b["attention_mask"], b["marker_pos"], b["marker_mask"], b["qtype"])
        pred = int(logits[0].argmax().item())
        pred_yes = keys[pred] == "yes"
        gold_yes = ex["target"]["golden_dog_potential"] == "yes"
        if pred_yes and gold_yes:
            tp += 1
        elif pred_yes and not gold_yes:
            fp += 1
        elif not pred_yes and gold_yes:
            fn += 1
        else:
            tn += 1
    acc = (tp + tn) / max(1, len(examples))
    recall = tp / max(1, tp + fn)          # 正样本召回率（金狗识别能力）
    precision = tp / max(1, tp + fp)        # 正样本精确率
    return {"acc": acc, "recall": recall, "precision": precision,
            "tp": tp, "fp": fp, "tn": tn, "fn": fn}


def _fmt_metrics(m):
    return (f"acc={m['acc']:.4f} recall={m['recall']:.4f} precision={m['precision']:.4f} "
            f"(tp={m['tp']} fp={m['fp']} tn={m['tn']} fn={m['fn']})")


def save_checkpoint(agent, state_dict, out_dir: Path, val_acc: float, test_acc: float):
    import shutil
    from safetensors.torch import save_file

    out_dir.mkdir(parents=True, exist_ok=True)
    save_file(state_dict, out_dir / "model.safetensors")

    # 复制 tokenizer 和 encoder（base model 的 multilingual 子目录），
    # 使新 checkpoint 可被 laya.load(本地目录) 直接加载。
    base_dir = Path(agent.cfg.get("_source_dir") or "")
    if not base_dir:
        # 从 agent 的 tok/model 反推不了目录时，从 HF 缓存定位
        import huggingface_hub
        base_dir = Path(huggingface_hub.constants.HF_HUB_CACHE or "") / "models--convaiinnovations--laya"

    # 更稳健：直接复用 agent 已经加载好的 tokenizer 目录路径
    # agent.tok 无法给出磁盘目录，故从 HF snapshot 找
    for name in ("tokenizer", "encoder"):
        src = _find_model_asset(name)
        if src and src.exists():
            shutil.copytree(src, out_dir / name, dirs_exist_ok=True)

    cfg = copy.deepcopy(agent.cfg)
    cfg["model_name"] = "laya-golden-dog-judge-v1"
    cfg["training"] = {
        "objective": "golden-dog-potential",
        "trained_layers": ["scorer", "act_head", "type_emb"],
        "frozen_layers": ["encoder", "head"],
        "question": "golden_dog_potential",
        "val_accuracy": round(val_acc, 4),
        "test_accuracy": round(test_acc, 4),
        "base": f"{MODEL_REPO}/{MODEL_SUBFOLDER}",
        "note": "金狗潜力判断器：二分类(yes/no) + goldenDogScore 校准",
    }
    (out_dir / "rl_agent_config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "base_model.txt").write_text(f"{MODEL_REPO}/{MODEL_SUBFOLDER}\n", encoding="utf-8")
    print(f"[save] checkpoint 已保存到 {out_dir}", flush=True)


def _find_model_asset(name: str):
    """在 HF 缓存中定位 multilingual 子目录下的 tokenizer/encoder 目录。"""
    cache_root = Path(os.environ.get("HF_HOME", str(ROOT / ".runtime-cache" / "laya-hf")))
    snapshots = cache_root / "hub" / "models--convaiinnovations--laya" / "snapshots"
    if not snapshots.exists():
        return None
    for snap in sorted(snapshots.iterdir(), reverse=True):
        cand = snap / MODEL_SUBFOLDER / name
        if cand.exists():
            return cand
    return None


def main():
    parser = argparse.ArgumentParser(description="微调 laya 为金狗潜力判断器")
    parser.add_argument("--data", default=str(ROOT / ".runtime-cache" / "training" / "golden-dog-labels.jsonl"))
    parser.add_argument("--out", default=str(ROOT / ".runtime-cache" / "laya-golden-dog-checkpoint"))
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--w-ce", type=float, default=1.0)
    parser.add_argument("--w-rl", type=float, default=0.3)
    parser.add_argument("--clip", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--log-every", type=int, default=5)
    parser.add_argument("--val-subset", type=int, default=30)
    parser.add_argument("--pos-weight", type=float, default=5.0)
    parser.add_argument("--cache", default=str(ROOT / ".runtime-cache" / "training" / "golden-dog-cache.pt"))
    args = parser.parse_args()

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_HOME", str(ROOT / ".runtime-cache" / "laya-hf"))

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    agent = load(MODEL_REPO, subfolder=MODEL_SUBFOLDER, device="cpu")
    model = agent.model
    max_len = agent.cfg.get("max_len", 1024)
    head_max_len = agent.cfg.get("head_max_len", 256)

    for name, param in model.named_parameters():
        param.requires_grad_(not (name.startswith("encoder.") or name.startswith("head.")))
    trainable = [n for n, p in model.named_parameters() if p.requires_grad]
    print(f"[train] 可训练参数 {len(trainable)} 个", flush=True)

    train_examples = load_examples(Path(args.data), "train")
    val_examples = load_examples(Path(args.data), "validation")
    test_examples = load_examples(Path(args.data), "test")
    print(f"[train] train={len(train_examples)} val={len(val_examples)} test={len(test_examples)}", flush=True)

    # 阶段1：预计算 marker hidden
    cache_path = Path(args.cache)
    if cache_path.exists():
        print(f"[train] 复用缓存 {cache_path}", flush=True)
        hidden_cache = torch.load(cache_path, weights_only=False)
    else:
        print(f"[train] 阶段1：预计算 {len(train_examples)} 条 marker hidden（约 {len(train_examples)*1.6:.0f}s）...", flush=True)
        hidden_cache = precompute_marker_hidden(agent, train_examples, max_len, head_max_len)
        torch.save(hidden_cache, cache_path)
        print(f"[train] 缓存已保存 {cache_path}", flush=True)

    # 阶段2：轻量头训练（均衡采样 + 纯 CE，避免灾难性遗忘）
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=args.lr, weight_decay=args.weight_decay,
    )
    best_val_f1 = -1.0
    best_state = None
    batch_size = args.batch_size
    steps = 0
    qtype_choice = torch.tensor([QTYPES["choice"]])

    # 正负样本分离（label: 0=yes(金狗), 1=no），用于均衡采样
    # 注意 keys = ["yes", "no"]，故 label 0 = 正样本(yes)
    pos_idx = [j for j, it in enumerate(hidden_cache) if it["label"] == 0]
    neg_idx = [j for j, it in enumerate(hidden_cache) if it["label"] == 1]
    n_pos, n_neg = len(pos_idx), len(neg_idx)
    print(f"[train] 均衡采样：正={n_pos} 负={n_neg}", flush=True)
    half = max(1, batch_size // 2)

    for epoch in range(args.epochs):
        # 每步均衡采样：half 正 + half 负
        n_steps = max(1, max(n_pos, n_neg) // half)
        epoch_losses = []
        for step_i in range(n_steps):
            batch_idx = []
            # 正样本有放回采样
            for _ in range(half):
                batch_idx.append(pos_idx[np.random.randint(0, n_pos)] if n_pos else neg_idx[0])
            # 负样本有放回采样
            for _ in range(half):
                batch_idx.append(neg_idx[np.random.randint(0, n_neg)] if n_neg else pos_idx[0])
            np.random.shuffle(batch_idx)
            batch_items = [hidden_cache[j] for j in batch_idx]

            m = torch.stack([it["m"] for it in batch_items])
            marker_mask = torch.stack([it["marker_mask"] for it in batch_items])
            pooled = torch.stack([it["pooled"] for it in batch_items])
            feats = torch.stack([it["feats"] for it in batch_items])
            label = torch.tensor([it["label"] for it in batch_items])

            logits = model.scorer(m).squeeze(-1).float()
            logits = logits.masked_fill(~marker_mask, -1e4)
            # act_head 前向（保持参数参与训练，但不影响 yes/no 判定）
            _ = model.act_head(torch.cat([pooled, feats], -1))

            # 纯均衡 CE（正负样本已均衡采样，无需 pos_weight，避免把分布整体压偏）
            ce = F.cross_entropy(logits, label)
            loss = ce

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], args.clip)
            optimizer.step()

            steps += 1
            epoch_losses.append(float(loss.detach()))
            if steps % args.log_every == 0:
                print(f"[train] epoch={epoch} step={steps} loss={float(loss.detach()):.4f}", flush=True)

        avg = float(np.mean(epoch_losses))
        val_m = evaluate(agent, val_examples[: args.val_subset], max_len, head_max_len)
        f1 = 2 * val_m["precision"] * val_m["recall"] / max(1e-9, val_m["precision"] + val_m["recall"])
        print(f"[train] epoch={epoch} 完成 avg_loss={avg:.4f} val {_fmt_metrics(val_m)} f1={f1:.4f}", flush=True)
        if f1 > best_val_f1:
            best_val_f1 = f1
            best_state = copy.deepcopy(model.state_dict())

    print(f"[train] 最佳验证 F1: {best_val_f1:.4f}", flush=True)

    if best_state is not None:
        model.load_state_dict(best_state)
    test_m = evaluate(agent, test_examples, max_len, head_max_len)
    print(f"[train] 测试集 {_fmt_metrics(test_m)}", flush=True)

    save_checkpoint(agent, model.state_dict(), Path(args.out), best_val_f1, test_m["acc"])


if __name__ == "__main__":
    main()
