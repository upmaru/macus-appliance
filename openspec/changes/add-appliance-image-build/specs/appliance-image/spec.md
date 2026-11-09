# Spec Delta

## ADDED Requirements

### Requirement: Locked channel package sets

Each kernel channel SHALL have one reviewed lock listing every package in its image at an exact version. The `lts` channel SHALL use `linux-lts` and `zfs-lts`, and the `stable` channel SHALL use `linux-stable` and `zfs-stable`. Both SHALL come from the `main` and `community` repositories of the same Alpine stable branch and share the same top-level userland list. Locks MUST NOT use edge, testing or third-party repositories. A lock SHALL change only through a human-reviewed refresh, never automatically in CI.

#### Scenario: Channel locks differ only in the kernel

- **WHEN** the `lts` and `stable` locks are refreshed from the same repository state
- **THEN** they differ only in kernel and ZFS-module packages and what those require, and the `stable` lock does not contain `linux-lts`

#### Scenario: Lock refresh

- **WHEN** a lock is refreshed
- **THEN** the full sorted closure is written for human review and no build or release follows automatically

### Requirement: Offline image build

The build SHALL start from the upstream Alpine archive verified against the pinned signer and the recorded archive and raw-disk digests, and SHALL install exactly the channel's lock into the image's root filesystem without booting it, with APK signature verification enabled. The image SHALL contain exactly one kernel flavor, its matching ZFS module and initramfs, and a boot configuration that selects that kernel. The build SHALL fail without producing artifacts when upstream verification fails, a locked version is unavailable, the installed set differs from the lock, more or fewer than one kernel is installed, the ZFS module does not match the kernel release, or the boot configuration does not select the installed kernel. The image MUST NOT contain Macus guest helpers, a machine ID, SSH host keys, cloud-init instance state, package caches, logs, credentials or build host paths.

#### Scenario: Locked build

- **WHEN** a build runs while Alpine still serves every locked version
- **THEN** the image contains exactly the lock, one kernel with its ZFS module and initramfs, and passes every static check

#### Scenario: Repository drift

- **WHEN** a locked version is unavailable or the installed set differs from the lock
- **THEN** the build fails and produces no image or release

#### Scenario: Image hygiene

- **WHEN** the image is prepared for packaging
- **THEN** it has no machine ID, host keys, cloud-init instance state, caches, logs, credentials or build paths, so Macus's seed runs as a first boot

### Requirement: Image record contract

Each image SHALL contain `/etc/macus-appliance-image` in format 1: ASCII LF-terminated `key=value` lines, in order, for `format`, `appliance_id`, `channel`, `alpine_branch`, `lock_sha256`, `manifest_sha256` and `kernel_release`, with no other keys. The installed manifest SHALL be the image's `apk info -v` output sorted with `LC_ALL=C`, each line LF-terminated, and `manifest_sha256` SHALL be the SHA-256 of exactly those bytes. `kernel_release` SHALL equal the installed kernel's module directory name, and `/etc/tama-zfs-qualified-kernel` SHALL contain the same value followed by LF. Any change to these keys, their order, the manifest rule or file locations SHALL use a new format number coordinated with Macus.

#### Scenario: Recomputable manifest

- **WHEN** Macus recomputes the manifest digest inside a booted image with the documented rule
- **THEN** it equals the image record's `manifest_sha256` and the release's published manifest

#### Scenario: Contract change

- **WHEN** a build would change the record keys, order, manifest rule or file locations
- **THEN** it uses a new `format` value, and format 1 images keep their meaning

### Requirement: Image artifacts

Each build SHALL produce a gzip tar archive containing exactly one regular member `disk.raw`, below 2 GiB, together with SHA-512 and SHA-256 sums for the archive and raw disk, the installed manifest, a build report and a build provenance attestation. The build report SHALL record the appliance ID, channel, upstream verification, lock and manifest digests, kernel release, sizes, digests, commit and workflow run, and MUST NOT contain secrets or runner paths. CI success MUST NOT be reported as boot, ZFS or hardware evidence.

#### Scenario: Artifacts match the image

- **WHEN** a build completes
- **THEN** the archive's single `disk.raw` matches the reported raw digest and its image record matches the reported manifest digest and kernel release

### Requirement: Draft release and retention

Image builds SHALL run only on manual dispatch with minimal permissions and SHA-pinned third-party actions, and SHALL create a draft release tagged with a new appliance ID that names the Alpine version and channel. A build MUST refuse an appliance ID that already has a release or tag. Workflows and agents MUST NOT publish a release, mark one latest, or delete or replace a published asset. A human SHALL publish a release only after Macus has recorded hardware qualification of that exact draft. Published images SHALL remain available and unchanged; any changed image requires a new appliance ID.

#### Scenario: Draft only

- **WHEN** the build workflow completes
- **THEN** its release is a draft and nothing is published

#### Scenario: Reused appliance ID

- **WHEN** a build is dispatched with an appliance ID that already has a release or tag
- **THEN** it fails before building

#### Scenario: Qualified image published

- **WHEN** Macus records hardware qualification for a draft image with matching digests
- **THEN** a human may publish that release, and it is never deleted or replaced afterwards

#### Scenario: Failed qualification

- **WHEN** Macus qualification of a draft image fails
- **THEN** the release stays unpublished and the next attempt uses a new appliance ID
