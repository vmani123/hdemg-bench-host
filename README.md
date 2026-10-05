# hdemg-bench

**ESP32-S3 vs ESP32-C5 wireless throughput comparison** for the 128-channel HD-EMG
acquisition board — firmware for both radios, and the host harness that measures them.

Design documents live in [`docs/`](docs/): the test plan says what is being measured and
why; the harness spec says what this repo implements.

---

## Start here — it runs with no hardware

```bash
pip3 install -r host/requirements-dev.txt
make test      # 45 tests, ~2 min: the whole harness against loopback fake ESPs
make sim       # a full simulated sweep + rendered report in host/results/sim-report/
```

Nothing is attached; nothing needs to be. That is the point — bench time is the scarce
resource, so the harness is debugged before a board is ever plugged in.

---

## Layout

```
firmware/
  bench_common/     THE measurement core — one copy, compiled by both targets
  esp32s3/          S3: entry point, pin map, QSPI ingress
  esp32c5/          C5: entry point, pin map, QSPI + SDIO ingress
host/
  bench/            frame · control · receiver · fakeesp · ledger · gates
                    rungs · parity · rfmeta · orchestrator · drivers · report
  cli/bench.py      sim | run | report | parity | effort
  rungs/            common-levers.yaml + per-chip ladders (s3/, c5/)
  matrices/         stage0 (iperf) · stage1 (synth, swept) · stage2 (real ingress)
  results/          runs.jsonl — the append-only ledger
hostrun.sh          constrained runner: executes builds and flashes on the Mac
docs/               test plan · harness spec
```

---

## The two things this repo's structure is for

### 1. One measurement core, not two kept in sync

`firmware/bench_common/` fixes **what the number means**: the single byte-counting point
(`pipe_note_sent()` in `pipe.c`, called once by the sink task and nowhere else), the paced
source accuracy, the latency stamp instant, the wire format and the control protocol.
Both targets compile that same directory.

This matters because the comparison rests on it. Two copies of this code, however
carefully reviewed, can drift — and a drift here would look exactly like a silicon
difference while nothing in the data revealed it. One copy makes that impossible by
construction.

`make parity` guards the way that guarantee still breaks in a monorepo: a target that
stops pointing at the shared component, or a file copied into `firmware/<target>/main/`
to "just tweak it for the S3".

### 2. Each chip tuned independently — that divergence *is* the experiment

This is a **best-effort comparison**, not a matched-configuration one: each chip is tuned
as hard as it will go, including with levers the other chip does not have (the S3's
dual-core task split, the C5's 5 GHz band and SDIO ingress). The question is *which part
delivers more in a product*.

So `sdkconfig` is deliberately **not** checked for parity, and `firmware/*/sdkconfig.defaults`
carries environment only — nothing that affects throughput. Every tuning lever lives in
`host/rungs/<chip>/` and is applied at build time as a second fragment.

Because tuning effort then becomes an experimental variable,
`host/rungs/common-levers.yaml` is an equal-effort checklist and `make effort` fails while
any lever is unattempted on a chip that has it. Unequal rung *counts* are legitimate;
unequal effort is not, and the ladders are the only available answer to "did you try as
hard on the S3?"

Both readings come out at no extra cost: `baseline` is a common stock configuration on
both chips (the matched-silicon comparison), `tuned` is each chip's own best (the board
decision). The gap between them says how much of each part's performance is free.

---

## Commands

```bash
make test | sim | report | parity | effort | fw-s3 | fw-c5

cd host
python3 -m cli.bench sim    --matrix matrices/stage1.yaml --hold 1.0    # no hardware
python3 -m cli.bench run    --matrix matrices/stage1.yaml --ceilings 5=95,2.4=60
python3 -m cli.bench report
```

`run` refuses to start if the parity gate fails, and refuses any run for which no rig
ceiling has been measured on that band — a number above the rig's own ceiling is a
property of the rig, not the silicon.

---

## The rig: which access point

The matrix's `ap:` names the rig, and the harness behaves accordingly.

- **`ap: verizon-router` (current bench).** The boards join a dual-band home router; the
  network name and password live in `firmware/<target>/sdkconfig.local` (untracked). The
  router offers both bands under one SSID, so **the band of each cell is pinned in the
  firmware build** (`BENCH_BAND`, passed by the orchestrator through `hostrun.sh`) and
  verified from the device. The Mac is the second hop and must not share the first hop's
  air: keep it on the router's **other** band, or wire it. `discover` shows which band
  the Mac is on, every ledger record stores it (`rf.mac_link`, `rf.host_path`), and a cell
  on the Mac's own band is **refused by the pre-run gate** (`--allow-shared-band` turns
  that into a warning). In practice: Mac on 5 GHz → run the 2.4 GHz block; for the 5 GHz
  block, wire the Mac to the router.
- **`ap: iphone-hotspot`.** The original plan: the Mac is wired to the phone over USB and
  the band is the hotspot's Maximize Compatibility toggle. Everything below about the
  hotspot applies to this rig only.

- **`ap: iphone-hotspot-wifi`.** The phone's hotspot with the Mac on the hotspot's own
  Wi-Fi. Both hops share one channel, so every frame crosses the same air twice and the
  numbers are roughly half of what a wired second hop would show; the matrix accepts
  that with `allow_shared_band: true` and every run carries the warning. Used by the
  short probe `hotspot_probe.sh` (`matrices/hotspot-probe.yaml`).

A matrix can also name the network it must run on with `ssid:`. That is **enforced**:
nothing is built, flashed or measured unless this Mac is associated to that network and
each board's `sdkconfig.local` is configured for it (a board can only ever join the one
network it was built for). The staged matrices name the router; the probe names the
hotspot.

Numbers taken on different rigs are not comparable with each other; the access point is
recorded in every run (`rf.ap`).

## Before the first real measurement

1. **iPhone hotspot with the Mac wired to it over USB-C** (or the router rig above). If
   both the ESP and the Mac join over Wi-Fi on one band, every frame crosses the air
   twice on one channel and the whole matrix is quietly deflated.
2. **Measure the rig ceiling per band** with a known-good client and pass it to
   `--ceilings`. It is the roof over every ESP number.
3. **Start `hostrun.sh`** in its own Terminal and approve the flash window once.
4. **Band switching is manual** — Maximize Compatibility ON is 2.4 GHz, OFF is 5 GHz. The
   orchestrator sorts runs by band so this happens at most twice per stage, and verifies
   the band from the device rather than taking your word for it.

## Running a sweep unattended

The hotspot serves one band at a time and the toggle is manual, so **one band is the unit
that runs unattended**. Two blocks with one toggle between them, rather than six blocking
prompts scattered through the night.

Once per band, before anything else — measure the rig ceiling with a known-good client
(a second laptop or tablet running `iperf3` to the Mac over the hotspot). Every run is
refused without it, because a number above the rig's own ceiling is a property of the rig.

**Block 1 — 2.4 GHz** (Maximize Compatibility ON), both boards flashed and associated:

```bash
cd host
python3 -m cli.bench run --matrix matrices/stage1.yaml     --band 2.4 --unattended --ceilings 2.4=<measured>,5=<measured>     --s3-hub-port 2 --c5-hub-port 3        # omit if you have no switchable hub
```

Go to bed. In the morning, flip Maximize Compatibility **OFF** (5 GHz) and run block 2
with `--band 5`. The S3 has no cells in that block — it is 2.4-only.

```bash
python3 -m cli.bench report
```

Three things keep it alive overnight:

- **Keepalive.** Apple drops third-party hotspot clients after 90 s without traffic, and
  the gaps between runs exceed that. A low-rate control-plane poll holds the association,
  pauses during every measurement so it never appears in its own number, and records a
  re-association on the next run's ledger record rather than absorbing it silently.
- **`--unattended`** never blocks on a prompt. Set the band before starting; the pre-run
  gate verifies it *from the device*, so a wrong toggle fails loudly instead of quietly
  producing numbers for the other band.
- **Auto power-cycle.** A board that stops answering gets one power cycle through a
  `uhubctl` hub, then the point is recorded invalid and the sweep moves on. Resume retries
  it later. One wedged board costs one measurement, not the night.

Scale: Stage 1 is 288 runs at 60 s hold — about 5.5 hours, roughly 12 flashes.

## Status

Stage 1 (paced synthetic source) is implemented and the host harness is fully tested.
`source_qspi.c` and `source_sdio.c` fail the build on purpose — read their header comments
before writing them; each names the specific thing that silently corrupts data if skipped.

**The firmware has never been compiled** — no ESP-IDF in the environment it was written
in. CI builds both targets on the first push; expect to fix a few includes.
