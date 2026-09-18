#include "control.h"
#include "pipe.h"
#include "instr.h"
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
};
static volatile bool s_running;

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
             "\"dst\":\"%s:%u\",\"dur_s\":%lu,\"frame_bytes\":%lu,\"label\":\"%s\"}}",
             (unsigned long long)s_cfg.rate_bps,
             s_cfg.transport == XPORT_TCP ? "tcp" : "udp",
             s_cfg.dst_ip, (unsigned)s_cfg.dst_port,
             (unsigned long)s_cfg.dur_s, (unsigned long)s_cfg.frame_bytes, s_cfg.label);
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
    char rx[320], tx[640];
    for (;;) {
        struct sockaddr_in peer; socklen_t plen = sizeof(peer);
        int n = recvfrom(s, rx, sizeof(rx) - 1, 0, (struct sockaddr *)&peer, &plen);
        if (n <= 0) continue;
        rx[n] = 0;
        char *sp = strpbrk(rx, " \t");
        char *rest = NULL;
        if (sp) { *sp = 0; rest = sp + 1; }
        for (char *p = rx; *p; ++p) if (*p == '\r' || *p == '\n') *p = 0;

        if (!strcmp(rx, "hello")) {
            const esp_app_desc_t *d = esp_app_get_description();
            snprintf(tx, sizeof(tx),
                     "{\"ok\":true,\"proto\":%d,\"chip\":\"%s\",\"idf\":\"%s\","
                     "\"fw_sha\":\"%.8s\",\"ingress\":\"%s\",\"rung\":\"%s\","
                     "\"band\":\"%s\"}",
                     CONTROL_PROTOCOL_VERSION, CONFIG_IDF_TARGET, d->idf_ver,
                     d->version, INGRESS_NAME, RUNG_NAME, instr_band());
        } else if (!strcmp(rx, "cfg") && rest) {
            handle_cfg(rest, tx, sizeof(tx));
        } else if (!strcmp(rx, "start")) {
            pipe_reset_stats();
            s_running = true;
            snprintf(tx, sizeof(tx), "{\"ok\":true,\"t0_us\":%lu}",
                     (unsigned long)(esp_timer_get_time() & 0xFFFFFFFF));
        } else if (!strcmp(rx, "stop")) {
            s_running = false;
            instr_stat_json(tx, sizeof(tx), "summary");
        } else if (!strcmp(rx, "stat")) {
            instr_stat_json(tx, sizeof(tx), NULL);
        } else if (!strcmp(rx, "reset")) {
            reply(s, &peer, "{\"ok\":true}");
            vTaskDelay(pdMS_TO_TICKS(200));
            esp_restart();
            continue;
        } else {
            snprintf(tx, sizeof(tx), "{\"ok\":false,\"err\":\"unknown command\"}");
        }
        reply(s, &peer, tx);
    }
}

void control_start(void)
{
    xTaskCreate(control_task, "ctl", 4096, NULL, 5, NULL);
}
