#!/system/bin/sh
# Called once per boot by the root-owned init service after boot_completed=1.
set -eu
PATH=/system/bin
export PATH
unset ENV BASH_ENV CDPATH LD_PRELOAD LD_LIBRARY_PATH
umask 077
BASE=/data/adb/root-control
TB=/system/bin/toybox
[ "$("$TB" id -u)" = 0 ] || exit 1
[ "$(getprop sys.boot_completed)" = 1 ] || exit 1
if [ -e /data/local/tmp/root-control.disable ]; then
    log -p i -t RootControl 'autostart disabled by owner marker'
    exit 0
fi
boot_id=$("$TB" cat /proc/sys/kernel/random/boot_id)
attempt=0
while [ "$("$TB" cat "$BASE/state/prepared" 2>/dev/null || true)" != "$boot_id" ]; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 60 ] || { log -p e -t RootControl 'boot preparation did not succeed'; exit 1; }
    "$TB" sleep 1
done
previous=$("$TB" cat "$BASE/state/last-boot-id" 2>/dev/null || true)
[ "$previous" != "$boot_id" ] || exit 0
printf '%s\n' "$boot_id" > "$BASE/state/last-boot-id"
log -p i -t RootControl 'starting pinned script; mode=1'
printf 'running\n' > "$BASE/state/autostart.status"
set +e
# The argument chooses mode 1; stdin also supplies the requested 1 + Enter.
printf '1\n' | "$BASE/bin/root-control" 1 >> "$BASE/logs/payload.log" 2>&1
result=$?
set -e
printf 'exited=%s\n' "$result" > "$BASE/state/autostart.status"
log -p i -t RootControl "pinned script finished; exit=$result"
exit "$result"
