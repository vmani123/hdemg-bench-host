/* link_qspi.c — Stage 2a: QUADSPI master -> ESP GP-SPI Slave HD (guide §5).
 *
 * Wiring (guide §3.1):  CLK PB2 · NCS PG6 · IO0 PD11 · IO1 PD12 · IO2 PE2 · IO3 PD13 ·
 * READY PG9 (input, from the ESP).  The pins are only driven while the link is open;
 * closed, they are analog/Hi-Z, so an idle H7 cannot disturb a board it is wired to.
 *
 * Every transaction: 8-bit command on ONE line, 8-bit address, 8 dummy cycles, data.
 *   push frames   WRDMA  0xA3 (address + data on 4 lines) or 0x23 (address on 1 line)
 *   end buffer    WR_END 0x07, single line, 24 clocks in total
 *   read counter  RDBUF  0x02, single line, 4 data bytes (the master samples IO1)
 *
 * The data path is a polled, word-wide FIFO fill rather than MDMA: at 4 lines the bus
 * moves at most 12.5 MB/s at 25 MHz and this loop is several times faster, the CPU has
 * nothing else to do (generation preempts it from an interrupt), and it removes MDMA
 * and its cache rules from the list of things that can silently corrupt data.
 *
 * Flow control is the credit counter of guide §5.4, never a READY level: the slave
 * publishes how many receive buffers it has LOADED; the master counts the buffers it
 * has ended (`sent`) and transmits only while LOADED - sent > 0. READY's rising edge
 * just tells the master when re-reading the counter is worthwhile, and a slow poll
 * covers a missed edge, so a broken READY wire costs latency, not correctness. */
#include <string.h>
#include "link.h"
#include "gen.h"
#include "board.h"

#define Q               QUADSPI
#define CCR_IMODE_1     (1U << QUADSPI_CCR_IMODE_Pos)
#define CCR_ADMODE_1    (1U << QUADSPI_CCR_ADMODE_Pos)
#define CCR_ADMODE_4    (3U << QUADSPI_CCR_ADMODE_Pos)
#define CCR_ADSIZE_8    (0U << QUADSPI_CCR_ADSIZE_Pos)
#define CCR_DMODE_1     (1U << QUADSPI_CCR_DMODE_Pos)
#define CCR_DMODE_4     (3U << QUADSPI_CCR_DMODE_Pos)
#define CCR_FMODE_WR    (0U << QUADSPI_CCR_FMODE_Pos)
#define CCR_FMODE_RD    (1U << QUADSPI_CCR_FMODE_Pos)
#define CCR_DCYC(n)     ((uint32_t)(n) << QUADSPI_CCR_DCYC_Pos)
#define FCR_ALL         (QUADSPI_FCR_CTEF | QUADSPI_FCR_CTCF | QUADSPI_FCR_CSMF | QUADSPI_FCR_CTOF)

#define XFER_TMO_US     100000U     /* any single transaction */
#define POLL_US         100U        /* credit re-read interval while waiting without READY */
#define READY_TMO_US    10000U      /* no credit for this long counts as a ready_timeout */
#define SINGLE_HOLD_US  2000U       /* how long one odd frame waits for a partner */
#define CREDIT_SANE_MAX 64U         /* more "credit" than this means the counters disagree */

static link_cfg_t   s_cfg;
static link_stats_t s_st;
static int          s_is_open;
static uint32_t     s_presc_data, s_presc_rd, s_hclk;
static uint32_t     s_credit;
static volatile uint32_t s_ready_flag, s_ready_edges;
static uint32_t     s_last_poll_us;
static int          s_waiting, s_timeout_counted;
static uint32_t     s_wait_t0;
static int          s_single_pending;
static uint32_t     s_single_t0;

/* ---- low level -------------------------------------------------------------------- */

static int q_wait_clear(uint32_t mask, uint32_t tmo_us)
{
    uint32_t t0 = board_micros();
    while (Q->SR & mask) {
        if ((uint32_t)(board_micros() - t0) > tmo_us) return -1;
    }
    return 0;
}

static void q_abort(void)
{
    Q->CR |= QUADSPI_CR_ABORT;
    uint32_t t0 = board_micros();
    while ((Q->CR & QUADSPI_CR_ABORT) && (uint32_t)(board_micros() - t0) < 1000U) { }
    Q->FCR = FCR_ALL;
}

static uint32_t presc_for(uint32_t hz)
{
    if (hz == 0U) hz = 1000000U;
    uint32_t div = (s_hclk + hz - 1U) / hz;        /* round the clock DOWN, never up */
    if (div < 1U) div = 1U;
    if (div > 256U) div = 256U;
    return div - 1U;
}

static int q_set_presc(uint32_t presc)
{
    if (((Q->CR & QUADSPI_CR_PRESCALER_Msk) >> QUADSPI_CR_PRESCALER_Pos) == presc) return 0;
    if (q_wait_clear(QUADSPI_SR_BUSY, XFER_TMO_US) != 0) return -1;
    MODIFY_REG(Q->CR, QUADSPI_CR_PRESCALER_Msk, presc << QUADSPI_CR_PRESCALER_Pos);
    return 0;
}

/* Indirect write. n == 0 with an address: the transfer is the command + address only. */
static int q_write(uint32_t ccr, uint32_t addr, const uint8_t *d, uint32_t n)
{
    if (q_wait_clear(QUADSPI_SR_BUSY, XFER_TMO_US) != 0) { q_abort(); return -1; }
    Q->FCR = FCR_ALL;
    if (n) Q->DLR = n - 1U;
    Q->CCR = ccr | CCR_FMODE_WR;
    Q->AR = addr;                               /* with data, the first DR write starts it */

    uint32_t t0 = board_micros();
    while (n >= 4U) {
        while (((Q->SR & QUADSPI_SR_FLEVEL_Msk) >> QUADSPI_SR_FLEVEL_Pos) > 28U) {
            if ((Q->SR & QUADSPI_SR_TEF) || (uint32_t)(board_micros() - t0) > XFER_TMO_US) {
                q_abort();
                return -2;
            }
        }
        uint32_t w;
        memcpy(&w, d, 4);                       /* sent least-significant byte first */
        Q->DR = w;
        d += 4;
        n -= 4U;
    }
    while (n) {
        while (((Q->SR & QUADSPI_SR_FLEVEL_Msk) >> QUADSPI_SR_FLEVEL_Pos) > 31U) {
            if ((Q->SR & QUADSPI_SR_TEF) || (uint32_t)(board_micros() - t0) > XFER_TMO_US) {
                q_abort();
                return -2;
            }
        }
        *(__IO uint8_t *)&Q->DR = *d++;
        n--;
    }
    while (!(Q->SR & QUADSPI_SR_TCF)) {
        if ((Q->SR & QUADSPI_SR_TEF) || (uint32_t)(board_micros() - t0) > XFER_TMO_US) {
            q_abort();
            return -3;
        }
    }
    Q->FCR = QUADSPI_FCR_CTCF;
    return 0;
}

static int q_read(uint32_t ccr, uint32_t addr, uint8_t *d, uint32_t n)
{
    if (q_wait_clear(QUADSPI_SR_BUSY, XFER_TMO_US) != 0) { q_abort(); return -1; }
    Q->FCR = FCR_ALL;
    Q->DLR = n - 1U;
    Q->CCR = ccr | CCR_FMODE_RD;
    Q->AR = addr;                               /* starts the transfer */

    uint32_t t0 = board_micros();
    while (n) {
        uint32_t sr = Q->SR;
        if (sr & QUADSPI_SR_FLEVEL_Msk) {
            *d++ = *(__IO uint8_t *)&Q->DR;
            n--;
        } else if ((sr & QUADSPI_SR_TEF) || (uint32_t)(board_micros() - t0) > XFER_TMO_US) {
            q_abort();
            return -2;
        }
    }
    while (!(Q->SR & QUADSPI_SR_TCF)) {
        if ((uint32_t)(board_micros() - t0) > XFER_TMO_US) { q_abort(); return -3; }
    }
    Q->FCR = QUADSPI_FCR_CTCF;
    return 0;
}

/* ---- protocol --------------------------------------------------------------------- */

static int q_wrdma(const uint8_t *d, uint32_t n)
{
    uint32_t ccr = CCR_IMODE_1 | CCR_ADSIZE_8 | CCR_DCYC(s_cfg.qspi_dummy) | CCR_DMODE_4;
    if (s_cfg.qspi_addr_lines == 4U) ccr |= CCR_ADMODE_4 | HDEMG_QSPI_CMD_WRDMA_4_4;
    else                             ccr |= CCR_ADMODE_1 | HDEMG_QSPI_CMD_WRDMA_1_4;
    return q_write(ccr, 0, d, n);
}

/* Command + address + one more byte on a single line = 24 clocks: the exact waveform
 * IDF's own master helper produces (cmd, addr, one dummy byte). Sent as a data byte so
 * the count does not depend on how the peripheral treats a dummy phase with no data. */
static int q_wr_end(void)
{
    static const uint8_t zero = 0;
    uint32_t ccr = CCR_IMODE_1 | CCR_ADMODE_1 | CCR_ADSIZE_8 | CCR_DCYC(0) | CCR_DMODE_1 |
                   HDEMG_QSPI_CMD_WR_END;
    return q_write(ccr, 0, &zero, 1);
}

static int q_rdbuf(uint32_t addr, uint32_t *value)
{
    uint8_t b[4];
    uint32_t ccr = CCR_IMODE_1 | CCR_ADMODE_1 | CCR_ADSIZE_8 | CCR_DCYC(s_cfg.qspi_dummy) |
                   CCR_DMODE_1 | HDEMG_QSPI_CMD_RDBUF;
    if (q_set_presc(s_presc_rd) != 0) return -1;
    int rc = q_read(ccr, addr, b, 4);
    if (q_set_presc(s_presc_data) != 0 && rc == 0) rc = -1;
    if (rc != 0) return rc;
    memcpy(value, b, 4);
    return 0;
}

/* The slave updates a register word while the master may be reading it byte by byte:
 * accept a value only when two consecutive reads agree. */
int link_qspi_read_reg(uint32_t addr, uint32_t *value)
{
    uint32_t a, b;
    if (!s_is_open) return -9;
    for (int i = 0; i < 4; i++) {
        if (q_rdbuf(addr, &a) != 0 || q_rdbuf(addr, &b) != 0) { s_st.errors++; return -1; }
        if (a == b) { *value = a; return 0; }
        s_st.torn_reads++;
    }
    return -2;
}

int link_qspi_send_raw(const uint8_t *data, uint32_t n, int end_buffer)
{
    if (!s_is_open) return -9;
    int rc = q_wrdma(data, n);
    if (rc == 0 && end_buffer) rc = q_wr_end();
    if (rc == 0) { s_st.xfers++; s_st.bytes += n; if (end_buffer) s_st.sent++; }
    else s_st.errors++;
    return rc;
}

void EXTI9_5_IRQHandler(void)
{
    if (__HAL_GPIO_EXTI_GET_IT(GPIO_PIN_9) != 0U) {
        __HAL_GPIO_EXTI_CLEAR_IT(GPIO_PIN_9);
        s_ready_flag = 1;
        s_ready_edges++;
    }
}

static void pins_release(void)
{
    HAL_NVIC_DisableIRQ(EXTI9_5_IRQn);
    HAL_GPIO_DeInit(GPIOB, GPIO_PIN_2);
    HAL_GPIO_DeInit(GPIOG, GPIO_PIN_6 | GPIO_PIN_9);
    HAL_GPIO_DeInit(GPIOD, GPIO_PIN_11 | GPIO_PIN_12 | GPIO_PIN_13);
    HAL_GPIO_DeInit(GPIOE, GPIO_PIN_2);
}

/* Bring the master's `sent` into step with the slave: with nothing in flight, the
 * buffers the slave has completed are exactly the buffers the master has ended. */
static int resync(void)
{
    uint32_t loaded, completed;
    if (link_qspi_read_reg(HDEMG_QSPI_REG_COMPLETED, &completed) != 0) return -1;
    if (link_qspi_read_reg(HDEMG_QSPI_REG_LOADED, &loaded) != 0) return -1;
    s_st.last_loaded = loaded;
    s_st.last_completed = completed;
    s_st.sent = completed;
    s_credit = loaded - completed;
    if (s_credit > CREDIT_SANE_MAX) { s_st.credit_errors++; s_credit = 0; return -2; }
    return 0;
}

int link_qspi_open(const link_cfg_t *c, const char **err)
{
    GPIO_InitTypeDef g = {0};

    s_cfg = *c;
    memset(&s_st, 0, sizeof s_st);
    s_credit = 0;
    s_ready_flag = 0;
    s_ready_edges = 0;
    s_waiting = 0;
    s_timeout_counted = 0;
    s_single_pending = 0;
    if (s_cfg.batch_max < 1U || s_cfg.batch_max > HDEMG_LINK_MAX_FRAMES) s_cfg.batch_max = HDEMG_LINK_MAX_FRAMES;
    if (s_cfg.qspi_dummy > 31U) s_cfg.qspi_dummy = HDEMG_QSPI_DUMMY_CYCLES;

    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOD_CLK_ENABLE();
    __HAL_RCC_GPIOE_CLK_ENABLE();
    __HAL_RCC_GPIOG_CLK_ENABLE();
    __HAL_RCC_QSPI_CLK_ENABLE();
    __HAL_RCC_QSPI_FORCE_RESET();
    __HAL_RCC_QSPI_RELEASE_RESET();

    g.Mode = GPIO_MODE_AF_PP;
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    g.Alternate = GPIO_AF9_QUADSPI;
    g.Pin = GPIO_PIN_2;                                HAL_GPIO_Init(GPIOB, &g);   /* CLK */
    g.Pin = GPIO_PIN_11 | GPIO_PIN_12 | GPIO_PIN_13;   HAL_GPIO_Init(GPIOD, &g);   /* IO0 IO1 IO3 */
    g.Pin = GPIO_PIN_2;                                HAL_GPIO_Init(GPIOE, &g);   /* IO2 */
    g.Alternate = GPIO_AF10_QUADSPI;
    g.Pull = GPIO_PULLUP;                              /* NCS idles high even before EN */
    g.Pin = GPIO_PIN_6;                                HAL_GPIO_Init(GPIOG, &g);   /* NCS */

    /* READY from the ESP: pulled down so an unconnected wire reads "not ready". */
    memset(&g, 0, sizeof g);
    g.Mode = GPIO_MODE_IT_RISING;
    g.Pull = GPIO_PULLDOWN;
    g.Pin = GPIO_PIN_9;
    HAL_GPIO_Init(GPIOG, &g);
    __HAL_GPIO_EXTI_CLEAR_IT(GPIO_PIN_9);
    HAL_NVIC_SetPriority(EXTI9_5_IRQn, 4, 0);
    HAL_NVIC_EnableIRQ(EXTI9_5_IRQn);

    s_hclk = HAL_RCC_GetHCLKFreq();                    /* QUADSPI kernel clock = HCLK3 */
    s_presc_data = presc_for(s_cfg.qspi_hz);
    s_presc_rd = presc_for(s_cfg.qspi_rd_hz);
    if (s_presc_rd < s_presc_data) s_presc_rd = s_presc_data;   /* reads never faster than writes */

    /* Mode 0, CS high >= 8 cycles between transactions, "flash size" maxed so the
     * peripheral's address-range check can never trip. */
    Q->DCR = (31U << QUADSPI_DCR_FSIZE_Pos) | (7U << QUADSPI_DCR_CSHT_Pos);
    Q->CR = (s_presc_data << QUADSPI_CR_PRESCALER_Pos) |
            (s_cfg.qspi_sshift ? QUADSPI_CR_SSHIFT : 0U) | QUADSPI_CR_EN;
    s_st.actual_hz = s_hclk / (s_presc_data + 1U);
    s_is_open = 1;

    /* Refuse to start against a slave that is not there: a run with no receiver would
     * otherwise just look like 100 % loss. */
    if (!s_cfg.qspi_nocredit) {
        uint32_t magic = 0;
        if (link_qspi_read_reg(HDEMG_QSPI_REG_MAGIC, &magic) != 0) {
            *err = "qspi: register read failed";
            link_qspi_close();
            return -1;
        }
        if (magic != HDEMG_QSPI_MAGIC) {
            s_st.last_loaded = magic;                  /* kept for the error reply */
            *err = "qspi: no slave answered (bad magic)";
            link_qspi_close();
            return -2;
        }
        if (resync() != 0) {
            *err = "qspi: slave counters unreadable or inconsistent";
            link_qspi_close();
            return -3;
        }
    }
    s_last_poll_us = board_micros();
    return 0;
}

void link_qspi_close(void)
{
    if (!s_is_open) return;
    q_wait_clear(QUADSPI_SR_BUSY, XFER_TMO_US);
    Q->CR &= ~QUADSPI_CR_EN;
    pins_release();
    __HAL_RCC_QSPI_CLK_DISABLE();
    s_is_open = 0;
}

void link_qspi_poll(int flush)
{
    const uint8_t *p;
    uint32_t n = gen_peek(&p, s_cfg.batch_max);
    if (n == 0U) return;
    uint32_t now = board_micros();

    if (s_cfg.qspi_even) {
        if (n > 1U) {
            n &= ~1U;
        } else if (!flush && gen_fill() == 1U) {
            /* One frame alone: give it a moment to find a partner so the transaction
             * length stays a multiple of four bytes. */
            if (!s_single_pending) { s_single_pending = 1; s_single_t0 = now; return; }
            if ((uint32_t)(now - s_single_t0) < SINGLE_HOLD_US) return;
        }
    }
    s_single_pending = 0;

    if (!s_cfg.qspi_nocredit && s_credit == 0U) {
        if (!s_ready_flag && (uint32_t)(now - s_last_poll_us) < POLL_US) return;
        s_ready_flag = 0;
        s_last_poll_us = now;
        uint32_t loaded;
        if (link_qspi_read_reg(HDEMG_QSPI_REG_LOADED, &loaded) != 0) return;
        s_st.last_loaded = loaded;
        uint32_t credit = loaded - s_st.sent;          /* wrap-safe */
        if (credit > CREDIT_SANE_MAX) { s_st.credit_errors++; credit = 0; }
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
    int rc = q_wrdma(p, bytes);
    if (rc == 0) rc = q_wr_end();
    /* Consumed either way: after a failed transaction the frames are lost, and the host
     * must see that as loss rather than have them silently retried out of order. */
    gen_consume(n);
    if (rc != 0) {
        s_st.errors++;
        if (!s_cfg.qspi_nocredit) (void)resync();
        return;
    }
    s_st.sent++;
    if (s_credit) s_credit--;
    s_st.bytes += bytes;
    s_st.xfers++;
}

void link_qspi_stats(link_stats_t *out)
{
    s_st.ready_edges = s_ready_edges;
    *out = s_st;
}
