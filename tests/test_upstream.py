import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

from scripts.upstream import digest, verify_upstream


@unittest.skipUnless(shutil.which("gpg"), "requires GnuPG")
class UpstreamTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        home = Path(directory.name)
        cls.addClassCleanup(subprocess.run, ["gpgconf", "--homedir", str(home), "--kill", "gpg-agent"],
                            capture_output=True)
        cls.gpg = ["gpg", "--homedir", str(home), "--batch", "--pinentry-mode", "loopback",
                   "--passphrase", ""]
        cls.signer = cls.generate_key("Alpine fixture <fixture@example.invalid>")
        cls.other_signer = cls.generate_key("Other signer <other@example.invalid>")

    @classmethod
    def generate_key(cls, identity):
        subprocess.run(cls.gpg + ["--quick-generate-key", identity, "ed25519", "sign", "0"],
                       check=True, capture_output=True)
        result = subprocess.check_output(cls.gpg + ["--with-colons", "--list-keys", identity],
                                         text=True)
        return next(line.split(":")[9] for line in result.splitlines() if line.startswith("fpr:"))

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.inputs = Path(directory.name)
        self.archive = self.inputs / "upstream.raw.tar.gz"
        self.output = self.inputs / "disk.raw"
        self.raw = b"raw disk fixture\x00"

    def signed_archive(self, member_names=("disk.raw",), member_type=tarfile.REGTYPE):
        with tarfile.open(self.archive, "w:gz") as archive:
            for name in member_names:
                member = tarfile.TarInfo(name)
                member.type = member_type
                if member.isfile():
                    member.size = len(self.raw)
                    archive.addfile(member, io.BytesIO(self.raw))
                else:
                    member.linkname = "/etc/passwd"
                    archive.addfile(member)
        raw = self.inputs / "expected.raw"
        raw.write_bytes(self.raw)
        pin = {
            "archive": self.archive.name,
            "archive_bytes": self.archive.stat().st_size,
            "archive_sha512": digest(self.archive, "sha512"),
            "raw_bytes": len(self.raw),
            "raw_sha256": digest(raw, "sha256"),
            "signer_fingerprint": self.signer,
        }
        self.checksum = self.archive.with_suffix(".gz.sha512")
        self.checksum.write_text(pin["archive_sha512"] + "\n")
        subprocess.run(self.gpg + ["--armor", "--local-user", self.signer, "--detach-sign",
                                   str(self.archive)], check=True, capture_output=True)
        with (self.inputs / "signing-key.asc").open("wb") as key:
            subprocess.run(self.gpg + ["--armor", "--export"], stdout=key, check=True)
        return pin

    def test_signed_archive(self):
        verify_upstream(self.signed_archive(), self.inputs, self.output)
        self.assertEqual(self.output.read_bytes(), self.raw)

    def test_wrong_archive_digest(self):
        pin = self.signed_archive()
        pin["archive_sha512"] = "a" * 128
        self.checksum.write_text(pin["archive_sha512"] + "\n")
        with self.assertRaisesRegex(ValueError, "archive SHA-512 mismatch"):
            verify_upstream(pin, self.inputs, self.output)
        self.assertFalse(self.output.exists())

    def test_wrong_raw_digest(self):
        pin = self.signed_archive()
        pin["raw_sha256"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "raw disk SHA-256 mismatch"):
            verify_upstream(pin, self.inputs, self.output)
        self.assertFalse(self.output.exists())

    def test_wrong_signer(self):
        pin = self.signed_archive()
        pin["signer_fingerprint"] = self.other_signer
        with self.assertRaisesRegex(ValueError, "pinned signer"):
            verify_upstream(pin, self.inputs, self.output)
        self.assertFalse(self.output.exists())

    def test_extra_member(self):
        pin = self.signed_archive(("disk.raw", "extra"))
        with self.assertRaisesRegex(ValueError, "exactly one regular"):
            verify_upstream(pin, self.inputs, self.output)
        self.assertFalse(self.output.exists())

    def test_link_member(self):
        pin = self.signed_archive(member_type=tarfile.SYMTYPE)
        with self.assertRaisesRegex(ValueError, "exactly one regular"):
            verify_upstream(pin, self.inputs, self.output)
        self.assertFalse(self.output.exists())
