#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(/usr/bin/dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
tmp=
cleanup() {
  [[ -z ${tmp:-} ]] || rm -rf -- "$tmp"
}
trap cleanup EXIT
tmp=$(mktemp -d)

if [[ ${OMARCHY_FIREFOX_BRIDGE_TEST_SETUP_FAILURE:-0} == 1 ]]; then
  printf '%s\n' "$tmp" > "${OMARCHY_FIREFOX_BRIDGE_TEST_TMP_RECORD:?}"
  exit 73
fi

/usr/bin/python3 - "$repo" "$tmp" <<'PY'
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest


REPO = Path(sys.argv[1])
TEMP_ROOT = Path(sys.argv[2])
UID = os.geteuid()
SOURCE_FILES = (
    "__init__.py",
    "client.py",
    "colors.py",
    "framing.py",
    "host.py",
    "protocol.py",
    "sandbox_probe.py",
    "socket_server.py",
    "theme.py",
)
ACTIVE_PATHS = (
    ".local/lib/omarchy-firefox-bridge",
    ".local/bin/omarchy-firefox-bridge",
    ".mozilla/native-messaging-hosts/omarchy_firefox_bridge.json",
    ".config/omarchy/hooks/theme-set.d/omarchy-firefox-bridge",
)
LEGACY_PATHS = (
    ".local/bin/omarchy-firefox-theme-helper",
    ".mozilla/native-messaging-hosts/omarchy_firefox_theme.json",
    ".config/omarchy/hooks/theme-set.d/firefox-color-sync",
)


def write_file(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    path.chmod(mode)


def copy_file(source, destination, mode):
    write_file(destination, source.read_bytes(), mode)


def metadata(path):
    info = path.lstat()
    kind = "directory" if stat.S_ISDIR(info.st_mode) else "file"
    if stat.S_ISLNK(info.st_mode):
        kind = "symlink"
    return kind, stat.S_IMODE(info.st_mode), info.st_uid


def snapshot(path):
    if not path.exists() and not path.is_symlink():
        return None
    info = path.lstat()
    result = {
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
    }
    if stat.S_ISLNK(info.st_mode):
        result.update(type="symlink", target=os.readlink(path))
    elif stat.S_ISREG(info.st_mode):
        result.update(type="file", data=path.read_bytes().hex())
    elif stat.S_ISDIR(info.st_mode):
        result.update(
            type="directory",
            children={child.name: snapshot(child) for child in sorted(path.iterdir())},
        )
    else:
        result.update(type="other")
    return result


class InstallerTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-", dir=TEMP_ROOT))
        self.fixture_repo = self.make_repo()
        self.usr_bin = self.make_usr_bin()
        self.command_log = self.root / "commands.log"

    def make_repo(self):
        destination = self.root / "repo"
        for directory in (
            "bin",
            "hooks",
            "libexec",
            "src/omarchy_firefox_bridge",
            "tests/shell",
            "extension",
        ):
            (destination / directory).mkdir(parents=True, exist_ok=True)
        copy_file(REPO / "install.sh", destination / "install.sh", 0o755)
        copy_file(
            REPO / "hooks/omarchy-firefox-bridge",
            destination / "hooks/omarchy-firefox-bridge",
            0o755,
        )
        copy_file(
            REPO / "bin/omarchy-firefox-bridge",
            destination / "bin/omarchy-firefox-bridge",
            0o755,
        )
        copy_file(
            REPO / "libexec/omarchy-firefox-bridge-sandbox",
            destination / "libexec/omarchy-firefox-bridge-sandbox",
            0o755,
        )
        for name in SOURCE_FILES:
            copy_file(
                REPO / "src/omarchy_firefox_bridge" / name,
                destination / "src/omarchy_firefox_bridge" / name,
                0o644,
            )
        write_file(destination / "extension/manifest.json", "{}\n")
        write_file(
            destination / "src/omarchy_firefox_bridge/__pycache__/ignored.pyc",
            b"ignored cache",
        )
        write_file(
            destination / "src/omarchy_firefox_bridge/ignored-extra.txt",
            "ignored extra",
            0o600,
        )
        os.symlink(
            self.root / "outside-source",
            destination / "src/omarchy_firefox_bridge/ignored-link",
        )
        return destination

    def make_usr_bin(self):
        destination = self.root / "usr-bin"
        destination.mkdir()
        os.symlink("/usr/bin/python3", destination / "python3")
        for command in ("bwrap", "firefox", "inotifywait", "pacman", "web-ext"):
            write_file(
                destination / command,
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                f"printf '{command} %s\\n' \"$*\" >> \"$TEST_COMMAND_LOG\"\n"
                "exit 0\n",
                0o755,
            )
        write_file(
            destination / "omarchy",
            """#!/usr/bin/env bash
set -euo pipefail
printf 'omarchy %s\n' "$*" >> "$TEST_COMMAND_LOG"
if [[ ${1:-} == version ]]; then
  printf '%s\n' "${TEST_OMARCHY_VERSION:-4.0.1-1}"
elif [[ ${1:-} == hook && ${2:-} == install && ${3:-} == theme-set ]]; then
  printf 'begin %s\n' "$BASHPID" >> "$TEST_HOOK_LOG"
  if [[ ${TEST_HOOK_FAILURE:-0} == 1 ]]; then
    exit 42
  fi
  [[ -z ${TEST_HOOK_DELAY:-} ]] || /usr/bin/sleep "$TEST_HOOK_DELAY"
  /usr/bin/python3 - "$4" "$HOME/.config/omarchy/hooks/theme-set.d/$(/usr/bin/basename "$4")" <<'HOOK'
from pathlib import Path
import os
import sys

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_bytes(source.read_bytes())
destination.chmod(0o755)
HOOK
  if [[ ${TEST_HOOK_INTERRUPT:-0} == 1 ]]; then
    printf 'interrupt %s target %s\n' "$BASHPID" "$OMARCHY_FIREFOX_BRIDGE_TEST_INSTALLER_PID" >> "$TEST_HOOK_LOG"
    kill -TERM "${OMARCHY_FIREFOX_BRIDGE_TEST_INSTALLER_PID:?}"
    /usr/bin/sleep 1
  fi
  printf 'end %s\n' "$BASHPID" >> "$TEST_HOOK_LOG"
else
  exit 64
fi
""",
            0o755,
        )
        return destination

    def make_home(self, name="home"):
        home = self.root / name
        home.mkdir(mode=0o700)
        return home

    def environment(self, home, **updates):
        environment = os.environ.copy()
        environment.update(
            HOME=str(home),
            PATH=f"{self.usr_bin}:/usr/bin",
            TEST_COMMAND_LOG=str(self.command_log),
            TEST_HOOK_LOG=str(self.root / "hook.log"),
            OMARCHY_FIREFOX_BRIDGE_INSTALL_TESTING="1",
            OMARCHY_FIREFOX_BRIDGE_TEST_USR_BIN=str(self.usr_bin),
        )
        environment.pop("XDG_STATE_HOME", None)
        for key, value in updates.items():
            if value is None:
                environment.pop(key, None)
            else:
                environment[key] = str(value)
        return environment

    def run_install(self, home, *, expected=0, repo=None, process_umask=None, **updates):
        fixture_repo = repo or self.fixture_repo
        result = subprocess.run(
            ["/usr/bin/bash", str(fixture_repo / "install.sh")],
            cwd=self.root,
            env=self.environment(home, **updates),
            text=True,
            capture_output=True,
            timeout=15,
            preexec_fn=(None if process_umask is None else lambda: os.umask(process_umask)),
        )
        self.assertEqual(
            result.returncode,
            expected,
            msg=(
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}\n"
                f"hook log:\n{(self.root / 'hook.log').read_text() if (self.root / 'hook.log').exists() else '<absent>'}"
            ),
        )
        return result

    def assert_regular(self, path, mode, expected_bytes=None):
        kind, actual_mode, owner = metadata(path)
        self.assertEqual(kind, "file", path)
        self.assertEqual(actual_mode, mode, path)
        self.assertEqual(owner, UID, path)
        if expected_bytes is not None:
            self.assertEqual(path.read_bytes(), expected_bytes, path)

    def assert_directory(self, path, mode):
        kind, actual_mode, owner = metadata(path)
        self.assertEqual(kind, "directory", path)
        self.assertEqual(actual_mode, mode, path)
        self.assertEqual(owner, UID, path)

    def assert_installed(self, home):
        library = home / ".local/lib/omarchy-firefox-bridge"
        expected_inventory = {
            ".",
            "libexec",
            "libexec/omarchy-firefox-bridge-sandbox",
            "src",
            "src/omarchy_firefox_bridge",
            *(f"src/omarchy_firefox_bridge/{name}" for name in SOURCE_FILES),
        }
        actual_inventory = {"."}
        actual_inventory.update(
            str(path.relative_to(library))
            for path in library.rglob("*")
        )
        self.assertEqual(actual_inventory, expected_inventory)
        for directory in (
            library,
            library / "libexec",
            library / "src",
            library / "src/omarchy_firefox_bridge",
        ):
            self.assert_directory(directory, 0o755)
        self.assert_regular(
            library / "libexec/omarchy-firefox-bridge-sandbox",
            0o755,
            self.fixture_repo.joinpath("libexec/omarchy-firefox-bridge-sandbox").read_bytes(),
        )
        for name in SOURCE_FILES:
            self.assert_regular(
                library / "src/omarchy_firefox_bridge" / name,
                0o644,
                self.fixture_repo.joinpath("src/omarchy_firefox_bridge", name).read_bytes(),
            )
        launcher = home / ".local/bin/omarchy-firefox-bridge"
        self.assert_regular(
            launcher,
            0o755,
            self.fixture_repo.joinpath("bin/omarchy-firefox-bridge").read_bytes(),
        )
        hook = home / ".config/omarchy/hooks/theme-set.d/omarchy-firefox-bridge"
        self.assert_regular(
            hook,
            0o755,
            self.fixture_repo.joinpath("hooks/omarchy-firefox-bridge").read_bytes(),
        )
        manifest_path = home / ".mozilla/native-messaging-hosts/omarchy_firefox_bridge.json"
        self.assert_regular(manifest_path, 0o600)
        self.assert_directory(manifest_path.parent, 0o700)
        self.assertEqual(
            json.loads(manifest_path.read_text()),
            {
                "name": "omarchy_firefox_bridge",
                "description": "Omarchy Firefox Bridge native host",
                "path": str(launcher),
                "type": "stdio",
                "allowed_extensions": ["omarchy-bridge@interslice.systems"],
            },
        )
        expected_manifest = (
            "{\n"
            '  "name": "omarchy_firefox_bridge",\n'
            '  "description": "Omarchy Firefox Bridge native host",\n'
            f'  "path": "{launcher}",\n'
            '  "type": "stdio",\n'
            '  "allowed_extensions": [\n'
            '    "omarchy-bridge@interslice.systems"\n'
            "  ]\n"
            "}\n"
        ).encode()
        self.assertEqual(manifest_path.read_bytes(), expected_manifest)
        for relative in LEGACY_PATHS:
            path = home / relative
            self.assertFalse(path.exists() or path.is_symlink(), path)
        lock = home / ".local/state/omarchy-firefox-bridge/install.lock"
        self.assert_regular(lock, 0o600)
        self.assert_directory(lock.parent, 0o700)

    def assert_no_debris(self, home):
        for parent in (
            home / ".local/lib",
            home / ".local/bin",
            home / ".mozilla/native-messaging-hosts",
            home / ".config/omarchy/hooks/theme-set.d",
        ):
            if not parent.exists():
                continue
            leftovers = [
                child.name
                for child in parent.iterdir()
                if ".stage." in child.name or ".backup." in child.name
            ]
            self.assertEqual(leftovers, [], parent)

    def seed_prior_install(self, home):
        library = home / ACTIVE_PATHS[0]
        write_file(library / "old/nested", "old library", 0o640)
        os.symlink("nested", library / "old/link")
        write_file(home / ACTIVE_PATHS[1], "old launcher", 0o700)
        write_file(home / ACTIVE_PATHS[2], "old manifest", 0o600)
        write_file(home / ACTIVE_PATHS[3], "old hook", 0o711)
        write_file(home / LEGACY_PATHS[0], "legacy helper", 0o755)
        write_file(home / LEGACY_PATHS[1], "legacy manifest", 0o600)
        write_file(home / LEGACY_PATHS[2], "legacy hook", 0o755)

    def managed_snapshot(self, home):
        return {
            relative: snapshot(home / relative)
            for relative in (*ACTIVE_PATHS, *LEGACY_PATHS)
        }

    def test_happy_path_is_exact_idempotent_and_retires_legacy(self):
        home = self.make_home()
        self.seed_prior_install(home)
        predictable = home / ".mozilla/native-messaging-hosts/omarchy_firefox_bridge.json.tmp"
        write_file(predictable, "stale predictable temp", 0o640)

        first = self.run_install(home)
        lock = home / ".local/state/omarchy-firefox-bridge/install.lock"
        self.assertTrue(lock.exists(), lock)
        first_inode = lock.stat().st_ino
        second = self.run_install(home)

        self.assert_installed(home)
        self.assert_no_debris(home)
        self.assertEqual(lock.stat().st_ino, first_inode)
        self.assertEqual(predictable.read_text(), "stale predictable temp")
        self.assertEqual(
            (first.stdout + second.stdout).count("Local bridge installation complete"),
            2,
        )
        hook = home / ACTIVE_PATHS[3]
        hook_result = subprocess.run(
            [str(hook), "tokyo-night"],
            env={
                **os.environ,
                "HOME": str(home),
                "XDG_STATE_HOME": str(home / ".local/state"),
            },
            text=True,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(hook_result.returncode, 0, hook_result.stderr)
        breadcrumb = home / ".local/state/omarchy-firefox-bridge/last-theme-set"
        self.assert_regular(breadcrumb, 0o600)
        self.assertIn("\ttokyo-night\n", breadcrumb.read_text())
        commands = self.command_log.read_text().splitlines()
        self.assertEqual(
            [line.split()[1:4] for line in commands if line.startswith("omarchy hook")],
            [["hook", "install", "theme-set"], ["hook", "install", "theme-set"]],
        )
        self.assertFalse(any(line.startswith(("bwrap ", "firefox ", "inotifywait ", "pacman ", "web-ext ")) for line in commands))

    def test_stale_final_entry_types_are_replaced_without_following(self):
        home = self.make_home()
        outside = self.root / "outside"
        write_file(outside / "launcher", "outside launcher")
        write_file(outside / "manifest", "outside manifest")
        (outside / "library").mkdir(parents=True)
        write_file(outside / "library/sentinel", "outside library")
        for path in (home / ".local/bin", home / ".local/lib", home / ".mozilla/native-messaging-hosts", home / ".config/omarchy/hooks/theme-set.d"):
            path.mkdir(parents=True, exist_ok=True)
        os.symlink(outside / "library", home / ACTIVE_PATHS[0])
        (home / ACTIVE_PATHS[1]).mkdir()
        write_file(home / ACTIVE_PATHS[1] / "nested", "stale launcher directory")
        os.symlink(outside / "manifest", home / ACTIVE_PATHS[2])
        (home / ACTIVE_PATHS[3]).mkdir()
        write_file(home / ACTIVE_PATHS[3] / "nested", "stale hook directory")

        self.run_install(home)

        self.assert_installed(home)
        self.assertEqual((outside / "launcher").read_text(), "outside launcher")
        self.assertEqual((outside / "manifest").read_text(), "outside manifest")
        self.assertEqual((outside / "library/sentinel").read_text(), "outside library")

    def test_restrictive_umask_cannot_change_installed_modes(self):
        home = self.make_home()

        self.run_install(home, process_umask=0o077)

        self.assert_installed(home)

    def test_restrictive_stale_directories_are_rejected_before_mutation(self):
        for kind in ("root", "nested"):
            with self.subTest(kind=kind):
                home = self.make_home(f"restrictive-stale-{kind}")
                library = home / ACTIVE_PATHS[0]
                nested = library / "old/restricted"
                write_file(nested / "sentinel", "preserve", 0o640)
                restricted = library if kind == "root" else nested
                before = snapshot(library)
                if kind == "root":
                    before["mode"] = 0o000
                else:
                    before["children"]["old"]["children"]["restricted"]["mode"] = 0o000
                restricted.chmod(0o000)
                try:
                    result = self.run_install(home, expected=1)
                    self.assertNotIn("Local bridge installation complete", result.stdout)
                    self.assertTrue(library.is_dir() and not library.is_symlink())
                    restricted_mode = stat.S_IMODE(restricted.lstat().st_mode)
                    self.assertEqual(restricted_mode, 0o000)
                    self.assertFalse((home / ".local/state").exists())
                    self.assert_no_debris(home)
                    restricted.chmod(0o700)
                    after = snapshot(library)
                    if kind == "root":
                        after["mode"] = restricted_mode
                    else:
                        after["children"]["old"]["children"]["restricted"]["mode"] = restricted_mode
                    self.assertEqual(after, before)
                finally:
                    def make_accessible(path):
                        if not path.exists() or path.is_symlink():
                            return
                        if path.is_dir():
                            path.chmod(0o700)
                            for child in path.iterdir():
                                make_accessible(child)

                    make_accessible(home)

    def test_fresh_hook_failure_leaves_only_persistent_lock_state(self):
        home = self.make_home()

        result = self.run_install(home, expected=1, TEST_HOOK_FAILURE="1")

        self.assertNotIn("Local bridge installation complete", result.stdout)
        inventory = {
            str(path.relative_to(home))
            for path in home.rglob("*")
        }
        self.assertEqual(
            inventory,
            {
                ".local",
                ".local/state",
                ".local/state/omarchy-firefox-bridge",
                ".local/state/omarchy-firefox-bridge/install.lock",
            },
        )
        self.assert_regular(
            home / ".local/state/omarchy-firefox-bridge/install.lock",
            0o600,
        )
        self.assert_no_debris(home)

    def test_predictable_manifest_temp_file_directory_and_symlink_are_irrelevant(self):
        for kind in ("file", "directory", "symlink"):
            with self.subTest(kind=kind):
                home = self.make_home(f"home-{kind}")
                parent = home / ".mozilla/native-messaging-hosts"
                parent.mkdir(parents=True)
                predictable = parent / "omarchy_firefox_bridge.json.tmp"
                outside = self.root / f"outside-{kind}"
                if kind == "file":
                    write_file(predictable, "stale file", 0o640)
                elif kind == "directory":
                    write_file(predictable / "sentinel", "stale directory")
                else:
                    write_file(outside, "external sentinel")
                    os.symlink(outside, predictable)
                before = snapshot(predictable)

                self.run_install(home)

                self.assertEqual(snapshot(predictable), before)
                if kind == "symlink":
                    self.assertEqual(outside.read_text(), "external sentinel")
                self.assert_installed(home)

    def test_home_and_managed_component_boundaries_fail_closed(self):
        outside = self.root / "outside-home"
        write_file(outside / "sentinel", "preserve")
        home_link = self.root / "home-link"
        os.symlink(outside, home_link)
        missing = self.root / "missing-home"
        cases = (
            ("", {}),
            ("relative-home", {}),
            (str(missing), {}),
            (str(home_link), {}),
            (str(self.make_home("wrong-owner")), {"OMARCHY_FIREFOX_BRIDGE_TEST_EXPECT_UID": UID + 1}),
            (str(self.make_home("root-seam")), {"OMARCHY_FIREFOX_BRIDGE_TEST_EUID": 0}),
        )
        for value, updates in cases:
            with self.subTest(home=value, updates=updates):
                result = self.run_install(value, expected=1, **updates)
                self.assertNotIn("Local bridge installation complete", result.stdout)
        self.assertEqual((outside / "sentinel").read_text(), "preserve")
        self.assertFalse((self.root / "relative-home").exists())

        components = (
            ".local",
            ".local/bin",
            ".local/lib",
            ".local/state",
            ".local/state/omarchy-firefox-bridge",
            ".local/state/omarchy-firefox-bridge/install.lock",
            ".mozilla",
            ".mozilla/native-messaging-hosts",
            ".config",
            ".config/omarchy",
            ".config/omarchy/hooks",
            ".config/omarchy/hooks/theme-set.d",
        )
        for index, relative in enumerate(components):
            with self.subTest(component=relative):
                home = self.make_home(f"component-{index}")
                external = self.root / f"external-{index}"
                external.mkdir()
                write_file(external / "sentinel", "preserve")
                path = home / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if relative.endswith("/install.lock"):
                    path.parent.chmod(0o700)
                os.symlink(external, path)
                result = self.run_install(home, expected=1)
                self.assertNotIn("Local bridge installation complete", result.stdout)
                self.assertEqual((external / "sentinel").read_text(), "preserve")
                self.assertEqual(list(external.iterdir()), [external / "sentinel"])

    def test_legacy_directories_and_required_source_symlinks_are_refused(self):
        for index, relative in enumerate(LEGACY_PATHS):
            with self.subTest(legacy=relative):
                home = self.make_home(f"legacy-directory-{index}")
                legacy = home / relative
                write_file(legacy / "sentinel", "preserve")
                before = snapshot(legacy)
                result = self.run_install(home, expected=1)
                self.assertNotIn("Local bridge installation complete", result.stdout)
                self.assertEqual(snapshot(legacy), before)
                for active in ACTIVE_PATHS:
                    path = home / active
                    self.assertFalse(path.exists() or path.is_symlink())

        home = self.make_home("source-symlink")
        source = self.fixture_repo / "src/omarchy_firefox_bridge/host.py"
        source.unlink()
        os.symlink(REPO / "src/omarchy_firefox_bridge/host.py", source)
        result = self.run_install(home, expected=1)
        self.assertNotIn("Local bridge installation complete", result.stdout)
        for active in ACTIVE_PATHS:
            path = home / active
            self.assertFalse(path.exists() or path.is_symlink())

    def test_exact_runtime_paths_missing_command_version_and_shadowing(self):
        home = self.make_home()
        (self.usr_bin / "firefox").unlink()
        result = self.run_install(home, expected=1)
        self.assertIn(f"Missing runtime command: {self.usr_bin / 'firefox'}", result.stderr)
        self.assertFalse((home / ".local").exists())

        write_file(self.usr_bin / "firefox", "#!/usr/bin/env bash\nexit 0\n", 0o755)
        home = self.make_home("omarchy-3")
        result = self.run_install(home, expected=1, TEST_OMARCHY_VERSION="3.8.4")
        self.assertIn("Omarchy 4 is required; found 3.8.4", result.stderr)
        self.assertFalse((home / ".local").exists())

        malicious = self.root / "malicious-bin"
        malicious.mkdir()
        marker = self.root / "malicious-omarchy-ran"
        write_file(
            malicious / "omarchy",
            f"#!/usr/bin/env bash\nprintf ran > {marker}\nexit 99\n",
            0o755,
        )
        home = self.make_home("shadowing")
        result = self.run_install(home, PATH=f"{malicious}:{self.usr_bin}:/usr/bin")
        self.assertFalse(marker.exists(), result.stderr)
        self.assert_installed(home)
        source = self.fixture_repo.joinpath("install.sh").read_text()
        self.assertIn('usr_bin=/usr/bin', source)
        self.assertIn('OMARCHY_FIREFOX_BRIDGE_INSTALL_TESTING', source)

    def test_publication_hook_and_interruption_failures_restore_exact_prior_tree(self):
        failures = (
            {"OMARCHY_FIREFOX_BRIDGE_TEST_FAIL_AFTER": "library"},
            {"OMARCHY_FIREFOX_BRIDGE_TEST_FAIL_AFTER": "launcher"},
            {"OMARCHY_FIREFOX_BRIDGE_TEST_FAIL_AFTER": "manifest"},
            {"OMARCHY_FIREFOX_BRIDGE_TEST_FAIL_AFTER": "hook"},
            {"OMARCHY_FIREFOX_BRIDGE_TEST_INTERRUPT_AFTER": "manifest"},
            {"TEST_HOOK_INTERRUPT": "1"},
            {"TEST_HOOK_FAILURE": "1"},
        )
        for index, injection in enumerate(failures):
            with self.subTest(injection=injection):
                home = self.make_home(f"rollback-{index}")
                self.seed_prior_install(home)
                before = self.managed_snapshot(home)
                manifest_parent = home / ".mozilla/native-messaging-hosts"
                parent_mode = stat.S_IMODE(manifest_parent.lstat().st_mode)

                result = self.run_install(home, expected=1, **injection)

                self.assertNotIn("Local bridge installation complete", result.stdout)
                self.assertEqual(self.managed_snapshot(home), before)
                self.assertEqual(stat.S_IMODE(manifest_parent.lstat().st_mode), parent_mode)
                self.assert_no_debris(home)
                lock = home / ".local/state/omarchy-firefox-bridge/install.lock"
                self.assert_regular(lock, 0o600)

    def test_concurrent_installers_serialize_complete_transactions(self):
        home = self.make_home()
        environment = self.environment(home, TEST_HOOK_DELAY="0.2")
        command = ["/usr/bin/bash", str(self.fixture_repo / "install.sh")]
        processes = [
            subprocess.Popen(
                command,
                cwd=self.root,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        results = [process.communicate(timeout=15) + (process.returncode,) for process in processes]
        for stdout, stderr, status in results:
            self.assertEqual(status, 0, f"stdout:\n{stdout}\nstderr:\n{stderr}")
        lines = (self.root / "hook.log").read_text().splitlines()
        self.assertEqual([line.split()[0] for line in lines], ["begin", "end", "begin", "end"])
        self.assertNotEqual(lines[0].split()[1], lines[2].split()[1])
        self.assert_installed(home)
        self.assert_no_debris(home)

    def test_hook_rejects_redirection_and_replaces_final_symlink_safely(self):
        hook = self.fixture_repo / "hooks/omarchy-firefox-bridge"
        home = self.make_home()
        external = self.root / "external-state"
        external.mkdir()
        write_file(external / "sentinel", "preserve")

        state_link = self.root / "state-link"
        os.symlink(external, state_link)
        result = subprocess.run(
            [str(hook), "unsafe"],
            env={**os.environ, "HOME": str(home), "XDG_STATE_HOME": str(state_link)},
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((external / "sentinel").read_text(), "preserve")

        state = self.root / "state"
        state.mkdir(mode=0o700)
        app = state / "omarchy-firefox-bridge"
        os.symlink(external, app)
        result = subprocess.run(
            [str(hook), "unsafe"],
            env={**os.environ, "HOME": str(home), "XDG_STATE_HOME": str(state)},
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((external / "sentinel").read_text(), "preserve")
        app.unlink()
        app.mkdir(mode=0o700)
        final = app / "last-theme-set"
        os.symlink(external / "sentinel", final)
        predictable = app / "last-theme-set.tmp"
        os.symlink(external / "sentinel", predictable)

        result = subprocess.run(
            [str(hook), "safe"],
            env={**os.environ, "HOME": str(home), "XDG_STATE_HOME": str(state)},
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((external / "sentinel").read_text(), "preserve")
        self.assert_regular(final, 0o600)
        self.assertIn("\tsafe\n", final.read_text())
        self.assertTrue(predictable.is_symlink())
        self.assertEqual(os.readlink(predictable), str(external / "sentinel"))
        for invalid in ("", "relative", "/"):
            result = subprocess.run(
                [str(hook), "invalid"],
                env={**os.environ, "HOME": str(home), "XDG_STATE_HOME": invalid},
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0, invalid)

    def test_test_harness_trap_cleans_setup_failure(self):
        record = self.root / "temporary-path"
        result = subprocess.run(
            ["/usr/bin/bash", str(REPO / "tests/shell/test_install.sh")],
            env={
                **os.environ,
                "OMARCHY_FIREFOX_BRIDGE_TEST_SETUP_FAILURE": "1",
                "OMARCHY_FIREFOX_BRIDGE_TEST_TMP_RECORD": str(record),
            },
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 73)
        leaked = Path(record.read_text().strip())
        self.assertFalse(leaked.exists(), leaked)


case = os.environ.get("INSTALL_TEST_CASE")
names = unittest.defaultTestLoader.getTestCaseNames(InstallerTest)
if case:
    names = [name for name in names if case in name]
    if not names:
        raise SystemExit(f"unknown INSTALL_TEST_CASE: {case}")
suite = unittest.TestSuite(InstallerTest(name) for name in names)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
print("ok - installer transaction is local, exact, serialized, and reversible")
PY
