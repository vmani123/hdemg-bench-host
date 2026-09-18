#!/bin/bash
# ============================================================================
# hostrun.sh v2 — constrained action runner for the S3-vs-C5 bench (monorepo)
#
#   START:  bash hostrun.sh            (run it once, in its own Terminal window)
#   STOP :  Ctrl-C, or  touch _agent/DISABLED
#
# This runner does NOT execute scripts. It reads job files of key=value lines,
# matches 'action' against the fixed allowlist below, and runs a command line
# hard-coded HERE. The agent chooses an action and supplies narrow validated
# parameters. It cannot supply a command, a path, or a shell fragment.
#
# GUARANTEES (unchanged from v1)
#   * No deletion. This script contains no rm / rmdir / unlink / trash call.
#   * No path from a job file. Parameters are NAMES matched against a case
#     statement; absolute paths are resolved here. Traversal is impossible.
#   * No shell metacharacters. Values must match ^[A-Za-z0-9._-]+$ or refused.
#   * Everything is time-bounded. Nothing runs forever.
#   * Append-only audit log of every command actually executed.
#
# WHAT CHANGED IN v2
#   * Knows both firmware repos and builds a NAMED RUNG rather than whatever is
#     in sdkconfig.
#   * New actions: iperf, bench, hotspot, stm_build, stm_flash, power_cycle.
#   * SESSION-SCOPED FLASH APPROVAL. v1 asked you to type 'yes' for every flash,
#     which stalls an unattended sweep at the first cell. You now approve once,
#     for a bounded window; every flash is still logged.
#
# HONEST LIMITS — please read
#   * Runs as you, with your privileges. Safety comes from the allowlist being
#     small, not from any OS sandbox.
#   * Build tools execute project code. 'idf.py build' runs your CMakeLists, and
#     cmake can run arbitrary commands. The guarantee is "only these tools, only
#     on these projects" — not "nothing arbitrary ever executes".
# ============================================================================
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$ROOT"                            # the monorepo root
AGENT="$ROOT/_agent"
Q="$AGENT/queue"; O="$AGENT/out"; AUDIT="$AGENT/audit.log"
mkdir -p "$Q" "$O"

# ---- user-editable config ---------------------------------------------------
IDF_EXPORT="$HOME/esp/esp-idf/export.sh"
FLASH_WINDOW_MIN=240        # session flash approval lasts this many minutes
MAX_SECONDS=900             # hard ceiling on any single action
# -----------------------------------------------------------------------------

FLASH_OK_UNTIL=0

restrict_path() { PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"; export PATH; }
audit() { printf '%s  %s\n' "$(date -u +%FT%TZ)" "$*" >> "$AUDIT"; }
valid() { [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]]; }

run_bounded() {
  local secs="$1"; shift
  [ "$secs" -gt "$MAX_SECONDS" ] && secs="$MAX_SECONDS"
  "$@" & local pid=$!
  ( sleep "$secs"; kill -TERM "$pid" 2>/dev/null; sleep 3; kill -KILL "$pid" 2>/dev/null ) & local w=$!
  wait "$pid"; local rc=$?
  kill "$w" 2>/dev/null; wait "$w" 2>/dev/null
  return $rc
}

# A target NAME resolves to its firmware directory. This is the only way a path
# enters a command.
resolve_target_dir() {
  case "$1" in
    esp32s3) echo "$WORK/firmware/esp32s3" ;;
    esp32c5) echo "$WORK/firmware/esp32c5" ;;
    *) return 1 ;;
  esac
}
resolve_target()  { case "$1" in esp32s3|esp32c5) echo "$1" ;; *) return 1 ;; esac; }
resolve_ingress() { case "$1" in synth|qspi|sdio) echo "$1" ;; *) return 1 ;; esac; }
resolve_port()   { case "$1" in cu.*) [ -e "/dev/$1" ] && echo "/dev/$1" || return 1 ;; *) return 1 ;; esac; }

grant_flash_window() {
  echo
  echo "  >> FLASH APPROVAL: allow flashing for the next ${FLASH_WINDOW_MIN} minutes?"
  echo "  >> Every flash is still written to the audit log."
  read -r -p "  >> type 'yes' to allow: " a </dev/tty
  if [ "$a" = "yes" ]; then
    FLASH_OK_UNTIL=$(( $(date +%s) + FLASH_WINDOW_MIN * 60 ))
    audit "flash window granted until $FLASH_OK_UNTIL"
    echo "  >> granted."
    return 0
  fi
  echo "  >> denied."; return 1
}
flash_allowed() {
  [ "$(date +%s)" -lt "$FLASH_OK_UNTIL" ] && return 0
  grant_flash_window
}

# ---- actions ----------------------------------------------------------------
do_discover() {
  restrict_path
  echo "## host"; sw_vers 2>/dev/null; uname -m
  echo; echo "## tree"; for r in firmware/bench_common firmware/esp32s3 firmware/esp32c5 host; do
    [ -d "$WORK/$r" ] && echo "  ok  $r" || echo "  MISSING  $r"; done
  echo; echo "## serial ports"; ls -1 /dev/cu.* 2>/dev/null || echo "  none"
  echo; echo "## esp-idf"
  if [ -f "$IDF_EXPORT" ]; then
    ( . "$IDF_EXPORT" >/dev/null 2>&1; echo "  idf.py: $(command -v idf.py)"; idf.py --version 2>&1 | tail -1 )
  else echo "  missing: $IDF_EXPORT"; fi
  echo; echo "## host tooling"; python3 --version
  python3 -c "import yaml,matplotlib;print('  yaml+matplotlib ok')" 2>&1 | tail -1
  echo "  uhubctl: $(command -v uhubctl || echo no)"
}

do_esp_build() {   # target rung ingress
  local dir target rung ingress
  target="$(resolve_target "$1")"   || { echo "REFUSED: unknown target '$1'"; return 64; }
  dir="$(resolve_target_dir "$1")"  || { echo "REFUSED: unknown target '$1'"; return 64; }
  rung="$2"; valid "$rung"          || { echo "REFUSED: bad rung";            return 64; }
  ingress="$(resolve_ingress "${3:-synth}")" || { echo "REFUSED: bad ingress"; return 64; }
  [ -f "$IDF_EXPORT" ]              || { echo "REFUSED: no ESP-IDF at $IDF_EXPORT"; return 64; }
  local frag="$WORK/host/rungs/${target#esp32}/${rung}.yaml"
  [ -f "$frag" ] || { echo "REFUSED: no rung file $frag"; return 64; }
  restrict_path
  audit "esp_build target=$target rung=$rung ingress=$ingress"
  ( . "$IDF_EXPORT" >/dev/null 2>&1
    cd "$dir" || exit 64
    python3 "$WORK/host/tools/apply_rung.py" --rung "$frag" \
            --out "$dir/sdkconfig.rung" || exit 65
    run_bounded 600 idf.py -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.rung" \
                           set-target "$target"
    run_bounded 600 idf.py -DBENCH_INGRESS="$ingress" -DBENCH_RUNG="$rung" build )
}

do_esp_flash() {   # target port
  local dir target port
  target="$(resolve_target "$1")"  || { echo "REFUSED: unknown target '$1'"; return 64; }
  dir="$(resolve_target_dir "$1")" || { echo "REFUSED: unknown target '$1'"; return 64; }
  port="$(resolve_port "$2")"      || { echo "REFUSED: no such serial port '$2'"; return 64; }
  flash_allowed || { echo "DENIED by user"; return 77; }
  restrict_path
  audit "esp_flash target=$target port=$port"
  ( . "$IDF_EXPORT" >/dev/null 2>&1; cd "$dir" && run_bounded 300 idf.py -p "$port" flash )
}

do_esp_monitor() { # target port secs
  local dir port secs
  dir="$(resolve_target_dir "$1")" || { echo "REFUSED: unknown target '$1'"; return 64; }
  port="$(resolve_port "$2")"      || { echo "REFUSED: no such serial port '$2'"; return 64; }
  secs="$3"; [[ "$secs" =~ ^[0-9]{1,3}$ ]] || { echo "REFUSED: bad seconds"; return 64; }
  restrict_path
  audit "esp_monitor target=$1 port=$port secs=$secs"
  ( . "$IDF_EXPORT" >/dev/null 2>&1; cd "$dir" && run_bounded "$secs" idf.py -p "$port" monitor )
  echo "(monitor window ended after ${secs}s)"
}

do_iperf() {       # secs
  local secs="$1"
  [[ "$secs" =~ ^[0-9]{1,3}$ ]] || { echo "REFUSED: bad seconds"; return 64; }
  command -v iperf3 >/dev/null || { echo "REFUSED: iperf3 not installed (brew install iperf3)"; return 64; }
  restrict_path
  audit "iperf secs=$secs"
  echo "## iperf3 server — point the ESP's iperf example at this host"
  run_bounded "$secs" iperf3 -s -1
}

do_hotspot() {     # band
  local band="$1"
  case "$band" in 2.4|5) ;; *) echo "REFUSED: band must be 2.4 or 5"; return 64 ;; esac
  audit "hotspot band=$band"
  if [ "$band" = "2.4" ]; then
    echo ">> Set Personal Hotspot -> Maximize Compatibility ON  (2.4 GHz)"
  else
    echo ">> Set Personal Hotspot -> Maximize Compatibility OFF (5 GHz)"
  fi
  echo ">> The orchestrator re-verifies the band from the device before resuming;"
  echo ">> it does not take this message as proof."
}

do_bench() {       # matrix
  local matrix="$1"
  valid "$matrix" || { echo "REFUSED: bad matrix name"; return 64; }
  local dir="$WORK/host"
  local f="$dir/matrices/${matrix}.yaml"
  [ -f "$f" ] || { echo "REFUSED: no matrix $f"; return 64; }
  restrict_path
  audit "bench matrix=$matrix"
  ( cd "$dir" && run_bounded 900 python3 -m cli.bench run --matrix "matrices/${matrix}.yaml" \
      --ceilings "${BENCH_CEILINGS:-5=0,2.4=0}" --firmware "$WORK/firmware" )
}

do_parity() {
  restrict_path
  audit "parity"
  ( cd "$WORK/host" && run_bounded 120 python3 -m cli.bench parity --firmware "$WORK/firmware" )
}

do_power_cycle() { # hub_port
  local hp="$1"
  [[ "$hp" =~ ^[0-9]{1,2}$ ]] || { echo "REFUSED: bad hub port"; return 64; }
  command -v uhubctl >/dev/null || { echo "REFUSED: uhubctl not installed"; return 64; }
  restrict_path
  audit "power_cycle hub_port=$hp"
  run_bounded 60 uhubctl -a cycle -p "$hp"
}

# ---- main loop --------------------------------------------------------------
echo "hostrun v2: constrained runner"
echo "  repo      : $WORK"
echo "  queue     : $Q"
echo "  logs      : $O"
echo "  audit     : $AUDIT"
echo "  actions   : discover esp_build esp_flash esp_monitor iperf hotspot bench parity power_cycle"
echo "  flashing  : approved per session (${FLASH_WINDOW_MIN} min window), logged"
echo "  stop      : Ctrl-C, or create the file _agent/DISABLED"
echo

while true; do
  if [ -e "$AGENT/DISABLED" ]; then
    echo "hostrun: DISABLED file present — refusing all jobs. Remove it to resume."
    sleep 5; continue
  fi
  date -u +%FT%TZ > "$AGENT/heartbeat"

  shopt -s nullglob
  for f in "$Q"/*.job; do
    id="$(basename "$f" .job)"; log="$O/$id.log"
    if [ "$(wc -c < "$f")" -gt 512 ]; then
      echo "REFUSED: job file too large" > "$log"; mv "$f" "$Q/$id.done"; continue
    fi
    action=""; target=""; port=""; secs=""; rung=""; ingress=""; matrix=""; band=""; hub_port=""
    bad=0
    while IFS='=' read -r k v; do
      [ -z "${k// }" ] && continue
      case "$k" in \#*) continue ;; esac
      valid "$v" || { bad=1; break; }
      case "$k" in
        action) action="$v" ;; target) target="$v" ;; ingress) ingress="$v" ;;
        port) port="$v" ;; secs) secs="$v" ;; rung) rung="$v" ;;
        matrix) matrix="$v" ;; band) band="$v" ;; hub_port) hub_port="$v" ;;
        *) bad=1 ;;
      esac
    done < "$f"

    { echo "=== id $id  $(date -u +%FT%TZ)"
      echo "=== action=$action target=$target rung=$rung ingress=$ingress port=$port"
      echo "=== ---"; } > "$log"

    if [ "$bad" = 1 ]; then
      echo "REFUSED: invalid key or value (allowed chars: A-Za-z0-9._-)" >> "$log"
      mv "$f" "$Q/$id.done"; echo "hostrun: $id REFUSED"; continue
    fi

    echo "hostrun: job $id -> $action"
    case "$action" in
      discover)    do_discover                                       >> "$log" 2>&1 ;;
      esp_build)   do_esp_build  "$target" "${rung:-r0-baseline}" "${ingress:-synth}" >> "$log" 2>&1 ;;
      esp_flash)   do_esp_flash  "$target" "$port"                  >> "$log" 2>&1 ;;
      esp_monitor) do_esp_monitor "$target" "$port" "${secs:-30}"   >> "$log" 2>&1 ;;
      iperf)       do_iperf      "${secs:-60}"                       >> "$log" 2>&1 ;;
      hotspot)     do_hotspot    "${band:-5}"                        >> "$log" 2>&1 ;;
      bench)       do_bench      "${matrix:-stage1}"                 >> "$log" 2>&1 ;;
      parity)      do_parity                                         >> "$log" 2>&1 ;;
      power_cycle) do_power_cycle "${hub_port:-1}"                   >> "$log" 2>&1 ;;
      *)           echo "REFUSED: unknown action '$action'"          >> "$log" 2>&1 ;;
    esac
    rc=$?
    { echo "=== ---"; echo "=== exit $rc  $(date -u +%FT%TZ)"; } >> "$log"
    mv "$f" "$Q/$id.done"
    echo "hostrun: $id done exit=$rc"
  done
  shopt -u nullglob
  sleep 2
done
