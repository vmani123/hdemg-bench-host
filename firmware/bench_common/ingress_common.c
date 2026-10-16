/* ingress_common.c — buffer verification, counters and the stat port shared by the
 * wired-ingress links. See ingress.h. */
#include "ingress.h"
#include "control.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "lwip/sockets.h"

static const char *TAG = "ingress";

static ingress_stats_t   s_st;
static portMUX_TYPE      s_mux = portMUX_INITIALIZER_UNLOCKED;
static const char       *s_link = "none";
static volatile uint32_t s_loaded, s_completed;
static int64_t           s_t0_us, s_t_end_us;
static volatile bool     s_in_run;
static bool              s_have_seq;
static uint64_t          s_final_bps;

void ingress_set_init_err(int err) { s_st.init_err = err; }
void ingress_set_counters(uint32_t loaded, uint32_t completed) { s_loaded = loaded; s_completed = completed; }
bool ingress_link_test(void) { return s_st.link_test != 0; }
void ingress_note_starved(void) { s_st.pool_starved++; }
void ingress_note_stray(void) { s_st.stray_bufs++; }

void ingress_run_begin(void)
{
    int32_t init_err = s_st.init_err;
    portENTER_CRITICAL(&s_mux);
    memset(&s_st, 0, sizeof(s_st));
    portEXIT_CRITICAL(&s_mux);
    s_st.init_err = init_err;

    const char *payload = control_cfg()->payload;
    if (strncmp(payload, "lt:", 3) == 0) {
        s_st.link_test = 1;
        s_st.seed = (uint32_t)strtoul(payload + 3, NULL, 0);
    }
    s_have_seq = false;
    s_final_bps = 0;
    s_t0_us = esp_timer_get_time();
    s_in_run = true;
}

void ingress_run_end(void)
{
    s_t_end_us = esp_timer_get_time();
    s_in_run = false;
    int64_t el = s_t_end_us - s_t0_us;
    s_final_bps = el > 0 ? (uint64_t)((double)s_st.bytes_in * 8.0 * 1e6 / (double)el) : 0;
}

uint64_t ingress_achieved_bps(void)
{
    if (!s_in_run) return s_final_bps;
    int64_t el = esp_timer_get_time() - s_t0_us;
    if (el <= 0) return 0;
    return (uint64_t)((double)s_st.bytes_in * 8.0 * 1e6 / (double)el);
}

bool ingress_accept(const uint8_t *buf, size_t len)
{
    if (len == 0 || len > (size_t)HDEMG_LINK_MAX_FRAMES * HDEMG_RAW16_128CH ||
        (len % HDEMG_RAW16_128CH) != 0) {
        s_st.len_errors++;
        return false;
    }
    const uint32_t n = (uint32_t)(len / HDEMG_RAW16_128CH);
    const bool full = s_st.link_test != 0;
    bool ok = true;

    for (uint32_t f = 0; f < n; f++) {
        const uint8_t *p = buf + (size_t)f * HDEMG_RAW16_128CH;
        hdemg_hdr_t h;
        memcpy(&h, p, HDEMG_HDR_BYTES);
        if (h.magic != HDEMG_MAGIC) {
            s_st.magic_errors++;
            ok = false;
            continue;                      /* without a header there is no seq to trust */
        }
        if (!s_have_seq) {
            s_st.first_seq = h.seq;
            s_have_seq = true;
        } else if (h.seq != s_st.last_seq + 1U) {
            uint32_t d = h.seq - s_st.last_seq;            /* wrap-safe */
            if (d != 0 && d < 0x80000000U) { s_st.seq_gaps += d - 1U; s_st.seq_gap_events++; }
            else                           { s_st.seq_backwards++; }
        }
        s_st.last_seq = h.seq;

        if (full) {
            uint32_t x = hdemg_payload_state0(h.seq, s_st.seed);
            const uint8_t *q = p + HDEMG_HDR_BYTES;
            for (uint32_t i = 0; i < HDEMG_PAYLOAD_WORDS; i++) {
                uint32_t w;
                x = hdemg_xs32(x);
                memcpy(&w, q + 4U * i, 4);
                if (w != x) { s_st.payload_errors++; break; }
            }
        }
    }
    if (ok) {
        portENTER_CRITICAL(&s_mux);
        s_st.bytes_in += len;
        portEXIT_CRITICAL(&s_mux);
        s_st.bufs_in++;
        s_st.frames_in += n;
    }
    return ok;
}

static void stat_task(void *arg)
{
    (void)arg;
    int s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    struct sockaddr_in me = { .sin_family = AF_INET, .sin_port = htons(INGRESS_STAT_PORT),
                              .sin_addr.s_addr = htonl(INADDR_ANY) };
    if (s < 0 || bind(s, (struct sockaddr *)&me, sizeof(me)) < 0) {
        ESP_LOGE(TAG, "stat port bind failed");
        vTaskDelete(NULL);
        return;
    }
    char rx[32], tx[640];
    for (;;) {
        struct sockaddr_in peer;
        socklen_t plen = sizeof(peer);
        int n = recvfrom(s, rx, sizeof(rx) - 1, 0, (struct sockaddr *)&peer, &plen);
        if (n <= 0) continue;
        rx[n] = 0;
        if (strncmp(rx, "istat", 5) != 0) {
            snprintf(tx, sizeof(tx), "{\"ok\":false,\"err\":\"unknown command\"}");
        } else {
            ingress_stats_t c;
            portENTER_CRITICAL(&s_mux);
            c = s_st;
            portEXIT_CRITICAL(&s_mux);
            snprintf(tx, sizeof(tx),
                     "{\"ok\":true,\"link\":\"%s\",\"init_err\":%ld,\"in_run\":%s,"
                     "\"link_test\":%lu,\"seed\":%lu,"
                     "\"bytes_in\":%llu,\"bufs_in\":%lu,\"frames_in\":%lu,"
                     "\"len_errors\":%lu,\"magic_errors\":%lu,\"seq_gaps\":%lu,"
                     "\"seq_gap_events\":%lu,\"seq_backwards\":%lu,\"payload_errors\":%lu,"
                     "\"pool_starved\":%lu,\"stray_bufs\":%lu,"
                     "\"first_seq\":%lu,\"last_seq\":%lu,"
                     "\"loaded\":%lu,\"completed\":%lu,\"achieved_bps\":%llu}",
                     s_link, (long)c.init_err, s_in_run ? "true" : "false",
                     (unsigned long)c.link_test, (unsigned long)c.seed,
                     (unsigned long long)c.bytes_in, (unsigned long)c.bufs_in,
                     (unsigned long)c.frames_in,
                     (unsigned long)c.len_errors, (unsigned long)c.magic_errors,
                     (unsigned long)c.seq_gaps, (unsigned long)c.seq_gap_events,
                     (unsigned long)c.seq_backwards, (unsigned long)c.payload_errors,
                     (unsigned long)c.pool_starved, (unsigned long)c.stray_bufs,
                     (unsigned long)c.first_seq, (unsigned long)c.last_seq,
                     (unsigned long)s_loaded, (unsigned long)s_completed,
                     (unsigned long long)ingress_achieved_bps());
        }
        sendto(s, tx, strlen(tx), 0, (struct sockaddr *)&peer, sizeof(peer));
    }
}

void ingress_common_start(const char *link_name)
{
    s_link = link_name;
    xTaskCreate(stat_task, "istat", 4096, NULL, 4, NULL);
}
