"""Report rendering (spec §5). Reads the ledger and nothing else.

Every figure carries the rig ceiling and the observed PHY mode, so a hotspot-limited
number can never be mistaken for a silicon ceiling.
"""
from __future__ import annotations
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402

from .ledger import Ledger               # noqa: E402
from .orchestrator import knee           # noqa: E402
from .palette import (DARK, ENTITY_LABEL, ENTITY_ORDER, LIGHT,   # noqa: E402
                      color_for, entity)

FRAME_BITS = 270 * 8
TIERS = [("T0", 5.0, "2 kS/s design point +20%"),
         ("T1", 20.5, "10 kS/s raw / 30 kS/s at R=3"),
         ("T2", 41.3, "30 kS/s at R=1.485"),
         ("T3", 61.4, "30 kS/s uncompressed")]


def _style(ax, theme, *, xlabel="", ylabel="", title=""):
    ax.set_facecolor(theme["surface"])
    ax.figure.set_facecolor(theme["surface"])
    ax.grid(True, color=theme["grid"], linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(theme["axis"])
        ax.spines[side].set_linewidth(1.0)
    ax.tick_params(colors=theme["text_secondary"], labelsize=9, length=0)
    if xlabel:
        ax.set_xlabel(xlabel, color=theme["text_secondary"], fontsize=10)
    if ylabel:
        ax.set_ylabel(ylabel, color=theme["text_secondary"], fontsize=10)
    if title:
        ax.set_title(title, color=theme["text_primary"], fontsize=12,
                     loc="left", pad=12, fontweight="bold")


def group(records: list[dict]) -> dict:
    """(entity, variant, transport) -> {offered_bps: [records]}"""
    out: dict = defaultdict(lambda: defaultdict(list))
    for r in records:
        if not r.get("valid", True):
            continue
        ent = entity(r["target"], str(r["rf"]["band"]))
        out[(ent, r["variant"], r["transport"])][r["offered_bps"]].append(r)
    return out


def _median(recs, path):
    vals = []
    for r in recs:
        v = r["metrics"]
        for k in path:
            v = v.get(k) if isinstance(v, dict) else None
            if v is None:
                break
        if v is not None:
            vals.append(v)
    return statistics.median(vals) if vals else None


def fig_loss_vs_load(records, theme, variant="tuned", transport="udp"):
    g = group(records)
    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=160)
    _style(ax, theme, xlabel="Offered load (Mbit/s)", ylabel="Frame loss (%)",
           title="Loss versus offered load — the knee, not the saturated number")
    drawn = 0
    for ent in ENTITY_ORDER:
        by_off = g.get((ent, variant, transport))
        if not by_off:
            continue
        xs = sorted(by_off)
        ys = [_median(by_off[x], ["loss_pct"]) or 0.0 for x in xs]
        c = color_for(ent, theme)
        ax.plot([x / 1e6 for x in xs], ys, color=c, linewidth=2.0,
                marker="o", markersize=5, zorder=3, label=ENTITY_LABEL[ent])
        k = knee([r for recs in by_off.values() for r in recs])
        if k["knee_offered_bps"]:
            kx = k["knee_offered_bps"] / 1e6
            ky = _median(by_off[k["knee_offered_bps"]], ["loss_pct"]) or 0.0
            ax.plot([kx], [ky], marker="o", markersize=9, color=c,
                    markeredgecolor=theme["surface"], markeredgewidth=2.0, zorder=4)
            ax.annotate(f"knee {kx:.0f}", (kx, ky), textcoords="offset points",
                        xytext=(6, 8), color=theme["text_secondary"], fontsize=9)
        drawn += 1
    ax.axhline(0.1, color=theme["reference"], linewidth=1.2, linestyle=(0, (4, 3)),
               zorder=2)
    ax.annotate("0.1 % loss threshold", (0.99, 0.1), xycoords=("axes fraction", "data"),
                ha="right", va="bottom", color=theme["text_muted"], fontsize=9)
    ax.set_ylim(bottom=-0.4)
    if drawn >= 2:
        leg = ax.legend(frameon=False, fontsize=9, loc="upper left")
        for t in leg.get_texts():
            t.set_color(theme["text_secondary"])
    fig.tight_layout()
    return fig


def fig_goodput_vs_load(records, theme, variant="tuned", transport="udp"):
    g = group(records)
    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=160)
    _style(ax, theme, xlabel="Offered load (Mbit/s)",
           ylabel="Sustained goodput (Mbit/s)",
           title="Delivered goodput versus offered load")
    lim = 0
    drawn = 0
    for ent in ENTITY_ORDER:
        by_off = g.get((ent, variant, transport))
        if not by_off:
            continue
        xs = sorted(by_off)
        ys = [(_median(by_off[x], ["goodput_bps"]) or 0.0) / 1e6 for x in xs]
        lim = max(lim, max(xs) / 1e6, max(ys) if ys else 0)
        ax.plot([x / 1e6 for x in xs], ys, color=color_for(ent, theme),
                linewidth=2.0, marker="o", markersize=5, zorder=3,
                label=ENTITY_LABEL[ent])
        drawn += 1
    if lim:
        ax.plot([0, lim], [0, lim], color=theme["reference"], linewidth=1.0,
                linestyle=(0, (2, 3)), zorder=1)
        ax.annotate("ideal (all offered load delivered)", (lim, lim),
                    textcoords="offset points", xytext=(-6, -14), ha="right",
                    color=theme["text_muted"], fontsize=9)
    if drawn >= 2:
        leg = ax.legend(frameon=False, fontsize=9, loc="upper left")
        for t in leg.get_texts():
            t.set_color(theme["text_secondary"])
    fig.tight_layout()
    return fig


def fig_rate_vs_ratio(records, theme, variant="tuned", transport="udp"):
    """The headline figure: achievable 128-channel sample rate against the compression
    ratio the upstream FPGA/STM32 must deliver, with each part's measured point on it."""
    g = group(records)
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=160)
    _style(ax, theme, xlabel="Upstream compression ratio R",
           ylabel="Achievable 128-ch sample rate (kS/s)",
           title="What the measured link buys, as a function of compression")
    ratios = [1.0 + i * 0.05 for i in range(0, 61)]
    drawn = 0
    for ent in ENTITY_ORDER:
        by_off = g.get((ent, variant, transport))
        if not by_off:
            continue
        k = knee([r for recs in by_off.values() for r in recs])
        gp = k["goodput_bps"]
        if not gp:
            continue
        ys = [gp * R / FRAME_BITS / 1000.0 for R in ratios]
        c = color_for(ent, theme)
        ax.plot(ratios, ys, color=c, linewidth=2.0, zorder=3, label=ENTITY_LABEL[ent])
        ax.annotate(f"{ENTITY_LABEL[ent]} — {gp/1e6:.0f} Mbit/s",
                    (ratios[-1], ys[-1]), textcoords="offset points", xytext=(-4, 4),
                    ha="right", color=theme["text_secondary"], fontsize=9)
        drawn += 1
    for label, mbps, why in TIERS:
        rate = mbps * 1e6 / FRAME_BITS / 1000.0
        ax.axhline(rate, color=theme["reference"], linewidth=1.0,
                   linestyle=(0, (4, 3)), zorder=2)
        ax.annotate(f"{label} · {why}", (1.0, rate), textcoords="offset points",
                    xytext=(2, 3), color=theme["text_muted"], fontsize=8)
    ax.set_xlim(1.0, 4.0)
    if drawn >= 2:
        leg = ax.legend(frameon=False, fontsize=9, loc="upper left")
        for t in leg.get_texts():
            t.set_color(theme["text_secondary"])
    fig.tight_layout()
    return fig


def fig_baseline_vs_tuned(records, theme, transport="udp"):
    """How much of each part's performance is free, and how much is earned."""
    g = group(records)
    ents, base, tuned = [], [], []
    for ent in ENTITY_ORDER:
        b = g.get((ent, "baseline", transport))
        t = g.get((ent, "tuned", transport))
        if not b or not t:
            continue
        kb = knee([r for recs in b.values() for r in recs])
        kt = knee([r for recs in t.values() for r in recs])
        if not kb["goodput_bps"] or not kt["goodput_bps"]:
            continue
        ents.append(ent)
        base.append(kb["goodput_bps"] / 1e6)
        tuned.append(kt["goodput_bps"] / 1e6)
    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=160)
    _style(ax, theme, ylabel="Sustained goodput at the knee (Mbit/s)",
           title="Baseline versus each chip's own best tuning")
    if not ents:
        ax.text(0.5, 0.5, "no paired baseline/tuned cells yet",
                ha="center", color=theme["text_muted"], transform=ax.transAxes)
        fig.tight_layout()
        return fig
    xs = range(len(ents))
    w = 0.36
    # 2 px surface gap between adjacent bars: drawn as an edge in the surface colour.
    for i, (b, t) in enumerate(zip(base, tuned)):
        c = color_for(ents[i], theme)
        ax.bar(i - w / 2 - 0.01, b, width=w, color=c, alpha=0.45, zorder=3,
               edgecolor=theme["surface"], linewidth=2.0)
        ax.bar(i + w / 2 + 0.01, t, width=w, color=c, zorder=3,
               edgecolor=theme["surface"], linewidth=2.0)
        ax.annotate(f"{b:.0f}", (i - w / 2 - 0.01, b), ha="center", va="bottom",
                    textcoords="offset points", xytext=(0, 3),
                    color=theme["text_secondary"], fontsize=9)
        ax.annotate(f"{t:.0f}", (i + w / 2 + 0.01, t), ha="center", va="bottom",
                    textcoords="offset points", xytext=(0, 3),
                    color=theme["text_primary"], fontsize=9, fontweight="bold")
        gain = 100.0 * (t - b) / b if b else 0.0
        ax.annotate(f"+{gain:.0f}%", (i, max(b, t)), ha="center", va="bottom",
                    textcoords="offset points", xytext=(0, 18),
                    color=theme["text_muted"], fontsize=9)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([ENTITY_LABEL[e] for e in ents], color=theme["text_secondary"])
    ax.annotate("left bar: baseline   ·   right bar: tuned",
                (0, 1.0), xycoords="axes fraction", textcoords="offset points",
                xytext=(0, -12), color=theme["text_muted"], fontsize=9)
    fig.tight_layout()
    return fig


def decomposition(records) -> dict:
    """The derived quantities from plan §5.1."""
    g = group(records)

    def k_of(ent, variant="tuned", transport="udp"):
        by = g.get((ent, variant, transport))
        if not by:
            return None
        return knee([r for recs in by.values() for r in recs])["goodput_bps"]

    c5_5, c5_24, s3_24 = k_of("esp32c5@5"), k_of("esp32c5@2.4"), k_of("esp32s3@2.4")
    out = {}
    if c5_5 and c5_24:
        out["band_effect_mbps"] = round((c5_5 - c5_24) / 1e6, 2)
    if c5_24 and s3_24:
        out["soc_effect_mbps"] = round((c5_24 - s3_24) / 1e6, 2)
    idle = {}
    for ent in ENTITY_ORDER:
        by = g.get((ent, "tuned", "udp"))
        if not by:
            continue
        kk = knee([r for recs in by.values() for r in recs])
        if kk["knee_offered_bps"]:
            recs = by[kk["knee_offered_bps"]]
            vals = [r["metrics"].get("idle_pct") for r in recs
                    if r["metrics"].get("idle_pct")]
            if vals:
                idle[ent] = round(statistics.median(v[0] for v in vals), 1)
    out["cpu_idle_pct_at_knee"] = idle
    if idle:
        c5 = idle.get("esp32c5@5", idle.get("esp32c5@2.4"))
        if c5 is not None:
            out["single_core_verdict"] = (
                "core-limited: the second core would be a real advantage" if c5 < 5
                else "not core-limited: the limit is RF, the AP or protocol overhead, "
                     "and core count is irrelevant to this decision" if c5 > 20
                else "inconclusive — idle sits between the thresholds; extend the sweep")
    return out


def matrix_table(records) -> str:
    """The table view. Required for accessibility, and the thing most readers want."""
    g = group(records)
    rows = ["| Cell | Variant | Transport | Knee (Mbit/s) | Goodput (Mbit/s) | "
            "Spread (MAD) | Loss @ knee | Repeats |",
            "|---|---|---|---|---|---|---|---|"]
    for (ent, variant, transport), by in sorted(g.items()):
        recs = [r for rs in by.values() for r in rs]
        k = knee(recs)
        if not k["knee_offered_bps"]:
            rows.append(f"| {ENTITY_LABEL.get(ent, ent)} | {variant} | {transport} | "
                        f"— | — | — | — | {len(recs)} |")
            continue
        at = by[k["knee_offered_bps"]]
        loss = _median(at, ["loss_pct"])
        mad = k.get("goodput_mad_bps") or 0.0
        rows.append(f"| {ENTITY_LABEL.get(ent, ent)} | {variant} | {transport} | "
                    f"{k['knee_offered_bps']/1e6:.1f} | {k['goodput_bps']/1e6:.2f} | "
                    f"±{mad/1e6:.2f} | {loss:.3f} % | {k.get('repeats', len(at))} |")
    return "\n".join(rows)


def render(ledger_path="results/runs.jsonl", out_dir="results/report") -> dict:
    recs = Ledger(ledger_path).read()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for name, fn in (("loss_vs_load", fig_loss_vs_load),
                     ("goodput_vs_load", fig_goodput_vs_load),
                     ("rate_vs_ratio", fig_rate_vs_ratio),
                     ("baseline_vs_tuned", fig_baseline_vs_tuned)):
        for mode, theme in (("light", LIGHT), ("dark", DARK)):
            fig = fn(recs, theme)
            p = out / f"{name}.{mode}.png"
            fig.savefig(p, facecolor=theme["surface"])
            plt.close(fig)
            made.append(str(p))

    dec = decomposition(recs)
    ceilings = {r["rf"]["band"]: r["rf"].get("rig_ceiling_mbps") for r in recs}
    phy = sorted({str(r["rf"].get("phy_rate")) for r in recs if r["rf"].get("phy_rate")})
    md = [
        "# S3 vs C5 — measured results", "",
        f"Records: **{len(recs)}** · rig ceiling by band: "
        f"{ceilings or 'NOT MEASURED'} · observed PHY: {', '.join(phy) or 'unknown'}",
        "",
        "> A number above the rig ceiling is a property of the rig, not the silicon.",
        "> If the PHY column never reads HE, the C5 was not exercised at Wi-Fi 6 rates",
        "> and its figure is a lower bound (plan §7.3b).", "",
        "## Matrix", "", matrix_table(recs), "",
        "## Decomposition (plan §5.1)", "",
    ]
    for k, v in dec.items():
        md.append(f"- **{k}**: {v}")
    (out / "report.md").write_text("\n".join(md) + "\n")
    made.append(str(out / "report.md"))
    return {"files": made, "records": len(recs), "decomposition": dec}
