#!/usr/bin/env python3
"""Write an SRT on the cut timeline from corrected words that fall inside keep."""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
from pathlib import Path

import jieba

MAX_CUE_CHARS = 18
HARD_MAX_CHARS = 24
CLAUSE_END = "，、；：。！？!?"
TRAILING_PAUSE = "，、；：。,;:."
MIN_SCRIPT_CLAUSE = 4
_SKIP = set(" \t\r\n*_`#>*[]()（）【】「」『』“”\"'《》〈〉·—…,.;:!?，、；：。！？")

jieba.setLogLevel(logging.ERROR)


def with_timeline(keep: list[dict], *, fps_n: int | None = None, fps_d: int = 1) -> list[dict]:
    ordered = sorted(keep, key=lambda item: float(item["sourceStart"]))
    cursor = 0.0
    timeline_frame = 0
    out = []
    for index, item in enumerate(ordered):
        start = round(float(item["sourceStart"]), 3)
        end = round(float(item["sourceEnd"]), 3)
        copied = dict(item)
        copied["keepIndex"] = index
        copied["sourceStart"] = start
        copied["sourceEnd"] = end
        if fps_n:
            from export_fcpxml import to_frames

            frame_in = to_frames(start, fps_n, fps_d)
            frame_out = to_frames(end, fps_n, fps_d)
            dur = max(1, frame_out - frame_in)
            copied["frameIn"] = frame_in
            copied["frameOut"] = frame_in + dur
            copied["timelineFrame"] = timeline_frame
            copied["fps_n"] = fps_n
            copied["fps_d"] = fps_d
            copied["timelineStart"] = timeline_frame * fps_d / fps_n
            copied["timelineEnd"] = (timeline_frame + dur) * fps_d / fps_n
            timeline_frame += dur
        else:
            span = end - start
            copied["timelineStart"] = round(cursor, 3)
            copied["timelineEnd"] = round(cursor + span, 3)
            cursor += span
        out.append(copied)
    return out


def gap_break_seconds(decisions: dict) -> float:
    max_keep = float((decisions.get("breath") or {}).get("maxKeep") or 0.4)
    return max(0.2, round(max_keep - 0.05, 3))


def load_words(transcript: dict) -> list[dict]:
    words = []
    for seg in transcript.get("segments") or []:
        for word in seg.get("words") or []:
            text = str(word.get("word") or "")
            if not text.strip():
                continue
            start = float(word.get("start") or 0)
            end = float(word.get("end") or 0)
            if end <= start:
                continue
            words.append({"word": text, "start": start, "end": end})
    return words


def keep_for_midpoint(start: float, end: float, keep: list[dict]) -> dict | None:
    mid = (start + end) / 2
    for item in keep:
        if item["sourceStart"] - 1e-6 <= mid <= item["sourceEnd"] + 1e-6:
            return item
    return None


def to_timeline(seconds: float, item: dict) -> float:
    if "timelineFrame" not in item:
        return float(item["timelineStart"]) + (seconds - float(item["sourceStart"]))
    from export_fcpxml import to_frames

    fps_n, fps_d = int(item["fps_n"]), int(item["fps_d"])
    local = to_frames(seconds, fps_n, fps_d) - int(item["frameIn"])
    span = int(item["frameOut"]) - int(item["frameIn"])
    local = min(max(local, 0), span)
    return (int(item["timelineFrame"]) + local) * fps_d / fps_n


def norm_text(text: str) -> str:
    out = []
    for char in text:
        if char in _SKIP or char.isspace():
            continue
        out.append(char.lower() if char.isascii() else char)
    return "".join(out)


def script_body(script: str) -> str:
    text = script.lstrip("\ufeff")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    lines = []
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def script_clauses(script: str) -> list[str]:
    clauses = []
    for chunk in re.split(r"[。！？!?；;，、：:\n]+", script_body(script)):
        folded = norm_text(chunk)
        if len(folded) >= MIN_SCRIPT_CLAUSE:
            clauses.append(folded)
    return clauses


def script_break_indexes(words: list[dict], script: str) -> set[int]:
    clauses = script_clauses(script)
    if not clauses:
        return set()
    chars: list[tuple[str, int]] = []
    for index, word in enumerate(words):
        for char in norm_text(word["text"]):
            chars.append((char, index))
    if not chars:
        return set()
    speech = "".join(char for char, _ in chars)
    breaks: set[int] = set()
    cursor = 0
    for clause in clauses:
        if cursor >= len(speech):
            break
        window = speech[cursor:]
        found = window.find(clause)
        if found < 0:
            continue
        end = cursor + found + len(clause)
        if end <= 0 or end > len(chars):
            continue
        word_index = chars[end - 1][1]
        breaks.add(word_index)
        next_cursor = end
        while next_cursor < len(chars) and chars[next_cursor][1] == word_index:
            next_cursor += 1
        cursor = max(cursor + 1, next_cursor)
    return breaks


def present_line(text: str) -> str:
    end = len(text)
    while end > 0 and text[end - 1] in TRAILING_PAUSE:
        end -= 1
    return text[:end]


def split_at_clause_ends(word: dict) -> list[dict]:
    """Split ASR chunks that contain text after sentence punctuation."""
    text = str(word["text"])
    cuts = [
        index + 1
        for index, char in enumerate(text[:-1])
        if char in CLAUSE_END and text[index + 1] not in CLAUSE_END
    ]
    if not cuts:
        return [word]
    boundaries = [0, *cuts, len(text)]
    start = float(word["start"])
    end = float(word["end"])
    duration = max(0.0, end - start)
    out = []
    for left, right in zip(boundaries, boundaries[1:]):
        if left == right:
            continue
        copied = dict(word)
        copied["text"] = text[left:right]
        copied["start"] = start + duration * left / len(text)
        copied["end"] = start + duration * right / len(text)
        out.append(copied)
    return out


def lexical_part_boundaries(parts: list[tuple[int, dict]]) -> list[int]:
    text = "".join(word["text"] for _, word in parts)
    token_ends = {end for _, _, end in jieba.tokenize(text)}
    boundaries = [0]
    offset = 0
    for index, (_, word) in enumerate(parts, start=1):
        offset += len(word["text"])
        if offset in token_ends:
            boundaries.append(index)
    if boundaries[-1] != len(parts):
        boundaries.append(len(parts))
    return boundaries


def semantic_parts(
    parts: list[tuple[int, dict]], max_chars: int, hard_max: int
) -> list[list[tuple[int, dict]]]:
    """Balance a long clause, but only cut at lexical word boundaries."""
    lengths = [len(word["text"]) for _, word in parts]
    total = sum(lengths)
    boundaries = lexical_part_boundaries(parts)
    count = min(max(2, math.ceil(total / max_chars)), len(boundaries) - 1)
    if count < 2:
        return [parts]

    prefix = [0]
    for length in lengths:
        prefix.append(prefix[-1] + length)
    target = total / count
    inf = float("inf")
    costs = [[inf] * (len(parts) + 1) for _ in range(count + 1)]
    previous = [[-1] * (len(parts) + 1) for _ in range(count + 1)]
    costs[0][0] = 0.0
    for groups in range(1, count + 1):
        for end in boundaries[1:]:
            for begin in boundaries[:-1]:
                if begin >= end:
                    continue
                size = prefix[end] - prefix[begin]
                if costs[groups - 1][begin] == inf:
                    continue
                overflow = max(0, size - hard_max)
                cost = costs[groups - 1][begin] + (size - target) ** 2 + overflow**2 * 16
                if cost < costs[groups][end]:
                    costs[groups][end] = cost
                    previous[groups][end] = begin
    if costs[count][len(parts)] == inf:
        return [parts]

    cuts = [len(parts)]
    end = len(parts)
    for groups in range(count, 0, -1):
        end = previous[groups][end]
        cuts.append(end)
    cuts.reverse()
    return [parts[left:right] for left, right in zip(cuts, cuts[1:])]


def build_cues(
    words: list[dict],
    keep: list[dict],
    *,
    max_chars: int = MAX_CUE_CHARS,
    hard_max: int = HARD_MAX_CHARS,
    gap_break: float = 0.35,
    script: str = "",
) -> list[dict]:
    placed = []
    for word in words:
        item = keep_for_midpoint(word["start"], word["end"], keep)
        if item is None:
            continue
        placed.extend(
            split_at_clause_ends(
                {
                    "text": word["word"],
                    "start": to_timeline(word["start"], item),
                    "end": to_timeline(word["end"], item),
                    "keepIndex": item.get("keepIndex", item["sourceStart"]),
                    "keepSourceStart": float(item["sourceStart"]),
                    "keepSourceEnd": float(item["sourceEnd"]),
                }
            )
        )
    breaks = script_break_indexes(placed, script) if script else set()
    cues: list[dict] = []
    buf: list[tuple[int, dict]] = []

    def emit(parts: list[tuple[int, dict]]) -> None:
        if not parts:
            return
        text = present_line("".join(word["text"] for _, word in parts))
        if text:
            cues.append({"start": parts[0][1]["start"], "end": parts[-1][1]["end"], "text": text})

    def flush_buf() -> None:
        if not buf:
            return
        text = "".join(word["text"] for _, word in buf)
        if len(text) <= hard_max:
            emit(buf)
            buf.clear()
            return
        for piece in semantic_parts(buf, max_chars, hard_max):
            emit(piece)
        buf.clear()

    for index, word in enumerate(placed):
        if buf and word["keepIndex"] != buf[-1][1]["keepIndex"]:
            hole = word["keepSourceStart"] - buf[-1][1]["keepSourceEnd"]
            if hole >= gap_break - 1e-9:
                flush_buf()
        if buf:
            gap = word["start"] - buf[-1][1]["end"]
            if gap >= gap_break - 1e-9:
                flush_buf()
        buf.append((index, word))
        clause_done = word["text"].endswith(tuple(CLAUSE_END))
        phrase_done = index in breaks
        if phrase_done and not clause_done:
            nxt = placed[index + 1] if index + 1 < len(placed) else None
            if nxt is not None and norm_text(nxt["text"]) == "":
                phrase_done = False
        if clause_done or phrase_done:
            flush_buf()
    flush_buf()

    for index, cue in enumerate(cues):
        nxt = cues[index + 1]["start"] if index + 1 < len(cues) else None
        if cue["end"] < cue["start"] + 0.2:
            cap = nxt if nxt is not None and nxt > cue["start"] else cue["start"] + 0.4
            cue["end"] = min(cue["start"] + 0.4, cap)
        if nxt is not None and cue["end"] > nxt:
            cue["end"] = nxt
    return [cue for cue in cues if cue["end"] > cue["start"] and cue["text"]]


def format_timestamp(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_srt(cues: list[dict]) -> str:
    blocks = []
    for index, cue in enumerate(cues, start=1):
        start = max(0.0, float(cue["start"]))
        end = max(start + 0.001, float(cue["end"]))
        blocks.append(f"{index}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{cue['text']}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def resolve_script(
    decisions: dict,
    *,
    decisions_path: Path | None = None,
    run_dir: Path | None = None,
    explicit: Path | None = None,
) -> str:
    if explicit is not None:
        if not explicit.is_file():
            raise ValueError(f"找不到逐字稿：{explicit}")
        return explicit.read_text(encoding="utf-8")
    name = str(decisions.get("script") or "").strip()
    if not name:
        return ""
    roots: list[Path] = []
    if run_dir is not None:
        roots.extend([run_dir, run_dir.parent, run_dir.parent.parent])
    if decisions_path is not None:
        roots.extend(
            [decisions_path.parent, decisions_path.parent.parent, decisions_path.parent.parent.parent]
        )
    for root in roots:
        path = root / name
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return ""


def render_srt(
    transcript: dict,
    decisions: dict,
    script: str = "",
    fps: tuple[int, int] | None = None,
) -> str:
    fps_n, fps_d = fps or (25, 1)
    keep = with_timeline(decisions.get("keep") or [], fps_n=fps_n, fps_d=fps_d)
    cues = build_cues(
        load_words(transcript),
        keep,
        gap_break=gap_break_seconds(decisions),
        script=script,
    )
    if not cues:
        raise ValueError("没有可导出的字幕")
    return format_srt(cues)


def write_srt(
    transcript: dict,
    decisions: dict,
    output: Path,
    script: str = "",
    fps: tuple[int, int] | None = None,
) -> int:
    text = render_srt(transcript, decisions, script, fps=fps)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    return text.count("\n\n") + (1 if text.strip() else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--script", type=Path, help="逐字稿。不传则按 decisions 里的 script 字段去成片目录找")
    parser.add_argument("--source", type=Path, help="源片。用来和 XML 取同一个整数帧率")
    args = parser.parse_args()
    if not args.transcript.is_file():
        raise SystemExit(f"transcript not found: {args.transcript}")
    if not args.decisions.is_file():
        raise SystemExit(f"decisions not found: {args.decisions}")
    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    decisions = json.loads(args.decisions.read_text(encoding="utf-8"))
    try:
        script = resolve_script(decisions, decisions_path=args.decisions, explicit=args.script)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    fps = None
    if args.source is not None:
        from export_fcpxml import probe

        if not args.source.is_file():
            raise SystemExit(f"source not found: {args.source}")
        try:
            media = probe(args.source)
        except SystemExit as exc:
            raise SystemExit(str(exc)) from exc
        fps = (media["fps_n"], media["fps_d"])
    text = render_srt(transcript, decisions, script, fps=fps)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    count = text.count("\n\n") + (1 if text.strip() else 0)
    print(f"{args.output} ({count} cues)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
