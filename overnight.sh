#!/bin/bash
# ============================================================================
# overnight.sh — run ONE hotspot band block unattended, then render the report.
#
#   CEILINGS=2.4=<mbps>,5=<mbps> bash overnight.sh 2.4    # Maximize Compatibility ON
#   CEILINGS=2.4=<mbps>,5=<mbps> bash overnight.sh 5      # OFF; C5 only
#
# Before starting: hostrun.sh running in its own Terminal (started with
# FLASH_WINDOW_MIN=720 so approval outlasts the night), the iPhone wired to the Mac
# over USB with Personal Hotspot on the right band, both boards powered.
#
# What it adds on top of `bench run`:
#   * keeps the Mac awake for exactly as long as it runs (caffeinate -w);
#   * refuses to start if hostrun.sh is not alive — every build/flash goes through it;
#   * several passes: points skipped or invalidated are retried until none remain or a
#     pass makes no progress (the ledger is the state, so this is just resume);
#   * restarts the harness if the Python process itself dies;
#   * writes everything to host/results/overnight-<stamp>-band<B>.log.
#
# Environment (defaults are this bench's):
#   CEILINGS      required. Rig ceiling per band in Mbit/s. Measure it (plan §7.4).
#   EXPECTED_IDF  IDF version string the firmware must report (default: the matrix's)
#   S3_PORT       default cu.usbmodem101      C5_PORT  default cu.usbserial-110
#   S3_HUB_PORT / C5_HUB_PORT   uhubctl ports, if a switchable hub is attached
#   MATRIX        default matrices/stage1.yaml    PASSES  default 3
# ============================================================================
set -u
BAND="${1:-}"
case "$BAND" in 2.4|5) ;; *) echo "usage: CEILINGS=2.4=N,5=N bash overnight.sh <2.4|5>"; exit 64 ;; esac
: "${CEILINGS:?set CEILINGS, e.g. CEILINGS=2.4=60,5=95 (measured rig ceilings, Mbit/s)}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
MATRIX="${MATRIX:-matrices/stage1.yaml}"
PASSES="${PASSES:-3}"
S3_PORT="${S3_PORT:-cu.usbmodem101}"
C5_PORT="${C5_PORT:-cu.usbserial-110}"
mkdir -p "$ROOT/host/results"
LOG="$ROOT/host/results/overnight-$(date +%Y%m%d-%H%M)-band${BAND}.log"

[ -x "$PY" ] || { echo "no python at $PY (create the venv or set PY=)"; exit 64; }

hb="$ROOT/_agent/heartbeat"
if [ ! -f "$hb" ] || [ $(( $(date +%s) - $(stat -f %m "$hb") )) -gt 30 ]; then
  echo "hostrun.sh is not running (no fresh $hb)."
  echo "Start it in its own Terminal first:  FLASH_WINDOW_MIN=720 bash hostrun.sh"
  exit 64
fi

args=(--matrix "$MATRIX" --band "$BAND" --unattended --passes "$PASSES"
      --ceilings "$CEILINGS" --s3-port "$S3_PORT" --c5-port "$C5_PORT")
[ -n "${EXPECTED_IDF:-}" ] && args+=(--expected-idf "$EXPECTED_IDF")
[ -n "${S3_HUB_PORT:-}" ] && args+=(--s3-hub-port "$S3_HUB_PORT")
[ -n "${C5_HUB_PORT:-}" ] && args+=(--c5-hub-port "$C5_HUB_PORT")

caffeinate -ims -w $$ &

echo "overnight: band $BAND, log $LOG"
{ echo "=== overnight.sh band=$BAND start $(date)"; echo "=== args: ${args[*]}"; } >> "$LOG"

rc=1
for attempt in 1 2 3; do
  ( cd "$ROOT/host" && "$PY" -u -m cli.bench run "${args[@]}" ) 2>&1 | tee -a "$LOG"
  rc=${PIPESTATUS[0]}
  case "$rc" in
    0) echo "=== all points done" | tee -a "$LOG"; break ;;
    2) echo "=== refused to run (parity/config) — see above" | tee -a "$LOG"; break ;;
    3) echo "=== passes exhausted with points remaining — needs a human" | tee -a "$LOG"; break ;;
    *) echo "=== harness exited rc=$rc (crash), restart $attempt/3 in 60 s" | tee -a "$LOG"
       sleep 60 ;;
  esac
done

( cd "$ROOT/host" && "$PY" -m cli.bench report ) 2>&1 | tee -a "$LOG"
echo "=== overnight.sh end $(date) rc=$rc" | tee -a "$LOG"
exit "$rc"
