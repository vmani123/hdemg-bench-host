# Report 1 — How the test code is built, and what has been tested so far

*Written 2026-10-12, when picking the project back up. Plain-English summary of the
repository as it stands on `main` (commit `50aa0a1`) and of the first real measurement
session (2026-10-05). Sources are listed at the end, and numbered like [1], [2].*

*Updated 2026-10-13 with the overnight tuning study of 13 Oct (branch `tune-loop`, §5).
That study's code and fixes are on `tune-loop`, not yet on `main`; where this report
describes the code, it still describes `main` unless it says otherwise.*

---

## 1. The short version

- The project asks one question: **which chip, the ESP32-S3 or the ESP32-C5, can push more
  data over Wi-Fi** for the 128-channel muscle-signal (HD-EMG) board? [1]
- A lot of effort went into building a careful, automated measuring harness. It is
  well-tested against fake boards, and it has now produced two real sessions of data.
- **Latest numbers (13 Oct, 2.4 GHz, 20 MHz channel, tuned firmware, 0 % loss)** [10]:

  | Chip | TCP | UDP | UDP vs T2 (41.3 Mbit/s) |
  |---|---|---|---|
  | ESP32-S3 | **31.6 Mbit/s** | **44.6 Mbit/s** | passes, 8 % margin |
  | ESP32-C5 | **31.3 Mbit/s** | **54.0 Mbit/s** | passes, 31 % margin |

  Both chips clear the deciding tier T2 over UDP. Neither reaches T3 (61.4). TCP clears T1
  but not T2. These are tuning measurements (two 20 s runs per point), not yet the plan's
  qualifying 60 s runs, so treat the T2 pass as provisional (§5).
- **What changed since 5 Oct:**
    - A larger TCP send buffer, on both chips. The stock 5,760 B had capped TCP at 5–8 Mbit/s.
    - A fixed UDP sender.
    - On the S3: more Wi-Fi transmit buffers.
    - On the C5: lwIP core locking.
    - On both: moving network code into fast internal RAM.

  Espressif's own `iperf` firmware, run on the same link, gave S3 33.0 / 44.4 and C5
  28.8 / 48.4 Mbit/s TCP / UDP [11]. That is no better on UDP, and 4 % better only on S3 TCP.
- **The first session (5 Oct)** covered TCP at 2.4 GHz only: about **8 Mbit/s (C5)** and
  **5 Mbit/s (S3)**. Its UDP runs were thrown away because Cisco Secure Client on the Mac
  was silently dropping packets. Cisco has since been removed. It was briefly reinstalled
  and removed again between the two 13 Oct sessions. A UDP check before session 2 showed
  0 % loss and 0 host drops, and every 13 Oct UDP run passed the host-drop gate [13].
- **Memory is now the limit and the main risk.** Most remaining tuning settings ran the
  boards low on free memory, and in a 20 s run at the HD-EMG rate one board's free heap fell
  to 1.1 KB. A long soak test is needed before long recordings (§5, §6).
- **Not yet done at all:** 5 GHz, 40 MHz channels (this router's 2.4 GHz radio is set to
  20 MHz), a measured rig ceiling, the 30-minute hotspot probe, and the real wired-input
  stage (Stage 2, whose code is not on `main`).

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
| *On branch `tune-loop` only:* `host/bench/tune.py`, `host/tuning/`, `tune.sh` | The automated tuning study used on 13 Oct: tries one setting at a time on top of the current best and keeps it only if it gains (§5) [12] |
| *On branch `tune-loop` only:* `docs/TUNING_STUDY.md`, `docs/tuning-results/` | Run guide for the study, and a committed snapshot of its 13 Oct results (the live results folder is not tracked by git) [10][12] |

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

**Changed on `tune-loop` for the 13 Oct study** [12]:

- UDP datagrams carry whole frames up to 1,472 B (5 frames), so the network layer no
  longer splits them into fragments.
- A full Wi-Fi transmit queue now makes the sender wait and retry, instead of closing the
  socket and dropping the buffer. Overload shows up as a lower rate, not as fake loss.
- The paced source sleeps 1 ms when no frame is due, instead of spinning. The spin had
  starved the control channel and the idle counter on the single-core C5.
- Boards lock onto one access point by its BSSID (hardware address), and the link report
  names it. The C5 had kept joining a busier second radio on channel 6.
- Every run records the lowest free memory (heap low-water) since boot.
- Buffer-pool size, channel width (20/40 MHz), datagram size and the C5's Wi-Fi 6 MCS 8–9
  became build settings, so they can be tuned like any ESP-IDF setting.

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
| 0 | Stock Espressif `iperf`, to find the radio/rig ceiling | **run once as a side check on 13 Oct** (2 runs of 20 s per chip and transport, as a one-off, not through the Stage 0 matrix) [11] |
| 1 | Fake paced frames, swept over load, baseline vs tuned | **partly run:** TCP at 2.4 GHz on 5 Oct; TCP and UDP at 2.4 GHz as a tuning study on 13 Oct (one saturating load, 20 s runs, not the full sweep) |
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
| 2026-10-05 | Router rig added; **first real overnight sweep** on boards; Cisco UDP problem found and fixed in the gates; hotspot probe written (last commit on `main`, 14:06) |
| 2026-10-12 | Reports 1 and 2 written. Tuning-study code and the UDP sender fixes written on branch `tune-loop` |
| 2026-10-13, 02:26–06:21 PDT | Rig moved to House WiFi (UniFi router). **Tuning study** on both chips at 2.4 GHz, in two sessions. Espressif `iperf` run on both chips. At ~03:45, after session 1 and during a first latency attempt, both boards dropped off the network, most likely out of memory |
| 2026-10-13, 14:45 PDT | Latency test at the HD-EMG rate (4.32 Mbit/s) on both final designs |

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
The filter was turned off later that day. UDP was re-run cleanly on 13 Oct, on the fixed
UDP sender (next section).

**Why the S3 lost 70 runs.** The S3's *reported* signal strength wandered between -42 and
-58 dBm by itself while the C5 beside it held steady, and the 3 dB drift gate refused those
runs. An earlier cause, a harness bug that took the reference reading right after the
board booted, was fixed. An opt-in mode (`RSSI_DRIFT_WARN`) now keeps such runs with a
warning, but it was added after the overnight sweep and has not produced data yet [7].

### The tuning study: 2026-10-13, House WiFi, 2.4 GHz [10][11][13]

**Conditions.** This was a different network from 5 Oct: "House WiFi" on a UniFi router.

- The boards were locked to its 2.4 GHz radio: channel 1, 20 MHz wide. The router's 2.4 GHz
  radio is set to 20 MHz, so 40 MHz could not be tested.
- The S3 linked as 802.11n (HT). The C5 linked as **802.11ax (HE, Wi-Fi 6)**, its first
  run in Wi-Fi 6 mode.
- The Mac was on the 5 GHz radio (channel 48, 80 MHz), so the two hops never shared air.
- Signal in the session-2 throughput runs: S3 −57 to −60 dBm, C5 −63 to −67 dBm. In the
  14:45 latency runs it was −53/−54 and −56 to −59. The boards were not moved, and sat at
  different distances from the router.
- Both chips ran ESP-IDF v6.0-dev-2980.

**How it was measured.** This was not the full Stage 1 sweep. Each chip climbed a ladder
one setting at a time:

1. Build the current best plus one change, check that every setting reached the build,
   flash it.
2. Offer 60 Mbit/s, more than either chip can carry, so the delivered rate is the chip's
   maximum. Run TCP, then UDP: 2 runs of 20 s each, 17 s counted after a 3 s discard.
3. Keep the change if three things hold:
    - TCP or UDP gains at least 2 % **and** more than the two builds' combined repeat
      spread.
    - Neither transport drops by 2 % beyond that spread.
    - Free memory (heap low-water) stays at or above 20 KB.

   A change that would gain but breaks the memory floor is rejected as unsafe. A safe change
   may replace an unsafe best even if it is slower.

The S3 tried 12 changes and the C5 20, and both lists ran to the end. All 144 runs passed
the validity gates. Four C5 steps were set aside:

- Steps 2, 4 and 5 ran on the wrong radio. Step 2 was withdrawn by hand, and the
  access-point check that caught 4 and 5 was added after it.
- Step 18 asked for 40 MHz and was not granted it.

Session 1 ran 02:58–03:40 PDT and session 2
05:03–06:21 PDT. In between, the access-point lock and the 20 KB memory floor were added,
and each climb re-measured its current best before continuing. A first attempt at 02:26
was set aside because the S3's signal swung between −62 and −81 dBm.

**Final designs** (60 Mbit/s offered; median of 2 runs, range in brackets):

| | ESP32-S3, `t24-08-extra-iram` | ESP32-C5, `t24-16-extra-iram` |
|---|---|---|
| TCP | **31.6 Mbit/s** (28.1–35.1) | **31.3 Mbit/s** (31.0–31.7) |
| UDP | **44.6 Mbit/s** (43.4–45.8), 0 % loss | **54.0 Mbit/s** (53.5–54.4), 0 % loss |
| Espressif `iperf`, same link, TCP / UDP | 33.0 / 44.4 | 28.8 / 48.4 |
| UDP as a share of the link's raw rate | 62 % of 72.2 (HT20, MCS7) | 63 % of 86.0 (HE20, MCS7) |
| Lowest free memory at full load | 20.8 KB | 31.0 KB |

The raw rates are the top rates of these modes; 72.2 assumes the short guard interval.
Neither chip reports the rate it actually used.

The custom firmware matched Espressif's `iperf` on the S3 (96 % on TCP, 100 % on UDP) and
beat it by 9–11 % on the C5. So the firmware is no longer what limits the speed. One 20 MHz
stream rarely carries much more than 70–75 % of its raw rate as UDP (an estimate), so both
chips are close to what this link allows. The C5's UDP median (54.0) is 90 % of the
60 Mbit/s offered. That is right at the study's saturation check, so the offered load may
have held it back slightly.

**What moved the numbers** (kept steps only, each against the step before it):

| Chip | Change | Effect |
|---|---|---|
| S3 | TCP send buffer and window 5,760 → 65,535 B | TCP 3.9 → 20.3 Mbit/s (+416 %) |
| S3 | Transmit block-ack window 6 → 32 | TCP +3.4 % (a narrow keep) |
| S3 | Wi-Fi transmit buffers 32 → 64 | UDP +12 % |
| S3 | lwIP and FreeRTOS hot code in fast internal RAM (IRAM) | TCP +17 %, UDP +43 % (to 31.6 / 44.6) |
| C5 | TCP send buffer and window 5,760 → 65,535 B | TCP 4.5 → 19.0 Mbit/s (+322 %), UDP +17 % |
| C5 | Send buffer back to 41,760 B, buffer pool 6 → 4 | TCP −11 %, kept because free memory rose from 2.5 to 31 KB |
| C5 | lwIP core locking | UDP +8.8 % |
| C5 | More code in IRAM (lwIP extra, FreeRTOS) | TCP +5.4 % (to 31.3 / 54.0) |

Notable rejections:

- A 32-frame transmit block-ack window cost the C5 26 % of its UDP, though it helped the S3.
- Putting the S3's Wi-Fi and lwIP buffers in PSRAM freed memory but cost 13–15 %. That
  step changed several settings at once, so the cause is mixed.
- Turning on the C5's Wi-Fi 6 MCS 8–9 gained nothing. That faster coding likely needs a
  stronger signal than −63 to −67 dBm, but the rate actually used is not recorded.
- Thirteen steps broke the 20 KB memory floor (10 on the C5, 3 on the S3). Two of them
  would have gained 5–7 % of C5 UDP.

**Latency at the HD-EMG rate** (4.32 Mbit/s, 14:45 PDT, 2 runs of 20 s per chip and
transport):

- All 8 runs delivered every frame.
- Latency is relative: each frame's delay above the fastest frame in the same run. It was
  0.3–0.8 ms at the median and 14–21 ms at p99.
- The idle round trip, Mac to board and back over 50 requests, was 4.5 ms at best, 5.6 ms
  (C5) and 6.0 ms (S3) at the median, and 21–22 ms at worst.

**Against the tiers** (§2):

- **UDP:** both chips pass T2. The S3 clears it by 3.3 Mbit/s (8 %), the C5 by 12.7 (31 %).
  Neither reaches T3.
- **TCP:** both pass T1 and neither reaches T2.
- **The current design** has 7× (S3 TCP) to 12.5× (C5 UDP) headroom. Its rate here is
  4.32 Mbit/s including frame headers; §2's 4.1 counts samples only.

**Read before quoting these numbers:**

- **These are tuning runs, not qualifying runs.** They were 2 × 20 s at one load, not
  60 s runs over a sweep. Loss was 0 %, inside the plan's 0.1 %, but the 60 s requirement
  is not met yet. The T2 pass is therefore provisional.
- **Conditions move the same build a lot.** The same S3 build gave 27 % more TCP (19 % more
  UDP) at 05:05 than at 03:08. The C5 build gave 75 % more TCP (38 % more UDP). It was on
  the channel-1 radio both times, but in session 2 it was pinned there and had a few dB more
  signal. The S3's 8 % T2 margin is smaller than these swings, so compare numbers within
  one session.
- **Placement is part of the comparison.** In the final runs the S3's signal was 6–8 dB
  stronger than the C5's (−58 against −64 to −66 dBm).
- **Memory is the open risk.** In the latency runs the C5's lowest free memory fell from
  48 KB to 1.1 KB, during one TCP run with a brief stall. The S3 had already reached 5.1 KB
  at some point after its 05:30 flash. Both are far below the 21–31 KB seen in the climb.
  At ~03:45, after session 1, both boards dropped off the network, most likely out of
  memory.
- **No measured rig ceiling yet.** The 72 Mbit/s cap on offered load is the 20 MHz raw
  rate. The Mac's receive path sustained 150 Mbit/s in a self-test, so the Mac was not the
  limit.

**Compared with 5 Oct.** The router, channel and placement all changed, so this is not a
like-for-like comparison. The starting builds were the 5 Oct "tuned" rungs, still on stock
network settings. On the 13 Oct rig they gave 3.9 (S3) and 4.5 (C5) Mbit/s of TCP in
session 1, against 5.5 and 8.0 for the same rungs on 5 Oct. Tuning took both to about
31 Mbit/s; part of that rise is the better conditions of session 2.

---

## 6. Things to know before trusting or extending this code

These are findings from reading the code during this review:

1. **The rig ceilings are placeholders.** The `60` (2.4 GHz) and `95` (5 GHz) values passed
   to the runs were chosen provisionally; no `iperf` or known-good client has measured the
   real ceiling [7]. *Update 13 Oct:* the tuning study used `2.4=72`, the 20 MHz raw rate.
   Espressif's `iperf` gives a per-chip reference (§5), but a ceiling for the rig itself
   is still unmeasured.
2. **The UDP path in the firmware is probably pessimistic.** One datagram carries a whole
   pool buffer, up to 30 frames (8,100 bytes), so the network layer splits it into about
   six fragments; losing one fragment loses 30 frames. On a send error the sink also
   closes the socket and discards the buffer, and the firmware keeps no count of send
   failures, so Wi-Fi loss and local send failures look the same [4]. *Update 13 Oct:*
   fixed on `tune-loop` (1,472 B datagrams, wait-and-retry on a full queue, §4), and that
   fix produced the clean UDP numbers in §5. The board still keeps no count of send
   failures, and `main` still has the old code.
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
9. **Free memory is now the binding limit** (found 13 Oct). Faster settings cost internal
   RAM. The study's 20 KB floor is checked over four 20 s runs right after each flash, which
   underestimates rarer events: a brief TCP stall took the C5 to 1.1 KB at only 4.32 Mbit/s.
   The firmware treats a failed allocation as back-pressure. Still, the ~03:45 outage on
   13 Oct (both boards rejoined with 1.2 and 4.4 KB low-water marks) suggests that running
   out can take a board off the network [10][13].
10. **Conditions drift by tens of percent between sessions.** The same build measured
   19–75 % apart at 03:00 and 05:00 on 13 Oct. Re-measure the reference build at the
   start of each session (`tune.sh --remeasure` does this), and compare only within a
   session [10].

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

The 13 Oct tuning study, from a checkout of branch `tune-loop` (start `hostrun.sh` from
that same checkout first) [12]:

```bash
bash tune.sh 2.4 --sim                    # rehearsal against fake boards, a few minutes
CEILINGS=2.4=72 S3_PORT=cu.usbmodem5B8F0676581 C5_PORT=cu.usbserial-110 \
    bash tune.sh 2.4 --remeasure          # re-measure each chip's best first, then climb
cd host && python -m cli.bench tune --band 2.4 --status    # progress and current best
```

Port names change between sessions. Check them first, and never pass the Digilent FPGA
adapter (`cu.usbserial-210352BF9796*`) as a board.

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
10. Branch `tune-loop`, `docs/tuning-results/2026-10-13-house-wifi/` (snapshot commit
    `a684edf`): `README.md`; `max-tput/summary.md` (per-step decisions); `max-tput.jsonl`
    (all 144 runs); `latency/latency.jsonl` and `latency/rtt.json`. The final-design
    medians and ranges in §5 were re-checked against the raw run records for this update.
11. Same folder, `iperf/esp32s3.json` and `iperf/esp32c5.json`: Espressif's `iperf` example
    on each chip, same access point, 05:24–05:33 PDT. The figures are the Mac's receive
    rate; `iperf`'s own UDP "loss" counts datagrams its stack refused, not air loss.
12. Branch `tune-loop` (commits `4315846` to `adeeac8`): the firmware fixes, the tuning
    engine `host/bench/tune.py`, the plan `host/tuning/max-tput.yaml`, and the run guide
    `docs/TUNING_STUDY.md`.
13. The full write-up of the study: Claude Doc *ESP32-S3 vs ESP32-C5 Max-Throughput Tuning
    Study* (13 Oct 2026),
    https://claude.ai/code/artifact/90a3d1da-8f7f-40f7-824a-8ec4b47ef9fb — per-step tables,
    CPU and memory analysis, latency breakdown, incident log.
