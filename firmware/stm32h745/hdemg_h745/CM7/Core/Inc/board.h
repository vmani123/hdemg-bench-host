/* board.h — NUCLEO-H745ZI-Q bring-up: clocks, MPU, caches, LEDs, the µs timer. */
#pragma once
#include <stdint.h>
#include "stm32h7xx_hal.h"

/* AXI SRAM: the only RAM both MDMA (QUADSPI) and the SDMMC1 IDMA can reach. The CM7
 * linker script maps .data/.bss to DTCM, so DMA buffers are placed here explicitly
 * (section .axi_ring) and the region is made non-cacheable by board_mpu_config(). */
#define AXI_SRAM_BASE  0x24000000UL
#define AXI_RING_ATTR  __attribute__((section(".axi_ring"), aligned(32)))

void board_mpu_config(void);
void board_cache_enable(void);
int  board_wait_cm4_stopped(void);   /* 1 = CM4 reached STOP, 0 = timed out */
int  board_release_cm4(void);        /* 1 = CM4 woke, 0 = timed out */
void board_clock_config(void);       /* 400 MHz SYSCLK, 200 MHz HCLK, VOS1, direct SMPS */
void board_leds_init(void);
void board_us_timer_init(void);      /* TIM2 free-running at 1 MHz */
void board_fatal(void) __attribute__((noreturn));

void led_green(int on);              /* LD1 PB0  — heartbeat */
void led_yellow(int on);             /* LD2 PE1  — a run is active */
void led_red(int on);                /* LD3 PB14 — fault */

/* Microseconds since board_us_timer_init(), free-running and wrapping at 2^32 (71.6 min).
 * Fast and fine-grained, but only as accurate as the board's 8 MHz reference — see below.
 * Used for short timeouts; NOT for pacing or timestamps. */
static inline uint32_t board_micros(void) { return TIM2->CNT; }

/* ---- the reference clock -----------------------------------------------------------
 * The Nucleo's 8 MHz HSE input is supplied by the on-board ST-LINK and is not
 * crystal-grade: on this bench it measured ~0.27 % fast and WANDERED by several hundred
 * ppm from one half-minute to the next. Anything timed from it inherits that: the
 * offered load, and worse, the t_stm in every frame, which the receiver subtracts from
 * its own clock to get latency (a 400 ppm error is a 24 ms ramp across a 60 s run).
 *
 * The board also carries a 32.768 kHz crystal (LSE, X2). LPTIM1 counts it, and the
 * generator takes elapsed time from that count: exact rational arithmetic
 * (ticks * 15625 / 512 µs), 30.5 µs resolution, crystal stability. If the crystal does
 * not start, everything falls back to TIM2 and `hello` says so. */
#define BOARD_LSE_HZ 32768U
extern int g_board_lse_ok;                    /* 1 = LSE + LPTIM1 are the reference */
int board_ref_clock_init(void);               /* returns g_board_lse_ok */

/* The 16-bit LSE tick counter. LPTIM1 runs asynchronously to the bus, so a single read
 * is not trustworthy: read until two in a row agree. Safe from any context. */
static inline uint32_t board_lse_ticks16(void)
{
    uint32_t a, b;
    do { a = LPTIM1->CNT; b = LPTIM1->CNT; } while (a != b);
    return a & 0xFFFFU;
}
static inline uint64_t board_lse_ticks_to_us(uint64_t ticks) { return ticks * 15625ULL / 512ULL; }

/* µs since board_ref_clock_init() on the reference clock (LSE if it is up, else TIM2).
 * MAIN LOOP ONLY, and it must be called at least every 2 s (the LSE counter's wrap). */
uint64_t board_ref_micros(void);
