#!/usr/bin/env bash
# Run a command; every 5 s log free RAM; kill the whole process tree if free RAM drops below
# MIN_FREE_MB (default 2500).  usage: memwatch.sh <log> <command...>
LOG=$1; shift
MIN=${MIN_FREE_MB:-2500}
"$@" >> "$LOG" 2>&1 &
PID=$!
WINPID=$(cat /proc/$PID/winpid 2>/dev/null || echo "")
low=99999999
while kill -0 $PID 2>/dev/null; do
  free=$(powershell -NoProfile -Command "[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024)")
  [ "$free" -lt "$low" ] && low=$free
  if [ "$free" -lt "$MIN" ]; then
    echo "memwatch: free RAM $free MB < $MIN MB, killing" >> "$LOG"
    [ -n "$WINPID" ] && taskkill //F //T //PID "$WINPID" > /dev/null 2>&1
    kill $PID 2>/dev/null
    break
  fi
  sleep 5
done
wait $PID; code=$?
echo "memwatch: exit $code, lowest free RAM $low MB" >> "$LOG"
exit $code
