"""Read and watch Omarchy 4's atomically replaced current theme."""
from __future__ import annotations

import os
from pathlib import Path
import select
import subprocess
import threading
import time
import tomllib
from typing import Callable

from .colors import omarchy_to_firefox_theme

COLORS_RELATIVE_PATH = Path(".local/state/omarchy/current/theme/colors.toml")
DEBOUNCE_SECONDS = 0.15


def colors_path() -> Path:
    return Path.home() / COLORS_RELATIVE_PATH


def read_theme_message(path: Path | None = None) -> dict | None:
    path = path or colors_path()
    try:
        with path.open("rb") as stream:
            palette = tomllib.load(stream)
        return {"type": "theme", "theme": omarchy_to_firefox_theme(palette)}
    except (FileNotFoundError, tomllib.TOMLDecodeError, KeyError, ValueError):
        return None


def watch_theme(
    stop: threading.Event,
    changed: Callable[[], None],
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> None:
    current = colors_path().parents[1]
    try:
        process = popen(
            ["inotifywait", "-mq", "-e", "moved_to,create", str(current)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        return
    assert process.stdout is not None
    deadline = None
    try:
        while not stop.is_set():
            wait = 0.1
            if deadline is not None:
                wait = min(wait, max(0.0, deadline - time.monotonic()))
            readable, _, _ = select.select([process.stdout], [], [], wait)
            if readable:
                line = process.stdout.readline()
                if line == "":
                    break
                parts = line.rstrip("\n").split(maxsplit=2)
                if len(parts) == 3 and parts[2] == "theme":
                    deadline = time.monotonic() + DEBOUNCE_SECONDS
            if deadline is not None and time.monotonic() >= deadline:
                deadline = None
                changed()
    finally:
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        finally:
            process.stdout.close()
