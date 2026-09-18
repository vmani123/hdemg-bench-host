#!/usr/bin/env python3
"""Turn a rung YAML (plus its parent chain) into an sdkconfig fragment.

Called by hostrun.sh at build time. The host repo owns the ladders; the firmware
repos hold no tuning state of their own, so a rung cannot silently be applied to one
chip and not the other.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.rungs import Ladder     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rung", required=True, help="path to the rung yaml")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    rung_path = Path(a.rung)
    chip_dir = rung_path.parent                     # rungs/s3 or rungs/c5
    chip = {"s3": "esp32s3", "c5": "esp32c5"}[chip_dir.name]
    lad = Ladder.load(chip_dir.parent, chip)
    cfg = lad.resolve(rung_path.stem)

    lines = [f"# generated from {rung_path} — do not edit by hand",
             f"CONFIG_IDF_TARGET=\"{chip}\""]
    for k, v in sorted(cfg.items()):
        if v is True or v == "y":
            lines.append(f"{k}=y")
        elif v is False or v == "n":
            lines.append(f"{k}=n")
        elif isinstance(v, int):
            lines.append(f"{k}={v}")
        else:
            lines.append(f'{k}="{v}"')
    Path(a.out).write_text("\n".join(lines) + "\n")
    print(f"wrote {a.out}: {len(cfg)} symbols from rung {rung_path.stem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
