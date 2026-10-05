/* source_qspi.c — Stage 2a ingress entry point for the ESP32-C5 (target-specific).
 *
 * The implementation is shared: firmware/bench_common/ingress_qspi.c runs GP-SPI2 as a
 * Slave HD with a 4-line data phase, receiving straight into pool buffers from the
 * STM32H745's QUADSPI. This file only supplies the pins. See docs/STM32_STAGE2_GUIDE.md
 * and ingress.h for the contract and the link-test mode. */
#include "pipe.h"
#include "control.h"
#include "ingress.h"
#include "pins_c5.h"

void source_start(void)
{
    static const ingress_qspi_pins_t pins = {
        .cs = PIN_QSPI_CS, .clk = PIN_QSPI_CLK,
        .d0 = PIN_QSPI_D0, .d1 = PIN_QSPI_D1, .d2 = PIN_QSPI_D2, .d3 = PIN_QSPI_D3,
        .ready = PIN_READY,
    };
    ingress_qspi_start(&pins);
}

void source_stop(void) { control_stop_run(); }

uint64_t source_achieved_bps(void) { return ingress_achieved_bps(); }
