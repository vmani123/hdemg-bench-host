/* wifi_pin.c — station settings applied after the first join, with one deliberate rejoin.
 *
 * Access point pin (CONFIG_BENCH_WIFI_PIN_CHANNEL, a rig setting). House WiFi serves the
 * same name from two 2.4 GHz radios (channels 1 and 6). After a reflash the C5 joined
 * either, and a step measured on the other radio compares two access points, not two
 * configurations. With the pin set, a board on the wrong channel rescans starting from
 * the pinned one (fast scan: the first match wins) and rejoins; once there it locks onto
 * that access point's BSSID so a later rejoin cannot drift either. A build band-pinned to
 * 5 GHz takes its pin from CONFIG_BENCH_WIFI_PIN_CHANNEL_5G instead: one sdkconfig.local
 * serves both bands' builds, and each band has its own access point to stay on. The lock
 * lives in the driver's station config, so it also holds across a `linkdrop`.
 *
 * HE-MCS8/9 (CONFIG_BENCH_WIFI_HE_MCS9, a rung lever, 802.11ax chips). ESP-IDF leaves
 * he_mcs9_enabled at 0, which caps an HE20 link at MCS7 (86 Mbit/s). It is a capability
 * advertised at association, so setting it forces one rejoin.
 *
 * Like connect_forever(), it waits as long as it takes, saying so. */
#include "wifi_pin.h"
#include "sdkconfig.h"
#include <stdbool.h>
#include <string.h>
#include "esp_wifi.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "sta";

void bench_wifi_station_setup(int band_pin)
{
    wifi_config_t cfg;
    if (esp_wifi_get_config(WIFI_IF_STA, &cfg) != ESP_OK) return;
    bool rejoin = false;

#ifdef CONFIG_BENCH_WIFI_HE_MCS9
    if (!cfg.sta.he_mcs9_enabled) {
        cfg.sta.he_mcs9_enabled = 1;
        rejoin = true;                         /* advertised at association */
        ESP_LOGI(TAG, "HE-MCS8/9 enabled");
    }
#endif

    uint8_t want = (band_pin == 5) ? CONFIG_BENCH_WIFI_PIN_CHANNEL_5G
                                   : CONFIG_BENCH_WIFI_PIN_CHANNEL;
    if (want && ((band_pin == 5 && want <= 14) || (band_pin == 2 && want > 14))) {
        ESP_LOGW(TAG, "pinned channel %u is not on this build's band; not pinning", want);
        want = 0;
    } else if (want) {
        cfg.sta.channel = want;
        cfg.sta.scan_method = WIFI_FAST_SCAN;
    }
    if (!rejoin && !want) return;
    esp_wifi_set_config(WIFI_IF_STA, &cfg);
    if (rejoin) {
        /* The connect helper's disconnect handler rejoins with the config above. */
        esp_wifi_disconnect();
        vTaskDelay(pdMS_TO_TICKS(500));
    }

    wifi_ap_record_t ap;
    for (int tick = 1; ; tick++) {
        bool up = esp_wifi_sta_get_ap_info(&ap) == ESP_OK;
        if (up && (!want || ap.primary == want)) break;
        if (tick % 20 == 0) {
            ESP_LOGW(TAG, "%s; rejoining (%d s)", up ? "on the wrong channel" : "not joined",
                     tick / 2);
            if (up) esp_wifi_disconnect();
            else esp_wifi_connect();
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }
    if (want && esp_wifi_get_config(WIFI_IF_STA, &cfg) == ESP_OK) {
        memcpy(cfg.sta.bssid, ap.bssid, sizeof(cfg.sta.bssid));
        cfg.sta.bssid_set = true;
        esp_wifi_set_config(WIFI_IF_STA, &cfg);
    }
    ESP_LOGI(TAG, "joined: channel %u, AP %02x:%02x:%02x:%02x:%02x:%02x", ap.primary,
             ap.bssid[0], ap.bssid[1], ap.bssid[2], ap.bssid[3], ap.bssid[4], ap.bssid[5]);
}
