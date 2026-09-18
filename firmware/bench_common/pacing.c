#include "pacing.h"
#include "esp_timer.h"

void pacer_init(pacer_t *p, uint64_t rate_bps, uint32_t unit_bytes)
{
    p->rate_bps = rate_bps;
    p->unit_bytes = unit_bytes;
    p->t0_us = esp_timer_get_time();
    p->units_emitted = 0;
}

bool pacer_due(pacer_t *p)
{
    if (p->rate_bps == 0) return false;
    /* Schedule against the run start rather than the previous unit, so scheduling
     * jitter cannot accumulate into a systematic rate error over a 60 s hold. */
    int64_t elapsed_us = esp_timer_get_time() - p->t0_us;
    uint64_t due = (uint64_t)((double)elapsed_us * (double)p->rate_bps
                              / (8.0 * (double)p->unit_bytes * 1e6) );
    return p->units_emitted <= due;
}

void pacer_note(pacer_t *p) { p->units_emitted++; }

uint64_t pacer_achieved_bps(const pacer_t *p)
{
    int64_t el = esp_timer_get_time() - p->t0_us;
    if (el <= 0) return 0;
    return (uint64_t)((double)p->units_emitted * p->unit_bytes * 8.0 * 1e6 / (double)el);
}
