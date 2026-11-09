# Project instructions

macus-appliance builds prebuilt Macus appliance images: the signature-verified Alpine Linux ARM64 cloud image with an exact, locked package set (kernel, ZFS, Incus, QEMU and the rest of the Macus guest packages) installed into its root filesystem without booting it. [Macus](https://github.com/upmaru/macus) consumes the images. It pins each one by digest in its shipped catalog, delivers the guest helpers through its NoCloud seed, verifies the image at first boot and owns hardware qualification. Keep that boundary: never add Macus host code, guest helper scripts, the Macus catalog, or a fallback to live package installation here.

Use OpenSpec for substantial behavior changes and validate planning before implementation. The image record format in `/etc/macus-appliance-image` and the installed-manifest digest are a versioned contract with Macus; change them only through a new format version and a coordinated Macus change.

Package sources are only Alpine `main` and `community` on the image's stable branch, with APK signature verification enabled. Never use edge, testing or third-party repositories, never substitute a locked version, and never refresh a lock automatically in CI; a human reviews every lock diff. Each kernel channel (`lts`, `stable`) has its own lock and image, and an image carries exactly one kernel flavor.

CI success is static evidence only. Never report it as boot, ZFS or hardware evidence; that evidence is recorded by Macus's opt-in hardware acceptance on isolated state. Images must not contain a machine ID, SSH host keys, cloud-init instance state, package caches, logs, credentials or build host paths.

Workflows run image builds only on `workflow_dispatch`, with minimal permissions and third-party actions pinned by commit SHA. Builds create **draft** releases only. Agents and workflows must never publish a release, mark one latest, delete or replace a published asset, or reuse an appliance ID; a changed image always gets a new ID. Dispatching a build that creates a draft release needs user authorization.

Development tools are pinned in `mise.toml`. From the repository root, run `mise trust` and `mise install` once. Use `mise exec -- openspec ...` in agents or shells without activation; `mise run spec:validate` runs canonical strict validation. Never commit `.integration/`, build output, images or secrets.

Use Git Flow: `main` is released history, `develop` is integration, `feature/*` branches start from and merge into `develop`, `release/*` branches start from `develop` and finish into both `main` and `develop`, and `hotfix/*` branches start from `main` and finish into both long-lived branches. Publish development work on its feature branch. Do not finish/merge branches, create tags or publish releases without user authorization.

Every pull request opened by a bot or automated agent must be created as a draft (for example, `gh pr create --draft`) and remain a draft until a human marks it ready for review. Bots and agents must never mark any pull request ready for review.
