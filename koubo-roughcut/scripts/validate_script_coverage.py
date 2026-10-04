#!/usr/bin/env python3
"""Check that keep transcript still covers script proper nouns (post-drop gate). Read-only."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from decisions_common import normalize_zh
from merge_retake_judgments import load_json

REFERENCES_DIR = Path(__file__).resolve().parent.parent / "references"


def load_lexicon_terms(path: Path) -> list[str]:
    if not path.is_file():
        return []
    terms: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            terms.append(line)
    return terms


def parse_script_lines(path: Path) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = raw.strip()
        if not text:
            continue
        lines.append((f"L{i}", text))
    return lines


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


def build_keep_corpus(transcript: dict, keep: list[dict]) -> str:
    scoped = words_in_keep(load_words(transcript), keep)
    joined = "".join(t for _, _, t in scoped)
    return normalize_zh(joined)


def term_in_corpus(term: str, corpus: str) -> bool:
    tn = normalize_zh(term)
    if not tn:
        return True
    if tn in corpus:
        return True
    if re.search(r"[A-Za-z]", term):
        return tn.lower() in corpus.lower()
    return False


def terms_required_on_line(script_line: str, lexicon: list[str]) -> list[str]:
    line_n = normalize_zh(script_line)
    line_lower = script_line.lower()
    found: list[str] = []
    for term in lexicon:
        tn = normalize_zh(term)
        if not tn:
            continue
        if tn in line_n:
            found.append(term)
            continue
        if re.search(r"[A-Za-z]", term) and tn.lower() in line_lower:
            found.append(term)
    return found


def drops_mentioning(term: str, drops: list[dict], limit: int = 5) -> list[str]:
    tn = normalize_zh(term)
    hits: list[str] = []
    for d in drops:
        dt = normalize_zh(str(d.get("text") or ""))
        if not dt:
            continue
        if tn in dt or (re.search(r"[A-Za-z]", term) and tn.lower() in dt.lower()):
            hits.append(f"{d.get('id', '?')} {d.get('sourceStart')}-{d.get('sourceEnd')}")
        if len(hits) >= limit:
            break
    return hits


def render_md(
    *,
    script_path: Path,
    keep_corpus_preview: str,
    line_rows: list[dict],
    global_missing: list[str],
    strict: bool,
) -> str:
    n_missing = len(global_missing)
    status = "FAIL" if n_missing else "OK"
    lines = [
        "# 脚本覆盖 · proper nouns",
        "",
        f"脚本：`{script_path.name}` · 状态 **{status}** · missing **{n_missing}**"
        + (" · strict" if strict else ""),
        "",
        "规则：脚本行内出现且命中 `proper-nouns.txt` 的条目，须在 **keep 词级转写**（归一化后）中出现。",
        "不检查整句相等；ASR 错字可能仍报 missing（需人听或改 flags）。",
        "",
    ]
    if global_missing:
        lines.append("## 全局缺失")
        lines.append("")
        for t in global_missing:
            lines.append(f"- `{t}`")
        lines.append("")

    lines.extend(["## 按脚本行（仅含专名）", "", "| 行 | 稿内专名 | 缺失 | 相关 drop |", "|---|---|---|---|"])
    shown = [row for row in line_rows if row["required"]]
    if not shown:
        lines.append("| — | （无） | — | — |")
    notes: list[str] = []
    for row in shown:
        req = ", ".join(f"`{x}`" for x in row["required"])
        miss = ", ".join(f"`{x}`" for x in row["missing"]) if row["missing"] else "—"
        drops = "; ".join(row["dropHints"]) if row["dropHints"] else "—"
        lines.append(f"| {row['ref']} | {req} | {miss} | {drops} |")
        if row["missing"]:
            preview = row["scriptPreview"].replace("|", "\\|")[:72]
            notes.append(f"- **{row['ref']}** · {preview}")
    if notes:
        lines.extend(["", "### 缺失行摘要", ""] + notes)

    lines.extend(
        [
            "",
            "## keep 语料预览",
            "",
            f"归一化长度 {len(keep_corpus_preview)} 字 · 前 120 字：`{keep_corpus_preview[:120]}`…"
            if len(keep_corpus_preview) > 120
            else f"归一化长度 {len(keep_corpus_preview)} 字：`{keep_corpus_preview}`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, required=True, help="脚本.md")
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        help="report path; default decisions dir/script_coverage.md",
    )
    parser.add_argument(
        "--refs-dir",
        type=Path,
        default=REFERENCES_DIR,
        help="lexicon directory (proper-nouns.txt)",
    )
    parser.add_argument(
        "--include-vocabulary",
        action="store_true",
        help="also treat vocabulary-common.txt entries like proper nouns",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when any required term missing from keep",
    )
    args = parser.parse_args()

    if not args.script.is_file():
        raise SystemExit(f"script not found: {args.script}")

    lexicon = load_lexicon_terms(args.refs_dir / "proper-nouns.txt")
    if args.include_vocabulary:
        lexicon.extend(load_lexicon_terms(args.refs_dir / "vocabulary-common.txt"))
    lexicon = list(dict.fromkeys(lexicon))

    transcript = load_json(args.transcript)
    decisions = load_json(args.decisions)
    keep = decisions.get("keep") or []
    drops = decisions.get("drop") or []
    corpus = build_keep_corpus(transcript, keep)

    script_lines = parse_script_lines(args.script)
    all_required: set[str] = set()
    line_rows: list[dict] = []

    for ref, text in script_lines:
        required = terms_required_on_line(text, lexicon)
        missing = [t for t in required if not term_in_corpus(t, corpus)]
        for t in required:
            all_required.add(t)
        drop_hints: list[str] = []
        for t in missing:
            for hint in drops_mentioning(t, drops):
                drop_hints.append(f"{t}←{hint}")
        line_rows.append(
            {
                "ref": ref,
                "scriptPreview": text,
                "required": required,
                "missing": missing,
                "dropHints": drop_hints[:8],
            }
        )

    global_missing = sorted(t for t in all_required if not term_in_corpus(t, corpus))

    out_path = args.output or args.decisions.parent / "script_coverage.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render_md(
            script_path=args.script,
            keep_corpus_preview=corpus,
            line_rows=line_rows,
            global_missing=global_missing,
            strict=args.strict,
        ),
        encoding="utf-8",
    )

    print(out_path)
    if global_missing:
        print(f"missing {len(global_missing)} proper noun(s) in keep", file=sys.stderr)
        for t in global_missing[:20]:
            print(f"  - {t}", file=sys.stderr)
        if len(global_missing) > 20:
            print(f"  … and {len(global_missing) - 20} more", file=sys.stderr)
        return 1 if args.strict else 0
    print("all script proper nouns found in keep", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
