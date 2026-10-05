/* app_main.c — entry point (target-specific: this file is NOT parity-checked).
 *
 * Brings up Wi-Fi as a STATION, starts instrumentation, the control plane and the pipe,
 * then does nothing else. Everything that varies between runs — rate, transport,
 * destination, duration, frame size — arrives over the control plane at runtime, which
 * is what keeps the flash count in single figures across a ~500-run matrix.
 */
#include "nvs_flash.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_log.h"
#include "protocol_examples_common.h"
#include "pipe.h"
#include "control.h"
#include "instr.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "app";

/* ---- band pin --------------------------------------------------------------------
 * The C5 is dual-band, and a dual-band access point (one SSID on 2.4 and 5 GHz) would
 * let it pick whichever it likes. A cell is defined by its band, so the build pins the
 * radio to it: BENCH_BAND_PIN is 2 (2.4 GHz only), 5 (5 GHz only) or 0 (no pin), set by
 * -DBENCH_BAND=2.4|5|auto. The harness still verifies the band the board reports.
 *
 * esp_wifi_set_band_mode() needs the driver started, and the connect helper starts and
 * connects in one call, so the pin is applied from the STA_START event — that runs in
 * the event task, ahead of the helper's first esp_wifi_connect() — and enforced again
 * after the first association in case it joined the other band anyway. */
#ifndef BENCH_BAND_PIN
#define BENCH_BAND_PIN 0
#endif

#if BENCH_BAND_PIN
#define PINNED_MODE ((BENCH_BAND_PIN == 5) ? WIFI_BAND_MODE_5G_ONLY : WIFI_BAND_MODE_2G_ONLY)
#define PINNED_NAME ((BENCH_BAND_PIN == 5) ? "5" : "2.4")

static bool on_pinned_band(void)
{
    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) != ESP_OK) return false;
    return (ap.primary > 14) == (BENCH_BAND_PIN == 5);
}

static void on_sta_start(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg; (void)base; (void)id; (void)data;
    esp_err_t err = esp_wifi_set_band_mode(PINNED_MODE);
    if (err != ESP_OK) ESP_LOGW(TAG, "band pin at start: %s", esp_err_to_name(err));
}

static void enforce_band(void)
{
    esp_err_t err = esp_wifi_set_band_mode(PINNED_MODE);
    if (err != ESP_OK) ESP_LOGE(TAG, "esp_wifi_set_band_mode: %s", esp_err_to_name(err));
    for (int tick = 0; !on_pinned_band(); tick++) {
        if (tick % 20 == 0) {
            /* Drop the link; the connect helper's own disconnect handler re-associates,
             * and with the mode pinned it can only choose the right band. */
            ESP_LOGW(TAG, "not on the pinned %s GHz band; rejoining", PINNED_NAME);
            esp_wifi_disconnect();
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }
    ESP_LOGI(TAG, "band pinned to %s GHz", PINNED_NAME);
}
#endif

static void connect_forever(void)
{
    for (int attempt = 1; ; attempt++) {
        esp_err_t err = example_connect();
        if (err == ESP_OK) {
            if (attempt > 1) ESP_LOGW(TAG, "joined after %d attempts", attempt);
            return;
        }
        ESP_LOGW(TAG, "Wi-Fi join failed (%s) on attempt %d; retrying in 5 s",
                 esp_err_to_name(err), attempt);
        ESP_LOGW(TAG, "  disconnect reason 15 = WPA2 4-way handshake timeout, which "
                      "nearly always means a wrong password");
        ESP_LOGW(TAG, "  reason 201 = AP not found: wrong SSID, or the hotspot is on a "
                      "band this chip cannot use");
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    /* Station mode. The access point is the iPhone hotspot on the bench and a real AP
     * in the field; either way the ESP is a station, which is the mode that gives the
     * C5 access to 5 GHz (SoftAP does not).
     *
     * Retry forever rather than ESP_ERROR_CHECK. example_connect() gives up after its own
     * retry budget and returns ESP_FAIL; aborting on that reboots the board, which for a
     * bench instrument is the wrong failure mode — a hotspot hiccup at 3 a.m. would drop
     * the DHCP lease and leave the orchestrator talking to an address that no longer
     * exists, taking out the rest of the sweep. The device's one job is to stay
     * reachable. Re-associations are recorded host-side, so a retry is visible in the
     * ledger rather than silent. */
#if BENCH_BAND_PIN
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_START,
                                               on_sta_start, NULL));
#endif
    connect_forever();
#if BENCH_BAND_PIN
    enforce_band();
#endif

    /* Held constant across every run and both chips: power save off, TX power at the
     * chip maximum. These are environment, not tuning. */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    esp_wifi_set_max_tx_power(80);

    pipe_set_connected(1);
    instr_start();
    control_start();
    pipe_init();
    pipe_run();

    ESP_LOGI(TAG, "esp32c5 uplink up — ingress=%s rung=%s, awaiting control plane on :%d",
             INGRESS_NAME, RUNG_NAME, CONTROL_PORT);
}
