/* linkdrop.c — `linkdrop <ms>`: Wi-Fi off for ms, then back onto the same access point.
 * See linkdrop.h.
 *
 * esp_wifi_stop(), not esp_wifi_disconnect(). After a plain disconnect the connect
 * helper's handler (protocol_examples_common) rejoins at once, so the link would be down
 * for a few hundred milliseconds whatever was asked for. Stopped, the radio is off: that
 * handler still runs on the disconnect the stop causes, but esp_wifi_connect() then
 * returns ESP_ERR_WIFI_NOT_STARTED, which it accepts and ignores.
 *
 * What has to survive the stop, and how it does:
 *   - band pin (C5) and channel width (a rung): the target's own STA_START handlers
 *     re-apply them, exactly as at boot. This task waits for STA_START to have been
 *     handled before it connects (its handler is registered after the target's, and
 *     handlers for one event run in registration order).
 *   - the station config, including the access-point (BSSID) lock wifi_pin.c sets and
 *     HE-MCS8/9: kept in the driver's RAM across stop/start. Checked rather than
 *     assumed, and restored from a snapshot if it did not survive.
 *   - power save off and TX power at the maximum: set again once associated, as
 *     app_main does after the first join.
 *
 * Like connect_forever(), the rejoin waits as long as it takes and says so: staying
 * reachable is the board's one job. */
#include "linkdrop.h"
#include "instr.h"
#include <string.h>
#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"

static const char *TAG = "linkdrop";

#define EV_STA_START BIT0

static EventGroupHandle_t    s_ev;
static volatile TaskHandle_t s_task;           /* non-NULL while a drop is armed or running */
static uint32_t              s_ms;
static wifi_config_t         s_before, s_after; /* static: one drop at a time */

static void on_sta_start(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg; (void)base; (void)id; (void)data;
    if (s_ev) xEventGroupSetBits(s_ev, EV_STA_START);
}

uint32_t linkdrop_clamp_ms(uint32_t ms)
{
    if (ms < LINKDROP_MIN_MS) return LINKDROP_MIN_MS;
    if (ms > LINKDROP_MAX_MS) return LINKDROP_MAX_MS;
    return ms;
}

static void linkdrop_task(void *arg)
{
    (void)arg;
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);          /* released once the reply is sent */
    /* sendto() returned when lwIP queued the reply, not when it was on the air. */
    vTaskDelay(pdMS_TO_TICKS(100));

    bool have = esp_wifi_get_config(WIFI_IF_STA, &s_before) == ESP_OK;
    xEventGroupClearBits(s_ev, EV_STA_START);
    ESP_LOGW(TAG, "Wi-Fi off for %lu ms", (unsigned long)s_ms);
    esp_err_t err = esp_wifi_stop();
    if (err != ESP_OK) ESP_LOGE(TAG, "esp_wifi_stop: %s", esp_err_to_name(err));
    vTaskDelay(pdMS_TO_TICKS(s_ms));

    err = esp_wifi_start();
    if (err != ESP_OK) ESP_LOGE(TAG, "esp_wifi_start: %s", esp_err_to_name(err));
    if (!(xEventGroupWaitBits(s_ev, EV_STA_START, pdFALSE, pdTRUE, pdMS_TO_TICKS(2000))
          & EV_STA_START)) {
        ESP_LOGW(TAG, "no STA_START within 2 s; connecting anyway");
    }

    if (have && esp_wifi_get_config(WIFI_IF_STA, &s_after) == ESP_OK &&
        memcmp(&s_after.sta, &s_before.sta, sizeof(s_after.sta)) != 0) {
        ESP_LOGW(TAG, "station config changed across stop/start (BSSID lock %d -> %d); "
                      "restoring it", s_before.sta.bssid_set, s_after.sta.bssid_set);
        esp_wifi_set_config(WIFI_IF_STA, &s_before);
    }

    esp_wifi_connect();
    wifi_ap_record_t ap;
    for (int tick = 1; esp_wifi_sta_get_ap_info(&ap) != ESP_OK; tick++) {
        if (tick % 40 == 0) {                          /* every ~10 s, for ever */
            ESP_LOGW(TAG, "still rejoining after the link drop (%d s)", tick / 4);
            if (esp_wifi_connect() == ESP_ERR_WIFI_NOT_STARTED) {
                esp_wifi_start();                      /* the start above had failed */
                xEventGroupWaitBits(s_ev, EV_STA_START, pdFALSE, pdTRUE, pdMS_TO_TICKS(2000));
                esp_wifi_connect();
            }
        }
        vTaskDelay(pdMS_TO_TICKS(250));
    }
    /* Held constant across every run and both chips (app_main): environment, not tuning. */
    esp_wifi_set_ps(WIFI_PS_NONE);
    esp_wifi_set_max_tx_power(80);
    ESP_LOGI(TAG, "rejoined: channel %u, AP %02x:%02x:%02x:%02x:%02x:%02x", ap.primary,
             ap.bssid[0], ap.bssid[1], ap.bssid[2], ap.bssid[3], ap.bssid[4], ap.bssid[5]);

    s_task = NULL;
    vTaskDelete(NULL);
}

const char *linkdrop_arm(uint32_t ms)
{
    if (s_ev == NULL) return "linkdrop not initialised";
    if (s_task != NULL) return "a linkdrop is already in progress";
    s_ms = linkdrop_clamp_ms(ms);
    TaskHandle_t t = NULL;
    if (xTaskCreate(linkdrop_task, "linkdrop", 4096, NULL, 5, &t) != pdPASS) {
        return "no memory for the linkdrop task";
    }
    s_task = t;
    instr_note_linkdrop();
    return NULL;
}

void linkdrop_fire(void)
{
    TaskHandle_t t = s_task;
    if (t) xTaskNotifyGive(t);
}

void linkdrop_init(void)
{
    s_ev = xEventGroupCreate();
    esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_START, on_sta_start, NULL);
}
