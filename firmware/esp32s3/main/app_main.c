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

static const char *TAG = "app";

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    /* Station mode. The access point is the iPhone hotspot on the bench and a real AP
     * in the field; either way the ESP is a station, which is the mode that gives the
     * C5 access to 5 GHz (SoftAP does not). */
    ESP_ERROR_CHECK(example_connect());

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
