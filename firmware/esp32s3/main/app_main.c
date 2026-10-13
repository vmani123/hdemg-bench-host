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

/* ---- station channel width (a rung lever) -----------------------------------------
 * CONFIG_BENCH_WIFI_BW_20 / _40 come from bench_common/Kconfig and are set by a rung.
 * The width is part of what the station offers when it associates, and the connect
 * helper starts and connects in one call, so it is applied from STA_START and then made
 * certain with one deliberate re-association after the first join. With neither set
 * (the default) nothing here is compiled and the driver default stands. */
#if defined(CONFIG_BENCH_WIFI_BW_20) || defined(CONFIG_BENCH_WIFI_BW_40)
#define BENCH_PHY_HOOK 1
#ifdef CONFIG_BENCH_WIFI_BW_40
#define BENCH_BW WIFI_BW_HT40
#else
#define BENCH_BW WIFI_BW_HT20
#endif

static void apply_phy(void)
{
    esp_err_t err = esp_wifi_set_bandwidth(WIFI_IF_STA, BENCH_BW);
    if (err != ESP_OK) ESP_LOGE(TAG, "esp_wifi_set_bandwidth: %s", esp_err_to_name(err));
}

static void on_sta_start_phy(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg; (void)base; (void)id; (void)data;
    apply_phy();
}

static void reassociate_with_phy(void)
{
    apply_phy();
    /* The connect helper's disconnect handler rejoins, now with the width in place. */
    esp_wifi_disconnect();
    vTaskDelay(pdMS_TO_TICKS(500));
    wifi_ap_record_t ap;
    /* Waits as long as it takes, like connect_forever(): staying reachable is the
     * board's one job. It says so, and nudges the join if the helper went quiet. */
    for (int tick = 1; esp_wifi_sta_get_ap_info(&ap) != ESP_OK; tick++) {
        if (tick % 40 == 0) {
            ESP_LOGW(TAG, "still rejoining after the width change (%d s)", tick / 4);
            esp_wifi_connect();
        }
        vTaskDelay(pdMS_TO_TICKS(250));
    }
    ESP_LOGI(TAG, "station width %d MHz requested (AP secondary channel: %d)",
             BENCH_BW == WIFI_BW_HT40 ? 40 : 20, (int)ap.second);
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
#ifdef BENCH_PHY_HOOK
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_START,
                                               on_sta_start_phy, NULL));
#endif
    connect_forever();
#ifdef BENCH_PHY_HOOK
    reassociate_with_phy();
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

    ESP_LOGI(TAG, "esp32s3 uplink up — ingress=%s rung=%s, awaiting control plane on :%d",
             INGRESS_NAME, RUNG_NAME, CONTROL_PORT);
}
