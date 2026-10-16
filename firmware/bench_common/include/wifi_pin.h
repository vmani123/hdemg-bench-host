/* wifi_pin.h — station settings applied after the first join: stay on one access point of
 * a network with several (CONFIG_BENCH_WIFI_PIN_CHANNEL, and CONFIG_BENCH_WIFI_PIN_CHANNEL_5G
 * for a 5 GHz build: rig settings in sdkconfig.local), and advertise HE-MCS8/9
 * (CONFIG_BENCH_WIFI_HE_MCS9, a rung lever). */
#pragma once

/* band_pin: the build's band pin (2 = 2.4 GHz, 5 = 5 GHz, 0 = none). A pinned channel
 * on the other band is ignored, so a 5 GHz build is never sent hunting for channel 1. */
void bench_wifi_station_setup(int band_pin);
