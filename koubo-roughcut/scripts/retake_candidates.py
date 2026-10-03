#!/usr/bin/env python3
"""Enumerate retake candidate pairs from transcript (SKILL step 5). Mechanical only."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from decisions_common import normalize_zh

CLAUSE_SPLIT = re.compile(r"([，。！？；])")


def load_segment_words(seg: dict) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    for w in seg.get("words") or []:
        ws, we = float(w["start"]), float(w["end"])
        t = str(w.get("text") or w.get("word") or "").strip()
        if t and we > ws:
            out.append((ws, we, t))
    if not out and seg.get("text"):
        out.append((float(seg["start"]), float(seg["end"]), str(seg["text"])))
    return out


def split_clauses(words: list[tuple[float, float, str]]) -> list[dict]:
    """Split segment words into clauses with source time spans."""
    if not words:
        return []
    parts: list[tuple[str, float, float]] = []
    buf: list[str] = []
    cs, ce = words[0][0], words[0][1]
    for ws, we, t in words:
        if not buf:
            cs = ws
        buf.append(t)
        ce = we
        if t and t[-1] in "，。！？；":
            parts.append(("".join(buf), cs, ce))
            buf = []
    if buf:
        parts.append(("".join(buf), cs, ce))
    clauses: list[dict] = []
    for i, (text, ss, se) in enumerate(parts):
        norm = normalize_zh(text).strip('"“”‘’')
        if len(norm) < 3:
            continue
        clauses.append(
            {
                "index": i,
                "text": text.strip(),
                "norm": norm,
                "sourceStart": round(ss, 3),
                "sourceEnd": round(se, 3),
            }
        )
    return clauses


def shared_prefix_len(a: str, b: str) -> int:
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n


def bigram_jaccard(a: str, b: str) -> float:
    if len(a) < 2 or len(b) < 2:
        return 1.0 if a == b else 0.0
    ba = {a[i : i + 2] for i in range(len(a) - 1)}
    bb = {b[i : i + 2] for i in range(len(b) - 1)}
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def score_clause_pair(a: dict, b: dict) -> tuple[int, str] | None:
    """Return (score, sharedLabel) if a,b look like failed take + retake."""
    na, nb = a["norm"], b["norm"]
    if len(na) < 2 or len(nb) < 2:
        return None
    pf = shared_prefix_len(na, nb)
    if pf >= 5:
        if len(nb) <= len(na) and pf < len(na) - 1:
            return None
        return pf * 10 + (len(nb) - len(na)), na[:pf]
    if na == nb:
        return 120 + len(na), na
    # Short failed fragment then longer restart (e.g. 项目代号，→ 项目一行代码…)
    if len(na) <= 8 and na[:2] == nb[:2] and len(nb) >= len(na) + 3:
        return 88 + len(nb), na[:2]
    j = bigram_jaccard(na, nb)
    if len(na) >= 6 and len(nb) >= 6 and pf >= 2 and j >= 0.52:
        return int(95 + j * 30), f"~{j:.2f}"
    # Same anchors, word order shuffle (项目一行代码没写 ↔ 项目代码一行没写)
    if len(na) >= 8 and len(nb) >= 8 and na[:2] == nb[:2] and na[-2:] == nb[-2:]:
        anchors = ("代码", "一行", "没写")
        if all(x in na and x in nb for x in anchors) and j >= 0.35:
            return int(110 + j * 25), "shuffle"
    return None


def _candidate_from_clauses(
    a: dict,
    b: dict,
    *,
    kind: str,
    score: int,
    shared: str,
    extra: dict | None = None,
) -> dict:
    row = {
        "kind": kind,
        "score": score,
        "sharedPrefix": shared,
        "earlier": {
            "text": a["text"],
            "sourceStart": a["sourceStart"],
            "sourceEnd": a["sourceEnd"],
        },
        "later": {
            "text": b["text"],
            "sourceStart": b["sourceStart"],
            "sourceEnd": b["sourceEnd"],
        },
    }
    if extra:
        row.update(extra)
    return row


def intra_clause_candidates(seg: dict, seg_index: int) -> list[dict]:
    words = load_segment_words(seg)
    clauses = split_clauses(words)
    out: list[dict] = []
    for i in range(len(clauses)):
        for j in range(i + 1, len(clauses)):
            a, b = clauses[i], clauses[j]
            hit = score_clause_pair(a, b)
            if not hit:
                continue
            score, shared = hit
            if float(seg.get("start") or 0) < 120:
                score += 80
            out.append(
                _candidate_from_clauses(
                    a,
                    b,
                    kind="intra_clause",
                    score=score,
                    shared=shared,
                    extra={
                        "segmentIndex": seg_index,
                        "segmentStart": float(seg["start"]),
                    },
                )
            )
    return out


def bridge_clause_candidates(segs: list[dict]) -> list[dict]:
    """Last clause(s) of segment N vs first clause(s) of segment N+1."""
    out: list[dict] = []
    for i in range(len(segs) - 1):
        a_seg, b_seg = segs[i], segs[i + 1]
        gap = float(b_seg["start"]) - float(a_seg["end"])
        if gap > 180:
            continue
        ca = split_clauses(load_segment_words(a_seg))
        cb = split_clauses(load_segment_words(b_seg))
        if not ca or not cb:
            continue
        tail = ca[-2:]
        head = cb[:2]
        for a in tail:
            for b in head:
                hit = score_clause_pair(a, b)
                if not hit:
                    continue
                score, shared = hit
                score += max(0, 50 - int(gap * 10))
                out.append(
                    _candidate_from_clauses(
                        a,
                        b,
                        kind="bridge_clause",
                        score=score,
                        shared=shared,
                        extra={
                            "segmentIndexA": i,
                            "segmentIndexB": i + 1,
                            "gapSeconds": round(gap, 3),
                        },
                    )
                )
    return out


def cross_segment_candidates(segs: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i in range(len(segs) - 1):
        a, b = segs[i], segs[i + 1]
        gap = float(b["start"]) - float(a["end"])
        if gap > 180:
            continue
        ta = str(a.get("text") or "")
        tb = str(b.get("text") or "")
        na, nb = normalize_zh(ta), normalize_zh(tb)
        if len(na) < 8 or len(nb) < 8:
            continue
        pf = shared_prefix_len(na[:24], nb[:24])
        if pf < 8:
            # overlap: earlier opening repeats inside later (failed start + full retake)
            head = na[:12]
            if len(head) >= 8 and nb.find(head, 0, min(40, len(nb))) == 0:
                pf = len(head)
            else:
                continue
        score = pf * 5 + max(0, 60 - gap) + min(len(nb), 120) // 10
        out.append(
            {
                "kind": "cross_segment",
                "score": score,
                "segmentIndexA": i,
                "segmentIndexB": i + 1,
                "gapSeconds": round(gap, 3),
                "sharedPrefix": na[:pf],
                "earlier": {
                    "text": ta,
                    "sourceStart": round(float(a["start"]), 3),
                    "sourceEnd": round(float(a["end"]), 3),
                },
                "later": {
                    "text": tb,
                    "sourceStart": round(float(b["start"]), 3),
                    "sourceEnd": round(float(b["end"]), 3),
                },
            }
        )
    return out


def dedupe_candidates(raw: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    uniq: list[dict] = []
    for c in sorted(raw, key=lambda x: -x["score"]):
        key = (
            c["kind"],
            round(c["earlier"]["sourceStart"], 2),
            round(c["later"]["sourceStart"], 2),
        )
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top", type=int, default=0, help="keep top N by score; 0 = all")
    args = parser.parse_args()
    data = json.loads(args.transcript.read_text(encoding="utf-8"))
    segs = data.get("segments") or []
    raw: list[dict] = []
    for si, seg in enumerate(segs):
        raw.extend(intra_clause_candidates(seg, si))
    raw.extend(bridge_clause_candidates(segs))
    raw.extend(cross_segment_candidates(segs))
    candidates = dedupe_candidates(raw)
    if args.top > 0:
        candidates = candidates[: args.top]
    for i, c in enumerate(candidates, start=1):
        c["id"] = f"c{i:03d}"
    payload = {
        "version": 1,
        "transcript": str(args.transcript),
        "candidateCount": len(candidates),
        "candidates": candidates,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"candidates={len(candidates)} (from {len(raw)} raw)", file=__import__("sys").stderr)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
