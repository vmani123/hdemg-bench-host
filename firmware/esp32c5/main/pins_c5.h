/* pins_c5.h — ESP32-C5 bench pin map (target-specific). */
#pragma once
#define PIN_QSPI_CS    10
#define PIN_QSPI_CLK   12
#define PIN_QSPI_D0    11
#define PIN_QSPI_D1    13
#define PIN_QSPI_D2    14
#define PIN_QSPI_D3    9
#define PIN_READY      7

/* SDIO 2.0 slave — fixed pins per the ESP-AT documentation. VERIFY against the C5
 * datasheet before wiring: whether any are boot straps, and whether GPIO13/14 double as
 * native USB D+/D-. External 10-90 kohm pull-ups on CMD and DAT0-3 are MANDATORY;
 * WROOM modules do not include them. */
#define PIN_SDIO_CLK    9
#define PIN_SDIO_CMD   10
#define PIN_SDIO_D0     8
#define PIN_SDIO_D1     7
#define PIN_SDIO_D2    14
#define PIN_SDIO_D3    13
