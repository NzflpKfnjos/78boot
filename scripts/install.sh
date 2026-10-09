#!/system/bin/sh
# Usage: sh install.sh SOURCE_SCRIPT SOURCE_RUNNER
# Run from an already-authorized root context. Refuses to overwrite an install.
set -eu
PATH=/system/bin
export PATH
unset ENV BASH_ENV CDPATH LD_PRELOAD LD_LIBRARY_PATH
umask 077
TB=/system/bin/toybox
BASE=/data/adb/root-control
EXPECTED_SCRIPT=e9cb5f84428910e83c0c43957fa8deb01ce4fc3d7b899aaecea7810993c62200
EXPECTED_RUNNER=6f9ae6a53f2a518c0bd44137c3366adb399b5db0b1b0f1c790a0bb1dd02b29e5
fail() { printf 'install: %s\n' "$1" >&2; exit 1; }
[ "$("$TB" id -u)" = 0 ] || fail 'root caller required'
[ "$#" = 2 ] || fail 'usage: sh install.sh SOURCE_SCRIPT SOURCE_RUNNER'
[ ! -L /data/adb ] && [ -d /data/adb ] || fail '/data/adb is unavailable'
[ "$("$TB" stat -c '%u:%g:%a' /data/adb)" = '0:0:700' ] || fail '/data/adb must be root:root mode 700'
[ ! -e "$BASE" ] && [ ! -L "$BASE" ] || fail 'installation already exists'
stage=$("$TB" mktemp -d /data/adb/.root-control-install.XXXXXXXX)
trap '"$TB" rm -rf "$stage"' EXIT
"$TB" mkdir "$stage/bin" "$stage/scripts"
"$TB" cp "$1" "$stage/scripts/1_no_login.sh"
"$TB" cp "$2" "$stage/bin/root-control"
script_hash=$("$TB" sha256sum "$stage/scripts/1_no_login.sh")
runner_hash=$("$TB" sha256sum "$stage/bin/root-control")
[ "${script_hash%% *}" = "$EXPECTED_SCRIPT" ] || fail 'source script hash mismatch'
[ "${runner_hash%% *}" = "$EXPECTED_RUNNER" ] || fail 'source runner hash mismatch'
"$TB" chown -R 0:0 "$stage"
"$TB" chmod 700 "$stage" "$stage/bin" "$stage/scripts"
"$TB" chmod 500 "$stage/bin/root-control"
"$TB" chmod 400 "$stage/scripts/1_no_login.sh"
# A root-only installation lock prevents concurrent cooperative installers.
lock=/data/adb/.root-control-install.lock
"$TB" mkdir "$lock" || fail 'another installation is active'
trap '"$TB" rm -rf "$stage"; "$TB" rmdir "$lock"' EXIT
[ ! -e "$BASE" ] && [ ! -L "$BASE" ] || fail 'installation already exists'
"$TB" mv "$stage" "$BASE"
printf 'Installed: %s/bin/root-control\n' "$BASE"
