# Report 1 — How the test code is built, and what has been tested so far

*Written 2026-10-12, when picking the project back up. Plain-English summary of the
repository as it stands on `main` (commit `50aa0a1`) and of the one real measurement session
so far (2026-10-05). Sources are listed at the end, and numbered like [1], [2].*

---

## 1. The short version

- The project asks one question: **which chip, the ESP32-S3 or the ESP32-C5, can push more
  data over Wi-Fi** for the 128-channel muscle-signal (HD-EMG) board? [1]
- A lot of effort went into building a careful, automated measuring harness. It is
  well-tested against fake boards, and it has produced one real session of data.
- That session covered **TCP at 2.4 GHz only**. Both chips topped out at about
  **8 Mbit/s (C5)** and **5 Mbit/s (S3)**. That is far below what Espressif publishes for the
  same chips (see Report 2).
- **UDP was measured but thrown away**, because antivirus-style software on the Mac
  (Cisco Secure Client) was silently dropping packets. It was never re-run.
- **Not yet done at all:** 5 GHz, Espressif's own `iperf` as a reference, a measured ceiling
  for the test setup, the 30-minute hotspot probe, and the real wired-input stage (Stage 2,
  whose code is not on `main`).

---

## 2. What is being measured, and what counts as "good enough"

The board produces one *frame* per sample period: a 14-byte header plus 128 channels of
16-bit samples (256 bytes) = **270 bytes per frame** [2][3]. How fast the Wi-Fi must carry
frames depends on the sample rate:

| Tier | Needed data rate | What it covers |
|---|---|---|
| T0 | 5.0 Mbit/s | The current design: 128 channels at 2,000 samples/s (4.1 Mbit/s) plus margin |
| T1 | 20.5 Mbit/s | 10,000 samples/s uncompressed |
| **T2** | **41.3 Mbit/s** | **30,000 samples/s with the best compression measured (1.485x). This is the deciding tier.** |
| T3 | 61.4 Mbit/s | 30,000 samples/s uncompressed |

These come from the test plan [2]. A run only counts if it lasts 60 s and loses fewer than
0.1 % of frames. Compression is **not** done on the ESP; it happens upstream (FPGA or
STM32), so the ESP is just a pipe [2].

The plan's decision rule: if both chips pass T2, throughput does not decide and other
factors (5 GHz robustness, ingress fit, cost) pick the winner; if only one passes, that one
wins; if neither passes, escalate to a better access point or a different radio module [2].

---

## 3. How the test is wired together

```
   ┌──────────────────────┐  Wi-Fi   ┌────────┐  Wi-Fi/wire  ┌─────────────────────┐
   │ ESP32 (S3 or C5)     │ ───────► │ Router │ ───────────► │ Mac running the     │
   │ makes fake frames    │          │        │              │ harness + receiver  │
   │ at a commanded rate  │ ◄─────── │        │ ◄─────────── │ (also sends control │
   └──────────────────────┘ control  └────────┘   commands   │  commands)          │
                                                             └─────────────────────┘
```

1. The Mac tells the board over a small control channel what to do: *send TCP (or UDP),
   at X Mbit/s, for 60 s*.
2. The board's **synthetic source** generates numbered, time-stamped frames at exactly that
   rate (random payload bytes) and hands them to a **sink** that pushes them into a TCP or
   UDP socket [4].
3. The Mac's **receiver** parses frames out of the stream, counts the data, looks for
   missing or out-of-order frame numbers, and measures delay.
4. Every run is written as one line into a permanent log, the **ledger**
   (`host/results/runs.jsonl`), together with the conditions it was taken under (signal
   strength, band, channel, software version, git commit).
5. A **report generator** reads only that ledger and draws the tables and charts.

### The two ideas that shape the repo layout [1][5]

**One measuring core, two chips.** The code that decides *what the number means* — where
bytes are counted, how the pacing works, the frame format, the control protocol — exists
**once**, in `firmware/bench_common/`, and both chips compile that same folder. This makes
it impossible for the S3 and C5 to be accidentally measured two slightly different ways.
`make parity` checks that nobody has copied a file out of it.

**Each chip is tuned on its own.** This is a "best each chip can do" comparison, not a
"same settings on both" comparison. So tuning settings live on the Mac side in
*ladders* (`host/rungs/s3/`, `host/rungs/c5/`), applied at build time, and the firmware's
default config holds nothing that affects speed. A checklist (`common-levers.yaml`) keeps
the effort fair: it fails until every tuning idea has been tried, or explicitly ruled out,
on each chip.

### Folder map

| Path | What is in it |
|---|---|
| `firmware/bench_common/` | The shared measuring core: `source_synth.c` (fake frames), `pacing.c`, `pipe.c` (buffer pool + the single byte-counting point), `sink_tcp.c`, `sink_udp.c`, `control.c`, `instr.c` (CPU idle, heap) |
| `firmware/esp32s3/`, `firmware/esp32c5/` | Per-chip entry point (`app_main.c`), pin maps, and the wired-input code for Stage 2 (`source_qspi.c`, `source_sdio.c`) |
| `host/bench/` | The Python harness: `orchestrator.py` (runs a sweep), `receiver.py`, `control.py`, `gates.py` (validity checks), `keepalive.py`, `ledger.py`, `rfmeta.py` (records Wi-Fi conditions), `rungs.py`, `report.py`, `fakeesp.py` (a fake board for testing) |
| `host/matrices/` | Test recipes: `stage0.yaml`, `stage1.yaml`, `stage2.yaml`, `hotspot-probe.yaml` |
| `host/rungs/` | Tuning ladders per chip and the shared checklist |
| `host/results/` | The ledger, overnight logs, and rendered reports |
| `host/tests/` | 17 test files; the README says 45 tests run against fake boards with no hardware (I did not re-run them for this report) |
| `hostrun.sh` | The only path allowed to build and flash boards; it asks you to type `yes` before any flash |
| `overnight.sh`, `hotspot_probe.sh` | Unattended sweep scripts |
| `docs/` | Test plan v2, harness spec, overnight runbook |

---

## 4. How one measurement is made, in detail

### On the board [4]

- A source task builds frames and packs **whole frames** into an 8 KB buffer taken from a
  pool of 6. Up to 30 frames fit in one buffer (8,100 bytes).
- A sink task takes finished buffers and calls `send()` (TCP) or `sendto()` (UDP) once per
  buffer, then adds the byte count. That single place is the **only** place bytes are
  counted, on both chips.
- Pacing is scheduled against the run's start time, not the previous frame, so small
  delays cannot pile up into a wrong rate.
- The board also reports **CPU idle %** and how many frames it could not generate. This
  tells a "the CPU was the limit" result apart from a "the radio was the limit" result.
- TCP uses `TCP_NODELAY` (no waiting to merge small writes). Power save is switched off at
  run time in both `app_main.c` files.

### On the Mac [4]

- **Goodput** is counted when a whole frame has been parsed out of the stream.
- **Loss** comes from gaps in the frame numbers; reordering is counted separately.
- **Latency** is relative (the board's clock and the Mac's clock are unrelated), measured
  against the fastest frame in the run.
- Before and after every UDP run, the harness reads the Mac's own "dropped due to full
  socket buffers" counter, so it can tell if the Mac, not the Wi-Fi, lost the packets.

### The sweep and the "knee" [4]

A *cell* is one combination of chip, band, transport (TCP/UDP) and tuning level. For each
cell the harness steps the offered load upward (4, 12, 20 ... 60 Mbit/s in Stage 1), 3
repeats each, 60 s per run. The **knee** is the highest offered load whose *median* loss
across repeats stays under 0.1 %; the reported figure is the median goodput there, with
the spread (median absolute deviation). Repeats of a cell are deliberately not run back
to back, so slow drift shows up as spread instead of a fake trend.

### Validity gates [6]

A run is marked invalid, and not averaged into anything, when for example:

- the board is not on the expected band, or the Mac shares the board's band (every frame
  would cross the same air twice);
- no rig ceiling has been measured for that band, or goodput exceeds it;
- signal strength moved more than 3 dB from the cell's first run;
- the board reset, or ran low on memory, during the run;
- the Mac itself dropped more than 50 UDP datagrams (the Cisco problem below);
- the software version or the wire-format check does not match.

Some conditions are only warnings: a run is "source-limited" if the board could not make
frames as fast as commanded, or if frames arrived out of order.

### Stages [2]

| Stage | What | Status |
|---|---|---|
| 0 | Stock Espressif `iperf`, to find the radio/rig ceiling | **never run** |
| 1 | Fake paced frames, swept over load, baseline vs tuned | **partly run (TCP, 2.4 GHz)** |
| 2 | Real wired input (QSPI on both chips, SDIO on C5) | **never run.** On `main` the ESP input files (`source_qspi.c`, `source_sdio.c`) are still stubs marked "NOT YET IMPLEMENTED". The STM32H745 signal generator and the wired-input code were written on a separate branch, `worktree-stm32-stage2`, which has not been merged into `main` |

### Robustness features for unattended nights [5]

- A **keepalive** that polls the board between runs (iPhone hotspots drop quiet clients
  after 90 s).
- **Band ordering**: runs are grouped by band, because the band is pinned into each
  firmware build.
- Automatic **power-cycle** of a stuck board through a USB hub, then move on.
- **Resume**: rerunning the same command picks up where the ledger stops.

---

## 5. What has actually been run

### Timeline (from the git history and the ledger)

| Date | What happened |
|---|---|
| 2026-09-18 | First commit: harness, firmware, docs |
| 2026-09-29 | Firmware fixed to build under ESP-IDF v6; full fake-board sweep rendered (`sim-report/`) |
| 2026-10-05 | Router rig added; **first real overnight sweep** on boards; Cisco UDP problem found and fixed in the gates; hotspot probe written (last commit, 14:06) |
| After 10-05 | Nothing run |

### The real session: 2026-10-05, Verizon router, 2.4 GHz [7]

Conditions: boards on channel 6, 20 MHz wide, 802.11n ("HT") — **not** Wi-Fi 6. Signal
about -43 dBm. The Mac was on the router's 5 GHz band (channel 104), as required so it does
not share the boards' air. Both chips ran ESP-IDF v6.0-dev.

The ledger holds 170 run records plus one "void" marker. Of the 170: **79 usable** (all TCP),
**70 refused by the signal-drift gate** (all S3), and **21 UDP runs voided** afterwards.

**TCP results** (steady level across offered loads of 12–60 Mbit/s, 60 s runs):

| Cell | Median goodput | Range | Valid runs | CPU idle |
|---|---|---|---|---|
| C5, baseline | 7.65 Mbit/s | 6.95–8.35 | 24 of 24 | ~76 % |
| C5, tuned (IRAM) | 8.02 Mbit/s | 6.98–8.50 | 24 of 24 | ~77 % |
| S3, baseline | 5.17 Mbit/s | 3.19–5.59 | 24 of 43 | ~58 % |
| S3, tuned (core split) | 5.49 Mbit/s | 5.33–5.59 | 7 of 58 | — |

Reading it:

- Goodput stays flat from 12 to 60 Mbit/s offered, so this is a **ceiling**, not a gradual
  knee. The generated report prints a "knee" of 60, which is misleading; the real
  behaviour is a plateau [7].
- At 4 Mbit/s offered, both chips deliver all 4 Mbit/s with no loss.
- Loss was 0 % on TCP (TCP retries hidden losses), so the cost shows up as lost speed.
- Tuning gains are small: about +5 % (C5) and +6 % (S3, on very few valid runs).
- The CPU was mostly idle, so the chip's processor was not the limit.
- Against the tiers: both chips pass T0 and neither is near T1.

**UDP results (voided).** All 21 UDP runs showed 2–46 % loss. The Mac's Cisco Secure Client
network filter was making the Mac's kernel discard packets (about 570 per second were
dropped while the harness's own queue was empty). The runs were marked void, and the gate
now refuses UDP runs when the Mac drops more than 50 datagrams [8]. For reference only,
the voided numbers were roughly C5 ≈ 37 and S3 ≈ 27.5 Mbit/s delivered at the top loads.
The filter was turned off later that day, but **UDP has not been re-run**.

**Why the S3 lost 70 runs.** The S3's *reported* signal strength wandered between -42 and
-58 dBm by itself while the C5 beside it held steady, and the 3 dB drift gate refused those
runs. An earlier cause, a harness bug that took the reference reading right after the
board booted, was fixed. An opt-in mode (`RSSI_DRIFT_WARN`) now keeps such runs with a
warning, but it was added after the overnight sweep and has not produced data yet [7].

---

## 6. Things to know before trusting or extending this code

These are findings from reading the code during this review:

1. **The rig ceilings are placeholders.** The `60` (2.4 GHz) and `95` (5 GHz) values passed
   to the runs were chosen provisionally; no `iperf` or known-good client has measured the
   real ceiling [7].
2. **The UDP path in the firmware is probably pessimistic.** One datagram carries a whole
   pool buffer, up to 30 frames (8,100 bytes), so the network layer splits it into about
   six fragments; losing one fragment loses 30 frames. On a send error the sink also
   closes the socket and discards the buffer, and the firmware keeps no count of send
   failures, so Wi-Fi loss and local send failures look the same [4].
3. **TCP writes are batched, not 256 bytes.** At higher loads one `send()` carries up to
   8,100 bytes. (Some notes elsewhere assume 256-byte writes; that is the payload size per
   frame, not the write size.)
4. **Latency numbers are relative,** not absolute one-way delay.
5. **No retry counters** (TCP retransmits, Wi-Fi retries) are recorded, even though the
   spec lists them. This makes it hard to say *why* a plateau happens.
6. **Documentation is partly stale.** The README says the firmware "has never been
   compiled" (it builds and has run). The commit message for `c49c0e7` says "NOT
   DEPLOYED" (it is part of `main`, and the hotspot probe depends on it).
7. **The simulated report's ceilings are not predictions.** `sim-report/` shows numbers
   from a fake board and says so in the code [4].
8. The hotspot probe was designed to answer the two most useful open questions quickly
   (does a bigger TCP send buffer help, and what does clean UDP look like), but it shares
   one channel between boards and Mac, so its numbers will be about half of router-rig
   numbers and cannot be compared with them. The board credentials it needs were restored
   from an older copy and are unverified. A previous session also noted that the hotspot
   password appears in git history through `sdkconfig.local.example`, so consider
   changing it.

---

## 7. How to re-run things

```bash
make test                # all harness tests against fake boards, no hardware
make sim                 # a full simulated sweep + report
bash hostrun.sh          # (own Terminal) the runner that builds/flashes; type `yes` once
cd host
python3 -m cli.bench discover --matrix matrices/stage1.yaml
python3 -m cli.bench run --matrix matrices/stage1.yaml --band 2.4 --unattended \
        --ceilings 2.4=<measured>,5=<measured> --transport udp
python3 -m cli.bench report
bash ../hotspot_probe.sh     # the 30-minute probe (needs hostrun.sh running)
```

Note: `overnight.sh` refuses to run anywhere except the Verizon network named in the
matrix.

---

## Sources

1. `README.md` — project overview, rig description, status. In this repo.
2. `docs/S3_vs_C5_Test_Plan_v2.md` — decision tiers (§1), stages, single-core discussion
   (§2). In this repo.
3. `firmware/bench_common/include/hdemg_frame.h` — wire format (14-byte header, 270-byte
   frame).
4. Firmware and harness source read for this report: `firmware/bench_common/{pipe,
   source_synth,pacing,sink_tcp,sink_udp,control}.c`; `host/bench/{receiver,orchestrator,
   control,rfmeta}.py`.
5. `docs/BENCH_HARNESS_SPEC.md` — design rationale: shared core, ladders, keepalive,
   scheduling.
6. `host/bench/gates.py` — validity gate rules (pre-run and post-run).
7. `host/results/runs.jsonl`, `host/results/report/report.md`, and
   `host/results/overnight-20261005-*.log` — the measured data. Plateau statistics in §5
   were recomputed from the ledger with the void list applied.
8. Git commit `df6182a` ("Refuse UDP runs in which the host itself dropped datagrams") —
   the diagnosis of the Cisco filter, including the measured drop rate.
9. Prior Claude Code session transcripts for this project (2026-10-05), used for the
   history of what was tried and what was left unfinished (not available in the repo).
