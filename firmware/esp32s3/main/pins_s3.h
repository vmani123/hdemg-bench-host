/* pins_s3.h — ESP32-S3 bench pin map (target-specific).
 * S3 has NO SDIO slave, so QSPI (GP-SPI Slave HD, 4-line data phase) is the only
 * wired ingress available — which is also why QSPI is the common comparison link. */
#pragma once
#define PIN_QSPI_CS    10
#define PIN_QSPI_CLK   12
#define PIN_QSPI_D0    11
#define PIN_QSPI_D1    13
#define PIN_QSPI_D2    14
#define PIN_QSPI_D3    9
/* Slave -> master ready. MANDATORY: without it the master overruns the slave at
 * exactly the load being measured. */
#define PIN_READY      7
