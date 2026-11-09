# macus-appliance

Prebuilt appliance images for [Macus](https://github.com/upmaru/macus).

Fresh Macus appliances used to install their kernel, ZFS and Incus packages from Alpine's repositories on first boot. Alpine keeps only the newest revision of each package on a stable branch, so every kernel update broke fresh starts until Macus requalified. This repository instead builds a locked image ahead of time, so a qualified kernel/ZFS pair stays available after Alpine moves on.

## How images reach users

1. **Lock:** a human refreshes and reviews a channel's package lock.
2. **Build:** a manually dispatched workflow verifies the upstream Alpine cloud image, installs the locked packages into it without booting it, runs static checks and attaches the image to a **draft** release.
3. **Qualify:** Macus boots the draft image on real hardware through its opt-in acceptance tooling and records boot, storage, ZFS and relay evidence.
4. **Publish:** a human publishes the release after that evidence exists.
5. **Adopt:** a Macus change pins the published image by digest in its catalog and ships in a Macus release.

Published images are never deleted or replaced. A changed image always gets a new appliance ID.

## Kernel channels

| Channel | Kernel and ZFS module | Alpine repository |
|---|---|---|
| `lts` (default in Macus) | `linux-lts` / `zfs-lts` | v3.24 `main` |
| `stable` (opt-in) | `linux-stable` / `zfs-stable` | v3.24 `community` |

Both channels share the same ZFS userland, Incus and QEMU packages from the same branch. Alpine edge is not used.

## Status

Planning. See [`openspec/changes`](openspec/changes) for the image build change.

## Development

```sh
mise trust
mise install
mise run spec:validate
```

See [AGENTS.md](AGENTS.md) for project rules.

## License

MIT
