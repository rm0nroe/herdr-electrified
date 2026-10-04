# Scope and acceptance

The public release gate includes all five Electric MVP requirements. The public
URL checks in the last row run after publication.

The Codex patch also fixes an upstream streaming bug in
`app/agent_message_consolidation.rs`. A notice or warning arriving mid-stream
no longer causes the answer to duplicate on resize. Interleaved notices appear
after the completed answer. This fix applies with Electric on or off and is
the patch's one non-presentation Codex change.

| Capability | Current evidence |
| --- | --- |
| Installer preview/apply/check/undo, recovery, conflicts, launcher modes, symlinks | 97 automated checks passed against the current source, including 7 bundle privacy-gate cases |
| Native Herdr config validation and optional Claude rollback | 2 native checks passed against the rebuilt Herdr 0.8.2 |
| Rebuild Herdr with pinned source | Passed with full Xcode macOS 26.2 SDK and Zig 0.15.2 |
| Bundle local-path scan | Assembly gate: zero developer-home, other-user or temp paths in shipped files; only OpenAI CI paths (`/Users/runner`, CI `/var/folders/` in voice libraries) inside files byte-identical to the pinned upstream package. gitleaks: no findings |
| Codex streaming background and secondary-menu rendering changes | Implemented; native baseline reproduces bug; 18 targeted tests pass in Electric and standard modes; clippy clean |
| Full Electric bundle, launcher/helper runtime, rollback | Release bundle assembled and checksum-verified; preview/apply/check/undo against it in a temporary HOME left the stock config hash unchanged, removed the separate Electric config and all launchers, and left only receipt state; stock Herdr `config check` ok; 3 native checks passed with the bundled Herdr. Apply installs only the detected agents' pieces, verified on Claude-only and Codex-only HOMEs |
| Native plugin install and first dependency fetch | Passed from a staged Git repository through native `plugin install`; isolated uv cache |
| Native plugin preview/check/undo dispatch | All three native action logs report succeeded, exit 0 |
| Signed-in Claude Markdown, markers, code/diff and prompt | Real authenticated response captured in a temporary native terminal fixture |
| Signed-in Claude narrow/wide resize | Captured; completed response confirmed after restoring width |
| Signed-in Codex | Bundled Codex 0.159.3 answered on the default `gpt-6.1-sol` with a real signed-in `~/.codex`. Bundle 0.3.0 (Codex 0.160.0) on the macOS 26.5.2 Mac mini after a clean `herdr-electrified install`: `codex-electric exec` answered with the existing sign-in, and in a `herdr-electric` pane Codex showed `(v0.160.0, herdr-electrified build)` and answered beside a signed-in Claude Code pane. CI-built bundle 0.4.0 on the same mini: no startup warning, `herdr-electric` current in `/theme`, no daemon package created without one installed, and attached to an installed daemon in a scratch `CODEX_HOME` |
| Bundle 0.3.0 (Codex 0.160.0) | Built from the pinned sources; assembly privacy gate passed; archive sha256 `1ef0c3f12d9ff8a25d5b977d16c12ab53380ef08dd09f80c1d03e9d2367a61fb`; patch digest `44e6441d…` recorded in the manifest; Codex TUI suite 5626 passed, 8 skipped; `codex-cli 0.160.0` with the "herdr-electrified build" label; temp-HOME apply/check/undo with the real bundle verified checksums, reported nothing missing, and left only receipt state |
| Fresh second-machine install | CLI dependency fetch and settings lifecycle passed on a macOS 26.5.2 arm64 Mac mini. Electric on macOS 27.0 arm64, Claude Code only: detected agents `["claude"]`, no Codex pieces written, Claude theme rendered in an Electric pane, stock config hash unchanged, undo refused while running and restored `settings.json` byte-for-byte. Electric with Codex and stock Ghostty 1.3.1 on the Mac mini: matched the reference by eye, and plain `codex` ran Electric Codex despite a shell profile that puts stock `codex` first on PATH |
| Stock Herdr 0.9.3 (upstream latest, 2026-10-01) | Native config checks 3/3; settings-only apply/check/undo with the config path from 0.9.3 help; native plugin install from a staged Git copy with first-run dependency fetch, then preview, check and undo actions succeeded with exit 0 |
| Browser-quarantined Electric archive | macOS kills the unnotarized binaries (exit 137); apply refuses with the `xattr -dr com.apple.quarantine` fix; binaries run after it |
| Electric terminal layer | Fonts byte-identical to Nerd Fonts 3.5.1; temp-HOME apply made `ghostty +show-config` report background `#11111b`, JetBrainsMono Nerd Font Mono 12.5, zero padding and the reference cursor; undo left only receipt state |
| One-command install | `herdr-electrified install` and `install --settings-only` tested against served archives: checksum mismatch refused with nothing left behind, re-run reuses the extraction, settings-only adds the Ghostty look and undo removes it; real Nerd Fonts 3.5.1 download verified against the pinned checksum |
| Public bundle/CLI/plugin URL checks | Pending publication |

Local validation runs on macOS 26.6 arm64. The second machines are macOS 26.5.2
arm64 Mac mini (CLI, Electric with Codex) and macOS 27.0 arm64 (Electric with Claude Code). Other macOS versions, Linux, Windows, and Intel Macs are unverified for
Electric and are not advertised as supported.

Native terminal recordings preserve text and ANSI cell colors. Any HTML/PNG
renderings of those recordings are labeled as terminal captures, not desktop or
Ghostty screenshots. Deterministic local streaming fixtures are distinguished
from real authenticated model responses in their receipts.

Test isolation: temporary HOME/CODEX_HOME and named native sessions for installer
and mock-render tests; existing local Claude sign-in for the authenticated check,
without copying credentials. Exact temporary Codex threads must be archived after
fixtures exit. No production session is restarted by installer or acceptance work.
