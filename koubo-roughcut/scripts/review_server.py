#!/usr/bin/env python3
"""Local review panel for a run directory. Bind 127.0.0.1 only."""
from __future__ import annotations

import argparse
import array
import json
import mimetypes
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from datetime import datetime, timezone

from decisions_common import PIPELINE_SNAPSHOT, decisions_problems, fill_decision_text
from export_fcpxml import probe, write_fcpxml
from render_roughcut import render_mp4
from export_srt import render_srt, resolve_script
from review_log import build_review_log, load_words, write_review_log

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
EXPORT_DIR_FILE = REPO_ROOT / ".export-dir"
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
CHOOSE_FILE_SCRIPT = """
on run argv
  set defaultDir to POSIX file (item 1 of argv)
  set defaultName to item 2 of argv
  set promptText to item 3 of argv
  set chosen to choose file name with prompt promptText default name defaultName default location defaultDir
  return POSIX path of chosen
end run
"""


class ExportCanceled(Exception):
    pass


def safe_part(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "-", text).strip(" .-")
    return cleaned or "roughcut"


def export_basename(run_dir: Path, suffix: str) -> str:
    if run_dir.parent.name == "runs":
        piece = run_dir.parent.parent.name
    else:
        piece = run_dir.parent.name
    return f"{safe_part(piece)}-{safe_part(run_dir.name)}.{suffix.lstrip('.')}"


def remembered_export_dir(fallback: Path, store: Path | None = None) -> Path:
    path = store or EXPORT_DIR_FILE
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return fallback
    chosen = Path(text).expanduser()
    if chosen.is_dir():
        return chosen
    return fallback


def remember_export_dir(folder: Path, store: Path | None = None) -> None:
    path = store or EXPORT_DIR_FILE
    path.write_text(str(folder.resolve()) + "\n", encoding="utf-8")


def folder_choice_canceled(stderr: str) -> bool:
    text = stderr or ""
    return "User canceled" in text or "(-128)" in text


def choose_export_file(
    fallback: Path,
    default_name: str,
    prompt: str,
    *,
    store: Path | None = None,
    run=None,
) -> Path:
    """Native save panel. The name can be edited; macOS asks before replacing."""
    default = remembered_export_dir(fallback, store)
    if not default.is_dir():
        default = fallback
    if sys.platform != "darwin" and run is None:
        raise ValueError("选择导出位置目前只在 macOS 上可用")
    runner = run or subprocess.run
    proc = runner(
        ["osascript", "-e", CHOOSE_FILE_SCRIPT, str(default.resolve()), default_name, prompt],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        if folder_choice_canceled(proc.stderr or ""):
            raise ExportCanceled()
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ValueError(detail or "无法打开存储对话框")
    chosen = Path((proc.stdout or "").strip()).expanduser()
    if chosen.is_dir() or not chosen.parent.is_dir():
        raise ValueError("请选择一个文件名")
    remember_export_dir(chosen.parent, store)
    return chosen.resolve()


def recompute_timeline(data: dict) -> dict:
    keep = sorted(data.get("keep") or [], key=lambda x: float(x["sourceStart"]))
    t = 0.0
    for item in keep:
        ss = float(item["sourceStart"])
        se = float(item["sourceEnd"])
        span = se - ss
        item["sourceStart"] = round(ss, 3)
        item["sourceEnd"] = round(se, 3)
        item["timelineStart"] = round(t, 3)
        item["timelineEnd"] = round(t + span, 3)
        t += span
    data["keep"] = keep
    return data


def validate_decisions(data: dict) -> str | None:
    errors, _ = decisions_problems(data)
    return errors[0] if errors else None


def load_baseline(run_dir: Path) -> tuple[dict | None, bool]:
    """Pipeline output the review diff is measured against. Frozen on first open.

    Returns (baseline, created_now).
    """
    snapshot = run_dir / PIPELINE_SNAPSHOT
    if snapshot.is_file():
        return json.loads(snapshot.read_text(encoding="utf-8")), False
    decisions_path = run_dir / "cut_decisions.json"
    if not decisions_path.is_file():
        return None, False
    text = decisions_path.read_text(encoding="utf-8")
    snapshot.write_text(text, encoding="utf-8")
    return json.loads(text), True


def compact_transcript(raw: dict) -> dict:
    segs = []
    for i, seg in enumerate(raw.get("segments") or []):
        words = []
        for w in seg.get("words") or []:
            words.append(
                {
                    "word": str(w.get("word") or ""),
                    "start": float(w.get("start") or 0),
                    "end": float(w.get("end") or 0),
                }
            )
        segs.append(
            {
                "id": seg.get("id", i),
                "start": float(seg.get("start") or 0),
                "end": float(seg.get("end") or 0),
                "text": " ".join(str(seg.get("text") or "").split()),
                "words": words,
            }
        )
    return {
        "engine": raw.get("engine"),
        "model": raw.get("model"),
        "segments": segs,
    }


def probe_duration(src: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(src)],
        capture_output=True,
        check=True,
        text=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def compute_peaks(src: Path, bins: int) -> list[float]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "1000", "-f", "s16le", "pipe:1"],
        capture_output=True,
        check=True,
    ).stdout
    samples = array.array("h")
    samples.frombytes(raw)
    n = len(samples)
    if n == 0:
        return [0.0] * bins
    per = max(1, -(-n // bins))
    peaks = []
    for i in range(0, n, per):
        chunk = samples[i : i + per]
        if not chunk:
            continue
        peaks.append(round(max(max(chunk), -min(chunk)) / 32768.0, 4))
    return peaks


def waveform_payload(run_dir: Path, source: Path | None, audio: Path | None) -> dict:
    src = audio if audio and audio.is_file() else source
    if not src or not src.is_file():
        return {"ok": False, "error": "no audio source"}
    size = src.stat().st_size
    duration = probe_duration(src)
    bins = max(8000, min(96000, int(duration * 64)))
    cache = run_dir / ".waveform-peaks.json"
    if cache.is_file():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = None
        if (
            cached
            and cached.get("file") == src.name
            and cached.get("size") == size
            and cached.get("bins") == bins
        ):
            return cached
    payload = {
        "ok": True,
        "file": src.name,
        "size": size,
        "bins": bins,
        "duration": round(duration, 3),
        "peaks": compute_peaks(src, bins),
    }
    cache.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def transcript_file(run_dir: Path) -> Path:
    corrected = run_dir / "transcript.corrected.json"
    if corrected.is_file():
        return corrected
    return run_dir / "transcript.json"


def build_state(run_dir: Path, source: Path | None, audio: Path | None) -> dict:
    decisions_path = run_dir / "cut_decisions.json"
    transcript_path = transcript_file(run_dir)
    decisions = None
    if decisions_path.is_file():
        decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    transcript = None
    if transcript_path.is_file():
        transcript = compact_transcript(json.loads(transcript_path.read_text(encoding="utf-8")))
    return {
        "run": str(run_dir),
        "sourceName": source.name if source else "",
        "hasVideo": bool(source and source.is_file()),
        "hasAudio": bool(audio and audio.is_file()),
        "hasDecisions": decisions is not None,
        "hasTranscript": transcript is not None,
        "decisions": decisions,
        "transcript": transcript,
    }


class ReviewHandler(BaseHTTPRequestHandler):
    run_dir: Path
    source: Path | None
    audio: Path | None
    html: Path
    baseline_decisions: dict | None
    session_started_at: str
    port: int

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, code: int, data: bytes, content_type: str, no_store: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file_range(self, path: Path) -> None:
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        header = self.headers.get("Range")
        if not header:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with path.open("rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            return
        match = RANGE_RE.match(header.strip())
        if not match:
            self.send_error(400, "bad range")
            return
        start_s, end_s = match.group(1), match.group(2)
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else size - 1
        if start >= size or end < start:
            self.send_error(416, "range not satisfiable")
            return
        end = min(end, size - 1)
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with path.open("rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(1024 * 1024, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._bytes(200, self.html.read_bytes(), "text/html; charset=utf-8", no_store=True)
            return
        if path == "/review_cut.js":
            js = SCRIPTS_DIR / "review_cut.js"
            self._bytes(200, js.read_bytes(), "text/javascript; charset=utf-8", no_store=True)
            return
        if path == "/api/state":
            self._json(200, build_state(self.run_dir, self.source, self.audio))
            return
        if path == "/api/waveform":
            try:
                payload = waveform_payload(self.run_dir, self.source, self.audio)
            except (subprocess.CalledProcessError, OSError, KeyError, ValueError) as exc:
                self._json(500, {"ok": False, "error": f"waveform failed: {exc}"})
                return
            self._json(200 if payload.get("ok") else 404, payload)
            return
        if path == "/media/source" and self.source and self.source.is_file():
            self._file_range(self.source)
            return
        if path == "/media/audio" and self.audio and self.audio.is_file():
            self._file_range(self.audio)
            return
        self.send_error(404, "not found")

    def _read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 8 * 1024 * 1024:
            self._json(400, {"ok": False, "error": "bad body"})
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return None

    def _store_decisions(self, data: dict) -> dict:
        data = recompute_timeline(data)
        path = transcript_file(self.run_dir)
        if path.is_file():
            try:
                fill_decision_text(data, load_words(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass
        err = validate_decisions(data)
        if err:
            return {"ok": False, "error": err}
        out = self.run_dir / "cut_decisions.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log_path = None
        if self.baseline_decisions is not None:
            saved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            payload = build_review_log(
                run_dir=self.run_dir,
                baseline=self.baseline_decisions,
                final=data,
                session={
                    "startedAt": self.session_started_at,
                    "savedAt": saved_at,
                    "port": self.port,
                },
                transcript_path=transcript_file(self.run_dir),
            )
            log_path = write_review_log(self.run_dir, payload)
        return {"ok": True, "decisions": data, "reviewLog": str(log_path) if log_path else None}

    def _export_xml(self, decisions: dict, output: Path) -> str:
        if not self.source or not self.source.is_file():
            raise ValueError("没有源片。重新打开审片时要带上 --source。")
        write_fcpxml(self.source, decisions, output)
        return str(output)

    def _export_srt(self, decisions: dict, output: Path) -> tuple[str, int]:
        path = self.run_dir / "transcript.corrected.json"
        if not path.is_file():
            raise ValueError("没有修字稿，不能导出字幕。")
        if not self.source or not self.source.is_file():
            raise ValueError("没有源片。重新打开审片时要带上 --source。")
        transcript = json.loads(path.read_text(encoding="utf-8"))
        script = resolve_script(decisions, run_dir=self.run_dir)
        try:
            media = probe(self.source)
        except SystemExit as exc:
            raise ValueError(str(exc)) from exc
        text = render_srt(transcript, decisions, script, fps=(media["fps_n"], media["fps_d"]))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        count = text.count("\n\n") + 1
        return str(output), count

    def do_PUT(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/decisions":
            self.send_error(404, "not found")
            return
        data = self._read_json()
        if data is None:
            return
        stored = self._store_decisions(data)
        self._json(200 if stored["ok"] else 400, stored)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in {"/api/export/fcpxml", "/api/export/srt", "/api/export/mp4"}:
            self.send_error(404, "not found")
            return
        data = self._read_json()
        if data is None:
            return
        data = recompute_timeline(data)
        err = validate_decisions(data)
        if err:
            self._json(400, {"ok": False, "error": err})
            return
        if path.endswith("fcpxml"):
            suffix, prompt = "fcpxml", "导出 XML"
        elif path.endswith("srt"):
            suffix, prompt = "srt", "导出字幕"
        else:
            suffix, prompt = "mp4", "导出成片"
        try:
            target = choose_export_file(self.run_dir, export_basename(self.run_dir, suffix), prompt)
        except ExportCanceled:
            self._json(200, {"ok": False, "canceled": True})
            return
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        stored = self._store_decisions(data)
        if not stored["ok"]:
            self._json(400, stored)
            return
        try:
            if path.endswith("fcpxml"):
                name = self._export_xml(stored["decisions"], target)
                count = None
            elif path.endswith("srt"):
                name, count = self._export_srt(stored["decisions"], target)
            else:
                if not self.source or not self.source.is_file():
                    raise ValueError("没有源片。重新打开审片时要带上 --source。")
                self._export_mp4_stream(stored, target)
                return
        except (ValueError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
            self._json(
                400,
                {
                    "ok": False,
                    "error": str(exc),
                    "saved": True,
                    "decisions": stored["decisions"],
                    "reviewLog": stored.get("reviewLog"),
                },
            )
            return
        payload = {
            "ok": True,
            "name": name,
            "path": name,
            "saved": True,
            "decisions": stored["decisions"],
            "reviewLog": stored.get("reviewLog"),
        }
        if count is not None:
            payload["count"] = count
        self._json(200, payload)

    def _begin_events(self) -> None:
        self.protocol_version = "HTTP/1.1"
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _event(self, payload: dict) -> None:
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _end_events(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _export_mp4_stream(self, stored: dict, target: Path) -> None:
        self._begin_events()
        try:
            self._event({"progress": 0})

            def on_progress(fraction: float, remaining: float | None) -> None:
                payload: dict = {"progress": round(fraction, 4)}
                if remaining is not None:
                    payload["remaining"] = round(max(remaining, 0.0), 1)
                self._event(payload)

            assert self.source is not None
            render_mp4(self.source, stored["decisions"], target, on_progress=on_progress)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (ValueError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
            try:
                self._event(
                    {
                        "ok": False,
                        "error": str(exc),
                        "saved": True,
                        "decisions": stored["decisions"],
                        "reviewLog": stored.get("reviewLog"),
                    }
                )
                self._end_events()
            except (BrokenPipeError, ConnectionResetError):
                return
            return
        payload = {
            "ok": True,
            "name": str(target),
            "path": str(target),
            "saved": True,
            "decisions": stored["decisions"],
            "reviewLog": stored.get("reviewLog"),
        }
        try:
            self._event(payload)
            self._end_events()
        except (BrokenPipeError, ConnectionResetError):
            return


def find_audio(run_dir: Path) -> Path | None:
    for name in ("source_16k_mono.wav", "source_8k_mono.wav"):
        path = run_dir / name
        if path.is_file():
            return path
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="runs/<date>/ directory")
    parser.add_argument("--source", type=Path, help="talking-head source video")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    run_dir = args.run.expanduser().resolve()
    if not run_dir.is_dir():
        raise SystemExit(f"run dir not found: {run_dir}")
    html = SCRIPTS_DIR / "review.html"
    if not html.is_file():
        raise SystemExit(f"missing {html}")
    source = args.source.expanduser().resolve() if args.source else None
    if source and not source.is_file():
        raise SystemExit(f"source not found: {source}")
    if source is None:
        decisions_path = run_dir / "cut_decisions.json"
        if decisions_path.is_file():
            raw = json.loads(decisions_path.read_text(encoding="utf-8"))
            listed = (raw.get("source") or {}).get("path")
            if listed:
                cand = Path(listed)
                if not cand.is_file():
                    for base in (run_dir, run_dir.parent, run_dir.parent.parent):
                        alt = base / Path(listed).name
                        if alt.is_file():
                            cand = alt
                            break
                if cand.is_file():
                    source = cand
    audio = find_audio(run_dir)
    baseline_decisions, baseline_created = load_baseline(run_dir)
    session_started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    ReviewHandler.run_dir = run_dir
    ReviewHandler.source = source
    ReviewHandler.audio = audio
    ReviewHandler.html = html
    ReviewHandler.baseline_decisions = baseline_decisions
    ReviewHandler.session_started_at = session_started_at
    ReviewHandler.port = args.port
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), ReviewHandler)
    url = f"http://127.0.0.1:{args.port}/"
    print(url)
    print(f"run {run_dir}")
    if source:
        print(f"source {source}")
    if baseline_created:
        print(f"baseline {PIPELINE_SNAPSHOT} saved; review_log compares against it from now on")
    elif baseline_decisions is not None:
        print(f"baseline {PIPELINE_SNAPSHOT}")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
