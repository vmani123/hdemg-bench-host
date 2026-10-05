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
        ap=m.ap, ssid=m.ssid)
    if m.ssid:
        # Enforced before anything is built or flashed: this Mac on the named network,
        # and every board's firmware configured to join it.
        on = rfmeta.host_ssid()
        problems = []
        if on != m.ssid:
            problems.append(f"this Mac is on Wi-Fi network {on!r}; join {m.ssid!r} first")
        for chip in sorted({c["chip"] for c in m.cells}):
            built_for = drv.board_ssid(chip)
            if built_for != m.ssid:
                problems.append(f"firmware/{chip}/sdkconfig.local joins {built_for!r}, "
                                f"not {m.ssid!r}")
        if problems:
            print(f"this matrix must run on Wi-Fi network {m.ssid!r}:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            print("refusing to run; nothing was built or flashed", file=sys.stderr)
            return 2
        print(f"network '{m.ssid}': this Mac and every board's firmware are set to it")
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
                       allow_shared_band=a.allow_shared_band,
                       transports=[a.transport] if a.transport else None,
                       rssi_drift_warn_only=a.rssi_drift_warn)
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
    mx = Matrix.load(a.matrix)
    ap = mx.ap
    drv = HardwareDriver(ips={"esp32s3": None, "esp32c5": None},
                         firmware_root=str(REPO / "firmware"),
                         ports={}, host_ip=a.host_ip, ap=ap, ssid=mx.ssid)
    host = drv.host_ip()
    path = rfmeta.host_path(_gateway_of(host), ap)
    print(f"access point       : {ap}  (from {a.matrix})")
    print(f"host ip (stream to): {host}")
    print(f"host link          : {_describe_path(path)}")
    ssid_ok = True
    if mx.ssid:
        on = rfmeta.host_ssid()
        print(f"required network   : {mx.ssid}")
        if on != mx.ssid:
            ssid_ok = False
            print(f"  !! this Mac is on {on!r}, not {mx.ssid!r} — the test will refuse to run")
        for chip in sorted({c["chip"] for c in mx.cells}):
            built_for = drv.board_ssid(chip)
            if built_for != mx.ssid:
                ssid_ok = False
                print(f"  !! firmware/{chip}/sdkconfig.local joins {built_for!r}, not "
                      f"{mx.ssid!r} — the test will refuse to build")
    filters = rfmeta.content_filters_active()
    if filters:
        print(f"  !! {filters} socket content filter(s) active on this Mac (VPN / endpoint-"
              f"security software). It inspects every datagram and at bench rates makes the "
              f"kernel discard some: UDP loss would then be the Mac's, and such runs are "
              f"refused. TCP is unaffected. Turn the filter off to measure UDP.")
    rig_ok = ssid_ok
    if ap == rfmeta.IPHONE_AP:
        if path.get("link") != "usb":
            rig_ok = False
            print("  !! iPhone USB is NOT UP — plug the iPhone in and enable Personal Hotspot")
    elif path.get("link") == "wifi" and mx.allow_shared_band:
        print(f"  -> this Mac and the boards share the access point's one channel (band "
              f"{path.get('band')} GHz): every frame crosses the same air twice, so numbers "
              f"here are lower than on a rig with a wired second hop. Accepted by this "
              f"matrix and recorded as a warning on every run.")
    elif path.get("link") == "wifi":
        print(f"  -> cells on band {path.get('band')} GHz would share this Mac's channel and "
              f"are refused by the pre-run gate; the other band is clean. Wire the Mac to "
              f"the router to run both.")
    if mx.ssid and rfmeta.host_ssid() != mx.ssid:
        # Never go looking for boards on a network that is not the bench's: that would
        # be probing every address of somebody else's subnet.
        print("not looking for boards: this Mac is not on the required network")
        return 1
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


def cmd_void(a) -> int:
    """Withdraw recorded runs that turned out to be wrong. Append-only: the runs stay in
    the ledger with the reason, leave every median, and count as not done, so the next
    sweep measures them again."""
    led = Ledger(a.ledger)
    hit = []
    for r in led.read():
        if not r.get("valid", True) or r.get("voided"):
            continue
        if a.transport and r.get("transport") != a.transport:
            continue
        if a.target and r.get("target") != a.target:
            continue
        if a.cell and a.cell not in str(r.get("cell_id")):
            continue
        hit.append(r)
    for r in hit:
        m = r.get("metrics", {})
        print(f"  {r['run_id']}  good={m.get('goodput_bps', 0) / 1e6:.2f} loss={m.get('loss_pct')}")
    if a.dry_run:
        print(f"{len(hit)} valid run(s) match; nothing written (--dry-run)")
        return 0
    n = led.void([r["run_id"] for r in hit], a.reason)
    print(f"voided {n} run(s): {a.reason}")
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
    r.add_argument("--transport", default=None, choices=["udp", "tcp"],
                   help="restrict the sweep to one transport; the other's points stay "
                        "pending. Use tcp while this host cannot measure UDP (for "
                        "instance while a socket content filter is dropping datagrams).")
    r.add_argument("--rssi-drift-warn", action="store_true",
                   help="record an RSSI drift of more than 3 dB as a warning instead of "
                        "refusing the run. For a board whose reported RSSI wanders on its "
                        "own; both readings are stored with the run so it can be filtered "
                        "later. Off by default: a drift normally means the rig moved.")
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

    v = sub.add_parser("void", help="withdraw recorded runs that turned out to be wrong "
                                    "(appends a marker; nothing is rewritten)")
    v.add_argument("--ledger", default="results/runs.jsonl")
    v.add_argument("--reason", required=True, help="why — stored with the runs")
    v.add_argument("--transport", default=None, choices=["udp", "tcp"])
    v.add_argument("--target", default=None, choices=["esp32s3", "esp32c5"])
    v.add_argument("--cell", default=None, help="substring of the cell id")
    v.add_argument("--dry-run", action="store_true", help="list the matches, write nothing")
    v.set_defaults(fn=cmd_void)

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
