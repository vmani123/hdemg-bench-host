/* gen.c — paced frame source. See gen.h for the contract. */
#include <string.h>
#include "gen.h"
#include "board.h"

#define RING_MASK      (GEN_RING_FRAMES - 1U)
#define MAX_BURST      16U          /* frames per tick: bounds time spent in the interrupt */
#define BITS_PER_FRAME (8ULL * GEN_FRAME_BYTES)

_Static_assert((GEN_RING_FRAMES & RING_MASK) == 0, "ring size must be a power of two");
_Static_assert(HDEMG_PAYLOAD_WORDS * 4 + HDEMG_HDR_BYTES == GEN_FRAME_BYTES, "frame layout");

/* In AXI SRAM, non-cacheable (board_mpu_config): DMA-visible and never stale. */
/* +4: the SDIO link rounds a packet's last write up to four bytes on the wire. */
static uint8_t s_ring[GEN_RING_FRAMES * GEN_FRAME_BYTES + 4U] AXI_RING_ATTR;

/* Single producer (TIM3 interrupt) / single consumer (main loop). Indices are
 * free-running frame counters; fill = w - r. 32-bit loads and stores are atomic. */
static volatile uint32_t s_w, s_r;

static volatile uint32_t s_running, s_done;
static uint64_t s_rate_bps, s_dur_us;
static uint32_t s_dur_s, s_seed, s_t0;
static volatile uint64_t s_elapsed_us;
static uint32_t s_last_us;
static volatile uint32_t s_frames, s_drops, s_max;
static uint32_t s_seq;                       /* monotonic across runs, like the synth source */
static volatile uint32_t s_first_seq;

static inline void emit(uint32_t now_us)
{
    uint32_t seq = ++s_seq;
    s_frames++;

    uint32_t w = s_w;
    uint32_t fill = w - s_r;
    if (fill >= GEN_RING_FRAMES) { s_drops++; return; }   /* ring full: drop, keep the seq */

    uint8_t *p = &s_ring[(w & RING_MASK) * GEN_FRAME_BYTES];
    hdemg_hdr_t h = {
        .magic = HDEMG_MAGIC, .type = HDEMG_TYPE_RAW16, .chip_id = HDEMG_CHIP_COMBINED,
        .seq = seq, .t_stm = now_us, .n_ch = 128,
    };
    memcpy(p, &h, HDEMG_HDR_BYTES);
    p += HDEMG_HDR_BYTES;
    uint32_t x = hdemg_payload_state0(seq, s_seed);
    for (uint32_t i = 0; i < HDEMG_PAYLOAD_WORDS; i++) {
        x = hdemg_xs32(x);
        memcpy(p + 4U * i, &x, 4);
    }
    s_w = w + 1U;
    if (fill + 1U > s_max) s_max = fill + 1U;
}

void TIM3_IRQHandler(void)
{
    TIM3->SR = ~TIM_SR_UIF;
    if (!s_running) return;

    uint32_t now = board_micros();
    uint64_t el = s_elapsed_us + (uint32_t)(now - s_last_us);
    s_last_us = now;
    int last = 0;
    if (el >= s_dur_us) { el = s_dur_us; last = 1; }
    s_elapsed_us = el;

    /* Scheduled against the run start, not the previous frame, so jitter cannot
     * accumulate into a rate error over a 60 s hold (same rule as pacing.c). */
    uint64_t due = el * s_rate_bps / (BITS_PER_FRAME * 1000000ULL);
    uint32_t burst = 0;
    while ((uint64_t)s_frames < due && burst < MAX_BURST) { emit(now); burst++; }

    if (last && (uint64_t)s_frames >= due) {
        s_running = 0;
        s_done = 1;
        TIM3->CR1 &= ~TIM_CR1_CEN;
    }
}

void gen_init(void)
{
    __HAL_RCC_TIM3_CLK_ENABLE();
    uint32_t tclk = HAL_RCC_GetPCLK1Freq();
    if ((RCC->D2CFGR & RCC_D2CFGR_D2PPRE1_2) != 0U) tclk *= 2U;
    TIM3->CR1 = 0;
    TIM3->PSC = (tclk / 1000000U) - 1U;
    TIM3->ARR = GEN_TICK_US - 1U;
    TIM3->EGR = TIM_EGR_UG;
    TIM3->SR = 0;
    TIM3->DIER = TIM_DIER_UIE;
    HAL_NVIC_SetPriority(TIM3_IRQn, 0, 0);     /* highest: nothing delays generation */
    HAL_NVIC_EnableIRQ(TIM3_IRQn);
}

int gen_start(uint64_t rate_bps, uint32_t dur_s, uint32_t seed)
{
    if (s_running) return -1;
    if (rate_bps == 0 || dur_s == 0) return -2;

    s_r = s_w;                      /* discard anything left from a previous run */
    s_rate_bps = rate_bps;
    s_dur_s = dur_s;
    s_dur_us = (uint64_t)dur_s * 1000000ULL;
    s_seed = seed;
    s_elapsed_us = 0;
    s_frames = 0;
    s_drops = 0;
    s_max = 0;
    s_first_seq = s_seq + 1U;
    s_done = 0;
    s_t0 = board_micros();
    s_last_us = s_t0;
    s_running = 1;
    TIM3->CNT = 0;
    TIM3->SR = 0;
    TIM3->CR1 |= TIM_CR1_CEN;
    return 0;
}

void gen_stop(void)
{
    NVIC_DisableIRQ(TIM3_IRQn);
    if (s_running) {
        uint32_t now = board_micros();
        s_elapsed_us += (uint32_t)(now - s_last_us);
        s_running = 0;
        s_done = 1;
    }
    TIM3->CR1 &= ~TIM_CR1_CEN;
    NVIC_EnableIRQ(TIM3_IRQn);
}

void gen_stats(gen_stats_t *o)
{
    NVIC_DisableIRQ(TIM3_IRQn);
    o->running = s_running;
    o->done = s_done;
    o->rate_bps = s_rate_bps;
    o->dur_s = s_dur_s;
    o->seed = s_seed;
    o->t0_us = s_t0;
    o->elapsed_us = s_elapsed_us;
    if (s_running) o->elapsed_us += (uint32_t)(board_micros() - s_last_us);
    o->frames = s_frames;
    o->ring_drops = s_drops;
    o->ring_fill = s_w - s_r;
    o->ring_max = s_max;
    o->first_seq = s_first_seq;
    o->last_seq = s_seq;
    NVIC_EnableIRQ(TIM3_IRQn);
}

int gen_running(void) { return (int)s_running; }

uint32_t gen_fill(void) { return s_w - s_r; }

uint32_t gen_peek(const uint8_t **p, uint32_t max_frames)
{
    uint32_t r = s_r;
    uint32_t avail = s_w - r;
    uint32_t idx = r & RING_MASK;
    uint32_t to_wrap = GEN_RING_FRAMES - idx;
    if (avail > to_wrap) avail = to_wrap;
    if (avail > max_frames) avail = max_frames;
    *p = &s_ring[idx * GEN_FRAME_BYTES];
    return avail;
}

void gen_consume(uint32_t n_frames) { s_r += n_frames; }
