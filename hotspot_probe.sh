#!/bin/bash
# ============================================================================
# hotspot_probe.sh — the short hotspot test (host/matrices/hotspot-probe.yaml).
#
#   bash hotspot_probe.sh            # 2.4 GHz: Maximize Compatibility ON
#
# What it measures, on both chips: TCP with the stock build against TCP with only the
# send buffer enlarged, and UDP on the stock build at a handful of loads.
#
# AT MOST 30 MINUTES, start to finish (MAX_MINUTES). The matrix is sized to take about
# 20 (27.5 if every step takes its worst case), and the limit is hard: no run, and no flash, is started unless it can finish
# inside the budget. If the budget ends first, the points not reached stay pending and
# the same command picks them up.
#
# The rig is the phone's hotspot with THIS MAC ON THE HOTSPOT'S WI-FI, and it is
# enforced: the run is refused — before anything is built or flashed — unless this Mac
# is associated to the network named in the matrix (`ssid:`) and both boards' firmware
# is configured for it (firmware/<chip>/sdkconfig.local).
#
# Before starting: hostrun.sh running in its own Terminal, both boards on USB, the Mac
# joined to the hotspot, the phone left on the Personal Hotspot screen.
#
# Results stay out of the staged sweeps' ledger: host/results/hotspot-probe.jsonl, with
# the report in host/results/hotspot-probe-report/. Re-running resumes where it stopped.
#
# Environment (defaults are this bench's):
#   S3_PORT / C5_PORT   serial ports (S3 auto-detected between its two USB connectors)
#   CEILINGS            rig ceiling per band, Mbit/s (default 2.4=60,5=95: placeholders)
#   MAX_MINUTES         hard wall-clock limit for the whole script (default 30)
#   PASSES              default 2 (a second pass only runs if time is left)
#   RSSI_DRIFT_WARN     default 1: keep runs whose RSSI drifted >3 dB, with a warning.
#                       The S3's reported RSSI wanders on its own; set 0 to refuse them.
# ============================================================================
set -u
BAND="${1:-2.4}"
case "$BAND" in 2.4|5) ;; *) echo "usage: bash hotspot_probe.sh [2.4|5]"; exit 64 ;; esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
MATRIX="matrices/hotspot-probe.yaml"
LEDGER="results/hotspot-probe.jsonl"
OUT="results/hotspot-probe-report"
CEILINGS="${CEILINGS:-2.4=60,5=95}"
PASSES="${PASSES:-2}"
MAX_MINUTES="${MAX_MINUTES:-30}"
case "$MAX_MINUTES" in ''|*[!0-9]*) echo "MAX_MINUTES must be a whole number"; exit 64 ;; esac
# Everything is measured against this one instant, including restarts after a crash.
# 45 s are held back for the report at the end.
T_END=$(( $(date +%s) + MAX_MINUTES * 60 - 45 ))
C5_PORT="${C5_PORT:-cu.usbserial-110}"
if [ -z "${S3_PORT:-}" ]; then
  S3_PORT="cu.usbmodem101"
  for p in cu.usbmodem5B8F0676581 cu.usbmodem101; do
    if [ -e "/dev/$p" ]; then S3_PORT="$p"; break; fi
  done
fi
mkdir -p "$ROOT/host/results"
LOG="$ROOT/host/results/hotspot-probe-$(date +%Y%m%d-%H%M).log"

[ -x "$PY" ] || { echo "no python at $PY (create the venv or set PY=)"; exit 64; }

hb="$ROOT/_agent/heartbeat"
if [ ! -f "$hb" ] || [ $(( $(date +%s) - $(stat -f %m "$hb") )) -gt 30 ]; then
  echo "hostrun.sh is not running (no fresh $hb)."
  echo "Start it in its own Terminal first:  FLASH_WINDOW_MIN=240 bash hostrun.sh"
  exit 64
fi
for p in "$S3_PORT" "$C5_PORT"; do
  [ -e "/dev/$p" ] || echo "warning: /dev/$p is not present — is that board plugged in?"
done

args=(--matrix "$MATRIX" --ledger "$LEDGER" --band "$BAND" --unattended --passes "$PASSES"
      --ceilings "$CEILINGS" --s3-port "$S3_PORT" --c5-port "$C5_PORT")
[ "${RSSI_DRIFT_WARN:-1}" = "1" ] && args+=(--rssi-drift-warn)

caffeinate -ims -w $$ &

echo "hotspot probe: band $BAND, at most $MAX_MINUTES min, log $LOG"
{ echo "=== hotspot_probe.sh band=$BAND start $(date)"; echo "=== args: ${args[*]}"; } >> "$LOG"
( cd "$ROOT/host" && "$PY" -m cli.bench discover --matrix "$MATRIX" ) 2>&1 | tee -a "$LOG"

rc=1
for attempt in 1 2; do
  left=$(( T_END - $(date +%s) ))
  if [ "$left" -lt 60 ]; then
    echo "=== time budget of $MAX_MINUTES min used up before attempt $attempt" | tee -a "$LOG"
    rc=4; break
  fi
  ( cd "$ROOT/host" && "$PY" -u -m cli.bench run "${args[@]}" --max-seconds "$left" ) 2>&1 | tee -a "$LOG"
  rc=${PIPESTATUS[0]}
  case "$rc" in
    0) echo "=== all points done" | tee -a "$LOG"; break ;;
    2) echo "=== refused to run (wrong network, parity or config) — see above" | tee -a "$LOG"; break ;;
    3) echo "=== passes exhausted with points remaining — run the same command again" | tee -a "$LOG"; break ;;
    4) echo "=== $MAX_MINUTES-minute budget reached with points remaining — run the same command again to continue" | tee -a "$LOG"; break ;;
    *) echo "=== harness exited rc=$rc (crash), restart $attempt/2 in 15 s" | tee -a "$LOG"
       sleep 15 ;;
  esac
done

if [ "$rc" != 2 ]; then
  ( cd "$ROOT/host" && "$PY" -m cli.bench report --ledger "$LEDGER" --out "$OUT" ) 2>&1 | tee -a "$LOG"
  echo "=== report: $ROOT/host/$OUT/report.md" | tee -a "$LOG"
fi
echo "=== hotspot_probe.sh end $(date) rc=$rc" | tee -a "$LOG"
exit "$rc"
