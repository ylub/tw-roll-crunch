import datetime as dt
import unittest

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

    def test_zero_and_negative_slack(self):
        base = {"uuid": "A", "status": "pending", "due": "20260101T000000Z"}
        zero = {"uuid": "B", "status": "pending", "roll": "A", "roll_offset": "1d", "roll_fixed": "checkpoint", "due": "20260102T000000Z"}
        late = {"uuid": "C", "status": "pending", "roll": "A", "roll_offset": "2d", "roll_fixed": "finish-line", "due": "20260102T000000Z"}
        _, slack, _ = roll.calculate_schedule([base, zero, late])
        self.assertEqual(slack, {"B": 0.0, "C": -24.0})

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
