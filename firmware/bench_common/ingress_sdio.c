/* ingress_sdio.c — Stage 2b: SDIO 4-bit slave, fed by the STM32H745's SDMMC1
 * (guide §6). ESP32-C5 only: the S3 has no SDIO slave.
 *
 * NOT YET RUN AGAINST HARDWARE: the 2b harness and its pull-ups have not been wired.
 *
 * Receive buffers are the pool buffers themselves (zero copy). The installed IDF limits
 * a receive buffer to 4092 bytes, so one packet from the master is at most
 * HDEMG_SDIO_MAX_FRAMES (15) frames and uses the first half of a pool buffer. Flow
 * control is the driver's own token counter: every loaded buffer is one credit the
 * master reads back over the bus, so there is no READY line. */
#include "ingress.h"
#include "pipe.h"
#include "control.h"
#include "soc/soc_caps.h"

#if SOC_SDIO_SLAVE_SUPPORTED
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/sdio_slave.h"
#include "esp_timer.h"
#include "esp_log.h"

_Static_assert(HDEMG_SDIO_RECV_BUF_BYTES <= SDIO_SLAVE_RECV_MAX_BUFFER, "IDF receive-buffer limit");
_Static_assert(HDEMG_SDIO_RECV_BUF_BYTES <= PIPE_BUF_BYTES, "a receive buffer is a pool buffer");

static const char *TAG = "ingress_sdio";

static uint8_t                *s_buf[PIPE_NUM_BUFS];
static sdio_slave_buf_handle_t s_handle[PIPE_NUM_BUFS];
static int                     s_known;
static int                     s_armed;
static bool                    s_starved;
static uint32_t                s_loaded, s_completed;

/* Pool buffers are registered with the driver the first time each one is seen. */
static sdio_slave_buf_handle_t handle_for(uint8_t *b)
{
    for (int i = 0; i < s_known; i++) if (s_buf[i] == b) return s_handle[i];
    if (s_known >= PIPE_NUM_BUFS) return NULL;
    sdio_slave_buf_handle_t h = sdio_slave_recv_register_buf(b);
    if (h == NULL) return NULL;
    s_buf[s_known] = b;
    s_handle[s_known] = h;
    s_known++;
    return h;
}

static void arm(void)
{
    while (s_armed < PIPE_NUM_BUFS) {
        uint8_t *b = pipe_acquire();
        if (b == NULL) {
            if (s_armed == 0 && !s_starved) { s_starved = true; ingress_note_starved(); }
            return;
        }
        sdio_slave_buf_handle_t h = handle_for(b);
        if (h == NULL || sdio_slave_recv_load_buf(h) != ESP_OK) { pipe_release(b); return; }
        s_armed++;
        s_loaded++;
        s_starved = false;
    }
}

static void ingress_task(void *arg)
{
    (void)arg;
    bool was_running = false;
    int64_t end_us = 0;

    for (;;) {
        bool run = control_running();
        if (run && !was_running) {
            ingress_run_begin();
            end_us = esp_timer_get_time() + (int64_t)control_cfg()->dur_s * 1000000;
        }
        if (run && esp_timer_get_time() >= end_us) {
            control_stop_run();
            run = false;
        }
        if (!run && was_running) ingress_run_end();
        was_running = run;

        if (run) arm();
        ingress_set_counters(s_loaded, s_completed);

        sdio_slave_buf_handle_t h = NULL;
        esp_err_t r = sdio_slave_recv_packet(&h, 1);
        if (r != ESP_OK && r != ESP_ERR_NOT_FINISHED) continue;

        size_t len = 0;
        uint8_t *b = sdio_slave_recv_get_buf(h, &len);
        s_armed--;
        s_completed++;

        if (!run) {
            ingress_note_stray();
            pipe_release(b);
        } else if (r == ESP_ERR_NOT_FINISHED) {
            /* A packet that spills into a second buffer means the two sides disagree on
             * the buffer size. Counted as a length error: zero length is never valid. */
            (void)ingress_accept(b, 0);
            pipe_release(b);
        } else if (ingress_accept(b, len) && !ingress_link_test()) {
            pipe_submit(b, len);
        } else {
            pipe_release(b);
        }
    }
}

void ingress_sdio_start(void)
{
    ingress_common_start("sdio");

    sdio_slave_config_t cfg = {
        .sending_mode = SDIO_SLAVE_SEND_PACKET,
        .send_queue_size = 4,
        .recv_buffer_size = HDEMG_SDIO_RECV_BUF_BYTES,
        /* First knob to turn if CMD53 CRC-fails at 25 MHz but passes at 400 kHz (guide §6.4). */
        .timing = SDIO_SLAVE_TIMING_PSEND_PSAMPLE,
        /* No internal pull-ups: they are too weak to stand in for the external ones. */
        .flags = 0,
    };
    esp_err_t err = sdio_slave_initialize(&cfg);
    if (err == ESP_OK) err = sdio_slave_start();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "sdio slave bring-up failed: %s", esp_err_to_name(err));
        ingress_set_init_err(err);
        return;
    }
    ESP_LOGI(TAG, "sdio slave up, recv buffer %u bytes", (unsigned)HDEMG_SDIO_RECV_BUF_BYTES);
    xTaskCreate(ingress_task, "src", 4096, NULL, 6, NULL);
}

#else  /* !SOC_SDIO_SLAVE_SUPPORTED */

void ingress_sdio_start(void)
{
    ingress_common_start("sdio");
    ingress_set_init_err(-1);          /* this chip has no SDIO slave */
}

#endif
