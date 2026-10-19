#include "control.h"
#include "pipe.h"
#include "instr.h"
#include "linkdrop.h"
#include "bench_pins.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "esp_timer.h"

static const char *TAG = "ctl";
static run_cfg_t   s_cfg = {
    .rate_bps = 0, .transport = XPORT_UDP, .dst_ip = "0.0.0.0", .dst_port = 3333,
    .dur_s = 10, .frame_bytes = HDEMG_RAW16_128CH, .payload = "rand",
    .label = "unset",
    .nodelay = 1, .wr_max = 0, .dgram = 0,          /* v1 behaviour */
    .batch = 0,
};
static volatile bool s_running;

/* Static, not on the control task's stack: a stat reply is up to CONTROL_REPLY_MAX bytes,
 * and the control task is their only user. */
static char s_rx[320], s_tx[CONTROL_REPLY_MAX];

const run_cfg_t *control_cfg(void)   { return &s_cfg; }
bool control_running(void)           { return s_running; }
void control_stop_run(void)          { s_running = false; }

static int reply(int s, struct sockaddr_in *peer, const char *json)
{
    return sendto(s, json, strlen(json), 0, (struct sockaddr *)peer, sizeof(*peer));
}

static void handle_cfg(char *rest, char *out, size_t out_sz)
{
    char *save = NULL;
    for (char *tok = strtok_r(rest, " \t", &save); tok; tok = strtok_r(NULL, " \t", &save)) {
        char *eq = strchr(tok, '=');
        if (!eq) continue;
        *eq = 0;
        const char *k = tok, *v = eq + 1;
        if      (!strcmp(k, "rate_bps"))    s_cfg.rate_bps = strtoull(v, NULL, 10);
        else if (!strcmp(k, "transport"))   s_cfg.transport = strcmp(v, "tcp") ? XPORT_UDP : XPORT_TCP;
        else if (!strcmp(k, "dur_s"))       s_cfg.dur_s = (uint32_t)atoi(v);
        else if (!strcmp(k, "frame_bytes")) s_cfg.frame_bytes = (uint32_t)atoi(v);
        else if (!strcmp(k, "payload"))     { strncpy(s_cfg.payload, v, sizeof(s_cfg.payload)-1); }
        else if (!strcmp(k, "label"))       { strncpy(s_cfg.label, v, sizeof(s_cfg.label)-1); }
        /* v2. Read by the sink at the next connect (nodelay) or the next buffer (wr_max,
         * dgram); rounding to whole frames happens there, against the frame size then in
         * force, so the echo below is the value as given. */
        else if (!strcmp(k, "nodelay"))     s_cfg.nodelay = atoi(v) ? 1 : 0;
        else if (!strcmp(k, "wr_max"))      s_cfg.wr_max = (uint32_t)strtoul(v, NULL, 10);
        else if (!strcmp(k, "dgram"))       s_cfg.dgram = (uint32_t)strtoul(v, NULL, 10);
        /* Read by the source at the next run start; capped there at one pool buffer. */
        else if (!strcmp(k, "batch"))       s_cfg.batch = (uint32_t)strtoul(v, NULL, 10);
        else if (!strcmp(k, "dst")) {
            const char *colon = strrchr(v, ':');
            if (colon) {
                size_t n = (size_t)(colon - v);
                if (n >= sizeof(s_cfg.dst_ip)) n = sizeof(s_cfg.dst_ip) - 1;
                memcpy(s_cfg.dst_ip, v, n); s_cfg.dst_ip[n] = 0;
                s_cfg.dst_port = (uint16_t)atoi(colon + 1);
            }
        }
    }
    snprintf(out, out_sz,
             "{\"ok\":true,\"applied\":{\"rate_bps\":%llu,\"transport\":\"%s\","
             "\"dst\":\"%s:%u\",\"dur_s\":%lu,\"frame_bytes\":%lu,\"label\":\"%s\","
             "\"nodelay\":%u,\"wr_max\":%lu,\"dgram\":%lu,\"batch\":%lu}}",
             (unsigned long long)s_cfg.rate_bps,
             s_cfg.transport == XPORT_TCP ? "tcp" : "udp",
             s_cfg.dst_ip, (unsigned)s_cfg.dst_port,
             (unsigned long)s_cfg.dur_s, (unsigned long)s_cfg.frame_bytes, s_cfg.label,
             (unsigned)s_cfg.nodelay, (unsigned long)s_cfg.wr_max, (unsigned long)s_cfg.dgram,
             (unsigned long)s_cfg.batch);
}

static void control_task(void *arg)
{
    (void)arg;
    int s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    struct sockaddr_in me = { .sin_family = AF_INET, .sin_port = htons(CONTROL_PORT),
                              .sin_addr.s_addr = htonl(INADDR_ANY) };
    if (bind(s, (struct sockaddr *)&me, sizeof(me)) < 0) {
        ESP_LOGE(TAG, "control bind failed"); vTaskDelete(NULL); return;
    }
    char *rx = s_rx, *tx = s_tx;
    for (;;) {
        struct sockaddr_in peer; socklen_t plen = sizeof(peer);
        int n = recvfrom(s, rx, sizeof(s_rx) - 1, 0, (struct sockaddr *)&peer, &plen);
        if (n <= 0) continue;
        rx[n] = 0;
        char *sp = strpbrk(rx, " \t");
        char *rest = NULL;
        if (sp) { *sp = 0; rest = sp + 1; }
        for (char *p = rx; *p; ++p) if (*p == '\r' || *p == '\n') *p = 0;
        if (rest) for (char *p = rest; *p; ++p) if (*p == '\r' || *p == '\n') *p = 0;

        if (!strcmp(rx, "hello")) {
            const esp_app_desc_t *d = esp_app_get_description();
            snprintf(tx, sizeof(s_tx),
                     "{\"ok\":true,\"proto\":%d,\"chip\":\"%s\",\"idf\":\"%s\","
                     "\"fw_sha\":\"%.8s\",\"ingress\":\"%s\",\"rung\":\"%s\","
                     "\"band\":\"%s\"}",
                     CONTROL_PROTOCOL_VERSION, CONFIG_IDF_TARGET, d->idf_ver,
                     d->version, INGRESS_NAME, RUNG_NAME, instr_band());
        } else if (!strcmp(rx, "cfg") && rest) {
            handle_cfg(rest, tx, sizeof(s_tx));
        } else if (!strcmp(rx, "start")) {
            pipe_reset_stats();
            s_running = true;
            snprintf(tx, sizeof(s_tx), "{\"ok\":true,\"t0_us\":%lu}",
                     (unsigned long)(esp_timer_get_time() & 0xFFFFFFFF));
        } else if (!strcmp(rx, "stop")) {
            s_running = false;
            instr_stat_json(tx, sizeof(s_tx), "summary");
        } else if (!strcmp(rx, "stat")) {
            instr_stat_json(tx, sizeof(s_tx), NULL);
        } else if (!strcmp(rx, "reset")) {
            reply(s, &peer, "{\"ok\":true}");
            vTaskDelay(pdMS_TO_TICKS(200));
            esp_restart();
            continue;
        } else if (!strcmp(rx, "linkdrop")) {
            /* The reply has to go out BEFORE the link does: once the radio is off the
             * board cannot answer. The drop's task is created parked and released only
             * after the reply has been handed to the socket. */
            char *end = NULL;
            unsigned long ms = rest ? strtoul(rest, &end, 10) : 0;
            if (!rest || end == rest) {
                snprintf(tx, sizeof(s_tx), "{\"ok\":false,\"err\":\"usage: linkdrop <ms>\"}");
            } else {
                const char *why = linkdrop_arm((uint32_t)ms);
                if (why) {
                    snprintf(tx, sizeof(s_tx), "{\"ok\":false,\"err\":\"%s\"}", why);
                } else {
                    snprintf(tx, sizeof(s_tx), "{\"ok\":true,\"ms\":%lu}",
                             (unsigned long)linkdrop_clamp_ms((uint32_t)ms));
                    reply(s, &peer, tx);
                    linkdrop_fire();
                    continue;
                }
            }
        } else if (!strcmp(rx, "pins")) {
            bench_pins_command(rest, tx, sizeof(s_tx));
        } else {
            snprintf(tx, sizeof(s_tx), "{\"ok\":false,\"err\":\"unknown command\"}");
        }
        reply(s, &peer, tx);
    }
}

void control_start(void)
{
    linkdrop_init();          /* after the target's STA_START handlers: see linkdrop.c */
    xTaskCreate(control_task, "ctl", 4096, NULL, 5, NULL);
}
