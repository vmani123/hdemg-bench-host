/* pipe.c — buffer pool, queues, and the single byte-counting point. */
#include "pipe.h"
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "freertos/task.h"
#include "control.h"

static uint8_t          s_pool[PIPE_NUM_BUFS][PIPE_BUF_BYTES];
static QueueHandle_t    s_free_q, s_filled_q;
static SemaphoreHandle_t s_lock;
static pipe_stats_t     s_stats;
static volatile int     s_connected;

typedef struct { uint8_t *buf; size_t len; } slot_t;

void pipe_init(void)
{
    s_free_q   = xQueueCreate(PIPE_NUM_BUFS, sizeof(uint8_t *));
    s_filled_q = xQueueCreate(PIPE_NUM_BUFS, sizeof(slot_t));
    s_lock     = xSemaphoreCreateMutex();
    for (int i = 0; i < PIPE_NUM_BUFS; i++) {
        uint8_t *p = s_pool[i];
        xQueueSend(s_free_q, &p, 0);
    }
    memset(&s_stats, 0, sizeof(s_stats));
}

size_t pipe_capacity(void) { return PIPE_BUF_BYTES; }

uint8_t *pipe_acquire(void)
{
    uint8_t *p = NULL;
    /* Non-blocking on purpose: a source that cannot get a buffer is a measurable
     * event (pool starvation), not something to hide behind a blocking wait. */
    if (xQueueReceive(s_free_q, &p, 0) != pdTRUE) return NULL;
    return p;
}

void pipe_submit(uint8_t *buf, size_t len)
{
    slot_t s = { .buf = buf, .len = len };
    if (xQueueSend(s_filled_q, &s, 0) != pdTRUE) {
        xQueueSend(s_free_q, &buf, 0);
        pipe_note_drop();
    }
}

void pipe_release(uint8_t *buf) { xQueueSend(s_free_q, &buf, 0); }

int pipe_take(uint8_t **buf, size_t *len)
{
    slot_t s;
    if (xQueueReceive(s_filled_q, &s, pdMS_TO_TICKS(100)) != pdTRUE) return 0;
    *buf = s.buf; *len = s.len;
    return 1;
}

void pipe_recycle(uint8_t *buf) { xQueueSend(s_free_q, &buf, 0); }

/* ---- THE BYTE-COUNTING POINT ------------------------------------------------
 * Called by the sink after the socket layer has accepted the buffer. Bytes are
 * counted here and nowhere else, on both chips. */
void pipe_note_sent(size_t bytes, uint32_t frames)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_stats.bytes_sent  += bytes;
    s_stats.frames_sent += frames;
    xSemaphoreGive(s_lock);
}

void pipe_note_drop(void)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_stats.dropped++;
    s_stats.starve_events++;
    xSemaphoreGive(s_lock);
}

void pipe_stats(pipe_stats_t *out)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    *out = s_stats;
    xSemaphoreGive(s_lock);
}

void pipe_reset_stats(void)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    memset(&s_stats, 0, sizeof(s_stats));
    xSemaphoreGive(s_lock);
}

void pipe_set_connected(int up) { s_connected = up; }

void pipe_wait_connected(void)
{
    while (!s_connected) vTaskDelay(pdMS_TO_TICKS(50));
}

/* ---- the one sink task ------------------------------------------------------
 * Chooses its transport backend per run from the control-plane config, then drains
 * the pipe. pipe_note_sent() below is THE byte-counting point. */
static void sink_task(void *arg)
{
    (void)arg;
    const transport_ops_t *t = NULL;
    int fd = -1;
    for (;;) {
        uint8_t *buf; size_t len;
        if (!pipe_take(&buf, &len)) {
            if (!control_running() && fd >= 0) { t->close(fd); fd = -1; t = NULL; }
            continue;
        }
        const transport_ops_t *want =
            (control_cfg()->transport == XPORT_TCP) ? transport_tcp() : transport_udp();
        if (t != want) {                       /* transport changed between runs */
            if (fd >= 0) t->close(fd);
            t = want; fd = -1;
        }
        if (fd < 0) {
            fd = t->open();
            if (fd < 0) { pipe_recycle(buf); vTaskDelay(pdMS_TO_TICKS(200)); continue; }
        }
        int n = t->send(fd, buf, len);
        if (n > 0) {
            uint32_t fb = control_cfg()->frame_bytes;
            pipe_note_sent((size_t)n, fb ? (uint32_t)(len / fb) : 0);
        } else {
            t->close(fd); fd = -1;             /* reopen on the next buffer */
        }
        pipe_recycle(buf);
    }
}

void pipe_run(void)
{
    xTaskCreate(sink_task, "sink", 4096, NULL, 7, NULL);
    source_start();
}
