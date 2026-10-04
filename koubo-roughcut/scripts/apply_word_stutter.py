#!/usr/bin/env python3
"""Detect adjacent duplicate words/chars in keep (non-reduplication) and apply stutter drops. Step 4a."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from decisions_common import normalize_zh, prune_orphaned_empty_keeps, recompute_timeline
from merge_retake_judgments import apply_proposed_drop, load_json

REFERENCES_DIR = Path(__file__).resolve().parent.parent / "references"
MAX_GAP_SEC = 0.45


def load_lexicon_lines(path: Path) -> frozenset[str]:
    if not path.is_file():
        return frozenset()
    items: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        items.add(normalize_zh(line))
    return frozenset(items)


def load_stutter_whitelists(refs_dir: Path) -> tuple[frozenset[str], frozenset[str]]:
    aa = load_lexicon_lines(refs_dir / "word-stutter-redup.txt")
    words = load_lexicon_lines(refs_dir / "word-stutter-repeat-words.txt")
    return aa, words


def is_legitimate_redup(key: str, *, redup_aa: frozenset[str], redup_words: frozenset[str]) -> bool:
    if not key:
        return True
    if key in redup_words:
        return True
    if len(key) == 2 and key[0] == key[1] and key in redup_aa:
        return True
    return False


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


def words_in_keep(words: list[tuple[float, float, str]], keep: list[dict]) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    for item in keep:
        ss, se = float(item["sourceStart"]), float(item["sourceEnd"])
        for ws, we, t in words:
            if we > ss + 0.001 and ws < se - 0.001:
                out.append((ws, we, t))
    out.sort(key=lambda x: x[0])
    return out


CLAUSE_END = "。！？；"


def token_norm(text: str) -> str:
    return normalize_zh(text)


def find_restarted_openings(words: list[tuple[float, float, str]]) -> list[dict]:
    """Drop a short sentence start that is said again after a pause (所以 … 所以经常)."""
    hits: list[dict] = []
    used: set[int] = set()
    for i, (_ws, _we, text) in enumerate(words):
        if i in used:
            continue
        prev = words[i - 1][2] if i else ""
        after_punct = (i == 0) or (prev.rstrip()[-1:] in CLAUSE_END)
        if not after_punct:
            continue
        for n in (3, 2, 1):
            if i + n > len(words):
                continue
            phrase = [token_norm(words[i + k][2]) for k in range(n)]
            joined = "".join(phrase)
            if len(joined) < 2:
                continue
            last_end = words[i + n - 1][1]
            if i + n < len(words) and words[i + n][0] - last_end <= MAX_GAP_SEC:
                continue
            for j in range(i + n, len(words) - n + 1):
                if words[j][0] - last_end < MAX_GAP_SEC:
                    continue
                later = [token_norm(words[j + k][2]) for k in range(n)]
                if later != phrase or j + n >= len(words):
                    continue
                hits.append(
                    {
                        "sourceStart": round(words[i][0], 3),
                        "sourceEnd": round(last_end, 3),
                        "text": "".join(words[i + k][2] for k in range(n)),
                        "nextText": "".join(words[j + k][2] for k in range(n)),
                        "norm": joined,
                        "gapSeconds": round(words[j][0] - last_end, 3),
                    }
                )
                used.update(range(i, i + n))
                break
            else:
                continue
            break
    return hits


def find_stutter_drops(
    words: list[tuple[float, float, str]],
    *,
    redup_aa: frozenset[str],
    redup_words: frozenset[str],
) -> list[dict]:
    """Adjacent identical tokens → drop earlier occurrence."""
    hits: list[dict] = []
    i = 0
    while i < len(words) - 1:
        ws0, we0, t0 = words[i]
        ws1, we1, t1 = words[i + 1]
        gap = ws1 - we0
        if gap > MAX_GAP_SEC:
            i += 1
            continue
        k0, k1 = token_norm(t0), token_norm(t1)
        if not k0 or k0 != k1:
            i += 1
            continue
        # Character-level ASR stores 看看 as two tokens. The whitelist is the joined form.
        if len(k0) == 1 and (k0 + k1) in redup_aa:
            i += 2
            continue
        if is_legitimate_redup(k0, redup_aa=redup_aa, redup_words=redup_words):
            i += 2
            continue
        hits.append(
            {
                "sourceStart": round(ws0, 3),
                "sourceEnd": round(we0, 3),
                "text": t0,
                "nextText": t1,
                "norm": k0,
                "gapSeconds": round(gap, 3),
            }
        )
        i += 2
    return hits


def fmt_ms(seconds: float) -> str:
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:04.1f}"


def render_md(findings: list[dict], applied: list[dict], dry_run: bool) -> str:
    lines = [
        "# 词级 stutter",
        "",
        f"规则：keep 内相邻相同词/字（gap ≤ {MAX_GAP_SEC}s），非叠词白名单 → drop 前一 token。"
        "句号后短开头隔了一口气又重说 → 也删前一截。词表见 `koubo-roughcut/references/lexicon.md`。",
        "",
        f"候选 **{len(findings)}** · 应用 **{len(applied)}** · {'dry-run' if dry_run else 'applied'}",
        "",
        "| 源片 | norm | 删 | 留 | 结果 |",
        "|---|---|---|---|---|",
    ]
    applied_by_start = {a["sourceStart"]: a for a in applied}
    for f in findings:
        ss = f["sourceStart"]
        row = applied_by_start.get(ss)
        status = row["status"] if row else "—"
        detail = (row.get("detail") or "")[:32] if row else ""
        lines.append(
            f"| {fmt_ms(ss)} | `{f['norm'][:12]}` | {f['text'][:14]} | {f['nextText'][:14]} | {status} {detail} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, help="report path; default decisions dir/word_stutter.md")
    parser.add_argument("--dry-run", action="store_true", help="report only")
    parser.add_argument("--apply", action="store_true", help="write drops into cut_decisions.json")
    parser.add_argument(
        "--refs-dir",
        type=Path,
        default=REFERENCES_DIR,
        help="lexicon txt directory (default: koubo-roughcut/references)",
    )
    args = parser.parse_args()
    if args.apply and args.dry_run:
        raise SystemExit("use either --dry-run or --apply")
    dry_run = not args.apply

    redup_aa, redup_words = load_stutter_whitelists(args.refs_dir)
    transcript = load_json(args.transcript)
    before = load_json(args.decisions)
    keep = before.get("keep") or []
    all_words = load_words(transcript)
    scoped = words_in_keep(all_words, keep)
    findings = find_stutter_drops(scoped, redup_aa=redup_aa, redup_words=redup_words)
    findings.extend(find_restarted_openings(scoped))

    after = copy.deepcopy(before)
    applied_log: list[dict] = []
    if args.apply and findings:
        for f in sorted(findings, key=lambda x: -x["sourceStart"]):
            status, detail = apply_proposed_drop(
                after,
                ds=float(f["sourceStart"]),
                de=float(f["sourceEnd"]),
                text=str(f["text"]),
                reason="stutter",
                kept_instead=None,
                jev_id="word_stutter",
            )
            applied_log.append({**f, "status": status, "detail": detail or ""})
        recompute_timeline(after)
        prune_orphaned_empty_keeps(after, all_words)

    md_path = args.output_md or args.decisions.parent / "word_stutter.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_md(findings, applied_log, dry_run), encoding="utf-8")

    if args.apply:
        args.decisions.write_text(json.dumps(after, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        n_ok = sum(1 for a in applied_log if a["status"] == "applied")
        print(f"applied {n_ok}/{len(findings)} stutter drops → {args.decisions}", file=sys.stderr)
    else:
        print(f"found {len(findings)} candidates (dry-run) → {md_path}", file=sys.stderr)

    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
