# Tuning study: max-tput

_updated 2026-10-13T09:32:32Z_

## esp32s3 @ 2.4 GHz

- current best configuration: **r2-core-split** (climbing)
- TCP: **3.0 Mbit/s**
- UDP: **24.3 Mbit/s**, loss 0.06%

| step | rung | lever | parent | offered | TCP Mbit/s | UDP Mbit/s (loss) | gain % | decision | link | reason |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | r2-core-split | — | — | 60 | 3.0 ±0.7 | 24.3 ±1.3 (0.06%) | — | start | HT ch1 bw20 sta40 | starting point |
| 1 | t24-01-tcp-window | lwip_window | r2-core-split | 60 | 1.8 ±0.3 | 25.2 ±0.9 (2.50%) | tcp -41.2, udp +3.6 | rejected | HT ch1 bw20 sta40 | regressed: tcp -41.2% |

