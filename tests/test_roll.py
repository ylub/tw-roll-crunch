import datetime as dt
import io
import json
import runpy
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from hooks import roll


UTC = dt.timezone.utc


class RollTests(unittest.TestCase):
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
            roll.r_mark_for({"roll_fixed": "finish-line"}, -6), " finish-line -6h"
        )

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
        self.assertIn("Never use roll:P1D", result.stdout)
        self.assertIn("task roll view", result.stdout)
        self.assertIn("CHILD stores both roll and roll_offset", result.stdout)
        self.assertIn("44 is due 2 days after task 43", result.stdout)

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
        self.assertIn("1h 30m", output)
        self.assertIn(" finish-line", output)
        self.assertIn("󰩈 checkpoint", output)

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
            {"id": 2, "uuid": "B", "status": "pending", "description": "Review", "roll": "A", "roll_offset": "P2D", "roll_fixed": "checkpoint"},
        ]
        output = roll_help["render_rolls"](tasks, theme)
        self.assertIn("\033[1mPREDECESSOR", output)
        self.assertNotIn("\033[35m2d", output)
        self.assertIn("\033[36m2\033[0m Review", output)
        self.assertIn("\033[33m󰩈 checkpoint", output)

    def test_roll_show_explains_ignored_root_offset(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [
            {"id": 43, "uuid": "A", "status": "pending", "description": "Chapter", "roll_offset": "P2D"},
        ]
        output = roll_help["render_rolls"](tasks)
        self.assertIn("IGNORED 43 Chapter", output)
        self.assertIn("roll_offset 2d needs roll:<UUID>", output)

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

    def test_rock_stops_at_checkpoint(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [
            {"id": 1, "uuid": "A", "status": "pending", "description": "Check", "roll_fixed": "checkpoint", "due": "20260916T000000Z"},
            {"id": 2, "uuid": "B", "status": "pending", "description": "Submit", "roll": "A", "roll_offset": "P4D", "roll_fixed": "finish-line", "due": "20260920T000000Z"},
        ]
        plan = roll_help["rock_plan"](tasks, "2")
        self.assertNotIn("root", plan)
        self.assertIn("Checkpoint", plan["warnings"][0])

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

    def test_rock_view_command_exports_and_renders(self):
        roll_help = runpy.run_path(str(Path(__file__).parent.parent / "task_roll_help"))
        tasks = [{
            "id": 1, "uuid": "A", "status": "pending", "description": "Finish",
            "roll_fixed": "finish-line", "due": "20260920T000000Z",
        }]
        result = subprocess.CompletedProcess([], 0, json.dumps(tasks), "")
        with patch.object(roll_help["subprocess"], "run", return_value=result) as run, patch(
            "sys.stdout", new_callable=io.StringIO
        ) as output:
            self.assertEqual(roll_help["rock"](["view"]), 0)
            self.assertEqual(roll_help["rock"](["show"]), 0)
        self.assertIn("ROCK — plan from a solid deadline", output.getvalue())
        self.assertIn(" FINISH-LINE", output.getvalue())
        self.assertEqual(run.call_args.args[0][-1], "export")

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
