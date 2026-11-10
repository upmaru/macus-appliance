from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from scripts.build_image import fs_usage, install_locked
from scripts.image_artifacts import package
from scripts.image_contents import KERNEL_RECORD, LIMINE_CONFIG, RECORD
from scripts.image_contents import check_installed, check_kernel, check_records, manifest_bytes
from scripts.image_contents import prune_retired_modules, record_bytes, select_kernel, write_records
from scripts.image_disk import GUEST_ENV
from scripts.image_hygiene import check_hygiene, clean_image
from scripts.package_lock import decode_lock
from scripts.upstream import extract_disk


ID = "alpine-3.24.2-lts-1"
RELEASE = "6.18.62-0-lts"
PACKAGES = {"linux-lts": "6.18.62-r0", "zfs-lts": "6.18.62-r0"}
LOCK = b"linux-lts=6.18.62-r0\nzfs-lts=6.18.62-r0\n"
APK_INFO = b"zfs-lts-6.18.62-r0\nlinux-lts-6.18.62-r0\n"
MANIFEST = b"linux-lts-6.18.62-r0\nzfs-lts-6.18.62-r0\n"
MANIFEST_SHA256 = "d17960b8b08595a429f9b63697d500f67480b1c295318ec3cd10157a079b2067"
GOLDEN = f"""format=1
appliance_id={ID}
channel=lts
alpine_branch=v3.24
lock_sha256=9d6533c585b6a51d4a5f404a26737c8a4f2e307471759d831c4c3e3e5a6410f6
manifest_sha256={MANIFEST_SHA256}
kernel_release={RELEASE}
""".encode("ascii")
LIMINE = """timeout: 10

/Alpine Linux
    protocol: efi
    path: boot():/vmlinuz-lts
    cmdline: initrd=\\\\initramfs-lts root=LABEL=/ console=tty0
"""
ZFS = f"lib/modules/{RELEASE}/extra/zfs.ko.zst"


class ImageTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, name, data=b"fixture"):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def test_golden_records(self):
        self.assertEqual(manifest_bytes(APK_INFO), MANIFEST)
        self.assertEqual(manifest_bytes(APK_INFO.rstrip(b"\n")), MANIFEST)
        with self.assertRaises(ValueError):
            manifest_bytes(APK_INFO.replace(b"\n", b"\r\n"))
        record = record_bytes(ID, "lts", "v3.24", LOCK, MANIFEST, RELEASE)
        self.assertEqual(record, GOLDEN)
        (self.root / "etc").mkdir()
        write_records(self.root, record, RELEASE)
        self.assertEqual((self.root / RECORD).read_bytes(), GOLDEN)
        self.assertEqual((self.root / KERNEL_RECORD).read_bytes(), b"6.18.62-0-lts\n")
        check_records(self.root, GOLDEN, RELEASE)

    def test_filesystem_usage_is_reported_in_bytes(self):
        stat = os.statvfs(self.root)
        usage = fs_usage(self.root)
        self.assertEqual(usage["total_bytes"], stat.f_blocks * stat.f_frsize)
        self.assertGreaterEqual(usage["used_bytes"], 0)
        self.assertLessEqual(usage["available_bytes"], usage["total_bytes"])

    def test_wrong_records(self):
        for name in (RECORD, KERNEL_RECORD):
            with self.subTest(name=name):
                (self.root / "etc").mkdir(exist_ok=True)
                write_records(self.root, GOLDEN, RELEASE)
                self.write(name, b"wrong\n")
                with self.assertRaisesRegex(ValueError, "wrong"):
                    check_records(self.root, GOLDEN, RELEASE)

    def test_installed_packages_and_manifest(self):
        self.assertEqual(check_installed(PACKAGES, PACKAGES, APK_INFO), MANIFEST)
        for installed in ({}, {**PACKAGES, "extra": "1-r0"}, {**PACKAGES, "linux-lts": "1-r0"}):
            with self.subTest(installed=installed), self.assertRaisesRegex(ValueError, "packages differ"):
                check_installed(PACKAGES, installed, APK_INFO)
        with self.assertRaisesRegex(ValueError, "manifest differs"):
            check_installed(PACKAGES, PACKAGES, b"other-1-r0\n")

    def test_install_locked_for_both_channels(self):
        config = {"alpine": {"repositories": [
            "https://dl-cdn.alpinelinux.org/alpine/v3.24/main",
            "https://dl-cdn.alpinelinux.org/alpine/v3.24/community",
        ]}, "image": {"packages": ["incus"]}}
        for flavor, kernel, zfs, remove in (
            ("lts", "6.18.62-r0", "6.18.62-r0", []),
            ("stable", "7.1.5-r0", "7.1.5-r1", ["linux-lts"]),
        ):
            with self.subTest(channel=flavor):
                root = self.root / flavor
                self.write(f"{flavor}/etc/apk/world", b"linux-lts\nalpine-base\n")
                self.write(f"{flavor}/etc/limine/limine-efi-updater.conf")
                channel = {"kernel_flavor": flavor, "packages": [f"linux-{flavor}", f"zfs-{flavor}"],
                           "remove": remove}
                entries = ("alpine-base=3.24.4-r0", "incus=7.0.1-r2", f"linux-{flavor}={kernel}",
                           "musl=1.2.6-r3", f"zfs-{flavor}={zfs}")
                locked = decode_lock(("\n".join(entries) + "\n").encode("ascii"))
                expected_manifest = "\n".join(entry.replace("=", "-") for entry in entries) + "\n"
                world = root / "etc/apk/world"

                def simulate_apk(*args, **kwargs):
                    if "add" in args:
                        world.write_text("\n".join(entries) + "\n", encoding="ascii")

                commands = Mock()
                commands.run.side_effect = simulate_apk
                commands.apk_query.side_effect = [locked, locked]
                commands.guest.return_value.stdout = expected_manifest.encode("ascii")
                with patch.multiple("scripts.build_image", run=commands.run,
                                    apk_query=commands.apk_query, guest=commands.guest,
                                    trigger_mounts=lambda _: nullcontext()):
                    manifest = install_locked(root, config, channel, locked)

                apk = ["chroot", str(root), "apk", "--repositories-file", "/etc/apk/repositories",
                       "--timeout", "60", "--cache-packages=no"]
                removal = [call.run(*apk, "del", "linux-lts", env=GUEST_ENV)] if remove else []
                self.assertEqual(commands.mock_calls, [
                    call.run(*apk, "update", env=GUEST_ENV),
                    call.apk_query(apk, "--from", "repositories", "--recursive", *entries, env=GUEST_ENV),
                    *removal,
                    call.run(*apk, "add", *entries, env=GUEST_ENV),
                    call.apk_query(apk, "--installed", "*", env=GUEST_ENV),
                    call.guest(root, "apk", "info", "-v", stdout=subprocess.PIPE),
                ])
                self.assertEqual(manifest, expected_manifest.encode("ascii"))
                self.assertEqual(world.read_bytes(),
                                 f"alpine-base\nincus\nlinux-{flavor}\nzfs-{flavor}\n".encode("ascii"))

    def test_static_kernel_checks(self):
        for name in (ZFS, "boot/vmlinuz-lts", "boot/initramfs-lts",
                     "boot/EFI/BOOT/BOOTAA64.EFI", "usr/share/limine/BOOTAA64.EFI",
                     f"boot/config-{RELEASE}"):
            self.write(name)
        self.write(LIMINE_CONFIG, LIMINE.encode())
        def boot_info(_command, _root, tool, *_args, **_kwargs):
            if tool == "modinfo":
                return f"{RELEASE} SMP"
            return f"./lib/modules/{RELEASE}/kernel/module.ko\n"

        with patch("scripts.image_contents.output", side_effect=boot_info):
            self.assertEqual(check_kernel(self.root, PACKAGES, "lts"), RELEASE)
            for installed in ({}, {**PACKAGES, "linux-stable": "1-r0"}):
                with self.subTest(installed=installed), self.assertRaisesRegex(ValueError, "kernel flavor"):
                    check_kernel(self.root, installed, "lts")
            extra = self.root / "lib/modules/7.1.5-0-stable"
            extra.mkdir()
            with self.assertRaisesRegex(ValueError, "module directory"):
                check_kernel(self.root, PACKAGES, "lts")
            extra.rmdir()
            for missing, reason in [(ZFS, "ZFS module"), ("boot/initramfs-lts", "initramfs-lts")]:
                with self.subTest(missing=missing):
                    (self.root / missing).unlink()
                    with self.assertRaisesRegex(ValueError, reason):
                        check_kernel(self.root, PACKAGES, "lts")
                    self.write(missing)
            self.write(LIMINE_CONFIG, LIMINE.replace("vmlinuz-lts", "vmlinuz-stable").encode())
            with self.assertRaisesRegex(ValueError, "does not select the channel kernel"):
                check_kernel(self.root, PACKAGES, "lts")
            self.write(LIMINE_CONFIG, LIMINE.replace("initramfs-lts", "initramfs-stable").encode())
            with self.assertRaisesRegex(ValueError, "does not select the channel initramfs"):
                check_kernel(self.root, PACKAGES, "lts")
        self.write(LIMINE_CONFIG, LIMINE.encode())
        with patch("scripts.image_contents.output", side_effect=[f"{RELEASE} SMP", "other"]):
            with self.assertRaisesRegex(ValueError, "initramfs does not contain modules"):
                check_kernel(self.root, PACKAGES, "lts")
        with patch("scripts.image_contents.output", return_value="7.1.5-0-stable SMP"):
            with self.assertRaisesRegex(ValueError, "ZFS module does not match"):
                check_kernel(self.root, PACKAGES, "lts")

    def test_select_stable_limine_entry(self):
        self.write(LIMINE_CONFIG, LIMINE.encode())
        select_kernel(self.root, "stable")
        self.assertEqual((self.root / LIMINE_CONFIG).read_text(),
                         LIMINE.replace("vmlinuz-lts", "vmlinuz-stable")
                         .replace("initramfs-lts", "initramfs-stable"))

    def test_prune_only_retired_trigger_metadata(self):
        old = "lib/modules/6.18.52-0-lts"
        self.write(f"{old}/initramfs-suffix")
        self.write(f"{old}/modules.alias")
        self.write(ZFS)
        prune_retired_modules(self.root)
        self.assertFalse((self.root / old).exists())
        self.assertTrue((self.root / ZFS).is_file())

    def test_dirty_image_state(self):
        for name in ("etc/machine-id", "etc/ssh/ssh_host_ed25519_key", "var/lib/cloud/instance/data",
                     "var/cache/apk/pkg.apk", "var/log/messages", "root/.ash_history",
                     "etc/resolv.conf", "root/.netrc", "root/.ssh/authorized_keys"):
            with self.subTest(name=name):
                self.write(name)
                with self.assertRaisesRegex(ValueError, "unexpected image state"):
                    check_hygiene(self.root)
                clean_image(self.root)
                check_hygiene(self.root)

    def test_leaked_credential_or_build_path(self):
        for data in (b"ghs_" + b"x" * 36, b"/home/runner/work/macus-appliance",
                     b"runner-specific-secret", b"/tmp/build-macus/root", b"\n-----BEGIN OPENSSH PRIVATE KEY-----\n"
                     + b"x" * 48):
            with self.subTest(data=data[:6]):
                self.write("etc/fixture", b"x" * (1024 * 1024 - 2) + data)
                with self.assertRaisesRegex(ValueError, "credential|build path"):
                    check_hygiene(self.root, build_paths=("/tmp/build-macus",),
                                  secrets=("runner-specific-secret",))

    def test_build_path_prefix_and_embedded_elf_fixture_are_not_credentials(self):
        self.write("lib/fixture.so", b"\x7fELF\x00-----BEGIN EC PRIVATE KEY-----\n"
                   + b"x" * 48 + b"\x00/etc/apk/repositories")
        check_hygiene(self.root, build_paths=("/repo",))

    def test_package_artifacts(self):
        self.write("disk.raw", b"signed disk fixture\x00")
        disk = self.root / "disk.raw"
        report = {
            "appliance_id": ID,
            "channel": "lts",
            "alpine_branch": "v3.24",
            "upstream_archive": "upstream.raw.tar.gz",
            "upstream_archive_sha512": "a" * 128,
            "upstream_raw_sha256": "a" * 64,
            "signer_fingerprint": "A" * 40,
            "config_sha256": "b" * 64,
            "lock_path": "locks/lts.lock",
            "lock_sha256": "c" * 64,
            "kernel_release": RELEASE,
            "zfs_module_version": PACKAGES["zfs-lts"],
            "repository": "example/appliance",
            "commit": "d" * 40,
            "workflow_run_url": "https://github.com/example/appliance/actions/runs/123",
        }
        assets = self.root / "assets"
        package(disk, assets, report, MANIFEST)
        archive = assets / f"{ID}.raw.tar.gz"
        self.assertEqual({path.name for path in assets.iterdir()},
                         {archive.name, f"{ID}.manifest", f"{ID}.build.json", "SHA512SUMS", "SHA256SUMS"})
        report_text = (assets / f"{ID}.build.json").read_text()
        built = json.loads(report_text)
        self.assertEqual(built, {**report, **{key: built[key] for key in (
            "raw_bytes", "raw_sha256", "archive_bytes", "archive_sha512", "manifest_sha256"
        )}})
        self.assertNotIn(str(self.root), report_text)
        self.assertEqual(built["manifest_sha256"], MANIFEST_SHA256)
        self.assertEqual((assets / f"{ID}.manifest").read_bytes(), MANIFEST)
        self.assertEqual(built["archive_bytes"], archive.stat().st_size)
        extracted = self.root / "extracted.raw"
        extract_disk(archive, extracted, built["raw_bytes"], built["raw_sha256"])
        self.assertEqual(extracted.read_bytes(), disk.read_bytes())
        with patch("scripts.image_artifacts.ASSET_LIMIT", 1):
            with self.assertRaisesRegex(ValueError, "below 2 GiB"):
                package(disk, self.root / "oversize", report, MANIFEST)
        self.assertFalse((self.root / "oversize").exists())
