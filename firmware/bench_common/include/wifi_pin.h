/* wifi_pin.h — keep the station on one access point of a network with several.
 * A rig setting (CONFIG_BENCH_WIFI_PIN_CHANNEL, set in sdkconfig.local), not a lever. */
#pragma once

/* band_pin: the build's band pin (2 = 2.4 GHz, 5 = 5 GHz, 0 = none). A pinned channel
 * on the other band is ignored, so a 5 GHz build is never sent hunting for channel 1. */
void bench_wifi_pin_channel(int band_pin);
