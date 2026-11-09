"""Load the reviewed schema-1 appliance configuration."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tomllib
from urllib.parse import urlsplit


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "appliance.toml"
PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]*")
INCUS_PACKAGES = {"incus", "incus-client", "incus-openrc", "incus-vm"}
CHANNELS = {
    "lts": (["linux-lts", "zfs-lts"], []),
    "stable": (["linux-stable", "zfs-stable"], ["linux-lts"]),
}


def table(value, label, **fields):
    if type(value) is not dict:
        raise ValueError(f"{label} must be a table")
    missing = fields.keys() - value.keys()
    unknown = value.keys() - fields.keys()
    if missing or unknown:
        raise ValueError(f"{label}: missing keys {sorted(missing)}, unknown keys {sorted(unknown)}")
    for key, expected_type in fields.items():
        if type(value[key]) is not expected_type:
            raise ValueError(f"{label}.{key} must be a {expected_type.__name__}")
    return value


def require(condition, message):
    if not condition:
        raise ValueError(message)


def https_url(value):
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.port in (None, 443)
        and not parsed.query
        and not parsed.fragment
        and not any(character.isspace() or ord(character) < 32 for character in value)
    )


def load_config(path=DEFAULT_CONFIG):
    raw = Path(path).read_bytes()
    config = table(
        tomllib.loads(raw.decode("utf-8")), "config",
        schema=int, alpine=dict, upstream=dict, image=dict, channels=dict,
    )
    require(config["schema"] == 1, "schema must be 1")
    alpine = table(
        config["alpine"], "alpine", version=str, branch=str, arch=str, repositories=list,
    )
    require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", alpine["version"]), "invalid Alpine version")
    require(alpine["branch"] == "v" + alpine["version"].rsplit(".", 1)[0],
            "Alpine branch must match the stable release version")
    require(alpine["arch"] == "aarch64", "Alpine architecture must be aarch64")
    mirror = f'https://dl-cdn.alpinelinux.org/alpine/{alpine["branch"]}'
    require(alpine["repositories"] == [f"{mirror}/main", f"{mirror}/community"],
            "repositories must be the stable branch's main and community")

    upstream = table(
        config["upstream"], "upstream", archive=str, url=str, archive_sha512=str,
        archive_bytes=int, raw_sha256=str, raw_bytes=int, signing_key_url=str,
        signer_fingerprint=str,
    )
    for key, pattern in (
        ("archive_sha512", r"[0-9a-f]{128}"),
        ("raw_sha256", r"[0-9a-f]{64}"),
        ("signer_fingerprint", r"[0-9A-F]{40}"),
    ):
        require(re.fullmatch(pattern, upstream[key]), f"invalid upstream.{key}")
    for key in ("url", "signing_key_url"):
        require(https_url(upstream[key]), f"upstream.{key} must be an HTTPS URL")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.raw\.tar\.gz", upstream["archive"]),
            "invalid upstream archive filename")
    require(urlsplit(upstream["url"]).path.rsplit("/", 1)[-1] == upstream["archive"],
            "upstream URL must match the archive filename")
    require(upstream["archive_bytes"] > 0 and upstream["raw_bytes"] > 0,
            "upstream sizes must be positive")

    image = table(config["image"], "image", raw_bytes=int, packages=list)
    require(image["raw_bytes"] >= upstream["raw_bytes"], "image.raw_bytes is smaller than upstream")
    require(all(type(name) is str and PACKAGE.fullmatch(name) for name in image["packages"]),
            "image.packages must contain APK package names")
    names = set(image["packages"])
    require(len(names) == len(image["packages"]), "image.packages contains duplicates")
    require(INCUS_PACKAGES <= names, "image.packages must include the four Incus packages")
    require(not any(name.startswith(("linux-", "zfs-lts", "zfs-stable")) for name in names),
            "kernel packages belong in channel tables")

    channels = table(config["channels"], "channels", lts=dict, stable=dict)
    lock_paths = set()
    for name, (packages, remove) in CHANNELS.items():
        channel = table(channels[name], f"channels.{name}", kernel_flavor=str,
                        packages=list, remove=list, lock=str)
        require(channel["kernel_flavor"] == name, f"kernel flavor must match channel {name}")
        require(sorted_entries(channel["packages"]) == sorted(packages)
                and channel["remove"] == remove, f"invalid kernel packages for channel {name}")
        lock = PurePosixPath(channel["lock"])
        require(not lock.is_absolute() and lock.parts[:1] == ("locks",)
                and all(PACKAGE.fullmatch(part) for part in lock.parts) and lock.suffix == ".lock",
                f"channel {name} lock must be inside locks/")
        target = (Path(path).parent / lock).resolve()
        require(target.is_relative_to(Path(path).parent.resolve() / "locks"),
                f"channel {name} lock escapes locks/")
        require(target not in lock_paths, "channel locks must be distinct")
        lock_paths.add(target)
    return config, hashlib.sha256(raw).hexdigest()


def sorted_entries(entries):
    require(all(type(entry) is str for entry in entries), "channel packages must be strings")
    return sorted(entries)


def build_channel(config, channel, appliance_id):
    require(channel in config["channels"], f"unknown kernel channel: {channel}")
    prefix = f'alpine-{config["alpine"]["version"]}-{channel}-'
    require(re.fullmatch(re.escape(prefix) + r"[1-9][0-9]*", appliance_id),
            f"appliance ID must match {prefix}<positive integer>")
    return config["channels"][channel]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--appliance-id", required=True)
    args = parser.parse_args()
    try:
        config, _ = load_config(args.config)
        print(json.dumps(build_channel(config, args.channel, args.appliance_id)))
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
