# Reference

Detailed behavior of `herdr-electrified` apply, check and undo. For installation, see the [README](../README.md).

## Targets and confirmation

Use `uv run --frozen herdr-electrified ...` in this development checkout. Applying
requires one interactive confirmation, or `--yes` for scripted runs. An unpinned
scripted target requires `--herdr-bin` when a write is needed. An already-styled,
unpinned target is a no-op without executable selection. Conflicting managed keys require an
interactive review of the new diff. `--json` returns structured output.
The confirmation lists the files it will write, grouped by component; `--diff`
shows full diffs instead, and conflicting targets always show theirs. With no
answer in 5 minutes, or on Ctrl-C before writing, nothing is written.

For settings-only installs, the target is `--herdr-config`, then
`HERDR_CONFIG_PATH`, then the installed Herdr help output's default. Electric
uses `--herdr-config`, its recorded target, or
`herdr-electrified/electric/config.toml` under `XDG_CONFIG_HOME` (default `~/.config`),
in that order. A canonical target has one ownership receipt;
`undo` visits every recorded target, regardless of the current target flags.
Unrelated keys and comments survive; arrays are owned whole. Edited managed
values are preserved on undo and reported as conflicts.

## Undo and recovery

Undo restores the full original file only
when exact restore remains eligible and the current hash matches the installed
hash. This preserves original formatting and line endings without overwriting
newer edits. Otherwise undo restores matching owned keys individually; a deleted
target stays absent, with its receipt retained and owned keys reported as conflicts.

The private receipt and lock live below
`${XDG_STATE_HOME:-~/.local/state}/herdr-electrified/`. Original fragments, file hashes
and a pending journal support recovery after interrupted per-file writes. There
is no transaction spanning files. A foreign edit during recovery stops writes
and preserves the journal. A changed executable during recovery also stops:
retain the receipt for inspection, do not delete it to bypass the conflict.

Undo restores owned settings, theme files, and launchers, including previous
launcher modes. Edited files are preserved and reported as conflicts. Once
every owned file is restored, undo also removes the files Herdr Electric writes
at runtime (`release-notes.json` beside the Electric config and the
`herdr-electrified` session directory) and any directory apply created, if
it is empty. A config file apply created is removed even after Herdr records
its `onboarding = false` flag in it; any other setting you added keeps it. Undo refuses while Herdr Electric is running. Undo leaves
the extracted bundle and downloaded fonts in place; remove
`~/.local/share/herdr-electrified` manually once you no longer need them. Each write is journaled, and an interrupted operation can be retried.

## Check, dry runs and reloads

`check` and `apply --dry-run` never create a receipt, lock or state directory,
change a target, or prompt. Native validation uses disposable private temporary
files. Without an explicitly selected executable or matching pin, validation is
reported as not run; that is not a pass. A pin identifies the selected executable,
not the running server image. A busy read-only report may be stale.

Only a complete, matching inherited Herdr association permits an automatic
reload request after a write. Output distinguishes requested, failed and skipped
reloads from loaded state, which remains unknown until visual verification.
Failed reload does not undo saved settings; conflict-preserving undo is available.

Check the target path and diff before confirming. To select a different file,
add `--herdr-config /path/to/config.toml` to apply and check. `undo` visits all
owned targets in the receipt, not just the current configuration. A matching pane is a
Herdr pane whose `HERDR_CONFIG_PATH` is the target being applied. Outside a
matching pane, apply saves the settings without requesting a reload.
Inside a matching pane, the CLI reports a reload request with loaded state
unknown. Check the appearance visually to confirm the running pane repainted.

## Settings preset

The settings-only preset includes the supported palette, sidebar widths, two-line
cards, scrollbars and agent labels. It contains no quota rows, onboarding settings,
hidden-control options or custom renderer keys. Electric additionally enables
the custom renderer settings carried by its patched Herdr binary, including
`[theme.terminal]`: the pane background (`#11111b`), text (`#cdd6f4`) and 16 basic
colors (Ghostty's Catppuccin Mocha) that Herdr Electric paints itself and reports
to programs that ask (OSC 10, 11 and 4), with a dark color scheme. While a client
is attached it also sets the host terminal's background and cursor color (OSC 11
and 12) and resets both (OSC 111 and 112) on detach or exit. Herdr reads it
at startup, so restart Herdr Electric after changing it. Existing per-agent row overrides
are preserved and reported as partial coverage.

## Claude theme

Electric apply installs `themes/herdr-electrified.json` under `CLAUDE_CONFIG_DIR`
or `~/.claude` when Claude Code is detected, plus
`~/.local/share/herdr-electrified/commands/claude`, which Electric panes find
first on PATH. It runs the next `claude` on PATH with
`--settings '{"theme":"custom:herdr-electrified"}'`; Claude Code ranks command-line
settings above your own, so only Claude in an Electric pane changes and
`settings.json` is not written for the theme. Passing your own `--settings`
replaces Electric's for that run. `--agent claude` is accepted but does nothing.

Before v1.0.6 the theme was set globally through `theme` in `settings.json`. The
next apply releases that key: it is restored if it still holds
`custom:herdr-electrified`, and left alone if you changed it. A settings-only
install also removes the theme file it owned.

Settings writes (now only `statusLine`, below) keep hooks, permissions and other
settings at their values. Settings must be a strict
JSON object with unique keys and finite numbers. Settings previews show only the
managed keys (`theme`, `statusLine`) and disclose that writes can change whole-file formatting, Unicode
escapes and key order while preserving unrelated values. This applies to both
apply and key-level undo; byte-for-byte formatting preservation is not promised.
The namespaced theme file is owned
as a whole; later file edits or deletion are preserved as undo conflicts.

`check` includes previously owned Claude targets. `undo` covers every recorded component
and directory, even when `CLAUDE_CONFIG_DIR` changes. It restores the selection
before removing the theme file. Journal recovery is per file, so an interruption
can leave a partial apply/undo that must be retried. Claude uses JSON validation,
not the Herdr executable pin. No Claude process is launched or reloaded.
Signed-in Markdown, marker and prompt rendering and narrow/wide resizing were
captured from the native Claude terminal in an isolated working directory. The
recordings are ANSI captures, not desktop screenshots.

## Claude statusline

`--claude-statusline` (with `install`, `apply`, `preview-apply` or `check`) installs
`${XDG_DATA_HOME:-~/.local/share}/herdr-electrified/claude-statusline.py` and sets
only `statusLine` in the same `settings.json`, with or without Electric.
The script is standard-library Python 3.9+, run as `python3`, with no network access.
Claude Code runs it with the `python3` on your `PATH`, not uv's interpreter, so apply
refuses and `check` reports a `python3` conflict when no Python 3.9+ `python3` is found.
It reads Claude Code's statusline JSON and prints the directory, Git branch with a
dirty marker and ahead count, and a context-window bar with percentage and token
counts, in the Catppuccin Mocha palette. An existing `statusLine` is never chained or
silently replaced: it is reported as a conflict, `--yes` refuses, and only an
interactive review of the diff replaces it. Undo restores the original value and
removes the script; a later edit to either is preserved and reported as a conflict.
Once owned, the statusline stays managed by later apply and `check` runs until undo.

## Electric launcher environment

`~/.local/bin/codex-electric` also runs directly. Start `herdr-electric` from a
new terminal window, not from inside any Herdr pane (stock or Electric): Herdr
refuses to nest inside a pane. The launchers leave stock `herdr` and `codex`
commands intact. Herdr Electric uses the named
`herdr-electrified` session and clears inherited socket overrides, so opening
it does not attach to or hand off a stock Herdr server. It also clears Claude
Code's `CLAUDE_CODE_CHILD_SESSION` marker, so Claude Code in an Electric pane
keeps saving transcripts even when Electric was started from a Claude session. Its child PATH puts Electric's
`codex` and `claude` commands first, so `codex` in an Electric pane runs Electric Codex
and `claude` runs with the Electric theme. In zsh panes
this holds even when your profile prepends its own directories (for example
`~/.local/bin` or nvm with an npm-installed `codex`): the launcher points
`ZDOTDIR` at `~/.local/share/herdr-electrified/zsh`, whose `.zshenv` restores your
`ZDOTDIR`, runs your usual startup files, and puts those commands back first
before the first prompt. Bash and fish panes do not get this. There, if
`command -v codex` does not point at `~/.local/share/herdr-electrified/commands/codex`,
use `codex-electric` directly; for Claude, run
`~/.local/share/herdr-electrified/commands/claude`. Shell profiles are never edited.
After upgrading herdr-electrified, stop a running Electric server
(`herdr-electric server stop`) so new panes pick up the launcher's environment.

Codex uses the `CODEX_HOME` selected at installation, or your existing `~/.codex`.
Settings, authentication, and session history remain shared. The launcher selects
`herdr-electric` with a command-line theme override; global Codex configuration
is not rewritten. Stop and restart your own Electric session when changing the
bundle. The installer does not restart running sessions or request reloads from
stock sessions.

OpenCode reads custom themes only from its own config directory, so Electric adds
`herdr-electrified.json` to `${XDG_CONFIG_HOME:-~/.config}/opencode/themes`, where it
only appears in OpenCode's theme list. The `herdr-electric` launcher sets
`OPENCODE_TUI_CONFIG` and `OPENCODE_CONFIG` to two files beside the Electric config
that select that theme and the Build accent; OpenCode layers them over your global
settings, so only OpenCode in an Electric pane changes. A project's own `tui.json`
still wins over the theme selection. Undo removes all three files.

## Removing the plugin after undo

Undo removes the `herdr-electric` launcher. If you already ran undo, remove the
plugin with the bundled Herdr, which reads the same plugin list:

```sh
~/.local/share/herdr-electrified/bundles/herdr-electrified-0.5.0-macos-arm64/herdr plugin uninstall herdr-electrified
```
