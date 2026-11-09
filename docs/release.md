# Appliance release process

## Refresh the locks

Use a Linux arm64 host with root access, Python 3.12, GnuPG, curl, and the Linux disk tools used by the build workflow. Start on a feature branch from `develop`.

```sh
sudo unshare --mount --propagation private -- python3 -m scripts.refresh_locks \
  --incus-version 7.0.1-r2
git diff -- locks/
```

The refresh verifies the upstream archive, reads the base package set, and resolves both channels against the same signed v3.24 `main` and `community` APKINDEX snapshots. Pass `--channel lts` or `--channel stable` to refresh one channel. Refresh both channels together to change the exact Incus pins. Review the full diff before building, and record the refresh date and both printed APKINDEX SHA-256 digests in the lock commit. Refresh is manual, and CI runs only fixture tests and validation.

If Alpine removes a locked version, the next build fails. A human can review a fresh lock and build a new appliance ID.

## Build on GitHub Actions

Run **Appliance image** manually with a channel from `appliance.toml`, a full reviewed source commit SHA, and an unused ID. Use `alpine-<upstream version>-<channel>-<positive integer>`, such as `alpine-3.24.2-lts-1` or `alpine-3.24.2-stable-1`. Increase the suffix separately for each channel. GitHub requires the workflow to exist on the default branch before it accepts a dispatch.

Keep **dry run** enabled to build and check the image and retain its assets in the workflow run. Disable it only with explicit authorization to create a draft release. Preflight checks releases and tags for the ID before allocating the build runner. The release job rechecks the ID, attests the compressed archive, reserves the tag at the source commit, and attaches the assets to a draft. If draft creation or upload fails after the tag has been reserved, use a new ID.

The Linux arm64 runner mounts and modifies a verified raw disk without booting it. Static checks cover the exact installed package set, Limine selection, the kernel, initramfs and ZFS module, the image records, and hygiene. See [the image contract](image-contract.md) for the assets and digest rules.

## Qualify and publish

Record the draft's run URL, archive and raw digests, and installed manifest for the Macus handoff. Use authenticated `gh release download <id> --repo upmaru/macus-appliance` to acquire the private draft assets. Macus tests the exact draft image through its opt-in hardware acceptance on isolated state and records boot, storage, ZFS and relay evidence.

A human may publish the draft once Macus has recorded qualifying evidence for the matching digests. Link that evidence in the release notes. If qualification fails, leave the release in draft and use a new ID for another attempt. A human may delete a failed draft while it is still a draft. Keep its tag so the ID remains reserved.

Keep published assets available and unchanged. A Macus release can pin them for as long as that version is in use.
