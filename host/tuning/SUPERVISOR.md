# Supervising a `bench tune` study (instructions for a Claude `/loop`)

The engine (`bash tune.sh <band>`, i.e. `python -m cli.bench tune`) does the mechanics on
its own and is deterministic: it builds, flashes, measures and keeps or rejects one lever
at a time, and it resumes from its state. The supervisor's job is the judgement the
engine cannot make: noticing when a number means something other than it appears to,
and steering the candidate list. Start it with, from this checkout:

```
/loop Supervise the max-throughput study for band 2.4 per host/tuning/SUPERVISOR.md
```

Paths below are relative to `host/`. The study is named in `tuning/max-tput.yaml`.

## Each tick

1. **Is the engine alive?** `pgrep -fl "cli.bench tune"`. Read the newest
   `results/tuning/tune-<band>-*.log` tail and `results/tuning/max-tput/summary.md`.
   - Engine not running and climbs not done → find why (last log lines), fix what is
     yours to fix (see Steering), and relaunch in the background with the SAME ceilings
     the study was started with (each tune log begins "rig ceiling N Mbit/s"):
     `CEILINGS=<same> nohup bash ../tune.sh <band> > /dev/null 2>&1 &`. If it stopped on
     something only the user can fix (below), say so and stop the loop.
   - A climb `BLOCKED` → its reason says why. "candidates in a row produced no usable
     measurement" is a rig failure: read the two steps' logs, and do not relaunch until
     the cause is fixed — the engine would only block again.
   - Engine exited with every climb `done` → write the findings (last section) and stop.
2. **Read the newest step(s)** in `results/tuning/max-tput/state.json` and check:
   - `unmeasured` → open the step's `log`. Usual causes:
     - "does not carry [...]" — Kconfig dropped or clamped a symbol (an unmet dependency
       or range). Fix the value in a NEW candidate id; do not rename the old one.
     - a build error in `_agent/out/*.log` (e.g. "RX_BA_WIN should not be larger than
       double of the STATIC_RX_BUFFER_NUM") — same fix.
     - gate failures in the ledger record (`gate_failures`): offered load above the rig
       ceiling (a 40 MHz candidate needs `CEILINGS` >= 120), shared band, IDF mismatch,
       `host discarded N datagrams` (Cisco Secure Client's filter is on — user action).
   - "still unsaturated at the N Mbit/s rig ceiling" → the chip outran the rig; the
     number is a lower bound. Only a higher, measured ceiling fixes it — tell the user.
   - "lever not in effect: the link reads 'HT ch6 bw20 sta40'" → the router offered
     20 MHz; the 40 MHz lever was not tested. Tell the user it needs the router setting.
   - RSSI on the newest steps more than ~6 dB from the start step → conditions drifted;
     consider a drift check: a candidate that changes nothing measurable, e.g.
     `{id: remeasure-N, lever: none, config: {CONFIG_BENCH_PIPE_BUF_BYTES: 8192}}`
     (the default) — its "gain" is the drift.
   - UDP `loss_pct` above ~1 % at saturation, or a candidate kept on a gain barely above
     its spread → mention it; a kept-by-a-hair lever deserves a remeasure candidate.
3. **Report** in two or three lines: step reached, current best TCP/UDP per chip, anything
   flagged. Then schedule the next tick: a step takes ~4 minutes, so ~10–20 minutes.

## Steering (the only things the supervisor changes)

- Edit `tuning/max-tput.yaml`: append, reorder or remove candidates. The engine re-reads it
  before every step. A tried id is never re-run — retry with a new id.
- Good additions: a near-miss lever at a stronger value; a lever the other chip won with
  (equal effort, plan §5.4); undoing an earlier keep that a later lever made redundant
  (`null` removes an inherited symbol).
- Every symbol must exist in this ESP-IDF's Kconfig; `bench tune` refuses to start
  otherwise and says which.

## Never

- Flash or build directly, answer hostrun's flash-approval prompt, or touch the main
  checkout. Builds and flashes go through `hostrun.sh` only.
- Edit `state.json`, the ledger, or a study rung file by hand.
- Change `repeats`, `hold_s`, `transports`, `saturate_mbps` or firmware code mid-study:
  every comparison is against numbers taken under the old settings. A change like that
  is a new study (new `study:` name, new ledger and state paths).

## Findings (when every climb of the band is done)

From `summary.md`, per chip: best TCP and best UDP (Mbit/s, with loss), the rung chain
that reached them, which levers won and lost and by how much, the link (`phy_rate`), and
what limited the result (unsaturated, rig ceiling, 20 MHz router, drift). Compare with
Espressif's shield-box references in the plan header, and say which levers are worth
promoting into the formal ladders (`rungs/<chip>/`, `common-levers.yaml`) — that
promotion is the user's call, not the supervisor's.
