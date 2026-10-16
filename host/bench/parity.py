"""Shared-core integrity check.

In the three-repo layout this compared two copies of the measurement core byte for byte.
In the monorepo there is only ONE copy — `firmware/bench_common/` — so that class of
drift is impossible by construction, which is stronger than any checker.

What remains checkable is the way that guarantee gets broken in a monorepo: someone
copies `pipe.c` into `firmware/esp32s3/main/` to "just tweak it for the S3", or a target
stops pointing at the shared component. Either would reintroduce exactly the failure the
shared component exists to prevent — two measurement cores that look like one — and
neither shows up in the data.

So this checks:
  1. every target's CMakeLists points at the shared component;
  2. no target's `main/` shadows a contract file name;
  3. the control-protocol version is defined exactly once;
  4. one ESP-IDF version for both targets (the toolchain is environment, not tuning).

It deliberately does NOT compare `sdkconfig`. Each chip is tuned independently for
maximum performance; that divergence is the experiment (plan §5.2).
"""
from __future__ import annotations
import re
from pathlib import Path

COMMON_DIR = "bench_common"
TARGETS = ("esp32s3", "esp32c5")

# Names that may exist only inside the shared component.
CONTRACT_NAMES = (
    "pipe.c", "pipe.h", "control.c", "control.h", "hdemg_frame.h",
    "pacing.c", "pacing.h", "instr.c", "instr.h",
    "source_synth.c", "sink_tcp.c", "sink_udp.c",
)

PROTO_RE = re.compile(r"#define\s+CONTROL_PROTOCOL_VERSION\s+(\d+)")


def check(firmware_root: str | Path) -> dict:
    root = Path(firmware_root)
    if root.name != "firmware" and (root / "firmware").is_dir():
        root = root / "firmware"

    problems: list[str] = []
    common = root / COMMON_DIR
    if not common.is_dir():
        return {"ok": False, "problems": [f"shared component missing: {common}"],
                "targets": [], "proto": None, "idf": None}

    present = sorted(p.name for p in common.rglob("*") if p.is_file())
    missing = [n for n in CONTRACT_NAMES if n not in present]
    if missing:
        problems.append(f"shared component is missing contract files: {missing}")

    targets_seen = []
    for t in TARGETS:
        tdir = root / t
        if not tdir.is_dir():
            continue
        targets_seen.append(t)

        cml = tdir / "CMakeLists.txt"
        if not cml.exists() or COMMON_DIR not in cml.read_text():
            problems.append(f"{t}/CMakeLists.txt does not point at {COMMON_DIR} — this "
                            f"target is not using the shared measurement core")

        for p in (tdir / "main").rglob("*"):
            if p.is_file() and p.name in CONTRACT_NAMES:
                problems.append(f"{t}/main/{p.name} shadows a contract file — the "
                                f"measurement core must exist in exactly one place")

    # The protocol version must be defined once, in the shared component.
    # Only the ESP trees: firmware/ also holds the STM32 project and megabytes of vendor
    # headers, which are neither part of this contract nor guaranteed to be UTF-8.
    esp_dirs = [common] + [root / t for t in TARGETS if (root / t).is_dir()]
    defs = [p for d in esp_dirs for p in d.rglob("*.h")
            if "build" not in p.relative_to(d).parts
            and "managed_components" not in p.relative_to(d).parts
            and PROTO_RE.search(p.read_text(errors="replace"))]
    proto = None
    if not defs:
        problems.append("CONTROL_PROTOCOL_VERSION is not defined anywhere")
    elif len(defs) > 1:
        problems.append(f"CONTROL_PROTOCOL_VERSION defined in {len(defs)} files: "
                        f"{[str(d.relative_to(root)) for d in defs]}")
    else:
        m = PROTO_RE.search(defs[0].read_text())
        proto = int(m.group(1))
        if COMMON_DIR not in str(defs[0]):
            problems.append(f"CONTROL_PROTOCOL_VERSION is defined outside "
                            f"{COMMON_DIR}: {defs[0].relative_to(root)}")

    idf_file = root / ".idf-version"
    idf = idf_file.read_text().strip() if idf_file.exists() else None
    if not idf:
        problems.append("firmware/.idf-version is missing — one IDF version must be "
                        "pinned for both targets, and recorded in every run")

    return {"ok": not problems, "problems": problems, "targets": targets_seen,
            "proto": proto, "idf": idf, "shared_files": present}


def summary(result: dict) -> str:
    if result["ok"]:
        return (f"parity OK — one shared measurement core, "
                f"targets {result['targets']}, protocol v{result['proto']}, "
                f"IDF {result['idf']}")
    return "PARITY FAILED —\n  " + "\n  ".join(result["problems"])
