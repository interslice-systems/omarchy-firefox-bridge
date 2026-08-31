#!/usr/bin/env bash
set -euo pipefail

PATH=/usr/bin
repo=$(cd "$(/usr/bin/dirname "${BASH_SOURCE[0]}")" && pwd -P)
testing=${OMARCHY_FIREFOX_BRIDGE_INSTALL_TESTING:-0}
usr_bin=/usr/bin
effective_uid=$EUID

if [[ $testing == 1 ]]; then
  usr_bin=${OMARCHY_FIREFOX_BRIDGE_TEST_USR_BIN:?test usr-bin is required}
  effective_uid=${OMARCHY_FIREFOX_BRIDGE_TEST_EUID:-$EUID}
fi

if (( effective_uid == 0 )); then
  printf 'Refusing to install as root; run as the target user\n' >&2
  exit 1
fi

case ${HOME:-} in
  /*) ;;
  '') printf 'HOME must be a nonempty absolute path\n' >&2; exit 1 ;;
  *) printf 'HOME must be a nonempty absolute path: %s\n' "$HOME" >&2; exit 1 ;;
esac

missing=()
for command in bwrap firefox inotifywait python3 omarchy; do
  path=$usr_bin/$command
  [[ -f $path && -x $path ]] || missing+=("$path")
done
if (( ${#missing[@]} )); then
  for path in "${missing[@]}"; do
    printf 'Missing runtime command: %s\n' "$path" >&2
  done
  printf 'Install official packages with: sudo pacman -S --needed bubblewrap firefox inotify-tools python\n' >&2
  exit 1
fi

"$usr_bin/python3" -P - "$repo" "$HOME" "$usr_bin" "$testing" <<'PY'
import errno
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import stat
import subprocess
import sys


repo = Path(sys.argv[1])
home = Path(sys.argv[2])
usr_bin = Path(sys.argv[3])
testing = sys.argv[4] == "1"
actual_uid = os.geteuid()
expected_uid = actual_uid
if testing and "OMARCHY_FIREFOX_BRIDGE_TEST_EXPECT_UID" in os.environ:
    expected_uid = int(os.environ["OMARCHY_FIREFOX_BRIDGE_TEST_EXPECT_UID"])

source_names = (
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
active = {
    "library": home / ".local/lib/omarchy-firefox-bridge",
    "launcher": home / ".local/bin/omarchy-firefox-bridge",
    "manifest": home / ".mozilla/native-messaging-hosts/omarchy_firefox_bridge.json",
    "hook": home / ".config/omarchy/hooks/theme-set.d/omarchy-firefox-bridge",
}
legacy = {
    "legacy-helper": home / ".local/bin/omarchy-firefox-theme-helper",
    "legacy-manifest": home / ".mozilla/native-messaging-hosts/omarchy_firefox_theme.json",
    "legacy-hook": home / ".config/omarchy/hooks/theme-set.d/firefox-color-sync",
}
parent_specs = (
    ((".local",), 0o755),
    ((".local", "bin"), 0o755),
    ((".local", "lib"), 0o755),
    ((".local", "state"), 0o755),
    ((".local", "state", "omarchy-firefox-bridge"), 0o700),
    ((".mozilla",), 0o755),
    ((".mozilla", "native-messaging-hosts"), 0o700),
    ((".config",), 0o755),
    ((".config", "omarchy"), 0o755),
    ((".config", "omarchy", "hooks"), 0o755),
    ((".config", "omarchy", "hooks", "theme-set.d"), 0o755),
)


def fail(message):
    raise RuntimeError(message)


def lstat(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def validate_owned_directory(path, label):
    info = lstat(path)
    if info is None:
        fail(f"{label} does not exist: {path}")
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        fail(f"{label} must be a real directory: {path}")
    if info.st_uid != expected_uid:
        fail(f"{label} must be owned by uid {expected_uid}: {path}")
    return info


def validate_home():
    if not home.is_absolute() or home == Path("/"):
        fail(f"HOME must be an absolute non-root directory: {home}")
    validate_owned_directory(home, "HOME")
    if Path(os.path.realpath(home)) != home:
        fail(f"HOME must not contain symlink components: {home}")


def validate_existing_components():
    for components, _mode in parent_specs:
        path = home.joinpath(*components)
        info = lstat(path)
        if info is None:
            continue
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            fail(f"managed component must be a real directory: {path}")
        if info.st_uid != expected_uid:
            fail(f"managed component must be owned by uid {expected_uid}: {path}")


def validate_source(path, mode=None):
    info = lstat(path)
    if info is None or not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        fail(f"required source must be a regular non-symlink file: {path}")
    if info.st_uid != actual_uid:
        fail(f"required source must be owned by uid {actual_uid}: {path}")
    if mode is not None and stat.S_IMODE(info.st_mode) != mode:
        fail(f"required source has wrong mode: {path}")


def validate_sources():
    validate_owned_directory(repo, "repository")
    validate_owned_directory(repo / "src", "source directory")
    validate_owned_directory(repo / "src/omarchy_firefox_bridge", "source package")
    validate_source(repo / "bin/omarchy-firefox-bridge", 0o755)
    validate_source(repo / "libexec/omarchy-firefox-bridge-sandbox", 0o755)
    validate_source(repo / "hooks/omarchy-firefox-bridge", 0o755)
    for name in source_names:
        validate_source(repo / "src/omarchy_firefox_bridge" / name)


def validate_runtime():
    for name in ("bwrap", "firefox", "inotifywait", "python3", "omarchy"):
        path = usr_bin / name
        try:
            info = path.stat()
        except FileNotFoundError:
            fail(f"missing runtime command: {path}")
        if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
            fail(f"runtime command must be executable: {path}")
    version = subprocess.run(
        [str(usr_bin / "omarchy"), "version"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if not version.startswith("4."):
        fail(f"Omarchy 4 is required; found {version}")
    python_version = sys.version_info[:2]
    if testing and "OMARCHY_FIREFOX_BRIDGE_TEST_PYTHON_VERSION" in os.environ:
        python_version = tuple(
            int(part)
            for part in os.environ["OMARCHY_FIREFOX_BRIDGE_TEST_PYTHON_VERSION"].split(".")[:2]
        )
    if python_version < (3, 11):
        fail("Python 3.11 or newer is required")


def validate_final_entries():
    for path in active.values():
        info = lstat(path)
        if info is None:
            continue
        if info.st_uid != expected_uid:
            fail(f"managed destination must be owned by uid {expected_uid}: {path}")
        if not (
            stat.S_ISREG(info.st_mode)
            or stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
        ):
            fail(f"unsupported managed destination type: {path}")
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            validate_removable_tree(path)
    for path in legacy.values():
        info = lstat(path)
        if info is None:
            continue
        if info.st_uid != expected_uid:
            fail(f"legacy destination must be owned by uid {expected_uid}: {path}")
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            fail(f"legacy destination is an unexpected directory: {path}")
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            fail(f"unsupported legacy destination type: {path}")


def validate_removable_tree(root):
    pending = [root]
    while pending:
        path = pending.pop()
        info = lstat(path)
        if info is None or info.st_uid != expected_uid:
            fail(f"managed destination tree must be owned by uid {expected_uid}: {path}")
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            continue
        if stat.S_IMODE(info.st_mode) & 0o700 != 0o700:
            fail(f"managed destination directory must be owner-accessible: {path}")
        try:
            pending.extend(Path(entry.path) for entry in os.scandir(path))
        except OSError as error:
            fail(f"managed destination directory cannot be traversed: {path}: {error}")


def ensure_relative_directory(components, final_mode, created_paths=None):
    descriptor = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        final_created = False
        for index, component in enumerate(components):
            mode = final_mode if index == len(components) - 1 else 0o755
            created = False
            try:
                os.mkdir(component, mode, dir_fd=descriptor)
                created = True
                if created_paths is not None:
                    created_paths.append(home.joinpath(*components[:index + 1]))
            except FileExistsError:
                pass
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            info = os.fstat(child)
            if info.st_uid != expected_uid:
                os.close(child)
                fail(f"managed directory must be owned by uid {expected_uid}: {home.joinpath(*components[:index + 1])}")
            if created:
                os.fchmod(child, mode)
            if index == len(components) - 1:
                final_created = created
            os.close(descriptor)
            descriptor = child
        return descriptor, stat.S_IMODE(os.fstat(descriptor).st_mode), final_created
    except BaseException:
        os.close(descriptor)
        raise


def unique_path(parent, stem, category):
    while True:
        candidate = parent / f".{stem}.{category}.{os.getpid()}.{secrets.token_hex(8)}"
        if lstat(candidate) is None:
            return candidate


def write_exclusive(path, data, mode):
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        mode,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail(f"write made no progress: {path}")
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def copy_regular(source, destination, mode):
    validate_source(source)
    source_descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    destination_descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        mode,
    )
    try:
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    fail(f"copy made no progress: {destination}")
                view = view[written:]
        os.fchmod(destination_descriptor, mode)
        os.fsync(destination_descriptor)
    finally:
        os.close(destination_descriptor)
        os.close(source_descriptor)


def remove_entry(path):
    info = lstat(path)
    if info is None:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def stage_artifacts():
    stages = {}
    try:
        library = unique_path(active["library"].parent, "omarchy-firefox-bridge", "stage")
        stages["library"] = library
        library.mkdir(mode=0o755)
        library.chmod(0o755)
        for directory in (
            library / "libexec",
            library / "src",
            library / "src/omarchy_firefox_bridge",
        ):
            directory.mkdir(mode=0o755)
            directory.chmod(0o755)
        copy_regular(
            repo / "libexec/omarchy-firefox-bridge-sandbox",
            library / "libexec/omarchy-firefox-bridge-sandbox",
            0o755,
        )
        for name in source_names:
            copy_regular(
                repo / "src/omarchy_firefox_bridge" / name,
                library / "src/omarchy_firefox_bridge" / name,
                0o644,
            )

        launcher = unique_path(active["launcher"].parent, "omarchy-firefox-bridge", "stage")
        stages["launcher"] = launcher
        copy_regular(repo / "bin/omarchy-firefox-bridge", launcher, 0o755)

        manifest = {
            "name": "omarchy_firefox_bridge",
            "description": "Omarchy Firefox Bridge native host",
            "path": str(active["launcher"]),
            "type": "stdio",
            "allowed_extensions": ["omarchy-bridge@interslice.systems"],
        }
        manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode()
        manifest_stage = unique_path(active["manifest"].parent, "omarchy_firefox_bridge.json", "stage")
        stages["manifest"] = manifest_stage
        write_exclusive(manifest_stage, manifest_bytes, 0o600)

        hook_stage = unique_path(active["hook"].parent, "omarchy-firefox-bridge", "stage")
        stages["hook"] = hook_stage
        hook_stage.mkdir(mode=0o700)
        hook_stage.chmod(0o700)
        copy_regular(
            repo / "hooks/omarchy-firefox-bridge",
            hook_stage / "omarchy-firefox-bridge",
            0o755,
        )
        return stages, manifest_bytes
    except BaseException:
        cleanup_stages(stages)
        raise


def maybe_inject(boundary):
    if not testing:
        return
    if os.environ.get("OMARCHY_FIREFOX_BRIDGE_TEST_FAIL_AFTER") == boundary:
        fail(f"injected failure after {boundary}")
    if os.environ.get("OMARCHY_FIREFOX_BRIDGE_TEST_INTERRUPT_AFTER") == boundary:
        raise KeyboardInterrupt(f"injected interruption after {boundary}")


def verify_regular(path, mode, expected_bytes):
    info = lstat(path)
    if info is None or not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        fail(f"installed path is not a regular non-symlink file: {path}")
    if info.st_uid != expected_uid or stat.S_IMODE(info.st_mode) != mode:
        fail(f"installed file ownership or mode is wrong: {path}")
    if path.read_bytes() != expected_bytes:
        fail(f"installed file bytes differ: {path}")


def verify_install(manifest_bytes):
    expected_inventory = {
        ".",
        "libexec",
        "libexec/omarchy-firefox-bridge-sandbox",
        "src",
        "src/omarchy_firefox_bridge",
        *(f"src/omarchy_firefox_bridge/{name}" for name in source_names),
    }
    library = active["library"]
    actual_inventory = {"."}
    for path in library.rglob("*"):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            fail(f"installed library contains a symlink: {path}")
        actual_inventory.add(str(path.relative_to(library)))
    if actual_inventory != expected_inventory:
        fail("installed library inventory differs from the explicit source product")
    for path in (
        library,
        library / "libexec",
        library / "src",
        library / "src/omarchy_firefox_bridge",
    ):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            fail(f"installed path is not a real directory: {path}")
        if info.st_uid != expected_uid or stat.S_IMODE(info.st_mode) != 0o755:
            fail(f"installed directory ownership or mode is wrong: {path}")
    verify_regular(
        library / "libexec/omarchy-firefox-bridge-sandbox",
        0o755,
        (repo / "libexec/omarchy-firefox-bridge-sandbox").read_bytes(),
    )
    for name in source_names:
        verify_regular(
            library / "src/omarchy_firefox_bridge" / name,
            0o644,
            (repo / "src/omarchy_firefox_bridge" / name).read_bytes(),
        )
    verify_regular(
        active["launcher"],
        0o755,
        (repo / "bin/omarchy-firefox-bridge").read_bytes(),
    )
    verify_regular(active["manifest"], 0o600, manifest_bytes)
    verify_regular(
        active["hook"],
        0o755,
        (repo / "hooks/omarchy-firefox-bridge").read_bytes(),
    )
    manifest_parent = active["manifest"].parent.lstat()
    if (
        not stat.S_ISDIR(manifest_parent.st_mode)
        or stat.S_ISLNK(manifest_parent.st_mode)
        or manifest_parent.st_uid != expected_uid
        or stat.S_IMODE(manifest_parent.st_mode) != 0o700
    ):
        fail("native manifest directory ownership or mode is wrong")
    for path in legacy.values():
        if lstat(path) is not None:
            fail(f"legacy entry remains installed: {path}")


def cleanup_stages(stages):
    for path in stages.values():
        remove_entry(path)


def remove_created_directories(paths):
    errors = []
    for path in reversed(paths):
        try:
            path.rmdir()
        except FileNotFoundError:
            pass
        except OSError as error:
            if error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                errors.append(f"{path}: {error}")
    if errors:
        raise RuntimeError("created-directory cleanup failed: " + "; ".join(errors))


def rollback(states):
    errors = []
    for path, backup in reversed(list(states.values())):
        try:
            if backup is None:
                remove_entry(path)
            elif lstat(backup) is not None:
                remove_entry(path)
                os.replace(backup, path)
        except BaseException as error:
            errors.append(f"{path}: {error}")
    if errors:
        raise RuntimeError("rollback failed: " + "; ".join(errors))


def install_transaction():
    validate_home()
    validate_sources()
    validate_runtime()
    validate_existing_components()

    state_descriptor, state_mode, _state_created = ensure_relative_directory(
        (".local", "state", "omarchy-firefox-bridge"),
        0o700,
    )
    if state_mode != 0o700:
        os.close(state_descriptor)
        fail("install state directory must have mode 0700")
    lock_descriptor = os.open(
        "install.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=state_descriptor,
    )
    lock_info = os.fstat(lock_descriptor)
    if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != expected_uid:
        fail("install lock must be an owned regular file")
    os.fchmod(lock_descriptor, 0o600)
    fcntl.flock(lock_descriptor, fcntl.LOCK_EX)

    stages = {}
    states = {}
    mode_changes = []
    created_parents = []
    committed = False
    previous_handlers = {}

    def interrupted(signum, _frame):
        raise RuntimeError(f"installation interrupted by signal {signum}")

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, interrupted)
        validate_existing_components()
        validate_final_entries()
        for components, mode in parent_specs:
            descriptor, previous_mode, created = ensure_relative_directory(
                components,
                mode,
                created_parents,
            )
            if not created and previous_mode != mode and components == (".mozilla", "native-messaging-hosts"):
                mode_changes.append((home.joinpath(*components), previous_mode))
                os.fchmod(descriptor, mode)
            os.close(descriptor)
        stages, manifest_bytes = stage_artifacts()

        for label, path in (*active.items(), *legacy.items()):
            info = lstat(path)
            backup = None
            if info is not None:
                backup = unique_path(path.parent, path.name, "backup")
            states[label] = (path, backup)
            if backup is not None:
                os.replace(path, backup)

        os.replace(stages["library"], active["library"])
        stages.pop("library")
        maybe_inject("library")
        os.replace(stages["launcher"], active["launcher"])
        stages.pop("launcher")
        maybe_inject("launcher")
        os.replace(stages["manifest"], active["manifest"])
        stages.pop("manifest")
        maybe_inject("manifest")

        hook_environment = None
        if testing:
            hook_environment = os.environ.copy()
            hook_environment["OMARCHY_FIREFOX_BRIDGE_TEST_INSTALLER_PID"] = str(os.getpid())
        subprocess.run(
            [
                str(usr_bin / "omarchy"),
                "hook",
                "install",
                "theme-set",
                str(stages["hook"] / "omarchy-firefox-bridge"),
            ],
            check=True,
            text=True,
            capture_output=True,
            env=hook_environment,
        )
        maybe_inject("hook")
        verify_install(manifest_bytes)
        committed = True

        for _path, backup in states.values():
            if backup is not None:
                remove_entry(backup)
        cleanup_stages(stages)
    except BaseException:
        try:
            if not committed:
                rollback(states)
        finally:
            try:
                if not committed:
                    for path, mode in reversed(mode_changes):
                        descriptor = os.open(
                            path,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        )
                        try:
                            os.fchmod(descriptor, mode)
                        finally:
                            os.close(descriptor)
            finally:
                try:
                    cleanup_stages(stages)
                finally:
                    if not committed:
                        remove_created_directories(created_parents)
        raise
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)
        os.close(state_descriptor)


try:
    install_transaction()
except BaseException as error:
    message = str(error) or type(error).__name__
    print(f"Installation failed: {message}", file=sys.stderr)
    raise SystemExit(1)
PY

cat <<EOF
Local bridge installation complete.

Native host: $HOME/.mozilla/native-messaging-hosts/omarchy_firefox_bridge.json
Executable:  $HOME/.local/bin/omarchy-firefox-bridge
Extension:   $repo/extension/manifest.json

Load temporarily in Firefox:
  about:debugging#/runtime/this-firefox
  Load Temporary Add-on -> $repo/extension/manifest.json

This installer does not contact AMO or sign the extension.
EOF
