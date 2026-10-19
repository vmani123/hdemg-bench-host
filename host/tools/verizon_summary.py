"""Tables for Report 4 (the Verizon TCP plan, 19 Oct) from the campaign's own records.

    python tools/verizon_summary.py [--ledger results/campaign/verizon.jsonl]

Reads the ledger and the recovery ladder's log and prints markdown:

  * the stall zone (V.1a-c): per chip, setting and load, the median delivered rate, how
    many runs stalled (delivered under half the offered rate) and how many left the board
    locked up (the recovery ladder started before the board's next run);
  * the long runs (V.2-V.6): delivered rate, gaps, reconnects, memory, Wi-Fi drops,
    mid-run watchdog revivals, and how much of the run each 10 s window carried;
  * the write sizes (V.7).

A lock-up is attributed to the last run of that board that ENDED before the ladder
started; a ladder that starts while a long run is still going (the watchdog) belongs to
that run.
"""
from __future__ import annotations
import argparse
import calendar
import json
import re
import statistics
import time
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
TAG = re.compile(r"-t(V\.\d+[a-z]?)(?:r\d+)?-(.+)$")
LADDER = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (esp32\w+): not answering: "
                    r"recovery ladder starts")
SHORT = {"esp32s3": "S3", "esp32c5": "C5"}


def _utc(ts: str) -> float:
    return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))


def load(ledger: Path, rlog: Path):
    rows = []
    for line in ledger.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        m = TAG.search(r.get("cell_id", ""))
        if not m:
            continue
        r["_test"], r["_label"] = m.group(1), m.group(2)
        r["_end"] = _utc(r["timestamp"])
        r["_mbps"] = (r["metrics"].get("goodput_bps") or 0.0) / 1e6
        r["_off"] = round(r["offered_bps"] / 1e6, 2)
        r["_lock"] = 0
        rows.append(r)
    ladders = []
    if rlog.exists():
        for line in rlog.read_text().splitlines():
            m = LADDER.match(line)
            if m:
                ladders.append((time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")),
                                m.group(2)))
    # attribute each ladder to one run (deduplicated: a killed process and the next
    # one's health check can both start a ladder for the same lock-up)
    for t, chip in ladders:
        mine = [r for r in rows if r["target"] == chip]
        during = [r for r in mine if r["_end"] - (r["metrics"].get("duration_s") or 0) - 30
                  <= t <= r["_end"] and (r["metrics"].get("duration_s") or 0) > 120]
        if during:
            during[0]["_lock_during"] = during[0].get("_lock_during", 0) + 1
            continue
        before = [r for r in mine if r["_end"] <= t]
        if before:
            last = max(before, key=lambda r: r["_end"])
            if t - last["_end"] < 900:
                last["_lock"] = 1
    return rows


def _fmt(v, nd=1):
    return "—" if v is None else f"{v:.{nd}f}"


def zone(rows) -> list[str]:
    out = ["### Stall zone (V.1a–c): TCP, 20 s runs, shuffled order", "",
           "| chip | setting | offered | n | median Mbit/s | min–max | stalled | locked up "
           "after | heap low B (median) |", "|---|---|---|---|---|---|---|---|---|"]
    z = [r for r in rows if r["_test"].startswith("V.1") and r.get("valid", True)]
    keys = sorted({(r["target"], r["_label"], r["_off"]) for r in z},
                  key=lambda k: (k[0], k[1], k[2]))
    for chip, lab, off in keys:
        rs = [r for r in z if (r["target"], r["_label"], r["_off"]) == (chip, lab, off)]
        g = [r["_mbps"] for r in rs]
        stalled = sum(1 for r in rs if r["_mbps"] < 0.5 * off)
        locked = sum(r["_lock"] for r in rs)
        lows = [(r.get("telemetry") or {}).get("heap_low_B") for r in rs]
        lows = [x for x in lows if x is not None]
        out.append(f"| {SHORT[chip]} | {'batch=30' if lab.startswith('b') else 'default'} | "
                   f"{off:g} | {len(rs)} | {statistics.median(g):.2f} | "
                   f"{min(g):.1f}–{max(g):.1f} | {stalled}/{len(rs)} | {locked}/{len(rs)} | "
                   f"{int(statistics.median(lows)) if lows else '—'} |")
    by_round = ["", "Per round (median Mbit/s, all loads pooled per setting):", ""]
    for tid in ("V.1a", "V.1b", "V.1c"):
        for chip in ("esp32c5", "esp32s3"):
            for lab in ("def", "b30"):
                rs = [r for r in z if r["_test"] == tid and r["target"] == chip
                      and r["_label"] == lab]
                if rs:
                    share = statistics.median(r["_mbps"] / r["_off"] for r in rs)
                    by_round.append(f"- {tid} {SHORT[chip]} "
                                    f"{'batch=30' if lab == 'b30' else 'default'}: "
                                    f"median share of offered {share:.2f}, "
                                    f"stalled {sum(1 for r in rs if r['_mbps'] < 0.5 * r['_off'])}"
                                    f"/{len(rs)}, locked up "
                                    f"{sum(r['_lock'] for r in rs)}/{len(rs)}")
    return out + by_round


def _windows(r) -> list[dict]:
    """The run's 10 s receiver windows; rebuilt from its 1 s series when the receiver
    returned before computing them (V.5, 19 Oct: the drift fit outran the join)."""
    p = r.get("timeseries")
    w = []
    if p and Path(p).exists():
        try:
            w = json.loads(Path(p).read_text()).get("windows") or []
        except (OSError, ValueError):
            w = []
    if w:
        return w
    ps = r["metrics"].get("per_second") or []
    return [{"t_s": 10.0 * i, "goodput_bps": sum(ps[10 * i:10 * i + 10]) / 10.0}
            for i in range(len(ps) // 10)]


def longruns(rows) -> list[str]:
    out = ["### Long runs (V.2–V.6)", "",
           "| test | chip | setting | offered | min | Mbit/s | windows ≥ 90 % | first window "
           "< 50 % | longest gap s | connections | watchdog revivals | heap low B | Wi-Fi "
           "drops | resets |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted((r for r in rows if r["_test"] in ("V.2", "V.2b", "V.3", "V.4", "V.5", "V.6")),
                    key=lambda r: (r["_test"], r["target"])):
        m, te = r["metrics"], r.get("telemetry") or {}
        w = _windows(r)
        ok = sum(1 for x in w if x["goodput_bps"] >= 0.9 * r["offered_bps"])
        first_bad = next((x["t_s"] for x in w if x["goodput_bps"] < 0.5 * r["offered_bps"]),
                         None)
        dog = sum(1 for e in (m.get("events") or []) if e.get("kind") == "watchdog"
                  and e.get("action") == "restarted")
        out.append(f"| {r['_test']} | {SHORT[r['target']]} | {r['_label']} | {r['_off']:g} | "
                   f"{(m.get('duration_s') or 0) / 60:.0f} | {r['_mbps']:.2f} | "
                   f"{ok}/{len(w)} | {_fmt(first_bad, 0)} | {_fmt(m.get('longest_gap_s'))} | "
                   f"{m.get('connections') or '—'} | {dog} | {te.get('heap_low_B', '—')} | "
                   f"{te.get('wifi_disc_delta', '—')} | {te.get('resets', '—')} |")
    return out


def writes(rows) -> list[str]:
    out = ["### Write sizes (V.7): TCP at 60 offered, 20 s runs", "",
           "| chip | write | n | median Mbit/s | min–max | locked up after |",
           "|---|---|---|---|---|---|"]
    v = [r for r in rows if r["_test"] == "V.7"]
    for chip, lab in sorted({(r["target"], r["_label"]) for r in v}):
        rs = [r for r in v if (r["target"], r["_label"]) == (chip, lab)]
        good = [r for r in rs if r.get("valid", True)]
        g = [r["_mbps"] for r in good] or [0.0]
        out.append(f"| {SHORT[chip]} | {lab} | {len(good)} (+{len(rs) - len(good)} not run) | "
                   f"{statistics.median(g):.2f} | {min(g):.1f}–{max(g):.1f} | "
                   f"{sum(r['_lock'] for r in rs)}/{len(rs)} |")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=str(HOST / "results/campaign/verizon.jsonl"))
    ap.add_argument("--recovery-log",
                    default=str(HOST / "results/campaign/verizon/recovery.log"))
    a = ap.parse_args()
    rows = load(Path(a.ledger), Path(a.recovery_log))
    print("\n".join(zone(rows) + [""] + longruns(rows) + [""] + writes(rows)))


if __name__ == "__main__":
    main()
