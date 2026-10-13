#!/bin/bash
# ============================================================================
# tune.sh — the max-throughput study, one band per invocation.
#
#   bash tune.sh 2.4          # both chips at 2.4 GHz (run this first)
#   bash tune.sh 5            # the C5 at 5 GHz, starting from its 2.4 GHz winner
#   bash tune.sh 2.4 --sim    # rehearsal against fake ESPs, no hardware
#
# What it does: host/tuning/max-tput.yaml lists, per chip and band, a starting rung and
# candidate levers in order. Each candidate is built and flashed on top of the current
# best and measured at one saturating load per transport (TCP and UDP, 2 repeats x 20 s).
# It is kept only if it beats the current best by >= 2 % beyond the repeat spread. See
# docs/TUNING_STUDY.md.
#
# Before starting:
#   * hostrun.sh running in its own Terminal FROM THIS CHECKOUT, flash window approved;
#   * both boards on USB; firmware/<chip>/sdkconfig.local present in this checkout;
#   * this Mac on the network the plan names (ssid:), and on the OTHER band from the
#     one being measured (or wired) — the pre-run gate refuses a shared band;
#   * Cisco Secure Client's content filter off, or every UDP run is refused.
#
# Re-running the same command resumes: nothing already measured is measured again.
# Results: host/results/tuning/max-tput/summary.md (and state.json, logs/, the ledger).
#
# Environment:
#   CEILINGS          rig ceiling per band, Mbit/s (default 2.4=60,5=95 — placeholders;
#                     a 40 MHz candidate is offered 120 and is refused below that)
#   S3_PORT, C5_PORT  serial ports (default: first cu.usbmodem* / cu.usbserial-*)
#   ALLOW_SHARED_BAND 1 = measure even though this Mac shares the band (rig-limited)
#   MAX_STEPS         stop after this many measurements
#   PY                python with the host requirements
# ============================================================================
set -u
BAND="${1:-2.4}"
case "$BAND" in 2.4|5) ;; *) echo "usage: bash tune.sh [2.4|5] [--sim]"; exit 64 ;; esac
shift || true

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -z "${PY:-}" ]; then
  # This checkout's venv, or the main checkout's when this is a git worktree.
  for c in "$ROOT/.venv/bin/python" "$ROOT/../../../.venv/bin/python"; do
    [ -x "$c" ] && { PY="$c"; break; }
  done
fi
[ -n "${PY:-}" ] && [ -x "$PY" ] || { echo "no python with the host requirements (set PY=)"; exit 64; }

ARGS=(--band "$BAND" --ceilings "${CEILINGS:-2.4=60,5=95}")
[ -n "${S3_PORT:-}" ] && ARGS+=(--s3-port "$S3_PORT")
[ -n "${C5_PORT:-}" ] && ARGS+=(--c5-port "$C5_PORT")
[ "${ALLOW_SHARED_BAND:-0}" = "1" ] && ARGS+=(--allow-shared-band)
[ -n "${MAX_STEPS:-}" ] && ARGS+=(--max-steps "$MAX_STEPS")

mkdir -p "$ROOT/host/results/tuning"
LOG="$ROOT/host/results/tuning/tune-$BAND-$(date +%Y%m%d-%H%M).log"
echo "tune: band $BAND  log $LOG"
cd "$ROOT/host" && "$PY" -u -m cli.bench tune "${ARGS[@]}" "$@" 2>&1 | tee "$LOG"
exit "${PIPESTATUS[0]}"
