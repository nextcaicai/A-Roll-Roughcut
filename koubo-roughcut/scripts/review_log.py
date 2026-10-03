"""Build review_log.json from baseline vs final cut_decisions (review panel save)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decisions_common import PIPELINE_SNAPSHOT, normalize_zh


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_words(transcript: dict) -> list[tuple[float, float, str]]:
    words: list[tuple[float, float, str]] = []
    for seg in transcript.get("segments") or []:
        for w in seg.get("words") or []:
            ws, we = float(w.get("start", 0)), float(w.get("end", 0))
            t = str(w.get("text") or w.get("word") or "").strip()
            if t and we > ws:
                words.append((ws, we, t))
    words.sort(key=lambda x: x[0])
    return words


def context_around(
    words: list[tuple[float, float, str]],
    source_start: float,
    source_end: float,
    *,
    before_chars: int = 24,
    after_chars: int = 24,
) -> dict[str, str]:
    mid = (source_start + source_end) / 2
    before: list[str] = []
    after: list[str] = []
    for ws, we, t in words:
        if we <= source_start + 0.001:
            before.append(t)
        elif ws >= source_end - 0.001:
            after.append(t)
    b = normalize_zh("".join(before))[-before_chars:]
    a = normalize_zh("".join(after))[:after_chars]
    return {"before": b, "after": a, "midSourceSec": round(mid, 3)}


def timeline_end(decisions: dict) -> float:
    keep = decisions.get("keep") or []
    if not keep:
        return 0.0
    return max(float(k.get("timelineEnd") or 0) for k in keep)


MIN_DIFF_SEC = 0.1


def kept_intervals(decisions: dict) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for k in sorted(decisions.get("keep") or [], key=lambda x: float(x["sourceStart"])):
        ss, se = float(k["sourceStart"]), float(k["sourceEnd"])
        if out and ss <= out[-1][1] + 0.001:
            out[-1] = (out[-1][0], max(out[-1][1], se))
        else:
            out.append((ss, se))
    return out


def subtract(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Parts of intervals `a` not covered by `b`. Both sorted and non-overlapping."""
    out: list[tuple[float, float]] = []
    j = 0
    for s, e in a:
        cur = s
        while j < len(b) and b[j][1] <= cur:
            j += 1
        k = j
        while k < len(b) and b[k][0] < e:
            if b[k][0] > cur:
                out.append((cur, b[k][0]))
            cur = max(cur, b[k][1])
            k += 1
        if cur < e:
            out.append((cur, e))
    return out


def dominant_drop(start: float, end: float, drops: list[dict]) -> dict | None:
    best, best_len = None, 0.0
    for d in drops:
        ov = min(end, float(d["sourceEnd"])) - max(start, float(d["sourceStart"]))
        if ov > best_len:
            best, best_len = d, ov
    return best


def change_entry(
    start: float,
    end: float,
    drops: list[dict],
    *,
    words: list[tuple[float, float, str]] | None,
) -> dict[str, Any]:
    owner = dominant_drop(start, end, drops) or {}
    if words:
        text = "".join(t for ws, we, t in words if start <= (ws + we) / 2 < end)
    else:
        text = str(owner.get("text") or "")
    out: dict[str, Any] = {
        "id": owner.get("id"),
        "sourceStart": round(start, 3),
        "sourceEnd": round(end, 3),
        "text": text,
        "reason": owner.get("reason"),
        "keptInstead": owner.get("keptInstead"),
    }
    if words:
        out["context"] = context_around(words, start, end)
    return out


def diff_drops(
    baseline: dict,
    final: dict,
    *,
    words: list[tuple[float, float, str]] | None,
) -> dict[str, Any]:
    """Compare what is cut, by time. Splitting or re-labelling a drop is not a change."""
    base_kept = kept_intervals(baseline)
    final_kept = kept_intervals(final)
    restored = [
        change_entry(s, e, baseline.get("drop") or [], words=words)
        for s, e in subtract(final_kept, base_kept)
        if e - s >= MIN_DIFF_SEC
    ]
    added = [
        change_entry(s, e, final.get("drop") or [], words=words)
        for s, e in subtract(base_kept, final_kept)
        if e - s >= MIN_DIFF_SEC
    ]

    return {
        "dropsRestored": restored,
        "dropsAdded": added,
        "dropsRestoredCount": len(restored),
        "dropsAddedCount": len(added),
    }


def flag_summary(final: dict) -> dict[str, Any]:
    flags = final.get("flags") or []
    unheard = [
        {
            "id": f.get("id"),
            "sourceStart": round(float(f.get("sourceStart") or 0), 3),
            "note": str(f.get("note") or f.get("reason") or ""),
        }
        for f in flags
        if not f.get("heard")
    ]
    return {"total": len(flags), "heardCount": len(flags) - len(unheard), "unheard": unheard}


def build_review_log(
    *,
    run_dir: Path,
    baseline: dict,
    final: dict,
    session: dict[str, Any],
    transcript_path: Path | None = None,
) -> dict[str, Any]:
    words: list[tuple[float, float, str]] | None = None
    if transcript_path and transcript_path.is_file():
        try:
            raw = json.loads(transcript_path.read_text(encoding="utf-8"))
            words = load_words(raw)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            words = None

    diff = diff_drops(baseline, final, words=words)
    base_path = run_dir / "cut_decisions.json"

    return {
        "version": 1,
        "generatedAt": _iso_now(),
        "runDir": str(run_dir),
        "script": final.get("script") or baseline.get("script"),
        "source": final.get("source") or baseline.get("source"),
        "session": session,
        "baseline": {
            "from": PIPELINE_SNAPSHOT,
            "note": "pipeline output, frozen the first time the review panel opened this run",
            "keepCount": len(baseline.get("keep") or []),
            "dropCount": len(baseline.get("drop") or []),
            "flagCount": len(baseline.get("flags") or []),
            "timelineEndSec": round(timeline_end(baseline), 3),
        },
        "final": {
            "from": str(base_path.name),
            "note": "decisions after last PUT /api/decisions",
            "keepCount": len(final.get("keep") or []),
            "dropCount": len(final.get("drop") or []),
            "flagCount": len(final.get("flags") or []),
            "timelineEndSec": round(timeline_end(final), 3),
        },
        "diff": diff,
        "flags": flag_summary(final),
        "stats": {
            "timelineDeltaSec": round(timeline_end(final) - timeline_end(baseline), 3),
            **{k: diff[k] for k in ("dropsRestoredCount", "dropsAddedCount")},
        },
    }


def write_review_log(run_dir: Path, payload: dict) -> Path:
    out = run_dir / "review_log.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from run_summary import merge_review_into_summary

    merge_review_into_summary(run_dir, payload)
    return out
