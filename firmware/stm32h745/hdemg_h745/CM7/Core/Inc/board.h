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
 * This is what goes into hdemg_hdr_t.t_stm: the host treats that field as µs. */
static inline uint32_t board_micros(void) { return TIM2->CNT; }
