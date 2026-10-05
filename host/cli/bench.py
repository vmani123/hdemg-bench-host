#!/usr/bin/env python3
"""hdemg-bench — command line entry point.

    python3 -m cli.bench sim      --matrix matrices/stage1.yaml
    python3 -m cli.bench run      --matrix matrices/stage1.yaml --ceilings 5=95,2.4=60
    python3 -m cli.bench report
    python3 -m cli.bench parity
    python3 -m cli.bench effort
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
REPO = Path(__file__).resolve().parents[2]

from bench import parity, report, rfmeta, rungs                      # noqa: E402
from bench.drivers import HardwareDriver, SimDriver          # noqa: E402
from bench.ledger import Ledger                              # noqa: E402
from bench.orchestrator import Matrix, Orchestrator          # noqa: E402


def _ceilings(s: str | None) -> dict:
    if not s:
        return {}
    out = {}
    for part in s.split(","):
        band, _, val = part.partition("=")
        out[band.strip()] = float(val)
    return out


def _progress(kind, **kw):
    if kind == "band":
        print(f"\n--- band {kw['band']} ---")
    elif kind == "keepalive_lost":
        print(f"  !! keepalive lost the device ({kw.get('error')}) — will re-check")
    elif kind == "keepalive_reassociated":
        print(f"  ** device re-associated (#{kw.get('count')}); "
              f"the next run is flagged in the ledger")
    elif kind == "recovering":
        print(f"  !! {kw.get('chip')} not answering — attempting recovery")
    elif kind == "run_error":
        print(f"  !! {kw['run'].cell_id}: {kw.get('error')}\n"
              f"     skipping the rest of this cell for this pass; resume retries it")
    elif kind == "run":
        r = kw["run"]
        flag = "" if kw["valid"] else "  INVALID"
        loss = kw.get("loss")
        loss_s = f"{loss:5.2f}%" if loss is not None else "  —  "
        print(f"  {r.cell_id:<34} off={r.offered_bps/1e6:5.1f}  "
              f"good={kw['goodput_mbps']:6.2f}  loss={loss_s}  rep{r.repeat}{flag}")


def cmd_sim(a) -> int:
    m = Matrix.load(a.matrix)
    if a.hold:
        m.sweep["hold_s"] = a.hold
        m.sweep["discard_s"] = min(a.hold / 4, m.sweep["discard_s"])
    drv = SimDriver()
    led = Ledger(a.ledger)
    orc = Orchestrator(m, drv, led, rig_ceilings=_ceilings(a.ceilings) or
                       {"5": 95.0, "2.4": 60.0}, on_event=_progress)
    try:
        res = orc.run_all(limit=a.limit)
    finally:
        drv.shutdown()
    print(json.dumps(res, indent=2))
    print(f"flash operations: {drv.flashes}")
    return 0


def cmd_run(a) -> int:
    m = Matrix.load(a.matrix)
    par = parity.check(a.firmware)
    if not par["ok"]:
        print(parity.summary(par), file=sys.stderr)
        if not a.force:
            print("refusing to run a cross-chip comparison; pass --force to override",
                  file=sys.stderr)
            return 2
    hub = {}
    if a.s3_hub_port:
        hub["esp32s3"] = a.s3_hub_port
    if a.c5_hub_port:
        hub["esp32c5"] = a.c5_hub_port
    if a.expected_idf:
        m.expected_idf = a.expected_idf
    drv = HardwareDriver(
        ips={"esp32s3": a.s3_ip, "esp32c5": a.c5_ip},
        firmware_root=a.firmware,
        ports={"esp32s3": a.s3_port, "esp32c5": a.c5_port},
        agent_dir=a.agent_dir,
        host_ip=a.host_ip,
        interactive_band=not a.unattended,
        hub_ports=hub,
        ap=m.ap)
    print(f"access point '{m.ap}'  host ip {drv.host_ip()}  expected idf {m.expected_idf}")
    print(f"host link: {_describe_path(rfmeta.host_path(_gateway_of(drv.host_ip()), m.ap))}")
    for chip, h in sorted(drv.rediscover().items()):
        print(f"  found {chip} at {h['ip']}  band={h.get('band')} rung={h.get('rung')} "
              f"idf={h.get('idf')}")
    orc = Orchestrator(m, drv, Ledger(a.ledger),
                       rig_ceilings=_ceilings(a.ceilings),
                       parity_ok=par["ok"],
                       bands=[a.band] if a.band else None,
                       keepalive=True, recover=True, on_event=_progress,
                       allow_shared_band=a.allow_shared_band)
    # Several passes: every point skipped or invalidated in one pass is retried in the
    # next (the ledger is the state). Stop early once nothing is left, or once a pass
    # makes no valid progress — repeating it would only repeat the failure.
    res: dict = {}
    for p in range(1, max(1, a.passes) + 1):
        print(f"\n=== pass {p}/{a.passes}  pending {len(orc.pending())} ===", flush=True)
        res = orc.run_all(limit=a.limit)
        print(json.dumps(res, indent=2), flush=True)
        if res["remaining"] == 0 or res["valid"] == 0 or a.limit:
            break
        time.sleep(30.0)
    return 0 if res.get("remaining") == 0 else 3


def _gateway_of(host_ip: str) -> str:
    """An address on the host's own /24, good enough to ask the routing table which
    interface carries the bench traffic before any board has been found."""
    return host_ip.rsplit(".", 1)[0] + ".1"


def _describe_path(p: dict) -> str:
    link = p.get("link")
    if link == "wifi":
        return (f"Wi-Fi ({p.get('dev')}) on '{p.get('ssid')}', band {p.get('band')} GHz, "
                f"channel {p.get('channel')}, {p.get('width_mhz')} MHz, {p.get('phy')}")
    if link == "usb":
        return "iPhone USB (wired to the hotspot)"
    if link == "ethernet":
        return f"Ethernet ({p.get('dev')}), wired"
    return "unknown"


def cmd_discover(a) -> int:
    """Pre-flight: what the harness will see when the sweep starts."""
    from bench.control import ControlClient
    ap = Matrix.load(a.matrix).ap
    drv = HardwareDriver(ips={"esp32s3": None, "esp32c5": None}, firmware_root=".",
                         ports={}, host_ip=a.host_ip, ap=ap)
    host = drv.host_ip()
    path = rfmeta.host_path(_gateway_of(host), ap)
    print(f"access point       : {ap}  (from {a.matrix})")
    print(f"host ip (stream to): {host}")
    print(f"host link          : {_describe_path(path)}")
    rig_ok = True
    if ap == rfmeta.IPHONE_AP:
        if path.get("link") != "usb":
            rig_ok = False
            print("  !! iPhone USB is NOT UP — plug the iPhone in and enable Personal Hotspot")
    elif path.get("link") == "wifi":
        print(f"  -> cells on band {path.get('band')} GHz would share this Mac's channel and "
              f"are refused by the pre-run gate; the other band is clean. Wire the Mac to "
              f"the router to run both.")
    found = drv.rediscover()
    if not found:
        print(f"no bench devices answered on {host.rsplit('.', 1)[0]}.0/24")
        return 1
    for chip, h in sorted(found.items()):
        try:
            st = ControlClient(h["ip"]).stat()
        except Exception as e:                               # noqa: BLE001
            st = {"error": str(e)}
        print(f"{chip:<8} {h['ip']:<15} band={h.get('band')} rung={h.get('rung')} "
              f"ingress={h.get('ingress')} idf={h.get('idf')} rssi={st.get('rssi')} "
              f"phy={st.get('phy_rate')}")
    return 0 if rig_ok else 1


def cmd_report(a) -> int:
    res = report.render(a.ledger, a.out)
    print(json.dumps(res, indent=2))
    return 0


def cmd_parity(a) -> int:
    res = parity.check(a.firmware)
    print(parity.summary(res))
    print(json.dumps(res, indent=2))
    return 0 if res["ok"] else 1


def cmd_effort(a) -> int:
    res = rungs.effort_report(a.rungs)
    print(json.dumps(res, indent=2))
    if not res["equal_effort_ok"]:
        print("\nequal-effort protocol NOT satisfied: levers above are unattempted on "
              "one chip. A comparison published now is only as strong as the weaker "
              "tuning effort.", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bench", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sim", help="run a matrix against loopback fake ESPs (no hardware)")
    s.add_argument("--matrix", default="matrices/stage1.yaml")
    s.add_argument("--ledger", default="results/runs.jsonl")
    s.add_argument("--ceilings", default=None)
    s.add_argument("--limit", type=int, default=None)
    s.add_argument("--hold", type=float, default=None, help="override hold_s (fast sims)")
    s.set_defaults(fn=cmd_sim)

    r = sub.add_parser("run", help="run a matrix against real boards")
    r.add_argument("--matrix", required=True)
    r.add_argument("--ledger", default="results/runs.jsonl")
    r.add_argument("--ceilings", required=True, help='e.g. "5=95,2.4=60"')
    r.add_argument("--firmware", default=str(REPO / "firmware"))
    r.add_argument("--s3-ip", default="auto",
                   help="'auto' finds it by asking every address on the host's /24")
    r.add_argument("--c5-ip", default="auto")
    r.add_argument("--host-ip", default="auto",
                   help="address the ESPs stream to; 'auto' = the iPhone USB interface")
    r.add_argument("--s3-port", default="cu.usbmodem1101")
    r.add_argument("--c5-port", default="cu.usbmodem1201")
    r.add_argument("--agent-dir", default=str(REPO / "_agent"),
                   help="must be the directory hostrun.sh watches (<repo>/_agent)")
    r.add_argument("--expected-idf", default=None,
                   help="override the matrix's expected_idf (the pre-run gate compares "
                        "it to what the firmware reports)")
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--passes", type=int, default=1,
                   help="re-run skipped/invalid points up to this many passes")
    r.add_argument("--force", action="store_true")
    r.add_argument("--band", default=None, choices=["2.4", "5"],
                   help="restrict the sweep to one band: the unit that runs unattended. "
                        "On a router rig the Mac's own Wi-Fi must be on the OTHER band "
                        "(or wired); on the iPhone rig the hotspot serves one band at a "
                        "time and the toggle is manual.")
    r.add_argument("--unattended", action="store_true",
                   help="never block on a prompt. The pre-run gate verifies the band from "
                        "the device, so a wrong band fails loudly instead of quietly "
                        "measuring the other one.")
    r.add_argument("--allow-shared-band", action="store_true",
                   help="record a warning instead of refusing a cell when this Mac's own "
                        "Wi-Fi is on the band being measured (both hops then share one "
                        "channel and every number is depressed)")
    r.add_argument("--s3-hub-port", default=None,
                   help="uhubctl port number for the S3, enabling auto power-cycle "
                        "recovery of a wedged board")
    r.add_argument("--c5-hub-port", default=None)
    r.set_defaults(fn=cmd_run)

    d = sub.add_parser("discover", help="pre-flight: show the rig (access point, how this "
                                        "Mac reaches it) and find the boards: band, rung, "
                                        "IDF, RSSI, PHY")
    d.add_argument("--host-ip", default="auto")
    d.add_argument("--matrix", default="matrices/stage1.yaml",
                   help="the matrix whose `ap:` names the rig")
    d.set_defaults(fn=cmd_discover)

    q = sub.add_parser("report", help="render figures and tables from the ledger")
    q.add_argument("--ledger", default="results/runs.jsonl")
    q.add_argument("--out", default="results/report")
    q.set_defaults(fn=cmd_report)

    c = sub.add_parser("parity", help="check shared-core integrity (bench/parity.py)")
    c.add_argument("--firmware", default="../firmware")
    c.set_defaults(fn=cmd_parity)

    e = sub.add_parser("effort", help="check the equal-effort protocol (plan §5.4)")
    e.add_argument("--rungs", default="rungs")
    e.set_defaults(fn=cmd_effort)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
