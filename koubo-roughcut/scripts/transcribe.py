#!/usr/bin/env python3
"""Extract mono wav and write word-timed transcript.json + transcript.md.

Default engine is Alibaba Bailian file ASR paraformer-v2.
Realtime models and local Whisper are refused. Doubao file ASR 2.0
(volc.seedasr.auc) is an explicit --engine doubao choice, not a fallback.
API keys come from the environment or a gitignored .env — never from flags.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "paraformer-v2"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DOUBAO_RESOURCE_ID = "volc.seedasr.auc"
DOUBAO_SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
DOUBAO_QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
FILE_MODELS = {
    "paraformer-v2": {
        "sample_rate": 16000,
        "language_hints": True,
    },
    "paraformer-8k-v2": {
        "sample_rate": 8000,
        "language_hints": False,
    },
}
TRAILING_PUNCT = set("，。！？、；：,.!?;:…")


def run(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd), file=sys.stderr)
    subprocess.run(cmd, check=True)


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def env_key(name: str) -> str:
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(REPO_ROOT / ".env")
    return (os.environ.get(name) or "").strip()


def paraformer_unavailable(reason: str) -> SystemExit:
    return SystemExit(
        f"paraformer-v2 暂时用不了：{reason}。"
        "没有改用实时模型，也没有启用本地 Whisper。"
        "要换模型，需要你明确指定 --engine doubao（豆包录音文件识别 2.0），"
        "并提供它能下载的音频地址。"
    )


def api_key() -> str:
    key = env_key("DASHSCOPE_API_KEY")
    if not key:
        raise paraformer_unavailable(
            "缺少 DASHSCOPE_API_KEY，请写进环境变量或仓库根目录 .env"
        )
    return key


def doubao_api_key() -> str:
    key = env_key("DOUBAO_SPEECH_API_KEY")
    if not key:
        raise SystemExit(
            "豆包录音文件识别 2.0 需要环境变量 DOUBAO_SPEECH_API_KEY"
            "（或写在仓库根目录 .env）。现在没有，这次先停。"
            "没有改走 paraformer，也没有启用本地 Whisper。"
        )
    return key


def wav_name(sample_rate: int) -> str:
    return f"source_{sample_rate // 1000}k_mono.wav"


def extract_wav(source: Path, output_dir: Path, sample_rate: int = 16000) -> Path:
    wav = output_dir / wav_name(sample_rate)
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "wav",
            str(wav),
        ]
    )
    return wav


def compact_md(result: dict) -> str:
    lines = ["# Transcript", ""]
    for i, seg in enumerate(result.get("segments") or [], 1):
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or 0)
        text = " ".join(str(seg.get("text") or "").split())
        lines.append(f"{i:03d}  {start:.3f}-{end:.3f}  {text}")
    return "\n".join(lines) + "\n"


def collect_time_values(item: dict) -> list[float]:
    values: list[float] = []
    for key in ("begin_time", "end_time", "start", "end"):
        if item.get(key) not in (None, ""):
            values.append(float(item[key]))
    for word in item.get("words") or []:
        if isinstance(word, dict):
            values.extend(collect_time_values(word))
    return values


def unit_is_ms(values: list[float]) -> bool:
    if not values:
        return True
    if any(v >= 1000 for v in values):
        return True
    # Bailian timestamps are integer milliseconds; seconds usually have a fraction.
    if all(abs(v - round(v)) < 1e-6 for v in values) and max(values) >= 50:
        return True
    return False


def to_seconds(value: object, *, as_ms: bool) -> float | None:
    if value is None or value == "":
        return None
    n = float(value)
    return n / 1000.0 if as_ms else n


def is_sentence(item: dict) -> bool:
    if not isinstance(item, dict) or "text" not in item:
        return False
    if "begin_time" not in item and "start" not in item:
        return False
    if "punctuation" in item and "words" not in item and "sentence_id" not in item:
        return False
    return True


def collect_sentences(payload: object, acc: list[dict] | None = None) -> list[dict]:
    acc = acc if acc is not None else []
    if isinstance(payload, list):
        for item in payload:
            collect_sentences(item, acc)
        return acc
    if not isinstance(payload, dict):
        return acc
    if isinstance(payload.get("sentences"), list):
        for item in payload["sentences"]:
            if is_sentence(item):
                acc.append(item)
        return acc
    if isinstance(payload.get("transcripts"), list):
        for item in payload["transcripts"]:
            collect_sentences(item, acc)
        return acc
    sentence = payload.get("sentence")
    if isinstance(sentence, list):
        collect_sentences(sentence, acc)
        return acc
    if isinstance(sentence, dict) and is_sentence(sentence):
        if sentence.get("sentence_end") in (True, None, "true"):
            acc.append(sentence)
        return acc
    if is_sentence(payload) and payload.get("sentence_end") in (True, None, "true"):
        acc.append(payload)
        return acc
    for key, value in payload.items():
        if key in {"words", "usage"}:
            continue
        collect_sentences(value, acc)
    return acc


def sentence_to_segment(item: dict, offset: float) -> dict:
    as_ms = unit_is_ms(collect_time_values(item))
    start = to_seconds(item.get("begin_time", item.get("start")), as_ms=as_ms) or 0.0
    end = to_seconds(item.get("end_time", item.get("end")), as_ms=as_ms) or start
    words = []
    for word in item.get("words") or []:
        if not isinstance(word, dict):
            continue
        w_start = to_seconds(word.get("begin_time", word.get("start")), as_ms=as_ms)
        w_end = to_seconds(word.get("end_time", word.get("end")), as_ms=as_ms)
        token = str(word.get("text") or word.get("word") or "").strip()
        punct = str(word.get("punctuation") or "")
        if token:
            words.append(
                {
                    "word": token + punct,
                    "start": round((w_start or start) + offset, 3),
                    "end": round((w_end or end) + offset, 3),
                }
            )
    text = " ".join(str(item.get("text") or "").split())
    if not text and words:
        text = "".join(w["word"] for w in words)
    return {
        "start": round(start + offset, 3),
        "end": round(end + offset, 3),
        "text": text,
        "words": words,
    }


def segments_from_payload(payload: object, offset: float = 0.0) -> list[dict]:
    seen: set[tuple[float, float, str]] = set()
    segments: list[dict] = []
    for item in collect_sentences(payload):
        seg = sentence_to_segment(item, offset)
        key = (seg["start"], seg["end"], seg["text"])
        if not seg["text"] or key in seen:
            continue
        seen.add(key)
        segments.append(seg)
    segments.sort(key=lambda s: (s["start"], s["end"]))
    return segments


def resolve_preset(model: str) -> dict:
    if "realtime" in model or model not in FILE_MODELS:
        names = ", ".join(sorted(FILE_MODELS))
        raise paraformer_unavailable(f"只接受录音文件模型（{names}），不接受 {model}")
    return FILE_MODELS[model]


def reject_engine(engine: str, model: str) -> str | None:
    """Return a stop message when the choice is realtime or local Whisper."""
    normalized = "paraformer" if engine == "bailian" else engine
    if (
        normalized in {"whisper", "realtime"}
        or "realtime" in model
        or "whisper" in model
    ):
        return (
            "不用 realtime 模型，也不启用本地 Whisper。"
            "默认是录音文件识别 paraformer-v2。"
            "paraformer-v2 暂时用不了时，停下来告诉用户，不要改走这两个。"
            "要换模型，需要用户明确指定 --engine doubao（豆包录音文件识别 2.0）。"
        )
    if normalized not in {"paraformer", "doubao"}:
        return f"未知引擎 {engine}。可选：paraformer（默认）、doubao。"
    return None


def dashscope_base() -> str:
    return (os.environ.get("DASHSCOPE_HTTP_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def upload_wav_oss(wav: Path, *, model: str, key: str) -> str:
    import requests

    print(f"bailian {model}: uploading local wav", file=sys.stderr)
    try:
        policy_resp = requests.get(
            f"{dashscope_base()}/uploads",
            headers={"Authorization": f"Bearer {key}"},
            params={"action": "getPolicy", "model": model},
            timeout=60,
        )
        if policy_resp.status_code != 200:
            raise paraformer_unavailable(f"获取上传凭证 HTTP {policy_resp.status_code}")
        policy = policy_resp.json().get("data") or {}
        upload_dir = str(policy.get("upload_dir") or "")
        upload_host = str(policy.get("upload_host") or "")
        if not upload_dir or not upload_host:
            raise paraformer_unavailable("上传凭证缺少目录或地址")
        object_key = f"{upload_dir}/{wav.name}"
        with wav.open("rb") as handle:
            files = {
                "OSSAccessKeyId": (None, policy.get("oss_access_key_id")),
                "Signature": (None, policy.get("signature")),
                "policy": (None, policy.get("policy")),
                "x-oss-object-acl": (None, policy.get("x_oss_object_acl")),
                "x-oss-forbid-overwrite": (None, policy.get("x_oss_forbid_overwrite")),
                "key": (None, object_key),
                "success_action_status": (None, "200"),
                "file": (wav.name, handle),
            }
            uploaded = requests.post(upload_host, files=files, timeout=180)
        if uploaded.status_code != 200:
            raise paraformer_unavailable(f"上传音频 HTTP {uploaded.status_code}")
    except requests.RequestException:
        raise paraformer_unavailable("连不上百炼，本地音频没有传上去") from None
    return f"oss://{object_key}"


def task_body(payload: object) -> dict:
    if not isinstance(payload, dict):
        return {}
    output = payload.get("output")
    if isinstance(output, dict) and output.get("task_status"):
        return output
    if payload.get("task_status"):
        return payload
    return output if isinstance(output, dict) else {}


def collect_transcription_urls(node: object, urls: list[str], failed: list[str]) -> None:
    if isinstance(node, dict):
        status = node.get("subtask_status")
        if status and status != "SUCCEEDED":
            failed.append(str(status))
        url = node.get("transcription_url")
        if isinstance(url, str) and url and url not in urls:
            urls.append(url)
        for value in node.values():
            collect_transcription_urls(value, urls, failed)
    elif isinstance(node, list):
        for item in node:
            collect_transcription_urls(item, urls, failed)


def transcribe_filetrans(audio_url: str, *, model: str, preset: dict, language: str, key: str) -> list[dict]:
    import requests

    parameters: dict = {
        "timestamp_alignment_enabled": True,
        "disfluency_removal_enabled": False,
    }
    if preset.get("language_hints"):
        parameters["language_hints"] = [language, "en"] if language == "zh" else [language]
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    if audio_url.startswith("oss://"):
        headers["X-DashScope-OssResourceResolve"] = "enable"
    print(f"bailian {model}: file transcription", file=sys.stderr)
    try:
        submitted = requests.post(
            f"{dashscope_base()}/services/audio/asr/transcription",
            headers=headers,
            json={"model": model, "input": {"file_urls": [audio_url]}, "parameters": parameters},
            timeout=60,
        )
        if submitted.status_code != 200:
            raise paraformer_unavailable(f"提交转写 HTTP {submitted.status_code}")
        task_id = str((submitted.json().get("output") or {}).get("task_id") or "")
        if not task_id:
            raise paraformer_unavailable("提交转写没有返回任务号")
        deadline = time.time() + 20 * 60
        body: dict = {}
        while True:
            polled = requests.get(
                f"{dashscope_base()}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {key}"},
                timeout=60,
            )
            if polled.status_code != 200:
                raise paraformer_unavailable(f"查询转写 HTTP {polled.status_code}")
            body = task_body(polled.json())
            status = str(body.get("task_status") or "")
            if status == "SUCCEEDED":
                break
            if status in {"FAILED", "CANCELED", "UNKNOWN"}:
                raise paraformer_unavailable(f"转写任务 {status}")
            if time.time() > deadline:
                raise paraformer_unavailable("转写超过 20 分钟还没有结果")
            time.sleep(3)
        urls: list[str] = []
        failed: list[str] = []
        collect_transcription_urls(body, urls, failed)
        if failed:
            raise paraformer_unavailable(f"转写子任务 {failed[0]}")
        segments: list[dict] = []
        if not urls:
            segments.extend(segments_from_payload(body))
        for url in urls:
            fetched = requests.get(url, timeout=60)
            if fetched.status_code != 200:
                raise paraformer_unavailable(f"下载转写结果 HTTP {fetched.status_code}")
            segments.extend(segments_from_payload(fetched.json()))
    except requests.RequestException:
        raise paraformer_unavailable("连不上百炼，转写没有完成") from None
    if not segments or not any(seg.get("words") for seg in segments):
        raise paraformer_unavailable("转写没有词时间，不能拿来切")
    return segments


def attach_trailing_punct(text: str, words: list[dict]) -> None:
    if not words:
        return
    spoken = "".join(str(word.get("word") or "") for word in words)
    if not text.startswith(spoken):
        return
    tail = text[len(spoken) :].strip()
    if tail and all(ch in TRAILING_PUNCT for ch in tail):
        words[-1]["word"] = str(words[-1]["word"]) + tail


def doubao_to_segments(payload: object) -> list[dict]:
    result = payload.get("result") if isinstance(payload, dict) else None
    utterances = result.get("utterances") if isinstance(result, dict) else None
    if not isinstance(utterances, list):
        return []
    sentences = []
    for item in utterances:
        if not isinstance(item, dict):
            continue
        words = []
        for word in item.get("words") or []:
            if not isinstance(word, dict):
                continue
            token = str(word.get("text") or "").strip()
            if not token or word.get("start_time") is None or word.get("end_time") is None:
                continue
            words.append(
                {
                    "word": token,
                    "start": round(float(word["start_time"]) / 1000.0, 3),
                    "end": round(float(word["end_time"]) / 1000.0, 3),
                }
            )
        text = str(item.get("text") or "")
        attach_trailing_punct(text, words)
        start = item.get("start_time")
        end = item.get("end_time")
        if start is None or end is None or not words:
            continue
        sentences.append(
            {
                "text": text or "".join(word["word"] for word in words),
                "start": round(float(start) / 1000.0, 3),
                "end": round(float(end) / 1000.0, 3),
                "words": words,
            }
        )
    sentences.sort(key=lambda item: (item["start"], item["end"]))
    return sentences


def doubao_audio_format(audio_url: str) -> str:
    path = audio_url.split("?", 1)[0].lower()
    for ext in ("wav", "mp3", "ogg"):
        if path.endswith("." + ext):
            return ext
    raise SystemExit(
        "豆包录音文件识别 2.0 需要 wav、mp3 或 ogg 地址。这个地址的格式对不上，这次先停。"
    )


def transcribe_doubao(audio_url: str, *, key: str) -> list[dict]:
    import requests

    task_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": key,
        "X-Api-Resource-Id": DOUBAO_RESOURCE_ID,
        "X-Api-Request-Id": task_id,
        "X-Api-Sequence": "-1",
    }
    body = {
        "user": {"uid": "koubo-roughcut"},
        "audio": {
            "url": audio_url,
            "format": doubao_audio_format(audio_url),
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": False,
            "enable_punc": True,
            "enable_ddc": False,
            "show_utterances": True,
        },
    }
    print(f"doubao {DOUBAO_RESOURCE_ID}: file transcription", file=sys.stderr)
    try:
        submitted = requests.post(DOUBAO_SUBMIT_URL, headers=headers, json=body, timeout=60)
        code = submitted.headers.get("X-Api-Status-Code", "")
        if submitted.status_code != 200 or code != "20000000":
            message = submitted.headers.get("X-Api-Message", "")
            raise SystemExit(
                f"豆包录音文件识别 2.0 提交失败（HTTP {submitted.status_code} {code} {message}）。"
                "这次先停，没有改走别的模型。"
            )
        query_headers = {k: v for k, v in headers.items() if k != "X-Api-Sequence"}
        deadline = time.time() + 20 * 60
        while True:
            polled = requests.post(DOUBAO_QUERY_URL, headers=query_headers, json={}, timeout=60)
            status = polled.headers.get("X-Api-Status-Code", "")
            if status == "20000000":
                segments = doubao_to_segments(polled.json())
                if not segments or not any(seg.get("words") for seg in segments):
                    raise SystemExit("豆包录音文件识别没有词时间，不能拿来切。这次先停。")
                return segments
            if status in {"20000001", "20000002"}:
                if time.time() > deadline:
                    raise SystemExit("豆包录音文件识别超过 20 分钟还没有结果。这次先停。")
                time.sleep(3)
                continue
            message = polled.headers.get("X-Api-Message", "")
            raise SystemExit(
                f"豆包录音文件识别 2.0 失败（{status} {message}）。这次先停，没有改走别的模型。"
            )
    except requests.RequestException:
        raise SystemExit("豆包录音文件识别 2.0 连不上。这次先停，没有改走别的模型。") from None


def build_result(*, engine: str, model: str, language: str, segments: list[dict]) -> dict:
    for i, seg in enumerate(segments):
        seg["id"] = i
    return {
        "engine": engine,
        "model": model,
        "language": language,
        "text": "".join(str(s.get("text") or "") for s in segments),
        "segments": segments,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="runs/<date>/ directory")
    parser.add_argument("--language", default="zh")
    parser.add_argument(
        "--engine",
        default="paraformer",
        help="paraformer（默认，录音文件识别）或 doubao（豆包录音文件识别 2.0）。不要写 whisper / realtime。",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="paraformer 文件模型，默认 paraformer-v2。realtime 模型会被拒绝。",
    )
    parser.add_argument("--script", type=Path, help="脚本.md kept for later cut steps; not sent to ASR")
    parser.add_argument(
        "--audio-url",
        help="豆包必须给它能下载的地址。paraformer 有公网地址时也可传，否则上传本地 wav。",
    )
    parser.add_argument("--transcript", type=Path, help="Reuse an existing transcript JSON")
    args = parser.parse_args()
    refused = reject_engine(args.engine, args.model)
    if refused:
        raise SystemExit(refused)
    engine = "paraformer" if args.engine == "bailian" else args.engine
    if not args.source.exists() and not args.transcript and not args.audio_url:
        raise SystemExit(f"source not found: {args.source}")
    args.output.mkdir(parents=True, exist_ok=True)
    json_path = args.output / "transcript.json"
    md_path = args.output / "transcript.md"
    if args.transcript:
        result = json.loads(args.transcript.read_text(encoding="utf-8"))
    elif engine == "doubao":
        if not args.audio_url:
            raise SystemExit(
                "豆包录音文件识别 2.0 需要 --audio-url，而且必须是它能下载的 wav、mp3 或 ogg。"
                "本地文件不会自动换成这种地址。这次先停，没有改走别的模型。"
            )
        if args.script and not args.script.exists():
            raise SystemExit(f"script not found: {args.script}")
        segments = transcribe_doubao(args.audio_url, key=doubao_api_key())
        result = build_result(
            engine="doubao",
            model=DOUBAO_RESOURCE_ID,
            language=args.language,
            segments=segments,
        )
    else:
        key = api_key()
        if args.script and not args.script.exists():
            raise SystemExit(f"script not found: {args.script}")
        preset = resolve_preset(args.model)
        sample_rate = int(preset["sample_rate"])
        audio_url = args.audio_url
        if not audio_url:
            wav = args.output / wav_name(sample_rate)
            if not wav.exists() or wav.stat().st_size < 1000:
                wav = extract_wav(args.source, args.output, sample_rate=sample_rate)
            audio_url = upload_wav_oss(wav, model=args.model, key=key)
        segments = transcribe_filetrans(
            audio_url,
            model=args.model,
            preset=preset,
            language=args.language,
            key=key,
        )
        result = build_result(
            engine="bailian",
            model=args.model,
            language=args.language,
            segments=segments,
        )
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(compact_md(result), encoding="utf-8")
    print(json_path)
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
