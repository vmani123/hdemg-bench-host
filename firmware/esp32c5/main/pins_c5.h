/* pins_c5.h — ESP32-C5 bench pin map (target-specific).
 *
 * QSPI (Stage 2a) uses the chip's SPI2 IO_MUX pins (soc/esp32c5/include/soc/spi_pins.h):
 * full speed, no GPIO-matrix delay. The S3's numbers must NOT be reused here: on the C5
 * GPIO11/12 are UART0 TX/RX, the console and the path the board is flashed through.
 *
 * GPIO2 and GPIO7 are STRAPPING pins. An idle STM32 QUADSPI leaves its IO lines Hi-Z, so
 * it should not disturb boot, but that has to be checked on the bench — reset the C5
 * with the harness attached and powered — before these pins are trusted
 * (docs/STM32_STAGE2_GUIDE.md §3.2). */
#pragma once
#define PIN_QSPI_CS    10
#define PIN_QSPI_CLK   6
#define PIN_QSPI_D0    7     /* MOSI — strapping pin */
#define PIN_QSPI_D1    2     /* MISO — strapping pin */
#define PIN_QSPI_D2    5     /* WP */
#define PIN_QSPI_D3    4     /* HD */
/* Slave -> master ready. VERIFY this pin is free on the DevKit header before wiring. */
#define PIN_READY      3

/* SDIO 2.0 slave (Stage 2b) — fixed IO_MUX pins, soc/esp32c5/include/soc/sdio_slave_pins.h.
 * External 10-50 kohm pull-ups to 3V3 on CMD and DAT0-3 are MANDATORY; modules and
 * devkits do not include them. DAT1 is GPIO7, a strapping pin that the pull-up holds
 * high at reset: check the C5 still boots with the harness attached. The SDIO and QSPI
 * pin sets overlap (GPIO7, GPIO10): they are two harnesses and two builds, never both
 * wired. GPIO13/14 may be the native USB D-/D+; nothing may use that port in this build. */
#define PIN_SDIO_CLK    9
#define PIN_SDIO_CMD   10
#define PIN_SDIO_D0     8
#define PIN_SDIO_D1     7
#define PIN_SDIO_D2    14
#define PIN_SDIO_D3    13
