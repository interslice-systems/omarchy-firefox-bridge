import base64
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from omarchy_firefox_bridge.protocol import (  # noqa: E402
    GROUP_COLORS,
    ProtocolError,
    snapshot_ids,
    valid_request_id,
    validate_activation,
    validate_open_request,
    validate_snapshot,
)


def tab(**changes):
    value = {
        "tabId": 7,
        "windowId": 4,
        "index": 0,
        "title": "Issue 401",
        "displayUrl": "example.com/issues/401",
        "favicon": "",
        "active": True,
    }
    value.update(changes)
    return value


class ProtocolTest(unittest.TestCase):
    def test_request_id_is_bounded_printable_ascii(self):
        self.assertTrue(valid_request_id("a" * 64))
        for value in ("", "a" * 65, "é", "line\nbreak", None, 3):
            with self.subTest(value=value):
                self.assertFalse(valid_request_id(value))

    def test_validates_and_copies_projected_snapshot(self):
        original = [
            tab(tabId=9, windowId=2, index=0),
            tab(tabId=8, windowId=1, index=1, active=False),
            tab(tabId=7, windowId=1, index=0),
        ]
        validated = validate_snapshot(original)
        self.assertEqual(
            [(value["windowId"], value["index"]) for value in validated],
            [(1, 0), (1, 1), (2, 0)],
        )
        self.assertIsNot(validated[0], original[2])
        self.assertEqual(
            snapshot_ids(validated),
            frozenset({(1, 7), (1, 8), (2, 9)}),
        )

    def test_rejects_raw_or_unknown_fields(self):
        for field in ("url", "pendingUrl", "cookieStoreId"):
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                validate_snapshot([tab(**{field: "secret"})])

    def test_rejects_missing_projected_field(self):
        value = tab()
        del value["favicon"]
        with self.assertRaises(ProtocolError):
            validate_snapshot([value])

    def test_accepts_explicit_empty_snapshot(self):
        original = []
        validated = validate_snapshot(original)
        self.assertEqual(validated, [])
        self.assertIsNot(validated, original)

    def test_bounds_astral_unicode_by_code_point(self):
        accepted = "😀" * 1024
        self.assertEqual(validate_snapshot([tab(title=accepted)])[0]["title"], accepted)
        with self.assertRaises(ProtocolError):
            validate_snapshot([tab(title="😀" * 1025)])

    def test_bounds_favicon_by_decoded_bytes(self):
        accepted = "data:image/png;base64," + base64.b64encode(b"x" * 65536).decode()
        rejected = "data:image/png;base64," + base64.b64encode(b"x" * 65537).decode()
        self.assertEqual(
            validate_snapshot([tab(favicon=accepted)])[0]["favicon"], accepted
        )
        with self.assertRaisesRegex(ProtocolError, "exceeds 65536 decoded bytes"):
            validate_snapshot([tab(favicon=rejected)])

    def test_rejects_invalid_types_lengths_and_favicon(self):
        cases = [
            tab(tabId=True),
            tab(tabId=0),
            tab(windowId=-1),
            tab(windowId=0),
            tab(index=-1),
            tab(index="0"),
            tab(title="x" * 1025),
            tab(displayUrl="x" * 4097),
            tab(active=1),
            tab(favicon="https://example.com/icon.png"),
            tab(favicon="data:image/svg+xml;base64,PHN2Zz4="),
            tab(favicon="data:image/png;base64,"),
            tab(favicon="data:image/png;base64,YQ"),
            tab(favicon="data:image/png;base64," + "A" * 87384),
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                validate_snapshot([value])

    def test_rejects_duplicate_ids_indexes_and_invalid_active_counts(self):
        cases = [
            [tab(), tab(tabId=7, index=1, active=False)],
            [tab(), tab(tabId=8, index=0, active=False)],
            [tab(active=False)],
            [tab(), tab(tabId=8, index=1, active=True)],
        ]
        for tabs in cases:
            with self.subTest(tabs=tabs), self.assertRaises(ProtocolError):
                validate_snapshot(tabs)

    def test_activation_requires_positive_integer_ids(self):
        self.assertEqual(validate_activation(4, 7), (4, 7))
        for values in ((True, 7), (4, False), (-1, 7), (0, 7), (4, 0), (4, "7")):
            with self.subTest(values=values), self.assertRaises(ProtocolError):
                validate_activation(*values)


def open_request(**changes):
    value = {"url": "https://example.com/a", "toplevelTitle": "Page — Mozilla Firefox"}
    value.update(changes)
    return {key: item for key, item in value.items() if item is not None}


class ValidateOpenRequestTests(unittest.TestCase):
    def test_accepts_a_minimal_request(self):
        self.assertEqual(
            validate_open_request(open_request()),
            {"url": "https://example.com/a", "toplevelTitle": "Page — Mozilla Firefox"},
        )

    def test_accepts_mixed_case_schemes(self):
        result = validate_open_request(open_request(url="HtTpS://example.com/"))
        self.assertEqual(result["url"], "HtTpS://example.com/")

    def test_rejects_non_http_schemes(self):
        for url in (
            "javascript:alert(1)",
            "data:text/html,x",
            "file:///etc/passwd",
            "about:config",
            "ftp://example.com/",
            "//example.com/",
        ):
            with self.subTest(url=url), self.assertRaises(ProtocolError):
                validate_open_request(open_request(url=url))

    def test_rejects_control_characters_rather_than_normalising(self):
        for url in ("https://example.com/\n", "https://exa\tmple.com/", "https://x.com/\x7f"):
            with self.subTest(url=url), self.assertRaises(ProtocolError):
                validate_open_request(open_request(url=url))

    def test_rejects_lone_surrogates(self):
        with self.assertRaises(ProtocolError):
            validate_open_request(open_request(url="https://example.com/\ud800"))

    def test_enforces_length_bounds_in_code_points(self):
        validate_open_request(open_request(url="https://e.com/" + "a" * (4096 - 14)))
        with self.assertRaises(ProtocolError):
            validate_open_request(open_request(url="https://e.com/" + "a" * (4097 - 14)))

    def test_accepts_four_byte_code_points_up_to_the_limit(self):
        url = "https://e.com/" + "\U0001f600" * (4096 - 14)
        self.assertEqual(validate_open_request(open_request(url=url))["url"], url)

    def test_rejects_empty_or_missing_or_unknown_fields(self):
        for value in (
            {"url": "https://e.com/"},
            {"toplevelTitle": "t"},
            open_request(url=""),
            {**open_request(), "extra": 1},
            "not-a-dict",
            None,
        ):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                validate_open_request(value)

    def test_accepts_a_group_with_and_without_colour(self):
        self.assertEqual(
            validate_open_request(open_request(group={"title": "oracle"}))["group"],
            {"title": "oracle"},
        )
        self.assertEqual(
            validate_open_request(
                open_request(group={"title": "oracle", "color": "yellow"})
            )["group"],
            {"title": "oracle", "color": "yellow"},
        )

    def test_accepts_glob_characters_in_a_group_title(self):
        # The JS side filters literally, so * and ? are data, not wildcards.
        result = validate_open_request(open_request(group={"title": "we*rd?name"}))
        self.assertEqual(result["group"]["title"], "we*rd?name")

    def test_rejects_group_titles_outside_the_canonical_charset(self):
        for title in ("has space", "has.dot", "has:colon", "has/slash", "has#hash", "", "a" * 65):
            with self.subTest(title=title), self.assertRaises(ProtocolError):
                validate_open_request(open_request(group={"title": title}))

    def test_rejects_colours_outside_the_allowlist(self):
        self.assertEqual(len(GROUP_COLORS), 9)
        self.assertIn("grey", GROUP_COLORS)
        self.assertNotIn("gray", GROUP_COLORS)
        for color in ("gray", "chartreuse", "", 1, None):
            with self.subTest(color=color), self.assertRaises(ProtocolError):
                validate_open_request(open_request(group={"title": "oracle", "color": color}))

    def test_rejects_group_with_unknown_or_missing_fields(self):
        for group in ({"color": "red"}, {"title": "a", "extra": 1}, "oracle", 7):
            with self.subTest(group=group), self.assertRaises(ProtocolError):
                validate_open_request(open_request(group=group))


if __name__ == "__main__":
    unittest.main()
