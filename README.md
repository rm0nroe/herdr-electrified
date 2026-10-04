<p align="center">
  <img src="https://raw.githubusercontent.com/rm0nroe/herdr-electrified/main/assets/banner.svg" alt="herdr-electrified: reversible Herdr appearance, plus custom Herdr and Codex renderers" width="100%">
</p>

# Herdr Electrified

A reversible Electric look for Herdr, Codex, Claude Code and Ghostty. The
Electric bundle adds custom Herdr and Codex renderers: pane inset, tab
separators, a compact composer, pink markers, and readable code surfaces while
streaming. A settings-only option styles stock Herdr without the bundle.

| Option | What it changes |
| --- | --- |
| Electric bundle | Separate `herdr-electric` and `codex-electric` launchers with custom renderers and their own Herdr config. Stock `herdr` and `codex` are untouched |
| Settings only | Supported settings in your stock Herdr config |
| Herdr plugin | Preview, check and undo actions from Herdr's menu |

With Ghostty installed, both install options also add the Ghostty appearance
and font. When Claude Code is present, Electric also installs the Claude theme.

## Requirements

- macOS arm64. Linux, Windows and Intel Macs are unsupported.
- Git and [uv](https://docs.astral.sh/uv/). uv provides Python 3.12; no Rust,
  Zig or Xcode needed.
- Codex and Claude Code are optional and keep their own sign-in.

Electric is verified on macOS 26.6 and 26.5.2 (and with Claude Code only on
macOS 27.0). The settings preset and plugin are verified with stock Herdr 0.8.2
and 0.9.3.

## Install Electric

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv tool install --python 3.12 git+https://github.com/rm0nroe/herdr-electrified@v1.0.3
herdr-electrified install
```

Then restart Ghostty and run `herdr-electric` from a new window (not from
inside a Herdr pane; Herdr refuses to nest).

`install` downloads the Electric bundle 0.4.0 (patched Herdr 0.8.2, patched
Codex 0.160.0, JetBrainsMono Nerd Font Mono 3.5.1) into
`~/.local/share/herdr-electrified/bundles/`, refuses it unless its SHA-256
matches the checksum pinned in the CLI, lists the files it will write, and
asks once before writing (`--yes` skips the prompt, `--diff` shows full diffs,
`--dry-run` previews without writing). The launchers point at that
directory by absolute path, so don't move or delete it.

### Manual install

To verify the archive yourself:

```sh
mkdir -p ~/.local/share/herdr-electrified/bundles && cd ~/.local/share/herdr-electrified/bundles
curl -fLO https://github.com/rm0nroe/herdr-electrified/releases/download/v1.0.3/herdr-electrified-0.4.0-macos-arm64.tar.gz
curl -fLO https://github.com/rm0nroe/herdr-electrified/releases/download/v1.0.3/SHA256SUMS
shasum -a 256 -c SHA256SUMS
gh attestation verify herdr-electrified-0.4.0-macos-arm64.tar.gz --repo rm0nroe/herdr-electrified
tar -xzf herdr-electrified-0.4.0-macos-arm64.tar.gz
herdr-electrified preview-apply --electric ./herdr-electrified-0.4.0-macos-arm64
herdr-electrified apply --electric ./herdr-electrified-0.4.0-macos-arm64
```

The archive is built by this repository's public GitHub Actions workflow
([build-electric.yml](https://github.com/rm0nroe/herdr-electrified/blob/main/.github/workflows/build-electric.yml)),
and `gh attestation verify` checks its build provenance. The binaries are not notarized. A browser download marks the archive as
quarantined and `apply` refuses it. After the checksum passes, clear the mark
and apply again:

```sh
xattr -dr com.apple.quarantine ./herdr-electrified-0.4.0-macos-arm64
```

## What Electric changes

- **Herdr:** its own config at
  `${XDG_CONFIG_HOME:-~/.config}/herdr-electrified/electric/config.toml`. Your
  stock `~/.config/herdr/config.toml` is never written, so custom keybindings
  there do not carry over; copy them in if you want them. Herdr plugins are
  shared.
- **Codex** (when `codex` is on PATH, `CODEX_HOME` is set, or
  `~/.codex/auth.json` exists): `codex-electric`, a Codex theme, and a `codex`
  command that runs Electric Codex inside Electric panes. Your Codex settings,
  sign-in and history stay shared. Electric Codex joins Codex's shared
  background server only if you already have one installed; it never installs
  one itself. Its patch also fixes an upstream bug where a notice arriving
  mid-stream duplicated the answer on resize.
- **Claude Code** (when `claude` is on PATH, `CLAUDE_CONFIG_DIR` is set, or
  `~/.claude` exists): the Claude theme.
- **Ghostty** (when installed): an appearance include (Catppuccin Mocha, the
  Nerd Font, small padding) in the Ghostty config, plus the font in
  `~/Library/Fonts`. Keybindings and behavior are not touched. Reload Ghostty
  (cmd+shift+,) and restart it once after the font is first installed.

In zsh panes, `codex` runs Electric Codex even when your profile puts another
`codex` first on PATH. Bash and fish panes don't get this; use `codex-electric`
there. Shell profiles are never edited.

After upgrading herdr-electrified, run `herdr-electric server stop` so new panes
pick up the new launcher.

## Settings only

For stock Herdr on PATH, without the bundle:

```sh
uv tool install --python 3.12 git+https://github.com/rm0nroe/herdr-electrified@v1.0.3
herdr-electrified install --settings-only
```

This applies the Herdr preset to your Herdr config and, with Ghostty, the same
Ghostty appearance and font. Restart Ghostty once afterwards. To preview first:

```sh
herdr-electrified apply --dry-run --herdr-bin "$(command -v herdr)"
herdr-electrified apply --herdr-bin "$(command -v herdr)"
```

Add `--agent claude` to also set the Claude theme, or `--claude-statusline` for
the optional statusline (needs a Python 3.9+ `python3` on PATH).

## Check and undo

```sh
herdr-electrified check   # each owned file and its diff; empty diff and exit 0 mean no drift
herdr-electrified undo
```

To uninstall Electric, stop it first and remove the plugin if you installed it:

```sh
herdr-electric server stop
herdr-electric plugin uninstall herdr-electrified
herdr-electrified undo
```

Undo restores every file it changed. Files you edited since are kept and
reported as conflicts. Undo refuses while Herdr Electric is running, and leaves
the downloaded bundle and fonts in `~/.local/share/herdr-electrified` for you to
delete. If the bundle is already gone, pass any Herdr executable:
`herdr-electrified undo --herdr-bin /path/to/herdr`.

Details on targets, conflicts and recovery are in [reference](https://github.com/rm0nroe/herdr-electrified/blob/main/docs/reference.md).

## Herdr plugin

```sh
herdr-electric plugin install rm0nroe/herdr-electrified/plugin --ref v1.0.3 --yes
```

Install the appearance first; the plugin only previews, checks and undoes it.
Run its actions from Herdr's plugin menu. Output goes to
`herdr-electric plugin log list`, not the pane.

## Limitations

- Outside Ghostty, Electric needs a dark terminal background: Herdr paints its
  sidebar and tab bar but not pane backgrounds, so a light terminal shows
  white panes and makes Codex replies hard to read. Pane backgrounds are
  planned for v1.1.
- iTerm2 shows its own "Claude Code integration" banner over the top rows
  until you dismiss it; that banner is iTerm2's, not Electric's.
- The Electric binaries are built in public CI from the pinned sources and the
  patches in [`patches/`](https://github.com/rm0nroe/herdr-electrified/tree/main/patches), and attested. A bit-identical rebuild has
  not been established. Electric Codex shows "herdr-electrified build" in its
  header so it is not mistaken for OpenAI's release.
- The bundled Herdr reports the same `--version` as stock Herdr 0.8.2.
- OpenCode is out of scope. The Claude statusline has not been captured in a
  signed-in session yet.
- A single automatic Herdr tab can omit its title by design; a named tab shows
  it.

Build instructions are in [building](https://github.com/rm0nroe/herdr-electrified/blob/main/docs/building.md).

## Develop

```sh
uv sync --frozen
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen python tests/native_check.py /absolute/path/to/herdr
```

The tests use temporary configs. The native check only runs `config check`.

## License

Apache-2.0, see [LICENSE](https://github.com/rm0nroe/herdr-electrified/blob/main/LICENSE). Colors from
[Catppuccin](https://github.com/catppuccin/catppuccin) (MIT) and the Rye
wordmark (SIL OFL 1.1) are credited in [NOTICE](https://github.com/rm0nroe/herdr-electrified/blob/main/NOTICE). The Electric archive
includes third-party notices under `licenses/`. This project is independent of
the Herdr, Codex and Claude maintainers.
