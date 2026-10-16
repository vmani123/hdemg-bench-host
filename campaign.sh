#!/bin/bash
# campaign.sh — run (or resume) the follow-up campaign (docs/CAMPAIGN.md).
#
#   bash campaign.sh --sim              rehearsal against fake boards, a few minutes
#   bash campaign.sh --only W.1,0.2     the wiring hello world and the boot check
#   bash campaign.sh                    everything not done yet, in order
#   bash campaign.sh --status           the summary so far
#
# Any `bench campaign` option passes through. Start hostrun.sh first, in its own Terminal,
# from this checkout, and approve its flash window. Re-running resumes: tests already done
# are skipped, and a test cut short continues where its ledger stops.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-$HERE/.venv/bin/python}"
if [ ! -x "$PY" ]; then
  PY="$(cd "$(git -C "$HERE" rev-parse --git-common-dir)/.." && pwd)/.venv/bin/python"
fi
LOGDIR="$HERE/host/results/campaign/logs"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/campaign-$(date +%Y%m%d-%H%M%S).log"
cd "$HERE/host" || exit 1
echo "log: $LOG"
"$PY" -u -m cli.bench campaign "$@" 2>&1 | tee "$LOG"
exit "${PIPESTATUS[0]}"
