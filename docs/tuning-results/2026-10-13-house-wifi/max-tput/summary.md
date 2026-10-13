# Tuning study: max-tput

_updated 2026-10-13T10:39:54Z_

## esp32s3 @ 2.4 GHz

- current best configuration: **t24-03-wifi-tx-bufs** (done — 3 consecutive candidates gained under 2 % (stop rule))
- TCP: **21.3 Mbit/s**
- UDP: **26.2 Mbit/s**, loss 0.15%

| step | rung | lever | parent | offered | TCP Mbit/s | UDP Mbit/s (loss) | gain % | decision | link | reason |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | r2-core-split | — | — | 60 | 3.9 ±0.2 | 23.0 ±0.3 (0.00%) | — | start | HT ch1 bw20 sta40 | starting point |
| 1 | t24-01-tcp-window | lwip_window | r2-core-split | 60 | 20.3 ±0.3 | 23.6 ±0.0 (0.00%) | tcp +416.1, udp +2.6 | kept | HT ch1 bw20 sta40 | gained: tcp +416.1%, udp +2.6% |
| 2 | t24-02-tx-ba-window | ampdu_ba_win | t24-01-tcp-window | 60 | 21.0 ±0.3 | 23.4 ±0.1 (0.04%) | tcp +3.4, udp -0.9 | kept | HT ch1 bw20 sta40 | gained: tcp +3.4% |
| 3 | t24-03-wifi-tx-bufs | wifi_buffers | t24-02-tx-ba-window | 60 | 21.3 ±0.1 | 26.2 ±0.3 (0.15%) | tcp +1.5, udp +12.3 | kept | HT ch1 bw20 sta40 | gained: udp +12.3% |
| 4 | t24-04-wifi-rx-bufs | wifi_buffers | t24-03-wifi-tx-bufs | 60 | 20.1 ±0.2 | 28.0 ±0.1 (0.28%) | tcp -5.4, udp +6.8 | rejected | HT ch1 bw20 sta40 | regressed: tcp -5.4% |
| 5 | t24-05-pipe-pool | frame_size | t24-03-wifi-tx-bufs | 60 | 21.0 ±0.3 | 26.4 ±0.3 (0.23%) | tcp -1.4, udp +0.5 | rejected | HT ch1 bw20 sta40 | no transport gained 2 % |
| 6 | t24-06-core-lock | core_lock | t24-03-wifi-tx-bufs | 60 | 17.0 ±0.4 | 45.2 ±0.4 (4.07%) | tcp -19.9, udp +72.4 | rejected | HT ch1 bw20 sta40 | regressed: tcp -19.9% |

## esp32c5 @ 2.4 GHz

- current best configuration: **t24-01-tcp-window** (BLOCKED: 2 candidates in a row produced no usable measurement — likely the rig, not the levers (see their logs). Fix it and re-run; they will be retried.)
- TCP: **19.0 Mbit/s**
- UDP: **34.8 Mbit/s**, loss 1.67%

| step | rung | lever | parent | offered | TCP Mbit/s | UDP Mbit/s (loss) | gain % | decision | link | reason |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | r2-iram | — | — | 60 | 4.5 ±0.0 | 29.7 ±1.5 (0.44%) | — | start | HE ch1 bw20 sta20 | starting point |
| 1 | t24-01-tcp-window | lwip_window | r2-iram | 60 | 19.0 ±1.4 | 34.8 ±0.6 (1.67%) | tcp +322.1, udp +17.3 | kept | HE ch1 bw20 sta20 | gained: tcp +322.1%, udp +17.3% |
| 2 | t24-02-tx-ba-window | ampdu_ba_win | t24-01-tcp-window | 60 | 2.1 ±2.0 | 3.3 ±0.1 (1.39%) | tcp -88.8, udp -90.7 | unmeasured | HE ch6 bw20 sta20 | reopened: C5 was on the distant House WiFi AP (HE ch6, -85..-91 dBm) instead of the ch1 AP (-66..-75) for all 4 runs; measured the AP, not the lever (was rejected: regressed: tcp -88.8%, udp -90.7%) |
| 3 | t24-03-tx-ba-window | ampdu_ba_win | t24-01-tcp-window | 60 | 16.9 ±0.1 | 25.7 ±0.6 (0.82%) | tcp -11.3, udp -26.3 | rejected | HE ch1 bw20 sta20 | regressed: tcp -11.3%, udp -26.3% |
| 4 | t24-04-core-lock | core_lock | t24-01-tcp-window | 60 | 9.7 ±0.0 | 16.0 ±0.4 (0.00%) | — | unmeasured | HE ch6 bw20 sta20 | the board was on ch6, the current best on ch1: a different access point or channel, not comparable |
| 5 | t24-05-wifi-tx-bufs | wifi_buffers | t24-01-tcp-window | 60 | 9.9 ±0.7 | 16.4 ±0.2 (0.00%) | — | unmeasured | HE ch6 bw20 sta20 | the board was on ch6, the current best on ch1: a different access point or channel, not comparable |

