"""Work availability for Roll and Rock chains."""

from __future__ import annotations

import datetime as dt
import re
import subprocess
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc
DAY = dt.timedelta(days=1)
WORKDAY = dt.timedelta(hours=8, minutes=15)
KEY = re.compile(r"roll\.calendar\.(\d{4}-\d{2}-\d{2})(?:\.\.(\d{4}-\d{2}-\d{2}))?\s+(0|0\.5|1)\s*$")


def load_calendar(command: str) -> dict[dt.date, float] | None:
    """Read optional Taskwarrior calendar settings; reject conflicting dates."""
    result = subprocess.run(
        [command, "rc.hooks=0", "rc.context=", "rc.color=off", "show", "roll.calendar."],
        check=True, capture_output=True, text=True,
    )
    entries: dict[dt.date, float] = {}
    found = False
    for line in result.stdout.splitlines():
        if not line.startswith("roll.calendar."):
            continue
        found = True
        match = KEY.fullmatch(line)
        if not match:
            raise ValueError(f"Invalid calendar setting: {line}")
        start = dt.date.fromisoformat(match.group(1))
        end = dt.date.fromisoformat(match.group(2)) if match.group(2) else start
        if end < start:
            raise ValueError(f"Calendar range ends before it starts: {line}")
        value = float(match.group(3))
        day = start
        while day <= end:
            if day in entries and entries[day] != value:
                raise ValueError(f"Conflicting calendar entries for {day}")
            entries[day] = value
            day += DAY
    return entries if found else None


def availability(calendar: dict[dt.date, float], day: dt.date) -> float:
    return calendar.get(day, 0.0 if day.weekday() == 5 else 1.0)


def capacity_fraction(calendar: dict[dt.date, float], day: dt.date) -> float:
    value = availability(calendar, day)
    return (3.25 / 8.25) if value == 0.5 else value


def midnight(day: dt.date) -> dt.datetime:
    """Convert a local date boundary to UTC, respecting the system time zone."""
    return dt.datetime.combine(day, dt.time()).astimezone(UTC)


def local_wall(value: dt.datetime) -> dt.datetime:
    return value.astimezone().replace(tzinfo=None)


def work_window(day: dt.date, calendar: dict[dt.date, float]) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time(8, 45))
    available = availability(calendar, day)
    if not available:
        return start, start
    return start, dt.datetime.combine(day, dt.time(12 if available == 0.5 else 17))


def work_duration(raw: object, duration: dt.timedelta) -> dt.timedelta:
    """Interpret day units as 8h15 of available time; leave hour units alone."""
    value = str(raw).strip().upper()
    short = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([DW])", value)
    if short:
        days = float(short[1]) * (7 if short[2] == "W" else 1)
    else:
        iso = re.match(r"P(?:(?P<weeks>[0-9.]+)W)?(?:(?P<days>[0-9.]+)D)?", value)
        days = float(iso["weeks"] or 0) * 7 + float(iso["days"] or 0) if iso else 0
    return duration + (WORKDAY - DAY) * days


def roll_at_settings(raw: object, at: str, calendar: dict | None) -> tuple[int, dt.time]:
    match = re.fullmatch(r"P?([0-9]+)D", str(raw).upper())
    if not match or int(match[1]) < 1:
        raise ValueError("roll_at requires a positive whole-day roll_offset, such as P1D.")
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", at):
        raise ValueError("roll_at must be HH:MM, from 00:00 to 23:59.")
    clock = dt.time.fromisoformat(at)
    if calendar is not None and not dt.time(8, 45) <= clock <= dt.time(17):
        raise ValueError("With a calendar, roll_at must be between 08:45 and 17:00.")
    return int(match[1]), clock


def local_zone() -> ZoneInfo:
    # Taskwarrior sets TZ=UTC for hooks; fixed clock times use the system zone.
    with open("/etc/localtime", "rb") as source:
        return ZoneInfo.from_file(source)


def move_roll(
    base: dt.datetime, raw: object, duration: dt.timedelta,
    calendar: dict[dt.date, float] | None, at: str = "", backward: bool = False,
) -> dt.datetime:
    """Apply a Roll gap, or count available dates at a fixed local clock time."""
    if not at:
        amount = work_duration(raw, duration) if calendar is not None else duration
        amount = -amount if backward else amount
        return (move_work(base, amount, calendar, forward_on_zero=not backward)
                if calendar is not None else base + amount)
    days, clock = roll_at_settings(raw, at, calendar)
    zone = local_zone()
    day = base.astimezone(zone).date()

    def available(day: dt.date) -> bool:
        if calendar is None:
            return True
        start, end = work_window(day, calendar)
        return start < end and start.time() <= clock <= end.time()

    if backward:
        # Find the latest target date that can meet the downstream deadline.
        while not available(day) or dt.datetime.combine(day, clock, tzinfo=zone).astimezone(UTC) > base:
            day -= DAY
        days -= 1
    direction = -DAY if backward else DAY
    while days:
        day += direction
        if available(day):
            days -= 1
    if backward:
        day -= DAY
        if calendar is not None:
            while not availability(calendar, day):
                day -= DAY
            latest = work_window(day, calendar)[1].replace(tzinfo=zone)
        else:
            latest = dt.datetime.combine(day, dt.time(23, 59, 59), tzinfo=zone)
        return latest.astimezone(UTC)
    return dt.datetime.combine(day, clock, tzinfo=zone).astimezone(UTC)


def format_work_duration(duration: dt.timedelta) -> str:
    """Store a calendar gap in workday units without rounding away 15 minutes."""
    seconds = round(duration.total_seconds())
    days, seconds = divmod(seconds, int(WORKDAY.total_seconds()))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = f"{hours}H" if hours else ""
    parts += f"{minutes}M" if minutes else ""
    parts += f"{seconds}S" if seconds else ""
    return f"P{days}D" + (f"T{parts}" if parts else "")


def limited_dates(start: dt.datetime, end: dt.datetime, calendar: dict[dt.date, float]) -> list[dt.date]:
    """Explicit off/half days whose unavailable hours fall in an interval."""
    if end < start:
        start, end = end, start
    first, last = local_wall(start), local_wall(end)
    return [
        day for day, value in sorted(calendar.items())
        if value < 1
        and max(first, work_window(day, calendar)[1])
        < min(last, dt.datetime.combine(day + DAY, dt.time()))
    ]


def work_between(start: dt.datetime, end: dt.datetime, calendar: dict[dt.date, float]) -> dt.timedelta:
    """Return nominal available time between two UTC timestamps."""
    if end < start:
        return -work_between(end, start, calendar)
    total = 0.0
    cursor = start
    while cursor < end:
        day = cursor.astimezone().date()
        boundary = midnight(day + DAY)
        segment_end = min(boundary, end)
        window_start, window_end = work_window(day, calendar)
        left = max(local_wall(cursor), window_start)
        right = min(local_wall(segment_end), window_end)
        total += max(0, (right - left).total_seconds())
        cursor = segment_end
    return dt.timedelta(seconds=total)


def move_work(
    base: dt.datetime, amount: dt.timedelta, calendar: dict[dt.date, float],
    forward_on_zero: bool = True,
) -> dt.datetime:
    """Move through available local days, in either direction."""
    remaining = abs(amount.total_seconds())
    if not remaining:
        cursor = base
        while True:
            day = cursor.astimezone().date()
            start, end = work_window(day, calendar)
            wall = local_wall(cursor)
            if start < end and start <= wall <= end:
                return cursor
            if forward_on_zero:
                if start < end and wall < start:
                    return start.astimezone(UTC)
                cursor = midnight(day + DAY)
            else:
                if start < end and wall > end:
                    return end.astimezone(UTC)
                cursor = midnight(day) - dt.timedelta(microseconds=1)
    forward = amount.total_seconds() > 0
    cursor = base
    while remaining > 0.000001:
        day = (cursor if forward else cursor - dt.timedelta(microseconds=1)).astimezone().date()
        start, end = midnight(day), midnight(day + DAY)
        boundary = end if forward else start
        window_start, window_end = work_window(day, calendar)
        wall = local_wall(cursor)
        edge = max(wall, window_start) if forward else min(wall, window_end)
        capacity = max(0, ((window_end - edge) if forward else (edge - window_start)).total_seconds())
        if capacity >= remaining and capacity:
            target = edge + (dt.timedelta(seconds=remaining) if forward else -dt.timedelta(seconds=remaining))
            return target.astimezone(UTC)
        remaining -= capacity
        cursor = boundary
    return cursor


def long_breaks(start: dt.datetime, end: dt.datetime, calendar: dict[dt.date, float]) -> list[str]:
    """Describe breaks longer than one day, omitting a routine Saturday alone."""
    if end < start:
        start, end = end, start
    day = start.astimezone().date()
    last = end.astimezone().date()
    runs: list[list[dt.date]] = []
    current: list[dt.date] = []
    while day <= last:
        if availability(calendar, day) == 0:
            current.append(day)
        elif current:
            runs.append(current)
            current = []
        day += DAY
    if current:
        runs.append(current)
    return [
        f"{run[0]} to {run[-1]} ({len(run)} off days)" for run in runs
        if len(run) > 1 and any(day.weekday() != 5 for day in run)
    ]
