#!/usr/bin/env python3
"""Merge Jev retake_judgments into cut_decisions.json. --dry-run previews timeline only."""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

from decisions_common import recompute_timeline

EPS = 0.025
ID_NUM = re.compile(r"^([kdf])(\d+)$")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def timeline_end(data: dict) -> float:
    keep = data.get("keep") or []
    if not keep:
        return 0.0
    return float(max(float(k["timelineEnd"]) for k in keep))


def next_id(prefix: str, items: list[dict]) -> str:
    best = 0
    for item in items:
        raw = str(item.get("id") or "")
        m = ID_NUM.match(raw)
        if m and m.group(1) == prefix:
            best = max(best, int(m.group(2)))
    return f"{prefix}{best + 1:03d}"


def range_covered_by_drops(ds: float, de: float, drops: list[dict]) -> bool:
    for drop in drops:
        d0 = float(drop["sourceStart"])
        d1 = float(drop["sourceEnd"])
        if d0 <= ds + EPS and d1 >= de - EPS:
            return True
    return False


def overlaps_drop(ds: float, de: float, drops: list[dict]) -> bool:
    for drop in drops:
        d0 = float(drop["sourceStart"])
        d1 = float(drop["sourceEnd"])
        if de <= d0 + EPS or ds >= d1 - EPS:
            continue
        return True
    return False


def covering_keeps(ds: float, de: float, keep: list[dict]) -> list[dict] | None:
    """Keeps that together cover [ds, de] without a hole. Breath splits leave touching keeps."""
    hits = [
        k for k in sorted(keep, key=lambda x: float(x["sourceStart"]))
        if float(k["sourceEnd"]) > ds + EPS and float(k["sourceStart"]) < de - EPS
    ]
    if not hits:
        return None
    if float(hits[0]["sourceStart"]) - EPS > ds or float(hits[-1]["sourceEnd"]) + EPS < de:
        return None
    for prev, nxt in zip(hits, hits[1:]):
        if float(nxt["sourceStart"]) > float(prev["sourceEnd"]) + 0.001:
            return None
    return hits


def drop_reason_for_kind(kind: str) -> str:
    if kind in {"intra_clause", "bridge_clause"}:
        return "stutter"
    if kind == "cross_segment":
        return "retake"
    return "stutter"


def split_keep(item: dict, ds: float, de: float) -> list[dict]:
    ss = float(item["sourceStart"])
    se = float(item["sourceEnd"])
    pieces: list[dict] = []
    if ds > ss + EPS:
        left = copy.deepcopy(item)
        left["sourceEnd"] = round(ds, 3)
        pieces.append(left)
    if de < se - EPS:
        right = copy.deepcopy(item)
        right["sourceStart"] = round(de, 3)
        pieces.append(right)
    return pieces


def apply_proposed_drop(
    data: dict,
    *,
    ds: float,
    de: float,
    text: str,
    reason: str,
    kept_instead: str | None,
    jev_id: str,
) -> tuple[str, str | None]:
    """Return status, detail. status: applied | skipped."""
    if de <= ds + EPS:
        return "skipped", "empty range"
    drops = data.get("drop") or []
    if range_covered_by_drops(ds, de, drops):
        return "skipped", "already fully in drop"
    if overlaps_drop(ds, de, drops) and not range_covered_by_drops(ds, de, drops):
        return "skipped", "partly overlaps existing drop"
    keep = sorted(data.get("keep") or [], key=lambda x: float(x["sourceStart"]))
    hits = covering_keeps(ds, de, keep)
    if not hits:
        return "skipped", "range not inside any keep"
    ds = max(ds, float(hits[0]["sourceStart"]))
    de = min(de, float(hits[-1]["sourceEnd"]))
    drop_entry = {
        "id": next_id("d", drops),
        "sourceStart": round(ds, 3),
        "sourceEnd": round(de, 3),
        "text": text,
        "reason": reason,
        "jevRef": jev_id,
    }
    if kept_instead:
        drop_entry["keptInstead"] = kept_instead

    rest = [k for k in keep if not any(k is h for h in hits)]
    for item in hits:
        pieces = split_keep(item, ds, de)
        for n, piece in enumerate(pieces):
            piece["id"] = item.get("id") if n == 0 else next_id("k", rest)
            rest.append(piece)
    data["keep"] = rest
    drops.append(drop_entry)
    data["drop"] = drops
    ids = ", ".join(f"`{h.get('id')}`" for h in hits)
    recompute_timeline(data)
    return "applied", f"removed {de - ds:.3f}s from keep {ids}"


def resolve_kept_keep_id(cand: dict, panel_action: str, keep: list[dict]) -> str | None:
    if panel_action == "drop_earlier":
        probe = cand.get("later") or {}
    elif panel_action == "drop_later":
        probe = cand.get("earlier") or {}
    else:
        return None
    ps = float(probe.get("sourceStart") or 0)
    pe = float(probe.get("sourceEnd") or 0)
    mid = (ps + pe) / 2
    for item in keep:
        if float(item["sourceStart"]) - EPS <= mid <= float(item["sourceEnd"]) + EPS:
            return str(item.get("id"))
    return None


def add_flag(data: dict, judgment: dict) -> tuple[str, str]:
    flags = data.get("flags") or []
    cand = judgment.get("candidate") or {}
    earlier = cand.get("earlier") or {}
    entry = {
        "id": next_id("f", flags),
        "sourceStart": round(float(earlier.get("sourceStart") or 0), 3),
        "reason": "uncertain",
        "note": f"Jev {judgment.get('id')} {judgment.get('candidateId')}: {judgment.get('note', '')}",
    }
    flags.append(entry)
    data["flags"] = flags
    return "flagged", "added to flags"


def merge_judgments(data: dict, judgments: list[dict]) -> list[dict]:
    """Apply judgments in source-time order (end first avoids index drift). Returns change log."""
    log: list[dict] = []
    actionable = []
    for j in judgments:
        action = j.get("panelAction")
        if action in {"drop_earlier", "drop_later"}:
            pd = j.get("proposedDrop")
            if pd:
                actionable.append(j)
    actionable.sort(
        key=lambda j: float((j.get("proposedDrop") or {}).get("sourceStart") or 0),
        reverse=True,
    )
    for j in judgments:
        action = j.get("panelAction")
        if action == "flag":
            status, detail = add_flag(data, j)
            log.append(_log_row(j, status, detail, 0.0))
        elif action == "keep_both":
            log.append(_log_row(j, "skipped", "keep_both — no change", 0.0))
    for j in actionable:
        action = j.get("panelAction")
        pd = j.get("proposedDrop") or {}
        ds = float(pd["sourceStart"])
        de = float(pd["sourceEnd"])
        text = str(pd.get("text") or "")
        cand = j.get("candidate") or {}
        reason = drop_reason_for_kind(str(cand.get("kind") or ""))
        t_before = timeline_end(data)
        kept_id = resolve_kept_keep_id(cand, action, data.get("keep") or [])
        status, detail = apply_proposed_drop(
            data,
            ds=ds,
            de=de,
            text=text,
            reason=reason,
            kept_instead=kept_id,
            jev_id=str(j.get("id") or ""),
        )
        t_after = timeline_end(data)
        delta = round(t_before - t_after, 3) if status == "applied" else 0.0
        log.append(_log_row(j, status, detail, delta))
    return log


def _log_row(j: dict, status: str, detail: str, timeline_delta: float) -> dict:
    pd = j.get("proposedDrop") or {}
    cand = j.get("candidate") or {}
    return {
        "judgmentId": j.get("id"),
        "candidateId": j.get("candidateId"),
        "panelAction": j.get("panelAction"),
        "kind": cand.get("kind"),
        "sourceStart": pd.get("sourceStart"),
        "sourceEnd": pd.get("sourceEnd"),
        "dropText": (pd.get("text") or "")[:80],
        "status": status,
        "detail": detail,
        "timelineDeltaSec": timeline_delta,
    }


def fmt_ms(seconds: float) -> str:
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:04.1f}"


def render_preview_md(
    *,
    decisions_path: Path,
    judgments_path: Path,
    before: dict,
    after: dict,
    log: list[dict],
    applied_to: Path | None = None,
) -> str:
    t0 = timeline_end(before)
    t1 = timeline_end(after)
    applied = [r for r in log if r["status"] == "applied"]
    skipped = [r for r in log if r["status"] == "skipped"]
    flagged = [r for r in log if r["status"] == "flagged"]
    lines = [
        "# merge_retake_judgments · dry-run",
        "",
        f"- decisions: `{decisions_path}`",
        f"- judgments: `{judgments_path}`",
        "",
        "## Timeline",
        "",
        f"| | keep 段数 | drop 段数 | 成片时长 |",
        f"|---|---:|---:|---:|",
        f"| 合并前 | {len(before.get('keep') or [])} | {len(before.get('drop') or [])} | **{t0:.3f}s** |",
        f"| 合并后（预览） | {len(after.get('keep') or [])} | {len(after.get('drop') or [])} | **{t1:.3f}s** |",
        f"| 变化 | | | **−{t0 - t1:.3f}s** |",
        "",
        f"应用 drop：**{len(applied)}** · 跳过：**{len(skipped)}** · 新增 flag：**{len(flagged)}**",
        "",
    ]
    if applied:
        lines.extend(
            [
                "## 会执行的 drop",
                "",
                "| Jev | 候选 | 源片 | Δ timeline | 说明 |",
                "|---|---|---|---:|---|",
            ]
        )
        for r in applied:
            ss = float(r["sourceStart"] or 0)
            lines.append(
                f"| `{r['judgmentId']}` | `{r['candidateId']}` | {fmt_ms(ss)} | "
                f"{r['timelineDeltaSec']:.3f}s | {r['detail'][:48]} |"
            )
        lines.append("")
    if skipped:
        lines.extend(
            [
                "## 跳过",
                "",
                "| Jev | 候选 | action | 原因 |",
                "|---|---|---|---|",
            ]
        )
        for r in skipped:
            lines.append(
                f"| `{r['judgmentId']}` | `{r['candidateId']}` | {r['panelAction']} | {r['detail']} |"
            )
        lines.append("")
    if flagged:
        lines.extend(["## 新增 flags", ""])
        for r in flagged:
            lines.append(f"- `{r['judgmentId']}` {r['candidateId']}: {r['detail']}")
        lines.append("")
    if applied_to:
        lines.append(f"已写入 `{applied_to}`。")
    else:
        lines.append("未写入 `cut_decisions.json`。确认后加 `--apply`。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", type=Path, required=True, help="base cut_decisions.json")
    parser.add_argument("--judgments", type=Path, required=True, help="retake_judgments.json")
    parser.add_argument("--output", type=Path, help="dry-run report (.md); default judgments dir")
    parser.add_argument("--dry-run", action="store_true", help="preview only (default)")
    parser.add_argument("--apply", action="store_true", help="write merged cut_decisions.json")
    parser.add_argument(
        "--write-out",
        type=Path,
        help="output cut_decisions path for --apply (default: run dir next to judgments)",
    )
    args = parser.parse_args()
    if args.apply and args.dry_run:
        raise SystemExit("use either --dry-run or --apply, not both")
    dry_run = not args.apply
    before = load_json(args.decisions)
    jdata = load_json(args.judgments)
    judgments = jdata.get("judgments") or []
    after = copy.deepcopy(before)
    log = merge_judgments(after, judgments)
    out_base = args.output or args.judgments.with_name("merge_retake_preview")
    if out_base.suffix != ".md":
        md_path = out_base.with_suffix(".md")
        json_path = out_base.with_suffix(".json")
    else:
        md_path = out_base
        json_path = out_base.with_suffix(".json")
    write_out = args.write_out or (args.judgments.parent / "cut_decisions.json")
    if args.apply:
        write_out.parent.mkdir(parents=True, exist_ok=True)
        write_out.write_text(json.dumps(after, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(write_out, file=sys.stderr)

    preview = {
        "dryRun": dry_run,
        "appliedTo": str(write_out) if args.apply else None,
        "decisions": str(args.decisions),
        "judgments": str(args.judgments),
        "timelineBeforeSec": round(timeline_end(before), 3),
        "timelineAfterSec": round(timeline_end(after), 3),
        "timelineDeltaSec": round(timeline_end(before) - timeline_end(after), 3),
        "changes": log,
        "keepCountBefore": len(before.get("keep") or []),
        "keepCountAfter": len(after.get("keep") or []),
        "dropCountBefore": len(before.get("drop") or []),
        "dropCountAfter": len(after.get("drop") or []),
    }
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(
        render_preview_md(
            decisions_path=args.decisions,
            judgments_path=args.judgments,
            before=before,
            after=after,
            log=log,
            applied_to=write_out if args.apply else None,
        ),
        encoding="utf-8",
    )
    json_path.write_text(json.dumps(preview, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(md_path)
    print(json_path)
    print(
        f"timeline {preview['timelineBeforeSec']:.3f}s → {preview['timelineAfterSec']:.3f}s "
        f"(−{preview['timelineDeltaSec']:.3f}s)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
