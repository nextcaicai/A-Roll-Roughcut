#!/usr/bin/env python3
"""SKILL step 8: gather review diffs so a human can confirm preference rules.

Writes preference_candidates.md. Does not write references/.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decisions_common import normalize_zh, workspace_root

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = workspace_root()
SKIP_PARTS = {".git", ".venv", "node_modules"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_skipped(path: Path) -> bool:
    return any(part in SKIP_PARTS or part.startswith(".tmp") for part in path.parts)


def run_label(log_path: Path) -> str:
    parts = log_path.parts
    if "runs" in parts:
        i = parts.index("runs")
        film = parts[i - 1] if i else ""
        run = parts[i + 1] if i + 1 < len(parts) else log_path.parent.name
        return f"{film}/{run}" if film else run
    return str(log_path.parent)


def find_review_logs(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("review_log.json") if "runs" in p.parts and not is_skipped(p)
    )


def load_log(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def events_from_log(path: Path, data: dict[str, Any]) -> list[dict[str, Any]]:
    label = run_label(path)
    out: list[dict[str, Any]] = []
    diff = data.get("diff") or {}
    for kind, key in (("restored", "dropsRestored"), ("added", "dropsAdded")):
        for item in diff.get(key) or []:
            start = float(item.get("sourceStart") or 0)
            end = float(item.get("sourceEnd") or 0)
            out.append(
                {
                    "kind": kind,
                    "run": label,
                    "id": item.get("id"),
                    "reason": item.get("reason") or "",
                    "text": str(item.get("text") or ""),
                    "sourceStart": start,
                    "sourceEnd": end,
                    "duration": round(max(0.0, end - start), 3),
                    "context": item.get("context") or {},
                }
            )
    for item in (data.get("flags") or {}).get("unheard") or []:
        out.append(
            {
                "kind": "unheard",
                "run": label,
                "id": item.get("id"),
                "reason": "flag",
                "text": str(item.get("note") or ""),
                "sourceStart": float(item.get("sourceStart") or 0),
                "sourceEnd": None,
                "duration": 0.0,
                "context": {},
            }
        )
    return out


def cluster_key(event: dict[str, Any]) -> tuple[str, str, str]:
    text = normalize_zh(event.get("text") or "")
    return (str(event["kind"]), str(event.get("reason") or ""), text[:8])


def fmt_sec(seconds: float | None) -> str:
    if seconds is None:
        return ""
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:04.1f}"


def render_event(event: dict[str, Any]) -> str:
    span = fmt_sec(event.get("sourceStart"))
    if event.get("sourceEnd") is not None:
        span = f"{span}–{fmt_sec(event['sourceEnd'])}"
    text = (event.get("text") or "").replace("\n", " ").strip()
    if len(text) > 42:
        text = text[:42] + "…"
    ctx = event.get("context") or {}
    extra = ""
    if ctx.get("before") or ctx.get("after"):
        extra = f"  前后：{(ctx.get('before') or '')}｜{(ctx.get('after') or '')}"
    ident = f"`{event['id']}` " if event.get("id") else ""
    return f"- `{event['run']}` {span} {ident}「{text}」{extra}".rstrip()


def gather(paths: list[Path]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    events: list[dict[str, Any]] = []
    used: list[str] = []
    empty: list[str] = []
    for path in paths:
        data = load_log(path)
        if data is None:
            continue
        items = events_from_log(path, data)
        label = run_label(path)
        if items:
            used.append(label)
            events.extend(items)
        else:
            empty.append(label)
    return events, used, empty


def render_md(events: list[dict[str, Any]], used: list[str], empty: list[str], scanned: int) -> str:
    lines = [
        "# 学偏好候选",
        "",
        f"扫了 {scanned} 份审片日志，其中 {len(used)} 份有人改过或还没听的 flag。生成于 {_iso_now()}。",
        "",
    ]
    if empty:
        lines.append("没有差异的：" + "、".join(f"`{name}`" for name in empty) + "。旧基准被重置时会这样，不能从这些学。")
        lines.append("")
    if not events:
        lines.extend(
            [
                "没有可学的审片差异。不要编规则。",
                "",
            ]
        )
        return "\n".join(lines)

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        groups[cluster_key(event)].append(event)

    sections = (
        ("restored", "人恢复的删除", "流水线删了、人留回。下次同类情况应更保守，或先 flag。"),
        ("added", "人新删的", "流水线留了、人划掉。下次同类情况可以更敢删，仍不确定就 flag。"),
        ("unheard", "建议听没点开", "没有「听过」记录，不能当成人已经定了。"),
    )
    for kind, title, hint in sections:
        kind_groups = [(key, items) for key, items in groups.items() if key[0] == kind]
        if not kind_groups:
            continue
        lines.append(f"## {title}")
        lines.append("")
        lines.append(hint)
        lines.append("")
        kind_groups.sort(key=lambda x: (-len(x[1]), x[0][1], x[0][2]))
        for (_kind, reason, prefix), items in kind_groups:
            label = reason or "未写原因"
            extra = f" · 开头「{prefix}」" if prefix and kind != "unheard" else ""
            lines.append(f"### {label} × {len(items)}{extra}")
            lines.append("")
            for event in items:
                lines.append(render_event(event))
            lines.append("")

    lines.extend(
        [
            "## 下一步",
            "",
            "1. 对照 `references/semantic-deletion.md`，已经有的判例不重复提。",
            "2. 只把出现至少 2 次、或一条会改成片结构的（例如收尾只剩一遍）列为候选规则。",
            "3. 列给用户确认后，按四行格式写入 `semantic-deletion.md`；专名写入 `proper-nouns.txt`。",
            "4. 零候选就停，不写 references。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, help="一个 run 目录，读这里的 review_log.json")
    parser.add_argument("--repo", type=Path, help="从仓库根扫所有 runs/*/review_log.json")
    parser.add_argument("--output", type=Path, help="默认：--run 时写到该 run；否则打印")
    args = parser.parse_args()
    if args.run is None and args.repo is None:
        print("指定 --run <run目录> 或 --repo <仓库根>", file=__import__("sys").stderr)
        return 1

    if args.run is not None:
        run = args.run.expanduser().resolve()
        log = run / "review_log.json"
        if not log.is_file():
            print(f"没有 {log}。先做完第 7 步审片保存。", file=__import__("sys").stderr)
            return 1
        paths = [log]
        default_out = run / "preference_candidates.md"
    else:
        root = args.repo.expanduser().resolve()
        paths = find_review_logs(root)
        default_out = None
        if not paths:
            print("仓库里没有 runs/*/review_log.json。", file=__import__("sys").stderr)
            return 1

    events, used, empty = gather(paths)
    md = render_md(events, used, empty, scanned=len(paths))
    out = args.output.expanduser().resolve() if args.output else default_out
    if out is None:
        print(md, end="" if md.endswith("\n") else "\n")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md if md.endswith("\n") else md + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
