from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402


DEFAULT_OUTPUT = ROOT / "docs" / "automation-briefs.json"


def build_payload(ids: list[str]) -> dict:
    briefs = []
    for automation_id in ids:
        brief = dict(server.latest_automation_brief(automation_id))
        brief.pop("sourceFile", None)
        brief["source"] = "codex-automation"
        briefs.append(brief)
    return {
        "updatedAt": int(time.time() * 1000),
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": "codex-automation-export",
        "briefs": briefs,
    }


def write_if_changed(path: Path, payload: dict) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def run_git(args: list[str]) -> None:
    subprocess.run(["git", *args], cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Codex automation briefs to a GitHub-readable JSON file.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path inside this repository.")
    parser.add_argument("--ids", nargs="*", default=list(server.AUTOMATION_BRIEF_IDS), help="Automation ids to export.")
    parser.add_argument("--push", action="store_true", help="Commit and push the JSON file after writing it.")
    parser.add_argument("--message", default="chore: update automation briefs", help="Git commit message for --push.")
    args = parser.parse_args()

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = ROOT / output_path

    payload = build_payload(args.ids)
    changed = write_if_changed(output_path, payload)
    print(f"wrote {output_path} ({len(payload['briefs'])} briefs, changed={changed})")

    if args.push and changed:
        run_git(["add", str(output_path.relative_to(ROOT))])
        run_git(["commit", "-m", args.message])
        run_git(["push"])
    elif args.push:
        print("no changes to push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
