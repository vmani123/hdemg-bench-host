# Max-throughput tuning study

**Question:** how fast can each chip go on this rig — one number per transport (TCP, UDP)
per band — and which levers get it there? A short study with its own ledger, separate from
the staged sweeps and not comparable with them.

Engine: `host/bench/tune.py` · command: `bench tune` / `tune.sh` · plan:
`host/tuning/max-tput.yaml` · supervisor: `host/tuning/SUPERVISOR.md`.

---

## 1. How a test works

```
 host/tuning/max-tput.yaml          (re-read before every step: the supervisor's lever)
   per chip+band: start rung, ordered candidate levers
        │
        ▼
 bench tune  (deterministic climber)                                 state.json
   ├─ step 0: measure the starting rung ──────────────┐             summary.md
   └─ step n: write rung  t24-NN-<lever>              │             logs/, matrices/
              parent = current best, + ONE lever      │                   ▲
        │                                             ▼                   │
        │   generated matrix: 1 chip, 1 rung, tcp + udp, one saturating load, 2 repeats
        ▼                                                                 │
 bench run  (the normal sweep machinery, unchanged: gates, resume, keepalive, recovery)
   ├─ hostrun.sh: esp_build (rung → sdkconfig.rung) → check every rung symbol landed
   │              → esp_flash → wait for boot, verify the board runs that rung
   └─ 2 × TCP + 2 × UDP runs of 20 s  ──► results/tuning/max-tput.jsonl (the ledger)
        │
        ▼
 decide: kept if a transport gains ≥ 2 % beyond the repeat spread and none regresses
         → becomes the parent of the next candidate; otherwise the best stays.
         stop a chip after 3 misses in a row (plan §5.4) or when the list runs out.

 Claude /loop supervisor (optional, on top): reads summary/state each tick, flags what a
 number really means (unsaturated, 40 MHz not offered, drift, refused UDP), edits the plan.
```

**Why one saturating load instead of a sweep.** The question is the maximum. Offering more
than the chip can carry makes the goodput read back *be* the maximum. That is valid for
UDP too now: a full Wi-Fi TX queue makes the sender wait (it no longer drops), so
the excess never gets a sequence number and never shows up as loss. What the ledger
calls loss at saturation is real air/AP/host loss. Each run's `source_limited` warning is
expected — it is how saturation looks. A result within 10 % of the offered load is
flagged `unsaturated`: the load set it, not the chip.

**Why the rungs don't disturb Stage 1.** Study rungs carry `study: max-tput`; the formal
ladder (`tuned`, the stop rule, `bench effort`) ignores them. Winners are promoted into
the formal ladders by hand afterwards.

## 2. Timing (2 repeats × 20 s)

"Repeats" stays at 2. Per step (= one configuration on one chip):

| part | time |
|---|---|
| build (set-target + full build) + flash + boot + join + found by discovery | ~1.5–2 min |
| 4 runs (TCP ×2, UDP ×2) × (20 s hold + ~8 s per-run overhead) | ~2 min |
| **one step** | **~3.5–4 min** |

| block | steps (worst case: no early stop) | time |
|---|---|---|
| S3 @ 2.4 GHz | start + 12 candidates | ~50 min |
| C5 @ 2.4 GHz | start + 11 candidates | ~45 min |
| **2.4 GHz block** (`bash tune.sh 2.4`) | | **~1.6 h**, less once a chip plateaus |
| C5 @ 5 GHz (`bash tune.sh 5`) | start + 2 | ~12 min |

## 3. Why the Mac can't sit on 5 GHz while the C5 is measured there

The router has one 5 GHz radio on one channel (today ch104, 80 MHz). The Mac is on it. A
C5 joining 5 GHz lands on the same channel, so every frame crosses that channel twice —
C5 → router, then router → Mac — and a Wi-Fi channel is half-duplex: the two hops take
turns for the same airtime. The Mac's hop is fast (80 MHz, 802.11ax) so it costs less air
than the C5's 20 MHz hop, but by an amount nobody has measured, and the C5's number
would then describe the rig, not the chip. The pre-run gate refuses it by default.

For the 2.4 GHz block the Mac on 5 GHz is exactly right (the other band). For the 5 GHz
block, pick one:

1. **Wire the Mac** to the router (USB-C Ethernet adapter). Best: the second hop leaves
   the air entirely.
2. **Move the Mac to 2.4 GHz** for that block. With one SSID for both bands macOS picks the
   band itself, so this needs the router to offer the bands under separate names, and the
   C5's `sdkconfig.local` to name the 5 GHz one.
3. **Accept it**: `ALLOW_SHARED_BAND=1 bash tune.sh 5`. Every run carries the warning and
   the number is a lower bound.

## 4. What changed in the firmware for this (both chips, one shared core)

| change | why |
|---|---|
| UDP datagrams ≤ 1472 B of whole frames (`CONFIG_BENCH_UDP_DGRAM_MAX`, 0 = old 8 KB) | an 8 KB datagram was IP-fragmented 6 ways; one lost fragment lost 30 frames |
| UDP `ENOMEM`/`ENOBUFS` → wait 1 ms and resend; no socket close | a full TX queue is back-pressure, not a socket fault or network loss |
| source sleeps 1 tick when nothing is due (was a `taskYIELD` spin) | the spin starved the control plane and idle hook on the single-core C5 |
| pool geometry from Kconfig (`CONFIG_BENCH_PIPE_NUM_BUFS/_BUF_BYTES`, default 6 × 8 KB) | plan §5.3 lists it as a per-chip lever; rungs could not reach it |
| channel width hook (`CONFIG_BENCH_WIFI_BW_20/_40`); on the C5, 40 MHz means 802.11n | 40 MHz doubles the PHY ceiling; IDF makes 11ax/11ac 20 MHz-only |
| `phy_rate` now also reports the station's width: `HT ch6 bw20 sta40` | `bw` is what the AP offers; the link is the narrower — the only way to see a 40 MHz lever take effect |
| host: a build that dropped or clamped a rung symbol is refused before flashing | Kconfig does that silently; the binary would measure the parent |
| host: `r0-baseline` no longer sets the two `POWER_SAVE` symbols | they do not exist in IDF v6 (dropped silently); power save is off at runtime |

Defaults keep behaviour except the UDP datagram size (the old size is `=0`). Every
UDP run in the old ledger was voided, so no comparable data is lost.

## 5. Steps to run it

1. **Network and credentials.** The plan names the rig (`ap:`, a label recorded in every
   run) and the network (`ssid:`, enforced: nothing is built or flashed unless this Mac
   is on it and both boards' firmware joins it). It ships set to the router rig
   (`verizon-router` / `Verizon_SLD967`); on another network change those two lines —
   any label other than the hotspot ones behaves as a router rig. Then put
   `firmware/esp32s3/sdkconfig.local` and `firmware/esp32c5/sdkconfig.local` (gitignored;
   template `firmware/sdkconfig.local.example`) in this checkout, naming that network.
2. **Rig ceiling.** Measure what a known-good 2.4 GHz client gets to this Mac (`iperf3 -s`
   on the Mac; a phone or laptop on the router's 2.4 GHz band runs the client) and pass it
   as `CEILINGS=2.4=<measured>,5=<measured>`. With the placeholder `2.4=60`, the 40 MHz
   candidates (offered 120) are refused and recorded as unmeasured.
3. **Optional, for the 40 MHz levers:** set the router's 2.4 GHz channel width to 20/40
   (auto). Otherwise those steps show `bw20 sta40` and test nothing.
4. **Cisco Secure Client content filter off** for the session (it voided every UDP run
   on Oct 5; the gate now refuses such runs).
5. **Start hostrun from this checkout**, in its own Terminal:
   `bash <this checkout>/hostrun.sh` — type `yes` at the flash prompt. (`FLASH_WINDOW_MIN`
   defaults to 240 minutes, which covers the 2.4 GHz block.)
6. **Rehearse** (no hardware, a few minutes): `bash tune.sh 2.4 --sim`. Fake ESPs, 1 s
   holds, its own state/ledger/rungs (`results/tuning/max-tput-sim*`); it exercises the
   plumbing, not the decisions (a fake's speed does not depend on the rung).
7. **Run the 2.4 GHz block:** `CEILINGS=2.4=60,5=95 bash tune.sh 2.4`
   — or have Claude run and supervise it: `/loop Supervise the max-throughput study for
   band 2.4 per host/tuning/SUPERVISOR.md`.
8. **Watch:** `host/results/tuning/max-tput/summary.md`, or
   `cd host && python -m cli.bench tune --band 2.4 --status`.
9. **5 GHz block** (after choosing an option in §3): `bash tune.sh 5`.
10. **Afterwards:** promote the winning levers into `host/rungs/<chip>/` and mark them in
    `common-levers.yaml`; the study's rung files (`t24-*`, `t5-*`) record every result.
