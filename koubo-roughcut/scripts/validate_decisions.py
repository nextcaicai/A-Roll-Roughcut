#!/usr/bin/env python3
"""Validate cut_decisions.json against the schema in references/cut-decisions.md."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from decisions_common import decisions_problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("decisions", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.decisions.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read JSON: {exc}", file=sys.stderr)
        return 1
    errors, warnings = decisions_problems(data)
    for msg in warnings:
        print(f"warn  {msg}", file=sys.stderr)
    if errors:
        for msg in errors:
            print(msg, file=sys.stderr)
        return 1
    keep = data.get("keep") or []
    timeline = max(float(k["timelineEnd"]) for k in keep)
    print(f"ok  keep={len(keep)} drop={len(data.get('drop') or [])} timeline={timeline:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
