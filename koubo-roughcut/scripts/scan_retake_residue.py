#!/usr/bin/env python3
"""Scan kept transcript for likely retake residue and suspicious drops. Output for model/human pass."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from decisions_common import normalize_zh

CLAUSE_SPLIT = re.compile(r"[，。！？；]")


def load_words(transcript: dict) -> list[tuple[float, float, str]]:
    words: list[tuple[float, float, str]] = []
    for seg in transcript.get("segments") or []:
        for w in seg.get("words") or []:
            ws, we = float(w["start"]), float(w["end"])
            t = str(w.get("text") or w.get("word") or "").strip()
            if not t or we <= ws:
                continue
            words.append((ws, we, t))
    words.sort(key=lambda x: x[0])
    return words


def words_in_range(words: list[tuple[float, float, str]], ss: float, se: float) -> list[tuple[float, float, str]]:
    return [(ws, we, t) for ws, we, t in words if we > ss and ws < se]


def next_keep_after(keep: list[dict], source_end: float) -> dict | None:
    later = [k for k in keep if float(k["sourceStart"]) >= source_end - 0.05]
    if not later:
        return None
    return min(later, key=lambda k: float(k["sourceStart"]))


def keep_ending_near(keep: list[dict], t: float, slop: float = 0.5) -> dict | None:
    hits = [k for k in keep if abs(float(k["sourceEnd"]) - t) <= slop]
    return max(hits, key=lambda k: float(k["sourceEnd"])) if hits else None


def keep_starting_near(keep: list[dict], t: float, slop: float = 0.5) -> dict | None:
    hits = [k for k in keep if abs(float(k["sourceStart"]) - t) <= slop]
    return min(hits, key=lambda k: float(k["sourceStart"])) if hits else None


def common_prefix_len(a: str, b: str) -> int:
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n


def restated_tail(norm: str) -> str | None:
    """If the drop restates its own opening, return the last copy plus what follows."""
    if len(norm) < 12:
        return None
    max_len = min(20, len(norm) // 2)
    for length in range(max_len, 5, -1):
        phrase = norm[:length]
        j = norm.find(phrase, length)
        if j != -1:
            return norm[j:]
    return None


def drop_is_failed_opening(dropped: str, kept: str) -> bool:
    """True when drop is a messier / shorter take of the kept sentence."""
    d, k = normalize_zh(dropped), normalize_zh(kept)
    if len(d) < 4 or not k:
        return False
    if d in k or (len(k) >= 8 and k[:12] in d):
        return True
    n = common_prefix_len(d, k)
    if n >= 8:
        return True
    if n >= 4 and d[n:] and d[n:] in k:
        return True
    if n >= 4 and len(d) <= 12:
        return True
    tail = restated_tail(d)
    if tail and (tail in k or k.startswith(tail[:8])):
        return True
    for size in range(min(12, len(d)), 4, -1):
        if d[-size:] in k[: max(24, size + 4)]:
            return True
    return False


def keep_text(item: dict, words: list[tuple[float, float, str]]) -> str:
    return "".join(t for _, _, t in words_in_range(words, float(item["sourceStart"]), float(item["sourceEnd"])))


def drop_covered_by_next_keep(
    drop: dict,
    keep: list[dict],
    words: list[tuple[float, float, str]],
) -> bool:
    """True when the drop is a failed opening already present in a later keep."""
    ds, de = float(drop["sourceStart"]), float(drop["sourceEnd"])
    dropped = normalize_zh("".join(t for _, _, t in words_in_range(words, ds, de)) or str(drop.get("text") or ""))
    if len(dropped) < 4:
        return False
    adjacent = keep_starting_near(keep, de)
    later = next_keep_after(keep, de)
    for nxt in (adjacent, later):
        if nxt is None:
            continue
        if drop_is_failed_opening(dropped, keep_text(nxt, words)):
            return True
    tail = restated_tail(dropped)
    if tail and len(tail) >= 8:
        for item in keep:
            if item.get("reason") != "speech":
                continue
            if float(item["sourceStart"]) < de - 0.05:
                continue
            if tail in normalize_zh(keep_text(item, words)):
                return True
    return False


def fmt_ms(seconds: float) -> str:
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:04.1f}"


def find_intra_repeats(text: str, min_chars: int = 8, min_gap: int = 10) -> list[tuple[str, int, int]]:
    """Return (phrase, start_idx, second_idx) in normalized text."""
    norm = normalize_zh(text)
    if len(norm) < min_chars * 2 + min_gap:
        return []
    hits: list[tuple[str, int, int, int]] = []
    max_len = min(20, len(norm) // 2)
    for length in range(max_len, min_chars - 1, -1):
        for i in range(0, len(norm) - length - min_gap + 1):
            phrase = norm[i : i + length]
            j = norm.find(phrase, i + length + min_gap)
            if j == -1:
                continue
            hits.append((phrase, i, j, length))
    if not hits:
        return []
    hits.sort(key=lambda x: (-x[3], x[1]))
    chosen: list[tuple[str, int, int]] = []
    used_ranges: list[tuple[int, int]] = []
    for phrase, i, j, _ in hits:
        span = (i, j + len(phrase))
        if any(not (span[1] <= a or span[0] >= b) for a, b in used_ranges):
            continue
        chosen.append((phrase, i, j))
        used_ranges.append(span)
        if len(chosen) >= 3:
            break
    return chosen


def find_clause_prefix_repeats(text: str, min_prefix: int = 5) -> list[str]:
    parts = [normalize_zh(p) for p in CLAUSE_SPLIT.split(text) if len(normalize_zh(p)) >= min_prefix]
    out: list[str] = []
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            prefix = 0
            for ca, cb in zip(a, b):
                if ca == cb:
                    prefix += 1
                else:
                    break
            if prefix >= min_prefix and (len(a) >= min_prefix and len(b) >= min_prefix):
                out.append(a[:prefix] if len(a) <= len(b) else b[:prefix])
    return out[:3]


def find_adjacent_overlap(prev_text: str, next_text: str, min_chars: int = 5) -> str | None:
    a = normalize_zh(prev_text)
    b = normalize_zh(next_text)
    if not a or not b:
        return None
    for n in range(min(16, len(a), len(b)), min_chars - 1, -1):
        if a[-n:] == b[:n]:
            return a[-n:]
    return None


def char_time_map(words: list[tuple[float, float, str]]) -> tuple[str, list[float]]:
    chars: list[str] = []
    times: list[float] = []
    for ws, we, t in words:
        for ch in normalize_zh(t):
            chars.append(ch)
            times.append(ws)
    return "".join(chars), times


def time_at_char_index(times: list[float], idx: int) -> float:
    if not times:
        return 0.0
    idx = max(0, min(idx, len(times) - 1))
    return times[idx]


def scan_keeps(
    keep: list[dict],
    words: list[tuple[float, float, str]],
) -> list[dict]:
    findings: list[dict] = []
    speech_keeps = [k for k in sorted(keep, key=lambda x: float(x["sourceStart"])) if k.get("reason") == "speech"]
    for item in speech_keeps:
        ss, se = float(item["sourceStart"]), float(item["sourceEnd"])
        if se - ss < 1.5:
            continue
        wds = words_in_range(words, ss, se)
        if not wds:
            continue
        text = "".join(t for _, _, t in wds)
        norm, times = char_time_map(wds)
        for phrase, i, j in find_intra_repeats(text):
            t0 = time_at_char_index(times, i)
            t1 = time_at_char_index(times, j)
            findings.append(
                {
                    "kind": "intra-repeat",
                    "keepId": item.get("id"),
                    "sourceStart": round(ss + t0, 3),
                    "sourceEnd": round(ss + t1 + len(phrase) * 0.08, 3),
                    "phrase": phrase,
                    "snippet": text[max(0, i - 4) : min(len(text), j + len(phrase) + 8)],
                }
            )
        for prefix in find_clause_prefix_repeats(text):
            findings.append(
                {
                    "kind": "intra-clause-repeat",
                    "keepId": item.get("id"),
                    "sourceStart": round(ss, 3),
                    "phrase": prefix,
                    "snippet": prefix,
                }
            )
    for item in speech_keeps:
        ss, se = float(item["sourceStart"]), float(item["sourceEnd"])
        if se - ss >= 1.5:
            continue
        snippet = str(item.get("text") or "")
        if not snippet:
            wds = words_in_range(words, ss, se)
            snippet = "".join(t for _, _, t in wds)
        for prefix in find_clause_prefix_repeats(snippet):
            findings.append(
                {
                    "kind": "intra-clause-repeat",
                    "keepId": item.get("id"),
                    "sourceStart": round(ss, 3),
                    "phrase": prefix,
                    "snippet": prefix,
                }
            )
    for a, b in zip(speech_keeps, speech_keeps[1:]):
        wds_a = words_in_range(words, float(a["sourceStart"]), float(a["sourceEnd"]))
        wds_b = words_in_range(words, float(b["sourceStart"]), float(b["sourceEnd"]))
        ta = "".join(t for _, _, t in wds_a)
        tb = "".join(t for _, _, t in wds_b)
        overlap = find_adjacent_overlap(ta, tb)
        if overlap and len(overlap) >= 5:
            findings.append(
                {
                    "kind": "join-repeat",
                    "keepIds": [a.get("id"), b.get("id")],
                    "sourceStart": round(float(b["sourceStart"]), 3),
                    "overlap": overlap,
                    "snippet": ta[-20:] + "｜" + tb[:20],
                }
            )
    return findings


def scan_bridge_stutter_drops(
    keep: list[dict],
    drops: list[dict],
    words: list[tuple[float, float, str]],
) -> list[dict]:
    """Stutter drops sandwiched between keeps that may have split one sentence (e.g. 也不 | 不过是两年前 | 当时)."""
    out: list[dict] = []
    ks = sorted(keep, key=lambda x: float(x["sourceStart"]))
    for drop in drops:
        if drop.get("reason") != "stutter":
            continue
        ds, de = float(drop["sourceStart"]), float(drop["sourceEnd"])
        wds = words_in_range(words, ds, de)
        clause = "".join(t for _, _, t in wds) or str(drop.get("text") or "")
        cn = normalize_zh(clause)
        if len(cn) < 6:
            continue
        prev_k = keep_ending_near(ks, ds)
        next_k = keep_starting_near(ks, de)
        if not prev_k or not next_k:
            continue
        prev_t = keep_text(prev_k, words)
        next_t = keep_text(next_k, words)
        if cn in normalize_zh(prev_t) or cn in normalize_zh(next_t):
            continue
        if drop_is_failed_opening(cn, next_t) or drop_covered_by_next_keep(drop, keep, words):
            continue
        # Broken join: prev ends mid-phrase (no strong clause end) and drop is not a pure duplicate tail.
        prev_norm = normalize_zh(prev_t)
        if prev_norm and prev_norm[-1] in "。！？":
            continue
        out.append(
            {
                "kind": "bridge-stutter",
                "dropId": drop.get("id"),
                "sourceStart": round(ds, 3),
                "sourceEnd": round(de, 3),
                "clause": clause.strip(),
                "prevKeep": prev_k.get("id"),
                "nextKeep": next_k.get("id"),
                "snippet": f"{prev_t[-12:]}｜{clause}｜{next_t[:12]}",
            }
        )
    return out


def scan_suspicious_drops(
    keep: list[dict],
    drops: list[dict],
    words: list[tuple[float, float, str]],
) -> list[dict]:
    kept_norm = normalize_zh(
        "".join(
            t
            for k in keep
            if k.get("reason") == "speech"
            for _, _, t in words_in_range(words, float(k["sourceStart"]), float(k["sourceEnd"]))
        )
    )
    out: list[dict] = []
    for drop in drops:
        if drop.get("reason") not in {"retake", "stutter"}:
            continue
        ds, de = float(drop["sourceStart"]), float(drop["sourceEnd"])
        wds = words_in_range(words, ds, de)
        text = "".join(t for _, _, t in wds) or str(drop.get("text") or "")
        norm = normalize_zh(text)
        if len(norm) < 8:
            continue
        if norm in kept_norm:
            continue
        if drop_covered_by_next_keep(drop, keep, words):
            continue
        tail = restated_tail(norm)
        if tail and tail in kept_norm:
            continue
        # Short stutter fragments are usually intentional drops.
        if len(norm) < 12 and drop.get("reason") == "stutter":
            continue
        clauses = [c.strip() for c in CLAUSE_SPLIT.split(text) if len(normalize_zh(c)) >= 4]
        if not clauses:
            clauses = [text.strip()]
        for clause in clauses:
            cn = normalize_zh(clause)
            if len(cn) < 4:
                continue
            if cn in kept_norm:
                continue
            out.append(
                {
                    "kind": "maybe-wrong-drop",
                    "dropId": drop.get("id"),
                    "sourceStart": round(ds, 3),
                    "sourceEnd": round(de, 3),
                    "reason": drop.get("reason"),
                    "clause": clause.strip(),
                    "keptInstead": drop.get("keptInstead"),
                }
            )
            break
    return out


def render_md(
    findings: list[dict],
    drop_flags: list[dict],
    bridge_flags: list[dict],
    run_label: str,
) -> str:
    lines = [
        f"# 残留重说审查 · {run_label}",
        "",
        "气口压缝后、审片前过一遍。模型或人据表改 `cut_decisions.json`，再 `validate_decisions.py`。",
        "",
    ]
    repeats = [f for f in findings if f["kind"] != "maybe-wrong-drop"]
    if repeats:
        lines.append("## 疑似残留重说（keep 内或接缝）")
        lines.append("")
        lines.append("| 源片时间 | 类型 | 片段 | 线索 |")
        lines.append("|---|---|---|---|")
        for f in repeats:
            if f["kind"] == "intra-repeat":
                lines.append(
                    f"| {fmt_ms(f['sourceStart'])} | keep 内重复 | `{f.get('keepId')}` | "
                    f"「{f['phrase']}」… {f.get('snippet', '')[:40]} |"
                )
            elif f["kind"] == "intra-clause-repeat":
                lines.append(
                    f"| {fmt_ms(f['sourceStart'])} | 分句重说 | `{f.get('keepId')}` | "
                    f"共享开头「{f['phrase']}…」 |"
                )
            else:
                lines.append(
                    f"| {fmt_ms(f['sourceStart'])} | 接缝重复 | `{f.get('keepIds')}` | "
                    f"重叠「{f['overlap']}」 |"
                )
        lines.append("")
    else:
        lines.append("## 疑似残留重说")
        lines.append("")
        lines.append("未扫到明显重复子串（仍建议听 `review.md` 抽检点）。")
        lines.append("")

    if bridge_flags:
        lines.append("## 疑似误删（stutter 切断了前后句）")
        lines.append("")
        lines.append("| 源片时间 | drop | 夹缝 | 内容 |")
        lines.append("|---|---|---|---|")
        for f in bridge_flags:
            lines.append(
                f"| {fmt_ms(f['sourceStart'])} | `{f.get('dropId')}` | "
                f"`{f.get('prevKeep')}`→`{f.get('nextKeep')}` | {f.get('clause', '')[:28]} |"
            )
        lines.append("")
        lines.append("常见于「也不｜不过是两年前｜当时」这类：应合并 keep 或删掉 drop。")
        lines.append("")

    if drop_flags:
        lines.append("## 疑似误删（drop 里像完整句，且 keep 未再出现）")
        lines.append("")
        lines.append("| 源片时间 | drop | 原因 | 内容 |")
        lines.append("|---|---|---|---|")
        for f in drop_flags:
            lines.append(
                f"| {fmt_ms(f['sourceStart'])} | `{f.get('dropId')}` | {f.get('reason')} | "
                f"{f.get('clause', '')[:36]} |"
            )
        lines.append("")
        lines.append("误删多为「整段重说」规则过头；核对脚本/听感后再决定恢复 keep 或改 drop。")
        lines.append("")

    lines.append("## 处理约定")
    lines.append("")
    lines.append("- **keep 内重复**：在 drop 补区间或收窄 keep，留最后一遍完整句。")
    lines.append("- **接缝重复**：修前一段 sourceEnd 或后一段 sourceStart。")
    lines.append("- **疑似误删**：优先恢复进 keep，或把 drop 改成 `flags` 让人听。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="retake_residue.md")
    args = parser.parse_args()
    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    data = json.loads(args.decisions.read_text(encoding="utf-8"))
    words = load_words(transcript)
    keep = data.get("keep") or []
    drops = data.get("drop") or []
    findings = scan_keeps(keep, words)
    drop_flags = scan_suspicious_drops(keep, drops, words)
    bridge_flags = scan_bridge_stutter_drops(keep, drops, words)
    label = args.decisions.parent.name
    md = render_md(findings, drop_flags, bridge_flags, label)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md, encoding="utf-8")
    print(
        f"repeats: {len(findings)}  bridge-stutter: {len(bridge_flags)}  suspicious drops: {len(drop_flags)}",
        file=__import__("sys").stderr,
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
