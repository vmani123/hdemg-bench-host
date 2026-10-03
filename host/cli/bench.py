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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench import parity, report, rungs                      # noqa: E402
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
        print(f"  !! {kw.get('chip')} not answering — attempting power cycle")
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
    drv = HardwareDriver(
        ips={"esp32s3": a.s3_ip, "esp32c5": a.c5_ip},
        firmware_root=a.firmware,
        ports={"esp32s3": a.s3_port, "esp32c5": a.c5_port},
        agent_dir=a.agent_dir,
        interactive_band=not a.unattended,
        hub_ports=hub)
    orc = Orchestrator(m, drv, Ledger(a.ledger),
                       rig_ceilings=_ceilings(a.ceilings),
                       parity_ok=par["ok"],
                       bands=[a.band] if a.band else None,
                       keepalive=True, recover=True, on_event=_progress)
    print(json.dumps(orc.run_all(limit=a.limit), indent=2))
    return 0


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
    r.add_argument("--firmware", default="../firmware")
    r.add_argument("--s3-ip", default="192.168.2.10")
    r.add_argument("--c5-ip", default="192.168.2.11")
    r.add_argument("--s3-port", default="cu.usbmodem1101")
    r.add_argument("--c5-port", default="cu.usbmodem1201")
    r.add_argument("--agent-dir", default="_agent")
    r.add_argument("--limit", type=int, default=None)
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
    r.set_defaults(fn=cmd_run)

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
