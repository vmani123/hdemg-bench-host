# Stage 2 — STM32H745 ingress master: build guide

**Companion to** `S3_vs_C5_Test_Plan_v2.md` (§4.3, §9.1.4, §9.2) and `BENCH_HARNESS_SPEC.md` (§1.1, §7).
**Written 2026-10-05** against the hardware and toolchain actually on the bench Mac. Every
protocol constant below is either read out of the **installed** ESP-IDF source (file and line
given) or marked **VERIFY**. Nothing here was flashed or wired while writing it.

Stage 2 measures what feeding the ESP over a wire costs (plan §2.2): the H745 plays the FPGA,
generating paced 128-channel frames and pushing them into the ESP over **QSPI** (2a, both chips)
or **SDIO 4-bit** (2b, C5 only). The ESP relays them over Wi-Fi exactly as in Stage 1. The
number that matters is `synth − ingress` per chip, so the H7 side must be a *faithful, paced,
loss-accounted* source — never "as fast as it will go".

---

## 0. What is on the bench (checked, not assumed)

| Item | Found | How |
|---|---|---|
| H7 board | **NUCLEO-H745ZI-Q** (STM32H745ZIT6, dual-core CM7+CM4) | ST-LINK reports `Board Name: NUCLEO-H745ZI-Q`; MSD volume `NOD_H745ZIQ` |
| Probe | on-board STLINK-V3, SN `004800443432511530343838`, **FW V3J5M2**, 4 APs | `STM32_Programmer_CLI -l st-link` |
| VCP | `/dev/cu.usbmodem21103` → USART3 (PD8 TX / PD9 RX) | Nucleo-144 VCP wiring; pins confirmed in CubeMX DB |
| IDE | **STM32CubeIDE 1.18.1** (embedded CubeMX 6.14.1) | `Info.plist` |
| Firmware pack | `~/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1` — has `stm32h7xx_hal_qspi`, `_hal_sdio`, `_hal_mdma`, Nucleo-H745ZI-Q examples | `ls` |
| Bundled CLI tools | GCC **13.3.rel1**, GNU make, **STM32_Programmer_CLI v2.19.0**, ST-LINK GDB server, ST OpenOCD | CubeIDE `plugins/…externaltools…/tools/bin` |
| Also on PATH | `arm-none-eabi-gcc` 10.3-2021.10, OpenOCD 0.12.0 (`/usr/local/bin`) | prefer the CubeIDE-bundled 13.3 |
| ESP-IDF | `/Users/th3vik/Desktop/Research/esp-idf`, **v6.0-dev-2980** (Oct-2025 master), not v6.0.2 | `git describe` |

**Heads-up — your existing H7 projects are for a different board.** Every H7 `.ioc` you have
built in CubeIDE (`Intan_RHD_STM32_Framework*/H7/*`, `Intan-STM32H7-Firmware/*`) targets
**NUCLEO-H723ZG** (STM32H723: single core, **OCTOSPI**, different pins and linker script). They
cannot be flashed onto the H745. The one H745 file (`~/STM32CubeIDE/May-07/rhd2164_receive_h745.ioc`)
is an empty `board=custom` stub at 64 MHz HSI with no generated code. **Start a fresh project
from the board selector** (§2) — that gets the SMPS supply right, which a `custom` board does not.

---

## 1. Building and flashing without the GUI

You only need the CubeIDE GUI for **one** thing: editing the `.ioc` (pins/peripherals) and
pressing *Generate Code*. Everything after that — build, flash, reset, read the VCP — runs from
the command line, which is what lets the agent (and `hostrun.sh`) drive it.

### 1.1 Headless build — verified on this Mac

```bash
CUBEIDE=/Applications/STM32CubeIDE.app/Contents/MacOS/STM32CubeIDE
WS="$HOME/.cache/cubeide-headless-ws"     # a SEPARATE workspace: the GUI locks its own

"$CUBEIDE" --launcher.suppressErrors -nosplash \
  -application org.eclipse.cdt.managedbuilder.core.headlessbuild \
  -data "$WS" \
  -import "/path/to/hdemg_h745" \
  -build "hdemg_h745_CM7/Debug"
```

Verified tonight on a scratch copy of `Intan_RHD_STM32_Framework_v1_1_5/H7/rhd_acquisition`:
exit 0, **19 s**, `rhd_acquisition.elf` produced (text 63 188 B) with the bundled GCC 13.3 and
make — no window opened. (A second copy, `Intan-STM32H7-Firmware/rhd2164_acquisition`, fails on
a genuine source typo: a stray `f` before the opening `/*` of `Core/Inc/userfunctions.h`.)

Notes:

- **Dual-core projects.** CubeIDE creates a parent folder with nested `<name>_CM7` and
  `<name>_CM4` projects. Use `-importAll /path/to/hdemg_h745` (imports nested projects) and
  build `-build "hdemg_h745_CM7/Debug"` (and `_CM4/Debug` if the CM4 image changes).
- `-import` is idempotent per workspace; on later builds you can drop it, or keep it — it is
  harmless. Use `-cleanBuild` instead of `-build` for a from-scratch build.
- The headless run **regenerates the makefiles** from `.cproject`, so new source files are
  picked up. It does **not** regenerate code from the `.ioc` — that is the one GUI step.
- Exit code is non-zero on a compile error; `Build Failed. N errors` appears in stdout.

### 1.2 Flash — `STM32_Programmer_CLI` (bundled with CubeIDE)

```bash
PROG=$(ls -d /Applications/STM32CubeIDE.app/Contents/Eclipse/plugins/com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.macos64_*/tools/bin)/STM32_Programmer_CLI

"$PROG" -l st-link                                           # list probes, touches nothing
"$PROG" -c port=SWD mode=UR -w hdemg_h745_CM7/Debug/hdemg_h745_CM7.elf -v -rst
"$PROG" -c port=SWD mode=UR -w hdemg_h745_CM4/Debug/hdemg_h745_CM4.elf -v -rst   # only if CM4 changed
```

The `.elf` carries its load addresses (CM7 → bank 1 `0x08000000`, CM4 → bank 2 `0x08100000`),
so no address argument is needed. `mode=UR` (connect under reset) recovers a board whose
firmware has disabled SWD pins or is stuck in a fault loop.

OpenOCD equivalent, if preferred: `openocd -f board/st_nucleo_h745zi.cfg -c "program
<elf> verify reset exit"`.

**ST-LINK firmware V3J5M2 is old (2019).** If programming the second bank or attaching to the
CM4 misbehaves, update it with ST's *STLinkUpgrade* (or the GUI CubeProgrammer) — a one-time,
two-minute GUI step.

### 1.3 Talk to it — the VCP

```bash
python3 -c "import serial; s=serial.Serial('/dev/cu.usbmodem21103',115200,timeout=1); s.write(b'hello\n'); print(s.readline())"
```

(`pyserial` is in the ESP-IDF Python env; add it to the bench `.venv` for the harness.)

### 1.4 How this becomes `hostrun.sh` actions

`BENCH_HARNESS_SPEC.md` §7.1 lists `stm_build` / `stm_flash`; they are **not implemented** in
`hostrun.sh` yet. They map 1:1 onto the two commands above. Proposed shape (same rules as the
existing actions — names, not paths; no shell metacharacters; bounded time; audited; flash
gated by the session window):

| Action | Params | Command line hard-coded in `hostrun.sh` |
|---|---|---|
| `stm_build` | `project` ∈ {`hdemg_h745`}, `config` ∈ {`Debug`,`Release`} | CubeIDE headless build of `firmware/stm32h745/<project>` with `-data $AGENT/cubeide-ws` |
| `stm_flash` | `project`, `core` ∈ {`cm7`,`cm4`} | `STM32_Programmer_CLI -c port=SWD mode=UR -w <resolved elf> -v -rst` |

Put the CubeIDE project in the repo at `firmware/stm32h745/hdemg_h745/` (commit `.ioc`,
`.project`, `.cproject`, `Core/`, `Drivers/`, linker scripts; gitignore `Debug/`, `Release/`).

### 1.5 Migrating off CubeIDE later (optional)

When you want a plain CMake project: generate the same `.ioc` from **standalone STM32CubeMX
≥ 6.11** with *Toolchain/IDE = CMake*, then `cmake --preset Debug && cmake --build --preset
Debug` with the bundled GCC, and flash with the same `STM32_Programmer_CLI` line. Standalone
CubeMX can also regenerate code headless (`STM32CubeMX -q gen.txt` with `config load x.ioc` /
`project generate` / `exit`). Not needed for Stage 2 — §1.1 already gives a GUI-free loop.

---

## 2. One-time project setup in CubeMX (the only GUI step)

*File → New → STM32 Project → **Board Selector** → NUCLEO-H745ZI-Q*, "initialize all
peripherals with default mode" **No** (you will add exactly what is below). Name it `hdemg_h745`.

**Do everything on the CM7.** Leave the CM4 project generated but empty (it boots, waits on the
HSEM, then idles). Assign every peripheral below to the **Cortex-M7** context.

| Block | Setting | Why |
|---|---|---|
| Power | **Direct SMPS supply** (board-selector default; the pack's examples define `USE_PWR_DIRECT_SMPS_SUPPLY`) | Wrong supply config on this board locks the MCU out of SWD — see §9 |
| Clock | HSE **bypass** 8 MHz (ST-LINK MCO) → PLL1 M=4 N=400 P=2 → **SYSCLK 400 MHz**, VOS1, AHB /2 = 200 MHz | Copied from `STM32Cube_FW_H7_V1.12.1/Projects/NUCLEO-H745ZI-Q/Examples/GPIO/GPIO_EXTI/CM7/Src/main.c`. 480 MHz needs VOS0, which the direct-SMPS supply does not allow |
| QUADSPI (2a) | Bank1, 4 lines, pins in §3.1 | Kernel clock = HCLK3 = 200 MHz |
| MDMA | QUADSPI FIFO-threshold request (CubeMX adds it under QUADSPI → DMA) | QUADSPI is served by **MDMA**, not DMA1/2 |
| SDMMC1 (2b) | "SD 4 bits Wide bus", pins in §3.3; you will drive it with `HAL_SDIO_*`, not `HAL_SD_*` | |
| USART3 | Async, 115200 8N1, PD8/PD9 | Control plane over the ST-LINK VCP |
| TIM2 | 32-bit, internal clock, prescaler → **1 MHz**, free-running | `t_stm` timestamp, in **µs** (§4.2) |
| GPIO EXTI | READY input, rising edge, no pull (ESP drives it) | §5.4 |
| RNG | enabled | Seeds the payload PRNG |
| CORTEX_M7 | I-cache + D-cache **on**, MPU region for the DMA ring (§9) | |
| SYS | Debug: Serial Wire | |

Then *Generate Code* once, close the GUI, and use §1 from here on.

---

## 3. Wiring

General rules: **common ground first** (one wire per board pair, plus a ground next to CLK);
wires ≤ 10 cm for QSPI above 20 MHz; every board powered from its own USB; all logic is
3.3 V on both sides (no level shifting). Disconnect H7 ↔ ESP wires before flashing an ESP until
§9's strapping checks have passed.

### 3.1 H745 QUADSPI pins (chosen to avoid SDMMC1 and the VCP)

From the CubeMX MCU database (`db/mcu/STM32H745ZITx.xml`). This set leaves PC8–PC12 / PD2 free,
so both Stage-2 harnesses can stay wired to the H7 at once.

| Signal | H745 pin | Alternatives in the DB |
|---|---|---|
| QUADSPI_CLK | **PB2** | PF10 |
| QUADSPI_BK1_NCS | **PG6** | PB6, PB10 (PB10 is also USART3_TX) |
| QUADSPI_BK1_IO0 | **PD11** | PF8, PC9 (SDMMC1_D1) |
| QUADSPI_BK1_IO1 | **PD12** | PF9, PC10 (SDMMC1_D2) |
| QUADSPI_BK1_IO2 | **PE2** | PF7 |
| QUADSPI_BK1_IO3 | **PD13** | PF6, PA1 |
| READY (EXTI in) | any free pin, e.g. **PG9** — VERIFY it is free on the Nucleo morpho header | |

CubeMX assigns the alternate-function numbers; check them in the generated `HAL_QSPI_MspInit`.
Set the QUADSPI GPIO speed to **Very High**.

### 3.2 Stage 2a — QSPI to the ESP

The ESP side is GP-SPI2 as **Slave HD**. Use the chip's **IO_MUX** pins (full speed, no GPIO
matrix delay) — from `components/soc/<chip>/include/soc/spi_pins.h` in the installed IDF.

| Bus line | H745 | **ESP32-S3** (`pins_s3.h`, matches IO_MUX) | **ESP32-C5** — IO_MUX (proposed) |
|---|---|---|---|
| CLK | PB2 | GPIO12 | **GPIO6** |
| CS | PG6 | GPIO10 | **GPIO10** |
| D0 (MOSI) | PD11 | GPIO11 | **GPIO7** ⚠ strap |
| D1 (MISO) | PD12 | GPIO13 | **GPIO2** ⚠ strap |
| D2 (WP) | PE2 | GPIO14 | **GPIO5** |
| D3 (HD) | PD13 | GPIO9 | **GPIO4** |
| READY (ESP → H7) | PG9 | GPIO7 | **GPIO3** — VERIFY free on your DevKit header |

**`firmware/esp32c5/main/pins_c5.h` is wrong for QSPI and must change before Stage 2a.** It is
a copy of the S3 map (CLK 12, D0 11, D1 13, D2 14, D3 9, READY 7). On the C5:

- **GPIO11 / GPIO12 are UART0 TX / RX** (`soc/esp32c5/include/soc/uart_pins.h`) — the console
  and the CP2102N path used to flash the C5. Driving them from the H7 breaks flashing and logs.
- The C5's SPI2 IO_MUX set is MISO 2, HD 4, WP 5, CLK 6, MOSI 7, CS 10 (`spi_pins.h`); the
  S3 pins would go through the GPIO matrix.
- **GPIO2 and GPIO7 are C5 strapping pins** (with 25, 27, 28 — `docs/en/api-reference/peripherals/gpio/esp32c5.inc`).
  STM32 QUADSPI IOs are Hi-Z while NCS is high, so an idle H7 should not disturb boot —
  **VERIFY** by resetting the C5 with the H7 attached and powered, before trusting it.
  If it does disturb boot, fall back to the GPIO matrix on non-strapping pins at ≤ 40 MHz.

This file is ESP firmware (not parity-checked, not used by the Stage 1 synth build) — change it
together with `source_qspi.c`, not tonight.

### 3.3 Stage 2b — SDIO 4-bit to the C5 (separate harness)

Fixed IO_MUX pins, `soc/esp32c5/include/soc/sdio_slave_pins.h`:

| SDIO | H745 (SDMMC1) | C5 | Pull-up |
|---|---|---|---|
| CLK | PC12 | GPIO9 | — |
| CMD | PD2 | GPIO10 | **10–50 kΩ to 3V3, mandatory** |
| D0 | PC8 | GPIO8 | **mandatory** |
| D1 | PC9 | GPIO7 ⚠ strap | **mandatory** |
| D2 | PC10 | GPIO14 | **mandatory** |
| D3 | PC11 | GPIO13 | **mandatory** |

- The **C5 QSPI and SDIO pin sets overlap** (GPIO7, 10 in both). They are two harnesses and
  two ESP builds; never both wired to the C5.
- WROOM modules and devkits do not include the pull-ups (`sdio_slave.rst` §Connections). Without
  them CMD5 times out or, worse, works at 400 kHz and fails at 25 MHz.
- The D1 pull-up holds **strapping pin GPIO7 high at reset**. **VERIFY** in the C5 datasheet
  strapping table what GPIO7 = 1 selects and that the C5 still boots normally with the harness
  attached.
- GPIO13/14 may be the C5's native USB D−/D+ (`pins_c5.h` comment) — fine on a DevKit flashed
  over the CP2102N, but nothing may use the native USB port in this build.

---

## 4. The source — what the H7 actually generates (both links)

The H7 replaces `source_synth.c` one hop upstream, so it must meet the **same measurement
contract**: commanded rate, whole frames, honest accounting.

### 4.1 Frame format — `firmware/bench_common/include/hdemg_frame.h`

270-byte frames: 14-byte packed little-endian header + 128 × int16.

| Field | Bytes | Value |
|---|---|---|
| `magic` | u16 | `0xA55A` |
| `type` | u8 | `0` (RAW16) |
| `chip_id` | u8 | `0xFF` (combined — same as the synth source) |
| `seq` | u32 | +1 per **generated** frame (see §4.3) |
| `t_stm` | u32 | **microseconds**, free-running 32-bit (§4.2) |
| `n_ch` | u16 | `128` |
| payload | 256 | PRNG bytes (§4.4) |

The H745 is little-endian like the ESPs, so a packed C struct is byte-exact. Copy
`hdemg_frame.h` into the H7 project verbatim (or add the repo path to its include dirs) — do not
retype it.

### 4.2 `t_stm` must be in µs

The host computes latency as `arrival_µs − t_src` (`host/bench/receiver.py` `_latencies_us`),
treating the field as microseconds; the synth source stamps `esp_timer_get_time()` (µs). Use
TIM2 at 1 MHz (`htim2.Instance->CNT`). A raw CPU-cycle counter would make every latency number
garbage without any error.

### 4.3 Pacing and loss accounting

- **Generate on the clock, not on demand.** Token bucket on the µs timer, as `pacing.c`:
  frames due = `elapsed_µs × rate_bps / (8 × 270 × 10⁶)`. At 60 Mbit/s that is 27 778 frames/s,
  one every 36 µs. Stamp `seq` and `t_stm` **at generation** and write the frame into a ring.
- **The ring models the ADC, which cannot stall.** If the link is not ready, frames wait in
  the ring (size ≥ 64 KiB ≈ 30 ms at 60 Mbit/s). If the ring overflows, **drop the frame
  but still consume its `seq`** and count `ring_drops`. The host then sees the gap as loss, and
  the H7's own counter tells you the loss happened at the link, not on Wi-Fi.
- **Never block generation on the link.** Blocking turns link back-pressure into a silently
  lower offered load, which reads as a better loss curve than reality.
- Report `achieved_bps` = frames actually generated × 270 × 8 / elapsed (should equal the
  command within 1 %) and `link_bps` = bytes the link accepted. A gap between them is the
  ingress bottleneck the experiment is looking for.

### 4.4 Payload

Use a 32-bit xorshift PRNG **seeded from `seq`** (seed with `seq ^ run_seed`, `run_seed` from
the RNG at `start`). It is random bytes for every purpose the plan cares about, and a receiver
can regenerate and check it — the only way to catch link corruption that leaves the header
intact (§6, §9).

### 4.5 Batching

Each link transaction carries **whole frames only**, at most `floor(8192 / 270) = 30` frames
= **8100 bytes** (the ESP's `PIPE_BUF_BYTES` is 8192, `pipe.h`). Send whatever has accumulated
(1–30 frames) whenever the link is ready, so latency stays low at low rates and transactions
stay large at high rates.

---

## 5. Stage 2a — QUADSPI → ESP GP-SPI Slave HD

### 5.1 Phase table, read from the installed IDF

Source: `components/hal/esp32s3/include/hal/spi_ll.h` lines 80–89 (base commands), 1609–1641
(`spi_ll_get_slave_hd_command`, `spi_ll_get_slave_hd_dummy_bits`); **identical** in
`components/hal/esp32c5/include/hal/spi_ll.h` lines 80–89, 1267–1300.

| Field | Value | Note |
|---|---|---|
| Command | **8 bits, always 1 line** | the line mode of the *rest* of the transaction is encoded in the command's high nibble |
| Base commands | `WRBUF 0x01` `RDBUF 0x02` `WRDMA 0x03` `RDDMA 0x04` `SEG_END 0x05` `EN_QPI 0x06` `WR_END 0x07` `INT0 0x08` `INT1 0x09` `INT2 0x0A` | |
| Mode prefix | `0x00` all 1-line · `0x10` data 2-line · `0x50` addr+data 2-line · **`0x20` data 4-line** · **`0xA0` addr+data 4-line** | `SEG_END` and `EN_QPI` never take a prefix |
| Address | **8 bits** | shared-register byte offset for BUF commands; `0` for DMA commands |
| Dummy | **8 cycles in every line mode** | `spi_ll_get_slave_hd_dummy_bits()` returns 8 unconditionally on both chips |
| Data | 1/2/4 lines per prefix | |
| SPI mode | **0** (CPOL 0, CPHA 0) | IDF's own HD masters use `.mode = 0` |

**The trap the source header warns about:** the older `esp_serial_slave_link` (`essl_spi.c`)
sends **4** dummy cycles in DIO/QIO modes. The installed v6.0-dev LL says **8** for every mode.
Use 8 and confirm it on the wire (§7.1). A wrong count shifts every data byte by one nibble:
the transaction "succeeds", the ESP receives plausible garbage, and it looks like loss.

### 5.2 The transactions the H7 issues

All three use **command in 1 line, 8-bit address, 8 dummy cycles**:

| Purpose | Cmd | Addr | Data | HAL `FunctionalMode` |
|---|---|---|---|---|
| Push frames | **`0xA3`** (WRDMA, addr+data 4-line) | `0x00`, 4 lines | 4 lines, N bytes (whole frames) | indirect write |
| End the buffer | **`0x07`** (WR_END, 1-line) | `0x00`, 1 line | none | indirect, no data |
| Read credit word | **`0x02`** (RDBUF, 1-line) | `0x3C` (word 15), 1 line | 1 line, 4 bytes | indirect read |

`0x23` (address on 1 line, data on 4) is equally valid if you prefer one address mode for
everything. The 1-line `WR_END` form is what IDF's own test helper sends
(`components/driver/test_apps/components/test_driver_utils/test_spi_utils.c`,
`essl_sspi_hd_dma_trans_seg`: cmd + addr + 1 dummy byte on one line).

HAL mapping (`stm32h7xx_hal_qspi.h`):

```c
/* Init (DCR/CR) */
hqspi.Init.ClockPrescaler     = 19;                        // 200 MHz/(19+1) = 10 MHz for bring-up
hqspi.Init.FifoThreshold      = 4;
hqspi.Init.SampleShifting     = QSPI_SAMPLE_SHIFTING_HALFCYCLE; // helps RDBUF reads (slave MISO delay)
hqspi.Init.FlashSize          = 31;                        // address checks never trip
hqspi.Init.ChipSelectHighTime = QSPI_CS_HIGH_TIME_4_CYCLE; // give the slave a clean CS-high gap
hqspi.Init.ClockMode          = QSPI_CLOCK_MODE_0;
hqspi.Init.FlashID            = QSPI_FLASH_ID_1;
hqspi.Init.DualFlash          = QSPI_DUALFLASH_DISABLE;

/* WRDMA 0xA3 */
QSPI_CommandTypeDef wr = {
    .InstructionMode = QSPI_INSTRUCTION_1_LINE,  .Instruction = 0xA3,
    .AddressMode     = QSPI_ADDRESS_4_LINES,     .AddressSize = QSPI_ADDRESS_8_BITS, .Address = 0,
    .AlternateByteMode = QSPI_ALTERNATE_BYTES_NONE,
    .DummyCycles     = 8,
    .DataMode        = QSPI_DATA_4_LINES,        .NbData = n_frames * 270,
    .DdrMode = QSPI_DDR_MODE_DISABLE, .DdrHoldHalfCycle = QSPI_DDR_HHC_ANALOG_DELAY,
    .SIOOMode = QSPI_SIOO_INST_EVERY_CMD,
};
HAL_QSPI_Command(&hqspi, &wr, 10);
HAL_QSPI_Transmit_DMA(&hqspi, ring_ptr);          // MDMA; wait for TxCplt before WR_END

/* WR_END 0x07 — no data phase: the transfer starts as soon as the command is written */
QSPI_CommandTypeDef end = wr;
end.Instruction = 0x07; end.AddressMode = QSPI_ADDRESS_1_LINE;
end.DataMode = QSPI_DATA_NONE; end.NbData = 0;
HAL_QSPI_Command(&hqspi, &end, 10);

/* RDBUF 0x02, word 15 */
QSPI_CommandTypeDef rd = end;
rd.Instruction = 0x02; rd.Address = 15 * 4;
rd.DataMode = QSPI_DATA_1_LINE; rd.NbData = 4;
HAL_QSPI_Command(&hqspi, &rd, 10);
HAL_QSPI_Receive(&hqspi, (uint8_t *)&credit, 10);
```

In single-line mode the H7 receives on IO1 (= ESP D1/MISO), which is how the bus is wired.

### 5.3 Clock and headroom

Per 30-frame transaction at 4 lines: 8 (cmd) + 2 (addr) + 8 (dummy) + 2 × 8100 (data) ≈ 16 218
cycles, plus a 24-cycle `WR_END` → **99.7 % efficiency**. Usable link rate ≈ 4 × f_QSPI:

| `ClockPrescaler` | f_QSPI | Link ≈ | vs T3 (61.4 Mbit/s) |
|---|---|---|---|
| 19 | 10 MHz | 40 Mbit/s | bring-up only |
| 9 | 20 MHz | 80 Mbit/s | 77 % busy — too tight |
| **7** | **25 MHz** | **100 Mbit/s** | 61 % busy — target |
| 4 | 40 MHz | 160 Mbit/s | if signal integrity allows |

The ESP slave's own ceiling: `spi_slave.rst` §"SCLK Frequency Requirements" gives
`{IDF_TARGET_MAX_FREQ}` per chip, and IDF's HD test notes MISO is only stable to ~10 MHz for
reads latched on the same edge — hence `SampleShifting = HALFCYCLE` for `RDBUF`. Step the clock
up only after §7 passes at each step.

### 5.4 Flow control — credit counter, READY as the wake-up

The plan makes a slave→master ready line mandatory: without it the master overruns the slave at
exactly the loads being measured. **A READY level sampled right after `WR_END` is racy.** The
ESP retires the buffer in an ISR some microseconds later, so the H7 can still see the *previous*
buffer's READY=1 and write into nothing. That loss is silent.

Use the scheme IDF's append-mode example is built on
(`examples/peripherals/spi_slave_hd/append_mode`, `SPI_SLAVE_HD_APPEND_MODE`):

- **ESP:** in `cb_recv_dma_ready` (an RX buffer was loaded into the DMA chain), increment
  `loaded` and publish it in shared-register word 15 (`spi_slave_hd_write_buffer(…, 15*4, …)`),
  then drive READY high. Drive READY low when `loaded == completed`.
- **H7:** keep `sent` (buffers it has ended with `WR_END`). `credit = loaded − sent` (u32
  wrap-safe). Send only while `credit > 0`. When it hits 0, wait for a READY **rising-edge EXTI**
  (clear the pending flag *before* the last `WR_END`), then re-read word 15.
- **Torn reads:** the master reads shared registers byte by byte while the slave writes words
  (`spi_slave_hd.rst` lines 114–122). Read word 15 **twice and accept only matching values**.

Correctness comes from the counter; READY only saves the H7 from polling `RDBUF` in a loop.
Count `ready_timeouts` (no credit within e.g. 10 ms) and keep generating into the ring meanwhile.

### 5.5 H7 memory and cache — do not skip

- QUADSPI DMA is **MDMA**. Put the frame ring in **AXI SRAM** (`0x2400 0000`, 512 KiB). The
  pack's CubeIDE linker script for this board (`…/NUCLEO-H745ZI-Q/…/STM32CubeIDE/CM7/STM32H745ZITX_FLASH.ld`)
  places `.data`, `.bss` and the stack in **DTCM** (`RAM`, `0x2000 0000`, 128 KiB) and does not
  declare AXI SRAM at all. Add `RAM_D1 (xrw) : ORIGIN = 0x24000000, LENGTH = 512K` to `MEMORY`,
  a `.axi_ring (NOLOAD) : { *(.axi_ring) } >RAM_D1` section, and place the ring with
  `__attribute__((section(".axi_ring"), aligned(32)))`.
- The CM7 D-cache is write-back. Either mark the ring **non-cacheable with an MPU region**
  (simplest — recommended), or `SCB_CleanDCache_by_Addr()` every span before handing it to
  MDMA (32-byte aligned and sized). If you get this wrong, DMA sends **stale bytes**. Headers
  still look right if they were flushed earlier, so the failure is intermittent and silent.
- Bring-up order: polled `HAL_QSPI_Transmit` → MDMA. Polled is plenty at 10 MHz and removes
  two variables while you validate the phase table.

---

## 6. Stage 2b — SDMMC1 host → C5 SDIO slave

### 6.1 Host driver

STM32Cube_FW_H7 **V1.12.1 ships an SDIO-card host driver** — `stm32h7xx_hal_sdio.h`:
`HAL_SDIO_Init` (identification: CMD0/CMD5/CMD3/CMD7), `HAL_SDIO_SetDataBusWidth`,
`HAL_SDIO_ConfigFrequency`, `HAL_SDIO_SetSpeedMode`, `HAL_SDIO_EnableIOFunction`,
`HAL_SDIO_SetBlockSize`, `HAL_SDIO_ReadDirect`/`WriteDirect` (CMD52),
`HAL_SDIO_ReadExtended`/`WriteExtended[_DMA]` (CMD53). The LL also exposes
`SDMMC_SDIO_CmdReadWriteDirect/Extended`. No hand-rolled command layer needed.

Bring-up sequence: init at **400 kHz** → 4-bit bus → enable function 1 → function-1 block size
**512** → `ConfigFrequency(25 MHz)` (default speed) → later high speed at 50 MHz if clean.

SDMMC1's internal DMA (IDMA) reaches **AXI SRAM only** — same ring placement and cache rule as §5.5.

### 6.2 ESP SDIO slave protocol — register map from the installed IDF

Host-visible function-1 addresses are the SLCHOST register offsets
(`components/soc/esp32c5/register/soc/sdio_slc_host_reg.h`, base `DR_REG_SLCHOST_BASE = 0x60018000`):

| Offset | Register | Use |
|---|---|---|
| `0x044` | `SLC0HOST_TOKEN_RDATA` | **bits [27:16] = TOKEN1**: 12-bit running count of receive buffers the slave has loaded |
| `0x058` | `SLC0HOST_INT_ST` | slave→host interrupt status |
| `0x060` | `PKT_LEN` | slave→host only; unused here |
| `0x06C`… | `CONF_W0`… | shared 8-bit registers (52 of them) |
| `0x08C` | `CONF_W7` | host→slave interrupt bits (`sdio_slave.rst`: "write a bit to 0x08D") |
| `0x0D4` | `SLC0HOST_INT_CLR` | clear slave→host interrupts |

**The FIFO:** a CMD53 write to function 1 at address **`0x1F800 − remaining_length`**
(`sdio_slave.rst` line 136: "requested length = 0x1F800 − address"). That encoding of the length
in the address is how the slave knows where a packet ends.

### 6.3 Sending one packet (whole frames, ≤ 8100 B)

This mirrors ESSL's `essl_sdio_send_packet`. ESSL is now in `espressif/esp_serial_slave_link`
on the component registry, **not** in the installed IDF tree. **VERIFY** this sequence against
that source before relying on it.

1. `credit = (TOKEN1 − buffers_sent) & 0xFFF` (read `0x044` with CMD53 4-byte or 4 × CMD52).
2. `need = ceil(len / RECV_BUF_SIZE)`. If `need > credit`, wait and re-read; keep generating
   into the ring meanwhile.
3. `remaining = len`; while `remaining > 0`: if `remaining ≥ 512` send
   `floor(remaining/512)` blocks (block mode), else `remaining` bytes (byte mode, ≤ 512); each
   CMD53 targets address **`0x1F800 − remaining`** with incrementing address; `remaining −=` the
   chunk.
4. `buffers_sent += need` (mod 4096).

`RECV_BUF_SIZE` is the C5's `sdio_slave_config_t.recv_buffer_size`. Pick **8192**, so one
30-frame packet is one buffer, and hard-code it on both sides with an assert. A mismatch
silently splits or merges packets.

### 6.4 Timing knob

The C5 slave's sampling/driving edges are `sdio_slave_config_t.timing`
(`hal/sdio_slave_types.h`): `PSEND_PSAMPLE` (default, HS) or `NSEND_PSAMPLE` (default for DS).
If CMD53s CRC-fail at 25 MHz but pass at 400 kHz, this (with the pull-ups) is the first thing to
change. The H7 side's equivalent is `SDMMC_CLKCR.NEGEDGE`.

### 6.5 Flow control

The token counter **is** the credit scheme. No READY GPIO is needed. The slave can also assert
the SDIO in-band interrupt on DAT1 when it loads buffers, but polling the token is simpler for
bring-up.

---

## 7. Verify the link before any Wi-Fi number is taken

Every check here runs **with Wi-Fi out of the loop**, so a link fault cannot masquerade as a
radio result.

### 7.1 QSPI on a logic analyzer (no ESP attached)

Loop `0xA3 + 270 bytes`, then `0x07`, at 1 MHz into nothing. Decode with any 6-channel analyzer
(sigrok/PulseView "SPI" decoder for the command byte, then "QSPI/nibble" by hand). Confirm, by
counting edges:

1. Command `0xA3` on IO0 alone, MSB first, 8 clocks.
2. Address: **2 clocks** on IO0–IO3.
3. **Exactly 8 idle clocks** (dummy).
4. Data starts with `5A A5` (magic, little-endian) on the first nibbles, high nibble first.
5. CS high ≥ 4 clocks between transactions.

If the dummy count is not exactly 8, stop. Nothing downstream can be trusted.

### 7.2 Link-only test on the ESP (`BENCH_LINK_TEST`)

An ESP build whose sink is a **verifier**, not the network. For every received buffer, check
`len % 270 == 0` and `magic == 0xA55A` at every 270-byte stride. Check `seq` is contiguous
except where the H7 reports `ring_drops`, and regenerate the PRNG payload from `seq`. Count
`len_errors`, `magic_errors`, `seq_gaps` and `payload_errors`, and report them on the control
plane `stat`. Pass criterion: **zero errors for 10 minutes at each clock step and each offered
load in the stage-2 sweep**. Do this for S3-QSPI, C5-QSPI and C5-SDIO.

### 7.3 Only then, Wi-Fi

Run one stage-2 cell at a single low load and compare against the same chip's Stage 1 synth
number at that load. They should agree within the Stage 1 repeat spread; if they do not at
low load, the ingress path is costing goodput before the radio is even busy.

---

## 8. Plugging into the harness (what still has to be written)

None of this exists yet. Tonight's Stage 1 run does not need it.

**H7 control plane** — line-oriented ASCII over the VCP (115200), JSON replies, deliberately
the same verbs as the ESP's UDP control plane (`firmware/bench_common/control.c`):

```
hello → {"ok":true,"proto":1,"chip":"stm32h745","fw_sha":"…","link":"qspi","qspi_hz":25000000}
cfg rate_bps=20000000 frame_bytes=270 link=qspi dur_s=62 → {"ok":true,"applied":{…}}
start → {"ok":true,"t0_us":…}
stat  → {"frames":…,"achieved_bps":…,"link_bps":…,"ring_drops":…,"ready_timeouts":…,"credit_waits":…}
stop  → {"ok":true,"summary":{…same fields…}}
```

**Host side** (`host/bench/`):

- `drivers.py` — an `H7Control` client on `/dev/cu.usbmodem21103` (pyserial); `ensure_flashed`
  learns a third device. The H7 build depends on `link` only, so it flashes at most twice per stage.
- `orchestrator.run_one` for `source ∈ {qspi, sdio}`: `cfg` the ESP (sink, transport, dst) **and**
  the H7 (rate) → `start` ESP → `start` H7 → receiver → `stop` H7 → `stop` ESP.
  `commanded_bps` goes to the H7; take `achieved_bps` from the **H7** (the source), and store
  `link_bps`, `ring_drops`, `ready_timeouts` and the ESP's ingress counters in the ledger record.
- `gates.post_run` — **invalid** if the ESP reports any `magic_errors`/`len_errors` (link
  corruption is a rig fault, not a result); `ring_drops > 0` is a legitimate result (the link
  could not keep up) and is labelled, like `source_limited`.
- `hostrun.sh` — `stm_build` / `stm_flash` per §1.4.
- `matrices/stage2.yaml` already lists the 10 cells (2a: c5@5, c5@2.4, s3@2.4 × udp/tcp;
  2b: c5@5, c5@2.4 × udp/tcp), tuned only. The C5 needs **separate builds and harnesses** for
  QSPI and SDIO, so order the run list `link` before `band` (one rewire per stage, not per cell).

**ESP side** (still `#error` placeholders):

- `source_qspi.c` (S3 and C5): `spi_slave_hd_init` on SPI2 with the IO_MUX pins, quad bus flags,
  `SPI_SLAVE_HD_APPEND_MODE`; RX descriptors pointing **directly at pipe buffers** (that is the
  `zero_copy_dma` common lever); `cb_recv_dma_ready` → credit word + READY; on completion
  `pipe_submit(buf, trans_len)` after the whole-frame check; `source_achieved_bps()` = bytes
  ingested per second.
- `source_sdio.c` (C5): `sdio_slave_initialize` with `recv_buffer_size = 8192`, register the pipe
  buffers with `sdio_slave_recv_register_buf`, `sdio_slave_recv_load_buf`, then
  `sdio_slave_recv_packet` → whole-frame check → `pipe_submit`.
- `pins_c5.h` — corrected QSPI map (§3.2).
- A `BENCH_LINK_TEST` sink (§7.2).

---

## 9. Silent-corruption checklist

Each of these produces plausible numbers rather than an error:

1. **Dummy cycles ≠ 8**, or 4-line address with a `0x2x` command (or vice versa). Data is shifted;
   the ESP gets garbage of the right length. → §7.1, §7.2 `magic_errors`.
2. **READY sampled as a level after `WR_END`.** Writes land in a retired buffer. → credit
   counter, §5.4.
3. **D-cache on the DMA ring.** Stale bytes go out intermittently. → MPU non-cacheable region, §5.5.
4. **Ring in DTCM** — where the default CM7 linker script puts every buffer. SDMMC1 IDMA cannot
   reach it. → explicit `RAM_D1` region + section, §5.5.
5. **`t_stm` not in µs.** Every latency figure is wrong. → TIM2 at 1 MHz, §4.2.
6. **Blocking generation on the link.** Back-pressure becomes a lower offered load and the loss
   curve flatters the link. → ring + `ring_drops`, §4.3.
7. **Frame split across transactions or buffers.** The receiver resyncs on magic and counts it
   as loss. → whole frames only, ≤ 8100 B, §4.5.
8. **SDIO `0x1F800 − remaining` address** computed from the chunk length instead of the
   *remaining packet* length. Packets merge or split. → §6.3.
9. **`recv_buffer_size` mismatch** between host and C5. Same symptom as 8. → hard-code + assert.
10. **Missing SDIO pull-ups.** Works at 400 kHz, fails or flakes at 25 MHz. → §3.3.
11. **Wrong power-supply config (LDO instead of direct SMPS).** The H745 locks out SWD. To recover:
    BOOT0 high (jumper on the morpho header), power-cycle, then `STM32_Programmer_CLI -c port=SWD
    mode=UR -e all`. Using the board selector avoids it.
12. **C5 strapping pins (GPIO2/7) held by the harness at reset.** The C5 boots into the wrong mode
    and looks like a dead board. → §3.2/§3.3 VERIFY.

---

## 10. Suggested order for tonight (H7 only — no ESP wiring)

1. CubeMX: new board-selector project `hdemg_h745`, peripherals per §2, Generate Code. *(GUI, ~15 min)*
2. Build headless (§1.1) and flash (§1.2) a blink + VCP `hello` echo. That proves the
   agent-drivable loop end to end.
3. TIM2 µs counter, frame generator + ring + pacing (§4), `cfg/start/stop/stat` over the VCP (§8).
   Check `achieved_bps` against the command at 4, 20 and 60 Mbit/s with no link attached
   (generate-and-drop).
4. QUADSPI polled `0xA3`/`0x07` at 10 MHz, verified on the logic analyzer (§7.1).
5. Stop there. ESP-side `source_qspi.c`, the C5 pin fix, and any H7↔ESP wiring come after the
   Stage 1 night, once the boards are free.

## 11. Open decisions (yours)

1. **Bench H7:** this guide targets the attached **Nucleo-H745ZI-Q**. Is the Rev-1 PCB (plan
   §9.1.4) the eventual master? If so, its QUADSPI/SDMMC pins and ready GPIO need their own §3.
2. **Flow control in 2a:** credit counter + READY wake-up (recommended, §5.4), or a pure READY
   edge handshake (simpler ESP code, needs a guaranteed minimum low pulse per buffer, racier).
3. **C5 QSPI pins:** IO_MUX set with the two strapping pins (recommended; verify boot), or GPIO
   matrix on non-strapping pins at a lower clock ceiling.
4. **Commit the CubeIDE project into this repo** at `firmware/stm32h745/` (recommended — then
   `parity`-style review and the headless build both see it), or keep it in `~/STM32CubeIDE`.
