#!/usr/bin/env python3
"""Taskwarrior Crunch: calculate workload pressure on add and modify."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from typing import Any

def duration_hours(value: str | None) -> float | None:
    if not value:
        return None
    match = re.fullmatch(
        r"P(?:(\d+(?:\.\d+)?)D)?"
        r"(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?"
        r"(?:(\d+(?:\.\d+)?)S)?)?",
        value,
    )
    if not match:
        return None
    days, hours, minutes, seconds = (float(part or 0) for part in match.groups())
    return days * 24 + hours + minutes / 60 + seconds / 3600


def parse_due(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def crunch_level(task: dict[str, Any], now: datetime | None = None) -> str | None:
    duration = duration_hours(task.get("duration"))
    if duration is None:
        return None
    try:
        progress = float(task.get("progress", 0) or 0)
    except (TypeError, ValueError):
        progress = 0
    progress = max(0, min(progress, 100))
    remaining = duration * (1 - progress / 100)
    if remaining <= 0:
        return None
    start_bonus = max(0, 1.5 * (1 - progress / 25))

    if remaining <= 0.25:
        easy_bonus = 2.0
    elif remaining <= 0.5:
        easy_bonus = 1.5
    elif remaining <= 1:
        easy_bonus = 1.0
    elif remaining <= 2:
        easy_bonus = 0.5
    else:
        easy_bonus = 0

    deadline_bonus = 0.0
    due = parse_due(task.get("due"))
    if due:
        days_left = (due - (now or datetime.now(timezone.utc))).total_seconds() / 86400
        if days_left <= 0:
            deadline_bonus = 4.0
        else:
            hours_per_day = remaining / days_left
            for threshold, bonus in ((8, 4), (4, 3), (2, 2), (1, 1), (0.5, 0.5)):
                if hours_per_day >= threshold:
                    deadline_bonus = bonus
                    break

    score = start_bonus + easy_bonus + deadline_bonus
    if score >= 6:
        return "CRITICAL"
    if score >= 4:
        return "HIGH"
    if score >= 2:
        return "MED"
    return "LOW" if score > 0 else None


def update_task(task: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    level = crunch_level(task, now)
    if level:
        task["crunch"] = level
    else:
        task.pop("crunch", None)
    task.pop("crunch_display", None)
    return task


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
