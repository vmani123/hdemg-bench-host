/* source_sdio.c — Stage 2b ingress entry point: SDIO 4-bit slave. C5 ONLY.
 *
 * The implementation is firmware/bench_common/ingress_sdio.c. The SDIO slave's pins are
 * fixed by the chip (IO_MUX), so there is nothing target-specific to supply beyond
 * choosing this source.
 *
 * This is the C5-only extension arm: it is compared against the C5's OWN QSPI number
 * (same chip, clean comparison) and never directly against an S3 number, because the S3
 * has no SDIO slave at all.
 *
 * Note when reading published figures: Espressif's SDIO 4-bit throughput row
 * (79.5 UDP / 53.4 TCP) was taken in a SHIELDED chamber on a C6 co-processor, while the
 * QSPI rows are open air on other parts. Comparing those two directly is a
 * chamber-versus-air error. This arm exists to measure both links on ONE rig instead.
 */
#include "pipe.h"
#include "control.h"
#include "ingress.h"

void source_start(void) { ingress_sdio_start(); }

void source_stop(void) { control_stop_run(); }

uint64_t source_achieved_bps(void) { return ingress_achieved_bps(); }
