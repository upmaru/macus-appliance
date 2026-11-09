"""Manually refresh channel locks using signed Alpine ARM64 indexes and a verified base."""
import argparse
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile

from scripts.appliance_config import DEFAULT_CONFIG, INCUS_PACKAGES, load_config, require
from scripts.image_disk import GUEST_ENV, mounted_image, require_linux_arm64, run
from scripts.package_lock import VERSION, apk_query, decode_lock, resolve_lock, write_lock
from scripts.upstream import digest, download, download_upstream, verify_upstream


@contextmanager
def mount_indexes(root, snapshots):
    target = root / "mnt"
    run("mount", "--bind", snapshots, target)
    try:
        run("mount", "-o", "remount,bind,ro", target)
        yield
    finally:
        run("umount", target)


def refresh(config, work, inputs, channels, incus_version):
    snapshots = work / "indexes"
    snapshots.mkdir()
    paths = []
    index_digests = {}
    for repo in config["alpine"]["repositories"]:
        name = repo.rsplit("/", 1)[-1]
        filename = f"{name}.APKINDEX.tar.gz"
        path = snapshots / filename
        download(f"{repo}/{config['alpine']['arch']}/APKINDEX.tar.gz", path)
        paths.append(f"/mnt/{filename}")
        index_digests[name] = digest(path, "sha256")
    (snapshots / "repositories").write_text("".join(f"ndx {path}\n" for path in paths))

    disk = work / "disk.raw"
    verify_upstream(config["upstream"], inputs, disk)
    root = work / "root"
    root.mkdir()
    with mounted_image(disk, root, writable=True), mount_indexes(root, snapshots):
        command = ["chroot", str(root), "apk", "--logfile=no", "--cache=no", "--network=no",
                   "--repositories-file", "/mnt/repositories"]
        # Fail if either snapshot cannot be loaded with the verified image's Alpine keys.
        run(*command, "update", env=GUEST_ENV)
        base = apk_query(command, "--installed", "*", env=GUEST_ENV)
        locks = {
            name: resolve_lock(command, base, config["image"]["packages"],
                               config["channels"][name], incus_version, env=GUEST_ENV)
            for name in channels
        }
    return locks, index_digests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--channel", choices=("lts", "stable"))
    parser.add_argument("--incus-version", required=True, help="exact Incus APK revision to resolve")
    parser.add_argument("--upstream-inputs", type=Path, help="reuse downloads; still verify all pins")
    args = parser.parse_args()
    try:
        config, _ = load_config(args.config)
        require(VERSION.fullmatch(args.incus_version), "invalid Incus APK version")
        if args.channel:
            for name, channel in config["channels"].items():
                if name == args.channel:
                    continue
                peer_lock = args.config.parent / channel["lock"]
                if peer_lock.exists():
                    versions = decode_lock(peer_lock.read_bytes())
                    require(all(versions.get(package) == args.incus_version for package in INCUS_PACKAGES),
                            "refresh both channels together when changing the Incus pins")
        require_linux_arm64()
        with tempfile.TemporaryDirectory(prefix="macus-lock-") as directory:
            work = Path(directory)
            inputs = args.upstream_inputs
            if inputs is None:
                inputs = work / "upstream"
                inputs.mkdir()
                download_upstream(config["upstream"], inputs)
            channels = (args.channel,) if args.channel else config["channels"]
            locks, index_digests = refresh(config, work, inputs, channels, args.incus_version)
        for channel, data in locks.items():
            write_lock(args.config.parent / config["channels"][channel]["lock"], data)
        for repo, sha256 in index_digests.items():
            print(f"{repo} APKINDEX SHA-256 {sha256}")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
