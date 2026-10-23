#!/bin/bash
# Flash a B1133C badge as soon as it shows up in flash mode (WCH ISP).
# Usage: recover.sh <firmware.bin>, then hold the badge's lower key for 5 seconds
# (on Nick's badge firmware), or plug it in while holding it.
cd "$(dirname "$0")"
W=tools/wchisp-dl/wchisp-linux-x64/wchisp
[ -x "$W" ] || W=$(command -v wchisp) || { echo "wchisp not found: get it from github.com/ch32-rs/wchisp" >&2; exit 1; }
[ $# -eq 1 ] || { echo "Usage: $0 <firmware.bin>" >&2; exit 1; }
IMAGE=$1
# Check before waiting: entering flash mode erases the firmware's start, so a
# missing image leaves the badge stuck there
[ -f "$IMAGE" ] || { echo "No such image: $IMAGE" >&2; exit 1; }
echo "Waiting for the badge in flash mode (Ctrl+C to stop)..."
until lsusb -d 4348:55e0 >/dev/null; do sleep 0.2; done
sleep 1  # let it settle; flashing the instant it appears is when the erase failed
for try in 1 2 3 4 5; do
  if $W flash "$IMAGE"; then echo "Flashed OK."; exit 0; fi
  echo "Attempt $try failed, retrying..."; sleep 1
done
echo "Flashing failed." >&2; exit 1
