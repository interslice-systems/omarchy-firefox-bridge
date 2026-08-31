from io import BytesIO
import json
from pathlib import Path
import struct
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from omarchy_firefox_bridge.framing import (  # noqa: E402
    FrameError,
    NativeWriter,
    encode_message,
    read_message,
)


class FramingTest(unittest.TestCase):
    def test_round_trips_utf8_json(self):
        frame = encode_message({"title": "café"})
        self.assertEqual(read_message(BytesIO(frame)), {"title": "café"})

    def test_rejects_short_header_and_body(self):
        with self.assertRaisesRegex(FrameError, "short native header"):
            read_message(BytesIO(b"\x01\x00"))
        with self.assertRaisesRegex(FrameError, "short native body"):
            read_message(BytesIO(struct.pack("<I", 10) + b"{}"))

    def test_rejects_oversized_frame_before_body_read(self):
        with self.assertRaisesRegex(FrameError, "exceeds 16 bytes"):
            read_message(BytesIO(struct.pack("<I", 17)), max_bytes=16)

    def test_rejects_invalid_utf8_json_and_non_object(self):
        for body in (b"\xff", b"not-json", b"[]"):
            with self.subTest(body=body), self.assertRaises(FrameError):
                read_message(BytesIO(struct.pack("<I", len(body)) + body))

    def test_writer_keeps_concurrent_theme_and_tab_frames_decodable(self):
        stream = BytesIO()
        writer = NativeWriter(stream)
        messages = [
            {"type": "theme", "theme": {"sequence": index}}
            if index % 2 == 0
            else {"type": "tabs.list", "requestId": f"request-{index}"}
            for index in range(20)
        ]
        threads = [
            threading.Thread(target=writer.send, args=(message,))
            for message in messages
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        stream.seek(0)
        decoded = [read_message(stream) for _ in range(20)]
        self.assertCountEqual(decoded, messages)

    def test_encode_enforces_application_to_extension_limit(self):
        with self.assertRaisesRegex(FrameError, "exceeds 32 bytes"):
            encode_message({"data": "x" * 64}, max_bytes=32)


if __name__ == "__main__":
    unittest.main()
