from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from omarchy_firefox_bridge.protocol import (  # noqa: E402
    ProtocolError,
    snapshot_ids,
    valid_request_id,
    validate_activation,
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


if __name__ == "__main__":
    unittest.main()
