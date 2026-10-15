# First real sweep, 5 Oct 2026 (Verizon router, 2.4 GHz)

A snapshot of `host/results/` from the first real overnight run. It is kept here because
`host/results/` is gitignored. Plain-English reading of this data: Report 1 §5
(`../REPORT_1_Test_Code_and_Results_So_Far.md`).

- `runs.jsonl`: the ledger, one line per run (171 lines: 170 runs plus one `void` marker).
  The marker voids the 21 UDP runs, because Cisco Secure Client on the Mac dropped
  datagrams. Of the 170 runs, 79 are usable (all TCP) and 70 were refused by the 3 dB
  signal-drift gate (all S3).
- `runs-before-void-0557.jsonl`: the ledger as it stood before the 05:57 restart.
- `overnight-20261005-*.log`: the console output of the two overnight invocations.
- `report/`: the harness's generated report and charts. Its "knee" of 60 Mbit/s is
  misleading: the real behaviour is a flat plateau (TCP 5–8 Mbit/s at every load from 12 to
  60), set by the stock 5,760 B TCP send buffer.

Conditions: boards on 2.4 GHz channel 6, 20 MHz, 802.11n (the C5 did not get Wi-Fi 6 from
this router). The Mac was on the router's 5 GHz band (channel 104). The rig ceilings passed
to the harness (`2.4=60`, `5=95`) were placeholders, not measured.

Headline TCP medians (offered load 12–60 Mbit/s, 60 s runs):

| Cell | Median goodput | Valid runs |
|---|---|---|
| C5 baseline | 7.65 Mbit/s | 24 of 24 |
| C5 tuned (`r2-iram`) | 8.02 Mbit/s | 24 of 24 |
| S3 baseline | 5.17 Mbit/s | 24 of 43 |
| S3 tuned (`r2-core-split`) | 5.49 Mbit/s | 7 of 58 |

The simulated sweep of 29 Sep (`host/results/sim-report/`, fake boards) is not included;
it can be regenerated with `make sim`.

The 13 Oct tuning study, which fixed the TCP cap and re-ran UDP, is in
`../tuning-results/2026-10-13-house-wifi/`.
