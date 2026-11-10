# Appliance image contract, format 1

Macus pins an appliance archive and its raw disk by digest. It supplies the expected record through its NoCloud seed and checks the record and installed manifest at first boot.

## Records in the image

`/etc/macus-appliance-image` contains exactly these seven ASCII lines in order. Each line ends in LF, including the last one.

```text
format=1
appliance_id=<id>
channel=<lts|stable>
alpine_branch=v3.24
lock_sha256=<lowercase SHA-256>
manifest_sha256=<lowercase SHA-256>
kernel_release=<release>
```

`lock_sha256` covers the exact bytes of the channel's lock. The lock lists one `name=version` per line and uses C-locale sort order and LF endings.

The installed manifest is the image's `apk info -v` output sorted with `LC_ALL=C sort`. Each line ends in LF. For example, an LTS entry looks like `linux-lts-6.18.62-r0`. `manifest_sha256` covers those exact bytes and matches the digest of the released manifest.

`kernel_release` is the installed kernel's module directory name, such as `6.18.62-0-lts`. `/etc/tama-zfs-qualified-kernel` contains the same value followed by LF. Macus checks the running kernel during qualification and first boot.

Changes to these keys, their order, the manifest rule, or either file location require a new format number and a coordinated Macus change.

## Release assets

| File | Contents |
| --- | --- |
| `<id>.raw.tar.gz` | Gzip-compressed tar under 2 GiB with exactly one regular member, `disk.raw` |
| `SHA512SUMS` | SHA-512 of the compressed archive |
| `SHA256SUMS` | SHA-256 of the compressed archive and of `disk.raw` |
| `<id>.manifest` | Installed manifest bytes used for the image record |
| `<id>.build.json` | Build report |

The build report contains the following fields.

| Fields | Meaning |
| --- | --- |
| `appliance_id`, `channel`, `alpine_branch` | Image identity |
| `upstream_archive`, `upstream_archive_sha512`, `upstream_raw_sha256`, `signer_fingerprint` | Verified upstream image and signer |
| `config_sha256`, `lock_path`, `lock_sha256`, `manifest_sha256` | Configuration and package inputs and the installed manifest digest |
| `kernel_release`, `zfs_module_version` | Kernel module directory name and ZFS module APK version |
| `raw_bytes`, `raw_sha256`, `archive_bytes`, `archive_sha512` | Exact sizes and digests of the output image |
| `repository`, `commit`, `workflow_run_url` | Source repository, build commit, and GitHub Actions run |

GitHub associates a build provenance attestation with the archive. The Macus catalog's pinned digests govern runtime trust.
