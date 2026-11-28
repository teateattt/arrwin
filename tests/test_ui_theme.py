import unittest

from ui_theme import (
    SystemThemeFollower,
    hex_to_rgb,
    normalize_theme_preference,
    resolve_theme_mode,
)


class ThemePreferenceTests(unittest.TestCase):
    def test_normalizes_supported_preferences(self):
        self.assertEqual(normalize_theme_preference("SYSTEM"), "system")
        self.assertEqual(normalize_theme_preference(" dark "), "dark")
        self.assertEqual(normalize_theme_preference("Light"), "light")

    def test_invalid_preference_falls_back_to_system(self):
        self.assertEqual(normalize_theme_preference("sepia"), "system")
        self.assertEqual(normalize_theme_preference(None), "system")

    def test_explicit_preference_wins(self):
        self.assertEqual(
            resolve_theme_mode("dark", system_theme="light"),
            "dark",
        )
        self.assertEqual(
            resolve_theme_mode("light", system_theme="dark"),
            "light",
        )

    def test_system_preference_uses_detected_theme(self):
        self.assertEqual(
            resolve_theme_mode("system", system_theme="dark"),
            "dark",
        )
        self.assertEqual(
            resolve_theme_mode("system", system_theme="light"),
            "light",
        )

    def test_unknown_system_theme_falls_back_to_light(self):
        self.assertEqual(
            resolve_theme_mode("system", system_theme="unknown"),
            "light",
        )

    def test_hex_color_conversion(self):
        self.assertEqual(hex_to_rgb("#1c2d3e"), (28, 45, 62))
        self.assertEqual(hex_to_rgb("ffffff"), (255, 255, 255))


class FakeRoot:
    def __init__(self):
        self.next_id = 1
        self.scheduled = {}
        self.cancelled = []

    def after(self, delay, callback):
        after_id = self.next_id
        self.next_id += 1
        self.scheduled[after_id] = (delay, callback)
        return after_id

    def after_cancel(self, after_id):
        self.cancelled.append(after_id)
        self.scheduled.pop(after_id, None)


class SystemThemeFollowerTests(unittest.TestCase):
    def setUp(self):
        self.root = FakeRoot()
        self.changes = []
        self.current_theme = "light"

    def _create_follower(self, initial_mode="light"):
        return SystemThemeFollower(
            self.root,
            preference="system",
            on_change=self.changes.append,
            initial_mode=initial_mode,
            theme_reader=lambda: self.current_theme,
            interval_ms=1500,
        )

    def test_callback_only_runs_when_theme_changes(self):
        follower = self._create_follower()
        self.assertEqual(follower.sync_now(), "light")
        self.current_theme = "dark"
        self.assertEqual(follower.sync_now(), "dark")
        self.assertEqual(follower.sync_now(), "dark")
        self.assertEqual(self.changes, ["dark"])

    def test_suspend_cancels_pending_poll(self):
        follower = self._create_follower()
        follower.start()
        scheduled_id = follower._after_id
        self.assertIsNotNone(scheduled_id)
        follower.suspend()
        self.assertFalse(follower.active)
        self.assertIsNone(follower._after_id)
        self.assertEqual(self.root.cancelled, [scheduled_id])

    def test_resume_can_sync_before_scheduling(self):
        follower = self._create_follower()
        follower.suspend()
        self.current_theme = "dark"
        follower.resume(refresh_now=True)
        self.assertTrue(follower.active)
        self.assertEqual(self.changes, ["dark"])
        self.assertEqual(len(self.root.scheduled), 1)


if __name__ == "__main__":
    unittest.main()
