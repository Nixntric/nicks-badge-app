#!/usr/bin/env python3
"""Control the 44x11 LED name badge (USB 0416:5020, WCH CH583, "wang" protocol).

Images taller than they are wide-ish scroll their full height in up/down mode on
badges running our custom firmware (see firmware/); elsewhere they're shrunk to 11px.
That firmware also lights the empty space around images whose background is lit.

Examples:
  badge.py "Hello there"
  badge.py "Hi!" "@logo.png" --mode still,left --speed 6 --brightness 50
  badge.py "SALE" --blink --ants
  badge.py "Preview only" --preview
"""
import argparse
import datetime
import glob
import os
import re
import sys
import time

VID, PID = 0x0416, 0x5020
WIDTH, HEIGHT = 44, 11
MAX_MESSAGES = 8
MAX_PAYLOAD = 8192
TALL_MAX_HEIGHT = 1100  # 100 bands; a 44px-wide image this tall is 6.6 KB
TALL_PRODUCT = "Badge Magic Tall"  # USB product name of firmware that understands tall images

MODES = {
    "left": 0, "right": 1, "up": 2, "down": 3, "still": 4,
    "animation": 5, "drop": 6, "curtain": 7, "laser": 8,
}
BRIGHTNESS = {100: 0x00, 75: 0x10, 50: 0x20, 25: 0x40}

# Classic 5x7 font, ASCII 0x20-0x7E. Each glyph is 5 column bytes, bit 0 = top row.
FONT5X7 = bytes.fromhex(
    "0000000000" "00005f0000" "0007000700" "147f147f14" "242a7f2a12"
    "2313086462" "3649562050" "0000070000" "001c224100" "0041221c00"
    "2a1c7f1c2a" "08083e0808" "0050300000" "0808080808" "0060600000"
    "2010080402" "3e5149453e" "00427f4000" "4261514946" "2141454b31"
    "1814127f10" "2745454539" "3c4a494930" "0171090503" "3649494936"
    "064949291e" "0036360000" "0056360000" "0814224100" "1414141414"
    "0041221408" "0201510906" "3249794131" "7e1111117e" "7f49494936"
    "3e41414122" "7f4141221c" "7f49494941" "7f09090901" "3e4149497a"
    "7f0808087f" "00417f4100" "2040413f01" "7f08142241" "7f40404040"
    "7f020c027f" "7f0408107f" "3e4141413e" "7f09090906" "3e4151215e"
    "7f09192946" "4649494931" "01017f0101" "3f4040403f" "1f2040201f"
    "3f4038403f" "6314081463" "0708700807" "6151494543" "007f414100"
    "0204081020" "0041417f00" "0402010204" "4040404040" "0001020400"
    "2054545478" "7f48444438" "3844444420" "384444487f" "3854545418"
    "087e090102" "0c5252523e" "7f08040478" "00447d4000" "2040443d00"
    "7f10284400" "00417f4000" "7c04180478" "7c08040478" "3844444438"
    "7c14141408" "081414187c" "7c08040408" "4854545420" "043f444020"
    "3c4040207c" "1c2040201c" "3c4030403c" "4428102844" "0c5050503c"
    "4464544c44" "0008364100" "00007f0000" "0041360800" "0804081008"
)

# Small built-in icons, usable inline as :name:. Rows top to bottom, '#' = lit.
ICONS = {
    "heart": [
        "...........", "..##...##..", ".####.####.", "###########", "###########",
        ".#########.", "..#######..", "...#####...", "....###....", ".....#.....", "...........",
    ],
    "smile": [
        "...#####...", "..#.....#..", ".#.......#.", "#..#...#..#", "#.........#",
        "#.........#", "#..#...#..#", "#...###...#", ".#.......#.", "..#.....#..", "...#####...",
    ],
    "star": [
        ".....#.....", ".....#.....", "....###....", "###########", ".#########.",
        "..#######..", "...#####...", "..###.###..", "..##...##..", ".##.....##.", "...........",
    ],
}


class BadgeError(Exception):
    pass


class Bitmap:
    """A bitmap stored as a list of columns (each a list of `height` bools).

    Normally HEIGHT rows. Taller bitmaps are sent as several 11-row bands, which the
    custom firmware scrolls vertically.
    """

    def __init__(self, columns=None, height=HEIGHT):
        self.columns = columns or []
        self.height = height

    @property
    def width(self):
        return len(self.columns)

    @property
    def bands(self):
        return -(-self.height // HEIGHT)

    def __add__(self, other):
        assert self.height == other.height == HEIGHT, "only 11-row bitmaps can be joined"
        return Bitmap(self.columns + other.columns)

    def inverted(self):
        return Bitmap([[not v for v in c] for c in self.columns], self.height)

    def lit_background(self):
        """True if most edge pixels are lit, e.g. an image with dark parts lit on a light background."""
        if not self.columns:
            return False
        edge = [v for c in (self.columns[0], self.columns[-1]) for v in c]
        edge += [c[y] for c in self.columns for y in (0, self.height - 1)]
        return sum(edge) * 2 > len(edge)

    def trimmed(self):
        """Without blank columns at either side. The firmware centers images on their
        lit columns, so trimming makes it center them the way the preview does."""
        lit = [x for x, c in enumerate(self.columns) if any(c)]
        if not lit:
            return self
        return Bitmap(self.columns[lit[0]:lit[-1] + 1], self.height)

    @staticmethod
    def blank(width):
        return Bitmap([[False] * HEIGHT for _ in range(width)])

    def to_bytes(self):
        """Pack into the badge format: 8-pixel-wide strips, 11 bytes each (one per row, MSB left).

        Tall bitmaps are packed band by band, top band first, each band a full row of strips.
        """
        rows = self.bands * HEIGHT
        cols = [c + [False] * (rows - len(c)) for c in self.columns]
        cols += [[False] * rows] * (-self.width % 8)
        out = bytearray()
        for band in range(self.bands):
            for x0 in range(0, len(cols), 8):
                for y in range(band * HEIGHT, (band + 1) * HEIGHT):
                    byte = 0
                    for i in range(8):
                        byte = (byte << 1) | cols[x0 + i][y]
                    out.append(byte)
        return bytes(out)

    def ascii(self):
        return "\n".join(
            "".join("█" if c[y] else "·" for c in self.columns) for y in range(self.height)
        )


def icon_bitmap(name):
    rows = ICONS[name]
    return Bitmap([[rows[y][x] == "#" for y in range(HEIGHT)] for x in range(len(rows[0]))])


def text_bitmap_builtin(text):
    """Render with the built-in 5x7 font, vertically centered, 1px letter spacing."""
    top = (HEIGHT - 7) // 2
    cols = []
    for ch in text:
        code = ord(ch)
        if not 0x20 <= code <= 0x7E:
            code = ord("?")
        glyph = FONT5X7[(code - 0x20) * 5:(code - 0x20) * 5 + 5]
        if ch == " ":
            glyph = glyph[:3]
        for g in glyph:
            col = [False] * HEIGHT
            for bit in range(7):
                col[top + bit] = bool(g >> bit & 1)
            cols.append(col)
        cols.append([False] * HEIGHT)
    return Bitmap(cols[:-1] if cols else [])


def text_bitmap_ttf(text, font_path, size):
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(font_path, size)
    left, top, right, bottom = font.getbbox(text)
    img = Image.new("1", (max(right - left, 1), HEIGHT), 0)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"  # no antialiasing
    # Center the font's ascent+descent box (not this string's ink) so mixed messages line up.
    ascent, descent = font.getmetrics()
    draw.text((-left, (HEIGHT - ascent - descent) // 2), text, font=font, fill=1)
    return image_to_bitmap(img)


def image_bitmap(path, threshold, invert, tall=False):
    """Load an image scaled to 11px tall, or with tall=True, at most 44px wide and full height."""
    from PIL import Image

    img = Image.open(path).convert("L")
    if tall and img.height * WIDTH > HEIGHT * img.width:  # proportionally taller than the badge
        if img.width > WIDTH:
            img = img.resize((WIDTH, round(img.height * WIDTH / img.width)), Image.LANCZOS)
        if img.height > TALL_MAX_HEIGHT:
            img = img.crop((0, 0, img.width, TALL_MAX_HEIGHT))
    elif img.height != HEIGHT:
        img = img.resize((max(1, round(img.width * HEIGHT / img.height)), HEIGHT), Image.LANCZOS)
    img = img.point(lambda p: 255 if (p < threshold) != invert else 0).convert("1")
    return image_to_bitmap(img)


def image_to_bitmap(img):
    px = img.load()
    return Bitmap([[bool(px[x, y]) for y in range(img.height)] for x in range(img.width)],
                  height=img.height)


def render_message(msg, args, tall=False):
    """A message is text with optional inline :icon: tokens, or '@path' for an image file.

    tall: let images keep their height (for up/down scrolling on the custom firmware).
    """
    if msg.startswith("@") and os.path.isfile(msg[1:]):
        return image_bitmap(msg[1:], args.threshold, args.invert, tall)

    def render_text(t):
        if args.font:
            return text_bitmap_ttf(t, args.font, args.size)
        return text_bitmap_builtin(t)

    bmp = Bitmap()
    # re.split with a capture group alternates text, icon name, text, ...
    for i, part in enumerate(re.split(r":(" + "|".join(ICONS) + r"):", msg)):
        if i % 2:
            bmp += Bitmap.blank(1) + icon_bitmap(part) + Bitmap.blank(1)
        elif part:
            bmp += render_text(part)
    return bmp


# Marching border settings; the stock firmware always draws this one
BORDER_DEFAULT = {"on": 1, "gap": 3, "rows": 1, "cols": 1, "cw": False}
BORDER_LIMITS = {"on": (1, 8), "gap": (1, 8), "rows": (1, 4), "cols": (1, 4)}


def border_bytes(border):
    """(style, size) header values for the custom firmware; (0, 0) is its default."""
    b = dict(BORDER_DEFAULT, **(border or {}))
    style = 0
    if (b["on"], b["gap"], b["cw"]) != (1, 3, False):
        style = 0x80 | (b["on"] - 1) | (b["gap"] - 1) << 3 | bool(b["cw"]) << 6
    return style, (b["rows"] - 1) | (b["cols"] - 1) << 2


# Modes the firmware centers when the image is narrower than the display
CENTERED_MODES = ("up", "down", "still", "drop", "laser")


def prepare(bmp, mode, invert_ok):
    """The bitmap as sent, and whether it's sent inverted: see build_packet()."""
    inverted = invert_ok and bmp.lit_background()
    if inverted:
        bmp = bmp.inverted()
    if mode in CENTERED_MODES and bmp.trimmed().width <= WIDTH:
        bmp = bmp.trimmed()
    return bmp, inverted


def build_packet(bitmaps, modes, speeds, blink, ants, brightness, invert_ok=False, borders=None):
    """invert_ok: the badge runs our custom firmware, which can show a message inverted.
    Bitmaps with a lit background are then sent inverted with the invert flag, so the
    space around them (while scrolling, or beside narrow images) lights up too.
    borders: per-message marching border settings (see BORDER_DEFAULT), which only the
    custom firmware understands."""
    header = bytearray(64)
    header[0:4] = b"wang"
    header[5] = BRIGHTNESS[brightness]
    data = bytearray()
    for i, bmp in enumerate(bitmaps):
        if blink[i]:
            header[6] |= 1 << i
        if ants[i]:
            header[7] |= 1 << i
            style, size = border_bytes(borders[i]) if invert_ok and borders else (0, 0)
            if style or size:
                header[44:46] = b"TL"
                header[56 + i] = style
                header[32 + i // 2] |= size << (i % 2 * 4)
        header[8 + i] = ((speeds[i] - 1) << 4) | MODES[modes[i]]
        bmp, inverted = prepare(bmp, modes[i], invert_ok)
        if inverted:
            header[44:46] = b"TL"  # custom firmware extension, in bytes stock leaves zero
            header[46] |= 1 << i
        if bmp.bands > 1:
            header[44:46] = b"TL"
            header[48 + i] = bmp.bands
        chunk = bmp.to_bytes()
        strips = len(chunk) // HEIGHT
        header[16 + 2 * i] = strips >> 8
        header[17 + 2 * i] = strips & 0xFF
        data += chunk
    now = datetime.datetime.now()
    header[38:44] = bytes([now.year % 100, now.month, now.day, now.hour, now.minute, now.second])
    payload = bytes(header) + bytes(data)
    payload += bytes(-len(payload) % 64)
    if len(payload) > MAX_PAYLOAD:
        raise BadgeError(f"Too much content: {len(payload)} bytes (max {MAX_PAYLOAD}). Shorten the messages.")
    return payload


def find_device():
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(node, "device/uevent")) as f:
                if f"HID_ID=0003:{VID:08X}:{PID:08X}" in f.read().upper():
                    return "/dev/" + os.path.basename(node)
        except OSError:
            pass
    return None


def supports_tall(device):
    """True if the badge at this hidraw node runs firmware that scrolls tall images."""
    try:
        with open(f"/sys/class/hidraw/{os.path.basename(device)}/device/uevent") as f:
            return TALL_PRODUCT in f.read()
    except OSError:
        return False


def usb_serial(device):
    """USB serial number of the badge at this hidraw node, or "" if unknown."""
    path = os.path.realpath(f"/sys/class/hidraw/{os.path.basename(device)}/device")
    while path not in ("/", ""):
        if os.path.exists(os.path.join(path, "idVendor")):  # the USB device itself
            try:
                with open(os.path.join(path, "serial")) as f:
                    return f.read().strip()
            except OSError:
                return ""
        path = os.path.dirname(path)
    return ""


def badge_identity(device):
    """(key, firmware version) for the badge at this hidraw node.

    Our firmware puts the chip ID in its serial number ("BM1144-C fw: v1.3.0 id:..."),
    so each badge gets its own key. Stock firmware reports the same serial on every
    badge, so they all share the key "stock".
    """
    serial = usb_serial(device)
    chip = re.search(r"\bid:([0-9a-f]{12})\b", serial)
    version = re.search(r"fw: ?(\S+)", serial)
    return ("chip-" + chip.group(1) if chip else "stock"), (version.group(1) if version else "")


def send(payload, device):
    try:
        fd = os.open(device, os.O_WRONLY)
    except PermissionError:
        raise BadgeError(f"No permission to write {device}. Install the udev rule (see README.md) or run with sudo.")
    try:
        for i in range(0, len(payload), 64):
            # hidraw needs a leading report-ID byte; this device uses none, so 0.
            report = b"\x00" + payload[i:i + 64]
            try:
                os.write(fd, report)
            except OSError:
                # The badge occasionally rejects a transfer right after (re)connecting.
                time.sleep(0.2)
                try:
                    os.write(fd, report)
                except OSError as e:
                    raise BadgeError(f"USB write failed ({e.strerror}). Press the badge's lower button to wake it, then try again.")
    finally:
        os.close(fd)


def per_message(value, count, cast, name):
    """Expand a comma list to one value per message, repeating the last entry."""
    items = [cast(v) for v in str(value).split(",")]
    if len(items) > count:
        sys.exit(f"--{name}: got {len(items)} values for {count} message(s)")
    return items + [items[-1]] * (count - len(items))


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("messages", nargs="+", metavar="MESSAGE",
                   help="up to 8 messages; '@file.png' for an image; inline icons: "
                        + ", ".join(f":{n}:" for n in ICONS))
    p.add_argument("-m", "--mode", default="left",
                   help="per-message comma list of: " + ", ".join(MODES) + " (default: left)")
    p.add_argument("-s", "--speed", default="4", help="per-message comma list, 1-8 (default: 4)")
    p.add_argument("-b", "--brightness", type=int, choices=sorted(BRIGHTNESS), default=100)
    p.add_argument("--blink", default="0", nargs="?", const="1",
                   help="blink; bare flag for all, or comma list of 0/1 per message")
    p.add_argument("--ants", default="0", nargs="?", const="1",
                   help="marching-ants border; bare flag for all, or comma list of 0/1")
    p.add_argument("--font", help="TTF/OTF font file instead of the built-in 5x7 font")
    p.add_argument("--size", type=int, default=11, help="font size for --font (default: 11)")
    p.add_argument("--threshold", type=int, default=128, help="image threshold 0-255 (default: 128)")
    p.add_argument("--invert", action="store_true", help="invert images (light pixels lit)")
    p.add_argument("-d", "--device", help="hidraw device (default: auto-detect)")
    p.add_argument("-p", "--preview", action="store_true", help="print to the terminal, don't send")
    args = p.parse_args()

    n = len(args.messages)
    if n > MAX_MESSAGES:
        p.error(f"at most {MAX_MESSAGES} messages")
    modes = per_message(args.mode, n, str, "mode")
    for m in modes:
        if m not in MODES:
            p.error(f"unknown mode {m!r}; choose from {', '.join(MODES)}")
    speeds = per_message(args.speed, n, int, "speed")
    if not all(1 <= s <= 8 for s in speeds):
        p.error("speed must be 1-8")
    blink = per_message(args.blink, n, lambda v: v == "1", "blink")
    ants = per_message(args.ants, n, lambda v: v == "1", "ants")

    device = None if args.preview else (args.device or find_device())
    if not (device or args.preview):
        sys.exit(f"Badge not found (USB {VID:04x}:{PID:04x}). Is it plugged in?")
    tall_ok = args.preview or supports_tall(device)
    bitmaps = [render_message(m, args, tall=tall_ok and modes[i] in ("up", "down"))
               for i, m in enumerate(args.messages)]
    try:
        payload = build_packet(bitmaps, modes, speeds, blink, ants, args.brightness, invert_ok=tall_ok)
    except BadgeError as e:
        sys.exit(str(e))

    if args.preview:
        for i, bmp in enumerate(bitmaps):
            print(f"[{i + 1}] {modes[i]}, speed {speeds[i]}, {bmp.width}x{bmp.height}")
            print(bmp.ascii(), end="\n\n")
        print(f"{len(payload)} bytes")
        return

    try:
        send(payload, device)
    except BadgeError as e:
        sys.exit(str(e))
    print(f"Sent {n} message(s), {len(payload)} bytes, to {device}")


if __name__ == "__main__":
    main()
