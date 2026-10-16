#include "instr.h"
#include "pipe.h"
#include "control.h"
#include "hdemg_frame.h"
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_freertos_hooks.h"
#include "soc/soc_caps.h"
#include "lwip/opt.h"               /* TCPIP_THREAD_NAME */
#include "sdkconfig.h"
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

/* ---- health counters (protocol v2), since boot ----------------------------------------
 * Written from the sink task, the event task and the control task, read by the control
 * task. Relaxed atomics: each counter only has to be exact on its own, and a 32-bit
 * atomic add is cheap enough for the sink's back-pressure path. */
static uint32_t s_udp_enomem, s_send_errs, s_tcp_opens, s_tcp_open_fails;
static uint32_t s_wifi_disc, s_wifi_last_reason, s_linkdrops;

static inline void     bump(uint32_t *c)       { __atomic_fetch_add(c, 1u, __ATOMIC_RELAXED); }
static inline uint32_t peek(const uint32_t *c) { return __atomic_load_n(c, __ATOMIC_RELAXED); }

void instr_note_udp_enomem(void)  { bump(&s_udp_enomem); }
void instr_note_send_err(void)    { bump(&s_send_errs); }
void instr_note_tcp_open(bool ok) { bump(ok ? &s_tcp_opens : &s_tcp_open_fails); }
void instr_note_linkdrop(void)    { bump(&s_linkdrops); }

/* Every station disconnect, whatever caused it: the access point, the radio, or a
 * linkdrop. Registered once the board is up (instr_start), so the deliberate rejoins of
 * the boot sequence (band pin, access-point pin, channel width) are not counted and a
 * non-zero figure always means something happened while the bench was using the board. */
static void on_sta_disconnected(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg; (void)base; (void)id;
    const wifi_event_sta_disconnected_t *d = data;
    __atomic_store_n(&s_wifi_last_reason, d ? (uint32_t)d->reason : 0u, __ATOMIC_RELAXED);
    bump(&s_wifi_disc);
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

/* The width this STATION is configured for. The "bw" field below is what the access
 * point advertises; the link runs at the narrower of the two, so a 40 MHz rung is only
 * in effect when both read 40. 0 = could not be read. */
static unsigned sta_bw_mhz(const wifi_ap_record_t *ap)
{
    wifi_bandwidth_t bw = 0;
#if SOC_WIFI_SUPPORT_5G
    wifi_bandwidths_t bws = {0};
    if (esp_wifi_get_bandwidths(WIFI_IF_STA, &bws) != ESP_OK) return 0;
    bw = (ap->primary > 14) ? bws.ghz_5g : bws.ghz_2g;
#else
    (void)ap;
    if (esp_wifi_get_bandwidth(WIFI_IF_STA, &bw) != ESP_OK) return 0;
#endif
    return bw == WIFI_BW_HT40 ? 40u : (bw == WIFI_BW_HT20 ? 20u : 0u);
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
    /* "ap:" = the last three bytes of the access point's BSSID: tells two radios that
     * serve one network name apart in the ledger. */
    snprintf(out, n, "%s ch%u bw%u sta%u ap:%02x%02x%02x", mode, (unsigned)ap.primary,
             ap.second == WIFI_SECOND_CHAN_NONE ? 20u : 40u, sta_bw_mhz(&ap),
             ap.bssid[3], ap.bssid[4], ap.bssid[5]);
}

static const char *reset_reason_name(esp_reset_reason_t r)
{
    switch (r) {
    case ESP_RST_POWERON:    return "POWERON";
    case ESP_RST_EXT:        return "EXT";
    case ESP_RST_SW:         return "SW";
    case ESP_RST_PANIC:      return "PANIC";
    case ESP_RST_INT_WDT:    return "INT_WDT";
    case ESP_RST_TASK_WDT:   return "TASK_WDT";
    case ESP_RST_WDT:        return "WDT";
    case ESP_RST_DEEPSLEEP:  return "DEEPSLEEP";
    case ESP_RST_BROWNOUT:   return "BROWNOUT";
    case ESP_RST_SDIO:       return "SDIO";
    case ESP_RST_USB:        return "USB";
    case ESP_RST_JTAG:       return "JTAG";
    case ESP_RST_EFUSE:      return "EFUSE";
    case ESP_RST_PWR_GLITCH: return "PWR_GLITCH";
    case ESP_RST_CPU_LOCKUP: return "CPU_LOCKUP";
    default:                 return "UNKNOWN";
    }
}

/* Internal-heap lows. While local monitoring is active (started in instr_start),
 * heap_caps_get_minimum_free_size() returns the low since monitoring last started;
 * stopping it folds that window back into the lifetime figure, which it then returns.
 * So: read the window, stop, read the lifetime lows, start the next window. The window
 * is what catches a dip between two polls that a lifetime minimum set at boot hides. */
static void heap_lows(size_t *win_min, size_t *life_min, long *psram_min)
{
    *win_min = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL);
    heap_caps_monitor_local_minimum_free_size_stop();   /* ESP_FAIL if it was not running */
    *life_min = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL);
#ifdef CONFIG_SPIRAM
    *psram_min = (long)heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM);
#else
    *psram_min = -1;
#endif
    heap_caps_monitor_local_minimum_free_size_start();
}

/* Free-stack low-water per task, in bytes (ESP-IDF's FreeRTOS counts stack in bytes).
 * The lwIP thread was "tiT" in older ESP-IDF and is TCPIP_THREAD_NAME ("tcpip") in the
 * installed one; the key stays "tiT", as the protocol names it. A task that is not found
 * (no "wifi" before the driver starts, say) is left out rather than reported as zero. */
static const struct { const char *key, *task; } k_stack_tasks[] = {
    { "src", "src" }, { "sink", "sink" }, { "ctl", "ctl" }, { "instr", "instr" },
    { "tiT", TCPIP_THREAD_NAME }, { "wifi", "wifi" },
};

/* Bounded append: once the buffer is full the rest is dropped, never overrun. */
typedef struct { char *p; size_t n, off; } jbuf_t;

static void jadd(jbuf_t *b, const char *fmt, ...)
{
    if (b->off >= b->n) return;
    va_list ap;
    va_start(ap, fmt);
    int w = vsnprintf(b->p + b->off, b->n - b->off, fmt, ap);
    va_end(ap);
    if (w > 0) b->off += (size_t)w;
}

/* Worst case is about 830 bytes with every number at its widest and a 47-character
 * label, inside the 1,400-byte reply limit (CONTROL_REPLY_MAX). */
void instr_stat_json(char *out, size_t out_sz, const char *wrap_key)
{
    pipe_stats_t st; pipe_stats(&st);
    const run_cfg_t *cfg = control_cfg();
    char idle[48] = "[";
    for (int c = 0; c < NCORES; c++) {
        char one[16];
        float pct = 100.0f * (float)s_idle_window[c] / (float)s_idle_max_per_s;
        if (pct > 100.0f) pct = 100.0f;
        snprintf(one, sizeof(one), "%s%.1f", c ? "," : "", pct);
        strncat(idle, one, sizeof(idle) - strlen(idle) - 2);
    }
    strncat(idle, "]", sizeof(idle) - strlen(idle) - 1);

    char phy[48]; phy_rate_str(phy, sizeof(phy));
    size_t win_min, life_min; long psram_min;
    heap_lows(&win_min, &life_min, &psram_min);
#ifdef CONFIG_SPIRAM
    long psram_free = (long)heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
#else
    long psram_free = -1;
#endif

    jbuf_t b = { out, out_sz, 0 };
    if (wrap_key) jadd(&b, "{\"ok\":true,\"%s\":{", wrap_key);
    else          jadd(&b, "{\"ok\":true,");
    /* v1 fields, unchanged. */
    jadd(&b, "\"bytes\":%llu,\"frames\":%llu,\"dropped\":%lu,\"idle_pct\":%s,"
             "\"rssi\":%d,\"phy_rate\":\"%s\",\"heap\":%u,\"heap_min\":%u,"
             "\"achieved_bps\":%llu,\"label\":\"%s\"",
             (unsigned long long)st.bytes_sent, (unsigned long long)st.frames_sent,
             (unsigned long)st.dropped, idle, instr_rssi(), phy,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL), (unsigned)life_min,
             (unsigned long long)source_achieved_bps(), cfg->label);
    /* v2: health. */
    jadd(&b, ",\"heap_largest\":%u,\"heap_win_min\":%u,\"psram_free\":%ld,\"psram_min\":%ld,"
             "\"uptime_s\":%lu,\"reset_reason\":\"%s\"",
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL), (unsigned)win_min,
             psram_free, psram_min, (unsigned long)(esp_timer_get_time() / 1000000),
             reset_reason_name(esp_reset_reason()));
    jadd(&b, ",\"udp_enomem\":%lu,\"send_errs\":%lu,\"tcp_opens\":%lu,\"tcp_open_fails\":%lu,"
             "\"wifi_disc\":%lu,\"wifi_last_reason\":%lu,\"linkdrops\":%lu",
             (unsigned long)peek(&s_udp_enomem), (unsigned long)peek(&s_send_errs),
             (unsigned long)peek(&s_tcp_opens), (unsigned long)peek(&s_tcp_open_fails),
             (unsigned long)peek(&s_wifi_disc), (unsigned long)peek(&s_wifi_last_reason),
             (unsigned long)peek(&s_linkdrops));
    jadd(&b, ",\"stack_min\":{");
    bool first = true;
    for (size_t i = 0; i < sizeof(k_stack_tasks) / sizeof(k_stack_tasks[0]); i++) {
        TaskHandle_t h = xTaskGetHandle(k_stack_tasks[i].task);
        if (h == NULL) continue;
        jadd(&b, "%s\"%s\":%u", first ? "" : ",", k_stack_tasks[i].key,
             (unsigned)uxTaskGetStackHighWaterMark(h));
        first = false;
    }
    jadd(&b, "},\"nodelay\":%u,\"wr_max\":%lu,\"dgram\":%lu",
             (unsigned)cfg->nodelay, (unsigned long)cfg->wr_max, (unsigned long)cfg->dgram);
    jadd(&b, wrap_key ? "}}" : "}");
}

void instr_start(void)
{
    esp_register_freertos_idle_hook_for_cpu(idle_hook_cpu0, 0);
#if NCORES > 1
    esp_register_freertos_idle_hook_for_cpu(idle_hook_cpu1, 1);
#endif
    esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, on_sta_disconnected, NULL);
    heap_caps_monitor_local_minimum_free_size_start();  /* the first heap_win_min window */
    xTaskCreate(instr_task, "instr", 3072, NULL, 1, NULL);
}
