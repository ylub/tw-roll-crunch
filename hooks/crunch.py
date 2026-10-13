#!/usr/bin/env python3
"""Taskwarrior Crunch: calculate workload pressure on add and modify."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from roll_crunch import CRUNCH_DISPLAY, crunch_level, duration_hours, parse_due, update_task


def main() -> int:
    lines = [line for line in sys.stdin if line.strip()]
    if not lines:
        return 0
    try:
        task = json.loads(lines[-1])
    except (json.JSONDecodeError, TypeError) as exc:
        print(f"Crunch error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(update_task(task), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
