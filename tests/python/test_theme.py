from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from omarchy_firefox_bridge.theme import (  # noqa: E402
    COLORS_RELATIVE_PATH,
    read_theme_message,
    watch_theme,
)


class RecordingStream:
    def __init__(self, stream):
        self.stream = stream
        self.events = 0
        self.final_event = None

    @property
    def closed(self):
        return self.stream.closed

    def fileno(self):
        return self.stream.fileno()

    def readline(self):
        line = self.stream.readline()
        if line.rstrip("\n").endswith(" theme"):
            self.events += 1
            if self.events == 2:
                self.final_event = time.monotonic()
        return line

    def close(self):
        self.stream.close()


class ThemeTest(unittest.TestCase):
    def test_uses_only_the_omarchy_4_state_path(self):
        self.assertEqual(
            COLORS_RELATIVE_PATH,
            Path(".local/state/omarchy/current/theme/colors.toml"),
        )

    def test_reads_a_complete_theme_message(self):
        path = ROOT / "tests" / "fixtures" / "dark-colors.toml"
        message = read_theme_message(path)
        self.assertEqual(message["type"], "theme")
        self.assertEqual(message["theme"]["frame"], "#120231")
        self.assertEqual(message["theme"]["popup_highlight"], "#59306f")
        self.assertEqual(message["theme"]["popup_highlight_text"], "#f2e9ff")

    def test_missing_malformed_or_incomplete_palette_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "colors.toml"
            self.assertIsNone(read_theme_message(path))
            path.write_text("not = [toml")
            self.assertIsNone(read_theme_message(path))
            path.write_text('background="#000000"\n')
            self.assertIsNone(read_theme_message(path))
            for selection in ('1', '"#fff"', '"#gg0000"'):
                path.write_text(
                    'mode="dark"\nbackground="#000000"\n'
                    'foreground="#ffffff"\naccent="#ff0000"\n'
                    f"selection={selection}\n"
                )
                self.assertIsNone(read_theme_message(path))

    def test_watcher_terminates_its_inotify_child_when_stopped(self):
        stop = threading.Event()
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.PIPE,
            text=True,
        )
        watcher = threading.Thread(
            target=watch_theme,
            args=(stop, lambda: None, lambda *args, **kwargs: child),
        )
        try:
            watcher.start()
            time.sleep(0.05)
            stop.set()
            watcher.join(timeout=1)
            self.assertFalse(watcher.is_alive())
            self.assertIsNotNone(child.poll())
            self.assertTrue(child.stdout.closed)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            if child.stdout is not None and not child.stdout.closed:
                child.stdout.close()

    def test_watcher_uses_trailing_edge_debounce_for_event_bursts(self):
        stop = threading.Event()
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; "
                "print('/tmp MOVED_TO theme', flush=True); "
                "time.sleep(0.03); "
                "print('/tmp MOVED_TO theme', flush=True); "
                "time.sleep(0.3)",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        child.stdout = RecordingStream(child.stdout)
        changes = []
        watcher = threading.Thread(
            target=watch_theme,
            args=(
                stop,
                lambda: changes.append(time.monotonic()),
                lambda *args, **kwargs: child,
            ),
        )
        try:
            watcher.start()
            watcher.join(timeout=1)
            self.assertFalse(watcher.is_alive())
            self.assertEqual(len(changes), 1)
            self.assertIsNotNone(child.stdout.final_event)
            self.assertGreaterEqual(changes[0] - child.stdout.final_event, 0.15)
            self.assertLess(changes[0] - child.stdout.final_event, 0.3)
            self.assertTrue(child.stdout.closed)
        finally:
            stop.set()
            if child.poll() is None:
                child.kill()
            child.wait()
            if child.stdout is not None and not child.stdout.closed:
                child.stdout.close()


if __name__ == "__main__":
    unittest.main()
