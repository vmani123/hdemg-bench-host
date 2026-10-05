#!/usr/bin/env python3
"""hdemg-bench — command line entry point.

    python3 -m cli.bench sim      --matrix matrices/stage1.yaml
    python3 -m cli.bench run      --matrix matrices/stage1.yaml --ceilings 5=95,2.4=60
    python3 -m cli.bench linktest --chip esp32s3 --link qspi      # Stage 2 gate (guide §7.2)
    python3 -m cli.bench h7 hello                                  # talk to the H7 generator
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

from bench import linktest, parity, report, rungs            # noqa: E402
from bench.h7 import DEFAULT_H7_PORT, H7Control, H7Error     # noqa: E402
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


def _link_opts(items) -> dict:
    """--link-opt qspi_hz=25000000 ... -> settings passed to the H7 generator's cfg."""
    out = {}
    for it in items or []:
        k, sep, v = it.partition("=")
        if not sep or not k:
            raise SystemExit(f"--link-opt wants key=value, got {it!r}")
        out[k.strip()] = v.strip()
    return out


def _csv(s: str | None) -> list[str] | None:
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def _hardware(a, *, interactive_band: bool = False) -> HardwareDriver:
    hub = {}
    if getattr(a, "s3_hub_port", None):
        hub["esp32s3"] = a.s3_hub_port
    if getattr(a, "c5_hub_port", None):
        hub["esp32c5"] = a.c5_hub_port
    return HardwareDriver(
        ips={"esp32s3": a.s3_ip, "esp32c5": a.c5_ip},
        firmware_root=a.firmware,
        ports={"esp32s3": a.s3_port, "esp32c5": a.c5_port},
        agent_dir=a.agent_dir,
        host_ip=a.host_ip,
        interactive_band=interactive_band,
        hub_ports=hub,
        tree=a.tree, h7_port=a.h7_port)


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
    if a.expected_idf:
        m.expected_idf = a.expected_idf
    drv = _hardware(a, interactive_band=not a.unattended)
    print(f"host ip {drv.host_ip()}  expected idf {m.expected_idf}")
    for chip, h in sorted(drv.rediscover().items()):
        print(f"  found {chip} at {h['ip']}  band={h.get('band')} rung={h.get('rung')} "
              f"idf={h.get('idf')}")
    orc = Orchestrator(m, drv, Ledger(a.ledger),
                       rig_ceilings=_ceilings(a.ceilings),
                       parity_ok=par["ok"],
                       bands=[a.band] if a.band else None,
                       keepalive=True, recover=True, on_event=_progress,
                       chips=_csv(a.chips), sources=_csv(a.sources),
                       link_opts=_link_opts(a.link_opt))
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


def cmd_linktest(a) -> int:
    """Stage 2 gate: prove the wire clean with Wi-Fi out of the loop (guide §7.2)."""
    drv = _hardware(a)
    loads = [int(float(x) * 1e6) for x in a.loads.split(",")]
    opts = _link_opts(a.link_opt)
    print(f"link test: {a.chip} {a.link}  loads {a.loads} Mbit/s  hold {a.hold:g} s  "
          f"opts {opts or '{}'}", flush=True)
    if a.hold < linktest.GUIDE_HOLD_S:
        print(f"  note: hold is under the guide's {linktest.GUIDE_HOLD_S:g} s — a smoke "
              f"test, not the pass criterion", flush=True)
    try:
        res = linktest.run(drv, a.chip, a.link, loads, hold_s=a.hold, tune=a.tune,
                           link_opts=opts, say=lambda m: print(m, flush=True))
    except Exception as e:                                   # noqa: BLE001
        res = {"chip": a.chip, "link": a.link, "passed": False, "steps": [],
               "error": f"{type(e).__name__}: {e}", "link_opts": opts, "hold_s": a.hold}
        print(f"  could not run the link test: {res['error']}", flush=True)
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=2, sort_keys=True))
        print(f"wrote {out}")
    print(f"link test {'PASSED' if res['passed'] else 'FAILED'}: {a.chip} {a.link}")
    return 0 if res["passed"] else 1


def cmd_h7(a) -> int:
    """Send one command line to the H7 generator and print its reply."""
    h = H7Control(a.h7_port)
    try:
        print(json.dumps(h._rpc(" ".join(a.words), timeout=a.timeout), sort_keys=True))
        return 0
    except H7Error as e:
        print(json.dumps({"ok": False, "err": str(e), **({"reply": e.reply} if e.reply else {})},
                         sort_keys=True))
        return 1
    finally:
        h.close()


def cmd_discover(a) -> int:
    """Pre-flight: what the harness will see when the sweep starts."""
    from bench import rfmeta
    from bench.control import ControlClient
    usb = rfmeta.iphone_usb_ip()
    drv = HardwareDriver(ips={"esp32s3": None, "esp32c5": None}, firmware_root=".",
                         ports={}, host_ip=a.host_ip)
    host = drv.host_ip()
    print(f"iPhone USB address : {usb or 'NOT UP — plug the iPhone in and enable Personal Hotspot'}")
    print(f"host ip (stream to): {host}")
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
    return 0 if usb else 1


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


def _wired_args(p) -> None:
    p.add_argument("--tree", default=None,
                   help="build and flash from this git worktree (.claude/worktrees/<name>) "
                        "instead of the repo root; passed to hostrun.sh as tree=<name>")
    p.add_argument("--h7-port", default=DEFAULT_H7_PORT,
                   help="serial port of the STM32H745 generator (the ST-LINK VCP)")
    p.add_argument("--link-opt", action="append", default=[], metavar="KEY=VALUE",
                   help="extra H7 link setting for wired cells, e.g. qspi_hz=25000000; "
                        "repeatable, and recorded with every run")


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
                   help="restrict the sweep to one band. The hotspot serves one band at "
                        "a time and the toggle is manual, so one band is the unit that "
                        "runs unattended: do 2.4 overnight, flip the toggle, do 5.")
    r.add_argument("--unattended", action="store_true",
                   help="never block on a prompt. Set the hotspot band BEFORE starting; "
                        "the pre-run gate verifies it from the device, so a wrong toggle "
                        "fails loudly instead of quietly measuring the other band.")
    r.add_argument("--s3-hub-port", default=None,
                   help="uhubctl port number for the S3, enabling auto power-cycle "
                        "recovery of a wedged board")
    r.add_argument("--c5-hub-port", default=None)
    r.add_argument("--chips", default=None,
                   help="restrict to these chips (comma separated). For Stage 2: only a "
                        "chip that is physically wired to the H7 can run a wired cell")
    r.add_argument("--sources", default=None,
                   help="restrict to these sources (synth,qspi,sdio)")
    _wired_args(r)
    r.set_defaults(fn=cmd_run)

    lt = sub.add_parser("linktest", help="Stage 2 gate: verify the H7->ESP wire with "
                                         "Wi-Fi out of the loop (guide §7.2)")
    lt.add_argument("--chip", required=True, choices=["esp32s3", "esp32c5"])
    lt.add_argument("--link", required=True, choices=["qspi", "sdio"])
    lt.add_argument("--loads", default="4,12,20,28,36,44,52,60",
                    help="offered loads in Mbit/s, comma separated")
    lt.add_argument("--hold", type=float, default=linktest.GUIDE_HOLD_S,
                    help="seconds per load; the guide's pass criterion is 600")
    lt.add_argument("--tune", default="tuned")
    lt.add_argument("--out", default=None, help="write the result as JSON here")
    lt.add_argument("--firmware", default=str(REPO / "firmware"))
    lt.add_argument("--s3-ip", default="auto")
    lt.add_argument("--c5-ip", default="auto")
    lt.add_argument("--host-ip", default="auto")
    lt.add_argument("--s3-port", default="cu.usbmodem101")
    lt.add_argument("--c5-port", default="cu.usbserial-110")
    lt.add_argument("--agent-dir", default=str(REPO / "_agent"))
    _wired_args(lt)
    lt.set_defaults(fn=cmd_linktest)

    h = sub.add_parser("h7", help="send one command to the H7 generator (hello, stat, ...)")
    h.add_argument("words", nargs="+")
    h.add_argument("--h7-port", default=DEFAULT_H7_PORT)
    h.add_argument("--timeout", type=float, default=3.0)
    h.set_defaults(fn=cmd_h7)

    d = sub.add_parser("discover", help="pre-flight: find the boards on the hotspot "
                                        "and show band, rung, IDF, RSSI, PHY")
    d.add_argument("--host-ip", default="auto")
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
