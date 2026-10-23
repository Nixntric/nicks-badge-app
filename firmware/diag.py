#!/usr/bin/env python3
"""Read LED refresh timing from the badge's custom firmware, and change refresh settings live.

  diag.py                 report settings and interrupt timing (stats reset on each read)
  diag.py --hz 8000       set the refresh interrupt rate (88 interrupts per full refresh)
  diag.py --park mid      park LED pins between columns: float, pulldown or mid
  diag.py --strong 5      20 mA drive for columns with more than this many lit LEDs
  diag.py --brightness 0  0-3; how many of the 4 slots per column stay lit, minus one
  diag.py --comp 50       0-100; shorten columns with few lit LEDs to even out brightness

Settings last until the badge restarts.
"""
import argparse
import os
import select
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import badge  # noqa: E402

FREQ_SYS = 60_000_000
PARK_MODES = ["float", "pulldown", "mid"]
FIELDS = ["hz", "park", "strong", "brightness", "period", "count", "overruns",
          "lit_entry", "lit_exit", "idle_entry", "idle_exit", "comp", "early"]


def command(fd, op, key=0, value=0):
    packet = bytearray(64)
    packet[0:6] = b"DIAG" + op + bytes([key])
    packet[6:10] = struct.pack("<I", value)
    packet[62:64] = b"\xa6\xd1"
    os.read(fd, 64) if select.select([fd], [], [], 0)[0] else None  # drop a stale report
    os.write(fd, b"\x00" + bytes(packet))
    # The badge polls at bInterval 255 ms; wait for the reply.
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if select.select([fd], [], [], 0.1)[0]:
            reply = os.read(fd, 64)
            if reply[:5] == b"DIAGr":
                return dict(zip(FIELDS, struct.unpack_from(f"<{len(FIELDS)}I", reply, 8)))
    sys.exit("No reply. Is the badge running the diagnostic firmware?")


def show(r, seconds):
    us = lambda cycles: f"{cycles * 1e6 / FREQ_SYS:6.1f} µs"  # noqa: E731
    period = r["period"]
    print(f"refresh interrupt {r['hz']} Hz (period {us(period).strip()}), "
          f"~{r['hz'] / 88:.0f} Hz full refresh")
    print(f"park {PARK_MODES[r['park']] if r['park'] < 3 else r['park']}, "
          f"strong drive above {r['strong']} lit, brightness {r['brightness']}, "
          f"evening {r['comp']}%")
    if r["count"]:
        expected = r["hz"] * seconds
        print(f"interrupts: {r['count']} in {seconds:.1f} s (expected ~{expected:.0f}), "
              f"overruns {r['overruns']}")
        print(f"  lighting a column: started up to {us(r['lit_entry'])} late, "
              f"finished by {us(r['lit_exit'])}")
        print(f"  other slots:       started up to {us(r['idle_entry'])} late, "
              f"finished by {us(r['idle_exit'])}")
        columns = r["count"] // 4
        print(f"columns released early for evening: {r['early']} of ~{columns}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hz", type=int)
    p.add_argument("--park", choices=PARK_MODES)
    p.add_argument("--strong", type=int)
    p.add_argument("--brightness", type=int, choices=range(4))
    p.add_argument("--comp", type=int, choices=range(101), metavar="0-100")
    p.add_argument("--window", type=float, default=2.0, help="seconds to collect stats (default 2)")
    args = p.parse_args()

    device = badge.find_device()
    if not device:
        sys.exit("Badge not found.")
    fd = os.open(device, os.O_RDWR)
    try:
        for key, value in ((1, args.hz), (2, args.park and PARK_MODES.index(args.park)),
                           (3, args.strong), (4, args.brightness), (6, args.comp)):
            if value is not None:
                command(fd, b"S", key, value)
        command(fd, b"R")  # reset stats
        time.sleep(args.window)
        start = time.monotonic()
        report = command(fd, b"R")
        show(report, args.window + time.monotonic() - start)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
