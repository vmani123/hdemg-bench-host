# S3 vs C5 — measured results

Records: **170** · rig ceiling by band: {'2.4': 60.0} · observed PHY: HT ch6 bw20

> A number above the rig ceiling is a property of the rig, not the silicon.
> If the PHY column never reads HE, the C5 was not exercised at Wi-Fi 6 rates
> and its figure is a lower bound (plan §7.3b).

## Matrix

| Cell | Variant | Transport | Knee (Mbit/s) | Goodput (Mbit/s) | Spread (MAD) | Loss @ knee | Repeats |
|---|---|---|---|---|---|---|---|
| C5 · 2.4 GHz | baseline | tcp | 60.0 | 8.00 | ±0.17 | 0.000 % | 3 |
| C5 · 2.4 GHz | tuned | tcp | 60.0 | 7.91 | ±0.28 | 0.000 % | 3 |
| S3 · 2.4 GHz | baseline | tcp | 60.0 | 5.27 | ±0.33 | 0.000 % | 3 |
| S3 · 2.4 GHz | tuned | tcp | 60.0 | 5.59 | ±0.00 | 0.000 % | 1 |

## Decomposition (plan §5.1)

- **cpu_idle_pct_at_knee**: {}
