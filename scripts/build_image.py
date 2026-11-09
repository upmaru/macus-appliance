"""Build a locked Alpine appliance disk offline on Linux arm64."""
import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from scripts.appliance_config import DEFAULT_CONFIG, build_channel, load_config, require
from scripts.image_artifacts import package
from scripts.image_contents import check_installed, check_kernel, check_records, record_bytes
from scripts.image_contents import prune_retired_modules, select_kernel, write_records
from scripts.image_disk import GUEST_ENV, grow_disk, guest, mounted_image, require_linux_arm64, run
from scripts.image_hygiene import check_hygiene, clean_image
from scripts.package_lock import apk_query, decode_lock
from scripts.upstream import download_upstream, verify_upstream


@contextmanager
def trigger_mounts(root):
    with ExitStack() as cleanup:
        for source in ("/dev", "/sys"):
            target = root / source[1:]
            run("mount", "--bind", source, target)
            cleanup.callback(run, "umount", target)
            run("mount", "-o", "remount,bind,ro", target)
        run("mount", "-t", "proc", "-o", "nosuid,nodev,noexec,ro", "proc", root / "proc")
        cleanup.callback(run, "umount", root / "proc")
        yield


def install_locked(root, config, channel, locked):
    repositories = root / "etc/apk/repositories"
    repositories.write_text("\n".join(config["alpine"]["repositories"]) + "\n", encoding="ascii")
    # The pinned base has no resolver configuration; provide one only for this transaction.
    resolver = root / "etc/resolv.conf"
    resolver.unlink(missing_ok=True)
    resolver.write_bytes(Path("/etc/resolv.conf").read_bytes())
    # Alpine's updater requires the guest ESP mount point when its trigger runs in the chroot.
    (root / "etc/limine/limine-efi-updater.conf").write_text(
        "efi_system_partition=/boot\nefi_file=BOOTAA64.EFI\n", encoding="ascii",
    )
    world = root / "etc/apk/world"
    initial_world = world.read_text(encoding="ascii").splitlines()
    apk = ["chroot", str(root), "apk", "--repositories-file", "/etc/apk/repositories",
           "--timeout", "60", "--cache-packages=no"]
    entries = [f"{name}={version}" for name, version in locked.items()]
    with trigger_mounts(root):
        run(*apk, "update", env=GUEST_ENV)
        available = apk_query(apk, "--from", "repositories", "--recursive", *entries, env=GUEST_ENV)
        require(available == locked, "repositories do not resolve exactly the lock")
        unused = {channel["kernel_flavor"]} | {name.removeprefix("linux-") for name in channel["remove"]}
        for flavor in sorted(unused):
            path = root / "boot" / f"dtbs-{flavor}"
            if path.exists():
                shutil.rmtree(path)
        if channel["remove"]:
            run(*apk, "del", *channel["remove"], env=GUEST_ENV)
            for name in channel["remove"]:
                (root / "boot" / f"initramfs-{name.removeprefix('linux-')}").unlink(missing_ok=True)
        run(*apk, "add", *entries, env=GUEST_ENV)
        installed = apk_query(apk, "--installed", "*", env=GUEST_ENV)
        info = guest(root, "apk", "info", "-v", stdout=subprocess.PIPE).stdout
        manifest = check_installed(locked, installed, info)
        # Leave the original world roots and the appliance's top-level packages for normal updates.
        wanted = (set(initial_world) | set(config["image"]["packages"]) | set(channel["packages"]))
        world.write_text("".join(f"{name}\n" for name in sorted(wanted - set(channel["remove"]))),
                         encoding="ascii")
    return manifest


def fs_usage(path):
    stat = os.statvfs(path)
    return {
        "total_bytes": stat.f_blocks * stat.f_frsize,
        "used_bytes": (stat.f_blocks - stat.f_bfree) * stat.f_frsize,
        "available_bytes": stat.f_bavail * stat.f_frsize,
    }


def build(args, config, config_sha256, channel, workspace):
    lock_data = (args.config.parent / channel["lock"]).read_bytes()
    locked = decode_lock(lock_data)
    require(not set(channel["remove"]) & locked.keys(), "lock includes a removed package")
    required = set(config["image"]["packages"]) | set(channel["packages"])
    require(required <= locked.keys(), "lock omits a top-level package")

    disk = workspace / "disk.raw"
    inputs = args.upstream_inputs
    if inputs is None:
        inputs = workspace / "upstream"
        inputs.mkdir()
        download_upstream(config["upstream"], inputs)
    verify_upstream(config["upstream"], inputs, disk)
    grow_disk(disk, config["image"]["raw_bytes"])

    root = workspace / "root"
    root.mkdir()
    with mounted_image(disk, root, writable=True, resize_and_zero=True):
        manifest = install_locked(root, config, channel, locked)
        prune_retired_modules(root)
        select_kernel(root, args.channel)
        with trigger_mounts(root):
            release = check_kernel(root, locked, args.channel)
        branch = config["alpine"]["branch"]
        record = record_bytes(args.appliance_id, args.channel, branch, lock_data, manifest, release)
        write_records(root, record, release)
        clean_image(root)
        check_records(root, record, release)
        check_hygiene(root, build_paths=(workspace, Path.cwd()),
                      secrets=(os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_TOKEN")))
        usage = {"channel": args.channel, "kernel_release": release, "raw_bytes": disk.stat().st_size,
                 "root": fs_usage(root), "efi": fs_usage(root / "boot")}
        print(json.dumps(usage, indent=2), flush=True)

    if args.measure:
        return
    pin = config["upstream"]
    report = {
        "appliance_id": args.appliance_id,
        "channel": args.channel,
        "alpine_branch": branch,
        "upstream_archive": pin["archive"],
        "upstream_archive_sha512": pin["archive_sha512"],
        "upstream_raw_sha256": pin["raw_sha256"],
        "signer_fingerprint": pin["signer_fingerprint"],
        "config_sha256": config_sha256,
        "lock_path": channel["lock"],
        "lock_sha256": hashlib.sha256(lock_data).hexdigest(),
        "kernel_release": release,
        "zfs_module_version": locked[f"zfs-{args.channel}"],
        "repository": args.repository,
        "commit": args.commit,
        "workflow_run_url": args.workflow_run_url,
    }
    package(disk, args.output, report, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--appliance-id", required=True)
    parser.add_argument("--upstream-inputs", type=Path, help="reuse downloads; still verify all pins")
    parser.add_argument("--measure", action="store_true", help="print filesystem usage without packaging")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repository")
    parser.add_argument("--commit")
    parser.add_argument("--workflow-run-url")
    args = parser.parse_args()
    try:
        config, config_sha256 = load_config(args.config)
        channel = build_channel(config, args.channel, args.appliance_id)
        require_linux_arm64()
        if not args.measure:
            require(args.output and not args.output.exists(), "choose a new artifact output directory")
            require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository or ""),
                    "a GitHub owner/repository is required")
            require(re.fullmatch(r"[a-f0-9]{40}", args.commit or ""), "a full commit SHA is required")
            prefix = f"https://github.com/{args.repository}/actions/runs/"
            require(re.fullmatch(re.escape(prefix) + r"[0-9]+", args.workflow_run_url or ""),
                    "the GitHub Actions run URL is required")
        with tempfile.TemporaryDirectory(prefix="macus-image-") as directory:
            build(args, config, config_sha256, channel, Path(directory))
    except subprocess.CalledProcessError as error:
        parser.exit(1, f"build command {error.cmd[0]} failed with status {error.returncode}\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
