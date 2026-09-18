/* pacing.h — token bucket for offered load.
 *
 * MEASUREMENT CONTRACT. Offered load must be a COMMANDED value, not "as fast as it
 * will go": a swept loss-versus-load curve is impossible otherwise, and a single
 * saturated number cannot distinguish a part that degrades gracefully from one that
 * collapses. Accuracy target is ~1%. */
#pragma once
#include <stdint.h>
#include <stdbool.h>

typedef struct {
    uint64_t rate_bps;
    uint32_t unit_bytes;
    int64_t  t0_us;
    uint64_t units_emitted;
} pacer_t;

void     pacer_init(pacer_t *p, uint64_t rate_bps, uint32_t unit_bytes);
bool     pacer_due(pacer_t *p);          /* true when the next unit may be emitted */
void     pacer_note(pacer_t *p);
uint64_t pacer_achieved_bps(const pacer_t *p);
