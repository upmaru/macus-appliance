# Proposal

## Why

Fresh Macus appliances install their kernel, ZFS and Incus packages live from Alpine's v3.24 repositories on first boot. The guest bootstrap pins the kernel/ZFS pair exactly and refuses to substitute, which is correct: the ZFS module must match the booted kernel, and the combination must be hardware-qualified. But Alpine keeps only the newest revision of each package on a stable branch, so every upstream kernel update breaks `macus start` for every new user until Macus requalifies and ships a new pin:

- On 2026-11-01 Alpine replaced `linux-lts`/`zfs-lts` 6.18.55-r0 with 6.18.60-r0, and fresh starts failed with `TAMA_ZFS_QUALIFIED_REVISION_UNAVAILABLE`.
- By 2026-11-08 the repository served 6.18.62-r0, and fresh starts failed the same way again.

Incus also floats (`incus>=7.0.1`), so two fresh appliances can differ. The defect is that qualified packages are fetched at first boot from a source that deletes them. Building the image ahead of time, with an exact lock, removes that dependency.

The build lives in this repository rather than in Macus so that its release-writing workflow cannot touch Macus code or releases, Macus's release page lists only Macus versions, and the Linux image tooling stays out of a Swift/macOS codebase. Macus keeps the trust decision: an image reaches users only when a Macus release pins its digest.

## What Changes

- **Locked package sets per kernel channel:** two channels from the same Alpine v3.24 branch. `lts` carries `linux-lts`/`zfs-lts` from `main` (6.18.62 on 2026-11-09) and is Macus's default. `stable` carries `linux-stable`/`zfs-stable` from `community` (7.1.5 on 2026-11-09) for users who opt into a newer kernel. Both share the same ZFS 2.4.4, Incus 7.0.1 and QEMU 11.0.3 userland. Each channel has its own full, sorted lock that a human refreshes and reviews. Alpine edge is not used.
- **Offline image build:** a manually dispatched GitHub Actions workflow on a GitHub-hosted Linux arm64 runner verifies the upstream Alpine 3.24.2 cloud image against Alpine's pinned signer and recorded digests, then installs the channel's exact locked packages into the image's root filesystem in a chroot, without booting it. An image carries exactly one kernel flavor.
- **Static verification in CI:** installed packages equal the lock, the kernel, ZFS module and initramfs match, the boot configuration selects the channel's kernel, and the image carries no machine ID, host keys, cloud-init instance state, caches, logs or credentials. CI cannot boot the image and does not claim to.
- **Versioned image record:** each image carries `/etc/macus-appliance-image` (format 1: appliance ID, channel, Alpine branch, lock digest, installed-manifest digest, kernel release) and `/etc/tama-zfs-qualified-kernel`. The manifest digest is defined exactly, so Macus can recompute it at first boot.
- **Draft releases only:** the workflow attaches the compressed image, digests, installed manifest, build report and a build provenance attestation to a **draft** release tagged with a new appliance ID. A human publishes it only after Macus records hardware qualification for that exact image. Published images are never deleted or replaced.

## Capabilities

### New Capabilities

- `appliance-image`: locked per-channel package sets, the offline image build, static verification, the image record contract, artifact format, draft release publication and retention.

### Modified Capabilities

None. This repository has no existing specs.

## Impact

- New in this repository: build inputs, per-channel locks, a lock-refresh script, the build and static-check scripts, `.github/workflows/appliance-image.yml`, tests with fixture data, and documentation of the image contract and release flow.
- Macus consumes the images through a separate change (upmaru/macus, `prebuilt-appliance-image`): its catalog pins published images from this repository's releases by digest, its seed carries the expected record values, its first boot verifies them, it selects the channel and it runs hardware qualification. This change does not modify Macus.
- No effect on existing Macus appliances.
