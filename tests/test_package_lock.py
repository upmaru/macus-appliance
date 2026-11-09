import base64
import gzip
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from scripts.appliance_config import CHANNELS, INCUS_PACKAGES
from scripts.package_lock import decode_lock, resolve_lock


APK_IMAGE = "alpine@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6"


def tar_bytes(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in entries:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        end = archive.offset
    return stream.getvalue()[:end]


def signed_index(path, key, packages):
    records = []
    for name, version, depends, provides in packages:
        checksum = base64.b64encode(hashlib.sha1(f"{name}={version}".encode()).digest()).decode()
        records.append(f"C:Q1{checksum}\nP:{name}\nV:{version}\nA:aarch64\nS:1\nI:1\n"
                       f"T:Fixture package\nL:MIT\nD:{depends}\np:{provides}\n")
    index = "\n".join(records).encode()
    payload = gzip.compress(tar_bytes([("DESCRIPTION", b"appliance test"), ("APKINDEX", index)])
                            + b"\0" * 1024, mtime=0)
    signature = subprocess.check_output(["openssl", "dgst", "-sha1", "-sign", key], input=payload)
    path.write_bytes(gzip.compress(tar_bytes([(".SIGN.RSA.fixture.rsa.pub", signature)]), mtime=0)
                     + payload)


class LockFormatTest(unittest.TestCase):
    def test_rejects_noncanonical_lock(self):
        for raw in (b"", b"# lock\na=1\n", b"a=1", b"a=1\r\n", b"a=1\na=2\n",
                    b"z=1\na=1\n", b"a=1\n\n"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_lock(raw)
        self.assertEqual(decode_lock(b"a=1\nb=2-r0\n"), {"a": "1", "b": "2-r0"})

    def test_reviewed_locks_use_the_same_incus_pins(self):
        root = Path(__file__).resolve().parents[1]
        lts = decode_lock((root / "locks/lts.lock").read_bytes())
        stable = decode_lock((root / "locks/stable.lock").read_bytes())
        self.assertEqual({name: lts[name] for name in INCUS_PACKAGES},
                         {name: stable[name] for name in INCUS_PACKAGES})
        self.assertNotIn("linux-lts", stable)


@unittest.skipUnless(os.environ.get("APPLIANCE_APK_FIXTURES") == "1", "enable native APK fixtures")
class APKIndexTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        keys = self.root / "keys"
        keys.mkdir()
        key = self.root / "fixture.rsa"
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                        "-out", key], check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", key, "-pubout", "-out", keys / "fixture.rsa.pub"],
                       check=True, capture_output=True)
        main = [
            ("base", "1-r0", "base-lib", ""),
            ("base-lib", "1-r0", "", ""),
            ("linux-lts", "6-r0", "fw-lts", ""),
            ("fw-lts", "1-r0", "", ""),
            ("zfs-lts", "2-r0", "linux-lts=6-r0", ""),
            ("shared-provider", "1-r0", "", "so:shared.so.1=1"),
        ]
        community = [
            ("linux-stable", "7-r0", "fw-stable", ""),
            ("fw-stable", "1-r0", "", ""),
            ("zfs-stable", "2-r0", "linux-stable=7-r0", ""),
            ("guest", "1-r0", "so:shared.so.1", ""),
        ] + [(name, version, "", "") for name in sorted(INCUS_PACKAGES) for version in ("1-r0", "2-r0")]
        signed_index(self.root / "main.APKINDEX.tar.gz", key, main)
        signed_index(self.root / "community.APKINDEX.tar.gz", key, community)
        key.unlink()
        (self.root / "repositories").write_text(
            "ndx /indexes/main.APKINDEX.tar.gz\nndx /indexes/community.APKINDEX.tar.gz\n"
        )
        self.command = [
            "docker", "run", "--rm", "--network", "none",
            "-v", f"{self.root}:/indexes:ro", APK_IMAGE, "apk",
            "--arch", "aarch64", "--cache=no", "--network=no", "--keys-dir", "/indexes/keys",
            "--repositories-file", "/indexes/repositories",
        ]

    def resolve(self, flavor):
        packages, remove = CHANNELS[flavor]
        return resolve_lock(self.command, {"base"}, {"guest", *INCUS_PACKAGES},
                            {"packages": packages, "remove": remove}, "1-r0")

    def test_channels_are_deterministic_and_keep_base_and_shared_dependencies(self):
        lts = self.resolve("lts")
        stable = self.resolve("stable")
        self.assertEqual(self.resolve("lts"), lts)
        self.assertEqual(self.resolve("stable"), stable)
        lts_versions, stable_versions = map(decode_lock, (lts, stable))
        self.assertEqual(set(lts_versions) - set(stable_versions), {"linux-lts", "zfs-lts", "fw-lts"})
        self.assertEqual(set(stable_versions) - set(lts_versions),
                         {"linux-stable", "zfs-stable", "fw-stable"})
        self.assertEqual({name: lts_versions[name] for name in set(lts_versions) & set(stable_versions)},
                         {name: "1-r0" for name in {"base", "base-lib", "guest", "shared-provider",
                                                     *INCUS_PACKAGES}})
        self.assertNotIn("linux-lts", stable_versions)

    def test_untrusted_index_cannot_produce_lock(self):
        (self.root / "keys/fixture.rsa.pub").unlink()
        with self.assertRaisesRegex(ValueError, "UNTRUSTED signature"):
            self.resolve("stable")
