# Max-throughput study, 13 Oct 2026 (House WiFi, 2.4 GHz)

A snapshot of `host/results/tuning/` from both sessions on 13 Oct, kept here because results
are gitignored. The write-up is in the study report doc ("ESP32-S3 vs ESP32-C5
Max-Throughput Tuning Study").

- `max-tput/summary.md`: per-step table with decisions and heap low-water;
  `state.json`: the engine's state; `logs/` and `matrices/`: one per step.
- `max-tput.jsonl`: every run (goodput, loss, relative latency, RSSI, link + BSSID, CPU
  idle, heap free and low-water).
- `iperf/`: Espressif's iperf example on each chip against the Mac, same access point,
  05:24-05:33 PDT. UDP "loss" in these files is iperf's client-side count of datagrams it
  could not hand to its own stack, not air loss.
- `latency/`: the HD-EMG operating point (4.32 Mbit/s) on the final designs, 14:45 PDT, plus
  `rtt.json` (control-plane round trips, boards idle). `latency/night1/` is the failed
  03:40 attempt (boards dropped off at ~03:45).
- `tune-logs/`: the engine's console output, one file per invocation.
- `archive/20261013-0226-boards-far-from-ap/`: the set-aside first attempt.

Sessions: 02:58-03:40 PDT (night 1, stop rule 3 misses) and 05:03-06:21 PDT (session 2:
boards pinned to the channel-1 BSSID, 20 KB heap floor, each climb re-measured its current
best first, candidate lists run to the end).

Final designs (60 Mbit/s offered, medians of 2 runs, 0% loss):

| Chip | Rung | TCP Mbit/s | UDP Mbit/s | Heap low-water |
|---|---|---|---|---|
| ESP32-S3 | `t24-08-extra-iram` | 31.6 | 44.6 | 20.8 KB |
| ESP32-C5 | `t24-16-extra-iram` | 31.3 | 54.0 | 31.0 KB |

Espressif iperf on the same link: S3 TCP 33.0 / UDP 44.5; C5 TCP 28.8 / UDP 48.5.
At 4.32 Mbit/s both designs delivered every frame (TCP and UDP, 2 repeats each); relative
one-way latency p50 0.3-0.8 ms, p99 14-21 ms; idle round trip 4.5 ms min, 5.6-6.0 ms median.
Open risk: during the latency runs the lifetime heap low-water fell to 1.1 KB (C5) and
5.1 KB (S3), well under the floor the climbs measured. See the report.

Rig: UniFi 2.4 GHz channel 1, 20 MHz (BSSID ...:b2:c0:fe); Mac on 5 GHz channel 48; boards
where they sat (S3 -53 to -60 dBm, C5 -56 to -67 dBm in session 2).
