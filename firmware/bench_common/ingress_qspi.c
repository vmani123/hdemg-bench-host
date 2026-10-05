/* ingress_qspi.c — Stage 2a: GP-SPI2 as Slave HD, 4-line data phase, fed by the
 * STM32H745's QUADSPI (guide §5). Shared by the S3 and the C5; each target supplies
 * its pins.
 *
 * SEGMENT mode, not append mode. The installed IDF's append mode rejects any buffer
 * over 4092 bytes (spi_slave_hd_append_trans), which would cap a transaction at 15
 * frames and break zero-copy into the 8192-byte pool buffers. Segment mode takes the
 * full buffer: receive transactions point DIRECTLY at pool buffers, the driver's ISR
 * loads the next queued one as soon as the previous finishes, and nothing is copied.
 *
 * Flow control (guide §5.4). One buffer is in the hardware at a time, and the master
 * may only end a buffer it has credit for:
 *   - cb_recv_dma_ready (ISR): a buffer was just loaded -> LOADED++, publish, READY high.
 *   - cb_recv (ISR): the master ended a buffer -> COMPLETED++, publish, READY low.
 * The master counts the buffers it has ended and sends only while LOADED - sent > 0.
 * READY is just the hint that re-reading LOADED is worthwhile. */
#include "ingress.h"
#include "pipe.h"
#include "control.h"
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/spi_slave_hd.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "esp_memory_utils.h"

#define QSPI_HOST SPI2_HOST

_Static_assert(PIPE_BUF_BYTES == HDEMG_LINK_BUF_BYTES, "link batch size is tied to the pool buffer size");

static const char *TAG = "ingress_qspi";

static ingress_qspi_pins_t s_pins;
static spi_slave_hd_data_t s_trans[PIPE_NUM_BUFS];
static bool                s_busy[PIPE_NUM_BUFS];   /* queued in the driver, not yet returned */
static int                 s_armed;
static bool                s_starved;
static volatile uint32_t   s_loaded, s_completed;

static bool cb_recv_dma_ready(void *arg, spi_slave_hd_event_t *ev, BaseType_t *awoken)
{
    (void)arg; (void)ev; (void)awoken;
    uint32_t v = ++s_loaded;
    spi_slave_hd_write_buffer(QSPI_HOST, HDEMG_QSPI_REG_LOADED, (uint8_t *)&v, 4);
    gpio_set_level(s_pins.ready, 1);
    return true;
}

static bool cb_recv(void *arg, spi_slave_hd_event_t *ev, BaseType_t *awoken)
{
    (void)arg; (void)ev; (void)awoken;
    gpio_set_level(s_pins.ready, 0);
    uint32_t v = ++s_completed;
    spi_slave_hd_write_buffer(QSPI_HOST, HDEMG_QSPI_REG_COMPLETED, (uint8_t *)&v, 4);
    return true;                        /* hand the transaction to get_trans_res() */
}

/* Queue every free pool buffer as a receive transaction. */
static void arm(void)
{
    for (int i = 0; i < PIPE_NUM_BUFS; i++) {
        if (s_busy[i]) continue;
        uint8_t *b = pipe_acquire();
        if (b == NULL) {
            /* Only a starvation event if the link now has nothing to receive into:
             * that is the sink (Wi-Fi) pushing back, and the master will see it as
             * missing credit. */
            if (s_armed == 0 && !s_starved) { s_starved = true; ingress_note_starved(); }
            return;
        }
        s_trans[i] = (spi_slave_hd_data_t){ .data = b, .len = PIPE_BUF_BYTES };
        if (spi_slave_hd_queue_trans(QSPI_HOST, SPI_SLAVE_CHAN_RX, &s_trans[i], 0) != ESP_OK) {
            pipe_release(b);
            return;
        }
        s_busy[i] = true;
        s_armed++;
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
        if (run && esp_timer_get_time() >= end_us) {   /* same self-limit as the synth source */
            control_stop_run();
            run = false;
        }
        if (!run && was_running) ingress_run_end();
        was_running = run;

        if (run) arm();
        ingress_set_counters(s_loaded, s_completed);

        spi_slave_hd_data_t *t = NULL;
        if (spi_slave_hd_get_trans_res(QSPI_HOST, SPI_SLAVE_CHAN_RX, &t, 1) != ESP_OK || t == NULL) {
            continue;
        }
        int idx = (int)(t - s_trans);
        if (idx >= 0 && idx < PIPE_NUM_BUFS) { s_busy[idx] = false; s_armed--; }

        if (!run) {
            ingress_note_stray();
            pipe_release(t->data);
        } else if (ingress_accept(t->data, t->trans_len) && !ingress_link_test()) {
            pipe_submit(t->data, t->trans_len);
        } else {
            /* Link test, or a buffer that failed the whole-frame check: never forwarded.
             * A corrupt buffer is a rig fault and invalidates the run host-side. */
            pipe_release(t->data);
        }
    }
}

void ingress_qspi_start(const ingress_qspi_pins_t *pins)
{
    s_pins = *pins;
    ingress_common_start("qspi");

    gpio_config_t io = { .pin_bit_mask = 1ULL << s_pins.ready, .mode = GPIO_MODE_OUTPUT };
    gpio_config(&io);
    gpio_set_level(s_pins.ready, 0);

    spi_bus_config_t bus = {
        .data0_io_num = s_pins.d0, .data1_io_num = s_pins.d1,
        .data2_io_num = s_pins.d2, .data3_io_num = s_pins.d3,
        .sclk_io_num = s_pins.clk,
        .max_transfer_sz = PIPE_BUF_BYTES,
    };
    spi_slave_hd_slot_config_t slot = {
        .mode = 0,
        .spics_io_num = s_pins.cs,
        .flags = 0,
        .command_bits = 8, .address_bits = 8, .dummy_bits = HDEMG_QSPI_DUMMY_CYCLES,
        .queue_size = PIPE_NUM_BUFS,
        .dma_chan = SPI_DMA_CH_AUTO,
        .cb_config = { .cb_recv_dma_ready = cb_recv_dma_ready, .cb_recv = cb_recv },
    };
    esp_err_t err = spi_slave_hd_init(QSPI_HOST, &bus, &slot);
    if (err != ESP_OK) {
        /* Stay up and reachable: the fault is reported on the stat port instead of
         * turning into a boot loop nobody is awake to see. */
        ESP_LOGE(TAG, "spi_slave_hd_init failed: %s", esp_err_to_name(err));
        ingress_set_init_err(err);
        return;
    }
    uint32_t zero = 0, magic = HDEMG_QSPI_MAGIC;
    spi_slave_hd_write_buffer(QSPI_HOST, HDEMG_QSPI_REG_LOADED, (uint8_t *)&zero, 4);
    spi_slave_hd_write_buffer(QSPI_HOST, HDEMG_QSPI_REG_COMPLETED, (uint8_t *)&zero, 4);
    spi_slave_hd_write_buffer(QSPI_HOST, HDEMG_QSPI_REG_MAGIC, (uint8_t *)&magic, 4);

    ESP_LOGI(TAG, "slave HD up: cs=%d clk=%d d0..3=%d,%d,%d,%d ready=%d",
             s_pins.cs, s_pins.clk, s_pins.d0, s_pins.d1, s_pins.d2, s_pins.d3, s_pins.ready);
    xTaskCreate(ingress_task, "src", 4096, NULL, 6, NULL);
}
