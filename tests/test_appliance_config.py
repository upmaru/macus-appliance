import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.appliance_config import build_channel, load_config


FIXTURE = Path(__file__).parent / "fixtures" / "appliance.toml"


class ApplianceConfigTest(unittest.TestCase):
    def test_repository_config_loads_for_both_channels(self):
        root = FIXTURE.resolve().parents[2]
        config, _ = load_config(root / "appliance.toml")
        version = config["alpine"]["version"]
        for channel in ("lts", "stable"):
            selected = build_channel(config, channel, f"alpine-{version}-{channel}-1")
            self.assertTrue((root / selected["lock"]).is_file())

    def test_valid_config(self):
        config, digest = load_config(FIXTURE)
        self.assertEqual(digest, hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
        channel = build_channel(config, "stable", "alpine-3.24.2-stable-1")
        self.assertEqual(channel["remove"], ["linux-lts"])
        for name, appliance_id in [
            ("edge", "alpine-3.24.2-edge-1"),
            ("lts", "alpine-3.24.2-stable-1"),
            ("lts", "alpine-3.24.2-lts-0"),
        ]:
            with self.subTest(name=name, appliance_id=appliance_id), self.assertRaises(ValueError):
                build_channel(config, name, appliance_id)

    def test_invalid_config(self):
        text = FIXTURE.read_text()
        cases = [
            ("schema = 1", ""),
            ("schema = 1", "schema = 1\nextra = 1"),
            ("schema = 1", "schema = true"),
            ("schema = 1", "schema = 2"),
            ('archive_sha512 = "', 'archive_sha512 = "a'),
            ('raw_sha256 = "', 'raw_sha256 = "X'),
            ('signer_fingerprint = "F', 'signer_fingerprint = "f'),
            ('url = "https:', 'url = "http:'),
            ('signing_key_url = "https:', 'signing_key_url = "http:'),
            ("/v3.24/community", "/edge/community"),
            ("/v3.24/main", "/v3.23/main"),
            ('kernel_flavor = "lts"', 'kernel_flavor = "stable"'),
            ('"linux-stable", "zfs-stable"', '"linux-lts", "zfs-stable"'),
            ('"incus",', '"incus=7.0.1-r2",'),
            ("4294967296", "0"),
            ("4294967296", "1073741823"),
            ("locks/stable.lock", "../stable.lock"),
            ("locks/stable.lock", "locks/../stable.lock"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "appliance.toml"
            for original, invalid in cases:
                with self.subTest(original=original, invalid=invalid):
                    self.assertIn(original, text)
                    config.write_text(text.replace(original, invalid, 1))
                    with self.assertRaises(ValueError):
                        load_config(config)

    def test_lock_symlink_cannot_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "appliance.toml"
            config.write_bytes(FIXTURE.read_bytes())
            (root / "locks").mkdir()
            (root / "locks/lts.lock").symlink_to(root / "escaped.lock")
            with self.assertRaisesRegex(ValueError, "escapes locks"):
                load_config(config)
