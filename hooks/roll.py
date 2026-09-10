#!/usr/bin/env python3
"""Taskwarrior Roll: rolling due dates and fixed-milestone slack."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any

UTC = dt.timezone.utc
ROLLABLE_STATUSES = {"pending", "waiting"}
MILESTONES = {"checkpoint", "finish-line"}


def parse_task_date(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return dt.datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


def format_task_date(value: dt.datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def parse_duration(value: Any) -> dt.timedelta | None:
    """Accept Taskwarrior/ISO durations plus 90h, 4d, and similar forms."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return dt.timedelta(seconds=float(value))

    text = str(value).strip().upper()
    if text.isdigit():
        return dt.timedelta(seconds=int(text))

    short = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(S|M|H|D|W)", text)
    if short:
        seconds = {"S": 1, "M": 60, "H": 3600, "D": 86400, "W": 604800}
        return dt.timedelta(seconds=float(short.group(1)) * seconds[short.group(2)])

    iso = re.fullmatch(
        r"P(?:(?P<weeks>[0-9.]+)W)?(?:(?P<days>[0-9.]+)D)?"
        r"(?:T(?:(?P<hours>[0-9.]+)H)?(?:(?P<minutes>[0-9.]+)M)?"
        r"(?:(?P<seconds>[0-9.]+)S)?)?",
        text,
    )
    if not iso or not any(iso.groupdict().values()):
        return None
    try:
        values = {key: float(number or 0) for key, number in iso.groupdict().items()}
    except ValueError:
        return None
    return dt.timedelta(**values)


def milestone_kind(task: dict[str, Any]) -> str | None:
    value = str(task.get("roll_fixed") or "").strip().lower()
    if value in {"1", "yes", "true", "on", "fixed"}:
        return "finish-line"
    return value if value in MILESTONES else None


def invalid_milestone(task: dict[str, Any]) -> bool:
    value = str(task.get("roll_fixed") or "").strip()
    return bool(value) and milestone_kind(task) is None


def task_command() -> str:
    return os.environ.get("TASKWARRIOR_ROLL_TASK") or shutil.which("task") or "task"


def task_run(command: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [command, "rc.hooks=0", "rc.context=", *args],
        check=True,
        capture_output=True,
        text=True,
    )


def export_tasks(command: str) -> list[dict[str, Any]]:
    data = json.loads(task_run(command, "rc.json.array=on", "export").stdout or "[]")
    return data if isinstance(data, list) else [data]


def base_date_for(task: dict[str, Any]) -> dt.datetime | None:
    if task.get("status") == "completed":
        return parse_task_date(task.get("end"))
    if task.get("status") in ROLLABLE_STATUSES:
        return parse_task_date(task.get("due"))
    return None


def calculate_schedule(
    tasks: list[dict[str, Any]],
) -> tuple[dict[str, dt.datetime], dict[str, float], list[str]]:
    by_uuid = {str(task["uuid"]): task for task in tasks if task.get("uuid")}
    memo: dict[str, dt.datetime | None] = {}
    visiting: set[str] = set()
    warnings: list[str] = []
    calculated: dict[str, dt.datetime] = {}
    slack_hours: dict[str, float] = {}

    def due_for(uuid: str, respect_milestones: bool = True) -> dt.datetime | None:
        memo_key = f"{uuid}|{int(respect_milestones)}"
        if memo_key in memo:
            return memo[memo_key]
        task = by_uuid.get(uuid)
        if not task:
            memo[memo_key] = None
            return None
        if uuid in visiting:
            warnings.append(f"Roll cycle at {uuid[:8]}.")
            memo[memo_key] = None
            return None

        predecessor_uuid = task.get("roll")
        if not predecessor_uuid or task.get("status") not in ROLLABLE_STATUSES:
            memo[memo_key] = base_date_for(task)
            return memo[memo_key]

        predecessor_uuid = str(predecessor_uuid)
        if parse_duration(predecessor_uuid) is not None:
            warnings.append(
                f"Roll {uuid[:8]}: roll:{predecessor_uuid} is a duration, not a "
                "predecessor UUID. Clear it with: "
                f"task {uuid} modify roll:"
            )
            memo[memo_key] = None
            return None

        if invalid_milestone(task):
            warnings.append(f"Roll {uuid[:8]}: invalid roll_fixed value.")
            memo[memo_key] = None
            return None

        offset = parse_duration(task.get("roll_offset"))
        if offset is None:
            warnings.append(f"Roll {uuid[:8]}: invalid/missing offset.")
            memo[memo_key] = None
            return None

        predecessor = by_uuid.get(predecessor_uuid)
        if not predecessor:
            warnings.append(f"Roll {uuid[:8]}: predecessor missing.")
            memo[memo_key] = None
            return None
        if predecessor.get("status") not in ROLLABLE_STATUSES | {"completed"}:
            warnings.append(f"Roll {uuid[:8]}: predecessor status unusable.")
            memo[memo_key] = None
            return None

        visiting.add(uuid)
        predecessor_due = due_for(predecessor_uuid, respect_milestones)
        visiting.remove(uuid)
        if predecessor.get("status") == "completed":
            predecessor_due = parse_task_date(predecessor.get("end"))
        if predecessor_due is None:
            if not predecessor.get("roll"):
                warnings.append(f"Roll {uuid[:8]}: predecessor has no due/end.")
            memo[memo_key] = None
            return None

        projected_due = predecessor_due + offset
        kind = milestone_kind(task)
        if kind and respect_milestones:
            fixed_due = parse_task_date(task.get("due"))
            if fixed_due is None:
                warnings.append(f"Roll {kind} {uuid[:8]}: no due date.")
                memo[memo_key] = None
                return None
            memo[memo_key] = fixed_due
            return fixed_due

        memo[memo_key] = projected_due
        return projected_due

    for uuid, task in by_uuid.items():
        if task.get("roll") and task.get("status") in ROLLABLE_STATUSES:
            value = due_for(uuid)
            if value is not None and milestone_kind(task) is None:
                calculated[uuid] = value

    for uuid, task in by_uuid.items():
        if milestone_kind(task) is None or task.get("status") not in ROLLABLE_STATUSES:
            continue
        fixed_due = parse_task_date(task.get("due"))
        projected_due = due_for(uuid, respect_milestones=False)
        if fixed_due is not None and projected_due is not None:
            slack_hours[uuid] = round(
                (fixed_due - projected_due).total_seconds() / 3600.0, 6
            )

    return calculated, slack_hours, warnings


def roll_mark_for(task: dict[str, Any], slack: float | None) -> str | None:
    kind = milestone_kind(task)
    if kind == "checkpoint":
        milestone = "\U000f0a48"
    elif kind == "finish-line":
        milestone = "\uf4cd"
    else:
        return None

    if slack is None:
        return milestone
    if slack < 0:
        warning = "\ue3c7"
    elif slack <= 12:
        warning = "\ue3c5"
    elif slack <= 24:
        warning = "\ue3c6"
    else:
        return milestone
    return f"{milestone} {warning}"


def apply_modifications(command: str, uuid: str, modifications: list[str]) -> None:
    subprocess.run(
        [
            command,
            "rc.hooks=0",
            "rc.context=",
            "rc.confirmation=off",
            "rc.verbose=nothing",
            uuid,
            "modify",
            *modifications,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def main() -> int:
    input_data = sys.stdin.read()
    if not input_data.strip():
        return 0
    command = task_command()
    try:
        changed_uuids = {
            str(task["uuid"])
            for line in input_data.splitlines()
            if line.strip()
            and isinstance((task := json.loads(line)), dict)
            and task.get("uuid")
        }
        tasks = export_tasks(command)
        calculated, slack_hours, warnings = calculate_schedule(tasks)
        by_uuid = {str(task["uuid"]): task for task in tasks if task.get("uuid")}
        changed_due_dates = 0
        changed_slack_values = 0
        released_links = 0

        for uuid, task in by_uuid.items():
            if invalid_milestone(task):
                continue
            modifications: list[str] = []
            due_changed = slack_changed = link_released = False
            predecessor_uuid = str(task.get("roll") or "")
            predecessor = by_uuid.get(predecessor_uuid)
            release = (
                uuid in calculated
                and predecessor is not None
                and predecessor.get("status") == "completed"
            )
            finalize = (
                release
                and predecessor_uuid in changed_uuids
                and predecessor.get("end") == predecessor.get("modified")
                and uuid not in changed_uuids
            )
            if (
                uuid in calculated
                and (not release or finalize)
                and parse_task_date(task.get("due")) != calculated[uuid]
            ):
                modifications.append(f"due:{format_task_date(calculated[uuid])}")
                due_changed = True
            if release:
                modifications.extend(("roll:", "roll_offset:"))
                link_released = True
            if uuid in slack_hours:
                try:
                    old_slack = float(task.get("roll_slack"))
                except (TypeError, ValueError):
                    old_slack = None
                if old_slack is None or abs(old_slack - slack_hours[uuid]) > 0.000001:
                    modifications.append(f"roll_slack:{slack_hours[uuid]:g}")
                    slack_changed = True
            elif "roll_slack" in task:
                modifications.append("roll_slack:")
                slack_changed = True
            new_mark = roll_mark_for(task, slack_hours.get(uuid))
            old_mark = task.get("roll_mark")
            if new_mark is not None and old_mark != new_mark:
                modifications.append(f"roll_mark:{new_mark}")
            elif new_mark is None and old_mark is not None:
                modifications.append("roll_mark:")
            if not modifications:
                continue
            try:
                apply_modifications(command, uuid, modifications)
                changed_due_dates += int(due_changed)
                changed_slack_values += int(slack_changed)
                released_links += int(link_released)
            except Exception as exc:
                warnings.append(f"Roll {uuid[:8]} update failed: {exc}")

        updates = []
        if changed_due_dates:
            updates.append(f"{changed_due_dates} due date{'s' if changed_due_dates != 1 else ''}")
        if changed_slack_values:
            updates.append(f"{changed_slack_values} slack value{'s' if changed_slack_values != 1 else ''}")
        if released_links:
            updates.append(
                f"{released_links} completed link"
                f"{'s' if released_links != 1 else ''} released"
            )
        if updates:
            print(f"Roll updated {' and '.join(updates)}.")
        for warning in dict.fromkeys(warnings):
            print(warning)
    except Exception as exc:
        print(f"Roll warning: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
