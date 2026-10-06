#!/bin/sh
# Runs a herdr-electrified command in the plugin popup: less scrolls output taller than the popup,
# and CLICOLOR_FORCE keeps the colors through the pipe.
run() { CLICOLOR_FORCE=1 ../.venv/bin/python -m herdr_electrified.cli "$@" 2>&1; }
page() { less -R -X -Ps"$1"; }

if [ "$1" != undo ]; then
  run "$@" | page 'q to close'
  exit
fi
# A menu click is easy to misfire, so undo previews and asks first; terminal undo does not.
preview=$(run undo --dry-run)
if [ $? -ne 0 ] || [ "$preview" = 'Nothing to undo.' ]; then
  printf '%s\n' "$preview" | page 'q to close'
  exit
fi
printf 'Undo will restore:\n%s\n' "$preview" | page 'q to continue'
printf 'Undo Herdr Electrified? [y/N] '
read -r answer
case $answer in
  [yY]*) run undo --yes | page 'q to close' ;;
  *) printf 'Nothing written.\n' | page 'q to close' ;;
esac
