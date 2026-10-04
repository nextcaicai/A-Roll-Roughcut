#!/usr/bin/env python3
"""Compress pauses inside keep ranges using ffmpeg silencedetect on source audio."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SILENCE_START = re.compile(r"silence_start: ([0-9.]+)")
SILENCE_END = re.compile(r"silence_end: ([0-9.]+) \| silence_duration: ([0-9.]+)")


def detect_silences(source: Path, ss: float, se: float, noise_db: float, min_d: float) -> list[tuple[float, float]]:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-ss",
            f"{ss:.3f}",
            "-to",
            f"{se:.3f}",
            "-i",
            str(source),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_d}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr[-800:] if proc.stderr else "ffmpeg failed")

    silences: list[tuple[float, float]] = []
    pending: float | None = None
    for line in proc.stderr.splitlines():
        m = SILENCE_START.search(line)
        if m:
            pending = float(m.group(1))
            continue
        m = SILENCE_END.search(line)
        if m and pending is not None:
            end = float(m.group(1))
            silences.append((ss + pending, ss + end))
            pending = None
    return silences


def compress_range(
    ss: float,
    se: float,
    silences: list[tuple[float, float]],
    max_keep: float,
) -> list[tuple[float, float, str]]:
    """Split [ss,se] into speech/breath keeps; trim silences longer than max_keep."""
    if se <= ss:
        return []
    inside = [(max(s, ss), min(e, se)) for s, e in silences if e > ss and s < se]
    inside.sort()
    parts: list[tuple[float, float, str]] = []
    cursor = ss
    for s, e in inside:
        if e <= cursor:
            continue
        if s > cursor:
            parts.append((cursor, s, "speech"))
        dur = e - s
        if dur > max_keep:
            parts.append((e - max_keep, e, "breath"))
            cursor = e
        else:
            parts.append((s, e, "breath"))
            cursor = e
    if cursor < se:
        parts.append((cursor, se, "speech"))
    merged: list[tuple[float, float, str]] = []
    for start, end, kind in parts:
        if end <= start:
            continue
        if merged and merged[-1][2] == kind and abs(merged[-1][1] - start) < 0.001:
            merged[-1] = (merged[-1][0], end, kind)
        else:
            merged.append((start, end, kind))
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--noise-db", type=float, default=-35.0)
    parser.add_argument("--min-silence", type=float, default=0.18, help="silencedetect d= (seconds)")
    parser.add_argument("--max-keep", type=float, default=None, help="override breath.maxKeep")
    args = parser.parse_args()

    data = json.loads(args.decisions.read_text(encoding="utf-8"))
    max_keep = float(args.max_keep or (data.get("breath") or {}).get("maxKeep") or 0.4)
    old_keep = data.get("keep") or []
    if not old_keep:
        raise SystemExit("no keep ranges")

    new_keep: list[dict] = []
    timeline = 0.0
    for item in old_keep:
        ss, se = float(item["sourceStart"]), float(item["sourceEnd"])
        sil = detect_silences(args.source, ss, se, args.noise_db, args.min_silence)
        parts = compress_range(ss, se, sil, max_keep)
        if not parts:
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

    for i, k in enumerate(new_keep, start=1):
        k["id"] = f"k{i:03d}"

    before = float(old_keep[-1]["timelineEnd"])
    data["keep"] = new_keep
    data["breath"] = {**(data.get("breath") or {}), "maxKeep": max_keep}

    out = args.decisions if args.in_place else args.decisions.with_name("cut_decisions.audio_breath.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"keep {len(old_keep)} -> {len(new_keep)}  timeline {before:.3f}s -> {timeline:.3f}s  "
        f"saved {before - timeline:.3f}s",
        file=sys.stderr,
    )
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
