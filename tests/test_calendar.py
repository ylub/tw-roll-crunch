import datetime as dt
import io
import os
import runpy
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hooks import roll
from roll_calendar import limited_dates, load_calendar, long_breaks, move_work, work_between, work_duration

ROOT = Path(__file__).parent.parent
UTC = dt.timezone.utc


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.previous_tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/Chicago"
        time.tzset()

    def tearDown(self):
        if self.previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.previous_tz
        time.tzset()

    def test_calendar_settings_and_5787_sample(self):
        source = (ROOT / "config/calendar-5787.taskrc").read_text()
        output = "\n".join(
            f"{key} {value}" for line in source.splitlines()
            if line.startswith("roll.calendar.")
            for key, value in [line.split("=", 1)]
        )
        with patch("roll_calendar.subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, "")):
            calendar = load_calendar("task")
        self.assertEqual(calendar[dt.date(2026, 9, 11)], 0.5)
        self.assertEqual(calendar[dt.date(2026, 9, 13)], 0)
        self.assertEqual(calendar[dt.date(2027, 4, 29)], 0)
        self.assertEqual(calendar[dt.date(2027, 6, 10)], 0.5)
        self.assertEqual(calendar[dt.date(2027, 8, 12)], 0)

    def test_bad_or_conflicting_entry_blocks_calendar(self):
        for output in (
            "roll.calendar.2026-09-31 0",
            "roll.calendar.2026-09-21 0.25",
            "roll.calendar.2026-09-21 0\nroll.calendar.2026-09-20..2026-09-22 0.5",
        ):
            with self.subTest(output=output), patch(
                "roll_calendar.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, output, ""),
            ), self.assertRaises(ValueError):
                load_calendar("task")

    def test_forward_backward_skip_saturday_and_half_day(self):
        friday = dt.datetime(2026, 9, 18, 21, tzinfo=UTC)  # Friday 16:00 local
        calendar = {dt.date(2026, 9, 21): 0.5}
        sunday = move_work(friday, work_duration("P1D", dt.timedelta(days=1)), calendar)
        self.assertEqual(sunday, dt.datetime(2026, 9, 20, 21, tzinfo=UTC))
        finish = move_work(friday, work_duration("P2D", dt.timedelta(days=2)), calendar)
        self.assertEqual(move_work(finish, -work_duration("P2D", dt.timedelta(days=2)), calendar), friday)
        self.assertEqual(work_between(friday, finish, calendar), dt.timedelta(hours=16, minutes=30))

    def test_dst_uses_local_dates(self):
        friday = dt.datetime(2026, 3, 6, 22, tzinfo=UTC)  # Friday 16:00 CST
        sunday = move_work(friday, work_duration("P1D", dt.timedelta(days=1)), {})
        self.assertEqual(sunday, dt.datetime(2026, 3, 8, 21, tzinfo=UTC))  # Sunday 16:00 CDT
        self.assertEqual(move_work(sunday, -work_duration("P1D", dt.timedelta(days=1)), {}), friday)

    def test_half_day_stops_at_local_noon(self):
        calendar = {dt.date(2026, 9, 25): 0.5,
                    dt.date(2026, 9, 26): 0,
                    dt.date(2026, 9, 27): 0}
        friday_11 = dt.datetime(2026, 9, 25, 16, tzinfo=UTC)
        friday_noon = dt.datetime(2026, 9, 25, 17, tzinfo=UTC)
        monday_945 = dt.datetime(2026, 9, 28, 14, 45, tzinfo=UTC)
        self.assertEqual(work_between(friday_noon, dt.datetime(2026, 9, 26, 4, tzinfo=UTC), calendar), dt.timedelta())
        self.assertEqual(move_work(friday_noon, dt.timedelta(hours=5, minutes=30), calendar),
                         dt.datetime(2026, 9, 28, 19, 15, tzinfo=UTC))
        self.assertEqual(move_work(friday_11, dt.timedelta(hours=2), calendar), monday_945)
        self.assertEqual(move_work(monday_945, -dt.timedelta(hours=2), calendar), friday_11)
        self.assertEqual(work_between(friday_11, monday_945, calendar), dt.timedelta(hours=2))
        help_code = runpy.run_path(str(ROOT / "task_roll_help"))
        tasks = [
            {"uuid": "A", "status": "pending", "due": "20260925T160000Z"},
            {"uuid": "B", "status": "pending", "roll": "A", "roll_offset": "PT2H",
             "roll_fixed": "finish-line", "due": "20260928T144500Z"},
        ]
        self.assertEqual(help_code["rock_plan"](tasks, "B", calendar)["start"], friday_11)

    def test_work_window_and_night_checkpoint(self):
        calendar = {dt.date(2026, 9, 25): 0.5,
                    dt.date(2026, 9, 26): 0,
                    dt.date(2026, 9, 27): 0}
        tasks = [
            {"uuid": "A", "status": "pending", "due": "20260924T214500Z"},  # Thu 16:45
            {"uuid": "B", "status": "pending", "roll": "A", "roll_offset": "PT30M"},
            {"uuid": "C", "status": "pending", "roll": "B", "roll_offset": "PT3H"},
            {"uuid": "N", "status": "pending", "roll": "C", "roll_offset": "PT1H",
             "roll_fixed": "night", "due": "20260929T030000Z"},  # Mon 22:00
            {"uuid": "D", "status": "pending", "roll": "N", "roll_offset": "PT30M"},
            {"uuid": "F", "status": "pending", "roll": "D", "roll_offset": "P1D",
             "roll_fixed": "finish-line", "due": "20260930T030000Z"},
            {"uuid": "Z", "status": "pending", "roll": "N", "roll_offset": "P0D"},
        ]
        due, _, warnings = roll.calculate_schedule(tasks, calendar)
        self.assertEqual(warnings, [])
        self.assertEqual(due["B"], dt.datetime(2026, 9, 25, 14, tzinfo=UTC))  # Fri 09:00
        self.assertEqual(due["C"], dt.datetime(2026, 9, 25, 17, tzinfo=UTC))  # Fri noon
        self.assertEqual(due["D"], dt.datetime(2026, 9, 29, 14, 15, tzinfo=UTC))  # Tue 09:15
        self.assertEqual(due["Z"], dt.datetime(2026, 9, 29, 13, 45, tzinfo=UTC))  # Tue 08:45
        self.assertNotIn("N", due)
        self.assertNotIn("F", due)
        self.assertEqual(roll.r_mark_for(tasks[3], None), "󰖔 night")
        help_code = runpy.run_path(str(ROOT / "task_roll_help"))
        self.assertEqual(help_code["r_mark_for"](tasks[3]), "󰖔 night")
        self.assertEqual(help_code["rock_plan"](tasks, "F", calendar)["boundary"][0], "night")
        self.assertEqual(tasks[5]["due"], "20260930T030000Z")
        zero_finish = [tasks[0], {"uuid": "H", "status": "pending", "roll": "A",
                                  "roll_offset": "P0D", "roll_fixed": "finish-line",
                                  "due": "20260929T030000Z"}]
        self.assertEqual(help_code["rock_plan"](zero_finish, "H", calendar)["start"],
                         dt.datetime(2026, 9, 28, 22, tzinfo=UTC))  # Mon 17:00

    def test_closing_time_and_hour_vs_day_offsets(self):
        base = dt.datetime(2026, 9, 28, 21, 45, tzinfo=UTC)  # Monday 16:45
        self.assertEqual(move_work(base, dt.timedelta(minutes=30), {}),
                         dt.datetime(2026, 9, 29, 14, tzinfo=UTC))  # Tuesday 09:00
        self.assertEqual(move_work(base, work_duration("P1D", dt.timedelta(days=1)), {}),
                         dt.datetime(2026, 9, 29, 21, 45, tzinfo=UTC))
        self.assertEqual(work_duration("PT24H", dt.timedelta(days=1)), dt.timedelta(days=1))

    def test_chain_initial_dates_respect_half_day_cutoff(self):
        help_code = runpy.run_path(str(ROOT / "task_roll_help"))
        uuids = [f"{index:08d}-1111-1111-1111-111111111111" for index in (1, 2)]
        tasks = [
            {"uuid": uuid, "status": "pending", "description": "work", "remaining": "PT1H"}
            for uuid in uuids
        ]
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as source:
            source.write("\n".join(f"{uuid}:work" for uuid in uuids))
            source.flush()
            options = help_code["parse_chain"]([
                source.name, "--start", "2026-09-25", "--finish", "2026-09-25",
            ])
            candidate, _ = help_code["chain_candidate"](
                tasks, options, calendar={dt.date(2026, 9, 25): 0.5},
            )
            tasks[-1]["due"] = "20260925T210000Z"  # Fixed finish at 16:00 local.
            hard_candidate, _ = help_code["chain_candidate"](
                tasks, options, hard_finish=True, calendar={dt.date(2026, 9, 25): 0.5},
            )
        self.assertEqual(
            [help_code["parse_date"](task["due"]).astimezone().strftime("%H:%M") for task in candidate],
            ["12:00", "12:00"],
        )
        self.assertEqual(
            [help_code["parse_date"](task["due"]).astimezone().strftime("%H:%M") for task in hard_candidate],
            ["12:00", "16:00"],
        )
        tasks[-1]["due"] = "20260926T030000Z"  # Existing due is 22:00 local.
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as source:
            source.write("\n".join(f"{uuid}:work" for uuid in uuids))
            source.flush()
            options = help_code["parse_chain"]([
                source.name, "--start", "2026-09-28", "--finish", "2026-09-28",
            ])
            flexible, _ = help_code["chain_candidate"](tasks, options, calendar={})
            fixed, _ = help_code["chain_candidate"](tasks, options, hard_finish=True, calendar={})
        self.assertEqual([help_code["parse_date"](task["due"]).astimezone().strftime("%H:%M") for task in flexible],
                         ["17:00", "17:00"])
        self.assertEqual(help_code["parse_date"](fixed[-1]["due"]).astimezone().strftime("%H:%M"), "22:00")

    def test_capacity_gap_uses_eight_hour_fifteen_minute_day(self):
        tasks = [{"uuid": "B", "status": "pending", "project": "work",
                  "remaining": "PT4H", "roll": "A", "roll_offset": "PT1H"}]
        with patch.object(roll, "project_capacity", return_value=24):
            changes = roll.refresh_offsets(tasks, "task", {})
        self.assertEqual(changes, {"B": "P1D"})
        tasks[0]["roll_offset"] = "PT24H"
        with patch.object(roll, "project_capacity", return_value=24):
            self.assertEqual(roll.refresh_offsets(tasks, "task", {}), {"B": "P1D"})
        help_code = runpy.run_path(str(ROOT / "task_roll_help"))
        half = dt.date(2026, 9, 25)
        plan = {"root": tasks[0], "finish": {"due": "20260925T170000Z"},
                "path": [(tasks[0], None)]}
        self.assertEqual(help_code["weekday_capacity_slack"](
            plan, 49.5, dt.datetime(2026, 9, 25, 13, tzinfo=UTC), {half: 0.5},
        ), -0.75)  # 3h15 available, less 4h remaining.

    def test_calendar_reschedules_without_a_task_change(self):
        tasks = [
            {"uuid": "A", "status": "pending", "due": "20260918T210000Z"},
            {"uuid": "B", "status": "pending", "due": "20260919T210000Z",
             "roll": "A", "roll_offset": "P1D"},
        ]
        with patch("sys.stdin", io.StringIO("")), patch.object(
            roll, "task_command", return_value="task"
        ), patch.object(roll, "export_tasks", return_value=tasks), patch.object(
            roll, "load_calendar", return_value={}
        ), patch.object(roll, "apply_modifications") as modify:
            self.assertEqual(roll.main(), 0)
        self.assertIn("due:20260920T210000Z", modify.call_args.args[2])
        with patch("sys.stdin", io.StringIO("")), patch.object(
            roll, "load_calendar", return_value=None
        ), patch.object(roll, "export_tasks") as export:
            self.assertEqual(roll.main(), 0)
            export.assert_not_called()

    def test_limited_dates_only_reports_explicit_lost_time(self):
        calendar = {dt.date(2026, 9, 25): 0.5, dt.date(2026, 9, 26): 0,
                    dt.date(2026, 9, 27): 0}
        friday_11 = dt.datetime(2026, 9, 25, 16, tzinfo=UTC)
        friday_noon = dt.datetime(2026, 9, 25, 17, tzinfo=UTC)
        monday_1 = dt.datetime(2026, 9, 28, 6, tzinfo=UTC)
        self.assertEqual(limited_dates(friday_11, friday_noon, calendar), [])
        self.assertEqual(limited_dates(friday_noon, monday_1, calendar),
                         [dt.date(2026, 9, 25), dt.date(2026, 9, 26), dt.date(2026, 9, 27)])
        self.assertEqual(limited_dates(friday_noon, monday_1, {}), [])

    def test_roll_notice_on_calendar_shift(self):
        tasks = [
            {"uuid": "A", "status": "pending", "due": "20260925T160000Z"},
            {"uuid": "B", "status": "pending", "due": "20260925T210000Z",
             "roll": "A", "roll_offset": "PT2H"},
        ]
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO("")), patch.object(
            roll, "task_command", return_value="task"
        ), patch.object(roll, "export_tasks", return_value=tasks), patch.object(
            roll, "load_calendar", return_value={dt.date(2026, 9, 25): 0.5,
                                                 dt.date(2026, 9, 26): 0,
                                                 dt.date(2026, 9, 27): 0}
        ), patch.object(roll, "apply_modifications") as modify, patch.dict(
            os.environ, {"TERM": "xterm-256color"}
        ), patch("sys.stdout", output):
            os.environ.pop("NO_COLOR", None)
            self.assertEqual(roll.main(), 0)
        self.assertIn("due:20260928T144500Z", modify.call_args.args[2])
        self.assertIn("\x1b[2;36mRoll calendar: limited availability on 2026-09-25, 2026-09-26, 2026-09-27", output.getvalue())

    def test_manual_chain_due_notice_but_standalone_unchanged(self):
        calendar = {dt.date(2026, 9, 26): 0}
        linked = [
            {"uuid": "A", "status": "pending", "due": "20260925T210000Z"},
            {"uuid": "B", "status": "pending", "due": "20260926T210000Z",
             "roll": "A", "roll_offset": "P1D", "roll_fixed": "checkpoint"},
        ]
        standalone = [{"uuid": "C", "status": "pending", "due": "20260926T210000Z"}]
        for tasks, expected in ((linked, "2026-09-26"), (standalone, None)):
            output = io.StringIO()
            with self.subTest(tasks=tasks), patch("sys.stdin", io.StringIO('{"uuid":"' + tasks[-1]["uuid"] + '"}\n')), patch.object(
                roll, "task_command", return_value="task"
            ), patch.object(roll, "export_tasks", return_value=tasks), patch.object(
                roll, "load_calendar", return_value=calendar
            ), patch.object(roll, "apply_modifications") as modify, patch("sys.stdout", output):
                self.assertEqual(roll.main(), 0)
            if expected:
                self.assertIn(f"Roll calendar: limited availability on {expected}", output.getvalue())
            else:
                self.assertNotIn("Roll calendar:", output.getvalue())
            if tasks is standalone:
                modify.assert_not_called()
                self.assertEqual(standalone[0]["due"], "20260926T210000Z")

    def test_roll_rock_and_chain_share_available_time(self):
        help_code = runpy.run_path(str(ROOT / "task_roll_help"))
        friday = "20260918T210000Z"
        tasks = [
            {"id": 1, "uuid": "A", "status": "pending", "description": "Start", "due": friday},
            {"id": 2, "uuid": "B", "status": "pending", "description": "Finish", "roll": "A", "roll_offset": "P2D"},
        ]
        calendar = {dt.date(2026, 9, 21): 0.5}
        due, _, warnings = roll.calculate_schedule(tasks, calendar)
        self.assertEqual(warnings, [])
        tasks[1].update(due=roll.format_task_date(due["B"]), roll_fixed="finish-line")
        plan = help_code["rock_plan"](tasks, "B", calendar)
        self.assertEqual(plan["start"], roll.parse_task_date(friday))
        self.assertEqual(help_code["weekday_dates"](
            dt.date(2026, 9, 18), dt.date(2026, 9, 22), 3, calendar,
        )[1], dt.date(2026, 9, 20))
        uuids = [f"{index:08d}-1111-1111-1111-111111111111" for index in range(1, 4)]
        selected_tasks = [
            {"id": index, "uuid": uuid, "status": "pending", "description": "work", "remaining": "PT1H"}
            for index, uuid in enumerate(uuids, 1)
        ]
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as source:
            source.write("\n".join(f"{uuid}:work" for uuid in uuids))
            source.flush()
            options = help_code["parse_chain"]([
                source.name, "--start", "2026-09-18", "--finish", "2026-09-22",
            ])
            candidate, _ = help_code["chain_candidate"](selected_tasks, options, True, calendar)
        self.assertEqual(help_code["rock_plan"](candidate, uuids[-1], calendar)["start"],
                         roll.parse_task_date(candidate[0]["due"]))
        self.assertEqual(help_code["parse_date"](candidate[1]["due"]).astimezone().date(),
                         dt.date(2026, 9, 20))

    def test_fixed_deadline_on_off_day_and_break_display(self):
        help_code = runpy.run_path(str(ROOT / "task_roll_help"))
        calendar = {dt.date(2026, 9, 27): 0, dt.date(2026, 9, 28): 0}
        self.assertEqual(long_breaks(
            dt.datetime(2026, 9, 25, 21, tzinfo=UTC),
            dt.datetime(2026, 9, 29, 21, tzinfo=UTC), calendar,
        ), ["2026-09-26 to 2026-09-28 (3 off days)"])
        self.assertEqual(long_breaks(
            dt.datetime(2026, 9, 25, 21, tzinfo=UTC),
            dt.datetime(2026, 9, 27, 21, tzinfo=UTC), {},
        ), [])
        view_tasks = [
            {"id": 1, "uuid": "X", "status": "pending", "description": "Before", "due": "20260925T210000Z"},
            {"id": 2, "uuid": "Y", "status": "pending", "description": "After", "due": "20260929T210000Z", "roll": "X", "roll_offset": "P1D"},
        ]
        roll_lines = help_code["render_rolls"](view_tasks, calendar=calendar).splitlines()
        chain_lines = help_code["render_chain_details"](view_tasks, 1, calendar=calendar).splitlines()
        for lines in (roll_lines, chain_lines):
            break_index = next(i for i, line in enumerate(lines) if "Calendar break:" in line)
            child_index = next(i for i, line in enumerate(lines) if "After" in line and "2" in line and i > 2)
            self.assertIn("2026-09-26 to 2026-09-28 (3 off days)", lines[break_index])
            self.assertLess(break_index, child_index)
        self.assertIn("DUE (LOCAL)", help_code["render_chain_details"](view_tasks, 1, calendar=calendar))
        self.assertIn("2026-09-25 16:00", help_code["render_chain_details"](view_tasks, 1, calendar=calendar))
        self.assertEqual(long_breaks(
            dt.datetime(2026, 9, 25, 21, tzinfo=UTC),
            dt.datetime(2026, 9, 28, 21, tzinfo=UTC),
            {dt.date(2026, 9, 27): 0},
        ), ["2026-09-26 to 2026-09-27 (2 off days)"])
        tasks = [
            {"id": 1, "uuid": "A", "status": "pending", "description": "Start", "due": "20260918T210000Z"},
            {"id": 2, "uuid": "B", "status": "pending", "description": "Finish", "roll": "A", "roll_offset": "P1D", "roll_fixed": "finish-line", "due": "20260919T210000Z"},
        ]
        self.assertIn("root", help_code["rock_plan"](tasks, "B", calendar))

    def test_manual_gap_completion_and_capacity(self):
        help_code = runpy.run_path(str(ROOT / "task_roll_help"))
        calendar = {dt.date(2026, 9, 21): 0}
        tasks = [
            {"uuid": "A", "status": "completed", "end": "20260918T210000Z"},
            {"uuid": "B", "status": "pending", "project": "work", "remaining": "PT4H",
             "roll": "A", "roll_offset": "P1D", "roll_manual": "yes"},
        ]
        with patch.object(roll, "project_capacity") as capacity:
            self.assertEqual(roll.refresh_offsets(tasks, "task", calendar), {})
            capacity.assert_not_called()
        due, _, _ = roll.calculate_schedule(tasks, calendar)
        self.assertEqual(due["B"], dt.datetime(2026, 9, 20, 21, tzinfo=UTC))
        plan = {"root": tasks[0], "finish": {"due": "20260922T210000Z"},
                "path": [(tasks[1], None)]}
        slack = help_code["weekday_capacity_slack"](
            plan, 24, dt.datetime(2026, 9, 18, 21, tzinfo=UTC), calendar,
        )
        self.assertEqual(slack, 8)  # Three available days at 4h, minus 4h remaining.


if __name__ == "__main__":
    unittest.main()
