# P2.1 v2.1 — ESP32-S3 vs ESP32-C5: wireless throughput comparison

**Supersedes** `P2.1_S3_vs_C5_Test_Plan.md` (2026-07-20), the notes PDF, and v2 of this file.
**Companion documents:** `S3_vs_C5_Plan_Critique_v1.md` (why this differs), `BENCH_HARNESS_SPEC.md` (how it is run).

**Changes in v2.1 (2026-09-16):**

- **No compression on the ESP.** Compression lives upstream (FPGA or STM32). The ESP is a pure
  relay in both arms. The compute-concurrent stage is deleted — see §2.1 for what this does to the
  decision.
- **The iPhone is the access point**, with the Mac wired to it over USB-C — see §7.
- **Best-effort, not matched-configuration.** Each chip is tuned independently for maximum
  performance, including with levers the other chip lacks. The environment is what is held identical.
  See §5.2 and the equal-effort protocol in §5.4.

**Bench inventory (2026-09-16):** ESP32-C5 devkit, ESP32-S3 devkit, STM32H745, MacBook Pro M1 Pro
(2021, Wi-Fi 6 client).

---

## 1. Pre-registered decision rule

Written before any data is collected, so the result cannot be interpreted after the fact.

### 1.1 Rate tiers

Application goodput, ESP → Mac, sustained 60 s, UDP loss < 0.1 %. A 128-channel RAW16 frame is 270
bytes, so sample rate *f* costs `270 × 8 × f` bit/s, divided by the compression ratio *R* achieved
**upstream** on the FPGA or STM32.

| Tier | Goodput | What it buys |
|---|---|---|
| **T0** | ≥ 5.0 Mbit/s | Rev A design point (128 ch @ 2 kS/s = 4.1 Mbit/s) + 20 % margin |
| **T1** | ≥ 20.5 Mbit/s | 10 kS/s uncompressed, or 30 kS/s at R = 3 |
| **T2** | ≥ 41.3 Mbit/s | **30 kS/s at R = 1.485** — the best ratio actually measured on Hyser data |
| **T3** | ≥ 61.4 Mbit/s | 30 kS/s uncompressed — no compression needed anywhere |

T2 is the decision tier. T0 is expected to pass everywhere and exists to catch a broken rig.

### 1.2 The rule

Evaluated at **each chip's own best** tuned, ingress-fed operating point (Stage 2) — not the pure-relay
ceiling, because pure relay is not what the product does, and not a matched configuration, because the
question is which part delivers more in a product (§5.2). The matched-configuration reading is
reported alongside it from the baseline rung.

1. **Both clear T2.** Throughput does not decide. Pick on the remaining axes (§8): band robustness,
   ingress fit, Rev A impact, size, power, cost. On present evidence that strongly favours the C5,
   and **Rev A stands**.
2. **C5 clears T2 (5 GHz only), S3 fails T2.** → **C5, and 5 GHz becomes a documented hard
   requirement**, not a preference. The C5's 2.4 GHz number is recorded as the degraded-mode figure.
3. **C5 fails T2 but S3 clears it.** → The interesting branch, and the only one where the S3 wins.
   Requires reverting Rev A's ingress to QSPI (§8). Before accepting it, check whether the C5's
   shortfall is a *tuning* shortfall (§5.3) or a real ceiling — the optimization levers
   disproportionately help the C5, because most of them cut per-packet CPU cost.
4. **Neither clears T2.** → 30 kS/s is not reachable over Wi-Fi with either part on this rig.
   Escalate to CC3351MOD (§9.3) and/or a real 802.11ax AP (§7.4) before concluding it is the
   silicon, then reclassify 30 kS/s as a wired/logged mode and fix the wireless product at T1.
5. **Parts within each other's measured spread** = a tie, reported as a tie, not a winner. Spread is
   the median absolute deviation over ≥ 3 repeats.

### 1.3 Falsifiers

- If the **Mac hotspot** caps below T2 when measured with a known-good client (§7.3), no ESP number
  above that cap is believable and the matrix is void until the rig is fixed.
- If `iperf` (Stage 0) and the custom firmware (Stage 1) disagree by more than 20 % on the same chip,
  band and transport, the firmware is the limiter and later numbers describe the firmware.
- If repeats of a cell spread more than ± 20 %, the RF environment is not controlled; re-run, do not
  average.

---

## 2. What this experiment is now

### 2.1 Removing ESP-side compression changes the shape of the decision — read this

The strongest argument for the S3 was its second core: with a codec running on-node, a dual-core part
keeps the network stack and the encoder out of each other's way, while a single-core C5 has to
interleave them. **With compression moved upstream, the S3's second core has no job.** What remains:

| | S3 | C5 |
|---|---|---|
| Bands | 2.4 only | 2.4 + 5 |
| Ingress | QSPI only (no SDIO slave) | SDIO 4-bit **and** QSPI |
| Rev A impact | Ingress must revert to QSPI | None |
| IDF maturity | Mature | Requires ≥ v6.0.2 |
| Spare core | Yes — **now unused** | n/a |

So this is now, in substance, **a 2.4 GHz vs 5 GHz band test with an SoC control**, and the expected
outcome is a C5 win. That is worth saying out loud, because it changes what the test is *for*: less a
board decision that could plausibly flip, more (a) establishing whether 30 kS/s is reachable at all,
and (b) producing a defensible number for the paper. It stays cheap to run the S3 arm, so keeping it
costs little — but do not expect it to be close.

### 2.2 The single-core question does not vanish, it shrinks

The C5's single core still runs lwIP, the Wi-Fi driver *and* the QSPI/SDIO ingest task. So there is
still a plausible single-core effect, and it is measured by exactly one derived quantity:

> **Ingress cost differential** = `(synth − ingress)` on the C5 **minus** `(synth − ingress)` on the S3.

If feeding the C5 over a wired link costs it materially more goodput than it costs the S3, the single
core is showing — without any codec needed to provoke it. This is Stage 2's job and it replaces the
deleted Stage 3.

### 2.3 One observable settles the single-core debate

Rather than arguing about it: **record CPU idle % at the knee.**

- Idle → ~0 % at the knee ⇒ the core is the limit, and the second core is a real advantage.
- Idle still > 20 % at the knee ⇒ the limit is RF, the AP or protocol overhead, and the core count is
  irrelevant to this decision.

Prior evidence points at the second case: Espressif's own C5 iperf figures (~50 Mbit/s TCP, ~64 UDP
in clean RF) are produced by that single core running essentially this workload, and both exceed T2.
So one core is probably adequate — but this metric is what turns "probably" into a measurement, and
it costs nothing to collect.

### 2.4 The two questions, in order

1. **What is each radio's ceiling on this rig?** (Stages 0–1, pure relay, no ingress.)
2. **What does the wired ingress cost, and does it cost the two parts differently?** (Stage 2.)

Anything not serving one of these is out of scope.

---

## 3. Variables

| Variable | Levels | Notes |
|---|---|---|
| `chip` | s3, c5 | |
| `band` | 2.4, 5 | S3 is 2.4-only — stated on every S3 row, not left blank |
| `source` | synth, qspi, sdio | `synth` = paced generator inside the ESP, no wiring. `sdio` is **C5-only** |
| `transport` | udp, tcp | |
| `tune` | baseline, tuned | Applied symmetrically to both chips |
| `offered_load` | swept | Inner loop, ~8 steps from 1 Mbit/s past the knee |
| `repeat` | 3 | Median + spread; never a bare mean |

**The common comparison link is QSPI**, because the S3 has no SDIO slave. SDIO is a C5-only
*extension* arm, compared only against the C5's own QSPI number (same chip, clean comparison), never
directly against an S3 number.

---

## 4. Stage gating

### 4.1 Stage 0 — rig validation and radio ceiling (no custom firmware)

Stock ESP-IDF `iperf` example, both chips, both bands, TCP and UDP, ESP transmitting.
6 cells × 3 repeats = 18 runs, scripted, ~2 hours unattended.

**Validate the hotspot first** (§7.3). If the Mac's hotspot caps below T2, stop and fix the rig.

*Gate:* if both chips are below T1 here, custom firmware cannot rescue them — go to §9.3.

### 4.2 Stage 1 — paced synthetic source, swept (custom firmware, no wiring)

The ESP generates its own counter-stamped frames at a commanded rate. Isolates the Wi-Fi stack under
*our* firmware and is the control arm every later number is subtracted from.

`chip/band` (3) × `transport` (2) × `tune` (2) = **12 cells**, each swept over ~8 offered loads,
× 3 repeats. Unattended overnight.

*Gate:* tuned Stage 1 must come within 20 % of Stage 0 iperf, or the firmware is the limiter.

### 4.3 Stage 2 — real ingress (the decision stage)

**2a — QSPI (the comparable arm).** H745 QUADSPI indirect-write master → ESP GP-SPI Slave HD, 4-line
data phase, with a mandatory slave→master ready GPIO. Tuned configs only.
`chip/band` (3) × `transport` (2) = **6 cells**.

**2b — SDIO 4-bit (C5-only).** H745 SDMMC host → C5 SDIO slave. `band` (2) × `transport` (2) =
**4 cells**. Reported as `c5_sdio − c5_qspi`.

Before 2a: read the QSPI phase table (command byte, address width, dummy cycles, data-line count)
out of the **installed** ESP-IDF source and configure the H745 field for field. A one-cycle dummy
mismatch corrupts silently and looks like a radio problem.

### 4.4 Stage 3 — deleted

Was the compute-concurrent codec track. Removed: compression happens upstream. **CPU idle % is still
recorded on every run** (§5), because it is the evidence behind §2.2.

---

## 5. Metrics per cell

| Metric | UDP | TCP |
|---|---|---|
| Sustained goodput at threshold | ✅ | ✅ (saturated) |
| Loss % vs offered load, and the knee | ✅ | ✗ — TCP does not lose |
| Retransmits | ✗ | ✅ |
| Latency p50 / p99 (`t_src` → arrival) | ✅ | ✅ |
| Jitter | ✅ | ✅ |
| ESP CPU idle %, per core on the S3 | ✅ | ✅ |
| Wi-Fi retries, negotiated PHY rate, RSSI | ✅ | ✅ |
| Max 128-ch sample rate = `goodput / (270 × 8)` | ✅ | ✅ |
| 5-minute stability hold (tuned cells) | ✅ | ✅ |

**Loss is only meaningful against a swept offered load.** Loss measured while flooding is
self-fulfilling and is not reported.

### 5.1 Derived quantities — what actually gets written up

| Quantity | Computed as | Isolates |
|---|---|---|
| Band effect | `c5@5 − c5@2.4` | Band, same silicon |
| SoC effect | `c5@2.4 − s3@2.4` | Silicon, same band |
| Ingress cost | `synth − ingress`, per chip | The wired link |
| SDIO advantage | `c5_sdio − c5_qspi` | Link type, same chip |
| **Ingress cost differential** | C5 ingress cost − S3 ingress cost | **The single-core effect (§2.2)** |
| **CPU idle % at the knee** | C5 idle %, measured at its own knee | **Whether the core is the limit at all (§2.3)** |

### 5.2 What is held constant, and what is not

**This is a best-effort (envelope) comparison, not a matched-configuration one.** Each chip is tuned
independently, as hard as it will go, including with levers the other chip does not have. The question
is "which part delivers more in a product", not "which part is faster at a fixed configuration".

That is the right question for a board decision, but it moves the burden: with configuration no longer
controlled, **the environment carries the entire comparison**, and the effort spent tuning becomes an
experimental variable in its own right (§5.4).

**Identical across every run — a violation makes the run invalid, not merely noisy:**

1. **The rig:** AP, band and channel (where the chip supports it), channel width, placement, board
   orientation, distance, RSSI within 3 dB, the receiving PC and its link, ambient RF.
2. **The measurement:** frame format, offered-load sweep definition, hold and discard times, duration,
   repeat count, loss threshold, and **the point in the pipeline at which goodput is counted**. If one
   build counts bytes at a different place than the other, the numbers are not comparable no matter
   how well each chip is tuned.
3. **The toolchain:** one ESP-IDF version for both targets, ≥ v6.0.2 (the C5's floor), recorded per run.
4. **The payload:** random bytes under a counter-stamped `hdemg_frame.h` header (`seq`, `t_src`).
5. **Power-save off** on both, and TX power at each chip's maximum.

**Deliberately different — each chip's own best:**

- Every `sdkconfig` lever, chosen per chip by measured gain (§5.3).
- Buffer pool geometry, frame size, and queue depths, if a chip goes faster with different ones.
- Task placement and core pinning — an S3-only lever, and its only remaining structural advantage.
- PSRAM: a **lever to test on the S3**, not a confound to suppress. It must still be declared per run,
  since it changes what the number means.
- Band: the C5 is tested on both; the S3 has only 2.4 GHz. That asymmetry is the finding, not a flaw.

**Both readings are available at no extra cost.** The matrix already runs `baseline` and `tuned` for
every cell (§3). `baseline` stays a common, stock configuration on both chips — so baseline-vs-baseline
is the *matched-configuration* comparison that isolates the silicon, and tuned-vs-tuned is the
*best-effort* comparison that answers the board question. Report both. The difference between them is
itself informative: it says how much of each part's performance is free and how much has to be earned.

### 5.4 Equal-effort protocol — the thing that makes a best-effort comparison defensible

The failure mode of a best-effort comparison is banal: twenty hours of tuning on one chip and six on
the other, and the result measures attention rather than silicon. "Max performance" is an unbounded
target, so the stopping rule has to be written down in advance.

1. **One lever list, attempted on both** (§5.3 common levers), plus each chip's exclusive levers.
   A common lever is attempted on both chips even if it is expected to help only one.
2. **One lever at a time**, measured, recorded as a rung with its measured gain. Rungs that lose are
   kept in the ladder with their negative result — they are evidence of effort.
3. **Stop rule, applied per chip independently:** stop when three consecutive levers each yield under
   2 % on that chip. A chip that keeps gaining keeps climbing; a chip that plateaus stops. This is what
   makes unequal *rung counts* legitimate while keeping effort equal.
4. **Publish both ladders.** The tuning ladder per chip is a deliverable, not a working note. It is
   the only available answer to "did you try as hard on the S3?", and a reviewer will ask.
5. If one chip's ladder is materially shorter and it *lost*, that result is reported as provisional
   until the ladder is extended.

### 5.3 Tuning levers

**Common — attempted on both chips:** CPU at maximum clock · `-O2`
(`COMPILER_OPTIMIZATION_PERF`) · logging off · AMPDU enabled with a larger TX block-ack window ·
larger Wi-Fi TX/RX buffer counts · larger lwIP TCP window and send buffer · `LWIP_TCPIP_CORE_LOCKING`
(and `..._INPUT`) · IRAM placement (`ESP_WIFI_IRAM_OPT`, `ESP_WIFI_RX_IRAM_OPT`,
`LWIP_IRAM_OPTIMIZATION`) · `SPI_SLAVE_ISR_IN_IRAM` · zero-copy DMA into the pipe buffer ·
`TCP_NODELAY` · static vs dynamic Wi-Fi TX buffers · MTU and frame-size sweep.

**S3-only:** task/core placement — `ESP_WIFI_TASK_CORE_ID` and `LWIP_TCPIP_TASK_AFFINITY` on separate
cores, app task on the third slot; ISR core routing for the ingress DMA; **PSRAM** for large Wi-Fi and
lwIP buffers, if the devkit has it (PSRAM is slower than internal SRAM, so this can lose — measure).

Note that `LWIP_TCPIP_CORE_LOCKING` and dual-core task separation partly work against each other: core
locking does protocol work in the calling task's context, which collapses the app/lwIP split. Both are
on the list; the ladder decides which wins on the S3, and that interaction is worth reporting.

**C5-only:** 5 GHz band · 802.11ax/HE-specific settings and any HE-related BA window or aggregation
tuning · SDIO 4-bit ingress (Stage 2b).

**Not valid on C5 / IDF v6** — these fail the build rather than helping: `CONFIG_ESP_INTR_IN_IRAM`,
`CONFIG_ESP_PHY_IRAM_OPT`.

---

## 6. What changed from the notes PDF

| Notes PDF | This plan | Why |
|---|---|---|
| Espressif's 30/20 tables as the comparison basis | Sanity floor only | The two tables are identical — generic doc figures, not part-differentiating |
| Direction unstated ("TX/RX") | Direction on every number (ESP TX) | The trap that nearly falsified the C5 once already |
| 8 C5 cells vs 2 S3 cells | Symmetric; `band` explicit on S3 rows | Most original cells varied two things at once |
| H745 at maximum rate | Swept offered load, `synth` control arm first | One saturated number has no knee and no attribution |
| "Packet loss rate" on TCP rows | Loss for UDP, retransmits for TCP | TCP does not lose |
| No repeats | ≥ 3, median + spread | RF moves ± 20 % between runs |
| No decision rule | §1, pre-registered | Otherwise the result is interpreted after the fact |

---

## 7. RF rig — iPhone as access point

```
ESP32 (STA) ──── the only wireless hop ────► iPhone 17 Pro (hotspot) ──USB-C──► MacBook Pro (receiver)
```

### 7.1 The Mac must be wired to the iPhone, not joined over Wi-Fi

**If both the ESP and the Mac join the hotspot over Wi-Fi, every frame crosses the air twice on the
same radio and the same channel** — ESP → iPhone, then iPhone → Mac. Those two transmissions share
one medium and contend for the same airtime, so throughput roughly **halves** and latency roughly
doubles. That is the standard wireless-repeater penalty; it is a property of the topology, not of the
ESP, and it would silently deflate every number in the matrix.

**Fix:** connect the Mac to the iPhone with **Personal Hotspot over USB-C**. The ESP is then the only
Wi-Fi client, the ESP → iPhone hop is the only wireless hop, and the iPhone → Mac hop is wired. As a
bonus this needs no Ethernet adapter — just the cable.

*Verify on the bench:* that iOS bridges traffic between a Wi-Fi hotspot client and the USB client
(it should — both sit on the hotspot's bridged subnet — but confirm it before planning around it).

### 7.2 Band selection

iPhone Personal Hotspot runs 5 GHz by default on modern iPhones. **Settings → Personal Hotspot →
Maximize Compatibility forces it to 2.4 GHz.** So the band switch is one toggle: off = 5 GHz,
on = 2.4 GHz. Manual, but with band-ordered scheduling (`BENCH_HARNESS_SPEC.md` §3.6) it happens at
most twice per stage.

No channel or channel-width selection is exposed. Record both from the ESP side every run.

### 7.3 Constraints this imposes

**(a) 90-second idle disconnect.** Apple documents that third-party devices are automatically
disconnected from Personal Hotspot after 90 seconds without traffic. An unattended sweep has gaps
between runs — build/flash, metadata capture, gate checks — that can exceed this. **The harness must
run a low-rate keepalive between runs**, and must treat a re-association as a metadata event, not
silently absorb it. This is exactly the kind of thing that kills an overnight sweep at 2 a.m.

**(b) Protocol is undocumented.** Apple publishes no 802.11 standard for hotspot AP mode, and there
is longstanding user speculation that it does not run 802.11ax as an AP even on Wi-Fi 6/7 phones. If
it does not, the C5's Wi-Fi 6 rates are not exercised and its number is a **lower bound**. This is
settled empirically, not by argument: **the harness records the negotiated PHY rate and mode on every
run** (`BENCH_HARNESS_SPEC.md` §1.5), which says directly which protocol was used. Until that reads
HE, the result is described as "5 GHz vs 2.4 GHz", never as "Wi-Fi 6 vs Wi-Fi 4".

**(c) Thermal and battery drift.** Sustaining 40+ Mbit/s of AP traffic for hours warms the phone and
drains it. Thermal throttling of the radio partway through a sweep would look like a slowly
degrading ESP. Mitigations: keep the phone on charge and cool, order repeats so a given cell's three
repeats are **not** consecutive, and treat a monotonic downward drift across a stage as a rig fault
rather than a finding.

**(d) Hotspot requires cellular.** Personal Hotspot generally needs an active cellular connection to
enable. ESP → Mac traffic stays on the local subnet and should not consume cellular data, but confirm
this on the first run rather than discovering it on a bill.

### 7.4 Hotspot validation — mandatory before Stage 0

Measure the hotspot's own ceiling with a known-good client: a second laptop or tablet on Wi-Fi
running `iperf3` to the USB-connected Mac, on each band. That number is the roof over every ESP
measurement. If it is below T2, the rig — not the ESP — is the limiter, and §1.3 voids the matrix.

Record it as `rig_ceiling_mbps` on every run so any future reader can see what the ESP numbers were
measured against.

### 7.5 Fallback: MacBook as the access point

If the iPhone proves unstable over long sweeps (§7.3a, §7.3c), fall back to macOS Internet Sharing
with the Mac as AP and the ESP as the only client. That topology also has one wireless hop, and it
**does** expose channel selection including 5 GHz channels 36/40/44/48 (UNII-1, non-DFS), which the
iPhone does not. Its costs: it needs a USB-C → Ethernet adapter as the "share from" interface (macOS
cannot share Wi-Fi to Wi-Fi), the Mac is then AP and endpoint on one radio, and macOS AP mode is
likely 11ac rather than 11ax.

Neither rig is clearly better. The iPhone wins on channel-free simplicity and possibly on protocol;
the Mac wins on channel control and stability. Start with the iPhone, keep this in reserve, and let
§7.4 decide.

### 7.6 When a real AP becomes necessary

Buy or borrow a dual-band 802.11ax AP (~$60-120) if §7.4 shows the hotspot capping below T2, or if
the negotiated PHY rate shows no HE **and** the paper needs an absolute C5 ceiling rather than a
comparison. For the board decision, either mobile rig is sufficient. For a published ceiling, neither
is.

### 7.7 Reverse topology is not a fallback

Running the **ESP as SoftAP** with the Mac joining it does not give a 5 GHz path: Espressif documents
that in SoftAP mode the C5 may not operate on DFS channels for regulatory compliance, and the
non-DFS SoftAP case is not clearly documented. Treat ESP-as-AP as 2.4 GHz-only.

### 7.8 Placement and hygiene

Fixed 1-2 m line of sight between ESP and phone, same spot every run, RSSI > -50 dBm and recorded.
Bluetooth off on both Mac and phone during 2.4 GHz runs. Hands, bodies and metal away from the
antennas; consistent board and phone orientation. Ambient scan captured every run.

## 8. Non-bench axes (scored, not measured)

Decides §1.2 branch 1.

| Axis | S3 | C5 |
|---|---|---|
| Bands | 2.4 only | 2.4 + 5 (Wi-Fi 6) |
| Ingress | QSPI Slave HD; **no SDIO slave** | SDIO 4-bit slave **and** Slave HD |
| Cores | 2 — **no longer used for anything** | 1 (+ LP core) |
| **Rev A impact if chosen** | **Ingress reverts to QSPI: block diagram, pin plan and FPGA ingress RTL all change** | None |
| Module size | — | C5-WROOM-1: 18 × 27.5 × 3.3 mm (495 mm²) |
| IDF maturity | Mature | Requires ≥ v6.0.2; newest silicon |
| Power at full TX | Pull from datasheet | Pull from datasheet |

---

## 9. Open items

### 9.1 Blocking

1. **Confirm iOS bridges Wi-Fi-client ↔ USB-client traffic** on Personal Hotspot (§7.1). If it does
   not, switch to the Mac-as-AP fallback (§7.5) and buy the Ethernet adapter.
2. **Hotspot ceiling measurement** with a known-good client, per band (§7.4).
3. **S3 devkit variant** — exact part number; PSRAM present or not. Live confound (§5.2).
4. **STM32H745 board** — Nucleo-H745ZI-Q or the Rev-1 PCB? Decides the QSPI break-out and whether a
   ready GPIO exists.

### 9.2 Before Stage 2

5. QSPI phase table read from the installed ESP-IDF source.
6. Ready-GPIO wiring, and the variable-length-vs-fixed-DMA framing decision.
7. USB hub with per-port power switching (`uhubctl`, ~$40) — the last manual step in an unattended
   sweep is recovering a wedged board.

### 9.3 Deferred, not dismissed

8. **CC3351MOD as a third arm.** The only other sub-$80 dual-band Wi-Fi 6 part with SDIO ingress and
   an FCC modular grant; claims 50 Mbps application throughput (no conditions published — treat as
   unverified); 121 mm², 4× smaller than the C5-WROOM; $48.75 EVM. Triggered by §1.2 branch 4, and
   worth a paragraph either way.
9. **ESP-Hosted-MCU as a reference-implementation arm.** A maintained, third-party-measured version
   of this exact pipe; running it on the same rig removes "your firmware is the limit" as an
   explanation for a low number. Cheap, and recommended before Stage 2.
10. **A real 802.11ax AP** (§7.4), if the paper needs an absolute ceiling rather than a comparison.

---

## 10. Deployment topology — how the finished device gets used

The test rig is chosen to match the intended deployment. This section states what that deployment can
and cannot be, because it is a hard constraint on the 30 kS/s mode and it belongs in the product spec,
not only in the test plan.

### 10.1 The rule — airtime, not "AP bandwidth"

In **infrastructure mode** — the normal case, where the device is a station on someone's Wi-Fi
network — all client-to-client traffic is relayed by the access point. Two stations cannot talk
directly. So if the PC is *also* a Wi-Fi station on the same radio and channel, every frame is
transmitted twice on that channel: device → AP, then AP → PC.

**What those two transmissions share is airtime, not a bandwidth pool.** Wi-Fi is half-duplex on a
single channel: one device transmits at a time. An AP advertising "300 Mbit/s" is quoting the *speed*
of its transmissions, not a capacity that can be split. Carrying a given bit rate over a fast link
therefore costs less airtime than carrying it over a slow one, and the relay penalty depends entirely
on how fast the second hop is.

If each hop's standalone goodput capacity is C₁ (device → AP) and C₂ (AP → PC), end-to-end throughput
is approximately

```
T  ≈  min( the device's own cap,  1 / (1/C₁ + 1/C₂) )
```

- **C₁ = C₂** → T = C/2. This is the classic "halving", and it holds only when both hops run at the
  same rate. It is the worst case, not the general case.
- **C₂ ≫ C₁** → the second term approaches C₁, and the device's own limit governs. **The relay costs
  almost nothing.**

Worked example. Suppose the C5 negotiates HE20 at ~103 Mbit/s PHY (≈ 65 Mbit/s of real goodput
capacity) and is CPU-limited to 40 Mbit/s:

| Hop | Capacity | Airtime to carry 40 Mbit/s |
|---|---|---|
| ESP → AP | ~65 Mbit/s | ~60 % |
| AP → PC, fast 2×2 HE80 client | ~600 Mbit/s | ~7 % |
| | | **~67 % total — fits; the device sees its full 40** |
| AP → PC, weak client | ~50 Mbit/s | ~80 % |
| | | **over budget; end-to-end falls to ≈ 28 Mbit/s** |

Two further effects: above roughly 70–80 % total airtime, CSMA/CA efficiency degrades and latency and
jitter climb sharply; and when the budget is exceeded, the shortfall appears as the **AP's downstream
queue overflowing**, which looks like packet loss at the PC even though the device's own link was
healthy.

802.11 does define a direct-link mode for this case (**TDLS**, 802.11z), but ESP-IDF does not
implement it and AP support is rare. Treat it as unavailable.

### 10.2 The four topologies

| # | Device | PC | Relay cost | Verdict |
|---|---|---|---|---|
| A | STA on AP | **Ethernet** to the same router | None — one air hop | **Best. Use this for 30 kS/s.** |
| B | STA on AP's 5 GHz radio | STA on the AP's 2.4 GHz radio | None — hops on different channels, simultaneous radios | Good. Both wireless, slightly higher latency |
| C | STA on AP | STA on the same AP, same band | Per §10.1: negligible if the PC's link is much faster, down to 50 % if the two links match | Fine at 2 kS/s; **unreliable at 30 kS/s** |
| D | **Device is the AP** (SoftAP) | STA on the device | None — one air hop | No infrastructure needed, but **2.4 GHz only** on these parts |

Topology B requires an AP whose radios operate simultaneously, which nearly all dual-band APs do.

### 10.3 The design rule this produces

- **128 ch @ 2 kS/s = 4.1 Mbit/s.** Even the worst case in topology C leaves several times the
  headroom. **Every topology works.** Do not over-constrain the deployment for this mode.
- **128 ch @ 30 kS/s = 61.4 Mbit/s raw, 41.3 at R = 1.485.** Topology C *can* carry this — but only
  if the PC's link happens to be much faster than the device's, and **C₂ is not under our control in
  the field**. A laptop two rooms away silently cuts throughput by a third or more. So A or B is a
  **documented requirement** of the high-rate mode, not a recommendation, and the reason to state is
  guaranteed airtime budget rather than "Wi-Fi halves throughput".

### 10.4 Two practical constraints on institutional networks

**Client isolation.** Managed networks — university, corporate, most guest Wi-Fi — commonly block
client-to-client traffic outright. On such a network the device cannot reach the PC at all, halved or
otherwise. A lab deployment realistically means a small dedicated AP, a wired PC, or SoftAP — not the
campus network.

**WPA2-Enterprise (802.1X).** Institutional Wi-Fi usually requires it. ESP-IDF supports it, but
provisioning certificates or credentials onto a wearable is a real piece of work, and it is worth
deciding early whether the device is ever expected to join such a network.

### 10.5 Why the test rig matches

The Stage 0–2 rig — ESP as station on the iPhone hotspot, Mac wired to the phone over USB-C — is
topology **A**: one wireless hop, wired PC. So the measured numbers describe the deployment the
device is actually specified for, rather than a best case it will never see.
