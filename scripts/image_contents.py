"""Create and verify the format-1 records and inspect the offline boot files."""
import hashlib
import re
import shutil

from scripts.appliance_config import require
from scripts.image_disk import GUEST_ENV, output


RECORD = "etc/macus-appliance-image"
KERNEL_RECORD = "etc/tama-zfs-qualified-kernel"
LIMINE_CONFIG = "boot/limine/limine.conf"
KERNEL_TRIGGER_OUTPUT = {
    "initramfs-suffix",
    "modules.alias", "modules.alias.bin", "modules.builtin.alias.bin", "modules.builtin.bin",
    "modules.dep", "modules.dep.bin", "modules.devname", "modules.softdep", "modules.symbols",
    "modules.symbols.bin", "modules.weakdep",
}


def manifest_bytes(apk_info):
    lines = apk_info.removesuffix(b"\n").split(b"\n")
    require(lines and all(re.fullmatch(rb"[A-Za-z0-9][A-Za-z0-9+_.-]*", line) for line in lines),
            "invalid apk info -v manifest")
    require(len(set(lines)) == len(lines), "duplicate package in manifest")
    return b"".join(line + b"\n" for line in sorted(lines))


def check_installed(locked, installed, apk_info):
    require(installed == locked, "installed packages differ from the channel lock")
    manifest = manifest_bytes(apk_info)
    expected = manifest_bytes(b"\n".join(f"{name}-{version}".encode("ascii")
                                         for name, version in locked.items()))
    require(manifest == expected, "installed apk info -v manifest differs from the lock")
    return manifest


def record_bytes(appliance_id, channel, branch, lock, manifest, release):
    fields = {
        "format": "1",
        "appliance_id": appliance_id,
        "channel": channel,
        "alpine_branch": branch,
        "lock_sha256": hashlib.sha256(lock).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "kernel_release": release,
    }
    return "".join(f"{key}={value}\n" for key, value in fields.items()).encode("ascii")


def write_records(root, record, release):
    (root / RECORD).write_bytes(record)
    (root / KERNEL_RECORD).write_bytes((release + "\n").encode("ascii"))


def check_records(root, record, release):
    require((root / RECORD).read_bytes() == record, "wrong appliance image record")
    require((root / KERNEL_RECORD).read_bytes() == (release + "\n").encode("ascii"),
            "wrong qualified kernel record")


def select_kernel(root, flavor):
    config = root / LIMINE_CONFIG
    text = config.read_text(encoding="ascii")
    text = re.sub(r"(?m)^(\s*path: boot\(\):/vmlinuz-)lts$", rf"\g<1>{flavor}", text)
    text = re.sub(r"(?m)^(\s*cmdline: initrd=\\+initramfs-)lts(?= )", rf"\g<1>{flavor}", text)
    config.write_text(text, encoding="ascii")


def check_boot(root, flavor):
    for filename in (f"vmlinuz-{flavor}", f"initramfs-{flavor}", "EFI/BOOT/BOOTAA64.EFI"):
        path = root / "boot" / filename
        require(path.is_file() and path.stat().st_size > 0, f"missing boot file {filename}")
    require((root / "boot/EFI/BOOT/BOOTAA64.EFI").read_bytes()
            == (root / "usr/share/limine/BOOTAA64.EFI").read_bytes(),
            "EFI bootloader differs from the installed Limine package")
    for alternative in ("boot/limine.conf", "boot/EFI/BOOT/limine.conf", "boot/boot/limine.conf",
                        "boot/boot/limine/limine.conf"):
        require(not (root / alternative).exists(), "multiple Limine configuration locations")
    text = (root / LIMINE_CONFIG).read_text(encoding="ascii")
    options = {}
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("/"):
            options = {}
            entries.append(options)
        elif entries and line and not line.startswith("#"):
            key, separator, value = line.partition(":")
            require(separator and key.lower() not in options, "invalid Limine entry")
            options[key.lower()] = value.strip()
    require(len(entries) == 1, "expected one Limine boot entry")
    boot = entries[0]
    require(boot.get("protocol") == "efi" and boot.get("path") == f"boot():/vmlinuz-{flavor}",
            "Limine does not select the channel kernel")
    arguments = boot.get("cmdline", "").split()
    initrds = [argument for argument in arguments if argument.startswith("initrd=")]
    require(len(initrds) == 1 and initrds[0].split("=", 1)[1].lstrip("\\") == f"initramfs-{flavor}"
            and initrds[0].startswith("initrd=\\") and "root=LABEL=/" in arguments,
            "Limine does not select the channel initramfs and root")


def prune_retired_modules(root):
    for directory in (root / "lib/modules").iterdir():
        if directory.is_dir() and not directory.is_symlink() and all(
            entry.is_file() and not entry.is_symlink() and entry.name in KERNEL_TRIGGER_OUTPUT
            for entry in directory.iterdir()
        ):
            shutil.rmtree(directory)


def check_kernel(root, installed, flavor):
    kernels = {name for name in installed if name in
               {"linux-lts", "linux-stable", "linux-virt", "linux-edge", "linux-hardened",
                "linux-rpi", "linux-asahi", "linux-lts-dbg", "linux-stable-dbg"}}
    require(kernels == {f"linux-{flavor}"}, "expected exactly one installed kernel flavor")
    module_dirs = [path for path in (root / "lib/modules").iterdir() if path.is_dir()]
    if len(module_dirs) != 1:
        entries = {path.name: sorted(entry.name for entry in path.iterdir()) for path in module_dirs}
        raise ValueError(f"expected exactly one installed kernel module directory, found {entries}")
    modules = module_dirs[0]
    release = modules.name
    require(re.fullmatch(rf"[0-9][A-Za-z0-9.+_-]*-{flavor}", release)
            and (root / "boot" / f"config-{release}").is_file(),
            "boot kernel and module directory do not match")
    zfs = [path for path in modules.rglob("zfs.ko*")
           if path.name in {"zfs.ko", "zfs.ko.gz", "zfs.ko.xz", "zfs.ko.zst"} and path.is_file()]
    require(len(zfs) == 1, "expected one ZFS module for the kernel")
    guest_zfs = "/" + zfs[0].relative_to(root).as_posix()
    vermagic = output("chroot", root, "modinfo", "-F", "vermagic", guest_zfs, env=GUEST_ENV).split()
    require(vermagic and vermagic[0] == release, "ZFS module does not match kernel release")
    check_boot(root, flavor)
    initramfs = output("chroot", root, "sh", "-ec", 'set -o pipefail; gzip -dc "$1" | cpio -it',
                       "inspect-initramfs", f"/boot/initramfs-{flavor}", env=GUEST_ENV)
    require(re.search(rf"(?:^|/)lib/modules/{re.escape(release)}/.*\.ko(?:\.[a-z0-9]+)?$",
                      initramfs, re.MULTILINE),
            "initramfs does not contain modules for the selected kernel")
    return release
