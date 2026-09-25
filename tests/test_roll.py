import datetime as dt
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hooks import roll


UTC = dt.timezone.utc


class RollTests(unittest.TestCase):
    def setUp(self):
        calendar = patch.object(roll, "load_calendar", return_value=None)
        calendar.start()
        self.addCleanup(calendar.stop)

    def test_roll_and_rock_help_list_fixed_date_values(self):
        command = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        for arguments in (["help"], ["rock", "help"]):
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(command["main"](arguments), 0)
            for value in ("checkpoint", "vacation", "off", "night", "finish-line"):
                with self.subTest(arguments=arguments, value=value):
                    self.assertIn(f"roll_fixed:{value}", output.getvalue())
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(command["main"](["help"]), 0)
        self.assertIn("phoenix_wait:yes", output.getvalue())
        self.assertIn("phoenix_count:2", output.getvalue())
        self.assertIn("two more copies", output.getvalue())

    def test_chain_fixed_milestones_and_slack(self):
        tasks = [
            {"uuid": "A", "status": "pending", "due": "20260101T000000Z"},
            {"uuid": "B", "status": "pending", "roll": "A", "roll_offset": "2d"},
            {"uuid": "C", "status": "pending", "roll": "B", "roll_offset": "4d", "roll_fixed": "checkpoint", "due": "20260110T000000Z"},
            {"uuid": "D", "status": "pending", "roll": "C", "roll_offset": "1d"},
            {"uuid": "E", "status": "pending", "roll": "D", "roll_offset": "2d", "roll_fixed": "finish-line", "due": "20260115T000000Z"},
        ]
        due, slack, warnings = roll.calculate_schedule(tasks)
        self.assertEqual(due["B"], dt.datetime(2026, 1, 3, tzinfo=UTC))
        self.assertNotIn("C", due)
        self.assertEqual(due["D"], dt.datetime(2026, 1, 11, tzinfo=UTC))
        self.assertNotIn("E", due)
        self.assertEqual(slack, {"C": 72.0, "E": 120.0})
        self.assertEqual(warnings, [])

    def test_named_checkpoints_keep_dates_and_stop_rock(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        for kind, marker in (("vacation", "󰂒 vacation"), ("off", "󱁕 off")):
            with self.subTest(kind=kind):
                tasks = [
                    {"id": 1, "uuid": "A", "status": "pending", "due": "20260910T000000Z"},
                    {"id": 2, "uuid": "B", "status": "pending", "roll": "A", "roll_offset": "P2D", "roll_fixed": kind, "due": "20260916T000000Z"},
                    {"id": 3, "uuid": "C", "status": "pending", "roll": "B", "roll_offset": "P1D"},
                    {"id": 4, "uuid": "D", "status": "pending", "roll": "C", "roll_offset": "P3D", "roll_fixed": "finish-line", "due": "20260920T000000Z"},
                ]
                due, slack, warnings = roll.calculate_schedule(tasks)
                self.assertNotIn("B", due)
                self.assertEqual(due["C"], dt.datetime(2026, 9, 17, tzinfo=UTC))
                self.assertEqual(slack["B"], 96.0)
                self.assertEqual(warnings, [])
                self.assertEqual(roll.r_mark_for(tasks[1], None), marker)
                self.assertEqual(roll_help["r_mark_for"](tasks[1]), marker)
                plan = roll_help["rock_plan"](tasks, "4")
                self.assertNotIn("root", plan)
                self.assertIn(kind.title(), plan["warnings"][0])

    def test_completed_predecessor_uses_end(self):
        tasks = [
            {"uuid": "A", "status": "completed", "due": "20260101T000000Z", "end": "20260102T120000Z"},
            {"uuid": "B", "status": "waiting", "roll": "A", "roll_offset": "PT12H"},
        ]
        due, _, _ = roll.calculate_schedule(tasks)
        self.assertEqual(due["B"], dt.datetime(2026, 1, 3, tzinfo=UTC))

    def test_completed_predecessor_releases_child(self):
        tasks = [
            {
                "uuid": "A",
                "status": "completed",
                "end": "20260102T120000Z",
                "modified": "20260102T120000Z",
            },
            {
                "uuid": "B",
                "status": "pending",
                "due": "20260110T000000Z",
                "roll": "A",
                "roll_offset": "PT12H",
            },
        ]
        cases = [
            (
                {"uuid": "A", "status": "completed"},
                ["due:20260103T000000Z", "roll:", "roll_offset:"],
            ),
            ({"uuid": "B", "status": "pending"}, ["roll:", "roll_offset:"]),
            ({"uuid": "X", "status": "pending"}, ["roll:", "roll_offset:"]),
        ]
        for changed, expected in cases:
            with self.subTest(changed=changed["uuid"]), patch(
                "sys.stdin", io.StringIO(json.dumps(changed))
            ), patch.object(roll, "task_command", return_value="task"), patch.object(
                roll, "export_tasks", return_value=tasks
            ), patch.object(
                roll, "apply_modifications"
            ) as modify:
                self.assertEqual(roll.main(), 0)
                modify.assert_called_once_with("task", "B", expected)

        tasks[0]["modified"] = "20260104T120000Z"
        with patch("sys.stdin", io.StringIO(json.dumps(tasks[0]))), patch.object(
            roll, "task_command", return_value="task"
        ), patch.object(roll, "export_tasks", return_value=tasks), patch.object(
            roll, "apply_modifications"
        ) as modify:
            self.assertEqual(roll.main(), 0)
            modify.assert_called_once_with("task", "B", ["roll:", "roll_offset:"])

    def test_zero_and_negative_slack(self):
        base = {"uuid": "A", "status": "pending", "due": "20260101T000000Z"}
        zero = {"uuid": "B", "status": "pending", "roll": "A", "roll_offset": "1d", "roll_fixed": "checkpoint", "due": "20260102T000000Z"}
        late = {"uuid": "C", "status": "pending", "roll": "A", "roll_offset": "2d", "roll_fixed": "finish-line", "due": "20260102T000000Z"}
        _, slack, _ = roll.calculate_schedule([base, zero, late])
        self.assertEqual(slack, {"B": 0.0, "C": -24.0})

    def test_r_mark_distinguishes_roll_and_rock(self):
        self.assertEqual(
            roll.r_mark_for({"roll": "A", "roll_offset": "P2D"}, None), "󰍃 +2d"
        )
        task = {"roll_fixed": "checkpoint"}
        self.assertEqual(roll.r_mark_for(task, None), "󰩈 checkpoint")
        self.assertEqual(
            roll.r_mark_for({"roll_fixed": "finish-line"}, -6), " finish-line"
        )

    def test_roll_r_mark_uses_only_its_explicit_offset(self):
        self.assertEqual(
            roll.r_mark_for({"roll": "A", "roll_offset": "P3D"}, None), "󰍃 +3d"
        )

    def test_refresh_offsets_uses_remaining_and_weekly_capacity(self):
        tasks = [
            {"uuid": "A", "status": "pending", "due": "20260101T000000Z"},
            {"uuid": "B", "status": "pending", "project": "posek.ch", "remaining": "PT10H", "roll": "A", "roll_offset": "P1D"},
        ]
        with patch.object(roll, "project_capacity", return_value=25):
            changes = roll.refresh_offsets(tasks, "task")
        self.assertEqual(changes, {"B": "P2DT19H"})
        due, _, _ = roll.calculate_schedule(tasks)
        self.assertEqual(due["B"], dt.datetime(2026, 1, 3, 19, tzinfo=UTC))

    def test_refresh_offsets_ignores_equivalent_taskwarrior_duration(self):
        tasks = [
            {"uuid": "A", "status": "pending"},
            {"uuid": "B", "status": "pending", "project": "work", "remaining": "PT5H30M",
             "roll": "A", "roll_offset": "PT5H30M"},
        ]
        with patch.object(roll, "project_capacity", return_value=49.5):
            self.assertEqual(roll.refresh_offsets(tasks, "task", {}), {})
        self.assertEqual(tasks[1]["roll_offset"], "PT5H30M")

    def test_main_ignores_slack_rounding_already_saved(self):
        tasks = [{"uuid": "B", "status": "pending", "roll_fixed": "finish-line",
                  "roll_slack": 20.1028, "r_mark": " finish-line"}]
        with patch("sys.stdin", io.StringIO("")), patch.object(
            roll, "task_command", return_value="task"
        ), patch.object(roll, "load_calendar", return_value={}), patch.object(
            roll, "export_tasks", return_value=tasks
        ), patch.object(roll, "refresh_offsets", return_value={}), patch.object(
            roll, "calculate_schedule", return_value=({}, {"B": 20.102778}, [])
        ), patch.object(roll, "apply_modifications") as modify:
            self.assertEqual(roll.main(), 0)
            modify.assert_not_called()

    def test_roll_manual_keeps_explicit_offset_and_still_schedules(self):
        tasks = [
            {"uuid": "A", "status": "pending", "due": "20260101T000000Z"},
            {
                "uuid": "B", "status": "pending", "project": "posek.ch",
                "remaining": "PT10H", "roll": "A", "roll_offset": "P1D",
                "roll_manual": "yes",
            },
        ]
        with patch.object(roll, "project_capacity") as capacity:
            self.assertEqual(roll.refresh_offsets(tasks, "task"), {})
            capacity.assert_not_called()
        due, _, _ = roll.calculate_schedule(tasks)
        self.assertEqual(due["B"], dt.datetime(2026, 1, 2, tzinfo=UTC))

    def test_release_clears_roll_manual(self):
        tasks = [
            {
                "uuid": "A", "status": "completed", "end": "20260102T120000Z",
                "modified": "20260102T120000Z",
            },
            {
                "uuid": "B", "status": "pending", "due": "20260110T000000Z",
                "roll": "A", "roll_offset": "P1D", "roll_manual": "yes",
            },
        ]
        with patch("sys.stdin", io.StringIO(json.dumps({"uuid": "A"}))), patch.object(
            roll, "task_command", return_value="task"
        ), patch.object(roll, "export_tasks", return_value=tasks), patch.object(
            roll, "apply_modifications"
        ) as modify:
            self.assertEqual(roll.main(), 0)
            modify.assert_called_once_with(
                "task", "B", ["due:20260103T120000Z", "roll:", "roll_offset:", "roll_manual:"]
            )

    def test_phoenix_creates_one_same_task_after_completion(self):
        tasks = [{
            "uuid": "A", "status": "completed", "end": "20260102T120000Z",
            "modified": "20260102T120000Z", "description": "Laundry",
            "project": "home", "tags": ["errands", "home"], "phoenix": "PT90M",
            "phoenix_wait": "yes",
            "track_session": "must-not-copy",
        }]
        with patch("sys.stdin", io.StringIO(json.dumps({"uuid": "A"}))), patch.object(
            roll, "task_command", return_value="task"
        ), patch.object(roll, "export_tasks", return_value=tasks), patch.object(
            roll, "task_run"
        ) as task_run:
            self.assertEqual(roll.main(), 0)
        task_run.assert_called_once_with(
            "task", "add", "due:20260102T133000Z", "wait:20260102T133000Z", "project:home", "+errands",
            "+home", "--", "Laundry"
        )

    def test_phoenix_without_wait_remains_visible(self):
        due = dt.datetime(2026, 1, 2, 13, 30, tzinfo=UTC)
        with patch.object(roll, "task_run") as task_run:
            roll.create_phoenix_task("task", {"description": "Laundry"}, due)
        task_run.assert_called_once_with("task", "add", "due:20260102T133000Z", "--", "Laundry")

    def test_phoenix_count_repeats_then_stops(self):
        for count, expected in (
            (2, ("due:20260102T133000Z", "wait:20260102T133000Z", "phoenix:PT90M", "phoenix_count:1", "phoenix_wait:yes", "--", "Laundry")),
            (1, ("due:20260102T133000Z", "wait:20260102T133000Z", "--", "Laundry")),
            (0, None),
            (1.5, None),
        ):
            task = {
                "uuid": "A", "status": "completed", "end": "20260102T120000Z",
                "modified": "20260102T120000Z", "description": "Laundry",
                "phoenix": "PT90M", "phoenix_wait": "yes", "phoenix_count": count,
            }
            with self.subTest(count=count), patch(
                "sys.stdin", io.StringIO(json.dumps({"uuid": "A"}))
            ), patch.object(roll, "task_command", return_value="task"), patch.object(
                roll, "export_tasks", return_value=[task]
            ), patch.object(roll, "task_run") as task_run:
                self.assertEqual(roll.main(), 0)
                if expected is None:
                    task_run.assert_not_called()
                else:
                    task_run.assert_called_once_with("task", "add", *expected)

    def test_phoenix_ignores_completed_task_without_a_completion_event(self):
        tasks = [{
            "uuid": "A", "status": "completed", "end": "20260102T120000Z",
            "modified": "20260102T120001Z", "description": "Laundry", "phoenix": "PT90M",
        }]
        with patch("sys.stdin", io.StringIO(json.dumps({"uuid": "A"}))), patch.object(
            roll, "task_command", return_value="task"
        ), patch.object(roll, "export_tasks", return_value=tasks), patch.object(
            roll, "task_run"
        ) as task_run:
            self.assertEqual(roll.main(), 0)
        task_run.assert_not_called()

    def test_roll_replaces_legacy_mark(self):
        tasks = [
            {"uuid": "A", "status": "pending", "due": "20260101T000000Z"},
            {"uuid": "B", "status": "pending", "roll": "A", "roll_offset": "2d", "roll_mark": "old"},
        ]
        with patch("sys.stdin", io.StringIO(json.dumps({"uuid": "A"}))), patch.object(
            roll, "task_command", return_value="task"
        ), patch.object(roll, "export_tasks", return_value=tasks), patch.object(
            roll, "apply_modifications"
        ) as modify:
            self.assertEqual(roll.main(), 0)
            modify.assert_called_once_with(
                "task", "B", ["due:20260103T000000Z", "r_mark:󰍃 +2d", "roll_mark:"]
            )

    def test_bad_chain_warns_without_due(self):
        tasks = [
            {"uuid": "A", "status": "pending", "roll": "B", "roll_offset": "1d"},
            {"uuid": "B", "status": "pending", "roll": "A", "roll_offset": "1d"},
            {"uuid": "C", "status": "pending", "roll": "missing", "roll_offset": "1d"},
            {"uuid": "D", "status": "pending", "roll": "A"},
        ]
        due, _, warnings = roll.calculate_schedule(tasks)
        self.assertEqual(due, {})
        self.assertTrue(any("cycle" in warning.lower() for warning in warnings))
        self.assertTrue(any("predecessor missing" in warning for warning in warnings))
        self.assertTrue(any("invalid/missing offset" in warning for warning in warnings))

    def test_duration_in_roll_gets_one_actionable_warning(self):
        tasks = [
            {"uuid": "A", "status": "pending", "due": "20260101T000000Z", "roll": "P1D"},
            {"uuid": "B", "status": "pending", "roll": "A", "roll_offset": "P1D"},
        ]
        _, _, warnings = roll.calculate_schedule(tasks)
        self.assertEqual(len(warnings), 1)
        self.assertIn("duration, not a predecessor UUID", warnings[0])
        self.assertIn("modify roll:", warnings[0])

    def test_task_roll_help_explains_roll_and_offset(self):
        result = subprocess.run(
            [sys.executable, "task_roll_help", "help"],
            cwd=Path(__file__).parent.parent,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("roll:<UUID>", result.stdout)
        self.assertIn("roll_offset:<duration>", result.stdout)
        self.assertIn("roll_manual:yes", result.stdout)
        self.assertIn("Never use roll:P1D", result.stdout)
        self.assertIn("task chain NUMBER", result.stdout)
        self.assertIn("CHILD stores both roll and roll_offset", result.stdout)
        self.assertIn("With a calendar, P1D is one 8h15 workday", result.stdout)
        self.assertIn("config/calendar-5787.taskrc", result.stdout)

    def test_roll_view_is_show_alias(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        main = roll_help["main"]
        with patch.dict(main.__globals__, {"show": lambda: 7}):
            self.assertEqual(main(["view"]), 7)

    def test_task_rock_alias_suffix_routes_to_rock(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        main = roll_help["main"]
        with patch.dict(main.__globals__, {"rock": lambda args: len(args)}):
            self.assertEqual(main(["view", "rock"]), 1)

    def test_rock_chain_routes_to_shared_chain_builder(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        with patch.dict(roll_help["rock"].__globals__, {"chain": lambda args, **kwargs: len(args)}):
            self.assertEqual(roll_help["rock"](["chain", "tasks.txt"]), 1)

    def test_roll_show_lists_links_offsets_and_milestones(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [
            {"id": 1, "uuid": "A", "status": "pending", "description": "Draft"},
            {"id": 2, "uuid": "B", "status": "pending", "description": "Review", "roll": "A", "roll_offset": "P2D"},
            {"id": 3, "uuid": "C", "status": "waiting", "description": "Send", "roll": "B", "roll_offset": "PT1H30M", "roll_fixed": "finish-line"},
            {"id": 4, "uuid": "D", "status": "pending", "description": "Approve", "roll": "B", "roll_offset": "PT2H", "roll_fixed": "checkpoint", "r_mark": "| check"},
        ]
        output = roll_help["render_rolls"](tasks)
        self.assertIn("CHILD is due GAP after PREDECESSOR", output)
        self.assertIn("PREDECESSOR", output)
        self.assertIn("1 Draft", output)
        self.assertIn("2d", output)
        self.assertNotIn(" finish-line", output)
        self.assertNotIn("󰩈 checkpoint", output)

    def test_roll_output_uses_three_theme_roles(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        self.assertEqual(roll_help["ansi"]("bold rgb405"), "\033[1;38;5;165m")
        self.assertEqual(roll_help["ansi"]("bold rgb055"), "\033[1;38;5;51m")
        theme = {
            "header": "\033[1m",
            "accent": "\033[35m",
            "child": "\033[36m",
            "warning": "\033[33m",
        }
        tasks = [
            {"id": 1, "uuid": "A", "status": "pending", "description": "Draft"},
            {"id": 2, "uuid": "B", "status": "pending", "description": "Review", "roll": "A", "roll_offset": "P2D"},
        ]
        output = roll_help["render_rolls"](tasks, theme)
        self.assertIn("\033[1mPREDECESSOR", output)
        self.assertIn("\033[35m1\033[0m Draft", output)
        self.assertNotIn("\033[35m2d", output)
        self.assertIn("\033[36m2\033[0m Review", output)
        self.assertNotIn("\033[33m", output)

    def test_roll_show_explains_ignored_root_offset(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [
            {"id": 43, "uuid": "A", "status": "pending", "description": "Chapter", "roll_offset": "P2D"},
        ]
        output = roll_help["render_rolls"](tasks)
        self.assertIn("IGNORED 43 Chapter", output)
        self.assertIn("roll_offset 2d needs roll:<UUID>", output)

    def test_chains_view_keeps_compact_paths(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [
            {"id": 1, "uuid": "A", "status": "pending", "description": "Draft", "due": "20260922T160000Z"},
            {"id": 2, "uuid": "B", "status": "pending", "description": "Review", "due": "20260923T160000Z", "roll": "A", "roll_offset": "P1D"},
            {"id": 3, "uuid": "C", "status": "pending", "description": "Send", "due": "20260924T160000Z", "roll": "B", "roll_offset": "P1D", "roll_fixed": "finish-line"},
            {"id": 4, "uuid": "D", "status": "pending", "description": "Other", "due": "20260925T160000Z", "roll": "B", "roll_offset": "P2D"},
        ]
        output = roll_help["render_chains"](tasks)
        self.assertIn("CHAINS — active Roll paths", output)
        self.assertIn("Each row is one leaf path", output)
        self.assertIn("1 Draft", output)
        self.assertIn("3 Send", output)
        self.assertIn("4 Other", output)
        self.assertIn("READY", output)
        self.assertIn("ROLLING", output)

    def test_task_chain_wrapper_routes_to_browser(self):
        result = subprocess.run(
            ["sh", "task_chain", "--help"], cwd=Path(__file__).parent.parent,
            env={**os.environ, "PATH": f"{Path(__file__).parent.parent}:{os.environ['PATH']}"},
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("task chain NUMBER shows tasks, capacity, and calendar breaks", result.stdout)

    def test_task_chains_wrapper_keeps_view_alias(self):
        result = subprocess.run(
            ["sh", "task_chains", "--help"], cwd=Path(__file__).parent.parent,
            env={**os.environ, "PATH": f"{Path(__file__).parent.parent}:{os.environ['PATH']}"},
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("task chains view", result.stdout)

    def test_rock_plan_prints_one_root_change(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [
            {"id": 1, "uuid": "A", "status": "pending", "description": "Draft"},
            {"id": 2, "uuid": "B", "status": "pending", "description": "Research", "roll": "A", "roll_offset": "P2D"},
            {"id": 3, "uuid": "C", "status": "pending", "description": "Submit", "roll": "B", "roll_offset": "P4D", "roll_fixed": "finish-line", "due": "20260920T000000Z"},
        ]
        plan = roll_help["rock_plan"](tasks, "3")
        self.assertEqual(plan["root"]["uuid"], "A")
        self.assertEqual(str(plan["start"].date()), "2026-09-14")
        output = roll_help["render_rock_plan"](plan)
        self.assertIn("DRY RUN", output)
        self.assertIn("task A modify due:20260914T000000Z", output)

    def test_rock_numeric_id_wins_over_deleted_uuid_prefix(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [
            {"id": 1, "uuid": "root", "status": "pending", "due": "20260923T160000Z"},
            {"id": 29, "uuid": "finish", "status": "pending", "roll": "root",
             "roll_offset": "P1D", "roll_fixed": "finish-line", "due": "20261012T170000Z"},
            {"id": 0, "uuid": "297c773f-deleted", "status": "deleted"},
        ]
        self.assertEqual(roll_help["rock_plan"](tasks, "29")["finish"]["uuid"], "finish")

    def test_rock_stops_at_checkpoint(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [
            {"id": 1, "uuid": "A", "status": "pending", "description": "Check", "roll_fixed": "checkpoint", "due": "20260916T000000Z"},
            {"id": 2, "uuid": "B", "status": "pending", "description": "Submit", "roll": "A", "roll_offset": "P4D", "roll_fixed": "finish-line", "due": "20260920T000000Z"},
        ]
        plan = roll_help["rock_plan"](tasks, "2")
        self.assertNotIn("root", plan)
        self.assertIn("Checkpoint", plan["warnings"][0])
        self.assertIn("CHECKPOINT: 1 Check fixed 2026-09-16", roll_help["rock_status"](plan))
        self.assertIn("full-path capacity is unavailable", roll_help["render_rock_plan"](plan))
        self.assertIn("CHECKPOINT:", roll_help["render_chain_browser"](tasks))
        detail = roll_help["render_chain_details"](tasks, 1)
        self.assertIn("capacity —\nCHECKPOINT:", detail)
        self.assertIn("Backward planning stops here; full-path capacity is unavailable.", detail)
        with self.assertRaisesRegex(ValueError, "blocked"):
            roll_help["apply_rock_plan"](plan, "task")

    def test_rock_checkpoint_wording_distinguishes_early_and_late(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [
            {"id": 1, "uuid": "A", "status": "pending", "description": "Write", "roll_fixed": "checkpoint", "due": "20260925T000000Z"},
            {"id": 2, "uuid": "B", "status": "pending", "description": "Finish", "roll": "A", "roll_offset": "P4D", "roll_fixed": "finish-line", "due": "20261012T000000Z"},
        ]
        plan = roll_help["rock_plan"](tasks, "2")
        self.assertEqual(
            roll_help["rock_status"](plan),
            "CHECKPOINT: 1 Write fixed 2026-09-25; Rock needs it by 2026-10-08 (13 calendar days later).",
        )
        tasks[0]["due"] = "20261010T000000Z"
        plan = roll_help["rock_plan"](tasks, "2")
        self.assertEqual(
            roll_help["rock_status"](plan),
            "WARN: Checkpoint 1 Write fixed 2026-10-10; Rock needs it by 2026-10-08 (2 calendar days earlier).",
        )
        tasks[0]["due"] = "20261008T010000Z"
        plan = roll_help["rock_plan"](tasks, "2")
        self.assertIn("WARN: Checkpoint 1 Write fixed 2026-10-08 01:00 UTC; Rock needs it by 2026-10-08 00:00 UTC", roll_help["rock_status"](plan))

    def test_rock_requires_a_linked_finish_line(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        plan = roll_help["rock_plan"]([{
            "id": 1, "uuid": "A", "status": "pending", "description": "Finish",
            "roll_fixed": "finish-line", "due": "20260920T000000Z",
        }], "1")
        self.assertNotIn("root", plan)
        self.assertIn("Roll predecessor", plan["warnings"][0])

    def test_rock_warns_about_other_root_branch(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [
            {"id": 1, "uuid": "A", "status": "pending", "description": "Root"},
            {"id": 2, "uuid": "B", "status": "pending", "description": "Target", "roll": "A", "roll_offset": "P2D", "roll_fixed": "finish-line", "due": "20260920T000000Z"},
            {"id": 3, "uuid": "C", "status": "pending", "description": "Other", "roll": "A", "roll_offset": "P1D"},
        ]
        plan = roll_help["rock_plan"](tasks, "2")
        self.assertEqual(plan["root"]["uuid"], "A")
        self.assertIn("other active branch", plan["warnings"][0])

    def test_capacity_slack_includes_weekend_days(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [
            {"uuid": "A", "status": "pending", "remaining": "PT10H"},
            {"uuid": "B", "status": "pending", "remaining": "PT10H", "roll": "A", "roll_offset": "P1D", "roll_fixed": "finish-line", "due": "20260920T160000Z"},
        ]
        plan = roll_help["rock_plan"](tasks, "B")
        slack = roll_help["weekday_capacity_slack"](
            plan, 70, dt.datetime(2026, 9, 18, tzinfo=UTC)
        )
        self.assertEqual(slack, 10)

    def test_chain_browser_shows_capacity_and_path_details(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [
            {"id": 1, "uuid": "A", "status": "pending", "description": "Root", "remaining": "PT1H"},
            {"id": 2, "uuid": "B", "status": "pending", "description": "Finish", "project": "posek.ch", "remaining": "PT1H", "roll": "A", "roll_offset": "P1D", "roll_fixed": "finish-line", "due": "20260920T160000Z"},
        ]
        output = roll_help["render_chain_browser"](tasks, capacities={"posek.ch": 70})
        self.assertIn("ROOT", output)
        self.assertIn("TASKS", output)
        self.assertIn("START", output)
        self.assertIn("CAPACITY", output)
        self.assertIn("h  READY", output)
        detail = roll_help["render_chain_details"](tasks, 1, capacities={"posek.ch": 70})
        self.assertIn("CHAIN 1", detail)
        self.assertIn("Root", detail)
        self.assertIn("Finish", detail)
        self.assertIn("ROCK: deadline", detail)

    def test_roll_view_hides_rock_finish_line(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [
            {"id": 1, "uuid": "A", "status": "pending", "description": "Root", "remaining": "PT1H"},
            {"id": 2, "uuid": "B", "status": "pending", "description": "Finish", "project": "posek.ch", "remaining": "PT1H", "roll": "A", "roll_offset": "P1D", "roll_fixed": "finish-line", "due": "20260920T160000Z"},
        ]
        output = roll_help["render_rolls"](tasks)
        self.assertEqual(output, "No active Roll links or milestones.\n")

    def test_rock_apply_updates_only_root(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        plan = {
            "root": {"uuid": "A", "id": 1, "description": "Root"},
            "start": dt.datetime(2026, 9, 14, tzinfo=UTC),
            "warnings": [],
        }
        with patch.object(roll_help["subprocess"], "run") as run:
            roll_help["apply_rock_plan"](plan, "task")
        run.assert_called_once_with(
            ["task", "rc.context=", "A", "modify", "due:20260914T000000Z"],
            check=True,
        )

    def test_rock_apply_refuses_warning(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        plan = {
            "root": {"uuid": "A"},
            "start": dt.datetime(2026, 9, 14, tzinfo=UTC),
            "warnings": ["Other branch"],
        }
        with self.assertRaisesRegex(ValueError, "refuses"):
            roll_help["apply_rock_plan"](plan, "task")

    def test_roll_validate_reports_cycle_and_root_offset(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        warnings = roll_help["validate_tasks"]([
            {"id": 1, "uuid": "A", "status": "pending", "roll": "B", "roll_offset": "P1D"},
            {"id": 2, "uuid": "B", "status": "pending", "roll": "A", "roll_offset": "P1D"},
            {"id": 3, "uuid": "C", "status": "pending", "roll_offset": "P1D"},
        ])
        self.assertTrue(any("cycle" in warning.lower() for warning in warnings))
        self.assertTrue(any("needs roll" in warning for warning in warnings))

    def test_roll_validate_reports_unusable_chain_base(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        warnings = roll_help["validate_tasks"]([
            {"id": 1, "uuid": "A", "status": "completed", "description": "Root"},
            {"id": 2, "uuid": "B", "status": "pending", "description": "Child", "roll": "A", "roll_offset": "P1D"},
            {"id": 3, "uuid": "C", "status": "pending", "description": "Milestone", "roll_fixed": "finish-line"},
        ])
        self.assertTrue(any("no due/end" in warning for warning in warnings))
        self.assertTrue(any("fixed milestone needs due" in warning for warning in warnings))

    def test_chain_candidate_uses_uuid_list_weekdays_and_finish_line(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        uuids = [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            "33333333-3333-3333-3333-333333333333",
        ]
        tasks = [
            {"id": index + 1, "uuid": uuid, "status": "pending", "description": f"write {index}"}
            for index, uuid in enumerate(uuids)
        ]
        tasks[-1]["due"] = "20260924T160000Z"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as source:
            source.write("\n".join(f"{uuid}:write {index}" for index, uuid in enumerate(uuids)))
            source.flush()
            options = roll_help["parse_chain"]([
                source.name, "--start", "2026-09-22", "--finish", "2026-09-24", "--remaining", "40m",
            ])
            candidate, selected = roll_help["chain_candidate"](tasks, options, hard_finish=True)
        by_uuid = {task["uuid"]: task for task in candidate}
        self.assertEqual(selected, uuids)
        self.assertEqual(by_uuid[uuids[0]]["wait"], "2026-09-22")
        self.assertEqual(by_uuid[uuids[1]]["roll_offset"], "P1D")
        self.assertEqual(by_uuid[uuids[-1]]["roll_fixed"], "finish-line")
        self.assertEqual(by_uuid[uuids[-1]]["due"], "20260924T160000Z")
        self.assertEqual(by_uuid[uuids[-1]]["remaining"], "PT40M")
        self.assertEqual(str(roll_help["rock_plan"](candidate, uuids[-1])["start"].date()), "2026-09-22")

    def test_chain_spreads_extra_tasks_without_requiring_one_per_weekday(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        uuids = [f"{index:08d}-1111-1111-1111-111111111111" for index in range(1, 5)]
        tasks = [
            {"id": index, "uuid": uuid, "status": "pending", "description": "write", "remaining": "PT10M"}
            for index, uuid in enumerate(uuids, 1)
        ]
        tasks[-1]["due"] = "20260923T160000Z"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as source:
            source.write("\n".join(f"{uuid}:write" for uuid in uuids))
            source.flush()
            options = roll_help["parse_chain"]([source.name, "--start", "2026-09-22", "--finish", "2026-09-23"])
            candidate, _ = roll_help["chain_candidate"](tasks, options)
        due_dates = [task["due"][:8] for task in candidate]
        self.assertEqual(due_dates[0], "20260922")
        self.assertEqual(due_dates[-1], "20260923")
        self.assertEqual(due_dates.count("20260922"), 2)
        self.assertNotIn("roll_fixed", candidate[-1])

    def test_chain_parser_accepts_stdin_marker(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        options = roll_help["parse_chain"]([
            "-", "--start", "2026-09-22", "--finish", "2026-09-23", "--remaining", "40m",
        ])
        self.assertEqual(options["file"], "-")

    def test_chain_preserves_existing_remaining_without_batch_value(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        uuids = [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ]
        tasks = [
            {"id": 1, "uuid": uuids[0], "status": "pending", "description": "First", "remaining": "PT20M"},
            {"id": 2, "uuid": uuids[1], "status": "pending", "description": "Last", "remaining": "PT1H", "due": "20260923T160000Z"},
        ]
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as source:
            source.write("\n".join(f"{uuid}:task" for uuid in uuids))
            source.flush()
            options = roll_help["parse_chain"]([
                source.name, "--start", "2026-09-22", "--finish", "2026-09-23",
            ])
            candidate, _ = roll_help["chain_candidate"](tasks, options)
        self.assertNotIn("remaining", options)
        self.assertEqual(candidate[0]["remaining"], "PT20M")
        self.assertEqual(candidate[1]["remaining"], "PT1H")

    def test_chain_lists_every_missing_remaining_estimate(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [
            {"id": 1, "uuid": "11111111-1111-1111-1111-111111111111", "status": "pending", "description": "First"},
            {"id": 2, "uuid": "22222222-2222-2222-2222-222222222222", "status": "pending", "description": "Last", "due": "20260923T160000Z"},
        ]
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as source:
            source.write("\n".join(f"{task['uuid']}:task" for task in tasks))
            source.flush()
            options = roll_help["parse_chain"]([
                source.name, "--start", "2026-09-22", "--finish", "2026-09-23",
            ])
            with self.assertRaisesRegex(ValueError, "1 First, 2 Last"):
                roll_help["chain_candidate"](tasks, options)

    def test_chain_help_is_available_from_roll_and_rock(self):
        script = Path(__file__).parent.parent / "task_roll_help"
        for arguments in (("chain", "--help"), ("rock", "chain", "--help")):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, script, *arguments], text=True, capture_output=True, check=False
                )
                self.assertEqual(result.returncode, 0)
                self.assertIn("UUID:description", result.stdout)

    def test_invalid_fixed_value_warns_without_changes(self):
        tasks = [
            {"uuid": "A", "status": "pending", "due": "20260101T000000Z"},
            {
                "uuid": "B",
                "status": "pending",
                "due": "20260110T000000Z",
                "roll": "A",
                "roll_offset": "2d",
                "roll_fixed": "finsh-line",
            },
        ]
        due, slack, warnings = roll.calculate_schedule(tasks)
        self.assertEqual((due, slack), ({}, {}))
        self.assertTrue(any("invalid roll_fixed" in warning for warning in warnings))

if __name__ == "__main__":
    unittest.main()
