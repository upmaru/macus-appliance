"""Attach and mount the pinned Alpine cloud image on Linux arm64."""
from contextlib import ExitStack, contextmanager
import errno
import os
from pathlib import Path
import platform
import stat
import subprocess

from scripts.appliance_config import require


GUEST_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LC_ALL": "C"}


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def output(*args, **kwargs):
    return run(*args, stdout=subprocess.PIPE, text=True, **kwargs).stdout


def require_linux_arm64():
    require(platform.system() == "Linux" and platform.machine() == "aarch64",
            "a Linux arm64 host is required")
    require(os.geteuid() == 0, "root is required to mount the appliance image")


def guest(root, *args, **kwargs):
    return run("chroot", root, *args, env=GUEST_ENV, **kwargs)


def grow_disk(disk, size):
    if disk.stat().st_size == size:
        return
    with disk.open("r+b") as stream:
        stream.truncate(size)
    run("sgdisk", "--move-second-header", disk)
    run("growpart", "--update", "off", disk, "2")


def partition(loop, number, cleanup):
    name = f"{loop.name}p{number}"
    node = loop.with_name(name)
    if not node.exists():
        # In containers without udev, the kernel registers the partition but no node is created.
        major, minor = Path(f"/sys/class/block/{name}/dev").read_text().strip().split(":")
        try:
            os.mknod(node, stat.S_IFBLK | 0o600, os.makedev(int(major), int(minor)))
        except FileExistsError:
            pass  # udev created the node after the existence check.
        else:
            cleanup.callback(node.unlink, missing_ok=True)
    return node


def zero_efi_free_blocks(boot):
    filler = boot / ".macus-zero-free"
    zero = bytes(1024 * 1024)
    with filler.open("xb", buffering=0) as stream:
        try:
            usage = os.statvfs(boot)
            while usage.f_bavail:
                try:
                    remaining = usage.f_bavail * usage.f_frsize
                    stream.write(zero[:remaining])
                except OSError as error:
                    if error.errno != errno.ENOSPC or os.statvfs(boot).f_bavail:
                        raise
                    break
                usage = os.statvfs(boot)
            os.fsync(stream.fileno())
        finally:
            filler.unlink()


@contextmanager
def mounted_image(disk, root, *, writable=False, resize_and_zero=False):
    with ExitStack() as cleanup:
        flags = [] if writable else ["--read-only"]
        loop = Path(output("losetup", "--find", "--show", "--partscan", *flags, disk).strip())
        cleanup.callback(run, "losetup", "--detach", loop)
        efi, filesystem = partition(loop, 1, cleanup), partition(loop, 2, cleanup)
        for device, expected in [(efi, "vfat"), (filesystem, "ext4")]:
            actual = output("blkid", "-p", "-s", "TYPE", "-o", "value", device).strip()
            require(actual == expected, f"expected {expected} on {device.name}")
        if resize_and_zero:
            result = subprocess.run(["e2fsck", "-pf", filesystem], check=False)
            require(result.returncode in (0, 1), f"e2fsck failed with status {result.returncode}")
            run("resize2fs", filesystem)
        with ExitStack() as mounts:
            run("mount", "-t", "ext4", "-o", "rw" if writable else "ro,noload", filesystem, root)
            mounts.callback(run, "umount", root)
            run("mount", "-t", "vfat", "-o", "rw,umask=077" if writable else "ro",
                efi, root / "boot")
            mounts.callback(run, "umount", root / "boot")
            yield root
            if resize_and_zero:
                zero_efi_free_blocks(root / "boot")
        if resize_and_zero:
            run("zerofree", filesystem)
