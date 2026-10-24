#!/usr/bin/env python3
"""Apply references/asr-corrections.txt. Times and word count stay put.

Writes a transcript the model then corrects by hand. Does not touch transcript.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from apply_text_corrections import iter_words, set_word_text, word_text

REFERENCES = Path(__file__).resolve().parent.parent / "references" / "asr-corrections.txt"
_AFFIX = re.compile(r"^(\W*)(.*?)(\W*)$", re.UNICODE)


def split_affix(text: str) -> tuple[str, str, str]:
    match = _AFFIX.match(text or "")
    if not match:
        return "", text or "", ""
    return match.group(1), match.group(2), match.group(3)


def load_rules(path: Path) -> list[tuple[list[str], list[str]]]:
    rules: list[tuple[list[str], list[str]]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=>" not in line:
            raise SystemExit(f"错词表需要 => ：{raw}")
        left, right = line.split("=>", 1)
        src = left.split()
        dst = right.split()
        if not src or len(src) != len(dst):
            raise SystemExit(f"左右词数必须一样：{raw}")
        rules.append((src, dst))
    return rules


def _core(word: dict) -> str:
    return split_affix(word_text(word))[1]


def apply_rules(transcript: dict, rules: list[tuple[list[str], list[str]]]) -> tuple[dict, list[dict], list[str]]:
    out = json.loads(json.dumps(transcript, ensure_ascii=False))
    words = [word for _, _, word in iter_words(out)]
    applied: list[dict] = []
    unresolved: list[str] = []
    index = 0
    while index < len(words):
        matches: list[tuple[list[str], list[str]]] = []
        for src, dst in rules:
            size = len(src)
            if index + size > len(words):
                continue
            if all(_core(words[index + offset]).casefold() == token.casefold() for offset, token in enumerate(src)):
                matches.append((src, dst))
        if not matches:
            index += 1
            continue
        longest = max(len(src) for src, _ in matches)
        winners = [(src, dst) for src, dst in matches if len(src) == longest]
        targets = {tuple(dst) for _, dst in winners}
        if len(targets) > 1:
            shown = " ".join(_core(words[index + offset]) for offset in range(longest))
            unresolved.append(shown)
            index += 1
            continue
        _, dst = winners[0]
        if all(_core(words[index + offset]) == token for offset, token in enumerate(dst)):
            index += len(dst)
            continue
        start = float(words[index]["start"])
        end = float(words[index + len(dst) - 1]["end"])
        before = "".join(word_text(words[index + offset]) for offset in range(len(dst)))
        for offset, token in enumerate(dst):
            prefix, _, suffix = split_affix(word_text(words[index + offset]))
            set_word_text(words[index + offset], f"{prefix}{token}{suffix}")
        after = "".join(word_text(words[index + offset]) for offset in range(len(dst)))
        applied.append({"start": round(start, 3), "end": round(end, 3), "from": before, "to": after})
        index += len(dst)
    for seg in out.get("segments") or []:
        seg["text"] = "".join(word_text(word) for word in seg.get("words") or [])
    if "text" in out:
        out["text"] = "".join(str(seg.get("text") or "") for seg in out.get("segments") or [])
    return out, applied, unresolved


def write_report(path: Path, applied: list[dict], unresolved: list[str]) -> None:
    lines = ["# 错词表", "", "脚本按 `references/asr-corrections.txt` 替换。模型只处理下面没改的。", ""]
    lines.extend(["| 时间 | 原文 | 改成 |", "|---|---|---|"])
    if applied:
        for item in applied:
            lines.append(f"| {item['start']:.3f}–{item['end']:.3f} | {item['from']} | {item['to']} |")
    else:
        lines.append("| — | — | 没有对上的词 |")
    lines.extend(["", "## 对上了多条、没改", ""])
    if unresolved:
        for item in unresolved:
            lines.append(f"- {item}")
    else:
        lines.append("没有。")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="transcript.lexicon.json")
    parser.add_argument("--lexicon", type=Path, default=REFERENCES)
    parser.add_argument("--report", type=Path, help="default: <output dir>/corrections.lexicon.md")
    args = parser.parse_args()
    if not args.lexicon.is_file():
        raise SystemExit(f"没有错词表：{args.lexicon}")
    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    corrected, applied, unresolved = apply_rules(transcript, load_rules(args.lexicon))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(corrected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = args.report or args.output.parent / "corrections.lexicon.md"
    write_report(report, applied, unresolved)
    print(f"lexicon applied {len(applied)}", file=sys.stderr)
    if unresolved:
        print(f"ambiguous {len(unresolved)}, left for the model", file=sys.stderr)
    print(args.output)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
