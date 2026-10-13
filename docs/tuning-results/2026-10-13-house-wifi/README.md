# Max-throughput study, 13 Oct 2026 (House WiFi, 2.4 GHz)

A snapshot of `host/results/tuning/` from the night's run, kept here because results are
gitignored. The write-up is in the study report doc.

- `max-tput/summary.md`: per-step table with decisions; `state.json`: the engine's state;
  `logs/` and `matrices/`: one per step.
- `max-tput.jsonl`: every run (goodput, loss, relative latency, RSSI, link, CPU idle).
- `latency/`: the latency check at 4.32 Mbit/s. It failed when both boards dropped off the
  network at about 03:45 PDT; one degraded S3 run and one C5 run on the channel-6 AP.
- `archive/20261013-0226-boards-far-from-ap/`: the set-aside first attempt.

Headline: S3 21.3 Mbit/s TCP / 45.2 Mbit/s UDP (UDP build); C5 19.0 TCP / 34.8 UDP
(provisional; climb blocked by access-point switching). Rig: UniFi 2.4 GHz channel 1,
20 MHz; Mac on 5 GHz; boards where they sat (S3 -55 dBm, C5 -66 to -75 dBm).
