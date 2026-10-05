/* hdemg_link.h — the Stage 2 wired-ingress contract between the STM32H745 master and
 * the ESP slave. Shared verbatim by firmware/stm32h745 and the ESP ingress sources;
 * mirrored by host/bench/frame.py (payload generator). Like hdemg_frame.h, a change
 * here is a contract break, not a refactor.
 *
 * See docs/STM32_STAGE2_GUIDE.md §4-§6. */
#pragma once
#include <stdint.h>
#include "hdemg_frame.h"

/* ---- batching (both links) --------------------------------------------------------
 * A link transaction carries WHOLE frames only, never more than one ESP pipe buffer
 * (PIPE_BUF_BYTES = 8192): floor(8192 / 270) = 30 frames = 8100 bytes. */
#define HDEMG_LINK_BUF_BYTES    8192u
#define HDEMG_LINK_MAX_FRAMES   (HDEMG_LINK_BUF_BYTES / HDEMG_RAW16_128CH)

/* ---- payload ------------------------------------------------------------------------
 * The 256 payload bytes of a wired-ingress frame are a xorshift32 stream seeded from the
 * frame's own seq and a per-run seed, so any receiver that knows the seed can
 * regenerate and compare them. That is the only way to catch link corruption that
 * leaves the 14-byte header intact. Words are stored little-endian. */
#define HDEMG_PAYLOAD_WORDS     ((HDEMG_RAW16_128CH - HDEMG_HDR_BYTES) / 4)   /* 64 */

static inline uint32_t hdemg_xs32(uint32_t x)
{
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return x;
}
static inline uint32_t hdemg_payload_state0(uint32_t seq, uint32_t seed)
{
    uint32_t x = seq ^ seed;
    return x ? x : 0x9E3779B9u;      /* xorshift has a fixed point at 0 */
}

/* ---- Stage 2a: ESP GP-SPI Slave HD shared registers (ESP -> H7) -------------------
 * Byte offsets into the slave's 64-byte shared buffer, read by the master with RDBUF.
 * Credit scheme (guide §5.4): the master may end a buffer only while
 * (LOADED - sent) > 0, where `sent` is its own count of WR_END commands. COMPLETED lets
 * the master resynchronise `sent` when it (re)opens the link and cross-check it later.
 * Both counters are free-running since the slave driver came up, never reset per run. */
#define HDEMG_QSPI_REG_MAGIC       (13 * 4)
#define HDEMG_QSPI_REG_COMPLETED   (14 * 4)   /* RX buffers finished (WR_END seen)      */
#define HDEMG_QSPI_REG_LOADED      (15 * 4)   /* RX buffers handed to the DMA chain     */
#define HDEMG_QSPI_MAGIC           0x51324148u /* "HA2Q" little-endian: slave is alive   */

/* Slave HD command bytes (installed IDF: hal/<chip>/include/hal/spi_ll.h). The high
 * nibble selects the line mode of the rest of the transaction. */
#define HDEMG_QSPI_CMD_RDBUF       0x02u      /* all single line                        */
#define HDEMG_QSPI_CMD_WRDMA_1_4   0x23u      /* address 1 line, data 4 lines           */
#define HDEMG_QSPI_CMD_WRDMA_4_4   0xA3u      /* address 4 lines, data 4 lines          */
#define HDEMG_QSPI_CMD_WR_END      0x07u      /* single line                            */
#define HDEMG_QSPI_DUMMY_CYCLES    8u         /* in every line mode on this IDF         */

/* ---- Stage 2b: ESP SDIO slave ------------------------------------------------------
 * The installed IDF caps an SDIO-slave receive buffer at 4092 bytes
 * (SDIO_SLAVE_RECV_MAX_BUFFER, sdio_slave.h), so one packet = one receive buffer = at
 * most 15 whole frames (4050 bytes): half of what the QUADSPI link carries per
 * transaction. The size is hard-coded on both sides — a mismatch silently splits or
 * merges packets (guide §6.3). */
#define HDEMG_SDIO_RECV_BUF_BYTES  4092u
#define HDEMG_SDIO_MAX_FRAMES      (HDEMG_SDIO_RECV_BUF_BYTES / HDEMG_RAW16_128CH)   /* 15 */
#define HDEMG_SDIO_BLOCK_BYTES     512u
#define HDEMG_SDIO_FIFO_ADDR_TOP   0x1F800u   /* CMD53 address = TOP - remaining length */
#define HDEMG_SDIO_REG_TOKEN_RDATA 0x044u     /* bits [27:16] = TOKEN1, 12-bit counter  */
