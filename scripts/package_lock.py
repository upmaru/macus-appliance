"""Canonical exact package locks and the native APK solver interface."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

from scripts.appliance_config import INCUS_PACKAGES, PACKAGE, require


VERSION = re.compile(r"[0-9][A-Za-z0-9_.+-]*")


def encode_lock(packages):
    require(packages and all(PACKAGE.fullmatch(name) and VERSION.fullmatch(version)
                             for name, version in packages.items()), "invalid package lock entry")
    return "".join(sorted(f"{name}={version}\n" for name, version in packages.items())).encode("ascii")


def decode_lock(data):
    packages = {}
    for entry in data.decode("ascii").splitlines():
        name, separator, version = entry.partition("=")
        require(separator and name not in packages, f"invalid or duplicate lock entry: {entry}")
        packages[name] = version
    require(encode_lock(packages) == data, "lock must use sorted name=version LF-terminated lines")
    return packages


def apk_query(command, *args, env=None):
    result = subprocess.run(
        [*command, "query", "--format", "json", "--fields", "name,version", *args],
        check=True, capture_output=True, text=True, env=env,
    )
    require(not result.stderr, result.stderr.strip())
    packages = json.loads(result.stdout)
    require(all("name" in package and "version" in package for package in packages),
            "APK could not resolve all package constraints")
    versions = {package["name"]: package["version"] for package in packages}
    require(len(versions) == len(packages), "APK returned multiple versions of a package")
    return versions


def resolve_lock(command, base_names, shared, channel, incus_version, *, env=None):
    required = (set(base_names) | set(shared) | set(channel["packages"])) - set(channel["remove"])
    constraints = sorted(required - INCUS_PACKAGES)
    constraints.extend(f"{name}={incus_version}" for name in sorted(INCUS_PACKAGES))
    constraints.extend(f"!{name}" for name in channel["remove"])
    versions = apk_query(command, "--from", "repositories", "--recursive", *constraints, env=env)
    require(required <= versions.keys(), "APK omitted a required package")
    require(not versions.keys() & set(channel["remove"]), "APK selected a removed package")
    require(all(versions.get(name) == incus_version for name in INCUS_PACKAGES),
            "APK did not select the requested Incus pins")
    return encode_lock(versions)


def write_lock(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        staged = Path(stream.name)
    try:
        staged.write_bytes(data)
        staged.chmod(0o644)
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)
