# Report 3 — The follow-up campaign (17–18 Oct): the tests that worked

*Written 2026-10-19. Plain English, in the style of Reports 1 and 2. It covers the tests that
ran to the end with usable numbers. Tests that failed or were cut off are listed briefly in
§9, so the gaps are visible. All figures come from the campaign's own records, copied into
`docs/campaign-results/2026-10-17-followup/` [1]–[4]. Where a statement is my reading of the
data rather than a measurement, it says so.*

---

## 1. The short version

- **UDP works on both chips, with or without the wired input.** At the HD-EMG rate
  (4.32 Mbit/s) and at T1 (20.5), both chips delivered the full rate. That held whether the
  data came from the ESP's own generator or from the STM32 over QSPI.
- **One setup carried T2 (41.3 Mbit/s) in full: the C5 on 5 GHz with a 40 MHz channel.** It
  delivered 41.3 Mbit/s with 0 % loss from its own generator, and 50.8 Mbit/s of UDP at full
  load (§6, §8). On 2.4 GHz at 20 MHz the best over QSPI were the C5 at 39.2 and the S3 at
  37.3.
- **The S3 is the steadier chip over 30 minutes.** In UDP at 4.32, the S3 lost 12 frames in
  30 min and never dropped off Wi-Fi. The C5 lost 3.0 % and disconnected 19 times (reason 34).
- **QSPI from the STM32 now works on both chips at 25 MHz**, once the STM32's output edges
  were slowed. The link check passed about 383,000 frames each with no errors. It costs the
  S3 about 2.3 Mbit/s at 41.3 and the C5 almost nothing.
- **40 MHz at 2.4 GHz does not help.** The S3 result was a tie that a later sweep did not
  repeat. The C5 was far worse: 9.9 Mbit/s against 42.4 at 20 MHz.
- **PSRAM made every variant slower**, so none was kept.
- **Nagle on or off made no difference.** Larger UDP datagrams helped the C5 (24.7 against
  16.4 Mbit/s) but not the S3.
- **TCP is the open problem.** Only one long TCP run held up: the S3 at full load (27.1 Mbit/s
  for 30 min). Every other long TCP run stopped delivering within 10–90 s, for two different
  reasons (§9):
  - The C5's runs were broken by its Wi-Fi drops together with a bug in the Mac's receiver.
  - The S3 stopped answering at all.

  Separately, Stage 2 TCP at 20.5 Mbit/s stalled in every run except the S3's own-generator
  runs. That one is not explained yet.

---

## 2. How to read the numbers

- **Mbit/s** means data delivered to the Mac (goodput). Each figure is the median of 2–3 runs
  of 20–60 s, unless it says 30 min. **±** is the spread between runs.
- **Offered loads**:
  - 4.32 is the HD-EMG data rate.
  - 20.5 and 41.3 are tiers T1 and T2 of the test plan.
  - **Full load** means offering 60 or 120 Mbit/s, more than either chip can carry. The
    result is then that chip's ceiling.
- **Rig**:
  - Boards on `House WiFi`: channel 1 at 2.4 GHz, channel 48 at 5 GHz.
  - The Mac is on the other band, so its own hop does not share the boards' channel.
  - Each chip runs its tuned design from 13 Oct (`c-s3-base`, `c-c5-base`) unless the test
    changes it.
- **The rig drifts.** The same reference test gave quite different results at three times
  of day. Compare numbers within one test (the same hour), not across tests run hours apart.

  | Reference, full load, TCP / UDP | 0.3: 17 Oct, 04:00 | 3.0: 17 Oct, 08:40 | 6.0: 18 Oct, 02:45 |
  |---|---|---|---|
  | S3 | 30.3 / 38.1 | 21.9 / 27.2 | 31.3 / 39.6 |
  | C5 | 16.8 / 38.1 | 10.5 / 29.4 | 17.4 / 39.2 |

  All three are below the 13 Oct tuning study: S3 31.6 / 44.6, C5 31.3 / 54.0 [7]. The C5's
  TCP is about half its 13 Oct figure. 13 Oct used short tuning runs, so this comparison is
  only indicative.

---

## 3. Rig, boot and wiring checks

| Test | Result |
|---|---|
| W.1 wiring hello world | All 18 Stage 2 wires worked in both directions: no opens, swaps or shorts. |
| 0.2 boot check | Both ESPs boot normally and can be flashed with the harness attached. |
| 0.1 rig check | Boards and Mac on the expected bands. In round 2, the 40 MHz builds connected as `bw40 sta40`, so the router's 2.4 GHz radio really was at 40 MHz. |
| 0.4 Mac on 2.4 GHz | The Mac's hop for the 5 GHz tests: channel 1, 20 MHz, 802.11ax, 103 Mbit/s link rate, −66 dBm. |
| 6.1 STM32 | Both cores flashed and in sync. Generator pacing was within 0.003 % at 4.32, 20.5 and 41.3. |

---

## 4. TCP and UDP settings (1.1, 1.3)

Full load, 2.4 GHz, 3 × 20 s.

| | S3 | C5 |
|---|---|---|
| 1.1 TCP, Nagle off (current setting) | 17.5 ±9.7 | 16.8 ±1.6 |
| 1.1 TCP, Nagle on | 16.5 ±10.1 | 19.0 ±6.2 |
| 1.3 UDP, 1,350 B datagrams (current setting) | 22.8 ±0.6 | 24.7 ±7.3 |
| 1.3 UDP, 540 B datagrams | 23.0 ±1.5 | 16.4 ±1.3 |

- **Nagle:** no difference beyond the spread on either chip. It stays off.
- **Datagram size:** the C5 loses about a third of its throughput with small datagrams. The S3
  does not care. 1,350 B stays.
- **Absolute levels:** all four figures are below the reference. Signal was weaker in this
  hour: −66 to −69 dBm, against −62 to −64 dBm at 0.3.
- **Lost runs:** 1.3 lost its first runs because the boards did not answer. It ran straight
  after 1.2, which locked both boards up (§9). Three good runs remained for each chip.

---

## 5. PSRAM (2.1–2.8)

**The rule:** keep a variant only if it frees more than 4 KB of internal memory without
losing more than 2 % of throughput. None qualified, so both chips keep their designs. That
left nothing for 2.8 to check.

| Variant | Internal memory gained | TCP | UDP |
|---|---|---|---|
| **C5 design (0.3)** | — | 16.8 | 38.1 |
| C5 P0: PSRAM on, nothing moved into it | −14.0 KB | 13.6 | 23.1 |
| C5 P1: buffer pool in PSRAM | +28.1 KB | 10.4 | 22.2 |
| C5 P2: Wi-Fi and lwIP buffers in PSRAM | +71.1 KB | 11.3 | 12.8 |
| C5 P3: P2 plus A-MSDU | +71.3 KB | 12.0 | 13.7 |
| **S3 design (0.3)** | — | 30.3 | 38.1 |
| S3 P1: buffer pool in PSRAM | +42.3 KB | 18.0 | 26.5 |
| S3 P2: Wi-Fi and lwIP buffers in PSRAM | +144.1 KB | 12.5 | 18.7 |
| S3 P3: P2 plus A-MSDU | +144.1 KB | 10.2 | 16.9 |

- **The C5 pays just for switching PSRAM on.** P0 moves nothing into PSRAM, yet it cost 40 %
  of the C5's UDP and also used more internal memory.
- **A-MSDU (P3) recovered nothing** on either chip.
- **Caveat: the reference drifted.** The rule compares against 0.3, run 3–4 hours earlier. By
  3.0, 40 minutes after the last variant, the S3's own reference had fallen to 21.9 / 27.2
  (§2). Measured against that, S3 P1 (18.0 / 26.5) is closer than the table suggests. P2 and P3
  are clearly slower against either reference.

---

## 6. Channel width and band

### 40 MHz at 2.4 GHz (4.1–4.3)

Full load, 3 × 60 s. Both 40 MHz builds connected at 40 MHz (`bw40 sta40`).

| TCP / UDP | 20 MHz | 40 MHz |
|---|---|---|
| S3 (4.1) | 29.8 / 37.6 | 30.6 / 42.0 ±10.2 |
| C5 (4.2): Wi-Fi 6 at 20 MHz, 802.11n at 40 MHz | 24.8 / 42.4 | 7.5 / 9.9 |

The 4.3 UDP sweep of each chip's 40 MHz build ran from 10 to 120 Mbit/s offered:

- **S3:** it levels off at 24–27 Mbit/s from 25.7 offered upwards, with 0 % loss. The 42.0 seen in
  4.1 did not come back 20 minutes later.
- **C5:** it delivered 8–10 Mbit/s at every load, including 8.5 when offered only 10. Its median
  latency was 57–72 ms.

**Verdict:** 40 MHz at 2.4 GHz is not worth using. For the S3 the rule calls it a tie
(within the spread), so the design stays. For the C5 it is much worse.

### The C5 at 5 GHz (5.2–5.6)

The Mac is on the 2.4 GHz network (0.4). Full load, 3 × 60 s. Signal was −69 to −74 dBm,
weaker than at 2.4 GHz (−62 to −68).

| Build | TCP | UDP |
|---|---|---|
| 5.2 Wi-Fi 6, 20 MHz | 22.6 | 18.6 |
| **5.3 802.11n, 40 MHz** | 27.0 | **50.8** |
| 5.4 Wi-Fi 6, 20 MHz, MCS 8–9 allowed | 20.0 | 20.6 |
| 5.6 5.4, with the Mac also on 5 GHz (shared channel) | 18.0 | 18.9 |

- **Sweep (5.5) of the 5.4 build:** it carries 10 Mbit/s in full, then levels off at 16.5–18.9
  from 25.7 offered upwards, with 0 % loss.
- **Width:** 40 MHz at 5 GHz gave 2.7 times the UDP of 20 MHz. That is more than the doubled
  width alone would explain (my reading); the reason is not known.
- **Sharing the channel** with the Mac cost 1–2 Mbit/s, within the spread.
- **Outcome:** the rule makes the C5's best setup 802.11n at 40 MHz on 5 GHz (50.8). That beats
  its 2.4 GHz design (42.4 in 4.2). The S3 stays on its 2.4 GHz design (37.6 in 4.1). Stage 2
  uses these setups.

---

## 7. Long runs (3.1, 3.3, 3.4, 3.6, 3.7)

Each chip's design, at 2.4 GHz.

### 30-minute soaks that held up

| 30 min | S3 | C5 |
|---|---|---|
| 3.1 UDP at 4.32 | 4.32 Mbit/s; 12 frames lost (0.0003 %); longest gap 0.17 s; no Wi-Fi drops | 4.19 Mbit/s; 3.0 % lost; longest gap 3.7 s; 19 Wi-Fi disconnects |
| 3.3 UDP at 41.3 | 31.3 Mbit/s; 0.12 % lost; longest gap 0.5 s; 1 power-on reset | 37.7 Mbit/s; 8.7 % lost; longest gap 15 s; 22 Wi-Fi disconnects |
| 3.4 TCP at full load (41.3 offered) | 27.1 Mbit/s, steady for all 30 min, no loss | stopped after 90 s (§9) |
| Lowest free internal memory | 3.1: 9.0 KB · 3.3: 85.3 KB · 3.4: 0.6 KB | 3.1: 17.8 KB · 3.3: 8.0 KB |

- **The C5 drops off Wi-Fi about every 1.5 minutes.** The reason it logs is mostly 802.11
  reason 34: too many frames went unacknowledged, which usually means a poor channel.
  - At 4.32 the C5 was unreachable for 69 s in total.
  - That is about the 3 % of data it lost.
- **At 41.3, neither chip carried the full rate for 30 minutes** at 2.4 GHz and 20 MHz. The S3
  delivered 31.3 cleanly. The C5 delivered 37.7 with gaps.
- **The S3 is near its memory limit.**
  - It ran 30 minutes of full-load TCP with its free memory down to 576 B, and kept going.
  - Its 3.3 run needed three tries. The first two ended with the board not answering, even
    after a power cycle.

### Link-drop stress (3.6), UDP at 4.32

There were six forced Wi-Fi drops, of 1, 5, 30, 1, 5 and 30 s.

| | S3 | C5 |
|---|---|---|
| Delivered | 4.11 Mbit/s | 4.00 Mbit/s |
| Lost | 4.8 % | 7.4 % |
| Came back after every drop | yes | yes |
| Longest gap | 32 s | 34 s |
| Wi-Fi disconnects, including the 6 forced ones | 6 | 22 |

- **The S3 lost little beyond the drops themselves.** The forced drops add up to 72 s, or 4.0 %
  of the run (my arithmetic). The C5's extra loss matches its own 16 disconnects.
- **One missed command:** the C5 did not answer one drop command (at 22.5 min), and recovered
  anyway.

### Start/stop leak check (3.7)

Each board did 500 start/stop cycles.

| | S3 | C5 |
|---|---|---|
| Cycles done | 500 | 500 |
| Stopped answering at cycle | 200, 370, 400, 492 | 6, 28, 200, 322, 334 |
| Resets by the harness | 4 | 4 |
| Free memory, first → last reading | 189.9 → 174.2 KB | 127.1 → 119.8 KB |

- **Both boards sometimes lock up when a run starts or stops.** Each time they came back: the
  harness reset them through the serial port, and one C5 lock-up cleared by itself.
- **This test does not settle the leak question.** Each board was reset four times between the
  first and last readings, so the difference does not measure a leak. A run without lock-ups is
  needed for that.

---

## 8. Stage 2: data from the STM32 over QSPI (6.2–6.5)

**Setup:**
- The STM32H745 generates counter-stamped 270-byte frames at the commanded rate.
- It sends them over QSPI at 25 MHz.
- The ESP checks every frame and forwards it over Wi-Fi.
- The STM32's output edges are set to their slowest speed (`slew=0`). With fast edges, the
  jumper wires rang and frames arrived with wrong lengths. Slowing the edges removed every
  error.
- The S3 uses its plain 2.4 GHz design.
- The C5 used its plain 2.4 GHz design in round 1, before 40 MHz had been tested. In round 2
  it used 802.11n at 40 MHz on 5 GHz; only the generator runs finished in that round.

### Link check (6.2): 20 s at 41.3 Mbit/s, without Wi-Fi

| Link | Result |
|---|---|
| S3 QSPI | pass: 382,784 frames, none dropped, no errors |
| C5 QSPI | pass: 382,823 frames, none dropped, no errors |
| C5 SDIO | fail (§9) |

### Built-in generator against QSPI input

Median of 3 × 20 s. "Generator" means the ESP's own generator; "QSPI" means data from the STM32.

**S3, 2.4 GHz, 20 MHz** (6.3, round 2). Round 1 showed the same pattern, 1.8–3.5 Mbit/s
lower at 41.3.

| Offered | UDP, generator | UDP, QSPI | TCP, generator | TCP, QSPI |
|---|---|---|---|---|
| 4.32 | 4.32 | 4.32 | 4.32 | 4.32 |
| 20.5 | 20.5 | 20.5 | 20.5 | stalls (§9) |
| 41.3 | 39.6 | 37.3 | 32.2 | 29.7 |

**C5, 2.4 GHz, Wi-Fi 6 at 20 MHz** (6.4, round 1)

| Offered | UDP, generator | UDP, QSPI | TCP, generator | TCP, QSPI |
|---|---|---|---|---|
| 4.32 | 4.32 | 4.32 | 4.32 | 4.32 |
| 20.5 | 20.5 | 20.5 | stalls (§9) | stalls (§9) |
| 41.3 | 39.4 | 39.2 | 25.1 | 20.7 |

**C5, 5 GHz, 802.11n at 40 MHz** (6.4, round 2). Only the generator runs finished; the QSPI
runs were not reached.

| Offered | UDP, generator | TCP, generator |
|---|---|---|
| 4.32 | 4.32 | 4.32 |
| 20.5 | 20.5 | stalls (§9) |
| 41.3 | **41.3, 0 % loss** | 23.2 |

**Batch size (6.5, round 1, 2.4 GHz):** the C5 over QSPI in 15-frame batches (the SDIO packet
limit) delivered 39.3 Mbit/s of UDP at 41.3. That is the same as with 30-frame batches (39.2).

**Latency at the HD-EMG rate** (UDP at 4.32, one way, relative to the fastest frame), median /
99th percentile:

| S3, generator | S3, QSPI | C5, generator (2.4 GHz) | C5, QSPI (2.4 GHz) | C5, generator (5 GHz, 40 MHz) |
|---|---|---|---|---|
| 0.7 / 18.7 ms | 0.8 / 30.6 ms | 0.3 / 20.0 ms | 0.8 / 48.2 ms | 1.5 / 27.5 ms |

What this shows:

- **The wired input costs nothing at the rates the board needs.** At 4.32 and 20.5 over UDP,
  QSPI delivered the full rate on both chips.
- **At 41.3, QSPI costs the S3 2.3 Mbit/s and the C5 0.2** (UDP, my subtraction). The C5 over
  QSPI (39.2) beat the S3 (37.3) on 2.4 GHz.
  - The shortfall at 41.3 is real loss: the STM32 had to drop 8 % (C5) to 20 % (S3) of its frames
    because the ESP could not take them fast enough.
  - The ESP found no damaged frames: no length, header or content errors. The sequence gaps it
    saw are the frames the STM32 dropped.
- **The C5 at 5 GHz with 40 MHz** is the first setup to carry T2 completely, from its own
  generator. Whether it holds with QSPI input is the unfinished part of 6.4.
- **The 12–13 % "loss" the QSPI runs show at 4.32 is a counting fault, not real loss.** The Mac
  received the full 4.32 Mbit/s, and the ESP saw every frame in sequence.
  - Cause: the first frames the Mac receives are a few left over from the previous run, which
    was TCP at full load. Their sequence numbers are about 6,000 lower, so the Mac counts the
    gap as lost.
  - The evidence: in one S3 run the Mac's first frame was number 2,200,776, while the STM32 and
    the ESP both started that run at 2,207,293. At 20.5, which follows a clean run, the first
    numbers match and the loss is 0.

---

## 9. What did not work, in brief

| Test | What happened |
|---|---|
| 1.2: TCP with 540 B writes | Both boards stopped answering, even after a power cycle. There is no result. |
| 3.2: TCP soak at 4.32, 30 min | Neither chip delivered after the first 10 s. Marked failed. **C5:** 19 Wi-Fi drops, then the receiver fault (a) below. **S3:** no Wi-Fi drops, but it stopped answering for 1,514 s of the 1,800, with free memory down to 652 B (fault b). It came back for about 5 min after a reconnect at 19 min, then stopped again. |
| 3.4: the C5's TCP soak at full load | About 27 Mbit/s for 90 s, then nothing for the remaining 28.5 min. The first Wi-Fi drop came at about 90 s, and the receiver fault (a) followed. The summary's "1.3 Mbit/s" is the 30-minute average. The S3, which had no Wi-Fi drops, was fine (§7). |
| 3.5: TCP stress at 4.32 | TCP stopped before the first fault was injected. **C5:** after 50 s; its counters show the same pattern as fault (a). **S3:** after 30 s; it stopped answering for 1,549 s of the run, with free memory down to 164 B (fault b). So the run says nothing about recovery from injected faults. Each chip came back briefly later: the S3 after a reconnect at 19.5 min, the C5 after the receiver restart at 22.5 min. The firewall-block fault was skipped, because it needs passwordless `sudo`. |
| Stage 2 TCP at 20.5 (6.3, 6.4) | Delivery stops early in the run; in the run examined in detail, within the first second. Results by arm:<br>• C5 from its generator: nothing in all 6 runs.<br>• C5 over QSPI: nothing in 4 of 6 runs; the other two gave 14.9 and 6.2.<br>• S3 over QSPI: 0.8 Mbit/s or less in all 12 runs.<br>• S3 from its generator: worked in all 6 runs.<br>Several runs left the board not answering, so it had to be reset. TCP at 4.32 and 41.3 worked in every short run (fault c). |
| C5 SDIO (6.2) | The card now answers identification and accepts data. That needed working around two bugs in ST's HAL driver, now in `link_sdio.c`. The link check still failed: at 25 MHz, 99.6 % of frames were dropped with 100,388 link errors, and at 4 MHz nothing crossed. Reads fail their CRC checks. The likely cause is that CMD and DAT0–3 have no external pull-up resistors. Espressif's driver says its internal pull-ups are not enough [8]. |
| 6.4 and 6.5, round 2 | Cut off when the campaign was stopped on 18 Oct. 6.4 had finished only its generator runs. |

**Why the TCP runs failed.** The ESP's 1 Hz counters in the long runs show three different
faults:

- **(a) C5, long runs: a fault in the test harness, set off by the C5's Wi-Fi drops.**
  1. When the C5 drops off Wi-Fi, its TCP connection dies. Evidently no reset reaches the Mac,
     because the receiver logged no connection error.
  2. The C5 reconnects. But the Mac's receiver (`host/bench/receiver.py`, `_run_tcp`) only
     takes a new connection after the old one reports an error, so it keeps waiting on the dead
     one.
  3. In 3.4 the C5 made 4 new connections, which matches the receiver's queue of 4 waiting
     connections (`listen(4)`). Every attempt after that failed; the run counted 407 failed
     attempts in all.

  These runs therefore measured the harness, not the C5's TCP. The fix is for the receiver to
  switch to a new connection as soon as one arrives. The C5 would still lose data at each Wi-Fi
  drop, but it should resume.
- **(b) S3 at 4.32 over 30 minutes: the board stops answering.**
  - In 3.2 and 3.5 the S3 had no Wi-Fi drops, yet was unreachable for most of the run, with
    free memory down to 164–652 B.
  - Over QSPI at 20.5 the S3 shows the same symptom; it had to be reset.
  - Running out of memory is the likely cause (my reading).
- **(c) Stage 2 TCP at 20.5: not explained yet.**
  - At 5 GHz the C5 kept answering, yet it delivered about 250 KB and then nothing; its free
    memory fell to 1.4 KB.
  - The short runs do not record the ESP's counters over time, so this needs a dedicated test.

**Measurement problems found along the way:**
- **The TCP receiver fault (a)** above.
- **Longest gap is wrong for TCP.** The long-run "longest gap" figure reports 0.09 s for the
  C5's 3.4 run, which had a 28-minute gap.
- **Leftover frames.** The 4.32 loss figure in Stage 2 is inflated by frames left over from the
  previous run (§8).

---

## 10. What this means for S3 against C5, so far

My reading of the results above:

- **At the HD-EMG rate over UDP, both chips work, wired or not.** The S3 is more reliable over
  time. The C5's disconnects, about one every 1.5 minutes, would cost about 3 % of the data
  until they are fixed.
- **For headroom at T2 (41.3), the C5 on 5 GHz at 40 MHz is the only setup that has carried it
  in full.** So far that is only in short runs from its own generator.
- **TCP cannot be relied on yet on either chip**, except the S3 at full load. The C5's long
  TCP failures are partly the harness's fault (§9, a), but its Wi-Fi drops are real. The S3's
  are a real board problem (b).
- **The deciding tests still to run** are the C5 at 5 GHz and 40 MHz over QSPI (the rest of
  6.4), followed by a 30-minute soak in that setup.

---

## 11. Fixes made during the campaign (all pushed to `origin/main`)

| Commit | What |
|---|---|
| `6866ca5` | The Mac's network names, spelled as the router spells them (`House wifi 2g` / `House wifi 5g`). |
| `60614bf` | Best-setup rule: falls back to the 0.3 reference when the width tests have not run. |
| `ae572ff` | Validity gates: long runs are judged by the host's share of the loss, not a fixed count. A test whose score delivered almost nothing now fails. |
| `1d5cfdf` | Stage 2 links. A setting for the STM32 edge speed (`slew`), with slow edges fixing QSPI at 25 MHz. SDIO identification and the data path, working around ST HAL bugs in our own code, so regenerating the HAL cannot undo them. |
| `0a8437f` | Campaign: QSPI at 25 MHz with slow edges by default. A board that stops answering is reset through its serial port. The leak check carries on after a lock-up. |

---

## 12. Next steps

1. **Fix the TCP receiver (§9, a).** Have it take a new connection as soon as one arrives.
   Then repeat the C5's TCP runs:
   `bash campaign.sh --redo 3.2,3.4,3.5 --only 3.2,3.4,3.5`.
2. **Find the Stage 2 stall at 20.5 and the S3 lock-up (§9, b and c).** A 15-minute run of each
   chip's generator over TCP at 10–30 Mbit/s, with the ESP's counters recorded, should show
   where it breaks and why.
3. **SDIO:** fit about 10 kΩ pull-ups on CMD and DAT0–3, then repeat 6.2.
4. **Finish 6.4 and 6.5** with the C5 at 5 GHz and 40 MHz:
   `bash campaign.sh --only 6.4,6.5`. Until item 2 is fixed, 6.4 will be marked failed on the
   20.5 TCP point.
5. **Look into the C5's Wi-Fi disconnects** (reason 34, about one every 1.5 min at 2.4 GHz).
6. **Other harness fixes:**
   - Discard frames left over from the previous run.
   - Correct the TCP longest-gap figure.
   - Record ESP counters in short Stage 2 runs.

---

## Sources

1. `docs/campaign-results/2026-10-17-followup/followup.jsonl`: the ledger, one line per run,
   including the ESP and STM32 counters for Stage 2 runs.
2. `docs/campaign-results/2026-10-17-followup/state.json`: each test's status and result, and
   the derived choices (kept PSRAM, best setups, link settings).
3. `docs/campaign-results/2026-10-17-followup/summary.md`: the same, as `bench campaign --status`
   writes it.
4. `docs/campaign-results/2026-10-17-followup/matrices/`: exactly what each test measured.
5. Per-run time series (10 s receiver windows and 1 Hz device samples) are under
   `host/results/campaign/followup/ts/`. They are not committed (15 MB). The stall timings in
   §9 come from them.
6. `docs/CAMPAIGN.md`: the test list and the rules for later tests.
7. `docs/REPORT_2_ESP32_WiFi_Tuning.md`: the 13 Oct tuning study.
8. ESP-IDF SDIO slave driver, `driver/sdio_slave.h`: the comment on
   `SDIO_SLAVE_FLAG_INTERNAL_PULLUP` says the internal pull-ups are not sufficient and external
   ones are needed.
