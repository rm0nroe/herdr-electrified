#!/usr/bin/env python3
"""Herdr Electrified Claude Code statusline: directory, Git branch and context use.

Installed and removed by herdr-electrified. Reads Claude Code's statusline JSON on
stdin and prints one line. Standard library only, Python 3.9+, no network access.
"""
import json
import os
import re
import subprocess
import sys

# Directory teal and branch gold match the reference statusline; the rest is Catppuccin Mocha
# green, surface0, overlay2, overlay0.
SKY, YELLOW, GREEN, SURFACE, MUTED, SEPARATOR = '78;201;212', '215;186;125', '166;227;161', '49;50;68', '147;153;178', '108;112;134'


def compact(value):
    return f'{value / 1_000_000:g}M' if value >= 1_000_000 else f'{value / 1000:.0f}k'


def clean(value):
    return re.sub(r'[\x00-\x1f\x7f-\x9f]', '', str(value))


def color(value, rgb):
    return f'\033[38;2;{rgb}m{clean(value)}\033[0m'


def field(data, key):
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def number(value):
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def render(data):
    home = os.path.expanduser('~')
    cwd = str(field(data, 'workspace').get('current_dir') or (data.get('cwd') if isinstance(data, dict) else None) or os.getcwd())
    label = '~' + cwd[len(home):] if cwd == home or cwd.startswith(home + '/') else cwd

    def git(*args):
        try:
            return subprocess.run(['git', '--no-optional-locks', '-C', cwd, *args], stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=1, check=True).stdout.strip()
        except (OSError, ValueError, subprocess.SubprocessError):
            return ''
    segments = [color(label, SKY)]
    branch = git('branch', '--show-current')
    if branch:
        dirty = ' *' if git('status', '--porcelain') else ''
        ahead = git('rev-list', '--count', '@{upstream}..HEAD')
        segments.append(color(branch + dirty, YELLOW) + (color(' ↑' + ahead, GREEN) if ahead.isdigit() and int(ahead) else ''))
    context = field(data, 'context_window')
    percent = min(100.0, number(context.get('used_percentage')))
    usage = field(context, 'current_usage')
    used = sum(number(usage.get(key)) for key in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'))
    capacity = number(context.get('context_window_size'))
    filled = round(percent / 10)
    bar = color('─' * filled, GREEN) + color('─' * (10 - filled), SURFACE)
    count = f' {compact(used)}/{compact(capacity)}' if 0 < capacity < float('inf') and used < float('inf') else ''
    segments.append(color('ctx ', MUTED) + bar + color(f' {percent:.0f}%', GREEN) + color(count, MUTED))
    return color(' > ', SEPARATOR).join(segments)


if __name__ == '__main__':
    try:
        line = render(json.load(sys.stdin))
    except Exception:
        try:
            line = render({})
        except Exception:
            line = color('ctx', MUTED)
    sys.stdout.buffer.write((line + '\n').encode('utf-8', 'replace'))
