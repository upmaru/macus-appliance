# Design

## Context

**Fresh Macus provisioning today:** `macus start` downloads the official Alpine 3.24.2 aarch64 cloud-init metal archive, verifies its archive SHA-512 and raw-disk SHA-256 against Macus's shipped catalog, and stages `disk.raw` as the appliance root. A NoCloud seed writes the guest helpers and runs `bootstrap.sh`, which installs Incus, QEMU and related packages, then the exact kernel/ZFS pair, from the live v3.24 repositories, and reboots into the new kernel. The install set today is:

- `incus`, `incus-client`, `incus-openrc`, `incus-vm` (each `>=7.0.1`), `skopeo`
- `qemu-system-aarch64`, `qemu-audio-spice`, `qemu-hw-display-virtio-gpu-pci`, `qemu-hw-usb-host`, `qemu-chardev-spice`, `qemu-hw-usb-redirect`, `qemu-ui-spice-core`, `aavmf`, `virtiofsd`
- `ca-certificates`, `e2fsprogs`, `e2fsprogs-extra`, `util-linux-misc`, `blkid`, `python3`, `shadow-subids`, `lxcfs`, `lxcfs-openrc`, `nftables`, `acpid`, `acpid-openrc`
- `linux-lts`, `zfs`, `zfs-libs`, `zfs-lts`, `zfs-openrc`, after removing `/boot/dtbs-lts` to free EFI partition space

The source of truth for that list is Macus's `Integration/guest/bootstrap.sh` at the time this change is implemented.

**Upstream base:** `alpine-3.24.2-aarch64-cloudinit-metal-r0.raw.tar.gz` from `https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/cloud/`, archive SHA-512 `d8ed6f0fa98d15218c351d2903d961b2cec68a7fd39ec8567fae9c49d943ae21f5cd56754497480a0cf899332acc632617b531b153e12e81d1f021c81f634518` (626,889,284 bytes), raw `disk.raw` SHA-256 `47c0b69be2a3458c2a1ff11519e8f4b3f6565a73e89a2fe2294ed0ca3e496516` (1,073,741,824 bytes), signed by Alpine's cloud key with fingerprint `F26ADFADBAE702EF7AF637459DA7EF23BFFCDF22` (`https://alpinelinux.org/keys/tomalok.asc`). These match Macus's `Integration/catalog/alpine-3.24.2.json`, and Macus's `Integration/scripts/verify-appliance.py` is the existing verifier. The base image ships `linux-lts`.

**Kernel flavors on v3.24 (aarch64 package indexes, 2026-11-09):**

| Flavor | Repository | Kernel | ZFS module | Shared userland |
|---|---|---|---|---|
| `lts` | `main` | `linux-lts` 6.18.62-r0 | `zfs-lts` 6.18.62-r0 | `zfs` 2.4.4-r0, `incus` 7.0.1-r2, `qemu-system-aarch64` 11.0.3-r0 |
| `stable` | `community` | `linux-stable` 7.1.5-r0 | `zfs-stable` 7.1.5-r1 | same |

Each ZFS module package depends on its exact kernel revision. Alpine edge has a newer kernel (7.3.3) but a different Incus and QEMU and a rolling userland, so it is not used.

**Constraints:**
- GitHub-hosted runners cannot run Apple Virtualization.framework guests, so CI cannot boot the appliance. Boot, storage, ZFS and relay evidence comes only from Macus's opt-in hardware acceptance.
- Releases are published, and builds that create draft releases are dispatched, only with user authorization.
- Macus's runtime trust root is the digest in its shipped catalog. Nothing in this repository is trusted by Macus at runtime except through that digest.

## Goals / Non-Goals

**Goals:**
- Every package in an image, including Incus, is fixed by a reviewed lock and recorded in a manifest.
- An image can be rebuilt from its lock while Alpine still serves those versions, and a published image keeps working after Alpine moves on.
- Each image carries a small, versioned record Macus can verify offline at first boot.
- `lts` and `stable` images are built, released and retained independently.

**Non-Goals:**
- Booting, qualifying or benchmarking images. Macus owns hardware qualification.
- Guest helper scripts, the NoCloud seed, the Macus catalog, channel selection or first-boot verification. Those stay in Macus.
- Bit-for-bit reproducible images. The published digest, not reproducibility, is the trust root.
- Alpine edge, testing or third-party repositories, or kernels from another branch.
- Automatic lock refresh, automatic builds, or publishing releases.

## Decisions

### 1. Install into the image on a Linux arm64 runner, without booting it

The workflow runs on a GitHub-hosted `ubuntu-24.04-arm` runner. The repository is public, so these runners are available without self-hosting. Installing into the image needs only native arm64 execution, loop devices and `chroot`.

Build steps:
1. Download the archive, its `.sha512` and `.asc`, and the signing key named in the build inputs. Verify the signature against the pinned fingerprint in an isolated GnuPG home, and verify the archive SHA-512 and extracted raw SHA-256 against the digests recorded in the build inputs, not only the downloaded sidecar. Extract exactly one regular `disk.raw` member.
2. Extend `disk.raw` to the build size and grow the root partition and filesystem.
3. Attach it as a loop device with partitions, mount the root and boot/EFI partitions, and bind-mount `/dev`, `/proc` and `/sys` for package triggers. Provide a temporary `resolv.conf`.
4. In a `chroot`, configure only `https://dl-cdn.alpinelinux.org/alpine/v3.24/main` and `/community`, run `apk update`, remove the channel's unused `/boot/dtbs-<flavor>` tree (the existing EFI space workaround), and install exactly the channel's lock. APK signature verification stays enabled with the image's own Alpine keys. For `stable`, remove `linux-lts` and its ZFS module so one kernel remains.
5. Let package triggers (`mkinitfs` and the boot configuration update) run inside the chroot.
6. Write the image record (Decision 5), clean the image (Decision 4), zero free blocks, unmount, detach and package (Decision 3).

*Alternative considered:* booting the image under QEMU on the runner. Rejected because QEMU is not Virtualization.framework, so a boot there is not the evidence that matters, and it is slower.

*Alternative considered:* `alpine-make-vm-image` creating a disk from scratch. Rejected for now because starting from the signed official cloud image keeps the EFI, cloud-init and partition layout that Macus hardware acceptance already covers. Its techniques may still be borrowed.

*Open points for implementation:*
- Confirm the Alpine cloud metal image's bootloader mechanism, and prove statically that the boot configuration selects the channel's installed kernel and initramfs, failing otherwise.
- Confirm `linux-lts` can be removed from the `stable` image. If a base package requires it, the build fails and this design is revisited rather than shipping two kernels.
- Measure the raw size needed for the installed set plus headroom.

### 2. One full lock per channel

`locks/lts.lock` and `locks/stable.lock` (paths indicative) each list every package in the final image as `name=version`, one per line, sorted with `LC_ALL=C`. That is the complete installed closure, including the base image's packages, not only the top-level list.

- The top-level list (`inputs/packages.txt` or similar) mirrors Macus's current install set with Incus pinned exactly. Channels differ only in the kernel and ZFS module packages (`linux-lts`/`zfs-lts` or `linux-stable`/`zfs-stable`) and what those pull in.
- A lock-refresh script resolves the current v3.24 `main`/`community` APKINDEX for a channel's top-level list and rewrites that lock. A human reviews the diff. It never runs automatically in CI.
- After installation the build compares the installed set with the lock and fails on any missing, extra or different package.
- The build fails if Alpine no longer serves a locked version. That is expected once Alpine moves on and does not affect published images.

### 3. Artifact format

- Keep Macus's existing archive format: a gzip tar with exactly one regular member `disk.raw`, so Macus acquisition and extraction keep working without a new decompressor.
- The compressed archive must stay under GitHub's 2 GiB release asset limit. Raw size is fixed per build and reported.
- Each build produces `<id>.raw.tar.gz`, `SHA512SUMS` (archive), `SHA256SUMS` (archive and raw disk), `<id>.manifest` (the installed manifest, Decision 5) and `<id>.build.json` (the build report).
- The build report records: appliance ID, channel, Alpine branch, upstream archive name and verified digests, signer fingerprint, lock path and SHA-256, manifest SHA-256, kernel release, ZFS module version, raw bytes, raw SHA-256, archive bytes, archive SHA-512, repository, commit and workflow run URL. It contains no secrets or runner paths.

### 4. Image contents and hygiene

**Baked in:** every locked package including kernel, initramfs and ZFS module; `/etc/macus-appliance-image`; `/etc/tama-zfs-qualified-kernel`; the HTTPS v3.24 `main`/`community` repositories file, so users can still run standard Alpine updates themselves.

**Not baked in:** Macus's guest helpers (`bootstrap.sh`, `storage.sh`, `bridge.py`, OpenRC scripts), the ZFS ARC cap and Incus preseed. Macus delivers those in its seed, so helper fixes ship with Macus without a new image and do not invalidate an image's qualification.

**Removed before packaging:** `/etc/machine-id` contents, SSH host keys, cloud-init instance state (equivalent to `cloud-init clean --logs`, so the Macus seed runs as a first boot), APK caches, `/var/log` contents, shell history, the temporary `resolv.conf`, and any workflow token or build path.

### 5. Image record contract (format 1)

`/etc/macus-appliance-image` is ASCII `key=value` lines, LF-terminated, in this order, with no other keys:

```
format=1
appliance_id=<id>
channel=<lts|stable>
alpine_branch=v3.24
lock_sha256=<hex>
manifest_sha256=<hex>
kernel_release=<release>
```

- **Installed manifest:** the output of `apk info -v` inside the image (lines like `linux-lts-6.18.62-r0`), sorted with `LC_ALL=C sort`, each line LF-terminated including the last. `manifest_sha256` is the SHA-256 of exactly those bytes. Macus recomputes it at first boot with the same rule, so the definition must not change within a format.
- **Kernel release:** the module directory name of the one installed kernel, which is what `uname -r` reports when it boots, for example `6.18.62-0-lts` or `7.1.5-0-stable`. `/etc/tama-zfs-qualified-kernel` contains the same value plus a trailing LF, which Macus already reads. The build checks that a ZFS module exists for that release.
- **Lock digest:** SHA-256 of the channel's lock file bytes.
- **Versioning:** any change to keys, ordering, the manifest rule or file locations is a new `format`, coordinated with a Macus change. Macus rejects formats it does not know.

`docs/image-contract.md` restates this contract for Macus implementers.

### 6. Appliance IDs, releases and retention

- An appliance ID names one exact image: `alpine-<alpine version>-<channel>-<n>`, for example `alpine-3.24.2-lts-1` and `alpine-3.24.2-stable-1`, where `<n>` increases per channel. The release tag equals the ID.
- The workflow refuses an ID that already has a release or tag, so IDs are never reused.
- Builds create **draft** releases only. Draft assets are not public; Macus qualification downloads them with authenticated `gh release download` and supplies them through its test-only path.
- A human publishes a release only after Macus has recorded hardware qualification of that exact draft (matching archive and raw digests). The release notes link that evidence. A failed qualification leaves the release unpublished, and it may be deleted only while still a draft.
- Published releases and assets are never deleted or replaced, because released Macus versions pin them. Any change to an image needs a new ID.

### 7. Workflow and supply chain

- `.github/workflows/appliance-image.yml` runs only on `workflow_dispatch`, with inputs for channel, appliance ID and the lock's commit, and a dry-run option that builds and checks without creating a release.
- Permissions are limited to `contents: write`, `id-token: write` and `attestations: write` on the job that creates the release; build steps run with read-only contents. Third-party actions are pinned by commit SHA. No secrets beyond the workflow's own token.
- `actions/attest-build-provenance` attests the archive.
- The regular `ci.yml` stays read-only and runs OpenSpec validation, script linting and unit tests with fixtures. It never builds or releases an image.

### 8. Channel cadence

`linux-stable` moves across kernel series during a branch's lifetime (for example 7.1 to 7.2), while `linux-lts` stays on one series. Each channel's lock, builds and releases are independent, so `stable` can move without touching `lts`. When Alpine moves on, the next build fails on the stale lock until a human refreshes it; published images keep working.

## Risks / Trade-offs

- **Boot configuration under chroot:** triggers may behave differently from a booted system. Mitigation: the build verifies the boot entry and initramfs statically, and Macus qualification boots the exact artifact before publication.
- **`stable` support level:** `linux-stable` and `zfs-stable` are in `community`, which Alpine supports for a shorter period than `main`, and its kernel series changes more often. `stable` is opt-in in Macus.
- **Slower security updates:** fresh appliances get kernel and Incus fixes only when a new image is qualified and adopted. That is the deliberate cost of a qualified kernel/ZFS pair.
- **Hosting:** fresh Macus installs depend on this repository's release assets. Retention rules and Macus's digest checks cover availability and integrity.
- **Contract drift:** the image record and manifest rule are shared with Macus. Format versioning and a written contract document keep the two in step.
- **Supply chain:** APK signatures, exact locks, a human-reviewed lock diff, SHA-pinned actions, minimal permissions and provenance attestations bound what a compromised dependency can change.
