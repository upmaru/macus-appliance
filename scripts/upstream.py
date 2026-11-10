"""Verify the pinned Alpine cloud image before exposing its raw disk."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

from scripts.appliance_config import require


def digest(path, algorithm):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def download(url, path):
    subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--location",
         "--proto", "=https", "--proto-redir", "=https", "--retry", "2",
         "--connect-timeout", "20", "--output", str(path), "--", url],
        check=True,
    )


def verify_signature(archive, signature, signing_key, fingerprint):
    with tempfile.TemporaryDirectory(prefix="macus-gpg-") as home:
        gpg = ["gpg", "--homedir", home, "--batch", "--no-autostart"]
        subprocess.run(gpg + ["--import", str(signing_key.resolve())],
                       check=True, capture_output=True)
        result = subprocess.run(
            gpg + ["--status-fd", "1", "--verify", str(signature.resolve()),
                   str(archive.resolve())],
            capture_output=True, text=True,
        )
    for line in result.stdout.splitlines():
        status = line.split()
        if len(status) >= 12 and status[:2] == ["[GNUPG:]", "VALIDSIG"]:
            if result.returncode == 0 and fingerprint in (status[2], status[11]):
                return
    raise ValueError("archive signature does not match the pinned signer")


def extract_disk(archive, output, raw_bytes, raw_sha256):
    with tarfile.open(archive, "r|gz") as tar:
        member = tar.next()
        require(member is not None and member.name == "disk.raw" and member.isfile(),
                "expected exactly one regular disk.raw archive member")
        require(member.size == raw_bytes, "raw disk size mismatch")
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".disk-", delete=False) as stream:
            staged = Path(stream.name)
            try:
                with tar.extractfile(member) as source:
                    shutil.copyfileobj(source, stream)
                stream.close()
                require(tar.next() is None, "expected exactly one regular disk.raw archive member")
                require(digest(staged, "sha256") == raw_sha256, "raw disk SHA-256 mismatch")
                output.hardlink_to(staged)
            finally:
                staged.unlink(missing_ok=True)


def verify_upstream(pin, inputs, output):
    archive = inputs / pin["archive"]
    checksum = archive.with_name(archive.name + ".sha512")
    sidecar = checksum.read_text(encoding="ascii").split()
    require(sidecar and sidecar[0].lower() == pin["archive_sha512"],
            "upstream sidecar SHA-512 differs from the pin")
    require(archive.stat().st_size == pin["archive_bytes"], "archive size mismatch")
    require(digest(archive, "sha512") == pin["archive_sha512"], "archive SHA-512 mismatch")
    verify_signature(
        archive, archive.with_name(archive.name + ".asc"), inputs / "signing-key.asc",
        pin["signer_fingerprint"],
    )
    extract_disk(archive, output, pin["raw_bytes"], pin["raw_sha256"])


def download_upstream(pin, inputs):
    archive = inputs / pin["archive"]
    for suffix in ("", ".sha512", ".asc"):
        download(pin["url"] + suffix, archive.with_name(archive.name + suffix))
    download(pin["signing_key_url"], inputs / "signing-key.asc")
