#!/bin/bash
# verify_build.sh — compile one ESP build exactly as hostrun.sh's esp_build would, but in a
# private build directory, so it never touches firmware/<target>/build, firmware/<target>/
# sdkconfig or a board. A compile check for code changes; it does not flash anything.
#
#   host/tools/verify_build.sh <esp32s3|esp32c5> <rung> <synth|qspi|sdio> <2.4|5|auto> [rung-file]
#
# rung-file: build a rung YAML that is not (yet) in host/rungs/, e.g. a candidate.
# Output goes to _verify/b-<target>-<rung>-<ingress>-<band>/ (build.log there).
set -euo pipefail
T=$1; RUNG=$2; ING=$3; BAND=$4; FRAG=${5:-}
WT="$(cd "$(dirname "$0")/../.." && pwd)"                       # this checkout
ROOT="$(cd "$(git -C "$WT" rev-parse --git-common-dir)/.." && pwd)"  # the repo root
TAG="$T-$RUNG-$ING-$BAND"
D="$WT/firmware/$T"; B="$WT/_verify/b-$TAG"
mkdir -p "$B"
[ -n "$FRAG" ] || FRAG="$WT/host/rungs/${T#esp32}/$RUNG.yaml"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3
"$PY" "$WT/host/tools/apply_rung.py" --rung "$FRAG" --out "$B/sdkconfig.rung" >/dev/null
DEFS="$D/sdkconfig.defaults"
LOCAL="$ROOT/firmware/$T/sdkconfig.local"
[ -f "$D/sdkconfig.local" ] && LOCAL="$D/sdkconfig.local"
[ -f "$LOCAL" ] && DEFS="$DEFS;$LOCAL"
DEFS="$DEFS;$B/sdkconfig.rung"
# shellcheck disable=SC1091
. "${IDF_EXPORT:-$HOME/Desktop/Research/esp-idf/export.sh}" >/dev/null 2>&1
cd "$D"
rm -f "$B/sdkconfig"
if idf.py -B "$B" -DSDKCONFIG="$B/sdkconfig" -DSDKCONFIG_DEFAULTS="$DEFS" -DIDF_TARGET="$T" \
     -DBENCH_INGRESS="$ING" -DBENCH_RUNG="$RUNG" -DBENCH_BAND="$BAND" build > "$B/build.log" 2>&1
then
  echo "OK   $TAG  $(grep -oE '0x[0-9a-f]+ bytes \([0-9]+%\) free' "$B/build.log" | tail -1)"
else
  echo "FAIL $TAG  (log: $B/build.log)"
  grep -nE "error:|Error|FATAL" "$B/build.log" | head -25
  exit 1
fi
