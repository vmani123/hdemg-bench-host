#!/usr/bin/env python3
"""Boot check for one ESP (hostrun.sh action esp_boot; runs in ESP-IDF's Python).

1. esptool connects through the ROM bootloader and reads the MAC: proof that the board
   can still be FLASHED with the Stage 2 harness attached (wrong levels on strapping
   pins can stop it entering download mode).
2. Then, with the port held open, the board is reset into its application by the
   usual RTS pulse and its console is read for --secs seconds. The ROM's first line
   says how it booted: "boot:0x.. (SPI_FAST_FLASH_BOOT)" is normal; "(DOWNLOAD...)"
   means a strapping pin held it in the bootloader.

Prints everything it sees, then one summary line:
  === boot: esptool=ok rom_line=yes mode=SPI_FAST_FLASH_BOOT
A port that disappears for a moment (a native-USB board re-enumerating after its reset)
is reopened; the ROM line may be lost then, and mode is reported as "unseen".
"""
import argparse
import re
import subprocess
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chip", required=True)
    ap.add_argument("--port", required=True)
    ap.add_argument("--secs", type=float, default=12.0)
    a = ap.parse_args()

    r = subprocess.run([sys.executable, "-m", "esptool", "--chip", a.chip, "-p", a.port,
                        "--after", "no-reset", "read-mac"],
                       capture_output=True, text=True, timeout=90)
    print(r.stdout + r.stderr, flush=True)
    esptool_ok = r.returncode == 0

    import serial                                    # pyserial ships with ESP-IDF
    seen = []
    deadline = time.time() + a.secs
    ser = None
    reset_done = False
    while time.time() < deadline:
        if ser is None:
            try:
                ser = serial.Serial()
                ser.port, ser.baudrate, ser.timeout = a.port, 115200, 0.2
                ser.dtr = False                      # keep IO0 high: a normal boot
                ser.rts = False
                ser.open()
            except (serial.SerialException, OSError):
                ser = None
                time.sleep(0.3)
                continue
            if not reset_done:
                ser.rts = True                       # EN low ...
                time.sleep(0.12)
                ser.rts = False                      # ... and released: boot the app
                reset_done = True
        try:
            line = ser.readline()
        except (serial.SerialException, OSError):
            try:
                ser.close()
            except Exception:                        # noqa: BLE001
                pass
            ser = None
            time.sleep(0.3)
            continue
        if line:
            text = line.decode("utf-8", "replace").rstrip()
            seen.append(text)
            print(text, flush=True)
    if ser is not None:
        ser.close()

    rom = next((t for t in seen if "boot:0x" in t), None)
    m = re.search(r"boot:0x[0-9a-fA-F]+\s*\(([^)]*)\)", rom or "")
    mode = m.group(1).replace(" ", "_") if m else "unseen"
    print(f"=== boot: esptool={'ok' if esptool_ok else 'FAILED'} "
          f"rom_line={'yes' if rom else 'no'} mode={mode}", flush=True)
    return 0 if esptool_ok and "DOWNLOAD" not in mode else 1


if __name__ == "__main__":
    raise SystemExit(main())
