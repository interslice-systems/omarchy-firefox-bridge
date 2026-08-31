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


class ThemeTest(unittest.TestCase):
    def test_uses_only_the_omarchy_4_state_path(self):
        self.assertEqual(
            COLORS_RELATIVE_PATH,
            Path(".local/state/omarchy/current/theme/colors.toml"),
        )

    def test_reads_a_complete_theme_message(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "colors.toml"
            path.write_text(
                'background="#000000"\nforeground="#ffffff"\naccent="#ff0000"\n'
                'selection_background="#333333"\nselection_foreground="#ffffff"\n'
            )
            message = read_theme_message(path)
            self.assertEqual(message["type"], "theme")
            self.assertEqual(message["theme"]["frame"], "#000000")

    def test_missing_malformed_or_incomplete_palette_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "colors.toml"
            self.assertIsNone(read_theme_message(path))
            path.write_text("not = [toml")
            self.assertIsNone(read_theme_message(path))
            path.write_text('background="#000000"\n')
            self.assertIsNone(read_theme_message(path))
            for background in ('1', '"#fff"', '"#gg0000"'):
                path.write_text(
                    f"background={background}\n"
                    'foreground="#ffffff"\naccent="#ff0000"\n'
                    'selection_background="#333333"\n'
                    'selection_foreground="#ffffff"\n'
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
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

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
        changes = []
        started = time.monotonic()
        watcher = threading.Thread(
            target=watch_theme,
            args=(stop, lambda: changes.append(time.monotonic()), lambda *args, **kwargs: child),
        )
        try:
            watcher.start()
            watcher.join(timeout=1)
            self.assertFalse(watcher.is_alive())
            self.assertEqual(len(changes), 1)
            self.assertGreaterEqual(changes[0] - started, 0.15)
            self.assertLess(changes[0] - started, 0.35)
        finally:
            stop.set()
            if child.poll() is None:
                child.kill()
            child.wait()


if __name__ == "__main__":
    unittest.main()
