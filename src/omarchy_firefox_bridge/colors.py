"""Map an Omarchy colors.toml palette to Firefox theme colors."""
from __future__ import annotations

import re


COLOR_KEYS = (
    "background",
    "foreground",
    "accent",
    "selection_background",
    "selection_foreground",
)
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def _wcag_ch(value: float) -> float:
    channel = value / 255
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[int, int, int]) -> float:
    red, green, blue = rgb
    return 0.2126 * _wcag_ch(red) + 0.7152 * _wcag_ch(green) + 0.0722 * _wcag_ch(blue)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    bounded = (max(0, min(255, int(round(channel)))) for channel in rgb)
    return "#{:02x}{:02x}{:02x}".format(*bounded)


def _mix(
    left: tuple[int, int, int] | tuple[float, float, float],
    right: tuple[int, int, int],
    amount: float,
) -> tuple[float, float, float]:
    return tuple(left[index] * (1 - amount) + right[index] * amount for index in range(3))


def _lift(
    rgb: tuple[int, int, int] | tuple[float, float, float],
    amount: float,
    is_dark: bool,
) -> tuple[float, float, float]:
    target = (255, 255, 255) if is_dark else (0, 0, 0)
    return _mix(rgb, target, amount)


def omarchy_to_firefox_theme(palette: dict) -> dict:
    """Convert a parsed Omarchy palette to `browser.theme.update` colors."""
    colors = {}
    for key in COLOR_KEYS:
        value = palette[key]
        if not isinstance(value, str) or HEX_COLOR.fullmatch(value) is None:
            raise ValueError(f"{key} must be a six-digit hex color")
        colors[key] = value
    background = _hex_to_rgb(colors["background"])
    foreground = _hex_to_rgb(colors["foreground"])
    foreground_hex = colors["foreground"]
    accent_hex = colors["accent"]
    selection_background = colors["selection_background"]
    selection_foreground = colors["selection_foreground"]
    is_dark = _luminance(background) < _luminance(foreground)
    frame = background
    toolbar = _lift(background, 0.05, is_dark)
    field = _lift(background, 0.10, is_dark)
    return {
        "frame": _rgb_to_hex(frame),
        "frame_inactive": _rgb_to_hex(_lift(frame, 0.03, is_dark)),
        "toolbar": _rgb_to_hex(toolbar),
        "toolbar_text": foreground_hex,
        "toolbar_field": _rgb_to_hex(field),
        "toolbar_field_text": foreground_hex,
        "toolbar_field_focus": _rgb_to_hex(_lift(field, 0.05, is_dark)),
        "toolbar_field_border": _rgb_to_hex(_lift(field, 0.10, is_dark)),
        "toolbar_field_border_focus": accent_hex,
        "toolbar_top_separator": _rgb_to_hex(_lift(frame, 0.10, is_dark)),
        "toolbar_bottom_separator": _rgb_to_hex(_lift(toolbar, 0.10, is_dark)),
        "tab_background_text": foreground_hex,
        "tab_text": foreground_hex,
        "tab_selected": _rgb_to_hex(toolbar),
        "tab_line": accent_hex,
        "popup": _rgb_to_hex(toolbar),
        "popup_text": foreground_hex,
        "popup_highlight": selection_background,
        "popup_highlight_text": selection_foreground,
        "sidebar": _rgb_to_hex(toolbar),
        "sidebar_text": foreground_hex,
        "sidebar_highlight": accent_hex,
        "sidebar_highlight_text": selection_foreground,
        "icons": foreground_hex,
        "icons_attention": accent_hex,
        "button_background_hover": _rgb_to_hex(_lift(toolbar, 0.08, is_dark)),
        "button_background_active": _rgb_to_hex(_lift(toolbar, 0.15, is_dark)),
        "ntp_background": colors["background"],
        "ntp_text": foreground_hex,
    }
