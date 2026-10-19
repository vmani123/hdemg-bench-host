### Stall zone (V.1a–c): TCP, 20 s runs, shuffled order

| chip | setting | offered | n | median Mbit/s | min–max | stalled | locked up after | heap low B (median) |
|---|---|---|---|---|---|---|---|---|
| C5 | batch=30 | 10 | 4 | 10.00 | 10.0–10.0 | 0/4 | 0/4 | 26608 |
| C5 | batch=30 | 15 | 4 | 15.00 | 15.0–15.0 | 0/4 | 0/4 | 27216 |
| C5 | batch=30 | 20.5 | 4 | 20.50 | 20.5–20.5 | 0/4 | 0/4 | 25284 |
| C5 | batch=30 | 25 | 4 | 25.00 | 25.0–25.0 | 0/4 | 0/4 | 15978 |
| C5 | batch=30 | 30 | 4 | 29.60 | 25.5–30.8 | 0/4 | 0/4 | 24912 |
| C5 | default | 10 | 4 | 3.58 | 0.0–10.0 | 3/4 | 3/4 | 5710 |
| C5 | default | 15 | 4 | 0.57 | 0.0–2.4 | 4/4 | 4/4 | 3656 |
| C5 | default | 20.5 | 4 | 0.00 | 0.0–6.4 | 4/4 | 4/4 | 4734 |
| C5 | default | 25 | 4 | 9.38 | 0.0–25.3 | 3/4 | 3/4 | 4274 |
| C5 | default | 30 | 4 | 29.73 | 28.1–30.4 | 0/4 | 0/4 | 5702 |

Per round (median Mbit/s, all loads pooled per setting):

- V.1a C5 default: median share of offered 0.14, stalled 8/10, locked up 8/10
- V.1a C5 batch=30: median share of offered 1.00, stalled 0/10, locked up 0/10
- V.1b C5 default: median share of offered 0.16, stalled 4/5, locked up 4/5
- V.1b C5 batch=30: median share of offered 1.00, stalled 0/5, locked up 0/5
- V.1c C5 default: median share of offered 0.99, stalled 2/5, locked up 2/5
- V.1c C5 batch=30: median share of offered 1.00, stalled 0/5, locked up 0/5

### Long runs (V.2–V.6)

| test | chip | setting | offered | min | Mbit/s | windows ≥ 90 % | first window < 50 % | longest gap s | connections | watchdog revivals | heap low B | Wi-Fi drops | resets |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V.2 | C5 | l15m | 15 | 15 | 0.44 | 0/90 | 10 | 127.7 | 7 | 6 | 2280 | 0 | 4 |
| V.2b | C5 | l15m-b30 | 15 | 15 | 15.00 | 90/90 | — | 0.2 | 1 | 0 | 19392 | 0 | 0 |
| V.3 | C5 | soak-b0 | 4.32 | 30 | 4.32 | 64/180 | 220 | 1150.7 | 2 | 12 | 5068 | 0 | 0 |
| V.4 | C5 | soak-b30 | 4.32 | 30 | 4.32 | 175/180 | — | 2.3 | 1 | 0 | 22984 | 0 | 0 |
| V.5 | C5 | full20m | 60 | 20 | 31.18 | 0/120 | 280 | 1.2 | 1 | 0 | 19412 | 0 | 0 |
| V.6 | C5 | stresstcp | 4.32 | 30 | 2.48 | 94/180 | 20 | 111.3 | 9 | 7 | 2892 | 1 | 7 |

### Write sizes (V.7): TCP at 60 offered, 20 s runs

| chip | write | n | median Mbit/s | min–max | locked up after |
|---|---|---|---|---|---|
| C5 | wr-1350 | 3 (+0 not run) | 26.91 | 26.9–27.1 | 0/3 |
| C5 | wr-2700 | 3 (+0 not run) | 28.81 | 27.1–29.6 | 0/3 |
| C5 | wr-8100 | 3 (+0 not run) | 27.92 | 25.3–30.3 | 0/3 |

Note: V.5 offered 60 Mbit/s, more than the C5 carries, so its "windows >= 90 %" and "first window < 50 %" columns (relative to the offered rate) do not apply; Report 4 section 6 gives its 10 s window spread instead. V.5 has no receiver windows (the drift fit outran the join); this table rebuilds them from the 1 s series.
