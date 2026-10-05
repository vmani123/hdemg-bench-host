#!/bin/bash
# ============================================================================
# overnight.sh — run ONE band block unattended, then render the report.
#
#   CEILINGS=2.4=<mbps>,5=<mbps> bash overnight.sh 2.4    # S3 and C5
#   CEILINGS=2.4=<mbps>,5=<mbps> bash overnight.sh 5      # C5 only
#
# Before starting: hostrun.sh running in its own Terminal (started with
# FLASH_WINDOW_MIN=720 so approval outlasts the night) and both boards on USB.
#
# The rig is the matrix's `ap:`. On the router rig (ap: verizon-router) the boards join
# the home router and the band of each cell is pinned in the firmware build; this Mac
# must be on the router's OTHER band, or wired to it — a cell on the Mac's own Wi-Fi
# band is refused by the pre-run gate, because both hops would share one channel.
# So: Mac on 5 GHz -> run the 2.4 block; for the 5 block, wire the Mac (or move it to
# 2.4 GHz). On the iPhone rig (ap: iphone-hotspot) the Mac is wired to the phone over
# USB and the band is the hotspot's Maximize Compatibility toggle.
#
# What it adds on top of `bench run`:
#   * keeps the Mac awake for exactly as long as it runs (caffeinate -w);
#   * refuses to start if hostrun.sh is not alive — every build/flash goes through it;
#   * several passes: points skipped or invalidated are retried until none remain or a
#     pass makes no progress (the ledger is the state, so this is just resume);
#   * restarts the harness if the Python process itself dies;
#   * writes everything to host/results/overnight-<stamp>-band<B>.log;
#   * afterwards, runs the Stage 2 (STM32 ingress) chain if — and only if — a handoff
#     file _agent/stage2.ready exists (see the end of this script). STAGE2=off disables it.
#
# Environment (defaults are this bench's):
#   CEILINGS      required. Rig ceiling per band in Mbit/s. Measure it (plan §7.4).
#   EXPECTED_IDF  IDF version string the firmware must report (default: the matrix's)
#   S3_PORT       default cu.usbmodem101      C5_PORT  default cu.usbserial-110
#   S3_HUB_PORT / C5_HUB_PORT   uhubctl ports, if a switchable hub is attached
#   MATRIX        default matrices/stage1.yaml    PASSES  default 3
#   TRANSPORT     tcp or udp: run only that transport; the other's points stay pending
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
[ -n "${TRANSPORT:-}" ] && args+=(--transport "$TRANSPORT")
[ -n "${S3_HUB_PORT:-}" ] && args+=(--s3-hub-port "$S3_HUB_PORT")
[ -n "${C5_HUB_PORT:-}" ] && args+=(--c5-hub-port "$C5_HUB_PORT")

caffeinate -ims -w $$ &

echo "overnight: band $BAND, log $LOG"
# Record the rig before anything is flashed: access point, how this Mac reaches it, and
# which boards already answer. Informational — boards that are not flashed yet do not
# answer, and that is not an error.
( cd "$ROOT/host" && "$PY" -m cli.bench discover --matrix "$MATRIX" ) 2>&1 | tee -a "$LOG"
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

# ---- optional Stage 2 chain ---------------------------------------------------
# Stage 2 (STM32H745 wired ingress) is attempted after this band's Stage 1 block ONLY
# if a handoff file exists. Whoever finished and bench-verified the H7 work writes it;
# its first line names the checkout to run from ("." = this one, otherwise a worktree
# under .claude/worktrees/). This script knows nothing about Stage 2 itself: it runs
# that checkout's stage2_chain.sh, which must gate itself on a link-only test before
# it takes any Wi-Fi number, and must leave the Stage 1 ledger alone.
# No handoff, STAGE2=off, or a Stage 1 that refused to run -> nothing happens.
# The exit code of this script stays Stage 1's.
hand="$ROOT/_agent/stage2.ready"
if [ "${STAGE2:-auto}" != "off" ] && [ -f "$hand" ] && { [ "$rc" = 0 ] || [ "$rc" = 3 ]; }; then
  tree="$(head -n 1 "$hand" | tr -d '[:space:]')"
  chain=""
  if [ "$tree" = "." ]; then
    chain="$ROOT/stage2_chain.sh"
  elif [[ "$tree" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]]; then
    chain="$ROOT/.claude/worktrees/$tree/stage2_chain.sh"
  fi
  if [ -n "$chain" ] && [ -f "$chain" ]; then
    echo "=== stage 2 chain: $chain (stage 1 rc=$rc) $(date)" | tee -a "$LOG"
    BENCH_ROOT="$ROOT" BENCH_TREE="$tree" BENCH_PY="$PY" BENCH_BAND="$BAND" \
    CEILINGS="$CEILINGS" EXPECTED_IDF="${EXPECTED_IDF:-}" \
    S3_PORT="$S3_PORT" C5_PORT="$C5_PORT" \
      bash "$chain" "$BAND" 2>&1 | tee -a "$LOG"
    echo "=== stage 2 chain exit ${PIPESTATUS[0]} $(date)" | tee -a "$LOG"
  else
    echo "=== stage 2 handoff names '$tree' but there is no stage2_chain.sh there — skipped" | tee -a "$LOG"
  fi
else
  echo "=== stage 2 chain not run (handoff: $([ -f "$hand" ] && echo present || echo absent), STAGE2=${STAGE2:-auto}, stage 1 rc=$rc)" | tee -a "$LOG"
fi

echo "=== overnight.sh end $(date) rc=$rc" | tee -a "$LOG"
exit "$rc"
