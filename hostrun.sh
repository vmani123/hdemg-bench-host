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
#   * stm_build / stm_flash drive the STM32H745 Stage-2 generator: a headless
#     STM32CubeIDE build and STM32_Programmer_CLI over the on-board ST-LINK.
#   * tree=<name> on a build/flash job selects WHICH CHECKOUT of this repository
#     is built: empty is the repo root, otherwise the git worktree
#     .claude/worktrees/<name>. It is a name matched against a strict pattern and
#     resolved here, never a path. This lets work in progress be built and
#     flashed without touching the checkout a running sweep is measuring from.
#     Builds from a worktree run at low priority for the same reason.
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
IDF_EXPORT="${IDF_EXPORT:-$HOME/Desktop/Research/esp-idf/export.sh}"
# Session flash approval lasts this many minutes. An overnight block runs ~5 h; if the
# window lapses mid-sweep the next flash blocks on a prompt nobody is awake to answer,
# so start an unattended night with FLASH_WINDOW_MIN=720.
FLASH_WINDOW_MIN="${FLASH_WINDOW_MIN:-240}"
MAX_SECONDS=900             # hard ceiling on any single action
CUBEIDE="${CUBEIDE:-/Applications/STM32CubeIDE.app/Contents/MacOS/STM32CubeIDE}"
# STM32_Programmer_CLI ships inside CubeIDE; found at use if not set here.
STM_PROG="${STM_PROG:-}"
# -----------------------------------------------------------------------------

FLASH_OK_UNTIL=0

# /opt/local/bin is MacPorts: this Mac's cmake and ninja live there, and ESP-IDF does
# not ship its own, so without it every build fails with "cmake not found".
restrict_path() { PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin:/opt/local/bin"; export PATH; }
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

# A tree NAME resolves to a checkout of this repository: empty is the repo root,
# anything else must be an existing git worktree under .claude/worktrees/. The pattern
# is stricter than valid(): no dots, so no '..'.
resolve_tree() {
  [ -z "$1" ] && { echo "$WORK"; return 0; }
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] || return 1
  local d="$ROOT/.claude/worktrees/$1"
  [ -d "$d/firmware" ] && [ -d "$d/host" ] || return 1
  echo "$d"
}
# Builds from a worktree can coincide with a measurement run on this Mac; keep them
# from competing with the receiver. 0 is a no-op.
tree_nice() { [ -z "$1" ] && echo 0 || echo 19; }

# A target NAME (within a resolved tree) resolves to its firmware directory. Names are
# the only way a path enters a command.
resolve_target_dir() {   # target tree_dir
  case "$1" in
    esp32s3) echo "$2/firmware/esp32s3" ;;
    esp32c5) echo "$2/firmware/esp32c5" ;;
    *) return 1 ;;
  esac
}
resolve_stm_project() { case "$1" in hdemg_h745) echo "$1" ;; *) return 1 ;; esac; }
resolve_stm_config()  { case "$1" in Debug|Release) echo "$1" ;; *) return 1 ;; esac; }
resolve_stm_core()    { case "$1" in cm7) echo CM7 ;; cm4) echo CM4 ;; *) return 1 ;; esac; }
resolve_stm_mode()    { case "$1" in UR|NORMAL|HOTPLUG) echo "$1" ;; *) return 1 ;; esac; }
resolve_stm_prog() {
  if [ -n "$STM_PROG" ]; then [ -x "$STM_PROG" ] && echo "$STM_PROG" || return 1; return; fi
  local p
  for p in /Applications/STM32CubeIDE.app/Contents/Eclipse/plugins/com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.macos64_*/tools/bin/STM32_Programmer_CLI; do
    [ -x "$p" ] && { echo "$p"; return 0; }
  done
  return 1
}
resolve_target()  { case "$1" in esp32s3|esp32c5) echo "$1" ;; *) return 1 ;; esac; }
resolve_ingress() { case "$1" in synth|qspi|sdio) echo "$1" ;; *) return 1 ;; esac; }
resolve_port()   { case "$1" in cu.*) [ -e "/dev/$1" ] && echo "/dev/$1" || return 1 ;; *) return 1 ;; esac; }

grant_flash_window() {
  # Actions run with stdout/stderr redirected into the job log, so the question has to
  # be written to the terminal explicitly or nobody ever sees it. No terminal, no grant.
  local a=""
  if ! { : > /dev/tty; } 2>/dev/null; then
    echo "  >> no terminal to ask for flash approval on — denied."
    return 1
  fi
  { echo
    echo "  >> FLASH APPROVAL: allow flashing (ESP boards and the STM32 H7) for the next ${FLASH_WINDOW_MIN} minutes?"
    echo "  >> Every flash is still written to the audit log."
    printf "  >> type 'yes' to allow: "; } > /dev/tty
  read -r a < /dev/tty || a=""
  if [ "$a" = "yes" ]; then
    FLASH_OK_UNTIL=$(( $(date +%s) + FLASH_WINDOW_MIN * 60 ))
    audit "flash window granted until $FLASH_OK_UNTIL"
    echo "  >> granted." > /dev/tty
    echo "flash window granted for ${FLASH_WINDOW_MIN} min"
    return 0
  fi
  echo "  >> denied." > /dev/tty
  return 1
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

do_esp_build() {   # target rung ingress tree
  local dir target rung ingress tdir pri
  target="$(resolve_target "$1")"   || { echo "REFUSED: unknown target '$1'"; return 64; }
  tdir="$(resolve_tree "${4:-}")"   || { echo "REFUSED: unknown tree '${4:-}'"; return 64; }
  dir="$(resolve_target_dir "$1" "$tdir")" || { echo "REFUSED: unknown target '$1'"; return 64; }
  pri="$(tree_nice "${4:-}")"
  rung="$2"; valid "$rung"          || { echo "REFUSED: bad rung";            return 64; }
  ingress="$(resolve_ingress "${3:-synth}")" || { echo "REFUSED: bad ingress"; return 64; }
  [ -f "$IDF_EXPORT" ]              || { echo "REFUSED: no ESP-IDF at $IDF_EXPORT"; return 64; }
  local frag="$tdir/host/rungs/${target#esp32}/${rung}.yaml"
  [ -f "$frag" ] || { echo "REFUSED: no rung file $frag"; return 64; }
  [ -d "$dir" ]  || { echo "REFUSED: no firmware dir $dir"; return 64; }
  # sdkconfig.local holds the hotspot credentials and is not in git, so a worktree does
  # not have one. Link the repo root's rather than copy it: one source of truth.
  if [ ! -e "$dir/sdkconfig.local" ] && [ -f "$WORK/firmware/$target/sdkconfig.local" ]; then
    ln -s "$WORK/firmware/$target/sdkconfig.local" "$dir/sdkconfig.local"
  fi
  restrict_path
  audit "esp_build target=$target rung=$rung ingress=$ingress tree=${4:-.}"
  ( . "$IDF_EXPORT" >/dev/null 2>&1
    cd "$dir" || exit 64
    python3 "$tdir/host/tools/apply_rung.py" --rung "$frag" \
            --out "$dir/sdkconfig.rung" || exit 65
    local sdkdefs="sdkconfig.defaults;sdkconfig.rung"
    [ -f "$dir/sdkconfig.local" ] && sdkdefs="sdkconfig.defaults;sdkconfig.local;sdkconfig.rung"
    run_bounded 600 nice -n "$pri" idf.py -D SDKCONFIG_DEFAULTS="$sdkdefs" set-target "$target" || exit 66
    run_bounded 600 nice -n "$pri" idf.py -DBENCH_INGRESS="$ingress" -DBENCH_RUNG="$rung" build )
}

do_esp_flash() {   # target port tree
  local dir target port tdir
  target="$(resolve_target "$1")"  || { echo "REFUSED: unknown target '$1'"; return 64; }
  tdir="$(resolve_tree "${3:-}")"  || { echo "REFUSED: unknown tree '${3:-}'"; return 64; }
  dir="$(resolve_target_dir "$1" "$tdir")" || { echo "REFUSED: unknown target '$1'"; return 64; }
  port="$(resolve_port "$2")"      || { echo "REFUSED: no such serial port '$2'"; return 64; }
  [ -d "$dir/build" ]              || { echo "REFUSED: nothing built in $dir"; return 64; }
  flash_allowed || { echo "DENIED by user"; return 77; }
  restrict_path
  audit "esp_flash target=$target port=$port tree=${3:-.}"
  ( . "$IDF_EXPORT" >/dev/null 2>&1; cd "$dir" && run_bounded 300 idf.py -p "$port" flash )
}

do_esp_monitor() { # target port secs
  local dir port secs
  dir="$(resolve_target_dir "$1" "$WORK")" || { echo "REFUSED: unknown target '$1'"; return 64; }
  port="$(resolve_port "$2")"      || { echo "REFUSED: no such serial port '$2'"; return 64; }
  secs="$3"; [[ "$secs" =~ ^[0-9]{1,3}$ ]] || { echo "REFUSED: bad seconds"; return 64; }
  restrict_path
  audit "esp_monitor target=$1 port=$port secs=$secs"
  ( . "$IDF_EXPORT" >/dev/null 2>&1; cd "$dir" && run_bounded "$secs" idf.py -p "$port" monitor )
  echo "(monitor window ended after ${secs}s)"
}

# The STM32H745 Stage-2 generator (docs/STM32_STAGE2_GUIDE.md §1). The project is a
# dual-core STM32CubeIDE project; one headless CubeIDE run builds it, with a workspace
# of its own per tree (the GUI locks its workspace, and two trees hold same-named
# projects).
do_stm_build() {   # project config core tree
  local proj cfg tdir dir ws pri cores c sha
  proj="$(resolve_stm_project "$1")" || { echo "REFUSED: unknown stm project '$1'"; return 64; }
  cfg="$(resolve_stm_config "$2")"   || { echo "REFUSED: config must be Debug or Release"; return 64; }
  case "$3" in cm7) cores="CM7" ;; cm4) cores="CM4" ;; all) cores="CM7 CM4" ;;
               *) echo "REFUSED: core must be cm7, cm4 or all"; return 64 ;; esac
  tdir="$(resolve_tree "${4:-}")"    || { echo "REFUSED: unknown tree '${4:-}'"; return 64; }
  dir="$tdir/firmware/stm32h745/$proj"
  [ -d "$dir/CM7" ] || { echo "REFUSED: no project at $dir"; return 64; }
  [ -x "$CUBEIDE" ] || { echo "REFUSED: no STM32CubeIDE at $CUBEIDE"; return 64; }
  pri="$(tree_nice "${4:-}")"
  ws="$AGENT/cubeide-ws/${4:-_root}"
  mkdir -p "$ws"
  restrict_path
  audit "stm_build project=$proj config=$cfg core=$3 tree=${4:-.}"
  # What the firmware reports as fw_sha in `hello`, so a run traces back to a build.
  sha="$(cd "$tdir" && git describe --always --dirty 2>/dev/null || echo unknown)"
  valid "$sha" || sha="unknown"
  if [ "$(cat "$dir/CM7/Core/Inc/fw_version.h" 2>/dev/null)" != "#define FW_SHA \"$sha\"" ]; then
    printf '#define FW_SHA "%s"\n' "$sha" > "$dir/CM7/Core/Inc/fw_version.h"
  fi
  local args=()
  for c in $cores; do args+=(-build "${proj}_${c}/${cfg}"); done
  run_bounded 600 nice -n "$pri" "$CUBEIDE" --launcher.suppressErrors -nosplash \
      -application org.eclipse.cdt.managedbuilder.core.headlessbuild \
      -data "$ws" -importAll "$dir" "${args[@]}"
}

do_stm_flash() {   # project core config tree mode
  local proj cfg core tdir elf prog mode
  proj="$(resolve_stm_project "$1")" || { echo "REFUSED: unknown stm project '$1'"; return 64; }
  core="$(resolve_stm_core "$2")"    || { echo "REFUSED: core must be cm7 or cm4"; return 64; }
  cfg="$(resolve_stm_config "$3")"   || { echo "REFUSED: config must be Debug or Release"; return 64; }
  tdir="$(resolve_tree "${4:-}")"    || { echo "REFUSED: unknown tree '${4:-}'"; return 64; }
  mode="$(resolve_stm_mode "${5:-UR}")" || { echo "REFUSED: mode must be UR, NORMAL or HOTPLUG"; return 64; }
  elf="$tdir/firmware/stm32h745/$proj/$core/$cfg/${proj}_${core}.elf"
  [ -f "$elf" ] || { echo "REFUSED: no ELF at $elf — run stm_build first"; return 64; }
  prog="$(resolve_stm_prog)" || { echo "REFUSED: STM32_Programmer_CLI not found"; return 64; }
  flash_allowed || { echo "DENIED by user"; return 77; }
  restrict_path
  audit "stm_flash project=$proj core=$2 config=$cfg tree=${4:-.} mode=$mode elf=$elf"
  # The ELF carries its load address (CM7 -> 0x08000000, CM4 -> 0x08100000). mode=UR
  # connects under reset, which also recovers a board stuck in a fault loop.
  run_bounded 180 "$prog" -c port=SWD mode="$mode" -w "$elf" -v -rst
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
echo "  actions   : discover esp_build esp_flash esp_monitor stm_build stm_flash iperf hotspot bench parity power_cycle"
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
    tree=""; project=""; config=""; core=""; mode=""
    bad=0
    while IFS='=' read -r k v; do
      [ -z "${k// }" ] && continue
      case "$k" in \#*) continue ;; esac
      valid "$v" || { bad=1; break; }
      case "$k" in
        action) action="$v" ;; target) target="$v" ;; ingress) ingress="$v" ;;
        port) port="$v" ;; secs) secs="$v" ;; rung) rung="$v" ;;
        matrix) matrix="$v" ;; band) band="$v" ;; hub_port) hub_port="$v" ;;
        tree) tree="$v" ;; project) project="$v" ;; config) config="$v" ;;
        core) core="$v" ;; mode) mode="$v" ;;
        *) bad=1 ;;
      esac
    done < "$f"

    { echo "=== id $id  $(date -u +%FT%TZ)"
      echo "=== action=$action target=$target rung=$rung ingress=$ingress port=$port tree=$tree project=$project core=$core config=$config"
      echo "=== ---"; } > "$log"

    if [ "$bad" = 1 ]; then
      echo "REFUSED: invalid key or value (allowed chars: A-Za-z0-9._-)" >> "$log"
      mv "$f" "$Q/$id.done"; echo "hostrun: $id REFUSED"; continue
    fi

    echo "hostrun: job $id -> $action"
    case "$action" in
      discover)    do_discover                                       >> "$log" 2>&1 ;;
      esp_build)   do_esp_build  "$target" "${rung:-r0-baseline}" "${ingress:-synth}" "$tree" >> "$log" 2>&1 ;;
      esp_flash)   do_esp_flash  "$target" "$port" "$tree"          >> "$log" 2>&1 ;;
      stm_build)   do_stm_build  "${project:-hdemg_h745}" "${config:-Release}" "${core:-cm7}" "$tree" >> "$log" 2>&1 ;;
      stm_flash)   do_stm_flash  "${project:-hdemg_h745}" "${core:-cm7}" "${config:-Release}" "$tree" "${mode:-UR}" >> "$log" 2>&1 ;;
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
