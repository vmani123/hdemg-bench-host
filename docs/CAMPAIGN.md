# Follow-up campaign: run guide

The tests that follow the 13 Oct tuning study: Nagle and write size, PSRAM, 30-minute
soaks and stress, 40 MHz, the C5 at 5 GHz, and Stage 2 (data coming in over QSPI or SDIO
from the STM32, then out over Wi-Fi). One command runs them all, in order, unattended,
and resumes where it stopped.

- Engine: `host/bench/campaign.py`. Commands: `bench campaign`, `bench wiring`,
  `campaign.sh`.
- Rig settings: `host/campaign/followup.yaml`.
- Firmware ↔ host contract and the Stage 2 pin map: `docs/CAMPAIGN_PROTOCOL.md`.

## 1. The tests

`bash campaign.sh --list` prints them. In the order they run:

| block | ids | what | about |
|---|---|---|---|
| A | W.1, 0.1–0.3 | wiring hello world, rig check, boot check, reference | 25 min |
| A | 1.1–1.3 | Nagle on/off; 8,100 vs 540 B TCP writes; 1,350 vs 540 B UDP datagrams (saturation, 3 × 20 s) | 15 min |
| A | 2.1–2.8 | PSRAM: C5 P0–P3, S3 P1–P3, then 10 min on each chip's kept PSRAM setup | 50 min |
| A | 4.1–4.3 | 40 MHz at 2.4 GHz on both chips, plus UDP load sweeps | 1 h |
| A | 0.4, 5.2–5.6 | the C5 at 5 GHz (Mac on the 2.4 GHz network), plus the shared-channel case | 50 min |
| B | 3.0–3.7 | reference, 30-min soaks, TCP and UDP stress, 500 start/stop leak check | 4.5 h |
| C | 6.0–6.5 | Stage 2: STM32 bring-up, link check, built-in generator vs QSPI (S3, C5) vs SDIO (C5) | 1.5 h |

Total: about 9.5 h of board time.

Between A and B nothing waits for you. Block C needs the wiring check (W.1) to have passed.

### Rules for what later tests use

These are written down before any results exist. They are applied by code (`campaign.py`
docstring), not by judgement:

- **Kept PSRAM setup** (used by the soaks): the variant that raises the internal-RAM
  low-water by more than 4 KB without losing more than 2 % beyond the combined repeat
  spread on either transport. If none qualifies, the design stays.
- **Best setup per chip** (used by Stage 2): the highest UDP median among the chip's
  20 MHz and 40 MHz builds (and, for the C5, its 5 GHz builds). A 40 MHz build counts
  only if its link read `bw40 sta40`. A tie within the spread goes to the plain design.

### Where the plan had to give

- **S3 "A-MSDU alone" (2.7)** can't be built: in this IDF, A-MSDU transmit needs the Wi-Fi
  cache TX buffers, which exist only when Wi-Fi/lwIP memory goes to PSRAM. Test 2.7 is
  therefore P2 + A-MSDU. P2 versus P3 still separates the two halves of the 13 Oct bundle.
- **Firewall block (3.5)** needs passwordless `sudo pfctl`. Without it, that one event is
  recorded as skipped. The other five faults in the run still happen.
- **Offered-load caps** are the 40 MHz raw rate (150 Mbit/s), not a measured rig ceiling,
  as on 13 Oct.

## 2. Setup: what a person has to do

1. **Router (UniFi app), once.**
   - Add two networks on the same LAN, with the House WiFi password and client isolation
     off: `House WiFi 2G` (Advanced → WiFi Band: 2.4 GHz only) and `House WiFi 5G` (5 GHz
     only).
   - The 2.4 GHz access point the boards use (channel 1): set Channel 1 (not Auto) and
     Channel Width 40 MHz.
   - The other 2.4 GHz access point: move it to channel 11, or turn its 2.4 GHz radio off.
   - Leave 5 GHz on channel 48 at 80 MHz. Turn off Nightly Channel Optimization.
2. **Mac, once.**
   - Join both new networks once so macOS saves them, then turn off Auto-Join for plain
     House WiFi.
   - Keep it on the charger with the lid open.
   - Turn Bluetooth off; keep Cisco Secure Client, VPNs and content filters off.
3. **Bench.**
   - Place the S3, C5 and Nucleo where they will stay, at about the same distance from the
     access point.
   - Plug all three into the Mac.
   - Wire the 18 Stage 2 wires plus 4 grounds per `docs/CAMPAIGN_PROTOCOL.md` §2. One
     wire per pin, no resistors, nothing shared.
4. **Board network settings.** `firmware/esp32s3/sdkconfig.local` and
   `firmware/esp32c5/sdkconfig.local` must join `House WiFi`, with
   `CONFIG_BENCH_WIFI_PIN_CHANNEL=1` and `CONFIG_BENCH_WIFI_PIN_CHANNEL_5G=48`. These files
   are gitignored; the template is `firmware/sdkconfig.local.example`.
5. **Runner.** In its own Terminal, from this checkout:
   `FLASH_WINDOW_MIN=5760 bash hostrun.sh`, then type `yes` once. The window is 4 days and
   covers every flash.

## 3. Running

```bash
bash campaign.sh --sim                 # rehearsal (fake boards), a few minutes, no hardware
bash campaign.sh --only W.1,0.2        # wiring hello world + boot check, ~15 min
bash campaign.sh                       # everything not done yet
bash campaign.sh --status              # host/results/campaign/followup/summary.md
```

- **The wiring hello world** puts both ESPs on synth builds, flashes the STM32 if needed,
  and drives each of the 18 wires from each end in turn. It prints a table with a verdict
  per wire: `ok`, `open`, `swapped with #n`, `shorted to #n`, `stuck high` or `one-way`.
  The boot check then resets each ESP with the harness attached, and confirms it can
  still be flashed and boots normally (C5 strapping pins GPIO2 and GPIO7 are on wires 10
  and 11).
- **Resuming.** Running again skips finished tests, and a half-finished test continues
  from its ledger.
  - `--redo 1.1,1.2` measures done tests again, with new repeats; the old runs stay in the
    ledger.
  - `--from 3.0 --to 3.7` runs one block.
- **What stops it.** Only things a person must fix stop the campaign: hostrun.sh not
  answering (or waiting at its flash prompt), the Mac unable to join a network, or a board
  locked by another session. The summary says which. A failed test is retried once, then
  recorded, and the campaign moves on.

## 4. Supervising with Claude

The campaign runs as a background process (`bash campaign.sh > /dev/null 2>&1 &`). A
`/loop` can then watch it: `/loop Supervise the follow-up campaign per docs/CAMPAIGN.md §4`.

Each tick:

1. **Is it alive?** `pgrep -fl "cli.bench campaign"`. Read the tail of the newest
   `host/results/campaign/logs/campaign-*.log`, and `summary.md`.
   - Stopped with `STOPPED at …`: the reason is in the summary. A person must fix it
     (hostrun, network, lock). Say so, and send a push notification if you can.
   - Exited because everything is done: write the findings (below) and stop the loop.
   - Died mid-test with nothing in the summary: relaunch it the same way. Resume is
     safe.
2. **Read the newest finished test** in `state.json` and check:
   - **Invalid runs** in its scores (`why_invalid`): the gate failures say why.
     `host discarded N datagrams` means a content filter is on (user action).
     `this host is on band X` means the Mac is on the wrong band.
   - **40 MHz tests** with `in_effect_40: false`: the router is at 20 MHz; tell the user.
   - **W.1 not ok**: Stage 2 will be skipped. The wire table says what to fix. Tell the
     user while the wireless tests carry on.
   - **Soak telemetry:** report `resets > 0`, `heap_low_B` under 20000,
     `heap_slope_B_per_h` clearly negative, or `wifi_disc_delta > 0` without a link
     drop.
3. **Report** in two or three lines: tests done/left, anything flagged. Schedule the next
   tick: ~20 min in block A, ~30 min during soaks.

**At the end:** a short findings section per group goes into `summary.md`'s companion
`docs/REPORT_3_Followup_Campaign.md`, in the same style as Reports 1 and 2.

## 5. Outputs

All of these are under `host/results/campaign/`:

| path | what |
|---|---|
| `followup.jsonl` | the ledger: one line per run, with telemetry summary, stress log, device_cfg |
| `followup/state.json` | every test's status, result and the derived choices (kept PSRAM, best setups, link clocks) |
| `followup/summary.md` | the same, readable |
| `followup/ts/<test>/<run>.json` | per run: 10 s receiver windows, 1 Hz device samples, injected faults |
| `followup/matrices/<test>.yaml` | exactly what each test measured |
| `logs/campaign-*.log` | console output of each invocation |
