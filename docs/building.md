# Building Electric

The initial target is macOS arm64. Builders need Python 3.12+, git, full Xcode,
Rust 1.96.1 (Herdr), Rust 1.95.0 (Codex), and Zig 0.15.2. End users do not need
these build tools. Set `ZIG` to the Zig executable when it is not on PATH.

```sh
python3.12 scripts/build-electric.py /path/to/new-build-directory
```

Release archives come from `.github/workflows/build-electric.yml`, which runs this
script on a GitHub-hosted `macos-26` runner with Xcode 26.2 and attests the archive's
build provenance. Check a download with
`gh attestation verify <archive> --repo rm0nroe/herdr-electrified`.

The script fetches pinned public source revisions, applies the checked-in patches,
builds both native executables (Codex with 16 codegen units at opt-level 2 so
the final thin-LTO link fits in memory), and verifies the official Codex helper archive
before assembly. Builds run sequentially with one Cargo worker by default to
limit memory pressure. It removes local home-directory prefixes from Rust/C compilation,
strips custom executables, and applies ad-hoc signatures. These binaries are not
Apple-notarized. The tar archive has normalized ownership and timestamps; a
bit-identical compiler output across SDK versions has not been established.

Before archiving, assembly scans every shipped file for the builder's home path,
any `/Users/<name>` path and macOS temp paths, and stops on a hit. The one
exception is OpenAI's CI paths (`/Users/runner`, and the CI temp dir under
`/var/folders/` in the voice libraries) inside files that are byte-identical to
the pinned upstream package. Unshipped build outputs are not
scanned: their debug map holds object paths that stripping removes.

The tested SDK is full Xcode's macOS 26.2 SDK. The Command Line Tools macOS 27 SDK
failed to link Zig 0.15.2's build runner with unresolved Darwin C symbols.
`DEVELOPER_DIR` is scoped to the build process; no global SDK selection is changed.

| Component | Pinned source |
| --- | --- |
| Herdr 0.8.2 | `herdrdev/herdr@9eb521456ac0d19d3ab3d9d7cea3cca10baa8a4c` |
| Codex 0.160.0 | `openai/codex@a956835d020762cb2b570053af06f643a11c0ecc` |
| Codex helpers | official `rust-v0.160.0` macOS arm64 package |
| Font | Nerd Fonts `v3.5.1` `JetBrainsMono.tar.xz` (Mono Regular, Bold, Italic, BoldItalic) |
| Zig | 0.15.2 |

## Releasing a new bundle

1. Bump `BUNDLE_VERSION` in `scripts/build-electric.py` and commit the patches.
2. Run the `build-electric` workflow on GitHub (`workflow_dispatch`, about 2.5 hours)
   and download its `electric-bundle` artifact.
3. In `src/herdr_electrified/electric.py`, set `BUNDLE` to the new name, append the
   version to `VERSIONS`, and set `BUNDLE_SHA` to the archive's SHA-256 from `SHA256SUMS`.
4. Bump the package version, then attach the archive and `SHA256SUMS` to that
   package's GitHub release: `BUNDLE_URL` points at `v{package version}`.
5. Update the bundle name in the README.

The manifest records upstream revisions, patch digests, helper-package and font-archive digests,
and each bundle file's checksum. The release includes the original upstream
notices, native dependency notices, and MPL component sources. See
`licenses/components.json` and `licenses/native/*sources.json` for provenance.

Run the installer suite with `PYTHONPATH=src python -m unittest discover -s tests -v`.
`tests/native_check.py` accepts the built Herdr binary for config validation.
Native session, rendering, clean-machine, and public-install checks are manual
and not part of this suite.
