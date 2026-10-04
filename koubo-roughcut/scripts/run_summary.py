"""run_summary.json: one page of numbers for a run. Written after pipeline; review save fills review."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def drop_reason_counts(data: dict) -> dict[str, int]:
    counts = Counter(str(d.get("reason") or "unknown") for d in data.get("drop") or [])
    return dict(sorted(counts.items()))


def timeline_end(data: dict) -> float:
    keep = data.get("keep") or []
    if not keep:
        return 0.0
    return max(float(k.get("timelineEnd") or 0) for k in keep)


def build_run_summary(
    *,
    run_dir: Path,
    before_sec: float,
    after: dict,
    coverage_missing: int | None,
    residue: dict[str, int],
) -> dict[str, Any]:
    source = after.get("source") or {}
    duration = float(source.get("duration") or 0)
    after_sec = timeline_end(after)
    flags = after.get("flags") or []
    cut_ratio = round(1 - after_sec / duration, 3) if duration else None
    return {
        "version": 1,
        "generatedAt": _iso_now(),
        "runDir": str(run_dir),
        "script": after.get("script"),
        "source": source,
        "pipeline": {
            "beforeSec": round(before_sec, 3),
            "afterSec": round(after_sec, 3),
            "sourceSec": round(duration, 3),
            "cutRatio": cut_ratio,
            "keep": len(after.get("keep") or []),
            "drop": len(after.get("drop") or []),
            "flags": len(flags),
            "flagsUnheard": sum(1 for f in flags if not f.get("heard")),
            "dropByReason": drop_reason_counts(after),
        },
        "coverage": {"missing": coverage_missing},
        "residue": residue,
        "review": None,
    }


def write_run_summary(run_dir: Path, payload: dict) -> Path:
    out = run_dir / "run_summary.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def merge_review_into_summary(run_dir: Path, review_log: dict) -> Path | None:
    path = run_dir / "run_summary.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    else:
        data = {"version": 1, "runDir": str(run_dir)}
    stats = review_log.get("stats") or {}
    flags = review_log.get("flags") or {}
    data["review"] = {
        "savedAt": (review_log.get("session") or {}).get("savedAt"),
        "dropsRestoredCount": stats.get("dropsRestoredCount", 0),
        "dropsAddedCount": stats.get("dropsAddedCount", 0),
        "timelineDeltaSec": stats.get("timelineDeltaSec", 0),
        "flagsUnheard": len(flags.get("unheard") or []),
    }
    data["generatedAt"] = _iso_now()
    return write_run_summary(run_dir, data)
