/* source_qspi.c — Stage 2a ingress: GP-SPI Slave HD, 4-line data phase.
 *
 * NOT YET IMPLEMENTED. Stage 1 (paced synthetic source) runs first and is the control
 * arm this will be subtracted from; building this before Stage 1 has a number would
 * mean measuring the ingress against nothing.
 *
 * Before writing it, two things must be settled or the bench time is wasted:
 *   1. Read the phase table — command byte, address width, dummy cycles, data-line
 *      count — out of the INSTALLED ESP-IDF source and configure the STM32H745's
 *      QUADSPI field for field. A one-cycle dummy mismatch corrupts silently and looks
 *      exactly like a radio problem.
 *   2. Wire PIN_READY. The ESP asserts it when a DMA rx buffer is queued and the master
 *      gates the next transaction on it. Without it the master overruns the slave at
 *      precisely the load being measured.
 *
 * The contract this must meet: fill pool buffers with WHOLE frames only, call
 * pipe_submit(), and report source_achieved_bps() so a source-limited cell is labelled
 * rather than read as a radio ceiling.
 */
#include "pipe.h"
#include "pins_c5.h"

#error "source_qspi.c is a Stage 2 placeholder — build with -DBENCH_INGRESS=synth"
