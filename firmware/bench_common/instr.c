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

static volatile uint32_t s_idle_ticks[portNUM_PROCESSORS];
static uint32_t          s_idle_window[portNUM_PROCESSORS];
static int64_t           s_window_t0;

/* FreeRTOS idle hooks: count idle passes per core, convert to a percentage against a
 * calibrated free-running maximum. Cheap, and it is the only CPU-headroom number that
 * does not require a profiler on the bench. */
static uint32_t s_idle_max_per_s = 1;

bool vApplicationIdleHook(void)
{
#if portNUM_PROCESSORS > 1
    s_idle_ticks[xPortGetCoreID()]++;
#else
    s_idle_ticks[0]++;
#endif
    return false;
}

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
        s_window_t0 = esp_timer_get_time();
        vTaskDelay(pdMS_TO_TICKS(1000));
        for (int c = 0; c < portNUM_PROCESSORS; c++) s_idle_window[c] = s_idle_ticks[c];
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
    const char *mode = "legacy";
#ifdef CONFIG_SOC_WIFI_HE_SUPPORT
    if (ap.phy_11ax) mode = "HE";
    else
#endif
    if (ap.phy_11n) mode = "HT";
    else if (ap.phy_11g) mode = "11g";
    snprintf(out, n, "%s ch%u", mode, (unsigned)ap.primary);
}

void instr_stat_json(char *out, size_t out_sz, const char *wrap_key)
{
    pipe_stats_t st; pipe_stats(&st);
    char idle[48] = "[";
    for (int c = 0; c < portNUM_PROCESSORS; c++) {
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
    xTaskCreate(instr_task, "instr", 3072, NULL, 1, NULL);
}
