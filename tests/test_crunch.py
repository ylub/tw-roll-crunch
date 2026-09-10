import datetime as dt
import unittest

from hooks import crunch


NOW = dt.datetime(2026, 1, 10, tzinfo=dt.timezone.utc)


class CrunchTests(unittest.TestCase):
    def test_levels(self):
        cases = (
            ({"remaining": "PT4H", "progress": 10}, "LOW"),
            ({"remaining": "PT1H", "progress": 0}, "MED"),
            ({"remaining": "PT16H", "progress": 50, "due": "20260111T000000Z"}, "HIGH"),
            ({"remaining": "PT1H", "progress": 0, "due": "20260109T000000Z"}, "CRITICAL"),
        )
        for task, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(crunch.crunch_level(task, NOW), expected)

    def test_no_score_unsets_crunch(self):
        task = {"remaining": "PT4H", "progress": 25, "crunch": "HIGH", "crunch_display": "old"}
        self.assertEqual(crunch.update_task(task, NOW), {"remaining": "PT4H", "progress": 25})

    def test_missing_remaining_unsets_crunch(self):
        task = {"crunch": "LOW", "crunch_display": "old"}
        self.assertEqual(crunch.update_task(task, NOW), {})

    def test_optional_tag_does_not_reduce_level(self):
        task = {"remaining": "PT1H", "progress": 0, "due": "20260109T000000Z", "tags": ["optional"]}
        self.assertEqual(crunch.crunch_level(task, NOW), "CRITICAL")

    def test_zero_remaining_work_has_no_crunch(self):
        self.assertIsNone(crunch.crunch_level({"remaining": "PT0S", "progress": 50}, NOW))

    def test_progress_does_not_reduce_remaining(self):
        task = {"remaining": "PT8H", "progress": 100, "due": "20260111T000000Z"}
        self.assertEqual(crunch.crunch_level(task, NOW), "HIGH")

    def test_high_display_uses_clock_alert_glyph(self):
        task = {"remaining": "PT16H", "progress": 50, "due": "20260111T000000Z"}
        self.assertEqual(crunch.update_task(task, NOW)["crunch_display"], "\U000f0955 HIGH")


if __name__ == "__main__":
    unittest.main()
