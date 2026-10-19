# Campaign followup-2026-10

_updated 2026-10-18 05:52:54 PDT_

34 done · 2 failed · 0 skipped · 1 pending (~32 min left)

| test | status | what | note |
|---|---|---|---|
| W.1 | done | Wiring hello world: all 18 Stage 2 wires, both directions |  |
| 0.1 | done | Rig check (2.4 GHz block) |  |
| 0.2 | done | Both ESPs boot and can be flashed with the Stage 2 harness on |  |
| 0.3 | done | Reference: each chip's design, TCP + UDP, 2 x 20 s, saturating |  |
| 1.1 | done | TCP: Nagle off vs on, today's writes (8,100 B) |  |
| 1.2 | failed | TCP: 8,100 B vs 540 B writes, Nagle off | delivered almost nothing: esp32c5 wr-540 tcp at 60.0 Mbit/s gave 0.02; esp32s3 wr-540 tcp at 60.0 Mbit/s gave  |
| 1.3 | done | UDP: 1,350 B vs 540 B datagrams |  |
| 2.1 | done | C5 P0: PSRAM on, nothing moved into it (control) |  |
| 2.2 | done | C5 P1: buffer pool in PSRAM |  |
| 2.3 | done | C5 P2: Wi-Fi and lwIP buffers in PSRAM |  |
| 2.4 | done | C5 P3: P2 plus A-MSDU |  |
| 2.5 | done | S3 P1: buffer pool in PSRAM |  |
| 2.6 | done | S3 P2: Wi-Fi and lwIP buffers in PSRAM, no A-MSDU |  |
| 2.7 | done | S3 P3: P2 plus A-MSDU (A-MSDU alone cannot be built) |  |
| 2.8 | done | 10-minute memory check of each chip's kept PSRAM setup, UDP 41.3 |  |
| 4.1 | done | S3: 20 vs 40 MHz, TCP + UDP, 3 x 60 s at 120 offered |  |
| 4.2 | done | C5: Wi-Fi 6 at 20 MHz vs 802.11n at 40 MHz |  |
| 4.3 | done | UDP load sweep, each chip's 40 MHz build, 10-120 Mbit/s |  |
| 0.4 | done | Mac to the 2.4 GHz network; its link (the second hop) |  |
| 5.2 | done | C5 at 5 GHz: Wi-Fi 6 at 20 MHz, TCP + UDP, 3 x 60 s |  |
| 5.3 | done | C5 at 5 GHz: 802.11n at 40 MHz |  |
| 5.4 | done | C5 at 5 GHz: Wi-Fi 6 at 20 MHz with MCS 8-9 |  |
| 5.5 | done | C5 at 5 GHz: UDP load sweep of the best of 5.2-5.4 |  |
| 5.6 | done | C5 at 5 GHz with the Mac also on 5 GHz (shared channel) |  |
| 3.0 | done | Block B start: rig check and reference |  |
| 3.1 | done | Soak: UDP at 4.32 Mbit/s, 30 min, both boards at once |  |
| 3.2 | failed | Soak: TCP at 4.32 Mbit/s, 30 min, both boards at once | delivered almost nothing: esp32c5 soaktcp tcp at 4.32 Mbit/s gave 0.00 |
| 3.3 | done | Soak: UDP at 41.3 Mbit/s, 30 min, S3 then C5 |  |
| 3.4 | done | Soak: TCP offered 41.3 Mbit/s (constant back-pressure), 30 min, S3 then C5 |  |
| 3.5 | done | Stress: TCP at 4.32, 30 min, both boards — read pauses, firewall block, reset, link drop |  |
| 3.6 | done | Stress: UDP at 4.32, 30 min, both boards — link drops 1/5/30 s |  |
| 3.7 | done | Leak check: 500 start/stop cycles, both boards |  |
| 6.0 | done | Block C start: rig check and reference |  |
| 6.1 | done | STM32: build and flash both cores; hello; pacing with no link |  |
| 6.2 | done | Link up: one 20 s link-only run per arm at 41.3 Mbit/s |  |
| 6.3 | done | S3: built-in generator vs QSPI input (best setup) |  |
| 6.4 | running | C5: built-in generator vs QSPI vs SDIO input (best setup) |  |
| 6.5 | pending | C5 QSPI in 15-frame batches (SDIO's limit), UDP at 41.3 |  |

## Best wireless setup per chip (used by Stage 2)

- **esp32c5**: `c-c5-ht40` at 5 GHz — UDP 50.8 Mbit/s (5.3), best of c-c5-base@2.4=42.4, c-c5-ht40@2.4=9.9, c-c5-base@5=18.6, c-c5-ht40@5=50.8, c-c5-mcs9@5=20.6
- **esp32s3**: `c-s3-base` at 2.4 GHz — UDP 37.6 Mbit/s (4.1), best of c-s3-base@2.4=37.6, c-s3-ht40@2.4=42.0

## Kept PSRAM setup (used by the soak tests)

- **esp32c5**: none — no PSRAM variant qualified
- **esp32s3**: none — no PSRAM variant qualified

## W.1 — Wiring hello world: all 18 Stage 2 wires, both directions

| # | Nucleo | H7 pin | ESP | carries | H7 drives -> ESP sees | ESP drives -> H7 sees | verdict |
|---|---|---|---|---|---|---|---|
| 1 | CN10-15 | PB2 | esp32s3 GPIO12 | QSPI CLK | esp32s3 GPIO12 | PB2 | ok |
| 2 | CN10-13 | PG6 | esp32s3 GPIO10 | QSPI CS | esp32s3 GPIO10 | PG6 | ok |
| 3 | CN10-23 | PD11 | esp32s3 GPIO11 | QSPI D0 | esp32s3 GPIO11 | PD11 | ok |
| 4 | CN10-21 | PD12 | esp32s3 GPIO13 | QSPI D1 | esp32s3 GPIO13 | PD12 | ok |
| 5 | CN10-25 | PE2 | esp32s3 GPIO14 | QSPI D2 | esp32s3 GPIO14 | PE2 | ok |
| 6 | CN10-19 | PD13 | esp32s3 GPIO9 | QSPI D3 | esp32s3 GPIO9 | PD13 | ok |
| 7 | CN7-20 | PG9 | esp32s3 GPIO7 | READY | esp32s3 GPIO7 | PG9 | ok |
| 8 | CN10-9 | PF10 | esp32c5 GPIO6 | QSPI CLK | esp32c5 GPIO6 | PF10 | ok |
| 9 | CN8-12 | PD2 | esp32c5 GPIO10 | SDIO CMD / QSPI CS | esp32c5 GPIO10 | PD2 | ok |
| 10 | CN8-4 | PC9 | esp32c5 GPIO7 | SDIO D1 / QSPI D0 | esp32c5 GPIO7 | PC9 | ok |
| 11 | CN9-28 | PF9 | esp32c5 GPIO2 | QSPI D1 | esp32c5 GPIO2 | PF9 | ok |
| 12 | CN9-26 | PF7 | esp32c5 GPIO5 | QSPI D2 | esp32c5 GPIO5 | PF7 | ok |
| 13 | CN10-7 | PF6 | esp32c5 GPIO4 | QSPI D3 | esp32c5 GPIO4 | PF6 | ok |
| 14 | CN8-14 | PG10 | esp32c5 GPIO3 | READY | esp32c5 GPIO3 | PG10 | ok |
| 15 | CN8-10 | PC12 | esp32c5 GPIO9 | SDIO CLK | esp32c5 GPIO9 | PC12 | ok |
| 16 | CN8-2 | PC8 | esp32c5 GPIO8 | SDIO D0 | esp32c5 GPIO8 | PC8 | ok |
| 17 | CN8-6 | PC10 | esp32c5 GPIO14 | SDIO D2 | esp32c5 GPIO14 | PC10 | ok |
| 18 | CN8-8 | PC11 | esp32c5 GPIO13 | SDIO D3 | esp32c5 GPIO13 | PC11 | ok |

## 0.1 — Rig check (2.4 GHz block)


## 0.2 — Both ESPs boot and can be flashed with the Stage 2 harness on

- **esp32s3**: `{"esptool": "ok", "hello": {"band": "2.4", "chip": "esp32s3", "fw_sha": "6866ca5", "idf": "v6.0-dev-2980-gab149384e1-dirty", "ingress": "synth", "ok": true, "proto": 2, "rung": "c-s3-base"}, "mode": "SPI_FAST_FLASH_BOOT", "ok": true, "rom_line": "yes"}`
- **esp32c5**: `{"esptool": "ok", "hello": {"band": "2.4", "chip": "esp32c5", "fw_sha": "6866ca5", "idf": "v6.0-dev-2980-gab149384e1-dirty", "ingress": "synth", "ok": true, "proto": 2, "rung": "c-c5-base"}, "mode": "SPI_FAST_FLASH_BOOT", "ok": true, "rom_line": "yes"}`

## 0.3 — Reference: each chip's design, TCP + UDP, 2 x 20 s, saturating

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | ref | tcp | 60.0 | 16.79 (±2.16) | 0.000 | 22336 | 13082/116772 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32c5 | ref | udp | 60.0 | 38.13 (±1.40) | 0.000 | 22336 | 7391/39083 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32s3 | ref | tcp | 60.0 | 30.31 (±0.05) | 0.000 | 10232 | 9547/39697 | HT ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32s3 | ref | udp | 60.0 | 38.09 (±0.12) | 0.000 | 10232 | 13279/44742 | HT ch1 bw20 sta20 ap:b2c0fe | 2 |

## 1.1 — TCP: Nagle off vs on, today's writes (8,100 B)

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | nagle-off | tcp | 60.0 | 16.84 (±1.62) | 0.000 | 22336 | 15361/55147 | HE ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32c5 | nagle-on | tcp | 60.0 | 18.98 (±6.24) | 0.000 | 21900 | 12624/51592 | HE ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32s3 | nagle-off | tcp | 60.0 | 17.55 (±9.75) | 0.000 | 9760 | 26286/67912 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32s3 | nagle-on | tcp | 60.0 | 16.48 (±10.15) | 0.000 | 9760 | 26351/71543 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |

## 1.2 — TCP: 8,100 B vs 540 B writes, Nagle off

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | wr-540 | tcp | 60.0 | 0.02 (±0.00) | 0.000 | 21900 | 37304/713474 | HE ch1 bw20 sta20 ap:b2c0fe | 1 (+4 invalid) |
| esp32s3 | wr-540 | tcp | 60.0 | 0.00 (±0.00) | 0.000 | 12952 | 29687/370234 | HT ch1 bw20 sta20 ap:b2c0fe | 1 (+4 invalid) |

## 1.3 — UDP: 1,350 B vs 540 B datagrams

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | dg-1350 | udp | 60.0 | 24.74 (±7.34) | 0.000 | 7648 | 14978/82052 | HE ch1 bw20 sta20 ap:b2c0fe | 3 (+3 invalid) |
| esp32c5 | dg-540 | udp | 60.0 | 16.36 (±1.31) | 0.000 | 7648 | 17355/52374 | HE ch1 bw20 sta20 ap:b2c0fe | 3 (+3 invalid) |
| esp32s3 | dg-1350 | udp | 60.0 | 22.83 (±0.64) | 0.000 | 2440 | 32815/88438 | HT ch1 bw20 sta20 ap:b2c0fe | 3 (+2 invalid) |
| esp32s3 | dg-540 | udp | 60.0 | 22.98 (±1.49) | 0.010 | 2440 | 19521/61816 | HT ch1 bw20 sta20 ap:b2c0fe | 3 (+2 invalid) |

## 2.1 — C5 P0: PSRAM on, nothing moved into it (control)

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | p0 | tcp | 60.0 | 13.61 (±0.50) | 0.000 | 8339 | 18860/67141 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32c5 | p0 | udp | 60.0 | 23.09 (±1.35) | 0.000 | 8339 | 13969/57955 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |

## 2.2 — C5 P1: buffer pool in PSRAM

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | p1 | tcp | 60.0 | 10.43 (±0.30) | 0.000 | 50447 | 26563/138543 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32c5 | p1 | udp | 60.0 | 22.17 (±1.46) | 0.000 | 50447 | 15890/79615 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |

## 2.3 — C5 P2: Wi-Fi and lwIP buffers in PSRAM

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | p2 | tcp | 60.0 | 11.26 (±0.38) | 0.000 | 93387 | 24177/90474 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32c5 | p2 | udp | 60.0 | 12.82 (±0.35) | 0.000 | 93387 | 44632/117279 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |

## 2.4 — C5 P3: P2 plus A-MSDU

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | p3 | tcp | 60.0 | 11.96 (±0.24) | 0.000 | 93663 | 22383/86126 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32c5 | p3 | udp | 60.0 | 13.69 (±0.73) | 0.000 | 93663 | 41841/102881 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |

## 2.5 — S3 P1: buffer pool in PSRAM

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32s3 | p1 | tcp | 60.0 | 18.03 (±0.26) | 0.000 | 52515 | 23906/79736 | HT ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32s3 | p1 | udp | 60.0 | 26.48 (±1.40) | 0.000 | 52515 | 21607/90367 | HT ch1 bw20 sta20 ap:b2c0fe | 2 |

## 2.6 — S3 P2: Wi-Fi and lwIP buffers in PSRAM, no A-MSDU

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32s3 | p2 | tcp | 60.0 | 12.49 (±0.09) | 0.000 | 154343 | 47434/100250 | HT ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32s3 | p2 | udp | 60.0 | 18.73 (±0.52) | 0.000 | 154343 | 28757/78844 | HT ch1 bw20 sta20 ap:b2c0fe | 2 |

## 2.7 — S3 P3: P2 plus A-MSDU (A-MSDU alone cannot be built)

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32s3 | p3 | tcp | 60.0 | 10.20 (±0.07) | 0.000 | 154343 | 61804/131507 | HT ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32s3 | p3 | udp | 60.0 | 16.86 (±0.73) | 0.000 | 154343 | 35154/75987 | HT ch1 bw20 sta20 ap:b2c0fe | 2 |

## 2.8 — 10-minute memory check of each chip's kept PSRAM setup, UDP 41.3


<details><summary>kept</summary>

```json
{
 "esp32c5": {
  "candidates": [
   {
    "heap_gain_B": -13997,
    "losses": [
     "tcp 13.6 vs 16.8",
     "udp 23.1 vs 38.1"
    ],
    "rung": "c-c5-p0",
    "test": "2.1"
   },
   {
    "heap_gain_B": 28111,
    "losses": [
     "tcp 10.4 vs 16.8",
     "udp 22.2 vs 38.1"
    ],
    "rung": "c-c5-p1",
    "test": "2.2"
   },
   {
    "heap_gain_B": 71051,
    "losses": [
     "tcp 11.3 vs 16.8",
     "udp 12.8 vs 38.1"
    ],
    "rung": "c-c5-p2",
    "test": "2.3"
   },
   {
    "heap_gain_B": 71327,
    "losses": [
     "tcp 12.0 vs 16.8",
     "udp 13.7 vs 38.1"
    ],
    "rung": "c-c5-p3",
    "test": "2.4"
   }
  ],
  "rung": null,
  "why": "no PSRAM variant qualified"
 },
 "esp32s3": {
  "candidates": [
   {
    "heap_gain_B": 42283,
    "losses": [
     "tcp 18.0 vs 30.3",
     "udp 26.5 vs 38.1"
    ],
    "rung": "c-s3-p1",
    "test": "2.5"
   },
   {
    "heap_gain_B": 144111,
    "losses": [
     "tcp 12.5 vs 30.3",
     "udp 18.7 vs 38.1"
    ],
    "rung": "c-s3-p2",
    "test": "2.6"
   },
   {
    "heap_gain_B": 144111,
    "losses": [
     "tcp 10.2 vs 30.3",
     "udp 16.9 vs 38.1"
    ],
    "rung": "c-s3-p3",
    "test": "2.7"
   }
  ],
  "rung": null,
  "why": "no PSRAM variant qualified"
 }
}
```
</details>

## 4.1 — S3: 20 vs 40 MHz, TCP + UDP, 3 x 60 s at 120 offered

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32s3 | 20mhz | tcp | 120.0 | 29.79 (±1.67) | 0.000 | 6524 | 9553/42742 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32s3 | 20mhz | udp | 120.0 | 37.64 (±0.26) | 0.000 | 6524 | 19674/48193 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32s3 | 40mhz | tcp | 120.0 | 30.58 (±4.83) | 0.000 | 5372 | 11028/46294 | HT ch1 bw40 sta40 ap:b2c0fe | 3 |
| esp32s3 | 40mhz | udp | 120.0 | 42.02 (±10.19) | 0.000 | 5372 | 15077/56349 | HT ch1 bw40 sta40 ap:b2c0fe | 3 |

## 4.2 — C5: Wi-Fi 6 at 20 MHz vs 802.11n at 40 MHz

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | 20mhz | tcp | 120.0 | 24.83 (±1.58) | 0.000 | 26784 | 7267/45167 | HE ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32c5 | 20mhz | udp | 120.0 | 42.39 (±0.28) | 0.000 | 26784 | 5963/31677 | HE ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32c5 | 40mhz | tcp | 120.0 | 7.54 (±0.22) | 0.000 | 22672 | 56064/136381 | HT ch1 bw40 sta40 ap:b2c0fe | 3 |
| esp32c5 | 40mhz | udp | 120.0 | 9.90 (±1.14) | 0.000 | 22672 | 62581/146705 | HT ch1 bw40 sta40 ap:b2c0fe | 3 |

## 4.3 — UDP load sweep, each chip's 40 MHz build, 10-120 Mbit/s

*esp32c5*

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | sweep | udp | 10.0 | 8.52 (±0.93) | 0.000 | 25664 | 68103/156396 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
| esp32c5 | sweep | udp | 104.3 | 7.67 (±0.29) | 0.000 | 25664 | 69082/178145 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
| esp32c5 | sweep | udp | 120.0 | 8.27 (±0.19) | 0.000 | 25664 | 69966/130208 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
| esp32c5 | sweep | udp | 25.7 | 9.59 (±0.96) | 0.000 | 25664 | 60988/158187 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
| esp32c5 | sweep | udp | 41.4 | 9.05 (±1.70) | 0.000 | 25664 | 65074/153356 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
| esp32c5 | sweep | udp | 57.1 | 8.40 (±0.72) | 0.000 | 25664 | 70863/157982 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
| esp32c5 | sweep | udp | 72.9 | 7.86 (±0.12) | 0.000 | 25664 | 72237/170580 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
| esp32c5 | sweep | udp | 88.6 | 9.90 (±2.31) | 0.000 | 25664 | 57209/181116 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
*esp32s3*

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32s3 | sweep | udp | 10.0 | 10.00 (±0.00) | 0.000 | 12948 | 3493/76410 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
| esp32s3 | sweep | udp | 104.3 | 25.58 (±1.97) | 0.000 | 12948 | 34048/111390 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
| esp32s3 | sweep | udp | 120.0 | 26.45 (±0.52) | 0.000 | 12948 | 32368/107108 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
| esp32s3 | sweep | udp | 25.7 | 25.75 (±0.03) | 0.000 | 12948 | 24416/99266 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
| esp32s3 | sweep | udp | 41.4 | 26.55 (±0.61) | 0.001 | 12948 | 27791/95454 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
| esp32s3 | sweep | udp | 57.1 | 25.20 (±0.47) | 0.000 | 12948 | 26908/107036 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
| esp32s3 | sweep | udp | 72.9 | 24.24 (±0.62) | 0.000 | 12948 | 33117/131904 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |
| esp32s3 | sweep | udp | 88.6 | 26.14 (±0.33) | 0.000 | 12948 | 28823/116568 | HT ch1 bw40 sta40 ap:b2c0fe | 2 |

## 0.4 — Mac to the 2.4 GHz network; its link (the second hop)


## 5.2 — C5 at 5 GHz: Wi-Fi 6 at 20 MHz, TCP + UDP, 3 x 60 s

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | base | tcp | 120.0 | 22.65 (±4.75) | 0.000 | 8748 | 9960/51064 | HE ch48 bw20 sta20 ap:b2c0ff | 3 |
| esp32c5 | base | udp | 120.0 | 18.56 (±1.42) | 0.000 | 8748 | 26122/88602 | HE ch48 bw20 sta20 ap:b2c0ff | 3 |

## 5.3 — C5 at 5 GHz: 802.11n at 40 MHz

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | ht40 | tcp | 120.0 | 26.96 (±0.41) | 0.000 | 18608 | 7878/40116 | HT ch48 bw40 sta40 ap:b2c0ff | 3 |
| esp32c5 | ht40 | udp | 120.0 | 50.77 (±1.77) | 0.000 | 18608 | 10024/50824 | HT ch48 bw40 sta40 ap:b2c0ff | 3 |

## 5.4 — C5 at 5 GHz: Wi-Fi 6 at 20 MHz with MCS 8-9

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | mcs9 | tcp | 120.0 | 19.97 (±4.20) | 0.000 | 20040 | 12888/58290 | HE ch48 bw20 sta20 ap:b2c0ff | 3 |
| esp32c5 | mcs9 | udp | 120.0 | 20.56 (±1.33) | 0.000 | 20040 | 26683/80129 | HE ch48 bw20 sta20 ap:b2c0ff | 3 |

## 5.5 — C5 at 5 GHz: UDP load sweep of the best of 5.2-5.4

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | sweep | udp | 10.0 | 10.00 (±0.01) | 0.000 | 20040 | 4528/35060 | HE ch48 bw20 sta20 ap:b2c0ff | 2 |
| esp32c5 | sweep | udp | 104.3 | 16.59 (±0.16) | 0.000 | 20040 | 29682/99252 | HE ch48 bw20 sta20 ap:b2c0ff | 2 |
| esp32c5 | sweep | udp | 120.0 | 18.68 (±2.20) | 0.000 | 20040 | 29682/114089 | HE ch48 bw20 sta20 ap:b2c0ff | 2 |
| esp32c5 | sweep | udp | 25.7 | 17.03 (±0.31) | 0.000 | 20040 | 29620/97549 | HE ch48 bw20 sta20 ap:b2c0ff | 2 |
| esp32c5 | sweep | udp | 41.4 | 18.90 (±0.78) | 0.000 | 20040 | 29303/77382 | HE ch48 bw20 sta20 ap:b2c0ff | 2 |
| esp32c5 | sweep | udp | 57.1 | 17.79 (±0.00) | 0.000 | 20040 | 29819/134729 | HE ch48 bw20 sta20 ap:b2c0ff | 2 |
| esp32c5 | sweep | udp | 72.9 | 17.10 (±1.17) | 0.000 | 20040 | 30577/89079 | HE ch48 bw20 sta20 ap:b2c0ff | 2 |
| esp32c5 | sweep | udp | 88.6 | 16.54 (±0.11) | 0.000 | 20040 | 28269/95628 | HE ch48 bw20 sta20 ap:b2c0ff | 2 |

## 5.6 — C5 at 5 GHz with the Mac also on 5 GHz (shared channel)

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | shared | tcp | 120.0 | 18.01 (±5.16) | 0.000 | 20040 | 14785/67495 | HE ch48 bw20 sta20 ap:b2c0ff | 3 |
| esp32c5 | shared | udp | 120.0 | 18.91 (±3.13) | 0.000 | 20040 | 36530/103146 | HE ch48 bw20 sta20 ap:b2c0ff | 3 |

## 3.0 — Block B start: rig check and reference

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | ref | tcp | 60.0 | 10.51 (±4.36) | 0.000 | 17752 | 19068/74602 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32c5 | ref | udp | 60.0 | 29.35 (±0.70) | 0.000 | 17752 | 10935/51406 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32s3 | ref | tcp | 60.0 | 21.93 (±0.64) | 0.000 | 9040 | 19643/64946 | HT ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32s3 | ref | udp | 60.0 | 27.22 (±3.16) | 0.000 | 9040 | 15890/55942 | HT ch1 bw20 sta20 ap:b2c0fe | 2 |

## 3.1 — Soak: UDP at 4.32 Mbit/s, 30 min, both boards at once

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | soakudp | udp | 4.32 | 4.17 (±0.02) | 3.409 | 17752 | 1932/33079 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32s3 | soakudp | udp | 4.32 | 4.32 (±0.00) | 0.000 | 9040 | 2083/36621 | HT ch1 bw20 sta20 ap:b2c0fe | 2 |

<details><summary>soak</summary>

```json
{
 "esp32c5/udp/soakudp": {
  "clock_skew_ppm": -2.71,
  "connections": null,
  "duration_s": 1800.0,
  "goodput_mbps": 4.189,
  "lat_max_us": 973513.0,
  "lat_p50_us": 1878.6,
  "lat_p99_us": 30161.0,
  "longest_gap_s": 3.65,
  "loss_pct": 3.0248282919528466,
  "lost_frames": 108894,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/followup/ts/3.1/s1-esp32c5-2.4-synth-udp-c-c5-base-t3.1-soakudp__4320k__r1__1792255496.json",
  "valid": true
 },
 "esp32s3/udp/soakudp": {
  "clock_skew_ppm": -24.16,
  "connections": null,
  "duration_s": 1800.0,
  "goodput_mbps": 4.32,
  "lat_max_us": 278354.3,
  "lat_p50_us": 1994.2,
  "lat_p99_us": 32386.2,
  "longest_gap_s": 0.168,
  "loss_pct": 0.0003333257409136792,
  "lost_frames": 12,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/followup/ts/3.1/s1-esp32s3-2.4-synth-udp-c-s3-base-t3.1-soakudp__4320k__r1__1792255496.json",
  "valid": true
 }
}
```
</details>

<details><summary>telemetry</summary>

```json
{
 "esp32c5/udp/soakudp": {
  "heap_free_min_B": 99056,
  "heap_largest_min_B": 61440,
  "heap_low_B": 17752,
  "heap_slope_B_per_h": -786.9,
  "linkdrops_delta": 0,
  "missed": 69,
  "phy_rates": [
   "HE ch1 bw20 sta20 ap:b2c0fe"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -54,
   "median": -64,
   "min": -66
  },
  "samples": 1806,
  "send_errs_delta": 52517,
  "stack_min_B": {
   "ctl": 1896,
   "instr": 2844,
   "sink": 3092,
   "src": 3712,
   "tiT": 2784,
   "wifi": 3740
  },
  "tcp_open_fails_delta": 0,
  "tcp_opens_delta": 0,
  "udp_enomem_delta": 18880,
  "unreachable_s": 69.0,
  "wifi_disc_delta": 19
 },
 "esp32s3/udp/soakudp": {
  "heap_free_min_B": 111864,
  "heap_largest_min_B": 69632,
  "heap_low_B": 9040,
  "heap_slope_B_per_h": -4834.2,
  "linkdrops_delta": 0,
  "missed": 7,
  "phy_rates": [
   "HT ch1 bw20 sta20 ap:b2c0fe"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -62,
   "median": -68,
   "min": -69
  },
  "samples": 1806,
  "send_errs_delta": 0,
  "stack_min_B": {
   "ctl": 1840,
   "instr": 2376,
   "sink": 2992,
   "src": 3320,
   "tiT": 1868,
   "wifi": 2420
  },
  "tcp_open_fails_delta": 0,
  "tcp_opens_delta": 0,
  "udp_enomem_delta": 4016,
  "unreachable_s": 7.0,
  "wifi_disc_delta": 0
 }
}
```
</details>

## 3.2 — Soak: TCP at 4.32 Mbit/s, 30 min, both boards at once

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | soaktcp | tcp | 4.32 | 0.00 (±0.00) | 0.000 | 7972 | 5605/184404 | HE ch1 bw20 sta20 ap:b2c0fe | 1 |
| esp32s3 | soaktcp | tcp | 4.32 | 3.49 (±0.00) | 0.000 | 652 | 36645/68120 | HT ch1 bw20 sta20 ap:b2c0fe | 1 |

<details><summary>soak</summary>

```json
{
 "esp32c5/tcp/soaktcp": {
  "clock_skew_ppm": null,
  "connections": 1,
  "duration_s": 1800.0,
  "goodput_mbps": 0.0,
  "lat_max_us": 199094.7,
  "lat_p50_us": 5605.4,
  "lat_p99_us": 184403.9,
  "longest_gap_s": 0.199,
  "loss_pct": 0.0,
  "lost_frames": 0,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/followup/ts/3.2/s1-esp32c5-2.4-synth-tcp-c-c5-base-t3.2-soaktcp__4320k__r1__1792257301.json",
  "valid": true
 },
 "esp32s3/tcp/soaktcp": {
  "clock_skew_ppm": -40.35,
  "connections": 2,
  "duration_s": 1800.0,
  "goodput_mbps": 3.486,
  "lat_max_us": 1151819936.5,
  "lat_p50_us": 36645.2,
  "lat_p99_us": 68120.5,
  "longest_gap_s": 1151.687,
  "loss_pct": 6.888878938285979e-05,
  "lost_frames": 2,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/followup/ts/3.2/s1-esp32s3-2.4-synth-tcp-c-s3-base-t3.2-soaktcp__4320k__r1__1792257314.json",
  "valid": true
 }
}
```
</details>

<details><summary>telemetry</summary>

```json
{
 "esp32c5/tcp/soaktcp": {
  "heap_free_min_B": 75972,
  "heap_largest_min_B": 32768,
  "heap_low_B": 7972,
  "heap_slope_B_per_h": -53.5,
  "linkdrops_delta": 0,
  "missed": 146,
  "phy_rates": [
   "HE ch1 bw20 sta20 ap:b2c0fe"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -54,
   "median": -61,
   "min": -63
  },
  "samples": 1801,
  "send_errs_delta": 5,
  "stack_min_B": {
   "ctl": 1896,
   "instr": 2844,
   "sink": 3092,
   "src": 3712,
   "tiT": 2736,
   "wifi": 3740
  },
  "tcp_open_fails_delta": 341,
  "tcp_opens_delta": 5,
  "udp_enomem_delta": 0,
  "unreachable_s": 146.0,
  "wifi_disc_delta": 19
 },
 "esp32s3/tcp/soaktcp": {
  "heap_free_min_B": 19668,
  "heap_largest_min_B": 7680,
  "heap_low_B": 652,
  "heap_slope_B_per_h": 4127171.7,
  "linkdrops_delta": 0,
  "missed": 1514,
  "phy_rates": [
   "HT ch1 bw20 sta20 ap:b2c0fe"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -61,
   "median": -62.0,
   "min": -64
  },
  "samples": 1814,
  "send_errs_delta": 1,
  "stack_min_B": {
   "ctl": 1840,
   "instr": 2376,
   "sink": 2992,
   "src": 3320,
   "tiT": 1868,
   "wifi": 2420
  },
  "tcp_open_fails_delta": 0,
  "tcp_opens_delta": 2,
  "udp_enomem_delta": 0,
  "unreachable_s": 1514.0,
  "wifi_disc_delta": 0
 }
}
```
</details>

## 3.3 — Soak: UDP at 41.3 Mbit/s, 30 min, S3 then C5

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | soakudp | udp | 41.3 | 37.88 (±0.17) | 8.241 | 7972 | — | HE ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32s3 | soakudp | udp | 41.3 | 31.27 (±0.00) | 0.117 | 85332 | — | HT ch1 bw20 sta20 ap:b2c0fe | 1 (+2 invalid) |

<details><summary>soak</summary>

```json
{
 "esp32c5/udp/soakudp": {
  "clock_skew_ppm": null,
  "connections": null,
  "duration_s": 1800.0,
  "goodput_mbps": 37.71,
  "lat_max_us": null,
  "lat_p50_us": null,
  "lat_p99_us": null,
  "longest_gap_s": 15.076,
  "loss_pct": 8.680221228619274,
  "lost_frames": 2987399,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/followup/ts/3.3/s1-esp32c5-2.4-synth-udp-c-c5-base-t3.3-soakudp__41300k__r1__1792261077.json",
  "valid": true
 },
 "esp32s3/udp/soakudp": {
  "clock_skew_ppm": null,
  "connections": null,
  "duration_s": 1800.0,
  "goodput_mbps": 31.271,
  "lat_max_us": null,
  "lat_p50_us": null,
  "lat_p99_us": null,
  "longest_gap_s": 0.48,
  "loss_pct": 0.11733536070851858,
  "lost_frames": 30620,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/followup/ts/3.3/s1-esp32s3-2.4-synth-udp-c-s3-base-t3.3-soakudp__41300k__r1__1792271177.json",
  "valid": true
 }
}
```
</details>

<details><summary>telemetry</summary>

```json
{
 "esp32c5/udp/soakudp": {
  "heap_free_min_B": 78364,
  "heap_largest_min_B": 15360,
  "heap_low_B": 7972,
  "heap_slope_B_per_h": 75522.5,
  "linkdrops_delta": 0,
  "missed": 1509,
  "phy_rates": [
   "HE ch1 bw20 sta20 ap:b2c0fe"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -63,
   "median": -65,
   "min": -67
  },
  "samples": 1814,
  "send_errs_delta": 497239,
  "stack_min_B": {
   "ctl": 1896,
   "instr": 2844,
   "sink": 3092,
   "src": 3712,
   "tiT": 2736,
   "wifi": 3740
  },
  "tcp_open_fails_delta": 0,
  "tcp_opens_delta": 0,
  "udp_enomem_delta": 971392,
  "unreachable_s": 1509.0,
  "wifi_disc_delta": 22
 },
 "esp32s3/udp/soakudp": {
  "heap_free_min_B": 90788,
  "heap_largest_min_B": 45056,
  "heap_low_B": 85332,
  "heap_slope_B_per_h": 54815.2,
  "linkdrops_delta": 0,
  "missed": 1229,
  "phy_rates": [
   "HT ch1 bw20 sta20 ap:b2c0fe"
  ],
  "psram_low_B": null,
  "reset_reasons": [
   "POWERON"
  ],
  "resets": 1,
  "rssi_dbm": {
   "max": -60,
   "median": -65,
   "min": -78
  },
  "samples": 1814,
  "send_errs_delta": 0,
  "stack_min_B": {
   "ctl": 1840,
   "instr": 2440,
   "sink": 3000,
   "src": 3312,
   "tiT": 1884,
   "wifi": 2340
  },
  "tcp_open_fails_delta": 0,
  "tcp_opens_delta": 0,
  "udp_enomem_delta": 1390309,
  "unreachable_s": 1229.0,
  "wifi_disc_delta": 0
 }
}
```
</details>

## 3.4 — Soak: TCP offered 41.3 Mbit/s (constant back-pressure), 30 min, S3 then C5

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | soaktcp | tcp | 41.3 | 1.30 (±0.00) | 0.000 | 7972 | 7041/37462 | HE ch1 bw20 sta20 ap:b2c0fe | 1 |
| esp32s3 | soaktcp | tcp | 41.3 | 27.11 (±0.00) | 0.000 | 576 | — | HT ch1 bw20 sta20 ap:b2c0fe | 1 |

<details><summary>soak</summary>

```json
{
 "esp32c5/tcp/soaktcp": {
  "clock_skew_ppm": -4.28,
  "connections": 1,
  "duration_s": 1800.0,
  "goodput_mbps": 1.305,
  "lat_max_us": 150254.7,
  "lat_p50_us": 7041.0,
  "lat_p99_us": 37461.6,
  "longest_gap_s": 0.087,
  "loss_pct": 0.0,
  "lost_frames": 0,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/followup/ts/3.4/s1-esp32c5-2.4-synth-tcp-c-c5-base-t3.4-soaktcp__41300k__r1__1792264701.json",
  "valid": true
 },
 "esp32s3/tcp/soaktcp": {
  "clock_skew_ppm": null,
  "connections": 1,
  "duration_s": 1800.0,
  "goodput_mbps": 27.111,
  "lat_max_us": null,
  "lat_p50_us": null,
  "lat_p99_us": null,
  "longest_gap_s": 1.375,
  "loss_pct": 0.0,
  "lost_frames": 0,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/followup/ts/3.4/s1-esp32s3-2.4-synth-tcp-c-s3-base-t3.4-soaktcp__41300k__r1__1792262894.json",
  "valid": true
 }
}
```
</details>

<details><summary>telemetry</summary>

```json
{
 "esp32c5/tcp/soaktcp": {
  "heap_free_min_B": 31244,
  "heap_largest_min_B": 15360,
  "heap_low_B": 7972,
  "heap_slope_B_per_h": -51.2,
  "linkdrops_delta": 0,
  "missed": 70,
  "phy_rates": [
   "HE ch1 bw20 sta20 ap:b2c0fe"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -55,
   "median": -64,
   "min": -69
  },
  "samples": 1801,
  "send_errs_delta": 5,
  "stack_min_B": {
   "ctl": 1896,
   "instr": 2844,
   "sink": 3092,
   "src": 3712,
   "tiT": 2736,
   "wifi": 3740
  },
  "tcp_open_fails_delta": 407,
  "tcp_opens_delta": 5,
  "udp_enomem_delta": 0,
  "unreachable_s": 70.0,
  "wifi_disc_delta": 21
 },
 "esp32s3/tcp/soaktcp": {
  "heap_free_min_B": 30320,
  "heap_largest_min_B": 7680,
  "heap_low_B": 576,
  "heap_slope_B_per_h": 54874.9,
  "linkdrops_delta": 0,
  "missed": 0,
  "phy_rates": [
   "HT ch1 bw20 sta20 ap:b2c0fe"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -60,
   "median": -70.0,
   "min": -73
  },
  "samples": 1814,
  "send_errs_delta": 1,
  "stack_min_B": {
   "ctl": 1840,
   "instr": 2376,
   "sink": 2816,
   "src": 3320,
   "tiT": 1868,
   "wifi": 2420
  },
  "tcp_open_fails_delta": 30,
  "tcp_opens_delta": 1,
  "udp_enomem_delta": 0,
  "wifi_disc_delta": 0
 }
}
```
</details>

## 3.5 — Stress: TCP at 4.32, 30 min, both boards — read pauses, firewall block, reset, link drop

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | stresstcp | tcp | 4.32 | 0.64 (±0.00) | 1.561 | 4760 | 20514/107617 | HE ch1 bw20 sta20 ap:b2c0fe | 1 |
| esp32s3 | stresstcp | tcp | 4.32 | 3.40 (±0.00) | 0.011 | 164 | 19435/76574 | HT ch1 bw20 sta20 ap:b2c0fe | 1 |

<details><summary>soak</summary>

```json
{
 "esp32c5/tcp/stresstcp": {
  "clock_skew_ppm": 0.71,
  "connections": 6,
  "duration_s": 1800.0,
  "goodput_mbps": 0.638,
  "lat_max_us": 1298555529.1,
  "lat_p50_us": 20513.5,
  "lat_p99_us": 107616.6,
  "longest_gap_s": 1300.758,
  "loss_pct": 1.5610604787056375,
  "lost_frames": 8506,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/followup/ts/3.5/s1-esp32c5-2.4-synth-tcp-c-c5-base-t3.5-stresstcp__4320k__r1__1792266506.json",
  "valid": true
 },
 "esp32s3/tcp/stresstcp": {
  "clock_skew_ppm": -17.63,
  "connections": 3,
  "duration_s": 1800.0,
  "goodput_mbps": 3.4,
  "lat_max_us": 1151385711.6,
  "lat_p50_us": 19435.4,
  "lat_p99_us": 76574.3,
  "longest_gap_s": 1151.341,
  "loss_pct": 0.010723633618591395,
  "lost_frames": 304,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/followup/ts/3.5/s1-esp32s3-2.4-synth-tcp-c-s3-base-t3.5-stresstcp__4320k__r1__1792266519.json",
  "valid": true
 }
}
```
</details>

<details><summary>telemetry</summary>

```json
{
 "esp32c5/tcp/stresstcp": {
  "heap_free_min_B": 29884,
  "heap_largest_min_B": 15360,
  "heap_low_B": 4760,
  "heap_slope_B_per_h": -224335.8,
  "linkdrops_delta": 1,
  "missed": 71,
  "phy_rates": [
   "HE ch1 bw20 sta20 ap:b2c0fe"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -60,
   "median": -65.0,
   "min": -78
  },
  "samples": 1801,
  "send_errs_delta": 10,
  "stack_min_B": {
   "ctl": 1896,
   "instr": 2844,
   "sink": 3092,
   "src": 3712,
   "tiT": 2736,
   "wifi": 3740
  },
  "tcp_open_fails_delta": 383,
  "tcp_opens_delta": 10,
  "udp_enomem_delta": 0,
  "unreachable_s": 71.0,
  "wifi_disc_delta": 20
 },
 "esp32s3/tcp/stresstcp": {
  "heap_free_min_B": 22784,
  "heap_largest_min_B": 7680,
  "heap_low_B": 164,
  "heap_slope_B_per_h": 154913.3,
  "linkdrops_delta": 0,
  "missed": 1549,
  "phy_rates": [
   "HT ch1 bw20 sta20 ap:b2c0fe"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -64,
   "median": -69,
   "min": -75
  },
  "samples": 1814,
  "send_errs_delta": 2,
  "stack_min_B": {
   "ctl": 1840,
   "instr": 2376,
   "sink": 2816,
   "src": 3320,
   "tiT": 1868,
   "wifi": 2420
  },
  "tcp_open_fails_delta": 0,
  "tcp_opens_delta": 3,
  "udp_enomem_delta": 0,
  "unreachable_s": 1549.0,
  "wifi_disc_delta": 0
 }
}
```
</details>

## 3.6 — Stress: UDP at 4.32, 30 min, both boards — link drops 1/5/30 s

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | stressudp | udp | 4.32 | 4.00 (±0.00) | 7.448 | 70300 | 3551/53152 | HE ch1 bw20 sta20 ap:b2c0fe | 1 |
| esp32s3 | stressudp | udp | 4.32 | 4.11 (±0.00) | 4.842 | 90440 | 4040/53441 | HT ch1 bw20 sta20 ap:b2c0fe | 1 |

<details><summary>soak</summary>

```json
{
 "esp32c5/udp/stressudp": {
  "clock_skew_ppm": -1.68,
  "connections": null,
  "duration_s": 1800.0,
  "goodput_mbps": 3.998,
  "lat_max_us": 284034.4,
  "lat_p50_us": 3550.8,
  "lat_p99_us": 53152.5,
  "longest_gap_s": 34.38,
  "loss_pct": 7.447682031641058,
  "lost_frames": 268117,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/followup/ts/3.6/s1-esp32c5-2.4-synth-udp-c-c5-base-t3.6-stressudp__4320k__r1__1792268511.json",
  "valid": true
 },
 "esp32s3/udp/stressudp": {
  "clock_skew_ppm": -23.19,
  "connections": null,
  "duration_s": 1800.0,
  "goodput_mbps": 4.111,
  "lat_max_us": 242560.4,
  "lat_p50_us": 4040.4,
  "lat_p99_us": 53441.2,
  "longest_gap_s": 32.383,
  "loss_pct": 4.8418977821579325,
  "lost_frames": 174312,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/followup/ts/3.6/s1-esp32s3-2.4-synth-udp-c-s3-base-t3.6-stressudp__4320k__r1__1792268509.json",
  "valid": true
 }
}
```
</details>

<details><summary>telemetry</summary>

```json
{
 "esp32c5/udp/stressudp": {
  "heap_free_min_B": 80080,
  "heap_largest_min_B": 53248,
  "heap_low_B": 70300,
  "heap_slope_B_per_h": -5587.1,
  "linkdrops_delta": 6,
  "missed": 187,
  "phy_rates": [
   "HE ch1 bw20 sta20 ap:b2c0fe"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -57,
   "median": -65.0,
   "min": -72
  },
  "samples": 1807,
  "send_errs_delta": 131684,
  "stack_min_B": {
   "ctl": 1900,
   "instr": 2864,
   "sink": 3128,
   "src": 3716,
   "tiT": 2720,
   "wifi": 3740
  },
  "tcp_open_fails_delta": 0,
  "tcp_opens_delta": 0,
  "udp_enomem_delta": 42546,
  "unreachable_s": 187.0,
  "wifi_disc_delta": 22
 },
 "esp32s3/udp/stressudp": {
  "heap_free_min_B": 130728,
  "heap_largest_min_B": 81920,
  "heap_low_B": 90440,
  "heap_slope_B_per_h": -2334.7,
  "linkdrops_delta": 6,
  "missed": 94,
  "phy_rates": [
   "HT ch1 bw20 sta20 ap:b2c0fe"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -58,
   "median": -64,
   "min": -69
  },
  "samples": 1805,
  "send_errs_delta": 89716,
  "stack_min_B": {
   "ctl": 1840,
   "instr": 2436,
   "sink": 2836,
   "src": 3324,
   "tiT": 1884,
   "wifi": 2404
  },
  "tcp_open_fails_delta": 0,
  "tcp_opens_delta": 0,
  "udp_enomem_delta": 8737,
  "unreachable_s": 94.0,
  "wifi_disc_delta": 6
 }
}
```
</details>

## 3.7 — Leak check: 500 start/stop cycles, both boards

- **esp32s3**: `{"cycles": 500, "errors": 12, "heap_first": 189904, "heap_last": 174240, "heap_low": 352, "heap_slope_B_per_100_cycles": 2046.8, "resets": 4, "series": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/followup/ts/3.7/esp32s3.json", "wedged_at": [200, 370, 400, 492]}`
- **esp32c5**: `{"cycles": 500, "errors": 17, "heap_first": 127052, "heap_last": 119792, "heap_low": 5300, "heap_slope_B_per_100_cycles": 2339.0, "resets": 4, "series": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/followup/ts/3.7/esp32c5.json", "wedged_at": [6, 28, 200, 322, 334]}`

## 6.0 — Block C start: rig check and reference

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | ref | tcp | 60.0 | 17.36 (±0.21) | 0.000 | 19992 | 13290/55405 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32c5 | ref | udp | 60.0 | 39.16 (±1.17) | 0.288 | 19992 | 6209/90506 | HE ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32s3 | ref | tcp | 60.0 | 31.35 (±0.56) | 0.000 | 20760 | 9078/37671 | HT ch1 bw20 sta20 ap:b2c0fe | 2 |
| esp32s3 | ref | udp | 60.0 | 39.55 (±1.42) | 0.000 | 20760 | 10627/37931 | HT ch1 bw20 sta20 ap:b2c0fe | 2 |

## 6.1 — STM32: build and flash both cores; hello; pacing with no link


<details><summary>pacing</summary>

```json
[
 {
  "achieved_mbps": 4.32,
  "error_pct": 0.003,
  "mbps": 4.32,
  "ok": true
 },
 {
  "achieved_mbps": 20.5,
  "error_pct": 0.001,
  "mbps": 20.5,
  "ok": true
 },
 {
  "achieved_mbps": 41.3,
  "error_pct": 0.001,
  "mbps": 41.3,
  "ok": true
 }
]
```
</details>

## 6.2 — Link up: one 20 s link-only run per arm at 41.3 Mbit/s


<details><summary>arms</summary>

```json
{
 "esp32s3/qspi": {
  "up": true,
  "opts": {
   "target": "s3",
   "qspi_hz": 25000000,
   "slew": 0
  },
  "tries": [
   {
    "opts": {
     "target": "s3",
     "qspi_hz": 25000000,
     "slew": 0
    },
    "passed": true,
    "failures": [],
    "notes": [
     "301 torn counter reads (re-read, harmless)"
    ]
   }
  ],
  "rung": "c-s3-base",
  "band": "2.4"
 },
 "esp32c5/qspi": {
  "up": true,
  "opts": {
   "target": "c5",
   "qspi_hz": 25000000,
   "slew": 0
  },
  "tries": [
   {
    "opts": {
     "target": "c5",
     "qspi_hz": 25000000,
     "slew": 0
    },
    "passed": true,
    "failures": [],
    "notes": [
     "138496 torn counter reads (re-read, harmless)"
    ]
   }
  ],
  "rung": "c-c5-ht40",
  "band": "5"
 },
 "esp32c5/sdio": {
  "up": false,
  "opts": null,
  "tries": [
   {
    "opts": {
     "target": "c5",
     "sdio_hz": 25000000,
     "slew": 0
    },
    "passed": false,
    "failures": [
     "H7 link_errors=100388"
    ],
    "notes": [
     "link could not carry this load: 381433 of 382849 frames dropped at the H7 ring (99.63%)",
     "7 torn counter reads (re-read, harmless)"
    ]
   },
   {
    "opts": {
     "target": "c5",
     "sdio_hz": 4000000,
     "slew": 0
    },
    "passed": false,
    "failures": [
     "nothing crossed the link (H7 sent 0 frames, ESP verified 0)"
    ],
    "notes": [
     "link could not carry this load: 381889 of 382913 frames dropped at the H7 ring (99.73%)",
     "1 credit waits over 10 ms"
    ]
   }
  ],
  "rung": "c-c5-ht40",
  "band": "5"
 }
}
```
</details>

## 6.3 — S3: built-in generator vs QSPI input (best setup)

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32s3 | qspi | tcp | 4.32 | 4.32 (±0.00) | 0.000 | 9712 | 1336/20119 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32s3 | qspi | tcp | 20.5 | — | — | — | — | — | 0 (6 invalid) |
| esp32s3 | qspi | tcp | 41.3 | 29.74 (±0.22) | 26.572 | 7648 | 72791/126927 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32s3 | qspi | udp | 4.32 | 4.32 (±0.00) | 13.070 | 7648 | 822/30628 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32s3 | qspi | udp | 20.5 | 20.50 (±0.00) | 0.000 | 7648 | 2510/31049 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32s3 | qspi | udp | 41.3 | 37.33 (±1.27) | 15.180 | 7648 | 69468/186334 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32s3 | synth | tcp | 4.32 | 4.32 (±0.00) | 0.000 | 148 | 952/25300 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32s3 | synth | tcp | 20.5 | 20.50 (±0.00) | 0.000 | 148 | 2476/27951 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32s3 | synth | tcp | 41.3 | 32.15 (±0.09) | 0.000 | 148 | 8062/37923 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32s3 | synth | udp | 4.32 | 4.32 (±0.00) | 0.000 | 148 | 698/18670 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32s3 | synth | udp | 20.5 | 20.50 (±0.00) | 0.000 | 148 | 2012/29846 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |
| esp32s3 | synth | udp | 41.3 | 39.62 (±2.31) | 0.000 | 148 | 18303/54044 | HT ch1 bw20 sta20 ap:b2c0fe | 3 |

<details><summary>ingress_cost</summary>

```json
{
 "qspi": {
  "tcp": {
   "4.32": 0.0,
   "41.3": 2.415
  },
  "udp": {
   "4.32": 0.0,
   "20.5": 0.001,
   "41.3": 2.284
  }
 }
}
```
</details>
