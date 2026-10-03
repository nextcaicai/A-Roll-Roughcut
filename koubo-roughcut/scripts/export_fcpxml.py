#!/usr/bin/env python3
"""Write FCPXML 1.8 from cut_decisions.json keep ranges. DaVinci-first; 剪映专业版导同一份。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape


# Editors misread peak rates such as 299/12. The sequence uses the nearest of these.
STANDARD_FPS = (24, 25, 30, 50, 60)


def sequence_rate(nominal_fps: float) -> tuple[int, int]:
    if nominal_fps <= 0:
        return 25, 1
    chosen = min(STANDARD_FPS, key=lambda fps: (abs(fps - nominal_fps), fps))
    return chosen, 1


def probe(source: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(source),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    data = json.loads(proc.stdout)
    video = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams") or [] if s.get("codec_type") == "audio"), None)
    if not video:
        raise SystemExit(f"no video stream: {source}")
    rate = video.get("r_frame_rate") or video.get("avg_frame_rate") or "30/1"
    nominal_n, nominal_d = parse_rate(rate)
    fps_n, fps_d = sequence_rate(nominal_n / nominal_d)
    duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    return {
        "width": int(video.get("width") or 1920),
        "height": int(video.get("height") or 1080),
        "fps_n": fps_n,
        "fps_d": fps_d,
        "duration": duration,
        "has_audio": audio is not None,
        "audio_rate": int(float(audio.get("sample_rate") or 48000)) if audio else 48000,
        "audio_channels": int(audio.get("channels") or 2) if audio else 2,
    }


def parse_rate(rate: str) -> tuple[int, int]:
    if "/" in rate:
        num, den = rate.split("/", 1)
        n, d = int(num), int(den)
        if d <= 0:
            return 30, 1
        return n, d
    fps = float(rate)
    if fps <= 0:
        return 30, 1
    return int(round(fps)), 1


def to_frames(seconds: float, fps_n: int, fps_d: int) -> int:
    return max(0, int(round(seconds * fps_n / fps_d)))


def fcpx_time(frames: int, fps_n: int, fps_d: int) -> str:
    if frames == 0:
        return "0s"
    return f"{frames * fps_d}/{fps_n}s"


def xml_escape(text: str) -> str:
    return escape(text, {"'": "&apos;", '"': "&quot;"})


def build_fcpxml(
    *,
    source: Path,
    project_name: str,
    keep: list[dict],
    media: dict,
) -> str:
    fps_n, fps_d = media["fps_n"], media["fps_d"]
    src_frames = to_frames(media["duration"], fps_n, fps_d)
    clips = []
    timeline = 0
    for item in keep:
        start = to_frames(float(item["sourceStart"]), fps_n, fps_d)
        end = to_frames(float(item["sourceEnd"]), fps_n, fps_d)
        dur = max(1, end - start)
        name = xml_escape(str(item.get("id") or item.get("text") or "keep")[:80])
        clips.append(
            "          "
            f'<asset-clip name="{name}" ref="r2" offset="{fcpx_time(timeline, fps_n, fps_d)}" '
            f'start="{fcpx_time(start, fps_n, fps_d)}" duration="{fcpx_time(dur, fps_n, fps_d)}" '
            'tcFormat="NDF"/>'
        )
        timeline += dur
    src = xml_escape(source.resolve().as_uri())
    title = xml_escape(project_name)
    has_audio = "1" if media["has_audio"] else "0"
    audio_attrs = ""
    if media["has_audio"]:
        audio_attrs = (
            f' audioSources="1" audioChannels="{media["audio_channels"]}" '
            f'audioRate="{media["audio_rate"]}"'
        )
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<!DOCTYPE fcpxml>",
            '<fcpxml version="1.8">',
            "  <resources>",
            f'    <format id="r1" name="{media["width"]}x{media["height"]}" '
            f'frameDuration="{fps_d}/{fps_n}s" width="{media["width"]}" height="{media["height"]}"/>',
            f'    <asset id="r2" name="{xml_escape(source.name)}" src="{src}" start="0s" '
            f'duration="{fcpx_time(src_frames, fps_n, fps_d)}" hasVideo="1" hasAudio="{has_audio}" '
            f'format="r1" videoSources="1"{audio_attrs}/>',
            "  </resources>",
            "  <library>",
            '    <event name="koubo-roughcut">',
            f'      <project name="{title}">',
            f'        <sequence format="r1" duration="{fcpx_time(timeline, fps_n, fps_d)}" '
            'tcStart="0s" tcFormat="NDF">',
            "          <spine>",
            *clips,
            "          </spine>",
            "        </sequence>",
            "      </project>",
            "    </event>",
            "  </library>",
            "</fcpxml>",
            "",
        ]
    )


def write_fcpxml(source: Path, decisions: dict, output: Path) -> None:
    if not source.is_file():
        raise ValueError(f"没有源片：{source}")
    keep = sorted(decisions.get("keep") or [], key=lambda item: float(item["sourceStart"]))
    if not keep:
        raise ValueError("没有可导出的保留段")
    try:
        media = probe(source)
    except SystemExit as exc:
        raise ValueError(str(exc)) from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("读取源片信息失败，确认本机有 ffprobe") from exc
    project = output.stem if output.stem != "roughcut" else source.stem
    xml = build_fcpxml(source=source, project_name=project, keep=keep, media=media)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(xml, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="path to roughcut.fcpxml")
    args = parser.parse_args()
    if not args.source.exists():
        raise SystemExit(f"source not found: {args.source}")
    if not args.decisions.is_file():
        raise SystemExit(f"decisions not found: {args.decisions}")
    data = json.loads(args.decisions.read_text(encoding="utf-8"))
    try:
        write_fcpxml(args.source, data, args.output)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
