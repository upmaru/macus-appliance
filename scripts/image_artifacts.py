"""Stage the verified disk and its format-1 evidence as release assets."""
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile

from scripts.appliance_config import require
from scripts.upstream import digest


ASSET_LIMIT = 2 * 1024**3


def package(disk, output, report, manifest):
    appliance_id = report["appliance_id"]
    require(not output.exists(), "artifact output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".appliance-assets-", dir=output.parent) as directory:
        staged = Path(directory) / "assets"
        staged.mkdir()
        archive = staged / f"{appliance_id}.raw.tar.gz"
        with tarfile.open(archive, "w:gz", format=tarfile.GNU_FORMAT) as stream:
            member = tarfile.TarInfo("disk.raw")
            member.size = disk.stat().st_size
            member.mode = 0o644
            with disk.open("rb") as raw:
                stream.addfile(member, raw)
        require(archive.stat().st_size < ASSET_LIMIT, "compressed archive must be below 2 GiB")

        report = {
            **report,
            "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
            "raw_bytes": disk.stat().st_size,
            "raw_sha256": digest(disk, "sha256"),
            "archive_bytes": archive.stat().st_size,
            "archive_sha512": digest(archive, "sha512"),
        }
        (staged / "SHA512SUMS").write_text(f'{report["archive_sha512"]}  {archive.name}\n')
        (staged / "SHA256SUMS").write_text(
            f'{digest(archive, "sha256")}  {archive.name}\n{report["raw_sha256"]}  disk.raw\n'
        )
        (staged / f"{appliance_id}.manifest").write_bytes(manifest)
        (staged / f"{appliance_id}.build.json").write_text(json.dumps(report, indent=2) + "\n")
        staged.rename(output)
