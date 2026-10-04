"""Shared helpers for cut_decisions.json post-processing."""
from __future__ import annotations

import re
from pathlib import Path

PUNCT_RE = re.compile(r"[\s，。！？、；：,.!?;:\"'「」『』（）()\[\]【】—\-…]+")
_ID_NUM = re.compile(r"^([kdf])(\d+)$")

REASONS = frozenset({"speech", "breath", "retake", "stutter", "long-pause", "uncertain", "manual"})
OVERLAP_TOL = 0.02
TOUCH_TOL = 0.05
PIPELINE_SNAPSHOT = "cut_decisions.pipeline.json"


def workspace_root() -> Path:
    """Directory that holds .env and the film folders.

    The skill lives at <root>/koubo-roughcut or <root>/.agents/skills/koubo-roughcut.
    """
    skill_root = Path(__file__).resolve().parent.parent
    container = skill_root.parent
    if container.name == "skills" and container.parent.name == ".agents":
        return container.parent.parent
    for candidate in (skill_root, *skill_root.parents):
        if (candidate / ".git").exists():
            return candidate
    return container


def next_item_id(prefix: str, items: list[dict]) -> str:
    best = 0
    for item in items:
        m = _ID_NUM.match(str(item.get("id") or ""))
        if m and m.group(1) == prefix:
            best = max(best, int(m.group(2)))
    return f"{prefix}{best + 1:03d}"


def _overlap(a: dict, b: dict) -> float:
    return min(float(a["sourceEnd"]), float(b["sourceEnd"])) - max(float(a["sourceStart"]), float(b["sourceStart"]))


def decisions_problems(data: dict) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors make the file unusable; warnings are worth a look."""
    errors: list[str] = []
    warnings: list[str] = []
    if int(data.get("version") or 0) != 1:
        return ["version must be 1"], warnings
    duration = float((data.get("source") or {}).get("duration") or 0)
    keep = data.get("keep") or []
    drop = data.get("drop") or []
    if not keep:
        return ["keep is empty"], warnings
    prev_end = 0.0
    timeline = 0.0
    for i, item in enumerate(keep):
        try:
            ss, se = float(item["sourceStart"]), float(item["sourceEnd"])
            ts, te = float(item["timelineStart"]), float(item["timelineEnd"])
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"keep[{i}] missing or bad time field: {exc}")
            continue
        if se <= ss:
            errors.append(f"keep[{i}] sourceEnd <= sourceStart")
        if duration and se > duration + 0.05:
            errors.append(f"keep[{i}] sourceEnd past source duration")
        if abs(ts - timeline) > 0.02:
            errors.append(f"keep[{i}] timelineStart {ts} != expected {timeline:.3f}")
        if abs((te - ts) - (se - ss)) > 0.02:
            errors.append(f"keep[{i}] timeline span != source span")
        if ss < prev_end - 0.001:
            errors.append(f"keep[{i}] overlaps previous source range")
        if item.get("reason") not in REASONS:
            errors.append(f"keep[{i}] bad reason {item.get('reason')}")
        prev_end = se
        timeline = te
    for i, item in enumerate(drop):
        try:
            ds, de = float(item["sourceStart"]), float(item["sourceEnd"])
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"drop[{i}] missing or bad time field: {exc}")
            continue
        if item.get("reason") not in REASONS:
            errors.append(f"drop[{i}] bad reason {item.get('reason')}")
        if de <= ds:
            errors.append(f"drop[{i}] empty range")
    if errors:
        return errors, warnings
    for k in keep:
        for d in drop:
            if _overlap(k, d) > OVERLAP_TOL:
                errors.append(
                    f"{k.get('id') or 'keep'} and {d.get('id') or 'drop'} overlap: "
                    f"{max(float(k['sourceStart']), float(d['sourceStart'])):.3f}s is both kept and dropped"
                )
    ordered = sorted(drop, key=lambda x: float(x["sourceStart"]))
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if float(b["sourceStart"]) >= float(a["sourceEnd"]):
                break
            if _overlap(a, b) > OVERLAP_TOL:
                warnings.append(f"{a.get('id') or 'drop'} and {b.get('id') or 'drop'} overlap")
    return errors, warnings


def recompute_timeline(data: dict) -> None:
    keep = sorted(data.get("keep") or [], key=lambda x: float(x["sourceStart"]))
    t = 0.0
    for item in keep:
        ss = float(item["sourceStart"])
        se = float(item["sourceEnd"])
        span = se - ss
        item["sourceStart"] = round(ss, 3)
        item["sourceEnd"] = round(se, 3)
        item["timelineStart"] = round(t, 3)
        item["timelineEnd"] = round(t + span, 3)
        t += span
    data["keep"] = keep


def gap_floor_before(source_start: float, keep: list[dict], drops: list[dict], index: int) -> float:
    """Earliest second we may extend into before source_start (never into drop or prev keep)."""
    floor = 0.0
    for drop in drops:
        ds = float(drop["sourceStart"])
        de = float(drop["sourceEnd"])
        if de <= source_start + 0.001:
            floor = max(floor, de)
        elif ds < source_start - 0.001 and de > source_start - 0.001:
            floor = max(floor, source_start)
    if index > 0:
        prev_end = float(keep[index - 1]["sourceEnd"])
        if prev_end <= source_start + 0.001:
            floor = max(floor, prev_end)
    return floor


def apply_lead_in(data: dict, *, lead_in: float | None = None) -> int:
    """Pull keep starts earlier into preceding gap only. Returns count of adjusted segments."""
    breath = data.get("breath") or {}
    pad = lead_in if lead_in is not None else float(breath.get("leadIn") or 0.12)
    if pad <= 0:
        return 0
    keep = sorted(data.get("keep") or [], key=lambda x: float(x["sourceStart"]))
    drops = data.get("drop") or []
    duration = float((data.get("source") or {}).get("duration") or 0)
    changed = 0
    for i, item in enumerate(keep):
        if item.get("reason") not in {"speech", "breath"}:
            continue
        ss = float(item["sourceStart"])
        floor = gap_floor_before(ss, keep, drops, i)
        new_ss = max(floor, ss - pad)
        if new_ss + 0.001 < ss:
            item["sourceStart"] = round(new_ss, 3)
            changed += 1
    data["keep"] = keep
    if changed:
        recompute_timeline(data)
    _ = duration  # reserved for future clamp
    return changed


def normalize_zh(text: str) -> str:
    return PUNCT_RE.sub("", text or "")


def text_in_range(words: list[tuple[float, float, str]], start: float, end: float) -> str:
    """Words whose midpoint falls in [start, end)."""
    parts: list[str] = []
    for ws, we, t in words:
        mid = (ws + we) / 2
        if start <= mid < end and t:
            parts.append(t)
    return "".join(parts)


def fill_decision_text(data: dict, words: list[tuple[float, float, str]]) -> int:
    """Write keep/drop text from the words actually in that span. Returns how many items changed."""
    changed = 0
    for item in data.get("keep") or []:
        ss, se = float(item["sourceStart"]), float(item["sourceEnd"])
        text = "" if item.get("reason") == "breath" else text_in_range(words, ss, se)
        if item.get("text") != text:
            item["text"] = text
            changed += 1
    for item in data.get("drop") or []:
        ss, se = float(item["sourceStart"]), float(item["sourceEnd"])
        text = text_in_range(words, ss, se)
        if item.get("text") != text:
            item["text"] = text
            changed += 1
    return changed


def keep_has_words(item: dict, words: list[tuple[float, float, str]]) -> bool:
    ss, se = float(item["sourceStart"]), float(item["sourceEnd"])
    for ws, we, t in words:
        if not t:
            continue
        mid = (ws + we) / 2
        if ss <= mid < se:
            return True
    return False


def _abuts(a_ss: float, a_se: float, b_ss: float, b_se: float) -> bool:
    return a_se + TOUCH_TOL >= b_ss and b_se + TOUCH_TOL >= a_ss


def prune_orphaned_empty_keeps(data: dict, words: list[tuple[float, float, str]]) -> int:
    """Drop empty/breath keeps that no longer touch remaining speech.

    After a retake or stutter cut, compress_breath's 0.4s inhale in front of
    the deleted words is still a keep. Those leftovers should go; an inhale
    that still sits on a kept word stays.
    """
    keep = sorted(data.get("keep") or [], key=lambda x: float(x["sourceStart"]))
    spoken = [k for k in keep if keep_has_words(k, words)]
    drops = list(data.get("drop") or [])
    kept: list[dict] = []
    removed = 0
    for item in keep:
        if keep_has_words(item, words):
            kept.append(item)
            continue
        ss, se = float(item["sourceStart"]), float(item["sourceEnd"])
        if any(_abuts(ss, se, float(s["sourceStart"]), float(s["sourceEnd"])) for s in spoken):
            item["reason"] = "breath"
            item["text"] = ""
            kept.append(item)
            continue
        drops.append(
            {
                "id": next_item_id("d", drops),
                "sourceStart": round(ss, 3),
                "sourceEnd": round(se, 3),
                "text": "",
                "reason": "long-pause",
            }
        )
        removed += 1
    data["keep"] = kept
    data["drop"] = drops
    if removed:
        recompute_timeline(data)
    return removed
