"""金狗潜力判断器推理入口。

加载微调后的 checkpoint（scorer 已特训为 golden_dog_potential yes/no），
对单个或批量候选输出金狗潜力判断（是/否 + 概率 + 置信度）。

用法：
  python infer_golden_dog.py --checkpoint .runtime-cache/laya-golden-dog-checkpoint --state '{"symbol":"TAKO","meme_score":62,...}'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".runtime-tools" / "laya-venv" / "Lib" / "site-packages"))
sys.path.insert(0, str(ROOT / "laya_golden_dog_training"))

from laya import load
from build_dataset import GOLDEN_DOG_QUESTIONS


def main():
    parser = argparse.ArgumentParser(description="金狗潜力判断器推理")
    parser.add_argument("--checkpoint", default=str(ROOT / ".runtime-cache" / "laya-golden-dog-checkpoint"))
    parser.add_argument("--base", default="convaiinnovations/laya")
    parser.add_argument("--base-subfolder", default="multilingual")
    parser.add_argument("--state", help="JSON 字符串的候选 state，或省略从 stdin 读 JSONL")
    args = parser.parse_args()

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_HOME", str(ROOT / ".runtime-cache" / "laya-hf"))

    # 加载微调后的 checkpoint
    ckpt = Path(args.checkpoint)
    agent = load(str(ckpt), device="cpu") if ckpt.exists() else load(args.base, subfolder=args.base_subfolder, device="cpu")

    question = GOLDEN_DOG_QUESTIONS
    states = []
    if args.state:
        states.append(json.loads(args.state))
    else:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            states.append(json.loads(line))

    for state in states:
        result = agent.system_one(state, question)
        answer = result["answers"]["golden_dog_potential"]
        print(json.dumps({
            "symbol": state.get("symbol"),
            "name": state.get("name"),
            "choice": answer["choice"],
            "goldenDogProbability": answer["probabilities"].get("yes"),
            "probabilities": answer["probabilities"],
            "confidence": answer["confidence"],
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
