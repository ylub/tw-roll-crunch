import datetime as dt
import io
import json
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

    def test_checkpoint_display_is_glyph_only(self):
        task = {"roll_fixed": "checkpoint"}
        self.assertEqual(roll.roll_mark_for(task, None), "\U000f0a48")

    def test_longest_dotted_capacity_match(self):
        values = {"roll.capacity.client": "20", "roll.capacity.client.special": "5"}
        getter = lambda _command, key: values.get(key)
        self.assertEqual(
            roll.capacity_for_project("task", "client.special.deep", config_getter=getter),
            (5.0, "client.special"),
        )
        self.assertEqual(
            roll.capacity_for_project("task", "client.other", config_getter=getter),
            (20.0, "client"),
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
