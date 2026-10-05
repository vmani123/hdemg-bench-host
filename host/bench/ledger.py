"""Append-only measurement ledger (spec §4, CONTRACTS.md §4).

The ledger is the only thing the report reads, and the only state the orchestrator
resumes from. A record that does not carry its provenance is rejected at write time
rather than discovered to be unusable at analysis time.
"""
from __future__ import annotations
import json
import os
import subprocess
import time
from pathlib import Path

REQUIRED = (
    "run_id", "timestamp", "git_sha", "target", "variant", "source",
    "offered_bps", "link", "transport", "duration_s", "metrics", "rf",
    "cell_id", "repeat", "rung", "fw_sha", "parity",
)
REQUIRED_RF = (
    "band", "channel", "rssi_dbm", "idf_version", "tx_power", "power_save",
    "phy_rate", "ap", "mac_link", "shielded", "rig_ceiling_mbps",
)
REQUIRED_METRICS = ("goodput_bps", "frames", "loss_pct")


class SchemaError(ValueError):
    pass


def validate(rec: dict) -> None:
    missing = [k for k in REQUIRED if k not in rec]
    if missing:
        raise SchemaError(f"record missing required keys: {missing}")
    if not isinstance(rec["rf"], dict):
        raise SchemaError("rf must be an object")
    missing_rf = [k for k in REQUIRED_RF if k not in rec["rf"]]
    if missing_rf:
        raise SchemaError(f"rf missing required keys: {missing_rf}")
    missing_m = [k for k in REQUIRED_METRICS if k not in rec["metrics"]]
    if missing_m:
        raise SchemaError(f"metrics missing required keys: {missing_m}")
    if rec["target"] not in ("esp32s3", "esp32c5"):
        raise SchemaError(f"unknown target {rec['target']!r}")
    if rec["transport"] not in ("udp", "tcp"):
        raise SchemaError(f"unknown transport {rec['transport']!r}")


def run_key(rec: dict) -> tuple:
    """Identity of a measurement point, for resume."""
    return (rec["cell_id"], int(rec["offered_bps"]), int(rec["repeat"]))


class Ledger:
    def __init__(self, path: str | os.PathLike = "results/runs.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, rec: dict) -> dict:
        validate(rec)
        with self.path.open("a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        return rec

    def read(self, *, skip_bad: bool = True) -> list[dict]:
        """Read all records. A truncated final line (killed mid-write) is skipped, which
        is what makes resume safe after a hard stop.

        VOID MARKERS. The ledger is append-only, so a run found to be wrong after the
        fact is never edited or deleted: a marker line naming its run_id is appended
        (see void()). Here every voided run comes back with valid=False and the reason
        among its gate_failures, so it leaves every median and counts as not done —
        while the original line, and the reason it was withdrawn, stay on the record."""
        out: list[dict] = []
        voided: dict[str, str] = {}
        if not self.path.exists():
            return out
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict) and "void" in rec and "cell_id" not in rec:
                    for rid in rec.get("void") or []:
                        voided[str(rid)] = str(rec.get("reason") or "voided")
                    continue
                validate(rec)
            except (json.JSONDecodeError, SchemaError):
                if skip_bad:
                    continue
                raise
            out.append(rec)
        for rec in out:
            why = voided.get(str(rec.get("run_id")))
            if why:
                rec["voided"] = why
                rec["valid"] = False
                rec["gate_failures"] = list(rec.get("gate_failures") or []) + [f"voided: {why}"]
        return out

    def void(self, run_ids, reason: str) -> int:
        """Withdraw recorded runs by appending a marker (nothing is rewritten). Returns
        how many run ids the marker names. Readers that predate markers skip the line."""
        ids = sorted({str(r) for r in run_ids})
        if not ids:
            return 0
        marker = {"void": ids, "reason": reason, "valid": False,
                  "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        with self.path.open("a") as f:
            f.write(json.dumps(marker, sort_keys=True) + "\n")
        return len(ids)

    def completed_keys(self) -> set[tuple]:
        """Keys of runs that count as done: present and not marked invalid."""
        return {run_key(r) for r in self.read() if r.get("valid", True)}


def git_sha(repo: str | os.PathLike = ".") -> str:
    try:
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        if not sha:
            return "nogit"
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "nogit"


def new_run_id(cell_id: str, offered_bps: int, repeat: int) -> str:
    return f"{cell_id}__{int(offered_bps)//1000}k__r{repeat}__{int(time.time())}"
