# Report 4 — TCP on the Verizon router (19 Oct): the stall zone and what fixes it

*Written 2026-10-19, in the style of Reports 1–3. Every figure comes from this run's own records,
copied into `docs/campaign-results/2026-10-19-verizon-tcp/` [1]–[4]. Where a statement is my
reading of the data rather than a measurement, it says so.*

---

## 1. The short version

- **The C5's TCP has a stall zone, and in these tests a one-line setting removed it.**
  - **On default,** the on-board generator hands TCP a small send every millisecond. With
    that, the C5 stalled in 14 of 20 runs between 10 and 25 Mbit/s, and locked up every
    time. It went silent and needed a reset through its serial port.
  - **With `batch=30`,** which hands TCP whole 8,100 B buffers, it stalled in 0 of 20 runs
    and delivered every load in full (§3).
  - At 30 Mbit/s and at full load, both settings were fine.
  - This is Report 3's unexplained Stage 2 stall at 20.5 (§9 c), reproduced with no wiring.
- **It holds for long runs.**

  | C5, TCP | default | `batch=30` |
  |---|---|---|
  | 15 Mbit/s, 15 min | 0.44 Mbit/s, locked up 6 times | **15.00 Mbit/s**, longest gap 0.17 s |
  | 4.32 Mbit/s (HD-EMG rate), 30 min | **19-minute stall** after 3.5 min | **175 of 180** windows at full rate, longest gap 2.3 s |

  The 4.32 Mbit/s default run *looks* clean in the ledger (4.32 Mbit/s, 0.001 % loss) only
  because the generator caught up after the stall. A real acquisition would have lost those
  19 minutes (§5).
- **At full load the C5's TCP is steady:** 31.2 Mbit/s for 20 minutes on one connection,
  longest gap 1.2 s (§6). This is the run Report 3's 3.4 could not complete.
- **Write size at full load does not matter.** 1,350, 2,700 and 8,100 B writes gave 26.9–28.8
  Mbit/s, with no lock-ups (§7). So small writes alone are not the trigger. The once-a-
  millisecond spacing of small sends seems to be (§3, my reading).
- **On default, the C5 survives injected faults but not its own stalls.** Under stress
  (read pauses up to 30 s, a receiver reset, a 10 s Wi-Fi drop) it recovered from each fault,
  but stalled 7 times in 30 minutes on its own (§7).
- **No Wi-Fi drops on this router.** The C5 never disconnected by itself in over 2 hours of
  long runs. On House WiFi it dropped about every 1.5 minutes (§6).
- **The S3 could not be tested.** It browns out at boot: its supply sags, which is not
  firmware. It needs a person to re-plug or re-power it. **There is no S3-vs-C5 comparison
  in this report** (§2).
- **New in the harness** (§8, §9): the Mac's TCP receiver now follows a reconnecting board
  (Report 3's fault a). Dead boards go through a recovery ladder (reset, port re-detection,
  reflash) instead of costing the test. Long runs have a mid-run watchdog. The ladder
  revived the C5 28 times this morning with no person involved.

---

## 2. What was run, and what went wrong with the rig

- **Rig.**
  - The `Verizon_SLD967` router, one network name for both bands. The boards were on its
    2.4 GHz radio: channel 6, 20 MHz.
  - The Mac stayed on the 5 GHz radio (channel 104, 80 MHz), so the two hops never shared air.
  - Cisco Secure Client was on. It drops inbound UDP, so **everything here is TCP**.
  - Only the boards' USB cables were connected; nothing was wired to the STM32.
- **Plan.**
  - V.0–V.7, as approved on 19 Oct, plus one extra (V.2b, §4). The extra used the time the
    S3 left free.
  - Nothing repeats a test that already has a result in Report 3.
  - Each chip runs its 13 Oct design (`c-s3-base`, `c-c5-base`).
- **The C5 joined as 802.11n** (`HT ch6 bw20`), not Wi-Fi 6. This router evidently does not
  offer 802.11ax at 2.4 GHz; the same happened on 5 Oct. Signal was −39 to −47 dBm.
- **The S3 could not run at all.** It resets about every 0.3 s during boot: 53 resets in 15 s.
  - The reset reasons are `RTC_SW_SYS_RST` with the saved PC in
    `rtc_brownout_isr_handler`, plus `POWERON`. So the brownout detector fires: the board's
    supply voltage sags as the app starts.
  - **It is not the firmware.** The unchanged firmware from `main` does exactly the same.
  - **It is not the USB port name.** The board is on its usual port (a WCH "USB Single
    Serial" bridge, 138 mA required of 500 mA available).
  - My reading: a power problem at the board (cable, connector or regulator), which nothing
    on the host side can fix. It needs a person.
  - The recovery ladder (§8) tried a reset and a full rebuild and reflash, then set the S3
    aside. Every later test checked for it again.
  - So **every result below is the C5's alone.** There is no S3 comparison in this report.
- **New in this run** (details in §9):
  - a runtime setting `batch=N`;
  - a fix to the Mac's TCP receiver (Report 3's fault a);
  - the recovery ladder;
  - a mid-run watchdog.

---

## 3. The stall zone (V.1a–c): where the C5's TCP breaks, and what fixes it

Each chip's design, TCP, 20 s runs at 10, 15, 20.5, 25 and 30 Mbit/s. Each point was
measured with today's behaviour (default) and with `batch=30`. The order of loads and settings
was shuffled in every round, so an effect of the previous run cannot pass for an effect of the
load. There were three rounds spread over the morning.

**What `batch=30` does.** The on-board generator normally hands TCP whatever frames fell due in
the last millisecond. That is one `send()` per millisecond, of 4 frames (1,080 B) at 10 Mbit/s
or 10 frames (2,700 B) at 20.5. With `batch=30` it waits until 30 frames (8,100 B, one pool
buffer) are due, then sends them in one call. The average rate is the same; only the size of
each send changes. At full load the sends are already 8,100 B, which is why full-load TCP never
showed this problem.

**Result, C5, 4 runs per point** (2 in round 1, 1 each in rounds 2 and 3):

| Offered (Mbit/s) | default: median delivered | default: runs that stalled | `batch=30`: median delivered | `batch=30`: runs that stalled |
|---|---|---|---|---|
| 10 | 3.6 | 3 of 4 | **10.0** | 0 of 4 |
| 15 | 0.6 | 4 of 4 | **15.0** | 0 of 4 |
| 20.5 | 0.0 | 4 of 4 | **20.5** | 0 of 4 |
| 25 | 9.4 | 3 of 4 | **25.0** | 0 of 4 |
| 30 | 29.7 | 0 of 4 | 29.6 | 0 of 4 |

"Stalled" means delivered under half the offered rate in a 20 s run.

- **Default: 14 of 20 runs in the zone stalled, and every one of the 14 left the C5 locked
  up.** It stopped answering the control plane, and only a reset through its serial port
  brought it back.
- **`batch=30`: 0 of 20 stalled.** Every load from 10 to 25 Mbit/s was delivered in full.
  - At 30 Mbit/s both settings deliver about 29.7, which is the C5's TCP ceiling on this router.
  - If the runs are treated as independent, 14 of 20 against 0 of 20 is very unlikely to be
    chance (Fisher's exact test, two-sided p ≈ 3 × 10⁻⁶; my arithmetic). They all come from
    one board on one morning.
- **A stall is fast.**
  - A stalled run delivered for 0–10 s, then nothing.
  - Within seconds the board stopped answering status requests: it missed 22–32 of the 33
    one-per-second samples.
  - The lowest free internal memory it reported, before it went quiet, was **2.7–7 KB**.
  - Runs with `batch=30` kept **16–28 KB** free (median about 25 KB) at the same loads.
- **It is not certain to happen.**
  - On default, 10 and 25 Mbit/s each got through once in four tries.
  - 15 and 20.5 never did.
  - Round 3 stalled less (2 of 5) than rounds 1–2 (12 of 15). This could be time of day on
    the channel, or chance; four repeats cannot tell.
- **It reproduces Report 3's unexplained Stage 2 stall at 20.5** (§9 c) on its own generator,
  with no wiring. It is the same symptom: delivery stops early, the free memory falls to a
  kilobyte or two, and the board goes silent.

**Why: not settled (my reading).**
- **What the data shows.** The stall comes with internal memory running out. Batching sends
  into whole 8,100 B buffers prevents it.
- **The obvious explanation is only half right.** The obvious explanation is that small sends
  mean small segments, each holding its own lwIP and Wi-Fi buffers, so the 41,760 B send
  buffer costs far more memory than its size. But V.7 (§7) splits full-load traffic into
  1,350 B writes, smaller than most of the stall-zone sends, and the C5 neither stalls nor
  locks up.
- **So segment size alone is not the trigger.** The difference is timing. In the stall zone,
  one small send goes out every millisecond, with the sink idle in between. At full load the
  same small writes go out back to back, and the Wi-Fi driver can aggregate them.
- **A possible mechanism** is that many separately transmitted small frames, each waiting for
  its own acknowledgement, pin more transmit buffers and memory than the same bytes sent in
  bursts. That would explain both results, but these tests do not prove it.
- **Settling it** would take the C5's Wi-Fi buffer counters, or a run at a fixed send interval
  with varied send sizes (§10).
- **Practical conclusion, whatever the cause:** do not hand the C5's TCP stack a small send
  every millisecond. Hand it whole buffers.

**What batching costs.**
- **Delay:** a frame can wait until 30 are due before it is sent. That is at most 6.5 ms at
  10 Mbit/s and 15 ms at the HD-EMG rate of 4.32.
- **Not visible in the latency figures:** this wait does not appear in the measured latency,
  because the generator stamps each frame when it writes the batch.
- **Measured latency** (relative to the fastest frame) on the `batch=30` runs: median 1.9–4.8 ms
  and 99th percentile 27–44 ms at 10–25 Mbit/s. On default at 30 Mbit/s it was 9.4 / 42.5 ms.

---

## 4. Fifteen minutes in the stall zone (V.2, and the extra V.2b)

**V.2: the C5 on default at 15 Mbit/s for 15 minutes**

The load was picked by the rule written before the run: the zone load where the C5 delivered
the smallest share in round 1. That was 15, tied with 20.5 at zero.

| | V.2 (default) |
|---|---|
| Delivered, 15-min average | 0.44 Mbit/s |
| 10 s windows at ≥ 90 % of the offered rate | 0 of 90 |
| Watchdog revivals (reset, stream restarted) | 6 |
| How long it delivered after each start | about 7 s at the start, then 0–8 s after each revival |
| Status requests answered | 26 of 913 |
| Lowest free internal memory seen | 2.6 KB |
| Wi-Fi disconnects | 0 |

- **On default, the C5 cannot hold 15 Mbit/s of TCP at all.** It locks up within seconds,
  every time, with no Wi-Fi drop involved.
- **The receiver fix worked.** It followed all 7 of the C5's connections. In Report 3 the
  receiver would have stayed on the first dead one.

**V.2b (extra): the same 15 Mbit/s for 15 minutes with `batch=30`**

This was added on the night, in the 15-minute slot the S3's half of V.2 would have used. It
asks whether batching holds for 15 minutes, not just 20 s.

| C5, TCP at 15 Mbit/s, 15 min | V.2: default | V.2b: `batch=30` |
|---|---|---|
| Delivered | 0.44 Mbit/s | **15.00 Mbit/s** |
| 10 s windows at ≥ 90 % of the rate | 0 of 90 | **90 of 90** |
| Longest gap | 128 s | 0.17 s |
| TCP connections / watchdog revivals | 7 / 6 | 1 / 0 |
| Lowest free internal memory | 2.6 KB | 19.4 KB |
| Frames lost | — | 0 |
| Latency, median / 99th percentile | — | 3.5 / 50 ms |

- **With `batch=30` the C5 carried the load it could not hold at all on default,** without a
  single gap longer than 0.17 s.

---

## 5. The HD-EMG rate for 30 minutes (V.3, V.4)

TCP at 4.32 Mbit/s for 30 minutes, the C5 alone. The S3's half could not run.

| C5, TCP at 4.32 Mbit/s, 30 min | V.3: default | V.4: `batch=30` |
|---|---|---|
| 10 s windows at ≥ 90 % of the rate | 64 of 180 | **175 of 180** |
| Longest gap | **1,151 s** (19 min) | 2.3 s |
| TCP connections | 2 | 1 |
| Watchdog actions | 12 (all ineffective, see below) | 0 |
| Lowest free internal memory | 5.1 KB | 23.0 KB |
| Status requests answered | 637 of 1,803 | 1,792 of 1,806 |
| Wi-Fi disconnects | 0 | 0 |
| Frames lost (ledger) | 46 of 3.6 M | 0 |

- **V.3 stalled 3.5 minutes in and stayed stalled for 19 minutes.**
  - At about 1,370 s, its send finally failed, it opened a new connection, and it pushed the
    backlog through at about 30 Mbit/s for 3 minutes. After that it ran at 4.32 again.
  - The ledger's 4.32 Mbit/s average and 0.001 % loss are misleading. When the pipe is
    full, the on-board generator stops making frames rather than numbering them as lost,
    then catches up.
  - A real acquisition front end cannot pause. With a real ADC this would have been a
    19-minute outage.
  - The 10 s windows and the longest gap are the figures to trust here.
- **The watchdog did not rescue V.3, and that was a harness fault, now fixed.**
  - During the stall the C5 still answered the short `hello` but not `stat`. My reading:
    too little memory to build the 1.4 KB reply.
  - So the ladder's first step "found" the board, and the watchdog restarted the stream on
    the stuck socket every 90 s.
  - Since `7e11bbe`, a watchdog revival always resets the board.
- **V.4 with `batch=30` held for the whole 30 minutes on one connection**, with 23 KB free at
  its lowest.
  - It was not flawless. Between about 23 and 27 minutes in, five windows dipped to
    2.7–3.8 Mbit/s and caught up straight after. Ten windows in that stretch had delays
    over 1 s, up to 3.3 s.
  - There were no Wi-Fi drops or reconnects in that stretch. My reading: a busy spell on
    channel 6, which a neighbouring network (`Verizon_X9ZJG7`) also uses.
  - Median latency was 2.9 ms; 99th percentile 183 ms.
- **This reads as the C5's version of Report 3's S3 fault (b)** (my reading): free memory
  near zero at 4.32 Mbit/s over TCP, and the board going silent. The S3 could not be tested
  here, so whether batching fixes the S3 too is still open.

---

## 6. The C5 at full load for 20 minutes (V.5)

TCP offered 60 Mbit/s for 20 minutes (more than the C5 can carry), with the fixed receiver.
This is the C5 half of Report 3's test 3.4, which then stopped after 90 s because of the
receiver fault.

| C5, TCP at full load, 20 min | V.5 |
|---|---|
| Delivered | **31.2 Mbit/s** |
| 10 s windows: median / slowest tenth / lowest | 31.6 / 28.9 / 23.5 Mbit/s |
| First 5 min / last 5 min (median) | 31.9 / 30.0 Mbit/s |
| Longest gap | 1.2 s |
| TCP connections / Wi-Fi disconnects / watchdog actions | 1 / 0 / 0 |
| Lowest free internal memory | 19.4 KB |
| Signal | −39 to −47 dBm |

- **At full load the C5's TCP is steady for 20 minutes on this router.** The sends are whole
  8,100 B buffers there, as with `batch=30`.
- **The C5 never dropped off Wi-Fi in any test on this router.** That held through
  2 h 20 min of long runs (V.2–V.6 and V.2b), apart from V.6's one forced drop. On House WiFi it disconnected about every
  1.5 minutes (Report 3 §7, reason 34). So that problem belongs to the House WiFi rig, or to
  Wi-Fi 6 there, not to the C5 in general. Here it joined as 802.11n. This is my reading:
  the two rigs differ in more than one way.
- **Harness note.** This run's 10 s windows and latency were not computed. The receiver's
  drift fit was too slow for a run of 17 million frames, and the run returned without them.
  The windows above are rebuilt from the run's 1 s series. Latency for V.5 is lost. Fixed in
  `f2c5cec` for later runs.

---

## 7. Stress (V.6) and write sizes (V.7)

### V.6: TCP stress at 4.32 Mbit/s, 30 minutes (default setting)

This is Report 3's test 3.5, which gave no result then because TCP had already stopped before
the first fault. The receiver reads into a small buffer (64 KB), so a read pause closes the
C5's window at once.

| Fault (time into the run) | Outcome |
|---|---|
| Receiver stops reading 1 s (2.5 min) | survived |
| Receiver stops reading 5 s (7.5 min) | survived |
| Receiver stops reading 30 s (12.5 min) | survived: delivery resumed after the pause |
| Firewall block 10 s (17.5 min) | skipped: needs passwordless `sudo pfctl` |
| Receiver resets the connection (22.5 min) | the C5 was already stalled at that moment (since 21.3 min); the watchdog reset it 17 s later |
| Wi-Fi link dropped 10 s (27.5 min) | survived: new connection 12 s later, full rate to the end |

| C5, V.6 | |
|---|---|
| Delivered, 30-min average | 2.48 Mbit/s |
| 10 s windows at ≥ 90 % of the rate | 94 of 180 |
| Stalls of its own (watchdog resets) | 7 |
| How long it ran between a reset and the next stall | 0.1–3.5 min, typically 2–3 min |
| Lowest free internal memory | 2.9 KB |
| Wi-Fi disconnects | 1 (the forced one) |

- **The injected faults are not what hurt the C5 on default; its own stalls are.** It recovered
  from every fault it met while it was running, including the 30 s read pause and the 10 s
  link drop.
- **It stalled seven times in 30 minutes.** Each time, the watchdog had to reset it. That is
  more often than V.3 (one stall in 30 min), probably because the small receive buffer and the
  pauses keep its send queue fuller (my reading).
- **The stress test with `batch=30` was not in the plan.** It is the obvious next run (§10).

### V.7: TCP write sizes at full load

TCP offered 60 Mbit/s, 3 × 20 s per write size. Each 8,100 B pool buffer is passed to `send()`
whole, or in 2,700 B or 1,350 B pieces. Report 3's test 1.2 tried only 540 B, and both boards
locked up (on House WiFi).

| C5 | 8,100 B (today) | 2,700 B | 1,350 B |
|---|---|---|---|
| Median delivered (min–max) | 27.9 (25.3–30.3) | 28.8 (27.1–29.6) | 26.9 (26.9–27.1) |
| Lock-ups | 0 of 3 | 0 of 3 | 0 of 3 |

- **Write size between 1,350 and 8,100 B makes no difference to the C5 at full load;** all
  three are within the spread.
- **None locked the board up.** So small writes as such are not what breaks it. See §3 for
  what this means for the stall-zone explanation.
- **This leaves 540 B as the only size that failed,** on the other router.

---

## 8. Board recovery: what the ladder did

Every step is in `recovery.log` [3]. Over the morning:

| | |
|---|---|
| Ladders started | 43 (7 of them by the mid-run watchdog) |
| Brought back by a serial reset (step 3) | 28, median 31 s each |
| "Found on the network" (step 2) | 12, all during V.3, where they did not help (§5) |
| Rebuild and reflash (step 5) | 2, both for the S3; neither brought it back |
| Gave up ("needs a person") | 2, both the S3 (06:36 and the 90-min retry at 09:28) |
| Reset after a stalled run, board still answering | 8 |
| Time spent in recovery | about 26 min in all |

- **For the C5, the ladder did its job.** It locked up after 14 stall-zone runs and 13 times
  inside long runs. Each time it was reset through its serial port and back within about
  30 s. No test was lost to it, and no person was needed.
- **For the S3, the ladder found the limit of what software can do.** A board that browns out
  at boot cannot be reset or reflashed back to life. The ladder said so twice, and the S3 sat
  out every test, with a cheap check before each one, instead of eating the time budget.
- **Two harness faults turned up and were fixed during the run.**
  - **V.3:** the watchdog trusted a board that answered `hello` while stalled. A watchdog
    revival now always resets (`7e11bbe`).
  - **V.5:** the receiver returned before computing a long run's windows (`f2c5cec`).
- **A gap remains.** A board whose USB device disappears entirely, or a router that goes
  down, still needs a person. The ladder logs that and carries on with the other board.

---

## 9. Changes made for this run (branch `verizon-tcp-repeats`)

| Commit | What |
|---|---|
| `d34f59d` | Firmware: runtime `cfg` key `batch=N` (the synth source holds frames until N are due, capped at one pool buffer; 0 = unchanged). Boards wipe a damaged NVS at boot instead of aborting. Receiver: a new TCP connection replaces the open one at once (Report 3 fault a); the longest-gap figure counts a silence that never ends. Recovery ladder, mid-run watchdog, shuffled run order, the Verizon plan. |
| `8784488` | A board the ladder could not revive persists as "down" and gets a cheap check before each test, with a full ladder at most every 90 min. |
| `f290b92` | The wait for a silent board to rejoin before a serial reset is a rig setting (12 s here; the C5 never rejoins after a lock-up). |
| `47a5cf1` | The extra V.2b; `host/tools/verizon_summary.py`, which prints this report's tables. |
| `7e11bbe` | A watchdog revival goes straight to a serial reset. Stall-zone and write-size runs that stall are followed by a reset, so the next run starts on a fresh board. |

---

## 10. What this means, and next steps

**For the HD-EMG board** (my reading):
- **Send in whole buffers.** On the C5, TCP with a small send every millisecond is not usable
  at any rate from 4.32 to 25 Mbit/s: it stalls within seconds to minutes and the board hangs.
  Sending whole 8,100 B buffers fixed it in every test here.
  - This costs up to 15 ms of extra delay at 4.32 Mbit/s.
  - The real firmware will receive frames from the STM32 in batches anyway. The point is not
    to hand them to TCP one millisecond at a time.
- **UDP is still not measured on this rig,** and the S3 is not measured at all. So nothing
  here changes Report 3's chip comparison yet.

**Next tests, in order of value:**
1. **Re-power the S3** (another USB port or cable, directly on the Mac). Then run its half of
   this plan:
   `bash campaign.sh --plan campaign/verizon-tcp.yaml --redo V.1a,V.1b,V.1c,V.3,V.4,V.6`.
   A redo measures both boards, so the C5 runs again too, which gives a same-hour
   comparison.
   The S3's 4.32 Mbit/s memory starvation in Report 3 (fault b) looks like the same
   problem; V.4 would show whether `batch=30` fixes it there too.
2. **V.6 with `batch=30`:** whether batching also makes the C5 robust under stress. On
   default, its own stalls dominated.
3. **Pin down the trigger:** a fixed send interval with varied send sizes, and the reverse,
   reading the C5's Wi-Fi transmit-buffer counters. That would tell whether the stall comes
   from spacing or size, and whether a smaller batch (say 10 frames, 5 ms at 4.32) is enough.
4. **Stage 2 TCP at 20.5 again** with `batch` set (Report 3 §9 c), once the STM32 is wired.
5. **Harness, small:** during a stall the C5 still answers `hello` but not `stat` (a
   1.4 KB reply), so its counters cannot be read just when they matter. A short status reply
   would fix that.

---

## Sources

1. `docs/campaign-results/2026-10-19-verizon-tcp/verizon.jsonl`: the ledger, one line per run, with the device telemetry summary and run events.
2. `.../state.json` and `.../summary.md`: each test's status and result, as `bench campaign --status` writes them.
3. `.../recovery.log`: every step of the recovery ladder, with the boards' boot console.
4. `.../tables.md`: the output of `host/tools/verizon_summary.py` for this ledger.
5. Per-run time series (10 s receiver windows, 1 Hz device samples, events) are under `host/results/campaign/verizon/ts/`. They are not committed; the soak figures in §4–§7 come from them.
6. `docs/REPORT_3_Followup_Campaign.md`: the 17–18 Oct results and the open problems this run picked up (§9, §12).
