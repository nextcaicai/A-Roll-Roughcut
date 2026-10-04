#!/usr/bin/env python3
"""Render a constant-frame-rate mp4 from cut_decisions.json keep ranges."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from export_fcpxml import probe, to_frames


def segment_bounds(keep: list[dict], fps_n: int, fps_d: int) -> list[tuple[float, float, int]]:
    """Same in/out grid as the FCPXML timeline. The third value is the frame count."""
    bounds = []
    for item in sorted(keep, key=lambda row: float(row["sourceStart"])):
        start_f = to_frames(float(item["sourceStart"]), fps_n, fps_d)
        end_f = to_frames(float(item["sourceEnd"]), fps_n, fps_d)
        dur = max(1, end_f - start_f)
        bounds.append((start_f * fps_d / fps_n, (start_f + dur) * fps_d / fps_n, dur))
    return bounds


def filter_graph(bounds: list[tuple[float, float, int]], fps: int, has_audio: bool) -> str:
    """Each piece is exactly `frames` long, matching the subtitle clock.

    fps() on uneven source pictures sometimes emits one extra frame. concat then
    waits for that frame and starts the next piece's audio late. Lock both
    streams to the frame count: clone the last picture if a piece is short,
    drop the extra picture if it is long, and pad audio with silence only up
    to that same length.
    """
    parts: list[str] = []
    labels: list[str] = []
    for idx, (start, end, frames) in enumerate(bounds):
        dur = frames / fps
        parts.append(
            f"[0:v]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,"
            f"fps={fps}:eof_action=pass,"
            f"tpad=stop_mode=clone:stop=-1,trim=end_frame={frames},setpts=PTS-STARTPTS[v{idx}]"
        )
        labels.append(f"[v{idx}]")
        if has_audio:
            parts.append(
                f"[0:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,"
                f"apad=pad_dur=1,atrim=end={dur:.6f},asetpts=PTS-STARTPTS[a{idx}]"
            )
            labels.append(f"[a{idx}]")
    if has_audio:
        parts.append("".join(labels) + f"concat=n={len(bounds)}:v=1:a=1[outv][outa]")
    else:
        parts.append("".join(labels) + f"concat=n={len(bounds)}:v=1:a=0[outv]")
    return ";\n".join(parts)


def parse_clock(value: str) -> float:
    """ffmpeg progress clock, `HH:MM:SS.microseconds`. Unknown clocks count as zero."""
    text = value.strip()
    if not text or text.upper() == "N/A":
        return 0.0
    hms, _, frac = text.partition(".")
    parts = hms.split(":")
    if len(parts) != 3:
        return 0.0
    try:
        hours, minutes, seconds = (int(part) for part in parts)
        base = hours * 3600 + minutes * 60 + seconds
        if not frac or not frac.isdigit():
            return float(base)
        return base + int(frac) / (10 ** len(frac))
    except ValueError:
        return 0.0


def parse_speed(value: str) -> float | None:
    text = value.strip()
    if not text or text.upper() == "N/A":
        return None
    if text.endswith("x"):
        text = text[:-1]
    try:
        speed = float(text)
    except ValueError:
        return None
    if speed <= 0:
        return None
    return speed


def progress_snapshot(out_time: float, speed: float | None, total: float, done: bool) -> dict:
    """Fraction of the cut already written, and seconds still to wait.

    Stay under 1 until ffmpeg says the encode ended, so the bar does not sit
    at 100% while the last frames are still being written.
    """
    if done:
        fraction = 1.0
    elif total <= 0:
        fraction = 0.0
    else:
        fraction = min(max(out_time, 0.0) / total, 0.99)
    remaining = None
    if not done and speed and total > out_time:
        remaining = (total - out_time) / speed
    return {"fraction": fraction, "remaining": remaining}


def render_mp4(
    source: Path,
    decisions: dict,
    output: Path,
    on_progress: Callable[[float, float | None], None] | None = None,
) -> None:
    if not source.is_file():
        raise ValueError(f"没有源片：{source}")
    keep = decisions.get("keep") or []
    if not keep:
        raise ValueError("没有可导出的保留段")
    try:
        media = probe(source)
    except SystemExit as exc:
        raise ValueError(str(exc)) from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("读取源片信息失败，确认本机有 ffprobe") from exc
    fps = int(media["fps_n"] / media["fps_d"])
    bounds = segment_bounds(keep, media["fps_n"], media["fps_d"])
    total = sum(frames for _, _, frames in bounds) / fps
    graph = filter_graph(bounds, fps, media["has_audio"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".ffmpeg", delete=False, encoding="utf-8") as tmp:
        tmp.write(graph)
        filter_path = tmp.name
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-filter_complex_script",
        filter_path,
        "-map",
        "[outv]",
    ]
    if media["has_audio"]:
        cmd.extend(["-map", "[outa]"])
    cmd.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-r",
            str(fps),
            "-movflags",
            "+faststart",
            "-nostats",
            "-stats_period",
            "0.5",
            "-progress",
            "pipe:1",
        ]
    )
    if media["has_audio"]:
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    cmd.append(str(output))
    proc: subprocess.Popen[str] | None = None
    code = 1
    stderr = ""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stderr_parts: list[str] = []

        def _drain() -> None:
            assert proc is not None and proc.stderr is not None
            stderr_parts.append(proc.stderr.read())

        drain = threading.Thread(target=_drain, daemon=True)
        drain.start()
        out_time = 0.0
        speed: float | None = None
        emitted = 0.0
        assert proc.stdout is not None
        for raw in proc.stdout:
            key, _, value = raw.strip().partition("=")
            if key == "out_time":
                out_time = parse_clock(value)
            elif key == "speed":
                speed = parse_speed(value)
            elif key == "progress" and on_progress is not None:
                done = value == "end"
                snap = progress_snapshot(out_time, speed, total, done)
                fraction = snap["fraction"]
                if not done and fraction < emitted:
                    continue
                emitted = fraction
                on_progress(fraction, snap["remaining"])
        drain.join()
        code = proc.wait()
        stderr = stderr_parts[0] if stderr_parts else ""
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        try:
            os.unlink(filter_path)
        except OSError:
            pass
    if code != 0:
        detail = stderr.strip().splitlines()
        tail = " ".join(detail[-3:]) if detail else "ffmpeg 失败"
        raise ValueError(tail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="path to roughcut.mp4")
    args = parser.parse_args()
    if not args.source.exists():
        raise SystemExit(f"source not found: {args.source}")
    data = json.loads(args.decisions.read_text(encoding="utf-8"))
    try:
        render_mp4(args.source, data, args.output)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
