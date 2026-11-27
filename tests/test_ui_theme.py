import unittest

from ui_theme import (
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


if __name__ == "__main__":
    unittest.main()
