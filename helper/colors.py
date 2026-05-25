"""Map an Omarchy colors.toml palette to a Firefox WebExtension theme dict.

Omarchy uses an ANSI 16-color terminal palette plus a few semantic keys
(accent, background, foreground, cursor, selection_*). Firefox themes expect
~30 named slots covering frame, toolbar, tabs, popups, sidebar, etc.

This module detects dark vs light theme by comparing WCAG relative luminance
of background vs foreground, computes shaded variants by linear-RGB mixing
toward white (dark themes) or black (light themes), and routes `accent` to
highlight slots (focus borders, active tab line, sidebar highlight) rather
than chrome fills — Omarchy accents are tuned for highlighting, not filling.
"""
from __future__ import annotations


def _wcag_ch(c: float) -> float:
    s = c / 255
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.2126 * _wcag_ch(r) + 0.7152 * _wcag_ch(g) + 0.0722 * _wcag_ch(b)


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        *(max(0, min(255, int(round(c)))) for c in rgb)
    )


def _mix(a, b, t: float):
    return tuple(a[i] * (1 - t) + b[i] * t for i in range(3))


def _lift(rgb, amount: float, is_dark: bool):
    target = (255, 255, 255) if is_dark else (0, 0, 0)
    return _mix(rgb, target, amount)


def omarchy_to_firefox_theme(palette: dict) -> dict:
    """Convert a parsed colors.toml dict to a Firefox theme.update() colors dict."""
    bg = _hex_to_rgb(palette["background"])
    fg = _hex_to_rgb(palette["foreground"])
    fg_hex = palette["foreground"]
    accent_hex = palette["accent"]
    sel_bg_hex = palette["selection_background"]
    sel_fg_hex = palette["selection_foreground"]

    is_dark = _luminance(bg) < _luminance(fg)

    frame = bg
    toolbar = _lift(bg, 0.05, is_dark)
    field = _lift(bg, 0.10, is_dark)

    return {
        "frame": _rgb_to_hex(frame),
        "frame_inactive": _rgb_to_hex(_lift(frame, 0.03, is_dark)),
        "toolbar": _rgb_to_hex(toolbar),
        "toolbar_text": fg_hex,
        "toolbar_field": _rgb_to_hex(field),
        "toolbar_field_text": fg_hex,
        "toolbar_field_focus": _rgb_to_hex(_lift(field, 0.05, is_dark)),
        "toolbar_field_border": _rgb_to_hex(_lift(field, 0.10, is_dark)),
        "toolbar_field_border_focus": accent_hex,
        "toolbar_top_separator": _rgb_to_hex(_lift(frame, 0.10, is_dark)),
        "toolbar_bottom_separator": _rgb_to_hex(_lift(toolbar, 0.10, is_dark)),
        "tab_background_text": fg_hex,
        "tab_text": fg_hex,
        "tab_selected": _rgb_to_hex(toolbar),
        "tab_line": accent_hex,
        "popup": _rgb_to_hex(toolbar),
        "popup_text": fg_hex,
        "popup_highlight": sel_bg_hex,
        "popup_highlight_text": sel_fg_hex,
        "sidebar": _rgb_to_hex(toolbar),
        "sidebar_text": fg_hex,
        "sidebar_highlight": accent_hex,
        "sidebar_highlight_text": sel_fg_hex,
        "icons": fg_hex,
        "icons_attention": accent_hex,
        "button_background_hover": _rgb_to_hex(_lift(toolbar, 0.08, is_dark)),
        "button_background_active": _rgb_to_hex(_lift(toolbar, 0.15, is_dark)),
        "ntp_background": palette["background"],
        "ntp_text": fg_hex,
    }
