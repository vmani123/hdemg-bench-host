# Report 2 — How Wi-Fi tuning works on the ESP32, what has been done, and what is left

*Written 2026-10-12. Plain English. Sources are listed at the end and numbered like [1], [2].
Where a statement comes from my own reading of the code or my own arithmetic rather than a
published source, it says so.*

---

## 1. The short version

- Your measured TCP speeds (**C5 ≈ 8 Mbit/s, S3 ≈ 5 Mbit/s**) are about **one-sixth to
  one-tenth** of what Espressif and third parties publish for these chips (roughly
  **45–85 Mbit/s TCP** and **60–100 Mbit/s UDP** in good conditions) [1][2][6].
- Wi-Fi speed on an ESP32 is mostly decided by **memory and queue settings** (how much data
  may be "in flight" at once), not by the chip's processor. Your tuning so far changed
  processor-side settings only, and the processor was mostly idle.
- **Every queue and window setting is still at the stock default**, and the stock defaults
  are the smallest sensible values. Espressif's own speed tests use values 3–10x larger.
- The most likely culprit for the TCP plateau is the **TCP send buffer** (5,760 bytes, set
  to 4 packets). That idea has been proposed and a test build exists, but **it has never
  been run**, so it is still a hypothesis.
- **UDP, 5 GHz and Wi-Fi 6 haven't been measured cleanly yet**, so the upper range of what
  these chips can do on your hardware is still unknown.

---

## 2. How data gets from your code to the air

Think of a pipeline with a waiting line at every stage. The slowest stage, or the
smallest waiting line, sets the speed.

```
 your code ──► socket buffer ──► TCP/IP stack (lwIP) ──► Wi-Fi driver ──► radio ──► router ──► Mac
   send()      "send buffer"      window, queues        TX/RX buffers,
                                                        frame bundling
```

1. **Your code** calls `send()` (TCP) or `sendto()` (UDP).
2. The **socket send buffer** holds data waiting to go out. If it is full, `send()` waits.
3. The **TCP/IP stack (lwIP)** cuts data into packets and, for TCP, may only have a limited
   amount unacknowledged ("in flight"), set by the **window**.
4. The **Wi-Fi driver** queues packets in its own **buffers** and bundles several into one
   transmission (**A-MPDU aggregation**), which is far cheaper in air time than sending
   them one by one. How many it may keep unacknowledged is the **block-ack window**.
5. The **radio** sends at a rate set by signal quality, band and channel width.
6. The **router** passes data on to the Mac; if the Mac is on Wi-Fi too, that is a second
   trip through the air.

### The one formula worth remembering

TCP can never go faster than **(data allowed in flight) ÷ (round-trip time)**. With only
5,760 bytes (46,080 bits) in flight:

| Round-trip time | Maximum TCP speed |
|---|---|
| 2 ms | 23 Mbit/s |
| 5.8 ms | 8 Mbit/s |
| 8.7 ms | 5.3 Mbit/s |

(This table is my arithmetic.) A bigger window lifts the cap; a shorter round trip does
too. Notice that the two measured plateaus (8 and 5.3 Mbit/s) correspond to plausible
Wi-Fi round trips of 6–9 ms. The round-trip time on your rig was **never measured
cleanly** (the one ping to the C5 was taken while a UDP flood was running).

---

## 3. The tuning "levers", in plain terms

| Lever | What it does | Why it matters |
|---|---|---|
| **TCP send buffer and window** (`LWIP_TCP_SND_BUF_DEFAULT`, `LWIP_TCP_WND_DEFAULT`) | How much unacknowledged data TCP may have in flight | The speed cap in the formula above. Biggest single lever for TCP [1][3] |
| **Wi-Fi TX/RX buffer counts** (`ESP_WIFI_DYNAMIC_TX_BUFFER_NUM`, `..._RX_...`, `STATIC_RX`) | How many packets the driver can hold | Too few and the driver stalls or drops. Espressif says to size these together with the TCP window [1] |
| **A-MPDU and block-ack window** (`ESP_WIFI_TX_BA_WIN`, `RX_BA_WIN`) | How many frames are sent in one bundle before waiting for acknowledgement | Fewer, larger air transmissions = much less overhead. Stock is 6; Espressif's speed test uses 32 [1][3] |
| **Power save off** (`esp_wifi_set_ps(WIFI_PS_NONE)`) | Stops the radio sleeping between packets | Required for sustained speed. Espressif's iperf example turns it off [3] |
| **CPU speed, compiler optimisation** | Faster code | Helps only if the CPU is the limit |
| **IRAM placement** (`WIFI_IRAM_OPT`, `WIFI_RX_IRAM_OPT`, `LWIP_IRAM_OPTIMIZATION`) | Keeps hot Wi-Fi/TCP code in fast internal memory instead of slower flash | Reduces per-packet CPU cost; Espressif's top-speed ranks all turn it on [1] |
| **Flash mode and cache** (QIO at 80 MHz, bigger instruction cache) | Faster code fetch | Part of Espressif's top-speed ranks [1] |
| **lwIP task priority, core pinning, core locking** | Which task and core runs the network stack | Mostly matters when the CPU is busy; core locking is used in Espressif's C5 build [3][4] |
| **Mailbox sizes** (`TCP_RECVMBOX_SIZE`, `UDP_RECVMBOX_SIZE`) | Small queues between tasks | Stock UDP mailbox is 6; Espressif uses 64 [3] |
| **Write size and Nagle** | How many bytes each `send()` carries, and whether TCP waits to merge small writes | Many tiny packets waste air time. Large writes (several KB) and datagrams near 1,470 bytes are the norm in benchmarks [3][4] |
| **Band and channel width** | 2.4 vs 5 GHz; 20 vs 40 MHz; Wi-Fi 4 vs Wi-Fi 6 | Sets the raw radio speed. The S3 is 2.4 GHz only; the C5 also does 5 GHz and Wi-Fi 6, but only at 20 MHz [1][5] |
| **The other end of the link** | Router quality, channel congestion, whether the Mac is also on Wi-Fi | Can cap everything below the chip's limit |

---

## 4. What Espressif and others publish

Espressif's own tables [1][2]. These are the best-case numbers they publish.
The S3 table I read directly from Espressif's page [1]; the C5 numbers were collected by a
research pass over the C5 page [2] and the C5 table has the same structure but I only
confirmed its first row myself (UDP RX 30 Mbit/s over the air in a lab, 68 in a shield box).

| Chip | Test | TCP send | UDP send | Notes |
|---|---|---|---|---|
| S3, "Iperf" settings | Shield box, ASUS RT-N66U | **83.9** | **98.7** | 64 KB send buffer and window |
| S3, "Default" settings | same | 64.3 | 96.3 | 32 KB buffers |
| S3, "Minimum" settings | same | 23.2 | 48.8 | ~6 KB send buffer and window, RX block-ack window 6, lwIP IRAM option off, smaller instruction cache |
| C5, 2.4 GHz | Shield box | ~49 | ~63 | |
| C5, 5 GHz | Shield box | ~50 | ~64 | |
| Both chips | "Over the air in a lab" | ~20 | ~30 | Espressif's own realistic figure [2] |

Real-world measurements by others, through a home router:

| Source | Chip | TCP | UDP |
|---|---|---|---|
| Kreuzer, FRITZ!Repeater at 2 m, ESP-IDF 5.4.1 iperf example [5] | S3, 2.4 GHz | 46.2 | 75.3 |
| same [5] | C5, 2.4 GHz | 47.5 | 62.5 |
| same [5] | C5, 5 GHz | 47.3 | 65.6 |
| Seeed XIAO C5 wiki [6] | C5, 2.4 GHz | 17 up | 27 up |

Two cautions: (a) the numbers are "up to" figures with careful setups, and (b) your
target tier T2 (41.3 Mbit/s) is *UDP*, so Espressif's UDP rows matter most.

**A useful detail in the S3 table.** The "Minimum" row has about the same send buffer
(6 KB) and receive block-ack window (6) as your stock build, yet reaches 23 Mbit/s over TCP in
the shield box. So a small buffer alone does not force 5–8 Mbit/s. In the formula of
section 2 that rate implies a round trip of only about 2 ms, and your path appears to
have a much longer one (6–9 ms, unmeasured). The buffer is therefore best thought of as
*one* of the causes, magnified by the long round trip on a two-hop Wi-Fi path. Note too
that the table changes several settings at once between rows, so the jumps cannot be
credited to any single setting.

**A known C5 caveat.** An Espressif issue thread reports a C5 whose throughput dropped
to zero in bursts until the Wi-Fi buffer and TCP window settings were brought back in
step; afterwards it held about 42 Mbit/s [7]. This supports "keep those settings
consistent with each other".

---

## 5. What your build uses today

Values read from the built `sdkconfig` of both chips (the same on S3 and C5) [8], against the
config Espressif uses for its published speed tests [3] (as reported by the research pass;
I did not re-fetch these files):

| Setting | Your build (both chips) | Espressif iperf, S3 | Espressif iperf, C5 |
|---|---|---|---|
| TCP send buffer | **5,760 B** | 65,535 | 41,760 |
| TCP window | **5,760 B** | 65,535 | 65,535 |
| Block-ack window, TX / RX | **6 / 6** | 32 / 32 | 32 / 22 |
| Wi-Fi dynamic TX buffers | **32** | 64 | 48 |
| Wi-Fi dynamic RX buffers | **32** | 64 | 48 |
| Wi-Fi static RX buffers | **10** | 16 (file) / 24 (docs table) | 20 |
| UDP mailbox size | **6** | 64 | 64 |
| lwIP task priority | **18** | 23 | — |
| Core locking | off | — | on |
| IRAM options | S3 off; C5 on (r2) | on | on |
| Power save | off at run time [8] | off | off |

---

## 6. What has been done so far

### The tuning ladders [8]

| Rung | Applies to | What it changes | Run? |
|---|---|---|---|
| `r0-baseline` | both | Stock settings, power save intended off | Yes (called "baseline") |
| `r1-cpu-opt` | both | 240 MHz, speed-optimised compile, logging off | Only as part of r2; never alone |
| `r2-iram` | C5 | r1 + the three IRAM options | Yes (called "tuned") |
| `r2-core-split` | S3 | r1 + Wi-Fi task on core 0, lwIP task on core 1 | Yes (called "tuned") |
| `p1-tcp-sndbuf` | both | **Only** the TCP send buffer: 5,760 → 46,080 B | **Built, never run** |

### Results of the ladder

| Chip | Baseline | "Tuned" | Change |
|---|---|---|---|
| C5 | 7.65 Mbit/s | 8.02 Mbit/s | +5 % |
| S3 | 5.17 Mbit/s | 5.49 Mbit/s | +6 % (only 7 valid tuned runs) |

(TCP, 2.4 GHz, steady level at 12–60 Mbit/s offered; see Report 1.) Gains of 5–6 % are what
you would expect when the CPU was mostly idle (~76 % idle on the C5).

### Other observations

- **Power save.** The power-save settings written in the rung files do not exist in the
  built config (no `POWER_SAVE` entry appears in it), so they did nothing. The runtime
  call `esp_wifi_set_ps(WIFI_PS_NONE)` in both `app_main.c` files is what turns power save
  off, and it does [8]. This is fine, but the rung files should be corrected so they do not
  claim something they don't do.
- **Equal-effort rule not satisfied.** The checklist `common-levers.yaml` still lists
  every lever as "NOT ATTEMPTED" for both chips. In particular the S3 ladder never tried the
  IRAM options that the C5 ladder did, although Espressif's S3 top-speed settings use
  them [1][8].
- **The S3 runs were partly lost** to a signal-reading quirk (Report 1), so the S3 numbers
  are on thinner data.
- **Link mode.** Both chips connected at 802.11n, 20 MHz on channel 6. Nothing was done to
  check whether a wider channel (40 MHz) or the C5's Wi-Fi 6 mode was available on the
  router.

---

## 7. What is left to do (suggested order)

Each step is cheap compared with a full overnight sweep. The order puts the
*explaining* work first, so later tuning is steered by evidence rather than guesses.

1. **Measure the round trip time** from the Mac to each board, on an otherwise quiet link,
   and (if possible) record TCP retransmissions and Wi-Fi retry counts during runs. It
   would turn the buffer-over-round-trip story from a guess into a check. *Effort: small.*
2. **Run Espressif's `iperf` on both boards (Stage 0)** with a known-good receiver. This
   gives the actual ceiling of *your* router, room and Mac setup. If custom firmware gets
   within ~20 % of it, the firmware is fine; if not, the firmware is the limit. It also
   replaces the placeholder rig ceilings (60 / 95). *Effort: small; the runner action
   exists.*
3. **Run the send-buffer probe** (`hotspot_probe.sh`, or the same two builds on the
   router). It isolates exactly one change. Expect the biggest jump if the hypothesis is
   right. Remember the hotspot version shares one channel and reads about half as fast.
   *Effort: ~30 minutes.*
4. **Fix the UDP sending code before measuring UDP** (Report 1 §6): send roughly five
   frames (about 1,350 bytes) per datagram so nothing is fragmented, retry briefly on
   "out of memory" errors instead of closing the socket, and count send failures on the
   board. Then re-run UDP with the Cisco filter off. *Effort: small code change.*
5. **Add a "network" rung** that copies Espressif's settings in section 5: send buffer and
   window 41–64 KB, block-ack window 32, 48–64 Wi-Fi buffers each way, bigger mailboxes,
   task priority 23, core locking on the C5. Then **remove settings one at a time** to see
   which ones carry the gain, so the ladder records each setting's measured value, as the
   project's equal-effort rule intends. Do this for both chips, including the S3's missing
   IRAM options. *Effort: medium.*
6. **Try write size** (batch more per `send()`) and the lwIP/Nagle behaviour; both are on
   the checklist as not attempted. *Effort: small.*
7. **Look at the radio side:** try 40 MHz on 2.4 GHz; wire the Mac to the router (so the
   second hop is no longer on Wi-Fi) and run the **C5 on 5 GHz** and check whether it
   actually connects in Wi-Fi 6 mode (Kreuzer found the C5 limited to 20 MHz there [5]).
   Choose a clean channel. *Effort: needs cabling and a router setting.*
8. **Consider a better access point** if, after steps 1–7, the chips still fall short of
   T2. The project plan already lists a real 802.11ax access point and the TI CC3351 as
   the next escalation [9].
9. **Only then** return to CPU-side tuning (clock, IRAM, core split), because its payoff
   shows up only once the CPU is the limit.

### What outcome to expect (with honest uncertainty)

- Reaching **T2 (41.3 Mbit/s)** looks plausible on UDP for both chips with Espressif-style
  settings, based on third-party home-router results (S3 ≈ 75, C5 ≈ 63–66) [5], but your
  rig is less ideal than a 2 m shielded test.
- Espressif's own realistic open-air figure is only ~30 Mbit/s UDP and ~20 TCP [2], below
  T2. If your room behaves like that, neither chip clears T2 on this rig and the plan's
  "neither clears" branch applies [9].
- The **S3 has more headroom on paper at 2.4 GHz**; the **C5's advantages are 5 GHz and
  Wi-Fi 6**, which are untested. The test plan's expectation that the C5 wins should
  therefore stay a hypothesis until the 5 GHz block is measured.

---

## 8. Plain-English glossary

- **Goodput** — useful data received per second, excluding overhead and retries.
- **TCP vs UDP** — TCP resends lost data and slows down to avoid overload; UDP just sends and
  never resends.
- **Window / send buffer** — how much sent-but-unacknowledged data TCP may have outstanding.
- **Round-trip time (RTT)** — time for a packet to go out and its acknowledgement to come back.
- **A-MPDU / block-ack window** — sending several frames in one air transmission, and how many
  may await acknowledgement.
- **IRAM** — fast internal memory; placing hot code there avoids slower flash reads.
- **HT / HE** — 802.11n (Wi-Fi 4) / 802.11ax (Wi-Fi 6) modes. The S3 has only HT.
- **Shield box** — a metal box that blocks outside radio noise, giving best-case numbers.

---

## Sources

1. Espressif, *ESP32-S3 Wi-Fi Performance and Power Save* (ESP-IDF programming guide).
   https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-guides/wifi-driver/wifi-performance-and-power-save.html
   — S3 speed table and "Iperf / Default / Minimum" settings. **Read directly** for this report.
2. Espressif, *ESP32-C5 Wi-Fi Performance and Power Save* and *Wi-Fi Driver Overview*.
   https://docs.espressif.com/projects/esp-idf/en/latest/esp32c5/api-guides/wifi-driver/wifi-performance-and-power-save.html
   https://docs.espressif.com/projects/esp-idf/en/latest/esp32c5/api-guides/wifi-driver/overview.html
   — C5 lab and shield-box table; "up to 20 Mbit/s TCP / 30 Mbit/s UDP over the air". Table
   rows partly confirmed by me; the rest from a research pass.
3. Espressif, ESP-IDF `examples/wifi/iperf` configuration files (`sdkconfig.defaults`,
   `.esp32s3`, `.esp32c5`), the settings behind Espressif's published speeds.
   https://github.com/espressif/esp-idf/tree/master/examples/wifi/iperf — values as
   reported by the research pass; not re-fetched by me.
4. Espressif, *lwIP* guide (ESP-IDF programming guide), on `TCP_NODELAY`, task affinity and
   core locking.
   https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-guides/lwip.html
5. H. Kreuzer, *ESP32-C5 dual-band 2.4 and 5 GHz Wi-Fi 6 IoT projects* — third-party
   iperf results on S3 and C5 through a FRITZ!Repeater.
   https://www.haraldkreuzer.net/en/news/esp32-c5-dual-band-24-and-5ghz-wi-fi-6-iot-projects
6. Seeed Studio, *XIAO ESP32-C5 Wi-Fi throughput tester* wiki, home-router measurements.
   https://wiki.seeedstudio.com/xaio_esp32c5_wifi_throughput_tester/
7. Espressif, `esp-hosted-mcu` issue #122 (C5 throughput instability traced to buffer
   mismatch). https://github.com/espressif/esp-hosted-mcu/issues/122
8. This repository: `firmware/*/sdkconfig` (built, stock values), `firmware/*/main/app_main.c`
   (power-save call), `host/rungs/**` (ladders), `host/rungs/common-levers.yaml`
   (checklist), `host/results/runs.jsonl` (measured results).
9. This repository: `docs/S3_vs_C5_Test_Plan_v2.md` (decision tiers, escalation path).
