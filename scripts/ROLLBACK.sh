#!/bin/sh
# New-file rollback: the baseline had no runner, so remove an explicit copy.
# Offline review helper, not an Android root revocation or uninstall mechanism.
set -eu
[ "$#" -eq 1 ] || { printf 'usage: ROLLBACK.sh /absolute/path/to/runner-copy\n' >&2; exit 2; }
case "$1" in
    /*) ;;
    *) printf 'an absolute copy path is required\n' >&2; exit 2 ;;
esac
[ ! -L "$1" ] && [ -f "$1" ] || { printf 'a regular non-symlink copy is required\n' >&2; exit 2; }
rm -f -- "$1"
printf 'Restored absent-file baseline: %s\n' "$1"
