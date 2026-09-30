# Bench harness specification — automating the S3-vs-C5 throughput comparison

**Companion to** `S3_vs_C5_Test_Plan_v2.md` (v2.1). This document specifies what gets built; no code
is written until it is signed off.

**Updated 2026-09-16:** no ESP-side compression (codec removed from the control plane and the build
matrix); **iPhone 17 Pro is the access point, with the Mac wired to it over USB-C** (see plan §7) —
new §3.6 band scheduling, §3.7 association keepalive, and §6 hotspot gates. **One repository**, in which the
measurement core is a single shared component both targets compile — see §1.0.

**Scale of the problem:** 12 (Stage 1) + 6 (Stage 2a) + 4 (Stage 2b) = **22 cells**, most swept over
~8 offered loads and repeated 3×, plus 18 Stage-0 runs. Order of **500 measured runs**. Done by hand
— flash, edit config, reflash, start a receiver, read a number off a terminal, type it into a table —
that is weeks of bench time and the numbers are not reproducible.

---

## 0. Where the time goes, and what removes it

| Manual cost | Removed by | Saving |
|---|---|---|
| Re-flashing for every rate / transport / destination change | **Runtime control plane** (§1.2) — one binary per (chip, ingress); everything else set at runtime | Hundreds of flashes → ~4 |
| Re-flashing for build-time tuning levers | Generated `sdkconfig` fragments + build cache keyed on config hash (§3.3) | Rebuild only when the hash changes |
| Starting receivers, timing runs, stepping the sweep | **Orchestrator** (§3) | The whole inner loop |
| Transcribing numbers into a table | **Ledger** `runs.jsonl` (§4) + report generator (§5) | All of it, and it becomes reproducible |
| Recording RSSI / channel / PHY rate / IDF version / git SHA | Auto-captured metadata (§3.5) | All of it, and it stops being forgotten |
| Reconfiguring the hotspot between bands | **Band-ordered scheduling** (§3.6) | Many switches → at most two per stage |
| Re-associating after the hotspot's 90 s idle timeout | **Keepalive between runs** (§3.7) | The overnight-sweep killer |
| Averaging in a run that was silently invalid | **Validity gates** (§6) | The silent-corruption failure mode |
| Pressing BOOT/RESET, recovering a wedged board | `uhubctl` per-port USB power (§7.2) | The last routine manual step |
| Debugging the harness during bench time | **Loopback self-test** (§8) | The highest-value item here |

**Honestly not automatable:** enabling Personal Hotspot and toggling Maximize Compatibility, choosing
an RF-quiet spot and fixing placement, and first-time QSPI/SDIO wiring and signal-integrity debug. Budget those as human
hours and automate everything downstream.

---

## 1. Firmware — one shared measurement core, two targets

**Layout note (superseded design).** This section previously described three repositories
— one per ESP target plus the host — kept comparable by a byte-for-byte checker. That was
replaced by a single repository in which the measurement core exists **once** and both
targets compile it. The reasoning below is why, because it is the same reasoning that
decides what may and may not diverge between the chips.

### 1.0 One core, not two kept in sync

```
firmware/bench_common/     THE measurement core — one copy, both targets compile it
firmware/esp32s3/          entry point, pin map, QSPI ingress
firmware/esp32c5/          entry point, pin map, QSPI + SDIO ingress
host/                      harness, per-chip rung ladders, matrices, ledger, report
```

**What must match, and what must not.** This is a **best-effort comparison** (plan §5.2):
each chip is tuned independently for maximum performance, including with levers the other
chip does not have. So the two builds are *expected* to diverge in configuration. What must
not diverge is the **measurement contract** — if the two count goodput at different points
in the pipeline, pace at different accuracies, or stamp latency from different instants,
the comparison is void no matter how well each chip is tuned, and nothing in the data would
reveal it.

Two copies of that code, however carefully reviewed, can drift. One copy makes the drift
impossible **by construction**, which is stronger than any checker. That is the whole
argument for the monorepo, and it is why `firmware/bench_common/` holds:

frame construction and `hdemg_frame.h` (§1.4) · the byte-counting point, `pipe_note_sent()`
in `pipe.c`, called once by the sink task and nowhere else · the pacing token bucket (§1.3)
· the latency stamp instant · the instrumentation definitions (§1.5) · the control-plane
protocol (§1.2).

**Free to differ — this is the experiment:** every `sdkconfig` lever; buffer pool geometry,
frame size and queue depths; task and core placement; PSRAM use; ingress peripheral init
and pin maps; `source_sdio.c` (C5 only); band.

The line between the two lists is: *does this change what the number means, or how big the
number is?* The first is locked; the second is what we are measuring.

### 1.0.1 The host owns the tuning, the firmware owns none of it

Per-chip ladders live in `host/rungs/s3/` and `host/rungs/c5/`, and
`firmware/*/sdkconfig.defaults` carries **environment only** — nothing that affects
throughput. The ladders are allowed, expected, to differ. Keeping them on the host side
buys three things: the orchestrator applies a rung without a firmware commit; each rung
records its **measured gain**, making the ladder the evidence that the equal-effort
protocol (plan §5.4) was followed; and the common-lever list is defined once, so a lever
attempted on one chip is visibly either attempted or explicitly declined on the other
rather than silently forgotten.

```yaml
# host/rungs/s3/r2-core-split.yaml
parent: r1-cpu-opt
applies_to: esp32s3
lever: core_split
rationale: "S3-only: separate Wi-Fi and lwIP tasks across cores"
config:
  CONFIG_ESP_WIFI_TASK_CORE_ID_0: y
  CONFIG_LWIP_TCPIP_TASK_AFFINITY_CPU1: y
measured_gain_pct: null      # filled in as the ladder is climbed
```

### 1.0.2 What the parity check still does

With one copy of the core, file drift cannot happen. `host/bench/parity.py` guards the way
the guarantee breaks in a monorepo instead: a target whose CMakeLists stops pointing at the
shared component, a contract file copied into `firmware/<target>/main/` to "just tweak it
for this chip", a duplicated `CONTROL_PROTOCOL_VERSION`, or a missing IDF pin. It
deliberately does **not** compare `sdkconfig`.

### 1.1 Build-time vs runtime

**Build-time only:**

- `IDF_TARGET` — `esp32s3` | `esp32c5`
- Ingress: `synth` | `qspi` | `sdio` (peripheral init and pin mux differ)
- Tuning rung: an `sdkconfig` fragment name (IRAM placement, buffer sizes, lwIP core-locking, `-O2`)

`sdio` is C5-only and `synth` carries all of Stage 1, so the working set during Stage 1 is **two
binaries** — one from each firmware repo.

**Runtime, over the control plane:** offered rate, transport, destination address and port, duration,
payload mode, frame size, run label, start/stop/reset.

### 1.2 Control plane

UDP control socket (default port 3334), line-oriented ASCII commands, JSON replies. UDP rather than
serial: serial is shared with `idf.py monitor`, is slow, and stops working the moment a board is not
on the desk. A serial fallback covers the case where Wi-Fi association is itself what is broken.

```
hello                        → {"chip":"esp32c5","idf":"v6.0.2","fw_sha":"…","ingress":"synth","rung":"tuned3"}
cfg rate_bps=20000000 transport=udp dst=192.168.2.1:3333 payload=rand dur_s=60 label=c5_5g_s1_r2
                             → {"ok":true,"applied":{…}}
start                        → {"ok":true,"t0_us":…}
stat                         → {"bytes":…,"frames":…,"dropped":…,"idle_pct":[…],"retries":…,
                                "rssi":-42,"phy_rate":"…","heap":…}
stop                         → {"ok":true,"summary":{…}}
reset                        → reboots
```

Telemetry is also **pushed** at 1 Hz rather than only polled, so a run that dies is visible
immediately instead of at the end of a 60-second window.

### 1.3 Rate pacing

A token bucket in the source task, driven by `esp_timer`, so **offered load is a commanded value
accurate to ~1 %** rather than "as fast as it will go". This is what makes a swept loss-vs-load curve
possible at all, and it is the single most important firmware feature in the harness.

Report **both** offered and achieved source rate. If the source cannot reach the commanded rate, the
cell is source-limited and is labelled as such rather than looking like a radio ceiling.

### 1.4 Frame format

Unchanged from `CONTRACTS.md` §1 — 14-byte header (`magic`, `type`, `chip_id`, `seq`, `t_stm`,
`n_ch`) then payload — so `pc_receiver.py` and `emu_verify.py` keep working. `t_stm` carries the
source cycle counter latched at generation; it is the latency anchor.

### 1.5 Instrumentation

Per-core idle percentage (both cores on the S3 — this is the direct evidence behind plan §2.2), Wi-Fi
retry counters and negotiated PHY rate (which is also how the plan's §7.2b "is this actually 11ax?"
caveat gets answered), RSSI, free and minimum-ever heap, and buffer-pool starvation counts.

---

## 2. Host receiver — `host/receiver.py`

Extends `host_tools/pc_receiver.py` rather than replacing it.

- Per-second and whole-run goodput.
- Loss **and reordering** from `seq` gaps (UDP). Reordering matters separately: a reordered stream can
  look lossless and still break a downstream decoder.
- Latency p50/p99 and jitter from `t_stm` → arrival, with clock-offset estimation over the run.
- TCP retransmits from `tcp_info` (fallback: a `netstat -s` delta), so TCP rows have a meaningful
  failure metric.
- One JSON summary per run plus the raw per-second series. `cap.bin` capture **off by default during
  sweeps** so disk I/O cannot cap the number.

---

## 3. Orchestrator — `host/bench.py`

### 3.1 Input

A YAML matrix per stage, expanded into the run list:

```yaml
stage: 1
repeats: 3
sweep: {start_mbps: 1, stop_mbps: 60, steps: 8, hold_s: 60, discard_s: 3}
cells:
  - {chip: c5, band: 5,   source: synth, transport: [udp, tcp], tune: [baseline, tuned]}
  - {chip: c5, band: 2.4, source: synth, transport: [udp, tcp], tune: [baseline, tuned]}
  - {chip: s3, band: 2.4, source: synth, transport: [udp, tcp], tune: [baseline, tuned]}
```

### 3.2 Per-run sequence

1. Resolve the required binary from (chip, ingress, rung); build if the config hash changed; flash if
   the flashed hash differs from the required one. **Most runs flash nothing.**
2. `hello`; assert chip, IDF version and firmware SHA match what was requested.
3. Capture RF and host metadata (§3.5).
4. Run pre-gates (§6).
5. `cfg` + `start`; run the receiver; `stop`.
6. Merge ESP-side and host-side summaries; run post-gates; append one ledger record.

### 3.3 Build cache

Keyed on `sha256(target, ingress, sdkconfig fragment, source tree SHA)`. A hit skips the build
entirely. Builds and flashes go through `hostrun.sh` (§7).

### 3.4 Resume

The ledger is the state. On restart, `bench.py` reads `runs.jsonl`, computes which (cell, load,
repeat) triples are already present and valid, and runs only the remainder. An overnight sweep that
dies at 3 a.m. resumes with no bookkeeping.

### 3.5 Auto-captured metadata

ESP-side association report (RSSI, channel, width, negotiated PHY rate and mode — the last of which
settles plan §7.3b) and macOS `wdutil info` / `system_profiler SPAirPortDataType` for the ambient
scan; plus IDF version, firmware and repo git SHA (with dirty flag), hotspot band, TX power, power-save state, `rig_ceiling_mbps` (plan §7.3), timestamp, duration and repeat
index. Nothing is typed by a human.

### 3.6 Band-ordered scheduling

The iPhone hotspot is on one band at a time and the switch is a manual toggle (Settings → Personal
Hotspot → Maximize Compatibility: on = 2.4 GHz, off = 5 GHz). The orchestrator therefore:

- **Sorts the run list by band**, so a stage touches each band once.
- At a band boundary it **pauses, prints exactly which toggle to flip, and waits**, then verifies the
  band from the ESP's own association report before resuming. Two 30-second interruptions per stage
  instead of a step buried in every run.
- After any switch, the hotspot ceiling (§6) is re-measured, because it is band-specific.
- **Repeats of the same cell are not scheduled consecutively**, so phone thermal drift (plan §7.3c)
  shows up as spread rather than as a fake trend.

### 3.7 Association keepalive

Apple disconnects third-party hotspot clients after **90 seconds without traffic**. Builds, flashes,
metadata capture and gate checks can easily exceed that between runs. The orchestrator therefore runs
a **low-rate keepalive** (a few packets per second, far below any measurement floor) whenever no run
is active, and records any re-association as a metadata event on the next run rather than absorbing
it silently. Without this, an unattended sweep dies quietly in the small hours.

---

## 4. Ledger — `host/results/runs.jsonl`

Exactly the schema in `CONTRACTS.md` §4, one JSON object per run, append-only, plus the added
`rig_ceiling_mbps` and `phy_rate` fields. A record missing a required key is rejected at write time,
not discovered later. The ledger is the only source the report reads, which is what makes every
figure reproducible from raw data.

---

## 5. Report — `host/report.py`

Renders from the ledger alone:

1. The filled matrix, median ± spread per cell.
2. Loss-vs-offered-load curves with the knee marked, one panel per chip/band.
3. The decomposition table from plan §5.1 — band effect, SoC effect, ingress cost, SDIO advantage,
   and the ingress cost differential.
4. **The plot that answers the question:** achievable 128-channel sample rate versus required
   compression ratio, with the T0–T3 tier lines and each part's measured operating point drawn on it.
   One figure, paper-ready.
5. A banner showing `rig_ceiling_mbps` and the observed PHY mode on every figure, so no reader can
   mistake a hotspot-limited number for a silicon ceiling.
6. An auto-evaluated statement of which branch of plan §1.2 the data selects.

---

## 6. Validity gates

Enforced by the harness; a tripped gate marks the run invalid and re-queues it rather than letting it
poison a median.

**Pre-run:** hotspot is up on the expected band and the ESP is associated (verified from the ESP's own
report, not assumed); a current `rig_ceiling_mbps` exists for this band and exceeds the offered load being
requested; ambient scan within tolerance of the cell's first repeat; RSSI within 3 dB of the cell's
first repeat; no unexpected ESP reset since the last run; requested and reported IDF version match.

**Post-run:** achieved source rate within 5 % of commanded (else the cell is labelled source-limited);
no ESP reset during the run; heap never crossed its low-water mark; receiver saw a contiguous `seq`
space apart from counted losses; **goodput did not exceed `rig_ceiling_mbps`** (if it did, the
ceiling measurement is stale and both are re-run).

---

## 7. `_agent/hostrun.sh` v2

### 7.1 Required changes

The current runner knows exactly one project (`esp32c5-byte-pipe`), has no S3, STM32, iperf or bench
actions, and — critically — **`CONFIRM_FLASH=1` prompts for a typed "yes" on every flash**, which
stalls an unattended sweep at the first cell.

| Action | Parameters | Purpose |
|---|---|---|
| `esp_build` | `project`, `target`, `rung` | Build a named config for a named target |
| `esp_flash` | `project`, `target`, `port` | Flash it |
| `iperf` | `target`, `port`, `band`, `transport`, `secs` | Stage 0 |
| `bench` | `matrix`, `stage` | Run the orchestrator |
| `hotspot` | `band` | Verify the hotspot band and association (§3.6) |
| `stm_build` / `stm_flash` | `project`, `port` | Stage 2 generator |
| `power_cycle` | `hub`, `hub_port` | `uhubctl` (§7.2) |

Flash approval becomes **session-scoped**: approved once at the start of a sweep, valid for a bounded
window and a named project set, logged per flash. That preserves the runner's audit guarantee while
allowing unattended operation. The existing guarantees — no deletion, no paths from job files, no
shell metacharacters, bounded execution — are kept exactly as they are.

### 7.2 Power cycling

A `uhubctl`-compatible USB hub with per-port power switching (~$40) turns board recovery into a
scripted action. Without it, one wedged board ends an overnight sweep.

---

## 8. Loopback self-test — build this first

A host-side fake ESP implementing the §1.2 control protocol and streaming conforming frames over the
loopback interface at a commanded rate. It lets the **entire** harness — orchestrator, sweep logic,
receiver, metrics, ledger schema, validity gates, band scheduling, resume-after-crash, report
rendering — be exercised with zero hardware, in the cloud, before a single board is plugged in.

This is the highest-value item in the document. Without it, the first bench session is spent debugging
Python instead of measuring radios, and bench time is the scarce resource.

`make -C host test` must pass: fake-ESP round trip, ledger schema validation, gate logic (including
deliberately tripped gates), resume from a truncated ledger, band-boundary handling, and report
rendering from a fixture ledger.

---

## 8.1 Repository tree

```
hdemg-bench/
  firmware/
    bench_common/      pipe.c control.c pacing.c instr.c source_synth.c
                       sink_udp.c sink_tcp.c  include/{pipe,control,pacing,instr,hdemg_frame}.h
    bench_options.cmake   BENCH_INGRESS / BENCH_RUNG defaults, resolved in one place
    esp32s3/           CMakeLists.txt sdkconfig.defaults main/{app_main.c,pins_s3.h,source_qspi.c}
    esp32c5/           same, plus main/source_sdio.c
  host/
    bench/             frame control receiver fakeesp ledger gates rungs parity rfmeta
                       orchestrator drivers report palette
    cli/bench.py       sim | run | report | parity | effort
    rungs/             common-levers.yaml + s3/ c5/
    matrices/          stage0 stage1 stage2
    tools/apply_rung.py
    results/runs.jsonl
    tests/
  hostrun.sh           the constrained runner that builds and flashes on the Mac
  docs/                this file + the test plan
```

## 9. Build order

| Step | Deliverable | Hardware needed |
|---|---|---|
| 0 | Three repos initialised; parity checker + rung/ladder schema | None |
| 1 | Loopback fake ESP + receiver + ledger + gates + report, self-tested | None |
| 2 | `bench.py` orchestrator against the fake ESP, including resume and band scheduling | None |
| 3 | `hostrun.sh` v2 + session-scoped flash approval + `hotspot` action | Mac only |
| 4 | Hotspot setup, USB-C bridging check, ceiling measurement per band, keepalive | iPhone + Mac + any client |
| 5 | Stage 0 iperf automation | Both ESPs |
| 6 | `bench_fw` synth source + control plane, both targets | Both ESPs |
| 7 | Tuning rungs as `sdkconfig` fragments + build cache | Both ESPs |
| 8 | QSPI ingress (ESP side, then H745 master, then ready GPIO) | + H745 |
| 9 | SDIO ingress, C5 only | + H745 SDMMC |

Steps 1–3 need no hardware at all, so they can be built now, in parallel with the Ethernet adapter and
hotspot questions in plan §9.1.
