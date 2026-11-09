"""Remove image state and detect leaked build data before packaging."""
import os
import re
import shutil
import stat
from pathlib import Path

from scripts.appliance_config import require


STATE = (
    "etc/machine-id", "var/lib/dbus/machine-id", "etc/ssh/ssh_host_*",
    "var/lib/cloud", "run/cloud-init", "var/cache/apk/*", "etc/apk/cache", "var/log/*",
    "root/.*history", "home/*/.*history", "etc/resolv.conf", "etc/apk/.apk-new",
    "root/.netrc", "home/*/.netrc", "root/.git-credentials", "home/*/.git-credentials",
    "root/.aws/credentials", "home/*/.aws/credentials", "tmp/*", "var/tmp/*",
    "root/.ssh/authorized_keys*", "home/*/.ssh/authorized_keys*",
    "etc/limine/limine-efi-updater.conf.apk-new",
)
CREDENTIAL = re.compile(
    rb"gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{50,255}|"
    # A PEM line starts a text record. GnuTLS also embeds public self-test key strings in ELF.
    rb"(?m:^[ \t]*-----BEGIN (?:OPENSSH|RSA|DSA|EC|ENCRYPTED) PRIVATE KEY-----"
    rb"\r?\n[ \t]*[A-Za-z0-9+/]{32})"
)
RUNNER_PATHS = (b"/home/runner/work/", b"/home/runner/_work/", b"/github/workspace/")


def state_paths(root):
    for pattern in STATE:
        parent, leaf = pattern.rsplit("/", 1)
        for directory in root.glob(parent):
            # Avoid following directory links, including absolute guest symlinks.
            if any(path.is_symlink() for path in (directory, *directory.parents)
                   if path.is_relative_to(root)):
                continue
            yield from directory.glob(leaf)


def clean_image(root):
    for path in state_paths(root):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def check_hygiene(root, build_paths=(), secrets=()):
    first = next(state_paths(root), None)
    require(first is None, f"unexpected image state: {first.relative_to(root) if first else ''}")
    literals = RUNNER_PATHS + tuple(secret.encode() for secret in secrets if secret)
    paths = tuple(os.fsencode(path) for path in build_paths)
    build_path = re.compile(rb"(?:" + b"|".join(map(re.escape, paths))
                            + rb")(?=$|[^A-Za-z0-9_.-])") if paths else None
    overlap = max(512, *(len(value) for value in (*literals, *paths)))
    shadow = root / "etc/shadow"
    if shadow.is_file() and not shadow.is_symlink():
        require(not any(line.split(":")[1].startswith("$") for line in shadow.read_text().splitlines()),
                "active password hash in etc/shadow")

    def check(data, path):
        require(not CREDENTIAL.search(data), f"credential material in {path.relative_to(root)}")
        require(not any(value in data for value in literals),
                f"build path or token in {path.relative_to(root)}")
        require(build_path is None or not build_path.search(data),
                f"build path in {path.relative_to(root)}")

    for parent, dirs, files in os.walk(root):
        for name in [*dirs, *files]:
            path = Path(parent) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                check(os.fsencode(os.readlink(path)), path)
            elif stat.S_ISREG(mode):
                tail = b""
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        data = (b"\x00" if tail else b"") + tail + chunk
                        check(data, path)
                        tail = data[-overlap:]
