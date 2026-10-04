#!/usr/bin/env python3
"""Compress intra-keep pauses to breath.maxKeep using transcript word timings.

Cut speech at breath_start (= next word start - maxKeep), not at ASR word-end.
Trim leading and trailing silence of a keep; the next keep's lead-in restores the inhale.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from merge_retake_judgments import next_id


def load_words(transcript: dict) -> list[tuple[float, float]]:
    words: list[tuple[float, float]] = []
    for seg in transcript.get("segments") or []:
        for w in seg.get("words") or []:
            ws, we = float(w["start"]), float(w["end"])
            if we <= ws:
                continue
            words.append((ws, we))
    words.sort(key=lambda x: x[0])
    return words


def words_in_range(words: list[tuple[float, float]], ss: float, se: float) -> list[tuple[float, float]]:
    return [(ws, we) for ws, we in words if we > ss and ws < se]


def merge_touching_keeps(keep: list[dict]) -> list[dict]:
    """Join keeps that already touch. Old compress left speech+breath stuck together."""
    runs: list[dict] = []
    for item in sorted(keep, key=lambda x: float(x["sourceStart"])):
        ss, se = float(item["sourceStart"]), float(item["sourceEnd"])
        if runs and ss <= float(runs[-1]["sourceEnd"]) + 0.001:
            runs[-1]["sourceEnd"] = max(float(runs[-1]["sourceEnd"]), se)
            if item.get("reason") == "speech":
                if item.get("text"):
                    runs[-1]["text"] = item.get("text") or runs[-1].get("text") or ""
                if item.get("scriptRef"):
                    runs[-1]["scriptRef"] = item["scriptRef"]
            continue
        runs.append(
            {
                "sourceStart": ss,
                "sourceEnd": se,
                "text": item.get("text") or "",
                "reason": "speech",
                "scriptRef": item.get("scriptRef"),
            }
        )
    return runs


def apply_compress_breath(
    data: dict,
    words: list[tuple[float, float]],
) -> tuple[list[dict], list[dict], float]:
    """Rewrite keep/drop in place. Returns (keep, drop, timeline_end)."""
    max_keep = float((data.get("breath") or {}).get("maxKeep") or 0.4)
    old_keep = merge_touching_keeps(data.get("keep") or [])
    drops = list(data.get("drop") or [])
    new_keep: list[dict] = []
    timeline = 0.0
    for item in old_keep:
        ss, se = float(item["sourceStart"]), float(item["sourceEnd"])
        parts, cut = compress_keep_words(words_in_range(words, ss, se), max_keep, ss, se)
        if not parts and not cut:
            parts = [(ss, se, str(item.get("reason") or "speech"))]
        for start, end, reason in parts:
            if end - start < 0.01:
                continue
            span = end - start
            entry = {
                "sourceStart": round(start, 3),
                "sourceEnd": round(end, 3),
                "timelineStart": round(timeline, 3),
                "timelineEnd": round(timeline + span, 3),
                "text": item.get("text") or "" if reason == "speech" else "",
                "reason": reason if reason in {"speech", "breath"} else item.get("reason") or "speech",
            }
            if item.get("scriptRef") and reason == "speech":
                entry["scriptRef"] = item["scriptRef"]
            new_keep.append(entry)
            timeline += span
        for ds, de in cut:
            if de - ds < 0.01:
                continue
            drops.append(
                {
                    "id": next_id("d", drops),
                    "sourceStart": round(ds, 3),
                    "sourceEnd": round(de, 3),
                    "text": "",
                    "reason": "long-pause",
                }
            )
    for i, item in enumerate(new_keep, start=1):
        item["id"] = f"k{i:03d}"
    data["keep"] = new_keep
    data["drop"] = drops
    return new_keep, drops, timeline


def compress_keep_words(
    word_spans: list[tuple[float, float]],
    max_keep: float,
    ss: float,
    se: float,
) -> tuple[list[tuple[float, float, str]], list[tuple[float, float]]]:
    """Split one keep. Long word-to-word gaps keep the last maxKeep as breath and drop the rest.

    Speech ends at the previous word. The kept breath sits on the inhale before the next word
    (`next_start - maxKeep`), not on the ASR word-end. Silence before the first word or after
    the last word is a long-pause drop; lead-in puts 0.12s back on the next keep.
    """
    if not word_spans:
        return [], [(ss, se)]

    keeps: list[tuple[float, float, str]] = []
    drops: list[tuple[float, float]] = []
    first_start = word_spans[0][0]
    if first_start > ss + 0.01:
        drops.append((ss, first_start))
    cur_start = first_start
    cur_end = word_spans[0][1]

    for ws, we in word_spans[1:]:
        if ws <= cur_end + max_keep:
            cur_end = we
            continue
        breath_start = max(first_start, min(se, ws - max_keep))
        speech_end = min(se, max(cur_start, cur_end))
        if speech_end > cur_start:
            keeps.append((cur_start, speech_end, "speech"))
        if breath_start > speech_end + 0.01:
            drops.append((speech_end, breath_start))
        if min(se, ws) > breath_start:
            keeps.append((breath_start, min(se, ws), "breath"))
        cur_start = ws
        cur_end = we

    if cur_end > cur_start:
        keeps.append((cur_start, cur_end, "speech"))
    if se > cur_end + 0.01:
        drops.append((cur_end, se))
    return keeps, drops


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--in-place", action="store_true", help="overwrite decisions file")
    args = parser.parse_args()

    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    data = json.loads(args.decisions.read_text(encoding="utf-8"))
    words = load_words(transcript)

    old_keep = data.get("keep") or []
    if not old_keep:
        raise SystemExit("no keep ranges")

    new_keep, extra_drops, timeline = apply_compress_breath(data, words)
    before_tl = float(old_keep[-1]["timelineEnd"])
    after_tl = timeline
    data["keep"] = new_keep
    data["drop"] = extra_drops

    out_path = args.decisions if args.in_place else args.decisions.with_name("cut_decisions.breath.json")
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"keep {len(old_keep)} -> {len(new_keep)}  "
        f"timeline {before_tl:.3f}s -> {after_tl:.3f}s  "
        f"saved {before_tl - after_tl:.3f}s",
        file=sys.stderr,
    )
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
