# Tasks

## 1. Inputs and locks

- [x] 1.1 Add `appliance.toml` (schema 1, design.md Decision 2) with the upstream pins from design.md Context, the v3.24 repositories, the shared top-level package list copied from Macus's current `Integration/guest/bootstrap.sh` using package names, and the `lts` and `stable` channel tables; pin Python 3.12 in `mise.toml`; add the single `tomllib` loader every script and the workflow use; develop the loader against fixtures and verify it rejects each invalid case in Decision 2 (missing or unknown key, wrong type or schema, malformed digest or fingerprint, non-HTTPS URL, wrong repositories, channel/flavor mismatch, zero or too-small `raw_bytes`, lock path outside `locks/`); leave this task open until Linux arm64 sizing in task 2.2 is measured and the committed file passes the loader.
- [x] 1.2 Add a lock-refresh script that resolves the current v3.24 `main`/`community` aarch64 APKINDEX for a channel's `image.packages` plus its channel `packages`, applies its `remove` list and writes the full sorted `name=version` closure, including base-image packages, with no comments or blank lines; verify with fixture APKINDEX data that output is deterministic, that `lts` and `stable` locks differ only in kernel/ZFS-module packages and their dependencies, that the `stable` lock excludes `linux-lts`, and that it is never invoked by CI.
- [x] 1.3 Generate and commit `lts` and `stable` locks from the live repositories for human review, pinning the same exact Incus revisions in both locks and recording the refresh date and APKINDEX digests in the commit message.

## 2. Build

- [x] 2.1 Port upstream verification (pinned fingerprint in an isolated GnuPG home, archive SHA-512 and raw SHA-256 checked against `appliance.toml`, exactly one regular `disk.raw` member); verify with fixtures for a good archive, wrong digest, wrong signer and extra members.
- [x] 2.2 Write the build script taking a channel: extend `disk.raw` to `image.raw_bytes` and grow it, loop-mount root and boot/EFI partitions, configure only v3.24 `main`/`community`, remove the channel's unused `/boot/dtbs-<flavor>`, install exactly the lock with APK signatures enabled, remove the channel's `remove` packages, run triggers, and resolve the open points in design.md (bootloader mechanism, `linux-lts` removal, raw size, recording the measured size in `image.raw_bytes`); verify locally on Linux arm64 or through a dry-run workflow and record the findings in design.md.
- [x] 2.3 Write the image record and kernel record per design.md Decision 5, and the installed manifest with its exact digest rule; verify the record format and manifest digest against golden fixtures, including line endings and sort order.
- [x] 2.4 Clean the image (machine ID, host keys, cloud-init instance state, APK cache, logs, history, temporary `resolv.conf`, tokens and build paths) and zero free blocks; verify a hygiene check finds each item in a deliberately dirty fixture.

## 3. Static checks and packaging

- [x] 3.1 Add static checks that fail the build: installed set differs from the lock, more or fewer than one kernel flavor, no ZFS module for the kernel release, missing initramfs, boot configuration not selecting the channel's kernel, wrong image or kernel record, or any hygiene failure; verify each check fails on a deliberately broken fixture.
- [x] 3.2 Package `<id>.raw.tar.gz` as a single-member `disk.raw` gzip tar under 2 GiB, with `SHA512SUMS`, `SHA256SUMS`, `<id>.manifest` and `<id>.build.json` per design.md Decision 3; verify the archive passes the same single-regular-member extraction rules Macus applies and the report has every field and no runner paths.

## 4. Workflow

- [x] 4.1 Add `.github/workflows/appliance-image.yml`: `workflow_dispatch` only, inputs for channel (validated against `appliance.toml`), appliance ID, commit and dry run; `ubuntu-24.04-arm`; SHA-pinned actions; read-only validation and build jobs, a preflight job with contents write permission for read-only draft and tag queries, and write permissions for attestation and draft creation on the release job; refuse an ID that already has a release or tag before building; `actions/attest-build-provenance` on the archive; create a **draft** release tagged with the ID and never publish it or mark it latest; verify with actionlint and a dry run that creates no release.
- [x] 4.2 Extend `ci.yml` (read-only) to lint scripts and run the unit tests alongside OpenSpec validation; verify it passes on the feature branch.

## 5. Documentation

- [x] 5.1 Write `docs/image-contract.md` restating the image record format 1, manifest rule, kernel record, artifact names and build report fields for Macus implementers.
- [x] 5.2 Document lock refresh, building, draft releases, the hand-off to Macus qualification, publication, retention and the per-channel ID scheme in `docs/release.md`, and update `README.md`.

## 6. Verification and first drafts

- [x] 6.1 Run all checks and `mise exec -- openspec validate --all --strict --no-interactive`.
- [x] 6.2 With user authorization, dispatch the workflow for `lts` (`alpine-3.24.2-lts-1`) and record the run, digests and manifest. CI success is not hardware evidence.
- [x] 6.3 With user authorization, dispatch the workflow for `stable` (`alpine-3.24.2-stable-1`) and record the same. Neither build depends on the other.
- [x] 6.4 Hand the exact draft archives and verified digests to Macus for operator-initiated local qualification. Confirm both releases remain drafts.
- [x] 6.5 Record the existing passing local result for `alpine-3.24.2-lts-1` against its matching archive, raw disk and manifest digests. Link the [Macus hardware report](https://github.com/upmaru/macus/blob/e76eb18b6f4ca02cd366f4a233cad078d5f0e4f1/docs/testing/prebuilt-draft-qualification.json) and leave [Stable task 4.3](https://github.com/upmaru/macus/blob/e76eb18b6f4ca02cd366f4a233cad078d5f0e4f1/openspec/changes/prebuilt-appliance-image/tasks.md#4-verification-and-adoption) open for default nested KVM.
