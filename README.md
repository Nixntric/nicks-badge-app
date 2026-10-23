# Nick's Badge

A desktop app and command-line tool for the 44×11 LED name badge (USB `0416:5020`, WCH
CH582). It works with the badge's stock firmware, and does much more with
[Nick's badge firmware](https://github.com/Nixntric/nicks-badge-firmware).

## Install

Requirements:
- Linux with Python 3.
- GTK 4 and libadwaita 1.5+ (on Debian/Ubuntu: `python3-gi gir1.2-adw-1`).
- [Pillow](https://pypi.org/project/pillow/) (`python3-pil`), for images.

    git clone https://github.com/Nixntric/nicks-badge-app
    cd nicks-badge-app
    ./install.sh

`install.sh` adds **Nick's Badge** to your app menu. It also installs a udev rule, which
asks for your password, so your user can talk to the badge and to its flash mode.
Unplug and replug the badge afterwards.

To update firmware from the app you also need [wchisp](https://github.com/ch32-rs/wchisp)
on your PATH.

## The app

Run it from the app menu, or with `./badge_gui.py`. Ctrl+Enter sends.

- **Live preview:** the badge drawn LED by LED. It plays every effect, the marching
  border and inversion the way the firmware does.
- **Messages:** up to 8, each with an effect (scroll left, right, up or down, still,
  animation, drop in, curtain, laser), a speed of 1 to 8, blink, and a marching border.
  With Nick's firmware the border has its own dash length, gap, thickness and direction.
- **Text:** the built-in 5×7 font, with icons: `:heart:` `:smile:` `:star:`.
- **Images:** click the image button on a message, or drop a picture or animated GIF onto
  it. Its **Image settings** stay in the message: size and crop, threshold, dithering,
  inverting, auto contrast, and for GIFs the clip and frame count, plus a "Real Speed"
  button. Change one and the original converts again. Converted images are stored in
  `~/.config/nametag/images/`.
- **Tall images** (with Nick's firmware): use "Fit width, full height" and the image
  scrolls its whole height in up/down mode. Images with a light background are shown
  inverted, so the whole display lights up around them.
- **Several badges:** each badge you plug in gets a name and its own messages,
  brightness and preview colour. Badges on Nick's firmware report a unique chip ID.
  Badges on stock firmware can't be told apart, so they share one profile.
- **Update Firmware** (button at the top left): drop in a firmware `.bin`, or open one,
  and press **Arm Firmware Launcher**. Then put the badge in flash mode: on Nick's
  firmware, hold the lower key for 5 seconds. The app waits for the badge, checks the
  chip is a CH582, and flashes it with retries.

Messages and badges are saved in `~/.config/nametag/messages.json`.

## Command line

`badge.py` sends straight from a terminal, and needs nothing beyond Python (plus Pillow
for images and fonts):

    ./badge.py "Hello there"                         # scroll left
    ./badge.py "Hi" --mode still                     # static, centered
    ./badge.py "Hi :heart:" "@logo.png" -m left,still -s 6,4
    ./badge.py "SALE" --blink --ants -b 50
    ./badge.py "Hi" --font /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf --size 13
    ./badge.py "anything" --preview                  # ASCII preview, no badge needed

Options per message (`--mode`, `--speed`, `--blink`, `--ants`) take comma lists, and the
last value repeats. Images are scaled to 11 px tall, and dark pixels light up
(`--invert` flips that).

If a send fails with "Protocol error", the badge is asleep: press its lower key and try
again.

## Firmware tools

- `firmware/recover.sh <firmware.bin>` is the command-line version of the app's firmware
  updater. It waits for a badge in flash mode, then flashes it.
- `firmware/diag.py` reads and changes display refresh settings live, on Nick's firmware.

## Limits

- Uploads are about 8 KB in total, so animations get at most 123 frames.
- Animation frames change about every 0.65 s at speed 4, and about every 0.23 s at
  speed 8.

## License

Apache-2.0. The preview effects are ported from FOSSASIA's Badge Magic app; see NOTICE.
