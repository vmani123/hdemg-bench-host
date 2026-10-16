# Follow-up campaign: firmware ↔ host contract

The follow-up campaign (October 2026; tests listed in `docs/CAMPAIGN.md`) needs a few new
things from the boards. This file fixes their names and formats once. The ESP firmware,
the STM32H745 firmware, the fake boards (`host/bench/fakeesp.py`, `host/bench/h7.py`
`FakeH7`) and the campaign engine all implement exactly this.

## 1. ESP control plane, protocol v2

`CONTROL_PROTOCOL_VERSION` goes from 1 to 2 (firmware `control.h` and host
`control.py`). The host refuses a v1 board, so a board running an old build is reflashed
rather than half-understood.

### 1.1 New `cfg` keys

Runtime keys, no reflash needed. Defaults reproduce the v1 behaviour exactly. A key keeps
its value until it is set again or the board reboots, and every key is echoed in the
`applied` object of the reply.

| key | default | meaning |
|---|---|---|
| `nodelay` | `1` | TCP: `TCP_NODELAY` on the next TCP connection (`0` = Nagle on). The sink opens one connection per run, so a value set before `start` applies to that run. |
| `wr_max` | `0` | TCP: largest number of bytes passed to one `send()` call. `0` = the whole pool buffer (up to 8,100 B, as in v1). Values are rounded **down** to whole frames (never below one frame), so every write is still frame-aligned. |
| `dgram` | `0` | UDP: largest datagram in bytes, whole frames only (never below one frame). `0` = `CONFIG_BENCH_UDP_DGRAM_MAX` (1,472 → 1,350 B, as in v1). |

### 1.2 New commands

| command | reply | effect |
|---|---|---|
| `linkdrop <ms>` | `{"ok":true,"ms":N}`, sent **before** the drop | Takes the Wi-Fi link down for `ms` milliseconds (100–60000), then rejoins the same access point with the same band, width and power settings. Done in its own task, so the reply goes out first. While the link is down the board is unreachable. A running stream keeps going: TCP sees its connection fail and reopens it, and UDP sends fail. That is the point: it is the stress a UDP sender cannot get from the receiver. Counted in `linkdrops`. |
| `pins list` | `{"ok":true,"pins":{"<gpio>":["qspi_cs","sdio_cmd"],...}}` | The board's Stage 2 link GPIOs and every role each one has (the C5 shares GPIO10 and GPIO7 between QSPI and SDIO). |
| `pins in` | `{"ok":true,"levels":{"<gpio>":0\|1,...}}` | Every link GPIO becomes an input with the internal pull-**down** on; reply with the levels. |
| `pins read` | same | Read levels without reconfiguring. |
| `pins drive <gpio> <0\|1>` | same | That GPIO becomes an output at that level; every other link GPIO is an input with a pull-down. |
| `pins release` | `{"ok":true}` | Every link GPIO goes back to its reset state (`gpio_reset_pin`). |

`pins` works only in a **synth** build: in a wired build the link driver owns the pins,
and the reply is `{"ok":false,"err":"pins are owned by the <link> link; flash a synth build"}`.
A GPIO that isn't a link pin is refused.

### 1.3 New `stat` fields (also in the `stop` summary)

Existing fields are unchanged. Added:

| field | meaning |
|---|---|
| `heap_largest` | largest free internal block, in bytes (fragmentation) |
| `heap_win_min` | lowest free internal heap since the previous `stat` (windowed; `heap_min` stays the lifetime figure) |
| `psram_free`, `psram_min` | free and lowest-free PSRAM heap; `-1` when the build has no PSRAM |
| `uptime_s` | seconds since boot |
| `reset_reason` | `esp_reset_reason()` as its name without the prefix, e.g. `"POWERON"`, `"PANIC"`, `"BROWNOUT"`, `"SW"` |
| `udp_enomem` | times the UDP sink waited because every Wi-Fi TX buffer was in use, since boot |
| `send_errs` | sends that failed outright (socket error, not back-pressure), since boot |
| `tcp_opens`, `tcp_open_fails` | TCP connections made / attempts that failed, since boot |
| `wifi_disc`, `wifi_last_reason` | station disconnect events since boot, and the reason code of the last |
| `linkdrops` | `linkdrop` commands served |
| `stack_min` | `{"src":B,"sink":B,"ctl":B,"instr":B,"tiT":B,"wifi":B}`: free stack low-water per task, in bytes; a task that can't be found is omitted |
| `nodelay`, `wr_max`, `dgram` | the values currently in force |

The whole reply must stay under 1,400 bytes (one unfragmented datagram).

### 1.4 Build switches (Kconfig, `firmware/bench_common/Kconfig`)

| symbol | type | meaning |
|---|---|---|
| `CONFIG_BENCH_PIPE_POOL_PSRAM` | bool, `depends on SPIRAM` | allocate the pipe pool in PSRAM (64-byte aligned) instead of static internal RAM. A wired-ingress build (`BENCH_INGRESS` qspi or sdio) refuses it at build time, because the link DMAs straight into pool buffers |
| `CONFIG_BENCH_WIFI_PIN_CHANNEL_5G` | int 0–177, default 0 | the access-point channel pin for a 5 GHz build (a rig setting in `sdkconfig.local`, like `CONFIG_BENCH_WIFI_PIN_CHANNEL`, which stays the 2.4 GHz pin) |

## 2. Stage 2 pin map (wire once, no resistors)

One wire per pin and no shared nets. The H7 picks its pin set when a link opens, from
`target` and `link`; every pin not in the active set is de-initialised (Hi-Z). Only the
ESPs are reflashed to change arms.

| # | Nucleo | H7 pin | role | ESP |
|---|---|---|---|---|
| 1 | CN10-15 | PB2 | QUADSPI_CLK (AF9), S3 set | S3 GPIO12 CLK |
| 2 | CN10-13 | PG6 | QUADSPI_BK1_NCS (AF10), S3 set | S3 GPIO10 CS |
| 3 | CN10-23 | PD11 | BK1_IO0 (AF9), S3 set | S3 GPIO11 D0 |
| 4 | CN10-21 | PD12 | BK1_IO1 (AF9), S3 set | S3 GPIO13 D1 |
| 5 | CN10-25 | PE2 | BK1_IO2 (AF9), S3 set | S3 GPIO14 D2 |
| 6 | CN10-19 | PD13 | BK1_IO3 (AF9), S3 set | S3 GPIO9 D3 |
| 7 | CN7-20 | PG9 | READY in, EXTI9 | S3 GPIO7 READY |
| 8 | CN10-9 | PF10 | QUADSPI_CLK (AF9), C5 set | C5 GPIO6 CLK |
| 9 | CN8-12 | PD2 | SDIO: SDMMC1_CMD (AF12) · C5 QSPI: GPIO output = software CS | C5 GPIO10 (SDIO CMD / QSPI CS) |
| 10 | CN8-4 | PC9 | SDIO: SDMMC1_D1 (AF12) · C5 QSPI: BK1_IO0 (AF9) | C5 GPIO7 (SDIO D1 / QSPI D0), strapping pin |
| 11 | CN9-28 | PF9 | BK1_IO1 (AF10), C5 set | C5 GPIO2 D1, strapping pin |
| 12 | CN9-26 | PF7 | BK1_IO2 (AF9), C5 set | C5 GPIO5 D2 |
| 13 | CN10-7 | PF6 | BK1_IO3 (AF9), C5 set | C5 GPIO4 D3 |
| 14 | CN8-14 | PG10 | READY in, EXTI10 (EXTI15_10) | C5 GPIO3 READY |
| 15 | CN8-10 | PC12 | SDMMC1_CK (AF12) | C5 GPIO9 SDIO CLK |
| 16 | CN8-2 | PC8 | SDMMC1_D0 (AF12) | C5 GPIO8 SDIO D0 |
| 17 | CN8-6 | PC10 | SDMMC1_D2 (AF12) | C5 GPIO14 SDIO D2 (USB D+ by default) |
| 18 | CN8-8 | PC11 | SDMMC1_D3 (AF12) | C5 GPIO13 SDIO D3 (USB D− by default) |
| — | CN10-17, CN10-22 | GND | | S3 GND ×2 |
| — | CN8-11, CN8-13 | GND | | C5 GND ×2 |

Consequences in code:

- **No external pull-ups.** The H7 enables its internal pull-ups on SDMMC1 CMD and D0–D3,
  and the C5's SDIO slave sets `SDIO_SLAVE_FLAG_INTERNAL_PULLUP`; together that's about
  20 kΩ. SDIO stays at ≤ 25 MHz and starts slow.
- **ESP QSPI builds** enable the internal pull-up on CS, so CS reads high while the H7's
  pins are Hi-Z.
- **C5 QSPI chip select is software**, on PD2 as a push-pull GPIO. It is held low for the
  whole transaction (command, address, dummy, data) and raised only once QUADSPI's BUSY
  flag has cleared, with a minimum high time between transactions. This applies to every
  transaction type, including WR_END and RDBUF.
- **Close every H7 link before any ESP reset or flash.** GPIO2 and GPIO7 are C5
  strapping pins and are on wires 10 and 11.

## 3. STM32H745 console additions (protocol stays 1; new keys and verbs only)

| item | form | meaning |
|---|---|---|
| `cfg target=s3\|c5` | default `s3` | which ESP the next `link=qspi` open drives: the S3 pin set (PB2/PG6 hardware NCS/PD11-13/PE2, READY PG9) or the C5 set (PF10/PC9/PF9/PF7/PF6, software CS PD2, READY PG10). `link=sdio` implies `c5`. Echoed by `cfg` and `hello` |
| `pins list` | `{"ok":1,"pins":{"PB2":"s3_qspi_clk",...}}` | the 18 link pins and their roles |
| `pins hiz` | `{"ok":1}` | every link pin de-initialised (refused with an error while a link is open or a run is active) |
| `pins drive <PIN> <0\|1>` | `{"ok":1,"levels":{...}}` | that pin a push-pull output at that level; every other link pin an input with the pull-**down** on |
| `pins in` | `{"ok":1,"levels":{"PB2":0,...}}` | every link pin an input with pull-down; reply with all levels |
| `pins read` | same | read levels without reconfiguring |

Pin names are written as `PB2`, `PG10` and so on. After any `pins` command other than
`hiz`, the next `link` open re-initialises its pins as usual, and a `probe` or `start`
releases them first.

## 4. The wiring "hello world" (`bench wiring`)

Run before anything else, with both ESPs on **synth** builds and the H7 link closed.

1. `hello` to the H7 over its USB serial port and to each ESP over Wi-Fi.
2. **H7 → ESP:** for each of the 18 H7 link pins, the H7 drives it high with every other
   H7 pin Hi-Z, while both ESPs hold all their link GPIOs as pulled-down inputs. Exactly
   the expected ESP GPIO, on the expected board, must read 1; everything else reads 0.
   Then drive it low and confirm the expected GPIO reads 0.
3. **ESP → H7:** for each ESP link GPIO, the ESP drives it high while the H7 holds all
   its link pins as pulled-down inputs. Exactly the expected H7 pin must read 1.
4. Report a table: wire #, H7 pin, expected ESP GPIO, seen, verdict (`ok`, `open`,
   `swapped with #n`, `shorted to #n`, `stuck high`). Release all pins and close the
   H7 link at the end.
5. **Boot check:** reset each ESP (`esp_monitor` through `hostrun`), with the H7
   connected and every link closed. Confirm it boots to the application, using the
   boot log and `hello`, which catches strapping-pin trouble on the C5.

Nothing in this check needs a link driver, so it separates "the wire is wrong" from "the
protocol is wrong" before any Stage 2 run.
