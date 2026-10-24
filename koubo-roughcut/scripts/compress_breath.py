#!/usr/bin/env python3
"""Compress pauses to breath.maxKeep using transcript word timings.

A long gap keeps the last maxKeep seconds before the next word, whether the
gap sits inside one keep or between two. The leadIn seconds before a word
that follows a content cut, or before the first word, stay inside a keep.
Speech-level audio with no word stays in a keep and is flagged for a listen.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from decisions_common import recompute_timeline
from merge_retake_judgments import next_id

CONTENT_DROP = {"retake", "stutter", "manual", "uncertain"}
VOICE_PEAK = 0.08
VOICE_MIN = 0.12
VOICE_MERGE = 0.10
VOICE_NOTE = "这里有人声，但没有字"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


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


def loud_runs(
    peaks: list[float],
    duration: float,
    threshold: float = VOICE_PEAK,
    min_duration: float = VOICE_MIN,
    merge_gap: float = VOICE_MERGE,
) -> list[tuple[float, float]]:
    """Spans whose peak stays at speech level. Short dips inside merge_gap join up."""
    if not peaks or duration <= 0:
        return []
    dt = duration / len(peaks)
    raw: list[tuple[float, float]] = []
    run: int | None = None
    for i in range(len(peaks) + 1):
        loud = i < len(peaks) and peaks[i] >= threshold
        if loud:
            if run is None:
                run = i
        elif run is not None:
            raw.append((run * dt, i * dt))
            run = None
    merged: list[tuple[float, float]] = []
    for start, end in raw:
        if merged and start - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return [(a, b) for a, b in merged if b - a >= min_duration - 0.001]


def _starts_at_content_end(start: float, drops: list[dict]) -> bool:
    """True when this audio is the tail of a deleted take, not a missing word."""
    for drop in drops:
        if drop.get("reason") not in CONTENT_DROP:
            continue
        end = float(drop["sourceEnd"])
        if end - 0.001 <= start <= end + 0.05:
            return True
    return False


def _add_voice_flag(data: dict, start: float) -> None:
    flags = data.setdefault("flags", [])
    for item in flags:
        if item.get("note") == VOICE_NOTE and abs(float(item.get("sourceStart") or 0) - start) <= 0.3:
            return
    flags.append(
        {
            "id": next_id("f", flags),
            "sourceStart": round(start, 3),
            "reason": "uncertain",
            "note": VOICE_NOTE,
        }
    )


def reclaim_untranscribed_voice(
    keeps: list[dict],
    drops: list[dict],
    peaks: list[float],
    duration: float,
    data: dict,
) -> tuple[list[dict], list[dict]]:
    """Keep speech-level audio that never became a word.

    Content cuts stay cut, including the moment right after one ends.
    """
    if not peaks or duration <= 0:
        return keeps, drops
    blocked = [
        (float(item["sourceStart"]), float(item["sourceEnd"]))
        for item in keeps
    ]
    blocked.extend(
        (float(drop["sourceStart"]), float(drop["sourceEnd"]))
        for drop in drops
        if drop.get("reason") in CONTENT_DROP
    )
    for start, end in loud_runs(peaks, duration):
        for piece_start, piece_end in _free_spans(start, end, blocked):
            if piece_end - piece_start < VOICE_MIN - 0.001:
                continue
            if _starts_at_content_end(piece_start, drops):
                continue
            drops = _reclaim_long_pause(drops, piece_start, piece_end)
            keeps.append(
                {
                    "sourceStart": round(piece_start, 3),
                    "sourceEnd": round(piece_end, 3),
                    "text": "",
                    "reason": "breath",
                    "untranscribed": True,
                }
            )
            blocked.append((piece_start, piece_end))
            _add_voice_flag(data, piece_start)
    return keeps, drops


def resolve_source(decisions_path: Path, data: dict) -> Path | None:
    listed = str((data.get("source") or {}).get("path") or "").strip()
    if not listed:
        return None
    listed_path = Path(listed)
    run_dir = decisions_path.parent
    candidates: list[Path] = []
    if listed_path.is_absolute():
        candidates.append(listed_path)
    else:
        candidates.append(Path.cwd() / listed_path)
        candidates.append(REPO_ROOT / listed_path)
    for base in (run_dir, run_dir.parent, run_dir.parent.parent, REPO_ROOT):
        candidates.append(base / listed_path)
        candidates.append(base / listed_path.name)
    seen: set[Path] = set()
    for cand in candidates:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def sync_voice_review(path: Path, data: dict) -> int:
    """Append listen lines for voice flags. Existing lines stay put."""
    flags = [item for item in (data.get("flags") or []) if item.get("note") == VOICE_NOTE and item.get("id")]
    if not path.is_file() or not flags:
        return 0
    text = path.read_text(encoding="utf-8")
    added: list[str] = []
    for item in flags:
        if f"- {item['id']} " in text:
            continue
        added.append(f"- {item['id']} {float(item.get('sourceStart') or 0):.1f}s {VOICE_NOTE}")
    if not added:
        return 0
    body = text.strip()
    if body in {"没有听点", "# 听点\n\n没有听点", "# 听点\n没有听点"}:
        path.write_text("# 听点\n\n" + "\n".join(added) + "\n", encoding="utf-8")
    else:
        path.write_text(text.rstrip() + "\n" + "\n".join(added) + "\n", encoding="utf-8")
    return len(added)


def apply_compress_breath(
    data: dict,
    words: list[tuple[float, float]],
    peaks: list[float] | None = None,
    duration: float = 0.0,
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
            drops.append(_long_pause(ds, de, drops))
    lead_in = float((data.get("breath") or {}).get("leadIn") or 0.12)
    new_keep, drops = unify_word_gaps(new_keep, drops, words, max_keep, lead_in)
    if peaks:
        span = duration or float((data.get("source") or {}).get("duration") or 0)
        new_keep, drops = reclaim_untranscribed_voice(new_keep, drops, peaks, span, data)
    for i, item in enumerate(sorted(new_keep, key=lambda x: float(x["sourceStart"])), start=1):
        item["id"] = f"k{i:03d}"
    data["keep"] = new_keep
    data["drop"] = drops
    recompute_timeline(data)
    timeline = float(data["keep"][-1]["timelineEnd"]) if data["keep"] else 0.0
    return data["keep"], drops, timeline


def _long_pause(start: float, end: float, drops: list[dict]) -> dict:
    return {
        "id": next_id("d", drops),
        "sourceStart": round(start, 3),
        "sourceEnd": round(end, 3),
        "text": "",
        "reason": "long-pause",
    }


def _free_spans(
    start: float,
    end: float,
    blocked: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    cursor = start
    free: list[tuple[float, float]] = []
    for left, right in sorted(blocked):
        if right <= cursor:
            continue
        if left > cursor:
            free.append((cursor, min(left, end)))
        cursor = max(cursor, right)
        if cursor >= end:
            break
    if cursor < end - 0.001:
        free.append((cursor, end))
    return [(a, b) for a, b in free if b - a >= 0.01]


def _reclaim_long_pause(drops: list[dict], start: float, end: float) -> list[dict]:
    kept: list[dict] = []
    for drop in drops:
        ds, de = float(drop["sourceStart"]), float(drop["sourceEnd"])
        if drop.get("reason") != "long-pause" or de <= start + 0.001 or ds >= end - 0.001:
            kept.append(drop)
            continue
        if ds < start - 0.001:
            left = dict(drop)
            left["sourceEnd"] = round(start, 3)
            kept.append(left)
        if de > end + 0.001:
            right = dict(drop)
            right["sourceStart"] = round(end, 3)
            right.pop("id", None)
            right["id"] = next_id("d", kept)
            kept.append(right)
    return kept


def unify_word_gaps(
    keeps: list[dict],
    drops: list[dict],
    words: list[tuple[float, float]],
    max_keep: float,
    lead_in: float,
) -> tuple[list[dict], list[dict]]:
    """One breath length before every kept word.

    Between two kept words, a gap longer than max_keep leaves the last
    max_keep seconds. After a content cut, and before the first word, only
    lead_in comes back into a keep. Neither reaches into a content drop.
    """
    speech = [item for item in keeps if item.get("reason") == "speech"]
    for seg in speech:
        seg_words = words_in_range(words, float(seg["sourceStart"]), float(seg["sourceEnd"]))
        if not seg_words:
            continue
        word_start = seg_words[0][0]
        speech_ends = [
            float(item["sourceEnd"])
            for item in speech
            if float(item["sourceEnd"]) <= word_start + 0.001 and item is not seg
        ]
        content_ends = [
            float(drop["sourceEnd"])
            for drop in drops
            if drop.get("reason") in CONTENT_DROP and float(drop["sourceEnd"]) <= word_start + 0.001
        ]
        prev_speech = max(speech_ends) if speech_ends else None
        prev_content = max(content_ends) if content_ends else None
        if prev_speech is not None and (prev_content is None or prev_speech > prev_content + 0.001):
            floor = prev_speech
            cap = max_keep
        else:
            floor = prev_content or 0.0
            cap = lead_in
        if word_start - floor <= 0.01 or cap <= 0:
            continue
        retain = min(word_start - floor, cap)
        desired = word_start - retain
        for drop in drops:
            if drop.get("reason") not in CONTENT_DROP:
                continue
            ds, de = float(drop["sourceStart"]), float(drop["sourceEnd"])
            if ds < word_start - 0.001 and de > desired + 0.001:
                desired = max(desired, min(de, word_start))
        if word_start - desired < 0.01:
            continue
        keep_spans = [(float(item["sourceStart"]), float(item["sourceEnd"])) for item in keeps]
        for start, end in _free_spans(desired, word_start, keep_spans):
            drops = _reclaim_long_pause(drops, start, end)
            keeps.append(
                {
                    "sourceStart": round(start, 3),
                    "sourceEnd": round(end, 3),
                    "text": "",
                    "reason": "breath",
                }
            )
        blocked = keep_spans + [(float(drop["sourceStart"]), float(drop["sourceEnd"])) for drop in drops]
        for start, end in _free_spans(floor, desired, blocked):
            pause = _long_pause(start, end, drops)
            drops.append(pause)
    return keeps, drops


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

    peaks = None
    duration = 0.0
    source = resolve_source(args.decisions, data)
    if source is None:
        print("没有源片，没字的人声仍按静音处理", file=sys.stderr)
    else:
        from review_server import compute_peaks, probe_duration

        duration = probe_duration(source)
        bins = max(8000, min(96000, int(duration * 64)))
        peaks = compute_peaks(source, bins)
    new_keep, extra_drops, timeline = apply_compress_breath(data, words, peaks, duration)
    before_tl = float(old_keep[-1]["timelineEnd"])
    after_tl = timeline
    data["keep"] = new_keep
    data["drop"] = extra_drops

    out_path = args.decisions if args.in_place else args.decisions.with_name("cut_decisions.breath.json")
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    review = args.decisions.parent / "review.md"
    added = sync_voice_review(review, data)
    if added:
        print(f"review.md +{added} 有声无字", file=sys.stderr)
    elif any(item.get("note") == VOICE_NOTE for item in data.get("flags") or []) and not review.is_file():
        print("有声无字已写入 flags。review.md 还没有，开审片前补上对应听点。", file=sys.stderr)
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
