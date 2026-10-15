# Documentation index

Read in this order if you are trying to understand the project as a person.

## 1. What the project is and how the code works
- `REPORT_1_Test_Code_and_Results_So_Far.md`: how the test code is built, how one
  measurement is made, and what has been run. §5 covers both real sessions (5 Oct and
  13 Oct).
- `BENCH_HARNESS_SPEC.md`: the design rationale (shared measuring core, per-chip tuning
  ladders, validity gates).
- `S3_vs_C5_Test_Plan_v2.md`: the question being answered, the throughput tiers
  (T0 5.0, T1 20.5, **T2 41.3**, T3 61.4 Mbit/s) and the decision rule.

## 2. How Wi-Fi tuning works, and what the experiments found
- `REPORT_2_ESP32_WiFi_Tuning.md`: the tuning levers in plain English, Espressif's published
  numbers, the step-by-step climb tables for both chips, what limits each chip now, and what
  is left to do.
- `TUNING_STUDY.md`: the run guide for the automated tuning loop (`tune.sh`, `bench tune`):
  the keep rule, safety checks, how to re-run.

## 3. The experimental data
| Folder | What |
|---|---|
| `results-2026-10-05-verizon/` | First real sweep: TCP only, 2.4 GHz, Verizon router. UDP voided. |
| `tuning-results/2026-10-13-house-wifi/` | Tuning study on both chips (144 runs), Espressif `iperf` references, latency at the HD-EMG rate. Start with its `README.md` and `max-tput/summary.md`. |

## Where things stand (13 Oct)
Both chips pass T2 (41.3 Mbit/s) over UDP at 2.4 GHz and 20 MHz in short tuning runs: S3
44.6, C5 54.0 Mbit/s, 0 % loss. TCP is about 31 Mbit/s on both. Qualifying 60 s runs, a
memory soak test, 40 MHz and 5 GHz are still to do (Report 2 §7).

## Not in this tree
- Stage 2 (STM32H745 signal generator and wired input): branch `worktree-stm32-stage2`,
  not merged. It has never run.
