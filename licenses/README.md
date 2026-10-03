# Bundled notices

components.json records the pinned Cargo metadata union for both renderers,
including build-only and platform-specific dependencies. This deliberately
includes more components than are linked into the macOS executable. Root and
nested license and notice files are preserved. Source archives for MPL-2.0
components are included under mpl-sources. For dual-license dependencies,
Apache-2.0 is selected where available; otherwise the permissive option is used.

Some upstream crates declare SPDX license identifiers but omit a separate
license file. Their package metadata, author attribution, source notice where
present, and standard declared-license terms are included explicitly. These
supplemental files are labeled rather than presented as upstream license files.

native contains upstream Herdr/Codex notices, vendored libghostty-vt notices,
V8 and native-submodule notices, zsh's license, and ripgrep notices. Provenance
URLs and pinned revisions are recorded in the JSON source inventories. Source
patches are provided in the repository's patches directory.
