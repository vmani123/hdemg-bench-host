/* wifi_pin.c — keep the station on one access point of a network with several.
 *
 * House WiFi serves the same name from two 2.4 GHz radios (channels 1 and 6). After a
 * reflash the C5 joined either, and a step measured on the other radio compares two
 * access points, not two configurations. With CONFIG_BENCH_WIFI_PIN_CHANNEL set, the
 * board checks the channel it joined; if it is the wrong one it rescans starting from
 * the pinned channel (fast scan: the first match wins) and rejoins, and once there it
 * locks onto that access point's BSSID so a later rejoin cannot drift either. Like
 * connect_forever(), it waits as long as it takes, saying so. */
#include "wifi_pin.h"
#include "sdkconfig.h"
#include <string.h>
#include "esp_wifi.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "pin";

void bench_wifi_pin_channel(int band_pin)
{
#if CONFIG_BENCH_WIFI_PIN_CHANNEL > 0
    const uint8_t want = CONFIG_BENCH_WIFI_PIN_CHANNEL;
    if ((band_pin == 5 && want <= 14) || (band_pin == 2 && want > 14)) {
        ESP_LOGW(TAG, "pinned channel %u is not on this build's band; not pinning", want);
        return;
    }
    wifi_config_t cfg;
    if (esp_wifi_get_config(WIFI_IF_STA, &cfg) != ESP_OK) return;
    cfg.sta.channel = want;
    cfg.sta.scan_method = WIFI_FAST_SCAN;
    esp_wifi_set_config(WIFI_IF_STA, &cfg);

    wifi_ap_record_t ap;
    for (int tick = 0; ; tick++) {
        if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK && ap.primary == want) break;
        if (tick % 20 == 0) {
            /* The connect helper's disconnect handler rejoins with the config above. */
            ESP_LOGW(TAG, "not on channel %u; rejoining (%d s)", want, tick / 2);
            esp_wifi_disconnect();
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }
    if (esp_wifi_get_config(WIFI_IF_STA, &cfg) == ESP_OK) {
        memcpy(cfg.sta.bssid, ap.bssid, sizeof(cfg.sta.bssid));
        cfg.sta.bssid_set = true;
        esp_wifi_set_config(WIFI_IF_STA, &cfg);
    }
    ESP_LOGI(TAG, "on channel %u, AP %02x:%02x:%02x:%02x:%02x:%02x", want, ap.bssid[0],
             ap.bssid[1], ap.bssid[2], ap.bssid[3], ap.bssid[4], ap.bssid[5]);
#else
    (void)band_pin;
#endif
}
