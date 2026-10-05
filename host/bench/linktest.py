"""Link-only test (docs/STM32_STAGE2_GUIDE.md §7.2).

Before any Stage 2 Wi-Fi number is taken, the wire has to be shown clean ON ITS OWN:
the generator offers a paced load, the ESP verifies every buffer in full — length, magic
at every stride, seq continuity, and the payload regenerated from (seq, seed) — and then
DROPS the data instead of sending it. Wi-Fi carries nothing but the control plane, so a
link fault cannot masquerade as a radio result.

The guide's pass criterion is zero errors for 10 minutes at each offered load. A shorter
hold is a smoke test, and is reported as such.
"""
from __future__ import annotations
import random
import time

from .h7 import H7Error

GUIDE_HOLD_S = 600.0
FRAME_BYTES = 270
MAX_BATCH_FRAMES = 30


def verdict(h7: dict, esp: dict, seed: int) -> tuple[bool, list[str], list[str]]:
    """(passed, failures, notes) for one load step."""
    f: list[str] = []
    notes: list[str] = []
    if not h7:
        f.append("no summary from the H7")
    if not esp:
        f.append("no ingress counters from the ESP")
    if f:
        return False, f, notes

    if not esp.get("link_test"):
        f.append("the ESP was not in link-test mode — the payload was not verified")
    elif int(esp.get("seed", -1)) != int(seed):
        f.append(f"the ESP verified against seed {esp.get('seed')}, not {seed}")
    if esp.get("init_err"):
        f.append(f"ESP ingress link failed to initialise (err {esp['init_err']})")
    for k in ("len_errors", "magic_errors", "seq_backwards", "payload_errors"):
        if esp.get(k, 0):
            f.append(f"ESP {k}={esp[k]}")
    for k in ("link_errors", "credit_errors"):
        if h7.get(k, 0):
            f.append(f"H7 {k}={h7[k]}")

    sent = int(h7.get("link_bytes", 0)) // FRAME_BYTES
    got = int(esp.get("frames_in", 0))
    if sent == 0 or got == 0:
        f.append(f"nothing crossed the link (H7 sent {sent} frames, ESP verified {got})")
    elif sent != got:
        f.append(f"H7 sent {sent} frames, ESP verified {got}: {sent - got} unaccounted for")
    drops, gaps = int(h7.get("ring_drops", 0)), int(esp.get("seq_gaps", 0))
    if gaps > drops:
        f.append(f"{gaps - drops} frames lost on the link")

    cmd, ach = float(h7.get("commanded_bps", 0)), float(h7.get("achieved_bps", 0))
    if cmd and abs(ach - cmd) / cmd > 0.01:
        f.append(f"generator pacing off: {ach / 1e6:.2f} vs {cmd / 1e6:.2f} Mbit/s commanded")
    if drops:
        notes.append(f"link could not carry this load: {drops} of {h7.get('frames')} frames "
                     f"dropped at the H7 ring ({100.0 * drops / max(1, int(h7.get('frames', 1))):.2f}%)")
    if h7.get("ready_timeouts", 0):
        notes.append(f"{h7['ready_timeouts']} credit waits over 10 ms")
    if h7.get("torn_reads", 0):
        notes.append(f"{h7['torn_reads']} torn counter reads (re-read, harmless)")
    return not f, f, notes


def run(driver, chip: str, link: str, loads_bps: list[int], *, hold_s: float = GUIDE_HOLD_S,
        tune: str = "tuned", link_opts: dict | None = None, recv_port: int = 3333,
        say=print, sleep=time.sleep, rng: random.Random | None = None) -> dict:
    """Run the link test at each load. Never raises for a link that fails: the result
    says so. Raises only if the boards cannot be built, flashed or reached."""
    link_opts = dict(link_opts or {})
    rng = rng or random.Random()
    driver.ensure_flashed(chip, link, tune)
    driver.ensure_h7()
    h7, c = driver.h7(), driver.control(chip)
    hello_esp, hello_h7 = c.hello(), h7.hello()
    steps: list[dict] = []

    for load in loads_bps:
        seed = rng.randrange(1, 2 ** 32)
        step = {"offered_bps": int(load), "seed": seed, "hold_s": hold_s}
        try:
            h7.cfg(link=link, rate_bps=int(load), dur_s=int(hold_s) + 5, seed=seed,
                   frame_bytes=FRAME_BYTES, **link_opts)
            h7.probe()
            c.cfg(rate_bps=int(load), transport="udp", dst=f"{driver.host_ip()}:{recv_port}",
                  dur_s=int(hold_s) + 8, frame_bytes=FRAME_BYTES, payload=f"lt:{seed}",
                  label="linktest")
            c.start()
            h7.start()
        except (H7Error, OSError, RuntimeError) as e:
            try:
                c.stop()
            except Exception:                                 # noqa: BLE001
                pass
            step.update(passed=False, failures=[f"could not start: {e}"], notes=[],
                        h7={}, esp={})
            steps.append(step)
            say(f"  {chip} {link} {load / 1e6:5.1f} Mbit/s  FAIL  could not start: {e}")
            break                       # nothing after this load can pass either

        # Hold, looking in every few seconds so a link that is already broken costs
        # seconds rather than the full ten minutes.
        t_end = time.monotonic() + hold_s
        aborted = None
        while time.monotonic() < t_end:
            sleep(min(5.0, max(0.05, t_end - time.monotonic())))
            try:
                live = driver.ingress_stat(chip)
            except (OSError, ValueError):
                continue
            bad = [k for k in ("len_errors", "magic_errors", "seq_backwards", "payload_errors")
                   if live.get(k, 0)]
            if bad:
                aborted = "errors seen mid-run: " + ", ".join(f"{k}={live[k]}" for k in bad)
                break

        try:
            h7_sum = h7.stop()
        except H7Error:
            h7_sum = {}
        try:
            c.stop()
        except Exception:                                     # noqa: BLE001
            pass
        try:
            esp = driver.ingress_stat(chip)
        except (OSError, ValueError):
            esp = {}
        ok, failures, notes = verdict(h7_sum, esp, seed)
        if aborted:
            notes.append(f"stopped early ({aborted})")
        step.update(passed=ok, failures=failures, notes=notes, h7=h7_sum, esp=esp)
        steps.append(step)
        say(f"  {chip} {link} {load / 1e6:5.1f} Mbit/s  {'pass' if ok else 'FAIL'}  "
            f"frames={esp.get('frames_in')} link={float(h7_sum.get('link_bps', 0)) / 1e6:.2f} "
            f"Mbit/s drops={h7_sum.get('ring_drops')}"
            + ("  " + "; ".join(failures) if failures else "")
            + ("  (" + "; ".join(notes) + ")" if notes else ""))
        if not ok:
            break

    passed = bool(steps) and len(steps) == len(loads_bps) and all(s["passed"] for s in steps)
    return {
        "chip": chip, "link": link, "tune": tune, "link_opts": link_opts,
        "hold_s": hold_s, "meets_guide_hold": hold_s >= GUIDE_HOLD_S,
        "passed": passed, "steps": steps,
        "esp": {k: hello_esp.get(k) for k in ("fw_sha", "rung", "ingress", "idf", "band")},
        "h7": {k: hello_h7.get(k) for k in ("fw_sha", "sysclk_hz")},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
