#!/usr/bin/env python3
"""Extend keep segment starts into preceding gap (inhalation buffer). Never into drop ranges."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from decisions_common import apply_lead_in


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--lead-in", type=float, default=None, help="seconds; default breath.leadIn or 0.12")
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.decisions.read_text(encoding="utf-8"))
    breath = data.setdefault("breath", {})
    if args.lead_in is not None:
        breath["leadIn"] = round(args.lead_in, 3)
    elif "leadIn" not in breath:
        breath["leadIn"] = 0.12
    n = apply_lead_in(data, lead_in=args.lead_in)
    out = args.decisions if args.in_place else args.decisions.with_name("cut_decisions.lead_in.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"lead-in {breath.get('leadIn')}s  adjusted {n} keep segment(s)", file=sys.stderr)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
