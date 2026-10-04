#!/usr/bin/env python3
"""Check ffmpeg/ffprobe, dashscope, and DASHSCOPE_API_KEY. Does not install or download."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from decisions_common import workspace_root

REPO_ROOT = workspace_root()


REQUIRED_TOOLS = ("ffmpeg", "ffprobe")
REQUIRED_IMPORTS = {
    "dashscope": "dashscope",
}
REQUIRED_KEYS = {
    "DASHSCOPE_API_KEY": "百炼转写",
}


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


def tool_ok(name: str) -> dict:
    path = shutil.which(name)
    if not path:
        return {"ok": False, "detail": "not found in PATH"}
    try:
        proc = subprocess.run([name, "-version"], check=True, text=True, capture_output=True)
        line = (proc.stdout or proc.stderr).splitlines()[:1]
        return {"ok": True, "detail": line[0] if line else path}
    except Exception:
        return {"ok": True, "detail": path}


def key_ok(name: str) -> dict:
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(REPO_ROOT / ".env")
    present = bool((os.environ.get(name) or "").strip())
    source = "env" if present else "missing"
    if present and (REPO_ROOT / ".env").is_file():
        source = "env or .env"
    return {"ok": present, "detail": source if present else f"set {name} or repo-root .env"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = {
        "tools": {name: tool_ok(name) for name in REQUIRED_TOOLS},
        "python": {
            mod: {
                "ok": importlib.util.find_spec(mod) is not None,
                "package": pkg,
            }
            for mod, pkg in REQUIRED_IMPORTS.items()
        },
        "keys": {name: key_ok(name) for name in REQUIRED_KEYS},
        "scripts": str(Path(__file__).resolve().parent),
    }
    missing = [k for k, v in report["tools"].items() if not v["ok"]]
    missing += [v["package"] for v in report["python"].values() if not v["ok"]]
    missing += [name for name, v in report["keys"].items() if not v["ok"]]
    report["ok"] = not missing
    report["missing"] = missing
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("ok" if report["ok"] else "missing: " + ", ".join(missing))
        if missing:
            print("install packages with: pip install -r scripts/requirements.txt", file=sys.stderr)
            for name in REQUIRED_KEYS:
                if name in missing:
                    print(f"export {name} ({REQUIRED_KEYS[name]}) or write it to repo-root .env", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
