import unittest

from llm_harness.gui_display import (
    MAX_WINDOW,
    MIN_WINDOW,
    FontScheme,
    format_window_geometry,
    parse_window_geometry,
    preferred_window_geometry,
)


class WindowGeometryTests(unittest.TestCase):
    def test_window_scales_with_the_screen_instead_of_a_constant(self):
        small = preferred_window_geometry(1366, 768)
        large = preferred_window_geometry(3840, 2160)

        self.assertEqual(small, MIN_WINDOW)
        self.assertEqual(large, MAX_WINDOW)
        self.assertGreater(large[0], small[0])

    def test_a_screen_smaller_than_the_minimum_still_gets_a_window_that_fits(self):
        width, height = preferred_window_geometry(800, 600)

        self.assertLess(width, 800)
        self.assertLess(height, 600)
        self.assertLess(width, MIN_WINDOW[0])

    def test_mid_size_screen_uses_a_fraction_of_the_screen(self):
        width, height = preferred_window_geometry(1920, 1200)

        self.assertEqual((width, height), (int(1920 * 0.72), int(1200 * 0.72)))

    def test_remembered_geometry_round_trips(self):
        self.assertEqual(parse_window_geometry(format_window_geometry(1400, 900)), (1400, 900))

    def test_malformed_or_unusable_geometry_is_ignored(self):
        for value in ("", "   ", "wide", "1400", "1400x", "x900", "10x10", "99999x99999"):
            self.assertIsNone(parse_window_geometry(value), value)


class FontSchemeTests(unittest.TestCase):
    def test_real_semibold_family_is_used_directly(self):
        scheme = FontScheme("Segoe UI", "Segoe UI Semibold", True)

        self.assertEqual(scheme.regular_font(10), ("Segoe UI", 10))
        self.assertEqual(scheme.emphasis_font(12), ("Segoe UI Semibold", 12))

    def test_without_a_semibold_family_emphasis_falls_back_to_bold(self):
        scheme = FontScheme("DejaVu Sans", "DejaVu Sans", False)

        self.assertEqual(scheme.emphasis_font(12), ("DejaVu Sans", 12, "bold"))


if __name__ == "__main__":
    unittest.main()
