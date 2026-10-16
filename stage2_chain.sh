#!/bin/bash
# ============================================================================
# stage2_chain.sh — attempt Stage 2 (STM32H745 wired ingress) after a Stage 1 block.
#
# Normally started by overnight.sh, and only when _agent/stage2.ready names this
# checkout. Can also be run by hand:
#
#   BENCH_ROOT=<repo root> CEILINGS=2.4=60,5=95 bash stage2_chain.sh 2.4
#
# "Attempt" is the operative word. Every step is a gate, and the chain stops at the
# first one that does not pass, saying why:
#
#   1. the H7 generator answers on its serial port;
#   2. for each chip that could be wired, the LINK-ONLY TEST passes: the ESP verifies
#      every frame's payload with Wi-Fi out of the loop (guide §7.2). A chip with no
#      harness attached fails this in seconds and is skipped;
#   3. only then, the Stage 2 cells for the chips that passed.
#
# Stage 2 results go to their OWN ledger (host/results/stage2.jsonl) and their own
# report directory, so nothing here can disturb the Stage 1 ledger or the code that
# reads it. Builds and flashes go through hostrun.sh like everything else, from this
# checkout (tree=...), so the repo root is never modified.
#
# Environment:
#   BENCH_ROOT     the repo root whose hostrun.sh is running (required)
#   BENCH_TREE     this checkout's worktree name under .claude/worktrees ("." = root)
#   BENCH_PY       python to use (default <root>/.venv/bin/python)
#   CEILINGS       rig ceilings, as for overnight.sh (required)
#   EXPECTED_IDF   IDF version string the firmware must report
#   S3_PORT / C5_PORT / H7_PORT   serial ports
#   STAGE2_CHIPS   chips to try (default: esp32s3 on 2.4 GHz; the C5 only if asked,
#                  because its QSPI pins include strapping pins that must be checked
#                  by hand first — guide §3.2)
#   STAGE2_HOLD    seconds per load in the link test (default 600, the guide's criterion)
#   STAGE2_QSPI_HZ QUADSPI clocks to try, fastest first (default "25000000 20000000")
#   STAGE2_SMOKE_HOLD  seconds for the first, single-load smoke test (default 20)
# ============================================================================
set -u
BAND="${1:-${BENCH_BAND:-}}"
case "$BAND" in 2.4|5) ;; *) echo "usage: BENCH_ROOT=... CEILINGS=... bash stage2_chain.sh <2.4|5>"; exit 64 ;; esac
: "${BENCH_ROOT:?set BENCH_ROOT to the repo root whose hostrun.sh is running}"
: "${CEILINGS:?set CEILINGS, e.g. CEILINGS=2.4=60,5=95}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TREE="${BENCH_TREE:-.}"
PY="${BENCH_PY:-$BENCH_ROOT/.venv/bin/python}"
S3_PORT="${S3_PORT:-cu.usbmodem101}"
C5_PORT="${C5_PORT:-cu.usbserial-110}"
H7_PORT="${H7_PORT:-cu.usbmodem21103}"
HOLD="${STAGE2_HOLD:-600}"
SMOKE_HOLD="${STAGE2_SMOKE_HOLD:-20}"
QSPI_HZ_LIST="${STAGE2_QSPI_HZ:-25000000 20000000}"
RES="$BENCH_ROOT/host/results"
STAMP="$(date +%Y%m%d-%H%M)"
LEDGER="$RES/stage2.jsonl"
mkdir -p "$RES"

say() { echo "=== stage2: $*"; }

[ -x "$PY" ] || { say "no python at $PY"; exit 64; }
hb="$BENCH_ROOT/_agent/heartbeat"
if [ ! -f "$hb" ] || [ $(( $(date +%s) - $(stat -f %m "$hb") )) -gt 30 ]; then
  say "hostrun.sh is not running in $BENCH_ROOT — nothing can be built or flashed. Skipped."
  exit 3
fi

# Which chips to try. The S3 has no 5 GHz radio, so on band 5 there is nothing to do
# unless the C5 was asked for explicitly.
if [ -n "${STAGE2_CHIPS:-}" ]; then CHIPS="$STAGE2_CHIPS"
elif [ "$BAND" = "2.4" ]; then CHIPS="esp32s3"
else CHIPS=""; fi
if [ -z "$CHIPS" ]; then
  say "no chip to try on band $BAND (set STAGE2_CHIPS=esp32c5 once its harness is verified). Skipped."
  exit 0
fi

common=(--agent-dir "$BENCH_ROOT/_agent" --firmware "$HERE/firmware"
        --s3-port "$S3_PORT" --c5-port "$C5_PORT" --h7-port "$H7_PORT")
[ "$TREE" != "." ] && common+=(--tree "$TREE")

cd "$HERE/host" || exit 64

# ---- gate 1: the generator is there -----------------------------------------
if ! "$PY" -m cli.bench h7 hello --h7-port "$H7_PORT" >/dev/null 2>&1; then
  # Not fatal by itself: the link test flashes the generator if it has to. Say so.
  say "H7 did not answer 'hello' on $H7_PORT yet; the link test will build and flash it"
fi

# ---- gate 2: link-only test per chip ----------------------------------------
passed_chips=""
chosen_hz=""
for chip in $CHIPS; do
  case "$chip" in esp32s3|esp32c5) ;; *) say "unknown chip '$chip' — skipped"; continue ;; esac
  ok_hz=""
  for hz in $QSPI_HZ_LIST; do
    # A short single-load run first: an unwired or dead link fails here in seconds
    # instead of after the first ten-minute hold.
    say "$chip qspi @ $hz Hz: smoke test (${SMOKE_HOLD}s at 4 Mbit/s)"
    if ! "$PY" -u -m cli.bench linktest --chip "$chip" --link qspi --loads 4 \
           --hold "$SMOKE_HOLD" --band "$BAND" --link-opt "qspi_hz=$hz" "${common[@]}" \
           --out "$RES/linktest-$STAMP-$chip-$hz-smoke.json"; then
      say "$chip qspi @ $hz Hz: smoke test failed"
      continue
    fi
    say "$chip qspi @ $hz Hz: full link test (${HOLD}s per load)"
    if "$PY" -u -m cli.bench linktest --chip "$chip" --link qspi \
           --hold "$HOLD" --band "$BAND" --link-opt "qspi_hz=$hz" "${common[@]}" \
           --out "$RES/linktest-$STAMP-$chip-$hz.json"; then
      ok_hz="$hz"
      break
    fi
    say "$chip qspi @ $hz Hz: full link test failed"
  done
  if [ -n "$ok_hz" ]; then
    say "$chip: link verified at $ok_hz Hz"
    # One clock for every chip in this block, so their numbers stay comparable: the
    # slowest clock any passing chip needed.
    if [ -z "$chosen_hz" ] || [ "$ok_hz" -lt "$chosen_hz" ]; then chosen_hz="$ok_hz"; fi
    passed_chips="${passed_chips:+$passed_chips,}$chip"
  else
    say "$chip: no QUADSPI clock passed the link test — no Stage 2 run for this chip"
  fi
done

if [ -z "$passed_chips" ]; then
  say "no chip passed the link test. Stage 2 not run. See $RES/linktest-$STAMP-*.json"
  exit 4
fi

# ---- gate 3 passed: the Stage 2 cells ----------------------------------------
args=(--matrix matrices/stage2.yaml --band "$BAND" --unattended --passes 2
      --ceilings "$CEILINGS" --ledger "$LEDGER"
      --chips "$passed_chips" --sources qspi --link-opt "qspi_hz=$chosen_hz"
      "${common[@]}")
[ -n "${EXPECTED_IDF:-}" ] && args+=(--expected-idf "$EXPECTED_IDF")
say "running Stage 2 cells for $passed_chips on band $BAND at qspi_hz=$chosen_hz -> $LEDGER"
"$PY" -u -m cli.bench run "${args[@]}"
rc=$?
case "$rc" in
  0) say "all Stage 2 points done" ;;
  3) say "Stage 2 passes exhausted with points remaining (see abandoned_cells above)" ;;
  *) say "Stage 2 run exited rc=$rc" ;;
esac

# Stage 1's ledger is read alongside (never written) so each wired cell is shown beside
# the same cell's synthetic number: the difference is the ingress cost.
with=()
[ -f "$RES/runs.jsonl" ] && with=(--with-ledger "$RES/runs.jsonl")
"$PY" -m cli.bench report --ledger "$LEDGER" --out "$RES/report-stage2" ${with[@]+"${with[@]}"} || \
  say "Stage 2 report could not be rendered (the ledger is intact: $LEDGER)"
exit "$rc"
