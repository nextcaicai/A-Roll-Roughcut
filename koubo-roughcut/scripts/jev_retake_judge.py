#!/usr/bin/env python3
"""Run Jev (TypeSafe System One) on retake candidate pairs. Not in the default skill path."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

POLICY = (
    "Talking-head rough-cut policy: if two takes are the same script beat, "
    "keep the later take unless the later take is clearly more incomplete or broken. "
    "If they are different consecutive ideas, they are not a retake group. "
    "For two clauses in the same breath, the shorter failed clause before a comma "
    "may be a stutter restart of the longer clause that follows."
)

SAME_BEAT_Q = {
    "type": "noul",
    "instructions": (
        "Do `earlier_take` and `later_take` cover the same talking-head beat "
        "(a retake of the same idea), rather than two different consecutive points?"
    ),
    "criteria": {
        "true": "Same idea said twice, including a failed clause plus a complete later clause",
        "false": "Different consecutive ideas, or the later take continues new content rather than restarting",
    },
}

LATER_WORSE_Q = {
    "type": "noul",
    "instructions": (
        "Relative to `earlier_take`, is `later_take` clearly more incomplete, slurred, or broken?"
    ),
    "criteria": {
        "true": "Later take trails off, repeats fragments, or is less usable than the earlier take",
        "false": "Later take is as complete or more complete",
    },
}

KEEP_WHICH_Q = {
    "type": "choice",
    "instructions": "Which take should remain in the rough cut?",
    "criteria": {
        "later": "Same beat and later take is usable; default is keep the last take",
        "earlier": "Same beat but later take is clearly more incomplete",
        "not_same": "Not a retake group; both may belong in sequence",
        "uncertain": "Cannot decide from this transcript text",
    },
}

QUESTIONS = {
    "same_beat": SAME_BEAT_Q,
    "later_worse": LATER_WORSE_Q,
    "keep_which": KEEP_WHICH_Q,
}


def load_env_key() -> str:
    key = (os.environ.get("TYPESAFE_API_KEY") or "").strip()
    if key:
        return key
    env_path = Path.cwd() / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TYPESAFE_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    raise SystemExit("TYPESAFE_API_KEY not set; source repo .env or export")


def _retry_sleep(attempt: int, exc: BaseException) -> float:
    if isinstance(exc, urllib.error.HTTPError) and exc.code in (429, 529):
        raw = exc.headers.get("Retry-After") if exc.headers else None
        if raw:
            try:
                return max(float(raw), 1.0)
            except ValueError:
                pass
        return min(30.0, 2.0 * (2**attempt))
    return 2.0 * (attempt + 1)


def jev_call(state: dict, questions: dict, api_key: str, retries: int = 5) -> dict:
    url = "https://api.typesafe.ai/v1/systemone"
    body = json.dumps({"state": state, "model": "jev-latest", "questions": questions}).encode()
    last: Exception | None = None
    for i in range(retries):
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "koubo-roughcut/jev-retake-judge",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 529) and exc.code < 500:
                body_text = exc.read().decode()[:800]
                raise RuntimeError(f"Jev API HTTP {exc.code}: {body_text}") from exc
            time.sleep(_retry_sleep(i, exc))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = exc
            time.sleep(_retry_sleep(i, exc))
    raise RuntimeError(f"Jev API failed: {last}")


def jev_eval(state: dict, api_key: str) -> dict:
    return jev_call(state, QUESTIONS, api_key)


def build_fanout_batch(cands: list[dict], start_index: int = 1) -> tuple[dict, dict]:
    """One state + 3 questions per candidate (TypeSafe parallel fan-out)."""
    pairs = []
    questions: dict = {}
    for offset, cand in enumerate(cands):
        i = start_index + offset
        jid = f"j{i:03d}"
        cid = str(cand.get("id") or jid)
        path = f"pairs[{offset}]"
        pairs.append(
            {
                "id": cid,
                "kind": cand.get("kind"),
                "earlier_take": cand["earlier"]["text"],
                "later_take": cand["later"]["text"],
            }
        )
        questions[f"{jid}__same_beat"] = {
            "type": "noul",
            "instructions": (
                f"Do `{path}.earlier_take` and `{path}.later_take` cover the same talking-head beat "
                "(a retake of the same idea), rather than two different consecutive points?"
            ),
            "criteria": SAME_BEAT_Q["criteria"],
        }
        questions[f"{jid}__later_worse"] = {
            "type": "noul",
            "instructions": (
                f"Relative to `{path}.earlier_take`, is `{path}.later_take` clearly more incomplete, "
                "slurred, or broken?"
            ),
            "criteria": LATER_WORSE_Q["criteria"],
        }
        questions[f"{jid}__keep_which"] = {
            "type": "choice",
            "instructions": f"For `{path}`, which take should remain in the rough cut?",
            "criteria": KEEP_WHICH_Q["criteria"],
        }
    state = {"policy": POLICY, "pairs": pairs}
    return state, questions


def judgment_from_answers(
    index: int,
    cand: dict,
    ans: dict,
    *,
    model: str | None,
    usage: dict | None,
) -> dict:
    jid = f"j{index:03d}"
    same = float(ans["same_beat"]["noul"])
    worse = float(ans["later_worse"]["noul"])
    kw = ans["keep_which"]
    choice = kw["choice"]
    conf = kw.get("confidence")
    if conf is not None:
        conf = float(conf)
    panel_action, note = decide_action(same, worse, choice, conf)
    print(
        f"{cand.get('id')}: {panel_action} same={same:.2f} worse={worse:.2f} choice={choice}",
        file=sys.stderr,
    )
    return {
        "id": jid,
        "candidateId": cand.get("id"),
        "candidate": cand,
        "sameBeat": round(same, 4),
        "laterWorse": round(worse, 4),
        "keepChoice": choice,
        "choiceConfidence": conf,
        "probabilities": kw.get("probabilities"),
        "panelAction": panel_action,
        "note": note,
        "proposedDrop": (
            cand["earlier"]
            if panel_action == "drop_earlier"
            else cand["later"]
            if panel_action == "drop_later"
            else None
        ),
        "model": model,
        "usage": usage,
    }


def parse_fanout_response(cands: list[dict], start_index: int, resp: dict) -> list[dict]:
    answers = resp.get("answers") or {}
    model = resp.get("model")
    usage = resp.get("usage")
    out: list[dict] = []
    for offset, cand in enumerate(cands):
        i = start_index + offset
        jid = f"j{i:03d}"
        try:
            block = {
                "same_beat": answers[f"{jid}__same_beat"],
                "later_worse": answers[f"{jid}__later_worse"],
                "keep_which": answers[f"{jid}__keep_which"],
            }
        except KeyError as exc:
            out.append(
                {
                    "id": jid,
                    "candidateId": cand.get("id"),
                    "candidate": cand,
                    "error": f"missing answer {exc}",
                    "panelAction": "flag",
                    "note": "Jev batch missing answer; manual review",
                }
            )
            continue
        out.append(judgment_from_answers(i, cand, block, model=model, usage=usage))
    return out


def judge_fanout_batch(
    cands: list[dict],
    api_key: str,
    start_index: int,
    *,
    api_calls: list[float],
) -> list[dict]:
    state, questions = build_fanout_batch(cands, start_index=start_index)
    t0 = time.monotonic()
    resp = jev_call(state, questions, api_key)
    api_calls.append(time.monotonic() - t0)
    return parse_fanout_response(cands, start_index, resp)


def judge_one(index: int, cand: dict, api_key: str) -> dict:
    jid = f"j{index:03d}"
    state = {
        "policy": POLICY,
        "kind": cand.get("kind"),
        "earlier_take": cand["earlier"]["text"],
        "later_take": cand["later"]["text"],
    }
    try:
        resp = jev_eval(state, api_key)
    except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        print(f"{cand.get('id')}: ERROR {exc}", file=sys.stderr)
        return {
            "id": jid,
            "candidateId": cand.get("id"),
            "candidate": cand,
            "error": str(exc),
            "panelAction": "flag",
            "note": "Jev API error; manual review",
        }
    ans = resp.get("answers") or {}
    block = {
        "same_beat": ans["same_beat"],
        "later_worse": ans["later_worse"],
        "keep_which": ans["keep_which"],
    }
    return judgment_from_answers(
        index,
        cand,
        block,
        model=resp.get("model"),
        usage=resp.get("usage"),
    )


def decide_action(same: float, worse: float, choice: str, choice_conf: float | None) -> tuple[str, str]:
    """Return panel_action, note. panel_action: drop_earlier | drop_later | keep_both | flag."""
    if choice == "not_same" or same < 0.45:
        return "keep_both", "not a retake group"
    if choice == "uncertain" or (choice_conf is not None and choice_conf < 0.35):
        return "flag", "Jev uncertain; keep both until review panel"
    if same >= 0.8 and worse >= 0.6 and choice == "earlier":
        return "drop_later", "keep earlier take; later clearly worse"
    if same >= 0.8 and worse <= 0.4 and choice in {"later", "earlier"}:
        if choice == "earlier":
            return "drop_later", "keep earlier (exception)"
        return "drop_earlier", "keep later take (default retake rule)"
    if same >= 0.55:
        return "flag", f"ambiguous same={same:.2f} worse={worse:.2f} choice={choice}"
    return "keep_both", "low same_beat"


def fmt_ms(seconds: float) -> str:
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:04.1f}"


def render_md(judgments: list[dict], run_label: str, meta: dict | None = None) -> str:
    counts: dict[str, int] = {}
    for j in judgments:
        counts[j["panelAction"]] = counts.get(j["panelAction"], 0) + 1
    lines = [
        f"# Jev 重说判定 · {run_label}",
        "",
        "`panelAction=drop_*` 表示进审片面板时应划掉（写 drop）的区间。",
        "",
    ]
    if meta:
        lines.append(
            f"模式：**{meta.get('mode', '?')}** · "
            f"{meta.get('elapsedSeconds', '?')}s · "
            f"HTTP {meta.get('httpCalls', '?')} 次"
        )
        if meta.get("apiSeconds"):
            lines.append(f"API 累计 {meta['apiSeconds']}s")
        lines.append("")
    lines.extend(
        [
            f"统计：{', '.join(f'{k}={v}' for k, v in sorted(counts.items()))}",
            "",
            "| id | 类型 | 源片 | panelAction | same | worse | choice | 说明 |",
            "|---|---|---|---|---:|---:|---|---|",
        ]
    )
    for j in judgments:
        c = j["candidate"]
        t0 = fmt_ms(c["earlier"]["sourceStart"])
        if j.get("error"):
            lines.append(
                f"| `{j['id']}` | {c['kind']} | {t0} | **flag** | — | — | — | API error |"
            )
            continue
        lines.append(
            f"| `{j['id']}` | {c['kind']} | {t0} | **{j['panelAction']}** | "
            f"{j['sameBeat']:.2f} | {j['laterWorse']:.2f} | {j['keepChoice']} | {j['note'][:36]} |"
        )
    lines.extend(["", "## 样本片段", ""])
    for j in judgments[:5]:
        c = j["candidate"]
        lines.append(f"### {j['id']} → {j['panelAction']}")
        lines.append(f"- earlier: {c['earlier']['text'][:80]}")
        lines.append(f"- later: {c['later']['text'][:80]}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--mode",
        choices=("fanout", "http"),
        default="fanout",
        help="fanout=one API call per batch (default); http=one call per candidate",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=0,
        help="pairs per fan-out request; 0 = all picked candidates in one call",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="parallel HTTP requests when --mode http",
    )
    parser.add_argument("--pin-ids", nargs="*", default=[], help="always judge these candidate ids")
    args = parser.parse_args()
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    api_key = load_env_key()
    data = json.loads(args.candidates.read_text(encoding="utf-8"))
    all_cands = data.get("candidates") or []
    picked: list[dict] = list(all_cands[: args.limit])
    if args.pin_ids:
        by_id = {c["id"]: c for c in all_cands}
        have = {c["id"] for c in picked}
        for pid in args.pin_ids:
            if pid in by_id and pid not in have:
                picked.append(by_id[pid])
    cands = picked
    if not cands:
        raise SystemExit("no candidates")

    t0 = time.monotonic()
    api_call_times: list[float] = []
    judgments: list[dict] = []

    if args.mode == "fanout":
        chunk = args.batch_size if args.batch_size > 0 else len(cands)
        idx = 1
        for start in range(0, len(cands), chunk):
            batch = cands[start : start + chunk]
            judgments.extend(
                judge_fanout_batch(batch, api_key, idx, api_calls=api_call_times)
            )
            idx += len(batch)
    else:
        workers = min(args.workers, len(cands))
        by_index: dict[int, dict] = {}

        def timed_judge(i: int, cand: dict) -> dict:
            t1 = time.monotonic()
            row = judge_one(i, cand, api_key)
            api_call_times.append(time.monotonic() - t1)
            return row

        if workers == 1:
            for i, cand in enumerate(cands, start=1):
                by_index[i] = timed_judge(i, cand)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(timed_judge, i, cand): i
                    for i, cand in enumerate(cands, start=1)
                }
                for fut in as_completed(futures):
                    by_index[futures[fut]] = fut.result()
        judgments = [by_index[i] for i in range(1, len(cands) + 1)]

    elapsed = time.monotonic() - t0
    http_calls = len(api_call_times)
    api_sum = round(sum(api_call_times), 2)
    print(
        f"judged {len(judgments)} in {elapsed:.1f}s "
        f"(mode={args.mode}, http={http_calls}, api_sum={api_sum}s)",
        file=sys.stderr,
    )

    meta = {
        "mode": args.mode,
        "batchSize": args.batch_size or len(cands),
        "httpCalls": http_calls,
        "apiSeconds": api_sum,
        "elapsedSeconds": round(elapsed, 2),
    }
    out = {
        "version": 1,
        "judgmentCount": len(judgments),
        **meta,
        "judgments": judgments,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path = args.output_md or args.output_json.with_suffix(".md")
    md_path.write_text(render_md(judgments, args.output_json.parent.name, meta), encoding="utf-8")
    print(args.output_json)
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
