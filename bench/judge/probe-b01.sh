#!/bin/sh
# probe-b01.sh PORT PRISTINE_ROOT (task B01), run from the judged copy's root. protocol.md, "Probes".
# Starts the judged server on PORT and the unchanged one on PORT+1; COMMAND DOCS must be byte-identical.
P=$1; Q=$((P + 1)); PR=$2; T=${RUN_TMP:?RUN_TMP must be set}
mkdir -p "$T/judged" "$T/pristine"
./src/redis-server --port "$P" --save '' --appendonly no --daemonize yes --pidfile "$T/judged.pid" \
  --dir "$T/judged" || exit 2
"$PR/src/redis-server" --port "$Q" --save '' --appendonly no --daemonize yes --pidfile "$T/pristine.pid" \
  --dir "$T/pristine" || exit 2
stop() {
  ./src/redis-cli -p "$P" SHUTDOWN NOSAVE >/dev/null 2>&1
  "$PR/src/redis-cli" -p "$Q" SHUTDOWN NOSAVE >/dev/null 2>&1
  for f in "$T/judged.pid" "$T/pristine.pid"; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null; done
}
trap stop EXIT
for port in "$P" "$Q"; do
  i=0; until ./src/redis-cli -p "$port" PING >/dev/null 2>&1; do i=$((i+1)); [ $i -gt 50 ] && exit 2; sleep 0.1; done
done
./src/redis-cli -p "$P" COMMAND DOCS get set lindex xadd zadd > "$T/docs-judged.txt" || exit 1
"$PR/src/redis-cli" -p "$Q" COMMAND DOCS get set lindex xadd zadd > "$T/docs-pristine.txt" || exit 1
cmp "$T/docs-judged.txt" "$T/docs-pristine.txt" || exit 1
exit 0
