# Report 2 — How Wi-Fi tuning works on the ESP32, what has been done, and what is left

*Written 2026-10-12. Plain English. Sources are listed at the end and numbered like [1], [2].
Where a statement comes from my own reading of the code or my own arithmetic rather than a
published source, it says so.*

*Updated 2026-10-13 with the results of the 13 Oct tuning study [10][11]. The new results are
mainly in §1, §6 and §7. §2 and §4 gained short notes marked "Update 13 Oct". §5 now also lists
the tuned settings and corrects one stock value. The rest is unchanged background.*

---

## 1. The short version

- **The 13 Oct tuning study raised TCP 4–6× over the 5 Oct numbers and gave the first clean
  UDP numbers** (2.4 GHz, 20 MHz channel, 0 % loss) [10]. It ran on a different router from
  5 Oct, so part of the rise is conditions:

  | Chip | Before tuning (5 Oct, TCP) | Tuned TCP | Tuned UDP | Espressif `iperf`, same link |
  |---|---|---|---|---|
  | ESP32-S3 | ≈ 5 Mbit/s | **31.6** | **44.6** | 33.0 / 44.4 |
  | ESP32-C5 | ≈ 8 Mbit/s | **31.3** | **54.0** | 28.8 / 48.4 |

- **Both chips now pass T2 (41.3 Mbit/s) over UDP**, the S3 narrowly (8 % margin) and the C5
  comfortably (31 %). Neither reaches T3 (61.4). These are short tuning runs, so the pass is
  provisional until 60 s qualifying runs confirm it.
- The hypothesis from 12 Oct held: the **TCP send buffer** was the cap. Raising it from
  5,760 B to 64 KB alone gave TCP 4–5× on both chips.
- The other gains came from **more Wi-Fi transmit buffers and moving network code into fast
  internal RAM** on the S3, and **core locking plus more code in fast RAM** on the C5.
- **Both chips now carry about 62–63 % of their link's raw rate as UDP**, close to what one
  20 MHz stream allows. Espressif's own `iperf` firmware did no better on UDP on the same
  link. Its one edge was 4 % more TCP on the S3.
  The next real gains need a wider channel (40 MHz) or the C5 on 5 GHz.
- **Memory has replaced settings as the limit.** Most remaining settings left under 20 KB
  of free memory and were rejected. In one short run at the HD-EMG rate, the C5 fell to
  1.1 KB free. A soak test is the first thing to do next.

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

*Update 13 Oct (House WiFi rig).* The round trip has now been measured with the boards idle:
4.5 ms at best and 5.6–6.0 ms at the median, over 50 requests per board [10]. Under
load it is longer. The stock 5,760 B buffer gave 3.9–4.5 Mbit/s there, which by the
formula implies 10–12 ms. With 64 KB in flight the same formula allows about 44–52 Mbit/s,
above what TCP reached, so the window stopped being the cap. That matches the 4–5× jump in
§6. The C5's final 41,760 B buffer allows about 28–33 Mbit/s at 10–12 ms (my arithmetic),
right where its TCP sits. That is why its 64 KB build was faster but was dropped for memory.

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

**On your own rig (13 Oct).** Espressif's `iperf` example, run on both boards on the House
WiFi 2.4 GHz radio (20 MHz), gave **S3 33.0 / 44.4** and **C5 28.8 / 48.4** Mbit/s TCP / UDP
(Mac-side receive rate, 2 runs of 20 s each) [10]. That is below Kreuzer's 2 m test and
above Espressif's open-air figure. The tuned custom firmware matched it on the S3 and beat it
on the C5 (§6).

**A useful detail in the S3 table.** The "Minimum" row has about the same send buffer
(6 KB) and receive block-ack window (6) as your stock build, yet reaches 23 Mbit/s over TCP in
the shield box. So a small buffer alone does not force 5–8 Mbit/s. In the formula of
section 2 that rate implies a round trip of only about 2 ms, and your path appears to
have a much longer one (6–9 ms, unmeasured). The buffer is therefore best thought of as
*one* of the causes, magnified by the long round trip on a two-hop Wi-Fi path. Note too
that the table changes several settings at once between rows, so the jumps cannot be
credited to any single setting. *Update 13 Oct:* the round trip has since been measured
(§2): 4.5–6 ms idle, and about 10–12 ms implied under load. That supports this reading.

**A known C5 caveat.** An Espressif issue thread reports a C5 whose throughput dropped
to zero in bursts until the Wi-Fi buffer and TCP window settings were brought back in
step; afterwards it held about 42 Mbit/s [7]. This supports "keep those settings
consistent with each other".

---

## 5. What your builds use: stock and tuned

Values read from the built `sdkconfig` of both chips (the same on S3 and C5 unless noted) [8],
against the config Espressif uses for its published speed tests [3] (as reported by the
research pass; I did not re-fetch these files). The two right-hand columns, added 13 Oct, are
the final designs of the tuning study, read from their rung files on `tune-loop` [12].

| Setting | Stock build (both chips) | Espressif iperf, S3 | Espressif iperf, C5 | **Tuned S3** (`t24-08`) | **Tuned C5** (`t24-16`) |
|---|---|---|---|---|---|
| TCP send buffer | **5,760 B** | 65,535 | 41,760 | 65,535 | 41,760 |
| TCP window | **5,760 B** | 65,535 | 65,535 | 65,535 | 65,535 |
| Block-ack window, TX / RX | **6 / 6** | 32 / 32 | 32 / 22 | 32 / 6 | 6 / 6 |
| Wi-Fi dynamic TX buffers | **32** | 64 | 48 | 64 | 32 |
| Wi-Fi dynamic RX buffers | **32** | 64 | 48 | 32 | 32 |
| Wi-Fi static RX buffers | **10** | 16 (file) / 24 (docs table) | 20 | 10 | 10 |
| UDP mailbox size | **6** | 64 | 64 | 6 | 6 |
| lwIP task priority | **18** | 23 | — | 18 | 18 |
| lwIP task core (S3) | not pinned | — | — | core 1 (Wi-Fi on core 0) | — (single core) |
| Core locking | off | — | on | off | on (+ input) |
| IRAM options | Wi-Fi TX/RX on by default on both; lwIP off on the S3, on on the C5 (r2) | on | on | Wi-Fi, lwIP, lwIP extra, FreeRTOS | Wi-Fi, lwIP, lwIP extra, FreeRTOS |
| Power save | off at run time [8] | off | off | off | off |

*Correction (13 Oct):* the 12 Oct version of this table said the S3's IRAM options were off.
In fact the Wi-Fi IRAM options are on by default on both chips; only the lwIP IRAM option was
off on the S3.

Settings specific to this bench, also part of the tuned designs (on `tune-loop`):

- UDP datagrams of 1,472 B (5 frames).
- A buffer pool of 6 × 8 KB on the S3 and 4 × 8 KB on the C5.
- The S3 asks for a 40 MHz channel, but the router's 2.4 GHz radio is set to 20 MHz.
- Both boards are pinned to the channel-1 access point.

Many of Espressif's larger values (RX buffers, a larger tcpip mailbox, task priority, the C5's 32-frame
TX window) were tried and rejected on this rig. They either did not gain or ran out of
memory (§6).

---

## 6. What has been done so far

### The 5 Oct ladder [8]

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

### The 13 Oct tuning study [10][11]

This study did roughly what §7 items 2–5 of the 12 Oct version proposed:

- It fixed the UDP sender.
- It ran Espressif's `iperf` as a reference.
- It applied Espressif-style network settings **one at a time**, so each setting's own
  effect is measured.

Each chip started from its 5 Oct "tuned" rung. Each step was the current best plus one
change, measured at 60 Mbit/s offered: TCP then UDP, 2 runs of 20 s each. A change was kept
if three things held:

- TCP or UDP gained at least 2 % **and** more than the two builds' combined repeat spread.
- Neither transport fell by 2 % beyond that spread.
- The lowest free internal memory stayed at or above 20 KB.

A change that would have gained but broke the memory floor was rejected as unsafe. A safe
change could replace an unsafe best even if slower (C5 step 10).

Rig: House WiFi, a UniFi router, 2.4 GHz channel 1 at 20 MHz. The Mac was on 5 GHz.
Report 1 §5 has the conditions and caveats in full.

**ESP32-S3 climb** (median of 2 runs; loss 0 % unless shown; "—" = memory not yet recorded):

| Step | Change | TCP Mbit/s | UDP Mbit/s | Lowest free memory | Decision |
|---|---|---|---|---|---|
| 0 | Start (`r2-core-split`): 240 MHz, -O2, Wi-Fi on core 0, lwIP on core 1 | 3.9 | 23.0 | — | start |
| 1 | TCP send buffer and window 5,760 → 65,535 B | 20.3 | 23.6 | — | **kept**: TCP +416 % |
| 2 | Transmit block-ack window 6 → 32 | 21.0 | 23.4 (0.04 % loss) | — | **kept**: TCP +3.4 % |
| 3 | Dynamic TX buffers 32 → 64 | 21.3 | 26.2 (0.15 % loss) | — | **kept**: UDP +12 % |
| 3, again | Same build, session 2 (05:05) | 27.0 | 31.3 | 61.0 KB | new reference |
| 4 | RX buffers: static 16, dynamic 64, RX block-ack 32 | 20.1 | 28.0 (0.28 % loss) | — | rejected: TCP −5.4 % |
| 5 | Buffer pool 6 → 12 × 8 KB | 21.0 | 26.4 (0.23 % loss) | — | rejected: no gain |
| 6 | lwIP core locking | 17.0 | 45.2 (4.1 % loss) | — | rejected: TCP −20 % |
| 7 | lwIP task priority 23, tcpip mailbox 32 → 64 | 26.4 | 30.8 | 45.2 KB | rejected: inside the spread |
| 8 | lwIP, lwIP extra and FreeRTOS hot code in IRAM | **31.6** | **44.6** | 20.8 KB | **kept**: TCP +17 %, UDP +43 % |
| 9 | 32 static TX buffers | 30.5 | 43.1 | 14.8 KB | rejected: memory, no gain |
| 10 | Caches: 32 KB instruction, 64 KB data | 32.4 | 44.6 | 0.7 KB | rejected: memory |
| 11 | Wi-Fi and lwIP buffers in PSRAM, A-MSDU on | 26.8 | 38.6 | 157.6 KB | rejected: TCP −15 %, UDP −13 % |
| 12 | Core locking on the IRAM build | 30.2 | 43.2 | 7.7 KB | rejected: memory, no gain |

**ESP32-C5 climb:**

| Step | Change | TCP Mbit/s | UDP Mbit/s | Lowest free memory | Decision |
|---|---|---|---|---|---|
| 0 | Start (`r2-iram`): 240 MHz, -O2, Wi-Fi and lwIP in IRAM | 4.5 | 29.7 (0.4 % loss) | — | start |
| 1 | TCP send buffer and window 5,760 → 65,535 B | 19.0 | 34.8 (1.7 % loss) | — | **kept**: TCP +322 %, UDP +17 % |
| 1, again | Same build, session 2, pinned to channel 1 (05:35) | 33.4 | 48.0 | 2.5 KB | new reference, but under the memory floor |
| 2 | TX block-ack window 6 → 32 | — | — | — | withdrawn: board was on the channel-6 radio |
| 3 | Same, retried | 16.9 | 25.7 (0.8 % loss) | — | rejected: TCP −11 %, UDP −26 % |
| 4, 5 | Core locking; dynamic TX buffers 32 → 48 | — | — | — | not measured: channel-6 radio |
| 6 | lwIP core locking (on the 64 KB build) | 34.4 | 50.6 | 1.1 KB | rejected: memory (would have gained UDP +5.5 %) |
| 7 | TCP send buffer 41,760 B | 29.5 | 49.9 | 14.8 KB | rejected: memory, TCP −12 % |
| 8 | TCP send buffer 32,768 B | 24.6 | 45.9 (0.3 % loss) | 14.9 KB | rejected: memory, TCP −26 % |
| 9 | Core locking, retried | 33.4 | 49.9 (0.01 % loss) | 1.9 KB | rejected: memory |
| 10 | Send buffer 41,760 B **and** buffer pool 6 → 4 | 29.7 | 48.5 | 31.4 KB | **kept**: replaces the unsafe reference (TCP −11 %) |
| 11 | Dynamic TX buffers 32 → 48 | 30.2 | 52.0 | 5.9 KB | rejected: memory (would have gained UDP +7.3 %) |
| 12 | Core locking on the safe base | 29.7 | 52.7 | 56.2 KB | **kept**: UDP +8.8 % |
| 13 | RX buffers: static 20, dynamic 48, RX block-ack 22 | 30.6 | 51.7 | 6.1 KB | rejected: memory |
| 14 | Buffer pool 4 → 12 | 26.8 | 51.4 | 1.8 KB | rejected: memory |
| 15 | lwIP task priority 23, tcpip mailbox 32 → 48 | 30.6 | 53.7 | 55.9 KB | rejected: TCP +3.0 % inside the spread |
| 16 | lwIP extra and FreeRTOS hot code in IRAM | **31.3** | **54.0** | 31.0 KB | **kept**: TCP +5.4 % |
| 17 | 32 static TX buffers | 31.4 | 52.4 | 1.9 KB | rejected: memory, no gain |
| 18 | 40 MHz (802.11n), offered 72 | 29.3 | 45.0 | 26.1 KB | not measured: the router gave 20 MHz |
| 19 | TX block-ack window 6 → 64 | 31.8 | 52.8 | 15.9 KB | rejected: memory, no gain |
| 20 | Wi-Fi 6 MCS 8–9 on | 30.5 | 52.4 | 19.7 KB | rejected: UDP −2.8 %, memory just under the floor |

Each step is compared with the configuration it was built on, measured in the same session.
The jumps between sessions (S3 step 3, C5 step 1) are conditions, not settings. The same
builds ran 19–75 % faster at 05:00 than at 03:00.

**Why the kept settings helped** (mechanisms marked *inferred* were not profiled):

- **TCP send buffer and window, both chips.** This is the formula of §2. 5,760 B at
  4–4.5 Mbit/s means a 10–12 ms round trip under load, and 64 KB lifts that cap well above
  what TCP then reached.
- **IRAM placement on the S3.** lwIP ran on core 1 from flash, through the instruction
  cache the two cores share.
    - Before: at 31 Mbit/s of UDP that core read 0.2–0.5 % idle, fully busy. The idle
      counter is rough in absolute terms, but this contrast is large.
    - After moving lwIP and FreeRTOS hot code into internal RAM: core 1 was 87–91 % idle at
      44.6 Mbit/s, and UDP rose 43 %.
    - The likely mechanism is fewer cache misses per packet (*inferred*).
    - Cost: about 40 KB of free memory.
    - This was the S3 lever the 12 Oct version flagged as never tried.
- **Core locking on the C5.** On one core, handing each packet to lwIP's own task costs a
  task switch. Core locking runs lwIP in the sending task instead (*inferred*). It fit in
  memory only after the send buffer and the buffer pool shrank (step 10).
- **64 Wi-Fi TX buffers on the S3.** With 32, the sender probably waited for a free buffer
  more often (*inferred*).

**What limits each chip now** [11]:

| Chip, transport | Limited by | Evidence |
|---|---|---|
| S3 UDP, 44.6 | The air | 62 % of the 72.2 Mbit/s raw rate (HT20, MCS7); equal to Espressif `iperf` (44.4); both cores read mostly idle (the idle counter is rough) |
| S3 TCP, 31.6 | TCP's per-segment and acknowledgement cost (likely) | 96 % of `iperf` on the same link, while the same air carries 44.6 of UDP |
| C5 UDP, 54.0 | The air | 63 % of the 86.0 Mbit/s raw rate (HE20, MCS7), above `iperf` (48.4) |
| C5 TCP, 31.3 | Memory | The 64 KB send buffer gave 33.4 Mbit/s but left 2.5 KB free |

One 20 MHz stream rarely carries more than 70–75 % of its raw rate as UDP (an estimate). That
puts the ceiling at about 50–54 Mbit/s for the S3's HT20 link and 60–65 for the C5's HE20
link. Both chips sit at about 85 % of it.

**Interactions worth knowing.** A 32-frame TX block-ack window, which Espressif's C5 `iperf`
config uses, cost this C5 26 % of its UDP on a clean retest, while it helped the S3 slightly.
That is likely an interaction between the C5's Wi-Fi 6 aggregation and this router
(unverified), and it may be why the tuned C5 beat Espressif's `iperf` here. ESP-IDF leaves
Wi-Fi 6 MCS 8–9 off by default, and turning it on gained nothing at −63 to −67 dBm.

### Other observations

- **Power save.** The power-save settings written in the rung files do not exist in the
  built config (no `POWER_SAVE` entry appears in it), so they did nothing. The runtime
  call `esp_wifi_set_ps(WIFI_PS_NONE)` in both `app_main.c` files is what turns power save
  off, and it does [8]. This is fine, but the rung files should be corrected so they do not
  claim something they don't do. *Update 13 Oct:* corrected on `tune-loop`. Both
  `r0-baseline` files are now empty, with a note that power save is set at run time. A
  build check now refuses any rung whose settings did not reach the build [12].
- **Equal-effort rule not satisfied.** The checklist `common-levers.yaml` still lists
  every lever as "NOT ATTEMPTED" for both chips. In particular the S3 ladder never tried the
  IRAM options that the C5 ladder did, although Espressif's S3 top-speed settings use
  them [1][8]. *Update 13 Oct:* the study tried the same families of setting on both chips:
  TCP window, block-ack window, Wi-Fi TX and RX buffers, buffer pool, core locking, task
  priority, IRAM and static TX buffers. It also tried a few chip-specific ones: S3 cache and
  PSRAM; C5 40 MHz, MCS 8–9 and smaller send buffers. The S3 IRAM gap is closed. The
  checklist file itself was not updated.
- **The S3 runs were partly lost** to a signal-reading quirk (Report 1), so the S3 numbers
  are on thinner data. *Update 13 Oct:* none of the study's 144 runs was refused by a gate.
- **Link mode.** Both chips connected at 802.11n, 20 MHz on channel 6. Nothing was done to
  check whether a wider channel (40 MHz) or the C5's Wi-Fi 6 mode was available on the
  router. *Update 13 Oct:* on House WiFi the C5 connects in Wi-Fi 6 mode (HE20). The
  router's 2.4 GHz radio is set to 20 MHz: the S3 asks for 40 MHz and gets HT20, and the
  C5's 40 MHz step fell back to HT20.

---

## 7. What is left to do (suggested order)

### Status of the 12 Oct list (updated 13 Oct)

| 12 Oct item | Status after 13 Oct |
|---|---|
| 1. Measure the round trip | **Done** with the boards idle: 4.5 ms best, 5.6–6.0 ms median. TCP retransmits and Wi-Fi retries are still not recorded |
| 2. Espressif `iperf` (Stage 0) | **Done** as a one-off: S3 33.0 / 44.4, C5 28.8 / 48.4 Mbit/s. The tuned firmware is within 5 % of it or above. The harness's rig-ceiling setting is still a placeholder (72) |
| 3. Send-buffer probe | **Done in effect** inside the climb: 65,535 B send buffer and window gave TCP 4–5× on both chips. The 46,080 B `p1-tcp-sndbuf` build itself never ran |
| 4. Fix the UDP sender | **Done** on `tune-loop`: 1,472 B datagrams, wait-and-retry on a full queue. No send-failure counter yet |
| 5. Network settings, one at a time | **Done** as the climb: 12 steps on the S3, 20 on the C5 (§6) |
| 6. Write size and Nagle | Not done; only the UDP datagram size changed |
| 7. Radio side | 40 MHz **blocked** for now: the router's 2.4 GHz radio is set to 20 MHz. C5 on 5 GHz not done |
| 8. Better access point | Not needed for T2 at 2.4 GHz if the qualifying runs confirm it. Still a route to T3 |
| 9. CPU-side tuning | **Done where it mattered**: IRAM on the S3 gave +43 % UDP. Bigger caches were rejected for memory |

### What is left now

1. **Soak-test memory before any long recording.** Stream at 4.32 Mbit/s for an hour or more
   on each final design, and record the lowest free memory per run. ESP-IDF's
   `heap_caps_monitor_local_minimum_free_size_start/_stop` does this. The C5 fell to 1.1 KB
   in a single 20 s run. If it dips that low again, there are three fixes: shrink the TCP
   buffer or the buffer pool, or stream HD-EMG over UDP, which needs no send buffer.
   *Effort: small code change, a few hours of board time.*
2. **Confirm T2 with qualifying runs.** Offer exactly 41.3 Mbit/s of UDP for 60 s, at least
   3 repeats per chip, in one session. Re-measure a reference build at the start. Check
   that loss is under 0.1 % and the full rate arrives. A Stage 1 sweep over the final
   designs adds the knee. *Effort: about an hour; a sweep is one night.*
3. **Free internal RAM so the rejected settings fit.** Options:
    - Move the S3's 48 KB buffer pool to PSRAM. It holds cold data, unlike the Wi-Fi and
      lwIP buffers, whose move to PSRAM cost 13–15 %.
    - Trim task stacks.
    - Drop an IRAM group that gains little.

   Then retry the three C5 settings that gained but broke the floor: the 64 KB TCP buffer
   (33.4 Mbit/s TCP), 48 TX buffers (UDP +7.3 %) and core locking on the 64 KB build.
   *Effort: medium.*
4. **40 MHz on 2.4 GHz.** It needs a router setting or a different access point. It
   doubles the S3's raw rate (72.2 → 150 Mbit/s), and Espressif's 84 / 99 Mbit/s S3 figures
   depend on it. The S3 firmware already asks for 40 MHz. On the C5, 40 MHz means giving up
   Wi-Fi 6 (802.11n at 150 against HE20 at 86). This is the most likely route to T3.
   *Effort: router access.*
5. **The C5 on 5 GHz, with the Mac wired to the router**, so the two hops do not share a
   channel. Espressif and Kreuzer report about 64–66 Mbit/s UDP there [2][5]. *Effort:
   a USB-C Ethernet adapter and a cable.*
6. **A fair placement.** In the final runs the S3's signal was 6–8 dB stronger than the
   C5's. Put the boards at equal signal before the deciding runs.
7. **Better instruments**, so close calls and causes are measured rather than inferred:
    - 3 repeats for decisions near the spread.
    - Per-task CPU from FreeRTOS run-time statistics.
    - The rate the radio actually used, from Wi-Fi TX statistics.
    - TCP retransmit and Wi-Fi retry counts.
    - A timestamp echo, for absolute latency.
8. **A better access point, or the TI CC3351** [9], only if T3 is needed and steps 4–5 do
   not reach it.

### The 12 Oct list, as written

Kept for the record; the status table above says what became of each item.

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

### Where this leaves the T2 question (13 Oct)

- **Both chips pass T2 over UDP** at 2.4 GHz and 20 MHz: S3 44.6, C5 54.0 Mbit/s, 0 % loss.
  This is pending the qualifying runs in step 2. By the plan's rule, if both pass T2,
  throughput does not pick the winner; 5 GHz robustness, input fit and cost do [9].
- **The S3's margin is thin**: 3.3 Mbit/s (8 %), smaller than the 19–27 % swing the same
  S3 build showed between sessions. On a worse night it could fall below T2. The C5's
  margin is 12.7 Mbit/s (31 %).
- **Over TCP neither chip passes T2** (both about 31 Mbit/s). That matters only if the
  product must use TCP.
- **Neither passes T3 (61.4) at 20 MHz.** The estimated ceiling of one 20 MHz stream,
  50–54 Mbit/s (S3) and 60–65 (C5), says T3 needs 40 MHz on the S3, or 5 GHz or better
  conditions on the C5.
- **The C5 leads at 2.4 GHz by 21 % over UDP**, helped by Wi-Fi 6's higher raw rate (86.0
  against 72.2 Mbit/s). The S3 had the stronger signal, so placement does not explain the
  lead. The S3 keeps the 40 MHz option, which the C5 cannot combine with Wi-Fi 6.
- The 12 Oct caution below, that Espressif's open-air 30 Mbit/s might apply, turned out
  pessimistic for this room. Both chips beat it.

### What outcome to expect (12 Oct, before the study)

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
- **Raw rate (PHY rate) and MCS** — the bit rate the radio signals at, before any overhead.
  It is set by the MCS (modulation and coding) index and the channel width: 72.2 Mbit/s
  for HT20 MCS7, 86.0 for HE20 MCS7.
- **Heap low-water** — the least free internal memory seen since boot. If it reaches zero,
  allocations fail and the board can drop off the network.
- **Core locking** — lwIP runs inside the task that sends, instead of in its own task, which
  saves a task switch per packet.
- **BSSID** — the hardware address of one access-point radio. One network name (SSID) can
  have several radios.

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
10. Branch `tune-loop`, `docs/tuning-results/2026-10-13-house-wifi/` (snapshot commit
    `a684edf`): `max-tput/summary.md` (per-step decisions), `max-tput.jsonl` (all 144 runs),
    `iperf/` (Espressif `iperf` on each chip, Mac-side rates), `latency/` (4.32 Mbit/s runs
    and idle round trips). Headline medians re-checked against the raw run records.
11. The full write-up of the study: Claude Doc *ESP32-S3 vs ESP32-C5 Max-Throughput Tuning
    Study* (13 Oct 2026),
    https://claude.ai/code/artifact/90a3d1da-8f7f-40f7-824a-8ec4b47ef9fb — CPU and memory
    analysis, limits per chip, latency breakdown, incident log.
12. Branch `tune-loop` (commits `4315846` to `adeeac8`): study rungs with their measured
    results (`host/rungs/s3/t24-*.yaml`, `host/rungs/c5/t24-*.yaml`), the emptied
    `r0-baseline` files, the build check that settings reached the build, the tuning
    engine `host/bench/tune.py`, and the run guide `docs/TUNING_STUDY.md`.
