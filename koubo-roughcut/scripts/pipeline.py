#!/usr/bin/env python3
"""SKILL step 5 in one run: breath, lead-in, word stutter, script coverage, residue scan.

Stops at the first failing step and says how to resume. Does not call Jev.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from decisions_common import (
    PIPELINE_SNAPSHOT,
    decisions_problems,
    fill_decision_text,
    prune_orphaned_empty_keeps,
    workspace_root,
)
from review_log import load_words
from run_summary import build_run_summary, write_run_summary

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = workspace_root()
STEPS = ("breath", "stutter", "coverage", "residue")
STEP_LABELS = {
    "breath": "气口压缝 + 入点回退",
    "stutter": "词级卡顿",
    "coverage": "脚本覆盖",
    "residue": "残留扫描",
}


def find_script(run_dir: Path, decisions: dict, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.is_file() else None
    names = [str(decisions.get("script") or "").strip(), "脚本.md"]
    for name in filter(None, names):
        for root in (run_dir, run_dir.parent, run_dir.parent.parent):
            if (root / name).is_file():
                return root / name
    return None


def timeline_end(path: Path) -> float:
    data = json.loads(path.read_text(encoding="utf-8"))
    return max((float(k.get("timelineEnd") or 0) for k in data.get("keep") or []), default=0.0)


def step_commands(step: str, run: Path, script: Path | None) -> list[list[str]]:
    tr = str(run / "transcript.corrected.json")
    dec = str(run / "cut_decisions.json")
    validate = ["validate_decisions.py", dec]
    if step == "breath":
        return [
            ["compress_breath.py", "--transcript", tr, "--decisions", dec, "--in-place"],
            ["apply_lead_in.py", "--decisions", dec, "--in-place"],
            validate,
        ]
    if step == "stutter":
        return [["apply_word_stutter.py", "--transcript", tr, "--decisions", dec, "--apply"], validate]
    if step == "coverage":
        if script is None:
            return []
        return [
            ["validate_script_coverage.py", "--script", str(script), "--transcript", tr, "--decisions", dec, "--output", str(run / "script_coverage.md")]
        ]
    if step == "residue":
        return [["scan_retake_residue.py", "--transcript", tr, "--decisions", dec, "--output", str(run / "retake_residue.md")]]
    raise ValueError(step)


def run_command(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / cmd[0]), *cmd[1:]],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def coverage_missing(run: Path) -> int | None:
    path = run / "script_coverage.md"
    if not path.is_file():
        return None
    m = re.search(r"missing \*\*(\d+)\*\*", path.read_text(encoding="utf-8"))
    return int(m.group(1)) if m else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="<成片名>/runs/<日期>/")
    parser.add_argument("--script", type=Path, help="逐字稿；默认按 cut_decisions.json 的 script 或 脚本.md 找")
    parser.add_argument("--from", dest="start", choices=STEPS, default=STEPS[0], help="从这一步接着跑")
    args = parser.parse_args()

    run = args.run.expanduser().resolve()
    dec_path = run / "cut_decisions.json"
    if not (run / "transcript.corrected.json").is_file():
        print("没有 transcript.corrected.json。先做第 2 步修字，再跑这里。", file=sys.stderr)
        return 1
    if not dec_path.is_file():
        print("没有 cut_decisions.json。先做第 4 步保守取舍，再跑这里。", file=sys.stderr)
        return 1
    decisions = json.loads(dec_path.read_text(encoding="utf-8"))
    errors, _ = decisions_problems(decisions)
    if errors:
        print("cut_decisions.json 没过校验，先改好再跑：", file=sys.stderr)
        for msg in errors[:10]:
            print(f"  {msg}", file=sys.stderr)
        return 1

    steps = STEPS[STEPS.index(args.start) :]
    script = find_script(run, decisions, args.script)

    before = timeline_end(dec_path)
    for step in steps:
        cmds = step_commands(step, run, script)
        if not cmds:
            print(f"· {STEP_LABELS[step]}：没有逐字稿，跳过", file=sys.stderr)
            continue
        print(f"· {STEP_LABELS[step]}", file=sys.stderr)
        for cmd in cmds:
            proc = run_command(cmd)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()
                print(f"\n{STEP_LABELS[step]} 失败（{cmd[0]}）：\n{detail}", file=sys.stderr)
                print(f"\n修好后从这一步接着跑：--from {step}", file=sys.stderr)
                return 1

    snapshot = run / PIPELINE_SNAPSHOT
    if snapshot.is_file():
        snapshot.unlink()

    final = json.loads(dec_path.read_text(encoding="utf-8"))
    tr_path = run / "transcript.corrected.json"
    words = load_words(json.loads(tr_path.read_text(encoding="utf-8")))
    n_prune = prune_orphaned_empty_keeps(final, words)
    n_text = fill_decision_text(final, words)
    if n_prune or n_text:
        dec_path.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    after = timeline_end(dec_path)
    missing = coverage_missing(run)
    residue_md = run / "retake_residue.md"
    residue = _residue_counts(residue_md)
    write_run_summary(
        run,
        build_run_summary(
            run_dir=run,
            before_sec=before,
            after=final,
            coverage_missing=missing,
            residue=residue,
        ),
    )
    print("")
    source_sec = float((final.get("source") or {}).get("duration") or 0)
    cut_pct = f" · 删 {round((1 - after / source_sec) * 100)}%" if source_sec else ""
    print(f"成片 {before:.1f}s → {after:.1f}s{cut_pct} · keep {len(final.get('keep') or [])} · drop {len(final.get('drop') or [])} · 建议听 {len(final.get('flags') or [])}")
    if missing:
        print(f"脚本覆盖：{missing} 个专名不在 keep 里，看 script_coverage.md")
    elif script is None:
        print("脚本覆盖：没有逐字稿，没查")
    print(f"指标写在 {run / 'run_summary.json'}")
    print("下一步（第 6 步）：模型读 retake_residue.md 修订 cut_decisions.json，再开审片面板。")
    return 0


def _residue_counts(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {"repeats": 0, "bridgeStutter": 0, "suspiciousDrops": 0}
    text = path.read_text(encoding="utf-8")
    def rows_after(header: str) -> int:
        if header not in text:
            return 0
        chunk = text.split(header, 1)[1]
        nxt = chunk.find("\n## ")
        body = chunk if nxt < 0 else chunk[:nxt]
        return sum(1 for line in body.splitlines() if line.startswith("| ") and not line.startswith("|---") and "源片时间" not in line)
    return {
        "repeats": rows_after("## 疑似残留重说（keep 内或接缝）"),
        "bridgeStutter": rows_after("## 疑似误删（stutter 切断了前后句）"),
        "suspiciousDrops": rows_after("## 疑似误删（drop 里像完整句，且 keep 未再出现）"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
