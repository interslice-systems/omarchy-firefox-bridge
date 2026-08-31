"""Bounded Firefox native-messaging framing."""
from __future__ import annotations

from io import BufferedIOBase
import json
import struct
import threading
from typing import BinaryIO

MAX_NATIVE_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_NATIVE_REQUEST_BYTES = 1024 * 1024


class FrameError(ValueError):
    """A native frame is malformed or outside the bridge bounds."""


def read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(
    stream: BinaryIO,
    max_bytes: int = MAX_NATIVE_RESPONSE_BYTES,
) -> dict:
    header = read_exact(stream, 4)
    if len(header) != 4:
        raise FrameError("short native header")
    (size,) = struct.unpack("<I", header)
    if size > max_bytes:
        raise FrameError(f"native body exceeds {max_bytes} bytes")
    body = read_exact(stream, size)
    if len(body) != size:
        raise FrameError("short native body")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FrameError("invalid native JSON") from error
    if not isinstance(value, dict):
        raise FrameError("native message must be an object")
    return value


def encode_message(message: dict, max_bytes: int = MAX_NATIVE_REQUEST_BYTES) -> bytes:
    body = json.dumps(
        message,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(body) > max_bytes:
        raise FrameError(f"native body exceeds {max_bytes} bytes")
    return struct.pack("<I", len(body)) + body


class NativeWriter:
    """Serialize complete frames from theme and socket threads."""

    def __init__(self, stream: BinaryIO | BufferedIOBase) -> None:
        self.stream = stream
        self.lock = threading.Lock()

    def send(self, message: dict) -> None:
        frame = encode_message(message)
        with self.lock:
            self.stream.write(frame)
            self.stream.flush()
