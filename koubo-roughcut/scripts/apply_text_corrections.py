#!/usr/bin/env python3
"""Apply text-only ASR corrections. Times and word count stay put. Step 1b."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def word_text(word: dict) -> str:
    return str(word.get("word") or word.get("text") or "")


def set_word_text(word: dict, text: str) -> None:
    if "word" in word or "text" not in word:
        word["word"] = text
        word.pop("text", None)
    else:
        word["text"] = text


def rounded(value: float) -> float:
    return round(float(value), 3)


def iter_words(transcript: dict):
    for seg_i, seg in enumerate(transcript.get("segments") or []):
        for word_i, word in enumerate(seg.get("words") or []):
            yield seg_i, word_i, word


def apply(transcript: dict, corrections: dict) -> tuple[dict, list[dict]]:
    replacements = corrections.get("replacements") or []
    if not isinstance(replacements, list):
        raise SystemExit("corrections.replacements must be a list")
    out = json.loads(json.dumps(transcript, ensure_ascii=False))
    used: set[tuple[int, int]] = set()
    applied: list[dict] = []
    for index, item in enumerate(replacements):
        if not isinstance(item, dict):
            raise SystemExit(f"replacements[{index}] must be an object")
        try:
            start = rounded(item["start"])
            end = rounded(item["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"replacements[{index}] needs numeric start and end") from exc
        source = str(item.get("from") or "")
        target = str(item.get("to") or "")
        if not source or not target:
            raise SystemExit(f"replacements[{index}] needs non-empty from and to")
        if source == target:
            raise SystemExit(f"replacements[{index}] from and to are the same: {source}")
        matches = []
        for seg_i, word_i, word in iter_words(out):
            if (seg_i, word_i) in used:
                continue
            if rounded(word["start"]) != start or rounded(word["end"]) != end:
                continue
            if word_text(word) != source:
                continue
            matches.append((seg_i, word_i, word))
        if len(matches) != 1:
            raise SystemExit(
                f"replacements[{index}] {source!r} @{start}-{end} matched {len(matches)} words; "
                "refusing a loose match"
            )
        seg_i, word_i, word = matches[0]
        set_word_text(word, target)
        used.add((seg_i, word_i))
        applied.append(
            {
                "start": start,
                "end": end,
                "from": source,
                "to": target,
                "context": str(item.get("context") or ""),
            }
        )
    for seg_i, seg in enumerate(out.get("segments") or []):
        if any(hit[0] == seg_i for hit in used):
            seg["text"] = "".join(word_text(word) for word in seg.get("words") or [])
    if "text" in out:
        out["text"] = "".join(str(seg.get("text") or "") for seg in out.get("segments") or [])
    assert_times_unchanged(transcript, out)
    return out, applied


def assert_times_unchanged(before: dict, after: dict) -> None:
    left = list(iter_words(before))
    right = list(iter_words(after))
    if len(left) != len(right):
        raise SystemExit("word count changed; corrections may only replace text")
    for (_, _, a), (_, _, b) in zip(left, right):
        if rounded(a["start"]) != rounded(b["start"]) or rounded(a["end"]) != rounded(b["end"]):
            raise SystemExit("a word time changed; refusing to write the corrected transcript")


def write_markdown(path: Path, applied: list[dict], unresolved: list) -> None:
    lines = ["# 修字表", "", "| 时间 | 原文 | 改成 | 上下文 |", "|---|---|---|---|"]
    for item in applied:
        lines.append(
            f"| {item['start']:.3f}–{item['end']:.3f} | {item['from']} | {item['to']} | {item['context']} |"
        )
    if not applied:
        lines.append("| — | — | 无替换 | — |")
    lines.extend(["", "## 未改", ""])
    if not unresolved:
        lines.append("没有拿不准的词。")
    else:
        lines.append("| 原文 | 为什么不改 |")
        lines.append("|---|---|")
        for item in unresolved:
            if isinstance(item, dict):
                lines.append(f"| {item.get('text', '')} | {item.get('note', '')} |")
            else:
                lines.append(f"| {item} | |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def compact_md(result: dict) -> str:
    lines = ["# Transcript（修字后，时间与原文相同）", ""]
    for i, seg in enumerate(result.get("segments") or [], 1):
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or 0)
        text = " ".join(str(seg.get("text") or "").split())
        lines.append(f"{i:03d}  {start:.3f}-{end:.3f}  {text}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--corrections", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    transcript = load_json(args.transcript)
    corrections = load_json(args.corrections)
    corrected, applied = apply(transcript, corrections)
    args.output.write_text(json.dumps(corrected, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = args.output.with_suffix(".md")
    md_path.write_text(compact_md(corrected), encoding="utf-8")
    unresolved = corrections.get("unresolved") or []
    report = args.corrections.with_suffix(".md")
    write_markdown(report, applied, unresolved)
    print(f"applied {len(applied)} text replacement(s)", file=sys.stderr)
    print(args.output)
    print(md_path)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
