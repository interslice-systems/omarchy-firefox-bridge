from io import BytesIO
import json
from pathlib import Path
import struct
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from omarchy_firefox_bridge.framing import (  # noqa: E402
    FrameError,
    NativeWriter,
    encode_message,
    read_message,
)


class FragmentedReader:
    def __init__(self, data):
        self.stream = BytesIO(data)

    def read(self, size):
        return self.stream.read(min(size, 1))


class ShortWriter:
    def __init__(self, limit=3, yield_writes=False):
        self.data = bytearray()
        self.limit = limit
        self.yield_writes = yield_writes
        self.flushed = False

    def write(self, data):
        written = min(len(data), self.limit)
        self.data.extend(data[:written])
        if self.yield_writes:
            time.sleep(0)
        return written

    def flush(self):
        self.flushed = True


class NoProgressWriter:
    def __init__(self, result):
        self.result = result
        self.flushed = False

    def write(self, _data):
        return self.result

    def flush(self):
        self.flushed = True


class FramingTest(unittest.TestCase):
    def test_round_trips_utf8_json(self):
        frame = encode_message({"title": "café"})
        self.assertEqual(read_message(BytesIO(frame)), {"title": "café"})

    def test_distinguishes_clean_eof_from_short_header(self):
        with self.assertRaises(EOFError):
            read_message(BytesIO())
        for header in (b"\x01", b"\x01\x00", b"\x01\x00\x00"):
            with self.subTest(header=header), self.assertRaisesRegex(
                FrameError, "short native header"
            ):
                read_message(BytesIO(header))

    def test_rejects_short_body(self):
        with self.assertRaisesRegex(FrameError, "short native body"):
            read_message(BytesIO(struct.pack("<I", 10) + b"{}"))

    def test_rejects_oversized_frame_before_body_read(self):
        with self.assertRaisesRegex(FrameError, "exceeds 16 bytes"):
            read_message(BytesIO(struct.pack("<I", 17)), max_bytes=16)

    def test_rejects_invalid_utf8_json_and_non_object(self):
        for body in (b"\xff", b"not-json", b"[]"):
            with self.subTest(body=body), self.assertRaises(FrameError):
                read_message(BytesIO(struct.pack("<I", len(body)) + body))

    def test_rejects_non_finite_json_constants(self):
        for constant in (b"NaN", b"Infinity", b"-Infinity"):
            body = b'{"value":' + constant + b"}"
            with self.subTest(constant=constant), self.assertRaisesRegex(
                FrameError, "invalid native JSON"
            ):
                read_message(BytesIO(struct.pack("<I", len(body)) + body))

    def test_encode_rejects_non_finite_numbers(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaisesRegex(
                FrameError, "invalid native JSON"
            ):
                encode_message({"value": value})

    def test_reads_fragmented_header_and_body(self):
        frame = encode_message({"type": "tabs.result", "tabs": []})
        self.assertEqual(
            read_message(FragmentedReader(frame)),
            {"type": "tabs.result", "tabs": []},
        )

    def test_writer_completes_short_writes(self):
        stream = ShortWriter()
        writer = NativeWriter(stream)
        message = {"type": "tabs.list", "requestId": "request-1"}
        writer.send(message)
        self.assertTrue(stream.flushed)
        self.assertEqual(read_message(BytesIO(stream.data)), message)

    def test_writer_rejects_writes_that_make_no_progress(self):
        for result in (None, 0, -1):
            stream = NoProgressWriter(result)
            with self.subTest(result=result), self.assertRaisesRegex(
                FrameError, "made no progress"
            ):
                NativeWriter(stream).send({"type": "tabs.list"})
            self.assertFalse(stream.flushed)

    def test_writer_keeps_concurrent_theme_and_tab_frames_decodable(self):
        stream = ShortWriter(limit=1, yield_writes=True)
        writer = NativeWriter(stream)
        messages = [
            {"type": "theme", "theme": {"sequence": index}}
            if index % 2 == 0
            else {"type": "tabs.list", "requestId": f"request-{index}"}
            for index in range(20)
        ]
        errors = []

        def send(message):
            try:
                writer.send(message)
            except Exception as error:  # pragma: no cover - asserted below
                errors.append(error)

        threads = [
            threading.Thread(target=send, args=(message,)) for message in messages
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertTrue(stream.flushed)
        decoded_stream = BytesIO(stream.data)
        decoded = [read_message(decoded_stream) for _ in range(20)]
        self.assertCountEqual(decoded, messages)
        with self.assertRaises(EOFError):
            read_message(decoded_stream)

    def test_encode_enforces_application_to_extension_limit(self):
        with self.assertRaisesRegex(FrameError, "exceeds 32 bytes"):
            encode_message({"data": "x" * 64}, max_bytes=32)


if __name__ == "__main__":
    unittest.main()
