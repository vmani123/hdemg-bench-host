/* source_synth.c — paced synthetic source.
 *
 * MEASUREMENT CONTRACT. This is the Stage 1 control arm: the ESP generates its own
 * counter-stamped frames at a COMMANDED rate, with no wired ingress at all. Without it,
 * a low number in a QSPI or SDIO cell is unattributable — ingress link, Wi-Fi stack, RF,
 * AP or PC? With it, (synth - ingress) is the ingress cost and everything else is the
 * radio. */
#include "pipe.h"
#include "pacing.h"
#include "control.h"
#include "hdemg_frame.h"
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_random.h"
#include "esp_timer.h"

static pacer_t          s_pacer;
static volatile uint64_t s_achieved;
static uint32_t          s_seq;

uint64_t source_achieved_bps(void) { return s_achieved; }

static void fill_payload(uint8_t *dst, size_t n)
{
    /* Random bytes: the pessimal case for any upstream codec, and stated as such.
     * With no ESP-side compression there is no downside to using them. */
    esp_fill_random(dst, n);
}

static void source_task(void *arg)
{
    (void)arg;
    const run_cfg_t *cfg = control_cfg();
    uint8_t *scratch = malloc(2048);
    for (;;) {
        while (!control_running()) { s_achieved = 0; vTaskDelay(pdMS_TO_TICKS(20)); }

        uint32_t frame_bytes = cfg->frame_bytes;
        if (frame_bytes < HDEMG_HDR_BYTES + 2) frame_bytes = HDEMG_RAW16_128CH;
        uint32_t payload_bytes = frame_bytes - HDEMG_HDR_BYTES;
        fill_payload(scratch, payload_bytes > 2048 ? 2048 : payload_bytes);
        pacer_init(&s_pacer, cfg->rate_bps, frame_bytes);
        int64_t end_us = esp_timer_get_time() + (int64_t)cfg->dur_s * 1000000;

        while (control_running() && esp_timer_get_time() < end_us) {
            if (!pacer_due(&s_pacer)) { taskYIELD(); continue; }

            uint8_t *buf = pipe_acquire();
            if (!buf) { pipe_note_drop(); vTaskDelay(1); continue; }

            size_t cap = pipe_capacity();
            size_t used = 0;
            uint32_t n_frames = 0;
            /* Batch whole frames into one pool buffer: the per-transaction cost scales
             * with transaction count, not bytes, so batching is what keeps the source
             * cheap at high rates. Never split a frame across buffers. */
            while (used + frame_bytes <= cap && pacer_due(&s_pacer)) {
                hdemg_hdr_t h = {
                    .magic = HDEMG_MAGIC, .type = HDEMG_TYPE_RAW16,
                    .chip_id = HDEMG_CHIP_COMBINED, .seq = ++s_seq,
                    .t_stm = (uint32_t)esp_timer_get_time(), .n_ch = 128,
                };
                memcpy(buf + used, &h, HDEMG_HDR_BYTES);
                memcpy(buf + used + HDEMG_HDR_BYTES, scratch,
                       payload_bytes > 2048 ? 2048 : payload_bytes);
                used += frame_bytes;
                n_frames++;
                pacer_note(&s_pacer);
            }
            if (used == 0) { pipe_release(buf); taskYIELD(); continue; }
            pipe_submit(buf, used);
            s_achieved = pacer_achieved_bps(&s_pacer);
        }
        control_stop_run();
        s_achieved = pacer_achieved_bps(&s_pacer);
    }
}

void source_start(void) { xTaskCreate(source_task, "src", 4096, NULL, 6, NULL); }
void source_stop(void)  { control_stop_run(); }
