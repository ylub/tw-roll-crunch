import datetime as dt
import unittest

from hooks import crunch


NOW = dt.datetime(2026, 1, 10, tzinfo=dt.timezone.utc)


class CrunchTests(unittest.TestCase):
    def test_levels(self):
        cases = (
            ({"duration": "PT4H", "progress": 10}, "LOW"),
            ({"duration": "PT1H", "progress": 0}, "MED"),
            ({"duration": "PT16H", "progress": 50, "due": "20260111T000000Z"}, "HIGH"),
            ({"duration": "PT1H", "progress": 0, "due": "20260109T000000Z"}, "CRITICAL"),
        )
        for task, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(crunch.crunch_level(task, NOW), expected)

    def test_no_score_unsets_crunch(self):
        task = {"duration": "PT4H", "progress": 25, "crunch": "HIGH", "crunch_display": "old"}
        self.assertEqual(crunch.update_task(task, NOW), {"duration": "PT4H", "progress": 25})

    def test_missing_duration_unsets_crunch(self):
        task = {"crunch": "LOW", "crunch_display": "old"}
        self.assertEqual(crunch.update_task(task, NOW), {})

    def test_optional_tag_does_not_reduce_level(self):
        task = {"duration": "PT1H", "progress": 0, "due": "20260109T000000Z", "tags": ["optional"]}
        self.assertEqual(crunch.crunch_level(task, NOW), "CRITICAL")

    def test_zero_remaining_work_has_no_crunch(self):
        self.assertIsNone(crunch.crunch_level({"duration": "PT4H", "progress": 500}, NOW))


if __name__ == "__main__":
    unittest.main()
