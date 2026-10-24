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
    for section in parse_script_sections(path):
        lines.extend(section["lines"])
    return lines


def parse_script_sections(path: Path) -> list[dict]:
    """Split 脚本.md into S0 intro and H* sections starting at ##."""
    sections: list[dict] = []
    current: dict = {
        "id": "S0",
        "title": "（开篇）",
        "lines": [],
        "is_heading_section": False,
        "heading_anchor": "",
    }
    heading_count = 0
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = raw.strip()
        if not text:
            continue
        ref = f"L{i}"
        if text.startswith("## "):
            if current["lines"]:
                sections.append(current)
            heading_count += 1
            title = text[3:].strip()
            current = {
                "id": f"H{heading_count}",
                "title": title,
                "lines": [(ref, text)],
                "is_heading_section": True,
                "heading_anchor": normalize_zh(title),
            }
        else:
            current["lines"].append((ref, text))
    if current["lines"]:
        sections.append(current)
    return sections


def anchor_in_corpus(anchor: str, corpus: str) -> bool:
    if len(anchor) < 4:
        return True
    if anchor in corpus:
        return True
    if re.search(r"[A-Za-z]", anchor):
        return anchor.lower() in corpus.lower()
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
    section_rows: list[dict],
    section_missing: list[str],
    strict: bool,
) -> str:
    n_missing = len(global_missing)
    n_section = len(section_missing)
    status = "FAIL" if (n_missing or n_section) else "OK"
    lines = [
        "# 脚本覆盖 · proper nouns",
        "",
        f"脚本：`{script_path.name}` · 状态 **{status}** · missing **{n_missing}** · section **{n_section}**"
        + (" · strict" if strict else ""),
        "",
        "规则：脚本行内出现且命中 `proper-nouns.txt` 的条目，须在 **keep 词级转写**（归一化后）中出现。",
        "各 `##` 小节标题锚点（归一化）也须在 keep 语料中出现。详见 `references/script-coverage.md`。",
        "不检查整句相等；ASR 错字可能仍报 missing（需人听或改 flags）。",
        "",
    ]
    if global_missing:
        lines.append("## 全局缺失")
        lines.append("")
        for t in global_missing:
            lines.append(f"- `{t}`")
        lines.append("")

    if section_missing:
        lines.append("## 小节标题锚点缺失")
        lines.append("")
        for t in section_missing:
            lines.append(f"- `{t}`")
        lines.append("")

    if section_rows:
        lines.extend(
            [
                "## 按脚本小节（## 标题锚点）",
                "",
                "| 小节 | 标题 | 锚点在 keep |",
                "|---|---|---|",
            ]
        )
        for row in section_rows:
            ok = "是" if row["ok"] else "**否**"
            title = row["title"].replace("|", "\\|")[:48]
            lines.append(f"| {row['id']} | {title} | {ok} |")
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

    section_rows: list[dict] = []
    section_missing: list[str] = []
    for section in parse_script_sections(args.script):
        if not section.get("is_heading_section"):
            continue
        anchor = str(section.get("heading_anchor") or "")
        ok = anchor_in_corpus(anchor, corpus)
        section_rows.append(
            {
                "id": section["id"],
                "title": section["title"],
                "anchor": anchor,
                "ok": ok,
            }
        )
        if not ok and len(anchor) >= 4:
            section_missing.append(f"{section['id']}:{anchor}")

    out_path = args.output or args.decisions.parent / "script_coverage.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render_md(
            script_path=args.script,
            keep_corpus_preview=corpus,
            line_rows=line_rows,
            global_missing=global_missing,
            section_rows=section_rows,
            section_missing=section_missing,
            strict=args.strict,
        ),
        encoding="utf-8",
    )

    print(out_path)
    if global_missing or section_missing:
        if global_missing:
            print(f"missing {len(global_missing)} proper noun(s) in keep", file=sys.stderr)
            for t in global_missing[:20]:
                print(f"  - {t}", file=sys.stderr)
        if section_missing:
            print(f"section anchor missing {len(section_missing)}", file=sys.stderr)
            for t in section_missing[:20]:
                print(f"  - {t}", file=sys.stderr)
        return 1 if args.strict else 0
    print("all script proper nouns and section anchors found in keep", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
