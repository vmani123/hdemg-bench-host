/* link_sdio.c — Stage 2b: SDMMC1 as an SDIO host -> ESP32-C5 SDIO slave (guide §6).
 *
 * NOT YET RUN AGAINST HARDWARE. Written from the HAL SDIO driver and the IDF sdio_slave
 * documentation; the 2b harness (with its mandatory pull-ups) has not been wired.
 *
 * Wiring (guide §3.3):  CK PC12 · CMD PD2 · D0 PC8 · D1 PC9 · D2 PC10 · D3 PC11, with
 * 10-50 kOhm pull-ups to 3V3 on CMD and D0-D3. Pins are only driven while the link is open.
 *
 * Protocol, mirroring Espressif's ESSL host:
 *   - credit is the slave's 12-bit TOKEN1 counter (function 1, 0x044 bits [27:16]): the
 *     number of receive buffers it has loaded. The master counts the buffers it has
 *     used and sends only while (TOKEN1 - used) mod 4096 > 0. No READY line.
 *   - a packet is written to function 1 at address 0x1F800 - remaining_length, so the
 *     slave can tell where the packet ends: whole 512-byte blocks in block mode, then the
 *     tail in byte mode (rounded up to 4 bytes on the wire, as ESSL does).
 *   - one packet = one slave receive buffer = at most HDEMG_SDIO_MAX_FRAMES frames. */
#include <string.h>
#include "link.h"
#include "gen.h"
#include "board.h"

#define POLL_US        200U
#define READY_TMO_US   10000U
#define SINGLE_HOLD_US 2000U
#define IO_TMO_MS      100U

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
    g.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    g.Alternate = GPIO_AF12_SDMMC1;
    g.Pull = GPIO_PULLUP;              /* in addition to the external pull-ups, not instead */
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

static int read_token(uint32_t *token)
{
    HAL_SDIO_ExtendedCmd_TypeDef a = {
        .Reg_Addr = HDEMG_SDIO_REG_TOKEN_RDATA, .OpCode = HAL_SDIO_OP_CODE_AUTO_INC,
        .Block_Mode = HAL_SDIO_MODE_BYTE, .IOFunctionNbr = HAL_SDIO_FUNCTION_1,
    };
    uint32_t v[2] = {0, 0};
    /* Read twice and accept only agreeing values: the slave updates it concurrently. */
    for (int i = 0; i < 4; i++) {
        if (HAL_SDIO_ReadExtended(&s_h, &a, (uint8_t *)&v[0], 4, IO_TMO_MS) != HAL_OK ||
            HAL_SDIO_ReadExtended(&s_h, &a, (uint8_t *)&v[1], 4, IO_TMO_MS) != HAL_OK) {
            s_st.errors++;
            return -1;
        }
        if (v[0] == v[1]) { *token = (v[0] >> 16) & 0xFFFU; return 0; }
        s_st.torn_reads++;
    }
    return -2;
}

static int send_packet(const uint8_t *d, uint32_t len)
{
    uint32_t remaining = len;
    while (remaining) {
        HAL_SDIO_ExtendedCmd_TypeDef a = {
            /* The address encodes the REMAINING PACKET length, not this chunk's. */
            .Reg_Addr = HDEMG_SDIO_FIFO_ADDR_TOP - remaining,
            .OpCode = HAL_SDIO_OP_CODE_AUTO_INC, .IOFunctionNbr = HAL_SDIO_FUNCTION_1,
        };
        uint32_t chunk, wire;
        if (remaining >= HDEMG_SDIO_BLOCK_BYTES) {
            a.Block_Mode = HAL_SDIO_MODE_BLOCK;
            chunk = (remaining / HDEMG_SDIO_BLOCK_BYTES) * HDEMG_SDIO_BLOCK_BYTES;
            wire = chunk;
        } else {
            a.Block_Mode = HAL_SDIO_MODE_BYTE;
            chunk = remaining;
            wire = (remaining + 3U) & ~3U;
        }
        if (HAL_SDIO_WriteExtended(&s_h, &a, (uint8_t *)(uintptr_t)d, wire, IO_TMO_MS) != HAL_OK) return -1;
        d += chunk;
        remaining -= chunk;
    }
    return 0;
}

int link_sdio_open(const link_cfg_t *c, const char **err)
{
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
    s_h.Init.ClockDiv = SDMMC_NSPEED_CLK_DIV;

    if (HAL_SDIO_Init(&s_h) != HAL_OK) {            /* CMD0 / CMD5 / CMD3 / CMD7 at 400 kHz */
        *err = "sdio: no card answered identification";
        HAL_SDIO_DeInit(&s_h);
        return -1;
    }
    s_is_open = 1;
    if (HAL_SDIO_SetDataBusWidth(&s_h, HAL_SDIO_4_WIRES_MODE) != HAL_OK) { *err = "sdio: 4-bit bus refused"; goto fail; }
    if (HAL_SDIO_EnableIOFunction(&s_h, HAL_SDIO_FUNCTION_1) != HAL_OK)  { *err = "sdio: function 1 did not enable"; goto fail; }
    if (HAL_SDIO_SetBlockSize(&s_h, HAL_SDIO_FUNCTION_1, HDEMG_SDIO_BLOCK_BYTES) != HAL_OK) { *err = "sdio: block size refused"; goto fail; }
    if (HAL_SDIO_ConfigFrequency(&s_h, s_cfg.sdio_hz) != HAL_OK) { *err = "sdio: clock change failed"; goto fail; }
    s_st.actual_hz = s_cfg.sdio_hz;

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
    if (rc != 0) { s_st.errors++; s_credit = 0; return; }
    s_used = (s_used + 1U) & 0xFFFU;
    s_st.sent++;
    s_credit--;
    s_st.bytes += bytes;
    s_st.xfers++;
}

void link_sdio_stats(link_stats_t *out) { *out = s_st; }
