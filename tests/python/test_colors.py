from pathlib import Path
import sys
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from omarchy_firefox_bridge.colors import (  # noqa: E402
    _hex_to_rgb,
    _luminance,
    omarchy_to_firefox_theme,
)


class ColorMappingTest(unittest.TestCase):
    def fixture(self, name: str) -> dict:
        with (ROOT / "tests" / "fixtures" / name).open("rb") as stream:
            return tomllib.load(stream)

    def test_preserves_dark_theme_mapping(self):
        theme = omarchy_to_firefox_theme(self.fixture("dark-colors.toml"))
        self.assertEqual(theme["frame"], "#120231")
        self.assertEqual(theme["toolbar"], "#1e0f3b")
        self.assertEqual(theme["toolbar_field"], "#2a1b46")
        self.assertEqual(theme["toolbar_field_focus"], "#34274f")
        self.assertEqual(theme["tab_line"], "#ff70a6")
        self.assertEqual(theme["popup_highlight"], "#59306f")
        self.assertEqual(theme["popup_highlight_text"], "#f2e9ff")
        self.assertEqual(theme["sidebar_highlight"], "#ff70a6")
        self.assertEqual(theme["sidebar_highlight_text"], "#f2e9ff")
        self.assertEqual(len(theme), 29)

    def test_darkens_light_theme_toolbar(self):
        theme = omarchy_to_firefox_theme(self.fixture("light-colors.toml"))
        frame = _luminance(_hex_to_rgb(theme["frame"]))
        toolbar = _luminance(_hex_to_rgb(theme["toolbar"]))
        self.assertLess(toolbar, frame)
        self.assertEqual(theme["toolbar_field_border_focus"], "#0066cc")

    def test_missing_selection_fails_closed(self):
        palette = self.fixture("dark-colors.toml")
        del palette["selection"]
        with self.assertRaises(KeyError):
            omarchy_to_firefox_theme(palette)

    def test_required_colors_are_strict_six_digit_hex(self):
        for key in ("background", "foreground", "accent", "selection"):
            for value in (1, None, "#fff", "#gg0000"):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    palette = self.fixture("dark-colors.toml")
                    palette[key] = value
                    omarchy_to_firefox_theme(palette)


if __name__ == "__main__":
    unittest.main()
