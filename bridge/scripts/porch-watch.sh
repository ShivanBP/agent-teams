#!/bin/bash
# One porch watcher per mic owner: arming kills its predecessor, so it is idempotent.
D=~/.voicemode/send-button
P="$D/watcher.pid"

stop() { [ -f "$P" ] && ps -p "$(cat "$P")" -o command= 2>/dev/null | grep -q porch-watch \
  && kill "$(cat "$P")" 2>/dev/null; rm -f "$P"; }

stop
[ "$1" = "--stop" ] && exit 0

echo $$ > "$P"
# A Talk tap while the mic is open is heard by converse and never consumes its flag, so
# talk-requested is routinely left on disk; arming without clearing wakes instantly (2026-08-02).
rm -f "$D/talk-requested" "$D/cancelled" "$D/sleep-requested"
while true; do
  if [ -f "$D/talk-requested" ]; then rm -f "$D/talk-requested" "$P"; echo TALK; exit 0; fi
  rm -f "$D/cancelled" "$D/sleep-requested"
  sleep 0.5
done
