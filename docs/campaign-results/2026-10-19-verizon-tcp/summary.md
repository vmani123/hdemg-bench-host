# Campaign verizon-tcp-2026-10

_updated 2026-10-19 09:50:02 EDT_

11 done · 0 failed · 0 skipped · 0 pending (~0 min left)

| test | status | what | note |
|---|---|---|---|
| V.0 | done | Setup: both boards on the router, designs at 2.4 GHz; link check |  |
| V.1a | done | TCP at 10-30 Mbit/s, default vs batch=30, shuffled, round 1 (2 repeats) |  |
| V.2 | done | 15 min TCP at the C5's worst stall-zone load: C5, then S3 |  |
| V.3 | done | HD-EMG soak: TCP at 4.32 Mbit/s, 30 min, both boards at once |  |
| V.1b | done | TCP at 10-30 Mbit/s, default vs batch=30, shuffled, round 2 |  |
| V.4 | done | HD-EMG soak with batch=30: TCP at 4.32, 30 min, both boards |  |
| V.5 | done | C5 TCP at full load (60 offered), 20 min |  |
| V.1c | done | TCP at 10-30 Mbit/s, default vs batch=30, shuffled, round 3 |  |
| V.6 | done | Stress: TCP at 4.32, 30 min, both boards — read pauses, receiver resets, link drops |  |
| V.7 | done | TCP writes: 8,100 vs 2,700 vs 1,350 B at full load |  |
| V.2b | done | Extra: 15 min TCP with batch=30 at V.2's load, C5 |  |

## V.0 — Setup: both boards on the router, designs at 2.4 GHz; link check


## V.1a — TCP at 10-30 Mbit/s, default vs batch=30, shuffled, round 1 (2 repeats)

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | b30 | tcp | 10.0 | 10.00 (±0.00) | 0.000 | 25088 | 1826/28218 | HT ch6 bw20 sta20 ap:d9f3d9 | 2 |
| esp32c5 | b30 | tcp | 15.0 | 15.00 (±0.00) | 0.000 | 6868 | 2470/31956 | HT ch6 bw20 sta20 ap:d9f3d9 | 2 |
| esp32c5 | b30 | tcp | 20.5 | 20.52 (±0.02) | 0.000 | 22564 | 3032/28433 | HT ch6 bw20 sta20 ap:d9f3d9 | 2 |
| esp32c5 | b30 | tcp | 25.0 | 24.99 (±0.02) | 0.000 | 6868 | 4434/43346 | HT ch6 bw20 sta20 ap:d9f3d9 | 2 |
| esp32c5 | b30 | tcp | 30.0 | 27.71 (±2.25) | 0.000 | 26572 | 7886/64378 | HT ch6 bw20 sta20 ap:d9f3d9 | 2 |
| esp32c5 | def | tcp | 10.0 | 3.58 (±0.86) | 0.000 | 4780 | 2369/40582 | HT ch6 bw20 sta20 ap:d9f3d9 | 2 |
| esp32c5 | def | tcp | 15.0 | 0.00 (±0.00) | 0.000 | 4536 | 10185/444151 | HT ch6 bw20 sta20 ap:d9f3d9 | 2 |
| esp32c5 | def | tcp | 20.5 | 0.00 (±0.00) | 0.000 | 3656 | 9339/116343 | HT ch6 bw20 sta20 ap:d9f3d9 | 2 |
| esp32c5 | def | tcp | 25.0 | 3.94 (±3.94) | 0.000 | 3924 | 9696/81714 | HT ch6 bw20 sta20 ap:d9f3d9 | 2 |
| esp32c5 | def | tcp | 30.0 | 29.25 (±1.20) | 0.000 | 4536 | 9788/40176 | HT ch6 bw20 sta20 ap:d9f3d9 | 2 |

<details><summary>stalls</summary>

```json
{
 "esp32c5/b30": {
  "10.0": [
   0,
   2
  ],
  "15.0": [
   0,
   2
  ],
  "20.5": [
   0,
   2
  ],
  "25.0": [
   0,
   2
  ],
  "30.0": [
   0,
   2
  ]
 },
 "esp32c5/def": {
  "10.0": [
   2,
   2
  ],
  "15.0": [
   2,
   2
  ],
  "20.5": [
   2,
   2
  ],
  "25.0": [
   2,
   2
  ],
  "30.0": [
   0,
   2
  ]
 }
}
```
</details>

## V.2 — 15 min TCP at the C5's worst stall-zone load: C5, then S3

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | l15m | tcp | 15.0 | 0.44 (±0.00) | 0.000 | 2280 | 6156361/9847916 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |

<details><summary>c5_share_by_load</summary>

```json
{
 "10.0": 0.35819999999999996,
 "15.0": 0.0,
 "20.5": 0.0,
 "25.0": 0.15756,
 "30.0": 0.9750666666666666
}
```
</details>

<details><summary>soak</summary>

```json
{
 "esp32c5/tcp/l15m": {
  "clock_skew_ppm": 1000364.39,
  "connections": 7,
  "duration_s": 900.0,
  "goodput_mbps": 0.442,
  "lat_max_us": 10155305.7,
  "lat_p50_us": 6156361.0,
  "lat_p99_us": 9847915.8,
  "longest_gap_s": 127.659,
  "loss_pct": 0.0,
  "lost_frames": 0,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/verizon/ts/V.2/s1-esp32c5-2.4-synth-tcp-c-c5-base-tV.2-l15m__15000k__r1__1792408740.json",
  "valid": true
 }
}
```
</details>

<details><summary>telemetry</summary>

```json
{
 "esp32c5/tcp/l15m": {
  "heap_free_min_B": 14352,
  "heap_largest_min_B": 1408,
  "heap_low_B": 2280,
  "heap_slope_B_per_h": 73795.6,
  "linkdrops_delta": 0,
  "missed": 887,
  "phy_rates": [
   "HT ch6 bw20 sta20 ap:d9f3d9"
  ],
  "psram_low_B": null,
  "reset_reasons": [
   "POWERON",
   "POWERON",
   "POWERON",
   "POWERON"
  ],
  "resets": 4,
  "rssi_dbm": {
   "max": -39,
   "median": -43.5,
   "min": -45
  },
  "samples": 913,
  "send_errs_delta": 0,
  "stack_min_B": {
   "ctl": 1888,
   "instr": 2864,
   "sink": 3088,
   "src": 3712,
   "tiT": 2720,
   "wifi": 4252
  },
  "tcp_open_fails_delta": 0,
  "tcp_opens_delta": 4,
  "udp_enomem_delta": 0,
  "unreachable_s": 887.0,
  "wifi_disc_delta": 0
 }
}
```
</details>

## V.3 — HD-EMG soak: TCP at 4.32 Mbit/s, 30 min, both boards at once

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | soak-b0 | tcp | 4.32 | 4.32 (±0.00) | 0.001 | 5068 | 8900/37131 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |

<details><summary>soak</summary>

```json
{
 "esp32c5/tcp/soak-b0": {
  "clock_skew_ppm": -3.73,
  "connections": 2,
  "duration_s": 1800.0,
  "goodput_mbps": 4.32,
  "lat_max_us": 1150729313.8,
  "lat_p50_us": 8899.8,
  "lat_p99_us": 37130.6,
  "longest_gap_s": 1150.709,
  "loss_pct": 0.001277768549449365,
  "lost_frames": 46,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/verizon/ts/V.3/s1-esp32c5-2.4-synth-tcp-c-c5-base-tV.3-soak-b0__4320k__r1__1792410593.json",
  "valid": true
 }
}
```
</details>

<details><summary>telemetry</summary>

```json
{
 "esp32c5/tcp/soak-b0": {
  "heap_free_min_B": 30112,
  "heap_largest_min_B": 15360,
  "heap_low_B": 5068,
  "heap_slope_B_per_h": 880884.9,
  "linkdrops_delta": 0,
  "missed": 1166,
  "phy_rates": [
   "HT ch6 bw20 sta20 ap:d9f3d9"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -39,
   "median": -44,
   "min": -45
  },
  "samples": 1803,
  "send_errs_delta": 2,
  "stack_min_B": {
   "ctl": 1892,
   "instr": 2872,
   "sink": 3060,
   "src": 3724,
   "tiT": 2640,
   "wifi": 4252
  },
  "tcp_open_fails_delta": 10,
  "tcp_opens_delta": 2,
  "udp_enomem_delta": 0,
  "unreachable_s": 1166.0,
  "wifi_disc_delta": 0
 }
}
```
</details>

## V.1b — TCP at 10-30 Mbit/s, default vs batch=30, shuffled, round 2

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | b30 | tcp | 10.0 | 10.01 (±0.00) | 0.000 | 31924 | 1930/25226 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | b30 | tcp | 15.0 | 15.01 (±0.00) | 0.000 | 28004 | 2426/37410 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | b30 | tcp | 20.5 | 20.50 (±0.00) | 0.000 | 28004 | 2926/35212 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | b30 | tcp | 25.0 | 25.00 (±0.00) | 0.000 | 28004 | 4799/44102 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | b30 | tcp | 30.0 | 30.78 (±0.00) | 0.000 | 23252 | 8888/44612 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | def | tcp | 10.0 | 0.00 (±0.00) | 0.000 | 123656 | 4037/93203 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | def | tcp | 15.0 | 2.39 (±0.00) | 0.000 | 2776 | 3998/37560 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | def | tcp | 20.5 | 0.00 (±0.00) | 0.000 | 23252 | 11871/433582 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | def | tcp | 25.0 | 10.87 (±0.00) | 0.000 | 4124 | 9509/34978 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | def | tcp | 30.0 | 29.86 (±0.00) | 0.000 | 28140 | 8329/49532 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |

<details><summary>stalls</summary>

```json
{
 "esp32c5/b30": {
  "15.0": [
   0,
   1
  ],
  "25.0": [
   0,
   1
  ],
  "20.5": [
   0,
   1
  ],
  "30.0": [
   0,
   1
  ],
  "10.0": [
   0,
   1
  ]
 },
 "esp32c5/def": {
  "20.5": [
   1,
   1
  ],
  "25.0": [
   1,
   1
  ],
  "15.0": [
   1,
   1
  ],
  "10.0": [
   1,
   1
  ],
  "30.0": [
   0,
   1
  ]
 }
}
```
</details>

## V.4 — HD-EMG soak with batch=30: TCP at 4.32, 30 min, both boards

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | soak-b30 | tcp | 4.32 | 4.32 (±0.00) | 0.000 | 22984 | 2887/183220 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |

<details><summary>soak</summary>

```json
{
 "esp32c5/tcp/soak-b30": {
  "valid": true,
  "goodput_mbps": 4.32,
  "loss_pct": 0.0,
  "lost_frames": 0,
  "duration_s": 1800.0,
  "longest_gap_s": 2.296,
  "lat_p50_us": 2887.0,
  "lat_p99_us": 183219.6,
  "lat_max_us": 3295496.7,
  "clock_skew_ppm": -3.53,
  "connections": 1,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/verizon/ts/V.4/s1-esp32c5-2.4-synth-tcp-c-c5-base-tV.4-soak-b30__4320k__r1__1792412910.json"
 }
}
```
</details>

<details><summary>telemetry</summary>

```json
{
 "esp32c5/tcp/soak-b30": {
  "heap_free_min_B": 31484,
  "heap_largest_min_B": 14336,
  "heap_low_B": 22984,
  "heap_slope_B_per_h": -104166.9,
  "linkdrops_delta": 0,
  "missed": 14,
  "phy_rates": [
   "HT ch6 bw20 sta20 ap:d9f3d9"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -41,
   "median": -44.0,
   "min": -49
  },
  "samples": 1806,
  "send_errs_delta": 1,
  "stack_min_B": {
   "ctl": 1900,
   "instr": 2868,
   "sink": 3068,
   "src": 3716,
   "tiT": 2720,
   "wifi": 4124
  },
  "tcp_open_fails_delta": 24,
  "tcp_opens_delta": 1,
  "udp_enomem_delta": 0,
  "unreachable_s": 14.0,
  "wifi_disc_delta": 0
 }
}
```
</details>

## V.5 — C5 TCP at full load (60 offered), 20 min

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | full20m | tcp | 60.0 | 31.18 (±0.00) | 0.000 | 19412 | — | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |

<details><summary>soak</summary>

```json
{
 "esp32c5/tcp/full20m": {
  "valid": true,
  "goodput_mbps": 31.183,
  "loss_pct": 0.0,
  "lost_frames": 0,
  "duration_s": 1200.0,
  "longest_gap_s": 1.214,
  "lat_p50_us": null,
  "lat_p99_us": null,
  "lat_max_us": null,
  "clock_skew_ppm": null,
  "connections": 1,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/verizon/ts/V.5/s1-esp32c5-2.4-synth-tcp-c-c5-base-tV.5-full20m__60000k__r1__1792414129.json"
 }
}
```
</details>

<details><summary>telemetry</summary>

```json
{
 "esp32c5/tcp/full20m": {
  "heap_free_min_B": 30060,
  "heap_largest_min_B": 15360,
  "heap_low_B": 19412,
  "heap_slope_B_per_h": 29217.9,
  "linkdrops_delta": 0,
  "missed": 41,
  "phy_rates": [
   "HT ch6 bw20 sta20 ap:d9f3d9"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -39,
   "median": -42,
   "min": -47
  },
  "samples": 1214,
  "send_errs_delta": 1,
  "stack_min_B": {
   "ctl": 1900,
   "instr": 2836,
   "sink": 3068,
   "src": 3716,
   "tiT": 2720,
   "wifi": 4124
  },
  "tcp_open_fails_delta": 28,
  "tcp_opens_delta": 1,
  "udp_enomem_delta": 0,
  "unreachable_s": 41.0,
  "wifi_disc_delta": 0
 }
}
```
</details>

## V.1c — TCP at 10-30 Mbit/s, default vs batch=30, shuffled, round 3

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | b30 | tcp | 10.0 | 10.00 (±0.00) | 0.000 | 19412 | 2110/30894 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | b30 | tcp | 15.0 | 15.00 (±0.00) | 0.000 | 27860 | 2249/40865 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | b30 | tcp | 20.5 | 20.50 (±0.00) | 0.000 | 4584 | 3051/24201 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | b30 | tcp | 25.0 | 25.00 (±0.00) | 0.000 | 2648 | 5689/36467 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | b30 | tcp | 30.0 | 29.24 (±0.00) | 0.000 | 22576 | 9211/36147 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | def | tcp | 10.0 | 9.96 (±0.00) | 0.000 | 2648 | 3048/29692 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | def | tcp | 15.0 | 1.13 (±0.00) | 0.000 | 2648 | 7827/303114 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | def | tcp | 20.5 | 6.45 (±0.00) | 0.000 | 3416 | 9498/54289 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | def | tcp | 25.0 | 25.30 (±0.00) | 0.000 | 4584 | 10224/62043 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |
| esp32c5 | def | tcp | 30.0 | 29.59 (±0.00) | 0.000 | 2648 | 9639/54884 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |

<details><summary>stalls</summary>

```json
{
 "esp32c5/b30": {
  "10.0": [
   0,
   1
  ],
  "25.0": [
   0,
   1
  ],
  "15.0": [
   0,
   1
  ],
  "20.5": [
   0,
   1
  ],
  "30.0": [
   0,
   1
  ]
 },
 "esp32c5/def": {
  "10.0": [
   0,
   1
  ],
  "30.0": [
   0,
   1
  ],
  "15.0": [
   1,
   1
  ],
  "25.0": [
   0,
   1
  ],
  "20.5": [
   1,
   1
  ]
 }
}
```
</details>

## V.6 — Stress: TCP at 4.32, 30 min, both boards — read pauses, receiver resets, link drops

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | stresstcp | tcp | 4.32 | 2.48 (±0.00) | 0.000 | 2892 | 118067081/243071652 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |

<details><summary>soak</summary>

```json
{
 "esp32c5/tcp/stresstcp": {
  "valid": true,
  "goodput_mbps": 2.48,
  "loss_pct": 0.0,
  "lost_frames": 0,
  "duration_s": 1800.0,
  "longest_gap_s": 111.311,
  "lat_p50_us": 118067081.4,
  "lat_p99_us": 243071652.1,
  "lat_max_us": 252292007.0,
  "clock_skew_ppm": 940711.12,
  "connections": 9,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/verizon/ts/V.6/s1-esp32c5-2.4-synth-tcp-c-c5-base-tV.6-stresstcp__4320k__r1__1792416255.json"
 }
}
```
</details>

<details><summary>telemetry</summary>

```json
{
 "esp32c5/tcp/stresstcp": {
  "heap_free_min_B": 18776,
  "heap_largest_min_B": 6144,
  "heap_low_B": 2892,
  "heap_slope_B_per_h": 4365.7,
  "linkdrops_delta": 1,
  "missed": 786,
  "phy_rates": [
   "HT ch6 bw20 sta20 ap:d9f3d9"
  ],
  "psram_low_B": null,
  "reset_reasons": [
   "POWERON",
   "POWERON",
   "POWERON",
   "POWERON",
   "POWERON",
   "POWERON",
   "POWERON"
  ],
  "resets": 7,
  "rssi_dbm": {
   "max": -37,
   "median": -42,
   "min": -46
  },
  "samples": 1803,
  "send_errs_delta": 2,
  "stack_min_B": {
   "ctl": 1888,
   "instr": 2868,
   "sink": 3088,
   "src": 3716,
   "tiT": 2720,
   "wifi": 4156
  },
  "tcp_open_fails_delta": 68,
  "tcp_opens_delta": 8,
  "udp_enomem_delta": 0,
  "unreachable_s": 786.0,
  "wifi_disc_delta": 1
 }
}
```
</details>

## V.7 — TCP writes: 8,100 vs 2,700 vs 1,350 B at full load

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | wr-1350 | tcp | 60.0 | 26.91 (±0.11) | 0.000 | 6216 | 7615/46963 | HT ch6 bw20 sta20 ap:d9f3d9 | 3 |
| esp32c5 | wr-2700 | tcp | 60.0 | 28.81 (±1.24) | 0.000 | 19004 | 7897/37587 | HT ch6 bw20 sta20 ap:d9f3d9 | 3 |
| esp32c5 | wr-8100 | tcp | 60.0 | 27.92 (±2.53) | 0.000 | 22628 | 7754/39766 | HT ch6 bw20 sta20 ap:d9f3d9 | 3 |

## V.2b — Extra: 15 min TCP with batch=30 at V.2's load, C5

| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | p50/p99 µs | link | n |
|---|---|---|---|---|---|---|---|---|---|
| esp32c5 | l15m-b30 | tcp | 15.0 | 15.00 (±0.00) | 0.000 | 19392 | 3458/50105 | HT ch6 bw20 sta20 ap:d9f3d9 | 1 |

<details><summary>soak</summary>

```json
{
 "esp32c5/tcp/l15m-b30": {
  "valid": true,
  "goodput_mbps": 15.004,
  "loss_pct": 0.0,
  "lost_frames": 0,
  "duration_s": 900.0,
  "longest_gap_s": 0.17,
  "lat_p50_us": 3457.6,
  "lat_p99_us": 50105.4,
  "lat_max_us": 231096.8,
  "clock_skew_ppm": -2.5,
  "connections": 1,
  "timeseries": "/Users/th3vik/Desktop/Research/HDEMG_PCB_WORK/ESP_COMPARISON_CODE/hdemg-bench/host/results/campaign/verizon/ts/V.2b/s1-esp32c5-2.4-synth-tcp-c-c5-base-tV.2b-l15m-b30__15000k__r1__1792417802.json"
 }
}
```
</details>

<details><summary>telemetry</summary>

```json
{
 "esp32c5/tcp/l15m-b30": {
  "heap_free_min_B": 30136,
  "heap_largest_min_B": 15360,
  "heap_low_B": 19392,
  "heap_slope_B_per_h": 56058.3,
  "linkdrops_delta": 0,
  "missed": 4,
  "phy_rates": [
   "HT ch6 bw20 sta20 ap:d9f3d9"
  ],
  "psram_low_B": null,
  "resets": 0,
  "rssi_dbm": {
   "max": -36,
   "median": -43,
   "min": -48
  },
  "samples": 909,
  "send_errs_delta": 1,
  "stack_min_B": {
   "ctl": 1892,
   "instr": 2840,
   "sink": 3068,
   "src": 3716,
   "tiT": 2720,
   "wifi": 4204
  },
  "tcp_open_fails_delta": 29,
  "tcp_opens_delta": 1,
  "udp_enomem_delta": 0,
  "unreachable_s": 4.0,
  "wifi_disc_delta": 0
 }
}
```
</details>
