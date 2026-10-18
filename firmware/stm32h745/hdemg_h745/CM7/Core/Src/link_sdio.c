/* link_sdio.c — Stage 2b: SDMMC1 as an SDIO host -> ESP32-C5 SDIO slave (guide §6).
 *
 * NOT YET RUN AGAINST HARDWARE. Written from the HAL SDIO driver and the IDF sdio_slave
 * documentation.
 *
 * Wiring (docs/CAMPAIGN_PROTOCOL.md §2, all SDMMC1 AF12):  CK PC12 · CMD PD2 · D0 PC8 ·
 * D1 PC9 · D2 PC10 · D3 PC11. There are NO external pull-ups: the H7 enables its internal
 * pull-ups on CMD and D0-D3 and the C5's slave sets SDIO_SLAVE_FLAG_INTERNAL_PULLUP, about
 * 20 kOhm together. That is weak, so the bus is only ever run in default-speed mode
 * (<= LINK_SDIO_HZ_MAX, 25 MHz), and the whole card set-up (identification, bus width,
 * function enable, block size) is done at 400 kHz; the data clock is set last. Pins are
 * only driven while the link is open; PD2 and PC9 double as the C5 QSPI CS and IO0, and
 * link.c releases all link pins before and after.
 *
 * Protocol, mirroring Espressif's ESSL host:
 *   - credit is the slave's 12-bit TOKEN1 counter (function 1, 0x044 bits [27:16]): the
 *     number of receive buffers it has loaded. The master counts the buffers it has
 *     used and sends only while (TOKEN1 - used) mod 4096 > 0. No READY line.
 *   - a packet is written to function 1 at address 0x1F800 - remaining_length, so the
 *     slave can tell where the packet ends: whole 512-byte blocks in block mode, then the
 *     tail in byte mode (rounded up to 4 bytes on the wire, as ESSL does).
 *   - one packet = one slave receive buffer = at most HDEMG_SDIO_MAX_FRAMES frames. */
#include <stdio.h>
#include <string.h>
#include "link.h"
#include "gen.h"
#include "board.h"

#define POLL_US        200U
#define READY_TMO_US   10000U
#define SINGLE_HOLD_US 2000U
#define IO_TMO_MS      100U
#define SETUP_HZ       400000U          /* identification and the whole card set-up */

static SDIO_HandleTypeDef s_h;
static link_cfg_t   s_cfg;
static link_stats_t s_st;
static int          s_is_open;
static uint32_t     s_credit;
static uint32_t     s_used;            /* slave buffers consumed, mod 4096 */
static uint32_t     s_last_poll_us;
static int          s_waiting, s_timeout_counted;
static uint32_t     s_wait_t0;
static int          s_single_pending;
static uint32_t     s_single_t0;

void HAL_SDIO_MspInit(SDIO_HandleTypeDef *hsdio)
{
    (void)hsdio;
    GPIO_InitTypeDef g = {0};
    __HAL_RCC_GPIOC_CLK_ENABLE();
    __HAL_RCC_GPIOD_CLK_ENABLE();
    __HAL_RCC_SDMMC1_CLK_ENABLE();
    __HAL_RCC_SDMMC1_FORCE_RESET();
    __HAL_RCC_SDMMC1_RELEASE_RESET();
    g.Mode = GPIO_MODE_AF_PP;
    g.Speed = link_pin_speed(s_cfg.slew);
    g.Alternate = GPIO_AF12_SDMMC1;
    g.Pull = GPIO_PULLUP;              /* the only pull-ups on the H7 side: there are no external ones */
    g.Pin = GPIO_PIN_8 | GPIO_PIN_9 | GPIO_PIN_10 | GPIO_PIN_11;
    HAL_GPIO_Init(GPIOC, &g);
    g.Pin = GPIO_PIN_2;
    HAL_GPIO_Init(GPIOD, &g);
    g.Pull = GPIO_NOPULL;
    g.Pin = GPIO_PIN_12;
    HAL_GPIO_Init(GPIOC, &g);
}

void HAL_SDIO_MspDeInit(SDIO_HandleTypeDef *hsdio)
{
    (void)hsdio;
    HAL_GPIO_DeInit(GPIOC, GPIO_PIN_8 | GPIO_PIN_9 | GPIO_PIN_10 | GPIO_PIN_11 | GPIO_PIN_12);
    HAL_GPIO_DeInit(GPIOD, GPIO_PIN_2);
    __HAL_RCC_SDMMC1_CLK_DISABLE();
}

/* SDMMC_CK = kernel / (2 * CLKDIV). Rounded so the clock never exceeds the request, and
 * CLKDIV is never 0 (that would bypass the divider: the full kernel clock on the wire). */
static uint32_t clkdiv_for(uint32_t ker, uint32_t hz)
{
    if (hz < LINK_SDIO_HZ_MIN) hz = LINK_SDIO_HZ_MIN;
    if (hz > LINK_SDIO_HZ_MAX) hz = LINK_SDIO_HZ_MAX;
    uint32_t div = (ker + 2U * hz - 1U) / (2U * hz);
    if (div < 1U) div = 1U;
    if (div > (SDMMC_CLKCR_CLKDIV_Msk >> SDMMC_CLKCR_CLKDIV_Pos)) div = SDMMC_CLKCR_CLKDIV_Msk >> SDMMC_CLKCR_CLKDIV_Pos;
    return div;
}

/* CMD53 byte-mode read of up to 32 bytes from function 1, polled. Ours rather than
 * HAL_SDIO_ReadExtended for the same reason as cmd53_write() below: the transfer state
 * stays in this file, outside the HAL's handle state machine. Returns 0 or SDMMC error bits. */
static uint32_t cmd53_read(uint32_t addr, uint8_t *d, uint32_t len)
{
    SDMMC_TypeDef *q = s_h.Instance;
    SDMMC_DataInitTypeDef dc = {
        .DataTimeOut = SDMMC_DATATIMEOUT, .DataLength = len,
        .DataBlockSize = SDMMC_DATABLOCK_SIZE_1B, .TransferDir = SDMMC_TRANSFER_DIR_TO_SDMMC,
        .TransferMode = SDMMC_TRANSFER_MODE_SDIO, .DPSM = SDMMC_DPSM_DISABLE,
    };
    q->DCTRL = (q->DCTRL & SDMMC_DCTRL_SDIOEN) ? SDMMC_DCTRL_SDIOEN : 0U;
    (void)SDMMC_ConfigData(q, &dc);
    __SDMMC_CMDTRANS_ENABLE(q);
    const uint32_t arg = ((uint32_t)HAL_SDIO_FUNCTION_1 << 28) | (1UL << 26) |   /* read, inc */
                         ((addr & 0x1FFFFU) << 9) | (len & 0x1FFU);
    uint32_t e = SDMMC_SDIO_CmdReadWriteExtended(q, arg);
    if (e != 0U) {
        MODIFY_REG(q->DCTRL, SDMMC_DCTRL_FIFORST, SDMMC_DCTRL_FIFORST);
        __SDMMC_CMDTRANS_DISABLE(q);
        __SDMMC_CLEAR_FLAG(q, SDMMC_STATIC_FLAGS | SDMMC_STATIC_DATA_FLAGS);
        return e;
    }
    uint32_t remaining = len;
    const uint32_t t0 = HAL_GetTick();
    for (;;) {
        while (remaining > 0U && !__SDMMC_GET_FLAG(q, SDMMC_FLAG_RXFIFOE)) {
            uint32_t w = q->FIFO;
            for (uint32_t b = 0U; b < 4U && remaining > 0U; b++, remaining--) {
                *d++ = (uint8_t)(w >> (8U * b));
            }
        }
        if (__SDMMC_GET_FLAG(q, SDMMC_FLAG_RXOVERR | SDMMC_FLAG_DCRCFAIL | SDMMC_FLAG_DTIMEOUT |
                                SDMMC_FLAG_DATAEND) && (remaining == 0U ||
                                __SDMMC_GET_FLAG(q, SDMMC_FLAG_RXFIFOE))) break;
        if (HAL_GetTick() - t0 >= IO_TMO_MS) { e = HAL_SDIO_ERROR_TIMEOUT; break; }
    }
    __SDMMC_CMDTRANS_DISABLE(q);
    if (e == 0U) {
        e = __SDMMC_GET_FLAG(q, SDMMC_FLAG_DTIMEOUT) ? HAL_SDIO_ERROR_DATA_TIMEOUT :
            __SDMMC_GET_FLAG(q, SDMMC_FLAG_DCRCFAIL) ? HAL_SDIO_ERROR_DATA_CRC_FAIL :
            __SDMMC_GET_FLAG(q, SDMMC_FLAG_RXOVERR) ? HAL_SDIO_ERROR_RX_OVERRUN :
            (remaining != 0U) ? HAL_SDIO_ERROR_DATA_TIMEOUT : 0U;
    }
    __SDMMC_CLEAR_FLAG(q, SDMMC_STATIC_FLAGS | SDMMC_STATIC_DATA_FLAGS);
    return e;
}

static int read_token(uint32_t *token)
{
    uint32_t v[2] = {0, 0};
    /* Read twice and accept only agreeing values: the slave updates it concurrently. */
    for (int i = 0; i < 4; i++) {
        uint32_t e = cmd53_read(HDEMG_SDIO_REG_TOKEN_RDATA, (uint8_t *)&v[0], 4);
        if (e == 0U) e = cmd53_read(HDEMG_SDIO_REG_TOKEN_RDATA, (uint8_t *)&v[1], 4);
        if (e != 0U) {
            s_st.errors++;
            s_st.last_err = e | 0x80000000UL;       /* top bit: it was a read */
            return -1;
        }
        if (v[0] == v[1]) { *token = (v[0] >> 16) & 0xFFFU; return 0; }
        s_st.torn_reads++;
    }
    return -2;
}

/* CMD53 write to function 1, incrementing address, in byte mode (len <= 512) or block
 * mode (len a whole number of 512-byte blocks), polled.
 *
 * Not HAL_SDIO_WriteExtended: the HAL shipped with this project (STM32H7 HAL V1.11.5) has
 * two bugs there, both fixed in ST's current driver: (1) the FIFO pointer is a uint8_t *,
 * so every 32-bit FIFO word carried one data byte and three zeros (seen on the C5 as
 * "5a 00 00 00 a5 00 00 00 ..."); (2) in block mode it put the BYTE count in CMD53's count
 * field, so a 3584-byte write asked for 3584 & 511 = 0 blocks, i.e. an endless transfer,
 * and the card never ended it (data timeout). The fixed logic lives here rather than as a
 * patch to the HAL, which CubeMX regeneration would silently revert. */
static int cmd53_write(uint32_t addr, int block_mode, const uint8_t *d, uint32_t len)
{
    SDMMC_TypeDef *q = s_h.Instance;
    const uint32_t nblk = block_mode ? len / HDEMG_SDIO_BLOCK_BYTES : 0U;
    SDMMC_DataInitTypeDef dc = {
        .DataTimeOut = SDMMC_DATATIMEOUT,
        .DataLength = block_mode ? nblk * HDEMG_SDIO_BLOCK_BYTES : len,
        .DataBlockSize = block_mode ? SDMMC_DATABLOCK_SIZE_512B : SDMMC_DATABLOCK_SIZE_1B,
        .TransferDir = SDMMC_TRANSFER_DIR_TO_CARD,
        .TransferMode = block_mode ? SDMMC_TRANSFER_MODE_BLOCK : SDMMC_TRANSFER_MODE_SDIO,
        .DPSM = SDMMC_DPSM_DISABLE,
    };
    q->DCTRL = (q->DCTRL & SDMMC_DCTRL_SDIOEN) ? SDMMC_DCTRL_SDIOEN : 0U;
    (void)SDMMC_ConfigData(q, &dc);
    __SDMMC_CMDTRANS_ENABLE(q);

    const uint32_t arg = (1UL << 31) |                      /* write */
                         ((uint32_t)HAL_SDIO_FUNCTION_1 << 28) |
                         ((uint32_t)(block_mode ? 1U : 0U) << 27) |
                         (1UL << 26) |                      /* incrementing address */
                         ((addr & 0x1FFFFU) << 9) |
                         ((block_mode ? nblk : len) & 0x1FFU);   /* blocks, or bytes */
    uint32_t e = SDMMC_SDIO_CmdReadWriteExtended(q, arg);
    if (e != 0U) {
        MODIFY_REG(q->DCTRL, SDMMC_DCTRL_FIFORST, SDMMC_DCTRL_FIFORST);
        __SDMMC_CMDTRANS_DISABLE(q);
        __SDMMC_CLEAR_FLAG(q, SDMMC_STATIC_FLAGS | SDMMC_STATIC_DATA_FLAGS);
        s_h.ErrorCode = e;
        return -1;
    }

    uint32_t remaining = dc.DataLength;
    const uint32_t t0 = HAL_GetTick();
    while (!__SDMMC_GET_FLAG(q, SDMMC_FLAG_TXUNDERR | SDMMC_FLAG_DCRCFAIL |
                                SDMMC_FLAG_DTIMEOUT | SDMMC_FLAG_DATAEND)) {
        if (__SDMMC_GET_FLAG(q, SDMMC_FLAG_TXFIFOHE) && remaining >= 32U) {
            for (int i = 0; i < 8; i++) {           /* half the FIFO: 8 words, 32 bytes */
                uint32_t w;
                memcpy(&w, d, 4);                   /* first byte on the wire first */
                q->FIFO = w;
                d += 4;
            }
            remaining -= 32U;
        } else if (remaining > 0U && remaining < 32U &&
                   __SDMMC_GET_FLAG(q, SDMMC_FLAG_TXFIFOHE | SDMMC_FLAG_TXFIFOE)) {
            while (remaining > 0U) {
                uint32_t w = 0U;
                for (uint32_t b = 0U; b < 4U && remaining > 0U; b++, remaining--) {
                    w |= (uint32_t)*d++ << (8U * b);
                }
                q->FIFO = w;
            }
        }
        if (HAL_GetTick() - t0 >= IO_TMO_MS) {
            __SDMMC_CMDTRANS_DISABLE(q);
            __SDMMC_CLEAR_FLAG(q, SDMMC_STATIC_FLAGS | SDMMC_STATIC_DATA_FLAGS);
            s_h.ErrorCode = HAL_SDIO_ERROR_TIMEOUT;
            return -2;
        }
    }
    __SDMMC_CMDTRANS_DISABLE(q);
    e = __SDMMC_GET_FLAG(q, SDMMC_FLAG_DTIMEOUT) ? HAL_SDIO_ERROR_DATA_TIMEOUT :
        __SDMMC_GET_FLAG(q, SDMMC_FLAG_DCRCFAIL) ? HAL_SDIO_ERROR_DATA_CRC_FAIL :
        __SDMMC_GET_FLAG(q, SDMMC_FLAG_TXUNDERR) ? HAL_SDIO_ERROR_TX_UNDERRUN : 0U;
    __SDMMC_CLEAR_FLAG(q, SDMMC_STATIC_FLAGS | SDMMC_STATIC_DATA_FLAGS);
    if (e != 0U) { s_h.ErrorCode = e; return -3; }
    return 0;
}

static int send_packet(const uint8_t *d, uint32_t len)
{
    uint32_t remaining = len;
    while (remaining) {
        /* The address encodes the REMAINING PACKET length, not this chunk's. */
        const uint32_t addr = HDEMG_SDIO_FIFO_ADDR_TOP - remaining;
        uint32_t chunk, wire;
        int block;
        if (remaining >= HDEMG_SDIO_BLOCK_BYTES) {
            block = 1;
            chunk = (remaining / HDEMG_SDIO_BLOCK_BYTES) * HDEMG_SDIO_BLOCK_BYTES;
            wire = chunk;
        } else {
            block = 0;
            chunk = remaining;
            wire = (remaining + 3U) & ~3U;
        }
        if (cmd53_write(addr, block, d, wire) != 0) return -1;
        d += chunk;
        remaining -= chunk;
    }
    return 0;
}

/* Card identification, step for step as the HAL's own SDIO_InitCard, except that it
 * records which step failed and the SDMMC error bits (the HAL's version drops both, so
 * every failure read "no card answered"), and it leaves out the 1.8 V request (S18R):
 * the C5's slave is a 3.3 V device. */
static const char *s_id_step = "";
static uint32_t     s_id_err;

/* CMD52 write of RES (CCCR 0x06, bit 3): the card's I/O part back to its initialisation
 * state. CMD0 does not do that for SDIO, so a card identified by an earlier open (the
 * host's probe, then its start) ignores the next CMD5. IDF's own host does this before
 * every identification. Best effort: a card that is already initialising does not answer
 * CMD52, and that timeout costs 64 clocks. */
static void io_reset(SDMMC_TypeDef *q)
{
    uint8_t r;
    (void)SDMMC_SDIO_CmdReadWriteDirect(q, (1UL << 31) | (0x06UL << 9) | 0x08UL, &r);
}

static HAL_StatusTypeDef identify_card(SDIO_HandleTypeDef *h)
{
    uint32_t resp4 = 0U, e;
    uint16_t rca = 1U;
    io_reset(h->Instance);
    s_id_step = "CMD0";
    if ((e = SDMMC_CmdGoIdleState(h->Instance)) != 0U) goto fail;
    s_id_step = "power";
    if (SDMMC_GetPowerState(h->Instance) == 0U) { e = 0U; goto fail; }
    s_id_step = "CMD5";
    if ((e = SDMMC_CmdSendOperationcondition(h->Instance, 0U, &resp4)) != 0U) goto fail;
    if (((resp4 & 0x70000000U) >> 28U) != 0U) {        /* I/O functions present */
        /* The SDIO spec has the host repeat CMD5 with its voltage window until the card
         * sets C (bit 31, "ready"); a card not yet ready ignores CMD3. The HAL sends it
         * once, which left the C5 still initialising when CMD3 came. */
        s_id_step = "CMD5(ocr)";
        uint32_t t0 = HAL_GetTick();
        do {
            if ((e = SDMMC_CmdSendOperationcondition(h->Instance, 1U << 20U /* 3.2-3.3 V */,
                                                     &resp4)) != 0U) goto fail;
            if (resp4 & 0x80000000U) break;
            HAL_Delay(1U);
        } while (HAL_GetTick() - t0 < 1000U);
        if (!(resp4 & 0x80000000U)) { s_id_step = "CMD5 never ready"; e = resp4; goto fail; }
    }
    s_id_step = "CMD3";
    for (uint32_t i = 0U; i < 1000U; i++) {
        e = SDMMC_CmdSetRelAdd(h->Instance, &rca);
        if (e != SDMMC_ERROR_ILLEGAL_CMD) break;
        HAL_Delay(1U);
    }
    if (e != 0U) goto fail;
    s_id_step = "CMD7";
    if ((e = SDMMC_CmdSelDesel(h->Instance, (uint32_t)rca << 16U)) != 0U) goto fail;
    s_id_step = "";
    return HAL_OK;
fail:
    s_id_err = e;
    return HAL_ERROR;
}

int link_sdio_open(const link_cfg_t *c, const char **err)
{
    static char why[96];
    s_cfg = *c;
    memset(&s_st, 0, sizeof s_st);
    s_credit = 0;
    s_waiting = 0;
    s_timeout_counted = 0;
    s_single_pending = 0;
    if (s_cfg.batch_max < 1U || s_cfg.batch_max > HDEMG_SDIO_MAX_FRAMES) s_cfg.batch_max = HDEMG_SDIO_MAX_FRAMES;

    memset(&s_h, 0, sizeof s_h);
    s_h.Instance = SDMMC1;
    s_h.Init.ClockEdge = SDMMC_CLOCK_EDGE_RISING;
    s_h.Init.ClockPowerSave = SDMMC_CLOCK_POWER_SAVE_DISABLE;
    s_h.Init.BusWide = HAL_SDIO_1_WIRE_MODE;
    /* The FIFO is filled by a polled loop that the generation interrupt preempts: let
     * the peripheral hold the clock rather than underrun. */
    s_h.Init.HardwareFlowControl = SDMMC_HARDWARE_FLOW_CONTROL_ENABLE;
    /* HAL_SDIO_Init identifies the card at 400 kHz (CMD0 / CMD5 / CMD3 / CMD7), then
     * switches to Init.ClockDiv for its own CCCR write: keep that at 400 kHz too, so
     * nothing before the final clock change depends on fast edges. */
    uint32_t ker = HAL_RCCEx_GetPeriphCLKFreq(RCC_PERIPHCLK_SDMMC);
    if (ker == 0U) { *err = "sdio: no SDMMC kernel clock"; return -1; }
    s_h.Init.ClockDiv = clkdiv_for(ker, SETUP_HZ);
    s_h.SDIO_IdentifyCard = identify_card;
    s_id_step = "init";
    s_id_err = 0U;

    if (HAL_SDIO_Init(&s_h) != HAL_OK) {
        /* SDMMC error bits: 0x1 response CRC failed, 0x4 no response (timeout). */
        snprintf(why, sizeof why, "sdio: no card answered identification (%s, error 0x%lx)",
                 s_id_step[0] ? s_id_step : "CCCR bus-width write", (unsigned long)s_id_err);
        *err = why;
        HAL_SDIO_DeInit(&s_h);
        return -1;
    }
    s_is_open = 1;
    if (HAL_SDIO_SetDataBusWidth(&s_h, HAL_SDIO_4_WIRES_MODE) != HAL_OK) { *err = "sdio: 4-bit bus refused"; goto fail; }
    if (HAL_SDIO_EnableIOFunction(&s_h, HAL_SDIO_FUNCTION_1) != HAL_OK)  { *err = "sdio: function 1 did not enable"; goto fail; }
    if (HAL_SDIO_SetBlockSize(&s_h, HAL_SDIO_FUNCTION_1, HDEMG_SDIO_BLOCK_BYTES) != HAL_OK) { *err = "sdio: block size refused"; goto fail; }
    /* Only now the data clock (HAL_SDIO_ConfigFrequency would round it UP, and to the
     * undivided kernel clock above 100 MHz). */
    uint32_t div = clkdiv_for(ker, s_cfg.sdio_hz);
    MODIFY_REG(s_h.Instance->CLKCR, SDMMC_CLKCR_CLKDIV, div << SDMMC_CLKCR_CLKDIV_Pos);
    s_st.actual_hz = ker / (2U * div);

    uint32_t token;
    if (read_token(&token) != 0) { *err = "sdio: token register unreadable"; goto fail; }
    s_st.last_loaded = token;
    /* The master's count starts in step with the slave: whatever is loaded now is credit. */
    s_used = 0;
    s_credit = token & 0xFFFU;
    if (s_credit > 64U) { s_used = token; s_credit = 0; }   /* stale counter: start from here */
    s_last_poll_us = board_micros();
    return 0;

fail:
    link_sdio_close();
    return -2;
}

void link_sdio_close(void)
{
    if (!s_is_open) return;
    io_reset(s_h.Instance);            /* leave the card ready to be identified again */
    HAL_SDIO_DeInit(&s_h);
    s_is_open = 0;
}

void link_sdio_poll(int flush)
{
    const uint8_t *p;
    uint32_t n = gen_peek(&p, s_cfg.batch_max);
    if (n == 0U) return;
    uint32_t now = board_micros();

    if (n > 1U) {
        n &= ~1U;                       /* even frame counts keep the length a multiple of 4 */
    } else if (!flush && gen_fill() == 1U) {
        if (!s_single_pending) { s_single_pending = 1; s_single_t0 = now; return; }
        if ((uint32_t)(now - s_single_t0) < SINGLE_HOLD_US) return;
    }
    s_single_pending = 0;

    if (s_credit == 0U) {
        if ((uint32_t)(now - s_last_poll_us) < POLL_US) return;
        s_last_poll_us = now;
        uint32_t token;
        if (read_token(&token) != 0) return;
        s_st.last_loaded = token;
        uint32_t credit = (token - s_used) & 0xFFFU;
        if (credit > 64U) { s_st.credit_errors++; credit = 0; }
        if (credit == 0U) {
            if (!s_waiting) {
                s_waiting = 1;
                s_timeout_counted = 0;
                s_wait_t0 = now;
                s_st.credit_waits++;
            } else if (!s_timeout_counted && (uint32_t)(now - s_wait_t0) > READY_TMO_US) {
                s_timeout_counted = 1;
                s_st.ready_timeouts++;
            }
            return;
        }
        s_credit = credit;
        s_waiting = 0;
    }

    uint32_t bytes = n * GEN_FRAME_BYTES;
    int rc = send_packet(p, bytes);
    gen_consume(n);                     /* lost on failure, and visible as loss */
    if (rc != 0) { s_st.errors++; s_st.last_err = s_h.ErrorCode; s_credit = 0; return; }
    s_used = (s_used + 1U) & 0xFFFU;
    s_st.sent++;
    s_credit--;
    s_st.bytes += bytes;
    s_st.xfers++;
}

void link_sdio_stats(link_stats_t *out) { *out = s_st; }
