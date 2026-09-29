#include "instr.h"
#include "pipe.h"
#include "control.h"
#include "hdemg_frame.h"
#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_wifi.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_freertos_hooks.h"
#include "soc/soc_caps.h"
#include <stdbool.h>

/* CONFIG_FREERTOS_NUMBER_OF_CORES replaced portNUM_PROCESSORS in IDF v5.3 and the old
 * name is gone in v6. */
#ifndef CONFIG_FREERTOS_NUMBER_OF_CORES
#define CONFIG_FREERTOS_NUMBER_OF_CORES 1
#endif
#define NCORES CONFIG_FREERTOS_NUMBER_OF_CORES

static volatile uint32_t s_idle_ticks[NCORES];
static uint32_t          s_idle_window[NCORES];

/* Idle hooks count idle passes per core; the count is converted to a percentage against
 * a maximum calibrated at boot. Cheap, and it is the only CPU-headroom number available
 * without a profiler on the bench — which matters because idle-at-the-knee is the
 * observable that settles the single-core question (plan §2.3).
 *
 * esp_register_freertos_idle_hook_for_cpu() is used rather than the plain
 * vApplicationIdleHook: it is per-core, which is the whole point on the dual-core S3,
 * and it does not depend on CONFIG_FREERTOS_USE_IDLE_HOOK. Its callback returns bool. */
static uint32_t s_idle_max_per_s = 1;

static bool idle_hook_cpu0(void) { s_idle_ticks[0]++; return false; }
#if NCORES > 1
static bool idle_hook_cpu1(void) { s_idle_ticks[1]++; return false; }
#endif

static void instr_task(void *arg)
{
    (void)arg;
    /* Calibrate: one second of (almost) pure idle at boot gives the 100% reference. */
    memset((void *)s_idle_ticks, 0, sizeof(s_idle_ticks));
    vTaskDelay(pdMS_TO_TICKS(1000));
    uint32_t cal = s_idle_ticks[0];
    if (cal > 0) s_idle_max_per_s = cal;

    for (;;) {
        memset((void *)s_idle_ticks, 0, sizeof(s_idle_ticks));
        vTaskDelay(pdMS_TO_TICKS(1000));
        for (int c = 0; c < NCORES; c++) s_idle_window[c] = s_idle_ticks[c];
    }
}

const char *instr_band(void)
{
    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) != ESP_OK) return "unknown";
    return (ap.primary > 14) ? "5" : "2.4";
}

int instr_rssi(void)
{
    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) != ESP_OK) return 0;
    return ap.rssi;
}

static void phy_rate_str(char *out, size_t n)
{
    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) != ESP_OK) { snprintf(out, n, "unknown"); return; }
    /* SOC_WIFI_HE_SUPPORT comes from soc_caps.h — it is NOT a Kconfig symbol, so the
     * old CONFIG_ prefix silently compiled the HE branch out. This is the field that
     * answers whether the access point ever offered 802.11ax (plan §7.3b): until it
     * reads HE, the C5 was not exercised at Wi-Fi 6 rates and its figure is a lower
     * bound. Getting the guard wrong would have hidden exactly that. */
    const char *mode = "legacy";
#if SOC_WIFI_HE_SUPPORT
    if (ap.phy_11ax) mode = "HE";
    else
#endif
    if (ap.phy_11n) mode = "HT";
    else if (ap.phy_11g) mode = "11g";
    snprintf(out, n, "%s ch%u bw%u", mode, (unsigned)ap.primary,
             ap.second == WIFI_SECOND_CHAN_NONE ? 20u : 40u);
}

void instr_stat_json(char *out, size_t out_sz, const char *wrap_key)
{
    pipe_stats_t st; pipe_stats(&st);
    char idle[48] = "[";
    for (int c = 0; c < NCORES; c++) {
        char one[16];
        float pct = 100.0f * (float)s_idle_window[c] / (float)s_idle_max_per_s;
        if (pct > 100.0f) pct = 100.0f;
        snprintf(one, sizeof(one), "%s%.1f", c ? "," : "", pct);
        strncat(idle, one, sizeof(idle) - strlen(idle) - 2);
    }
    strncat(idle, "]", sizeof(idle) - strlen(idle) - 1);

    char phy[32]; phy_rate_str(phy, sizeof(phy));
    char body[480];
    snprintf(body, sizeof(body),
             "\"bytes\":%llu,\"frames\":%llu,\"dropped\":%lu,\"idle_pct\":%s,"
             "\"rssi\":%d,\"phy_rate\":\"%s\",\"heap\":%u,\"heap_min\":%u,"
             "\"achieved_bps\":%llu,\"label\":\"%s\"",
             (unsigned long long)st.bytes_sent, (unsigned long long)st.frames_sent,
             (unsigned long)st.dropped, idle, instr_rssi(), phy,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
             (unsigned long long)source_achieved_bps(), control_cfg()->label);

    if (wrap_key) snprintf(out, out_sz, "{\"ok\":true,\"%s\":{%s}}", wrap_key, body);
    else          snprintf(out, out_sz, "{\"ok\":true,%s}", body);
}

void instr_start(void)
{
    esp_register_freertos_idle_hook_for_cpu(idle_hook_cpu0, 0);
#if NCORES > 1
    esp_register_freertos_idle_hook_for_cpu(idle_hook_cpu1, 1);
#endif
    xTaskCreate(instr_task, "instr", 3072, NULL, 1, NULL);
}
