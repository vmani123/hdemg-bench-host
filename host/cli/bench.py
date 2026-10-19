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

from bench import linktest, parity, report, rfmeta, rungs    # noqa: E402
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


def _hardware(a, *, interactive_band: bool = False,
              ap: str = rfmeta.IPHONE_AP, ssid: str | None = None) -> HardwareDriver:
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
        tree=getattr(a, "tree", None), h7_port=getattr(a, "h7_port", DEFAULT_H7_PORT),
        ap=ap, ssid=ssid)


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
    # The time budget covers everything this command does, from its first line.
    deadline = time.monotonic() + a.max_seconds if a.max_seconds else None
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
    drv = _hardware(a, interactive_band=not a.unattended, ap=m.ap, ssid=m.ssid)
    if m.ssid:
        # Enforced before anything is built or flashed: this Mac on the named network,
        # and every board's firmware configured to join it.
        on = rfmeta.host_ssid()
        problems = []
        want_host = m.host_ssid or m.ssid
        if on != want_host:
            problems.append(f"this Mac is on Wi-Fi network {on!r}; join {want_host!r} first")
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
        print(f"network '{m.ssid}': every board's firmware is set to it; this Mac is on "
              f"'{want_host}'")
    print(f"access point '{m.ap}'  host ip {drv.host_ip()}  expected idf {m.expected_idf}")
    print(f"host link: {_describe_path(rfmeta.host_path(_gateway_of(drv.host_ip()), m.ap))}")
    for chip, h in sorted(drv.rediscover().items()):
        print(f"  found {chip} at {h['ip']}  band={h.get('band')} rung={h.get('rung')} "
              f"idf={h.get('idf')}")
    orc = Orchestrator(m, drv, Ledger(a.ledger),
                       rig_ceilings=_ceilings(a.ceilings), recv_port=a.recv_port,
                       parity_ok=par["ok"],
                       bands=[a.band] if a.band else None,
                       keepalive=True, recover=True, on_event=_progress,
                       allow_shared_band=a.allow_shared_band,
                       transports=[a.transport] if a.transport else None,
                       rssi_drift_warn_only=a.rssi_drift_warn,
                       deadline=deadline,
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
        if res.get("stopped_for_time"):
            print(f"time budget of {a.max_seconds} s reached: no further run would finish "
                  f"inside it; {res['remaining']} point(s) left for the next invocation",
                  flush=True)
            return 0 if res["remaining"] == 0 else 4
        if res["remaining"] == 0 or res["valid"] == 0 or a.limit:
            break
        if deadline is not None and time.monotonic() + 30.0 >= deadline:
            break
        time.sleep(30.0)
    return 0 if res.get("remaining") == 0 else 3


def cmd_linktest(a) -> int:
    """Stage 2 gate: prove the wire clean with Wi-Fi out of the loop (guide §7.2)."""
    drv = _hardware(a, ap=Matrix.load(a.matrix).ap)
    loads = [int(float(x) * 1e6) for x in a.loads.split(",")]
    opts = _link_opts(a.link_opt)
    print(f"link test: {a.chip} {a.link}  loads {a.loads} Mbit/s  hold {a.hold:g} s  "
          f"opts {opts or '{}'}", flush=True)
    if a.hold < linktest.GUIDE_HOLD_S:
        print(f"  note: hold is under the guide's {linktest.GUIDE_HOLD_S:g} s — a smoke "
              f"test, not the pass criterion", flush=True)
    try:
        res = linktest.run(drv, a.chip, a.link, loads, hold_s=a.hold, tune=a.tune,
                           band=a.band, link_opts=opts,
                           say=lambda m: print(m, flush=True))
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
    res = report.render(a.ledger, a.out, extra_ledgers=a.with_ledger)
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


IDF_DEFAULT = Path.home() / "Desktop" / "Research" / "esp-idf"    # hostrun.sh's IDF_EXPORT


def _first_port(prefix: str, fallback: str) -> str:
    found = sorted(p.name for p in Path("/dev").glob(prefix + "*"))
    return found[0] if found else fallback


def cmd_tune(a) -> int:
    """The max-throughput climber (bench/tune.py). Refuses early rather than spending a
    night on a study that cannot produce a number."""
    import os
    import shutil
    from bench import tune
    host = Path(__file__).resolve().parents[1]
    plan_path = Path(a.plan)
    plan = tune.Plan.load(plan_path)
    state_dir, ledger, rungs_root = host / plan.state_dir, host / plan.ledger, host / "rungs"
    if a.sim:
        # A rehearsal of the plumbing, not of the rig: short holds, and none of the rig
        # checks a fake ESP cannot pass (IDF version, network). The plan is not re-read
        # per step, so these overrides hold for the whole rehearsal.
        plan.hold_s, plan.discard_s = 1.0, 0.2
        plan.expected_idf = plan.ssid = None
        plan_path = None
        # Its own state, ledger and rung tree: a rehearsal must never leave rung files
        # whose parents disagree with the real study's.
        state_dir = Path(str(state_dir) + "-sim")
        ledger = ledger.with_name(ledger.stem + "-sim.jsonl")
        sim_rungs = state_dir / "rungs"
        if not sim_rungs.exists():
            shutil.copytree(rungs_root, sim_rungs)
        rungs_root = sim_rungs
    if a.status:
        summary = state_dir / "summary.md"
        print(summary.read_text() if summary.exists() else "no results yet")
        return 0
    if a.reopen:
        if not a.reason:
            print("--reopen needs --reason (it is recorded in the step)", file=sys.stderr)
            return 2
        st = state_dir / "state.json"
        climbs = json.loads(st.read_text())["climbs"] if st.exists() else {}
        want = a.chips.split(",") if a.chips else None
        keys = [k for k, c in climbs.items() if c["band"] == a.band
                and (not want or c["chip"] in want)
                and any(s["rung"] == a.reopen for s in c["steps"])]
        if len(keys) != 1:
            print(f"{a.reopen!r} matches {keys or 'no'} {a.band} GHz climb(s); name the "
                  f"chip with --chips", file=sys.stderr)
            return 2
        key = keys[0]
        try:
            tune.Engine(plan, band=a.band, rungs_root=rungs_root, state_dir=state_dir,
                        ledger=ledger, runner=None).reopen(key, a.reopen, a.reason)
        except tune.TuneError as e:
            print(f"not reopened: {e}", file=sys.stderr)
            return 2
        print(f"{key}: {a.reopen} reopened — its candidate is retried on the next run")
        return 0

    idf = Path(os.environ.get("IDF_PATH") or IDF_DEFAULT)
    if (idf / "components").is_dir():
        known = rungs.known_symbols(idf, host.parent / "firmware")
        bad = [f"{c.key}/{k.id}: {s}" for c in plan.climbs for k in c.candidates
               for s in k.config if s not in known]
        if bad:
            print("these candidate symbols are not defined by this ESP-IDF's Kconfig "
                  "(Kconfig would drop them silently):", file=sys.stderr)
            for b in bad:
                print(f"  - {b}", file=sys.stderr)
            return 2
    else:
        print(f"note: no ESP-IDF at {idf}; candidate symbols not pre-checked "
              f"(the post-build check still applies)")

    max_load = None
    if a.sim:
        runner = tune.SimRunner()
    else:
        hb = Path(a.agent_dir) / "heartbeat"
        if not hb.exists() or time.time() - hb.stat().st_mtime > 60:
            print(f"hostrun.sh is not running for this checkout (no fresh {hb}). Start it "
                  f"in its own Terminal from THIS checkout — "
                  f"bash {host.parent / 'hostrun.sh'} — and approve the flash window.",
                  file=sys.stderr)
            return 2
        max_load = _ceilings(a.ceilings).get(a.band)
        if not max_load:
            print(f"--ceilings must give this band's rig ceiling (e.g. {a.band}=60): no "
                  f"run is accepted above it, and the study never offers more",
                  file=sys.stderr)
            return 2
        runner = tune.HardwareRunner(
            band=a.band, ceilings=a.ceilings, agent_dir=a.agent_dir,
            s3_port=a.s3_port or _first_port("cu.usbmodem", "cu.usbmodem101"),
            c5_port=a.c5_port or _first_port("cu.usbserial-", "cu.usbserial-110"),
            s3_hub_port=a.s3_hub_port, c5_hub_port=a.c5_hub_port,
            allow_shared_band=a.allow_shared_band)
        print(f"rig ceiling {max_load:g} Mbit/s on {a.band} GHz: no load above it is offered")
    chips = a.chips.split(",") if a.chips else None
    eng = tune.Engine(plan, band=a.band, rungs_root=rungs_root, state_dir=state_dir,
                      ledger=ledger, runner=runner, plan_path=plan_path,
                      max_load_mbps=max_load, chips=chips, remeasure=a.remeasure)
    # A stop (kill, closed terminal) must unwind like Ctrl-C, so board locks are released
    # and the bench-run child is stopped rather than left to flash a released board.
    import signal
    from contextlib import ExitStack

    def _stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, _stop)
    try:
        with ExitStack() as held:
            if not a.sim:
                ports = {"esp32s3": runner.args[runner.args.index("--s3-port") + 1],
                         "esp32c5": runner.args[runner.args.index("--c5-port") + 1]}
                need = sorted({c.chip for c in plan.climbs if c.band == a.band
                               and (not chips or c.chip in chips)})
                for chip in need:
                    d = held.enter_context(tune.board_lock(
                        chip, ports[chip], owner=f"bench tune {plan.study} {a.band} GHz "
                        f"(throughput sweep, {host.parent})", expected_s=4500,
                        root=a.lock_dir))
                    print(f"holding {d} for this run")
            eng.run(max_steps=a.max_steps)
    except tune.TuneError as e:
        print(f"tune stopped: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("tune stopped by a signal; locks released. Re-run to resume.", file=sys.stderr)
        return 130
    print((state_dir / "summary.md").read_text())
    return 0


def _find_h7_port(default: str) -> str | None:
    """The STM32H745's ST-LINK serial port. The default name if it exists; otherwise ask
    `hello` of each cu.usbmodem<digits> port (the ST-LINK's location-numbered name). The
    S3's native-USB port is named after its MAC (letters and digits) and is never opened
    here: opening an ESP's USB serial port can reset it."""
    dev = sorted(p.name for p in Path("/dev").glob("cu.usbmodem*"))
    if default.removeprefix("/dev/") in dev:
        return default.removeprefix("/dev/")
    for d in dev:
        if not d.removeprefix("cu.usbmodem").isdigit():
            continue
        try:
            h = H7Control(d, timeout=1.0, retries=1)
            if h.hello().get("chip") == "stm32h745":
                h.close()
                return d
            h.close()
        except (H7Error, OSError):
            continue
    return None


def _bench_ports(h7_port: str | None) -> tuple[str | None, str | None]:
    """The S3 (native USB, cu.usbmodem*) and C5 (CP2102N, cu.usbserial-*) ports, skipping
    the H7's ST-LINK (also cu.usbmodem*) and the Digilent FPGA adapter, which are not
    boards and must never be flashed."""
    dev = sorted(p.name for p in Path("/dev").glob("cu.*"))
    h7 = (h7_port or "").removeprefix("/dev/")
    s3 = [d for d in dev if d.startswith("cu.usbmodem") and d != h7
          and not d.removeprefix("cu.usbmodem").isdigit()]
    c5 = [d for d in dev if d.startswith("cu.usbserial-") and "210352BF9796" not in d]
    return (s3[0] if s3 else None), (c5[0] if c5 else None)


def cmd_campaign(a) -> int:
    """The follow-up campaign (bench/campaign.py, docs/CAMPAIGN.md)."""
    import os
    import subprocess
    import yaml
    from contextlib import ExitStack
    from bench import campaign as cp, macnet, tune
    from bench.wiring import FakeWiring
    host = Path(__file__).resolve().parents[1]
    plan_path = Path(a.plan) if Path(a.plan).is_absolute() else host / a.plan
    cfg = yaml.safe_load(plan_path.read_text())
    only = _csv(a.only)
    if a.list:
        for t in cp.plan_for(cfg):
            print(f"{t.id:>4}  [{t.kind:<9}] {t.band or '-':>4}  ~{t.est_min:>3g} min  {t.title}")
        return 0
    if a.sim:
        drv = SimDriver(wiring=FakeWiring(faults=json.loads(a.sim_faults or "{}")))
        c = cp.Campaign(plan_path, driver=drv, host_dir=host, sim=True)
    else:
        drv = None
        c = cp.Campaign(plan_path, driver=None, host_dir=host)
    if a.status:
        print((c.state_dir / "summary.md").read_text() if (c.state_dir / "summary.md").exists()
              else "no results yet")
        return 0
    for tid in _csv(a.redo) or []:
        c.redo(tid)
        print(f"{tid} will be run again (round {c.st(tid)['round']})")
    if a.sim:
        try:
            return c.run(only=only, start=a.start, stop=a.stop, max_tests=a.max_tests,
                         until=_until(a.until))
        finally:
            drv.shutdown()
            print(f"summary: {c.state_dir / 'summary.md'}")

    par = parity.check(a.firmware)
    if not par["ok"]:
        print(parity.summary(par), file=sys.stderr)
        return 2
    hb = Path(a.agent_dir) / "heartbeat"
    if not hb.exists() or time.time() - hb.stat().st_mtime > 60:
        print(f"hostrun.sh is not running for this checkout (no fresh {hb}). Start it in its "
              f"own Terminal — bash {REPO / 'hostrun.sh'} — and approve the flash window.",
              file=sys.stderr)
        return 2
    h7_port = a.h7_port if a.h7_port != DEFAULT_H7_PORT else \
        (_find_h7_port(DEFAULT_H7_PORT) or DEFAULT_H7_PORT)
    a.h7_port = h7_port
    s3_auto, c5_auto = _bench_ports(h7_port)
    ports = {"esp32s3": a.s3_port or s3_auto, "esp32c5": a.c5_port or c5_auto}
    if not all(ports.values()):
        print(f"board ports not found ({ports}); pass --s3-port / --c5-port", file=sys.stderr)
        return 2
    drv = HardwareDriver(ips={"esp32s3": "auto", "esp32c5": "auto"},
                         firmware_root=a.firmware, ports=ports, agent_dir=a.agent_dir,
                         host_ip="auto", interactive_band=False, h7_port=a.h7_port,
                         ap=cfg["ap"], ssid=cfg["ssid"])
    drv.trust_running_build = True
    if cfg.get("rejoin_timeout_s"):
        # How long the ladder waits for a silent board to rejoin on its own before it
        # resets it. A board that locks up never does (19 Oct: the C5 after a TCP stall).
        drv.REJOIN_TIMEOUT_S = float(cfg["rejoin_timeout_s"])
    wrong = {chip: drv.board_ssid(chip) for chip in ports
             if drv.board_ssid(chip) != cfg["ssid"]}
    if wrong:
        print(f"the boards' firmware must join {cfg['ssid']!r}; sdkconfig.local says {wrong}",
              file=sys.stderr)
        return 2
    password = macnet.password_from_sdkconfig(Path(a.firmware) / "esp32s3" / "sdkconfig.local")
    c = cp.Campaign(plan_path, driver=drv, host_dir=host, mac=cp.RealMac(
        cfg["mac_networks"], password), agent_dir=a.agent_dir, parity_ok=True)
    print(f"boards: S3 {ports['esp32s3']}, C5 {ports['esp32c5']}, H7 {a.h7_port}")
    # Keep the Mac awake for as long as this process lives (display, idle, system, disk).
    awake = subprocess.Popen(["/usr/bin/caffeinate", "-dimsu", "-w", str(os.getpid())])
    import signal

    def _stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, _stop)
    try:
        with ExitStack() as held:
            for chip, port in ports.items():
                d = held.enter_context(tune.board_lock(
                    chip, port, owner=f"bench campaign {cfg.get('campaign')} ({REPO})",
                    expected_s=36 * 3600, root=a.lock_dir))
                print(f"holding {d}")
            until = _until(a.until)
            if until:
                print(f"no test starts that could not finish by "
                      f"{time.strftime('%H:%M', time.localtime(until))}")
            return c.run(only=only, start=a.start, stop=a.stop, max_tests=a.max_tests,
                         until=until)
    except tune.TuneError as e:
        print(f"campaign not started: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        c.save()
        print("campaign stopped by a signal; locks released. Re-run to resume.",
              file=sys.stderr)
        return 130
    finally:
        awake.terminate()


def _until(hhmm: str | None) -> float | None:
    """--until HH:MM as epoch seconds: the next time the clock reads HH:MM."""
    if not hhmm:
        return None
    h, m = (int(x) for x in hhmm.split(":"))
    now = time.localtime()
    t = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, h, m, 0, 0, 0, -1))
    return t if t > time.time() else t + 86400


def cmd_wiring(a) -> int:
    """The wiring hello world on its own: W.1 (all 18 wires, both ways) and 0.2 (boot)."""
    a.only = "W.1,0.2"
    if a.again:
        a.redo = "W.1,0.2"
    return cmd_campaign(a)


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
    r.add_argument("--max-seconds", type=int, default=None,
                   help="wall-clock budget for the whole command. No run (or the flash it "
                        "needs) is started unless it can finish inside it; exit code 4 "
                        "means the budget ended with points still pending")
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
    r.add_argument("--recv-port", type=int, default=3333,
                   help="port this host receives the stream on (two concurrent runs need "
                        "two ports)")
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
    lt.add_argument("--band", default=None, choices=["2.4", "5"],
                    help="build the ESP for this band, as the Stage 2 cells that follow "
                         "will be, so the build tested is the build measured")
    lt.add_argument("--matrix", default="matrices/stage2.yaml",
                    help="the matrix whose `ap:` names the rig")
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
    q.add_argument("--with-ledger", action="append", default=[], metavar="PATH",
                   help="also read this ledger (repeatable). Stage 2 keeps its own "
                        "ledger; add Stage 1's to see each wired cell beside its "
                        "synthetic number")
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

    t = sub.add_parser("tune", help="max-throughput study: climb one lever at a time "
                                    "(bench/tune.py, plan in tuning/)")
    t.add_argument("--plan", default="tuning/max-tput.yaml")
    t.add_argument("--band", required=True, choices=["2.4", "5"],
                   help="one band per invocation: the climbs for that band")
    t.add_argument("--ceilings", default=None, help='rig ceilings, e.g. "2.4=60,5=95"')
    t.add_argument("--sim", action="store_true",
                   help="rehearse against fake ESPs (separate state, ledger and rungs)")
    t.add_argument("--status", action="store_true", help="print the summary and exit")
    t.add_argument("--max-steps", type=int, default=None,
                   help="stop after this many measurements (a supervisor's checkpoint)")
    t.add_argument("--s3-port", default=None, help="default: first /dev/cu.usbmodem*")
    t.add_argument("--c5-port", default=None, help="default: first /dev/cu.usbserial-*")
    t.add_argument("--s3-hub-port", default=None)
    t.add_argument("--c5-hub-port", default=None)
    t.add_argument("--allow-shared-band", action="store_true",
                   help="the Mac shares the cell's band (numbers are rig-limited)")
    t.add_argument("--agent-dir", default=str(REPO / "_agent"),
                   help="hostrun.sh's directory; must be this checkout's")
    t.add_argument("--chips", default=None,
                   help="only these chips' climbs, e.g. esp32c5 (others are not touched)")
    t.add_argument("--lock-dir", default=str(Path.home()),
                   help="where shared-board locks live (~/.<chip>-<port>.lock)")
    t.add_argument("--remeasure", action="store_true",
                   help="measure each climb's current best again first (conditions changed)")
    t.add_argument("--reopen", default=None, metavar="RUNG",
                   help="withdraw a rejected/unmeasured step's decision (needs --reason)")
    t.add_argument("--reason", default=None)
    t.set_defaults(fn=cmd_tune)

    for name, fn, hlp in (("campaign", cmd_campaign, "the follow-up campaign "
                           "(docs/CAMPAIGN.md): every test, unattended, resumable"),
                          ("wiring", cmd_wiring, "wiring hello world: all 18 Stage 2 "
                           "wires both ways, then both ESPs' boot check")):
        cp_ = sub.add_parser(name, help=hlp)
        cp_.add_argument("--plan", default="campaign/followup.yaml")
        cp_.add_argument("--sim", action="store_true",
                         help="rehearse against fake boards and a fake harness")
        cp_.add_argument("--sim-faults", default=None,
                         help='wiring faults for --sim, JSON, e.g. \'{"open": [3]}\'')
        cp_.add_argument("--status", action="store_true", help="print the summary and exit")
        cp_.add_argument("--list", action="store_true", help="list the tests and exit")
        cp_.add_argument("--only", default=None, help="run only these test ids (comma list)")
        cp_.add_argument("--from", dest="start", default=None, help="first test id")
        cp_.add_argument("--to", dest="stop", default=None, help="last test id")
        cp_.add_argument("--redo", default=None,
                         help="run these done tests again (comma list; new repeats)")
        cp_.add_argument("--max-tests", type=int, default=None)
        cp_.add_argument("--until", default=None,
                         help="HH:MM: start no test that could not finish by then")
        cp_.add_argument("--s3-port", default=None, help="default: the cu.usbmodem* that "
                                                         "is not the H7")
        cp_.add_argument("--c5-port", default=None, help="default: first cu.usbserial-*")
        cp_.add_argument("--h7-port", default=DEFAULT_H7_PORT)
        cp_.add_argument("--firmware", default=str(REPO / "firmware"))
        cp_.add_argument("--agent-dir", default=str(REPO / "_agent"))
        cp_.add_argument("--lock-dir", default=str(Path.home()))
        if name == "wiring":
            cp_.add_argument("--again", action="store_true",
                             help="re-run the check even if it already passed")
        cp_.set_defaults(fn=fn)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
