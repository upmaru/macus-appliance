# Tasks

## 1. Inputs and locks

- [ ] 1.1 Add versioned build inputs: upstream archive name and URL, archive SHA-512 and size, raw SHA-256 and size, signing key URL and pinned fingerprint (values in design.md Context), the top-level package list copied from Macus's current `Integration/guest/bootstrap.sh` with Incus pinned exactly, the per-channel kernel/ZFS packages, and the target raw size; verify a parser test reads them and rejects missing or malformed fields.
- [ ] 1.2 Add a lock-refresh script that resolves the current v3.24 `main`/`community` aarch64 APKINDEX for a channel's top-level list and writes the full sorted `name=version` closure, including base-image packages; verify with fixture APKINDEX data that output is deterministic, that `lts` and `stable` locks differ only in kernel/ZFS-module packages and their dependencies, that the `stable` lock excludes `linux-lts`, and that it is never invoked by CI.
- [ ] 1.3 Generate and commit `lts` and `stable` locks from the live repositories for human review, recording the date and APKINDEX they came from.

## 2. Build

- [ ] 2.1 Port upstream verification (pinned fingerprint in an isolated GnuPG home, archive SHA-512 and raw SHA-256 checked against the recorded inputs, exactly one regular `disk.raw` member); verify with fixtures for a good archive, wrong digest, wrong signer and extra members.
- [ ] 2.2 Write the build script taking a channel: extend and grow `disk.raw`, loop-mount root and boot/EFI partitions, configure only v3.24 `main`/`community`, remove the channel's unused `/boot/dtbs-<flavor>`, install exactly the lock with APK signatures enabled, remove `linux-lts` for `stable`, run triggers, and resolve the open points in design.md (bootloader mechanism, `linux-lts` removal, raw size); verify locally on Linux arm64 or through a dry-run workflow and record the findings in design.md.
- [ ] 2.3 Write the image record and kernel record per design.md Decision 5, and the installed manifest with its exact digest rule; verify the record format and manifest digest against golden fixtures, including line endings and sort order.
- [ ] 2.4 Clean the image (machine ID, host keys, cloud-init instance state, APK cache, logs, history, temporary `resolv.conf`, tokens and build paths) and zero free blocks; verify a hygiene check finds each item in a deliberately dirty fixture.

## 3. Static checks and packaging

- [ ] 3.1 Add static checks that fail the build: installed set differs from the lock, more or fewer than one kernel flavor, no ZFS module for the kernel release, missing initramfs, boot configuration not selecting the channel's kernel, wrong image or kernel record, or any hygiene failure; verify each check fails on a deliberately broken fixture.
- [ ] 3.2 Package `<id>.raw.tar.gz` as a single-member `disk.raw` gzip tar under 2 GiB, with `SHA512SUMS`, `SHA256SUMS`, `<id>.manifest` and `<id>.build.json` per design.md Decision 3; verify the archive passes the same single-regular-member extraction rules Macus applies and the report has every field and no runner paths.

## 4. Workflow

- [ ] 4.1 Add `.github/workflows/appliance-image.yml`: `workflow_dispatch` only, inputs for channel, appliance ID, lock commit and dry run; `ubuntu-24.04-arm`; SHA-pinned actions; read-only build steps and write permissions only on the release job; refuse an ID that already has a release or tag; `actions/attest-build-provenance` on the archive; create a **draft** release tagged with the ID and never publish it or mark it latest; verify with actionlint and a dry run that creates no release.
- [ ] 4.2 Extend `ci.yml` (read-only) to lint scripts and run the unit tests alongside OpenSpec validation; verify it passes on the feature branch.

## 5. Documentation

- [ ] 5.1 Write `docs/image-contract.md` restating the image record format 1, manifest rule, kernel record, artifact names and build report fields for Macus implementers.
- [ ] 5.2 Document lock refresh, building, draft releases, the hand-off to Macus qualification, publication, retention and the per-channel ID scheme in `docs/release.md`, and update `README.md`.

## 6. Verification and first images

- [ ] 6.1 Run all checks and `mise exec -- openspec validate --all --strict --no-interactive`.
- [ ] 6.2 With user authorization, dispatch the workflow for `lts` (`alpine-3.24.2-lts-1`) and record the run, digests and manifest. CI success is not hardware evidence.
- [ ] 6.3 With user authorization, dispatch the workflow for `stable` (`alpine-3.24.2-stable-1`) and record the same. Neither build depends on the other.
- [ ] 6.4 Hand the draft releases to Macus for hardware qualification. Do not publish a release, create other tags or merge without user authorization; publication follows Macus's recorded evidence.
