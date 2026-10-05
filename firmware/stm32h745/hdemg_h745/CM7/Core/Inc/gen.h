/* gen.h — the paced frame source and its ring.
 *
 * MEASUREMENT CONTRACT (guide §4). The H7 stands in for an ADC, which cannot stall:
 * frames are generated on the clock at the COMMANDED rate whether or not the link can
 * take them. They wait in a ring; when the ring is full the frame is dropped but its
 * seq is still consumed, so the host sees the gap as loss and ring_drops says the loss
 * happened at the link, not on Wi-Fi. Generation runs in a timer interrupt, so nothing
 * the link service loop does can slow the offered load. */
#pragma once
#include <stdint.h>
#include "hdemg_link.h"

#define GEN_FRAME_BYTES   HDEMG_RAW16_128CH   /* 270 */
#define GEN_RING_FRAMES   1024U               /* power of two; 276 480 B, ~37 ms at 60 Mbit/s */
#define GEN_TICK_US       50U                 /* generation interrupt period */

typedef struct {
    uint32_t running;        /* generation in progress */
    uint32_t done;           /* a run finished (duration elapsed or stopped) */
    uint64_t rate_bps;       /* commanded */
    uint32_t dur_s;
    uint32_t seed;
    uint32_t t0_us;          /* board_micros() at start */
    uint64_t elapsed_us;     /* generation time so far (frozen when the run ends) */
    uint32_t frames;         /* seq numbers consumed this run (generated + ring-dropped) */
    uint32_t ring_drops;     /* frames dropped because the ring was full */
    uint32_t ring_fill;      /* frames waiting right now */
    uint32_t ring_max;       /* high-water mark this run */
    uint32_t first_seq;      /* seq of the first frame of this run */
    uint32_t last_seq;
} gen_stats_t;

void gen_init(void);
int  gen_start(uint64_t rate_bps, uint32_t dur_s, uint32_t seed);   /* 0 = started */
void gen_stop(void);
void gen_stats(gen_stats_t *out);
int  gen_running(void);            /* cheap: safe to call from the service loop */

/* Consumer side (the link service loop). gen_peek returns how many whole frames are
 * available CONTIGUOUSLY at *p (at most max_frames; fewer at the ring's wrap point). */
uint32_t gen_peek(const uint8_t **p, uint32_t max_frames);
void     gen_consume(uint32_t n_frames);
uint32_t gen_fill(void);
