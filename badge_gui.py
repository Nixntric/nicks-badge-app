#!/usr/bin/env python3
"""GTK4/libadwaita front end for badge.py."""
import json
import math
import os
import sys
import time
from types import SimpleNamespace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badge  # noqa: E402
import effects  # noqa: E402
import imageconv  # noqa: E402
from flasher import FirmwareDialog  # noqa: E402

APP_ID = "dev.nick.Nametag"
STATE_FILE = os.path.join(GLib.get_user_config_dir(), "nametag", "messages.json")
IMAGES_DIR = os.path.join(GLib.get_user_config_dir(), "nametag", "images")

MODE_LABELS = {
    "left": "Scroll left", "right": "Scroll right", "up": "Scroll up", "down": "Scroll down",
    "still": "Still", "animation": "Animation", "drop": "Drop in", "curtain": "Curtain",
    "laser": "Laser",
}
MODE_KEYS = list(badge.MODES)
BRIGHTNESS_LEVELS = [100, 75, 50, 25]
LED_COLORS = {
    "Red": (1.0, 0.23, 0.19), "Green": (0.2, 0.9, 0.35), "Blue": (0.25, 0.55, 1.0),
    "White": (0.95, 0.95, 1.0), "Pink": (1.0, 0.35, 0.7),
}
RENDER_OPTS = SimpleNamespace(font=None, size=11, threshold=128, invert=False)
DEFAULT_MESSAGE = {"text": "Hello :heart:", "mode": "left", "speed": 4, "blink": False, "ants": False,
                   "border": dict(badge.BORDER_DEFAULT), "image": None}
# How a message's image was converted, so it can be changed later (see MessageCard)
IMAGE_DEFAULTS = {"source": None, "fit": "inside", "light_on": False, "autocontrast": True,
                  "dither": False, "threshold": 128, "animate": True, "frames": None,
                  "start": 0.0, "end": None}
UNNAMED = ""  # profile used before any badge has been seen
BORDER_LABELS = {"on": "Dash length", "gap": "Gap length", "rows": "Top and bottom thickness",
                 "cols": "Side thickness"}


def clean_border(border):
    """Border settings from saved state, with defaults for anything missing or out of range."""
    b = dict(badge.BORDER_DEFAULT)
    if isinstance(border, dict):
        for key, (lo, hi) in badge.BORDER_LIMITS.items():
            if isinstance(border.get(key), int) and lo <= border[key] <= hi:
                b[key] = border[key]
        b["cw"] = bool(border.get("cw", False))
    return b


def render(message, tall_ok):
    """Render a message; images keep their full height in up/down mode if tall_ok."""
    tall = tall_ok and message["mode"] in ("up", "down")
    return badge.render_message(message["text"], RENDER_OPTS, tall=tall)


class Preview(Gtk.DrawingArea):
    """On-screen rendering of what the badge will show, animated like the firmware."""

    def __init__(self, cell=14):
        super().__init__()
        self.set_content_width(badge.WIDTH * cell)
        self.set_content_height(badge.HEIGHT * cell + 16)
        self.set_hexpand(True)
        self.columns = []
        self.rows = badge.HEIGHT
        self.invert = False
        self.message = dict(DEFAULT_MESSAGE)
        self.color = LED_COLORS["Red"]
        self.brightness = 100
        self.start = time.monotonic()
        self.set_draw_func(self.draw)
        self.add_tick_callback(lambda *_: self.queue_draw() or GLib.SOURCE_CONTINUE)

    def show(self, message, bitmap=None, tall_ok=True):
        self.message = message
        if bitmap is None:
            bitmap = render(message, tall_ok)
        # Show what build_packet() sends: lit backgrounds inverted plus a flag, and
        # blank sides trimmed for the firmware's centering.
        sent, self.invert = badge.prepare(bitmap, message["mode"], tall_ok)
        self.columns = sent.columns
        self.rows = bitmap.height
        self.start = time.monotonic()

    def draw(self, _area, cr, width, height):
        cell = min(width / (badge.WIDTH + 2), height / (badge.HEIGHT + 2))
        bw, bh = cell * (badge.WIDTH + 2), cell * (badge.HEIGHT + 2)
        x0, y0 = (width - bw) / 2, (height - bh) / 2

        radius = cell * 1.2
        cr.new_sub_path()
        cr.arc(x0 + bw - radius, y0 + radius, radius, -math.pi / 2, 0)
        cr.arc(x0 + bw - radius, y0 + bh - radius, radius, 0, math.pi / 2)
        cr.arc(x0 + radius, y0 + bh - radius, radius, math.pi / 2, math.pi)
        cr.arc(x0 + radius, y0 + radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgb(0.07, 0.07, 0.08)
        cr.fill()

        elapsed = time.monotonic() - self.start
        tick = int(elapsed / effects.tick_seconds(self.message["speed"]))
        canvas = effects.frame(self.message["mode"], self.columns, tick, self.rows)
        canvas = effects.with_overlays(
            canvas, elapsed, self.message["blink"], self.message["ants"],
            self.message.get("border"), self.invert)

        r, g, b = self.color
        level = 0.4 + 0.6 * self.brightness / 100
        for y, row in enumerate(canvas):
            for x, lit in enumerate(row):
                if lit:
                    cr.set_source_rgb(r * level, g * level, b * level)
                else:
                    cr.set_source_rgb(0.16 + r * 0.05, 0.16 + g * 0.05, 0.17 + b * 0.05)
                cr.arc(x0 + cell * (x + 1.5), y0 + cell * (y + 1.5), cell * 0.36, 0, 2 * math.pi)
                cr.fill()


class ImageRows:
    """Image settings inside a message card: how its picture is converted. They stay
    editable: changing one converts the original picture again."""

    def __init__(self, card):
        self.card = card
        self.expander = Adw.ExpanderRow(title="Image settings", visible=False)
        remove = Gtk.Button(icon_name="edit-clear-symbolic", tooltip_text="Back to text",
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        remove.connect("clicked", lambda *_: card.drop_image(clear_text=True))
        self.expander.add_suffix(remove)

        self.fit = Adw.ComboRow(title="Size", model=Gtk.StringList.new(list(imageconv.FITS.values())))
        self.light_on = Adw.SwitchRow(title="Light pixels light up",
                                      subtitle="Off: dark parts of the image light up")
        self.autocontrast = Adw.SwitchRow(title="Auto contrast")
        self.dither = Adw.SwitchRow(title="Dithering", subtitle="Shading with dot patterns; good for photos")
        self.threshold = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 255, 1)
        self.threshold.set_hexpand(True)
        self.threshold.set_draw_value(False)
        threshold_row = Adw.ActionRow(title="Threshold", subtitle="Brighter ↔ darker cutoff")
        threshold_row.add_suffix(self.threshold)
        self.animate = Adw.SwitchRow(title="Animate")
        self.frames = Adw.SpinRow.new_with_range(2, 2, 1)
        self.frames.set_title("Frames")
        self.start = Adw.SpinRow.new_with_range(0, 1, 0.5)
        self.start.set_title("Clip start (seconds)")
        self.start.set_digits(1)
        self.end = Adw.SpinRow.new_with_range(0, 1, 0.5)
        self.end.set_title("Clip end (seconds)")
        self.end.set_digits(1)
        real_speed = Gtk.Button(label="Real Speed", valign=Gtk.Align.CENTER)
        real_speed.connect("clicked", self.match_real_speed)
        self.real_speed_row = Adw.ActionRow(title="Play at the GIF's real speed",
                                            subtitle="Shortens the clip so it isn't fast-forwarded")
        self.real_speed_row.add_suffix(real_speed)
        self.rows = (self.fit, self.light_on, self.autocontrast, self.dither, threshold_row,
                     self.animate, self.frames, self.start, self.end, self.real_speed_row)
        for row in self.rows:
            self.expander.add_row(row)

        self.loading = False  # set while filling in values, which aren't user changes
        self.fit.connect("notify::selected", self.changed)
        self.threshold.connect("value-changed", self.changed)
        for row in (self.light_on, self.autocontrast, self.dither, self.animate):
            row.connect("notify::active", self.changed)
        for row in (self.frames, self.start, self.end):
            row.connect("notify::value", self.changed)

    def show(self, image, source, budget):
        """Fill in the rows from image settings; source may be None if the file is gone."""
        self.loading = True
        self.fit.set_selected(list(imageconv.FITS).index(image["fit"]))
        self.light_on.set_active(image["light_on"])
        self.autocontrast.set_active(image["autocontrast"])
        self.dither.set_active(image["dither"])
        self.threshold.set_value(image["threshold"])
        self.animate.set_active(image["animate"])
        duration = round(source.duration, 1) if source else 0.1
        for row in (self.start, self.end):
            row.get_adjustment().set_upper(max(duration, 0.1))
        self.frames.get_adjustment().set_upper(max(2, budget))
        self.frames.set_value(image["frames"] or budget)
        self.start.set_value(image["start"])
        self.end.set_value(duration if image["end"] is None else image["end"])
        if source:
            self.animate.set_subtitle(f"{source.n_frames} frames, {duration:g} s long")
        self.loading = False
        self.expander.set_visible(True)
        for row in self.rows:
            row.set_sensitive(source is not None)
        self.update_visibility(image, source, budget)

    def read(self, image):
        """The image settings as the rows now have them."""
        return dict(image, fit=list(imageconv.FITS)[self.fit.get_selected()],
                    light_on=self.light_on.get_active(),
                    autocontrast=self.autocontrast.get_active(),
                    dither=self.dither.get_active(),
                    threshold=int(self.threshold.get_value()),
                    animate=self.animate.get_active(),
                    frames=int(self.frames.get_value()),
                    start=self.start.get_value(),
                    end=max(self.start.get_value(), self.end.get_value()))

    def update_visibility(self, image, source, budget):
        animated = bool(source and source.animated)
        pan = image["fit"] == "pan"
        playing = animating(image, source)
        self.animate.set_visible(animated and not pan and image["fit"] != "tall")
        for row in (self.start, self.end, self.real_speed_row):
            row.set_visible(playing)
        self.frames.set_visible(playing or pan)
        limit = min(budget, imageconv.pan_positions(source)) if pan and source else budget
        self.loading = True
        self.frames.get_adjustment().set_upper(max(2, limit))
        if self.frames.get_value() > limit:
            self.frames.set_value(limit)
        self.frames.set_subtitle(f"Up to {limit} fit on the badge")
        self.loading = False

    def match_real_speed(self, *_):
        source = self.card.source
        if not source:
            return
        frame_s = effects.ANIMATION_FRAME_TICKS * effects.tick_seconds(int(self.card.speed.get_value()))
        self.end.set_value(min(source.duration, self.start.get_value() + self.frames.get_value() * frame_s))

    def changed(self, *_):
        if not self.loading:
            self.card.image_changed()


def animating(image, source):
    """True for GIF playback; panning is an animation too but has no clip."""
    return bool(source and source.animated and image["animate"]
                and image["fit"] not in ("pan", "tall"))


class MessageCard:
    def __init__(self, window, message):
        self.window = window
        self.group = Adw.PreferencesGroup()
        self.image = None      # image settings, if this message is a converted picture
        self.image_text = None  # the entry text that converted picture has
        self.source = None
        self.pending = None
        self.setting_text = False

        remove = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove message",
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        remove.connect("clicked", lambda *_: window.remove_card(self))
        self.group.set_header_suffix(remove)
        self.remove_button = remove

        self.entry = Adw.EntryRow(title="Text — icons: :heart: :smile: :star:")
        self.entry.set_text(message["text"])
        image = Gtk.Button(icon_name="image-x-generic-symbolic", tooltip_text="Use an image",
                           valign=Gtk.Align.CENTER, css_classes=["flat"])
        image.connect("clicked", self.pick_image)
        self.entry.add_suffix(image)
        self.image_rows = ImageRows(self)

        self.mode = Adw.ComboRow(title="Effect",
                                 model=Gtk.StringList.new([MODE_LABELS[k] for k in MODE_KEYS]))
        self.mode.set_selected(MODE_KEYS.index(message["mode"]))
        self.speed = Adw.SpinRow.new_with_range(1, 8, 1)
        self.speed.set_title("Speed")
        self.speed.set_value(message["speed"])
        self.blink = Adw.SwitchRow(title="Blink", active=message["blink"])
        # Marching border, with its settings (custom firmware only) inside
        self.ants = Adw.ExpanderRow(title="Marching border", show_enable_switch=True,
                                    enable_expansion=message["ants"])
        border = clean_border(message.get("border"))
        self.border_rows = {}
        for key, (lo, hi) in badge.BORDER_LIMITS.items():
            row = Adw.SpinRow.new_with_range(lo, hi, 1)
            row.set_title(BORDER_LABELS[key])
            row.set_value(border[key])
            row.connect("notify::value", self.changed)
            self.ants.add_row(row)
            self.border_rows[key] = row
        self.border_cw = Adw.SwitchRow(title="Clockwise", active=border["cw"])
        self.border_cw.connect("notify::active", self.changed)
        self.ants.add_row(self.border_cw)

        for row in (self.entry, self.image_rows.expander, self.mode, self.speed, self.blink, self.ants):
            self.group.add(row)
        self.entry.connect("changed", self.text_changed)
        self.mode.connect("notify::selected", self.changed)
        self.speed.connect("notify::value", self.changed)
        self.blink.connect("notify::active", self.changed)
        self.ants.connect("notify::enable-expansion", self.changed)

        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_: window.set_active(self))
        self.group.add_controller(focus)
        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _t, file, *_: self.load_image(file.get_path()) or True)
        self.group.add_controller(drop)

        if isinstance(message.get("image"), dict) and message["text"].startswith("@"):
            self.image = dict(IMAGE_DEFAULTS, **message["image"])
            self.image_text = message["text"]
            self.open_source()
            self.image_rows.show(self.image, self.source, self.budget())
            self.update_caption()

    def message(self):
        return {
            "text": self.entry.get_text(),
            "mode": MODE_KEYS[self.mode.get_selected()],
            "speed": int(self.speed.get_value()),
            "blink": self.blink.get_active(),
            "ants": self.ants.get_enable_expansion(),
            "border": dict({k: int(r.get_value()) for k, r in self.border_rows.items()},
                           cw=self.border_cw.get_active()),
            "image": dict(self.image) if self.image else None,
        }

    def changed(self, *_):
        self.window.set_active(self)

    def text_changed(self, *_):
        # Typing over a converted picture's file name makes it a text message again.
        # Setting it here signals with the old text removed first, so skip those.
        if self.image and not self.setting_text and self.entry.get_text() != self.image_text:
            self.drop_image()
        self.changed()

    # --- image ---

    def pick_image(self, _button):
        filters = Gio.ListStore.new(Gtk.FileFilter)
        image_filter = Gtk.FileFilter(name="Images")
        image_filter.add_pixbuf_formats()
        filters.append(image_filter)
        dialog = Gtk.FileDialog(title="Choose an image", filters=filters)

        def done(dlg, result):
            try:
                self.load_image(dlg.open_finish(result).get_path())
            except GLib.Error:
                pass  # cancelled

        dialog.open(self.window, None, done)

    def load_image(self, path):
        try:
            source = imageconv.Source(path)
        except (OSError, ValueError) as e:
            self.window.toast(f"Couldn't open that image: {e}")
            return
        self.source = source
        self.image = dict(IMAGE_DEFAULTS, source=os.path.abspath(path), end=round(source.duration, 1),
                          frames=self.budget())
        if source.animated:
            self.speed.set_value(8)  # smoothest
        self.image_rows.show(self.image, source, self.budget())
        self.image_rows.expander.set_expanded(True)
        self.convert()

    def open_source(self):
        try:
            self.source = imageconv.Source(self.image["source"])
        except (OSError, ValueError, TypeError):
            self.source = None

    def drop_image(self, clear_text=False):
        self.image = self.image_text = self.source = None
        self.image_rows.expander.set_visible(False)
        if clear_text:
            self.entry.set_text("")
            self.entry.grab_focus()

    def budget(self):
        return imageconv.frame_budget(self.window.other_bytes(self))

    def image_changed(self):
        self.image = self.image_rows.read(self.image)
        self.image_rows.update_visibility(self.image, self.source, self.budget())
        # Debounce: GIF clips may need decoding hundreds of frames.
        if self.pending:
            GLib.source_remove(self.pending)
        self.pending = GLib.timeout_add(120, self.convert)

    def convert(self):
        self.pending = None
        if not (self.image and self.source):
            return GLib.SOURCE_REMOVE
        image = self.image
        bitmap = imageconv.convert(
            self.source, image["fit"], threshold=image["threshold"], dither=image["dither"],
            light_on=image["light_on"], autocontrast=image["autocontrast"],
            animate=image["animate"], start=image["start"],
            end=image["end"] if image["end"] is not None else self.source.duration,
            count=image["frames"])
        stem = "".join(c for c in os.path.splitext(os.path.basename(image["source"]))[0]
                       if c.isalnum() or c in "-_")[:40] or "image"
        path = imageconv.save(bitmap, IMAGES_DIR, stem)
        self.window.created_images.add(path)

        # The effect the picture needs: animations, tall images and wide ones differ
        mode = MODE_KEYS[self.mode.get_selected()]
        if animating(image, self.source) or image["fit"] == "pan":
            mode = "animation"
        elif image["fit"] == "tall":
            mode = mode if mode in ("up", "down") else "up"
        elif mode == "animation":
            mode = "still" if bitmap.width <= badge.WIDTH else "left"
        self.mode.set_selected(MODE_KEYS.index(mode))

        self.image_text = "@" + path
        self.setting_text = True
        self.entry.set_text(self.image_text)
        self.setting_text = False
        self.update_caption(bitmap)
        return GLib.SOURCE_REMOVE

    def update_caption(self, bitmap=None):
        if not self.image:
            return
        name = os.path.basename(self.image["source"] or "")
        if not self.source:
            self.image_rows.expander.set_subtitle(f"{name} · original not found, so these can't change")
            return
        if bitmap is None:
            bitmap = render(self.message(), self.window.tall_ok)
        mode = MODE_KEYS[self.mode.get_selected()]
        if mode == "animation":
            frames = bitmap.width // effects.ANIMATION_FRAME_WIDTH
            plays = frames * effects.ANIMATION_FRAME_TICKS * effects.tick_seconds(int(self.speed.get_value()))
            caption = f"{name} · {frames} frames · plays in {plays:.0f} s"
        elif self.image["fit"] == "tall":
            caption = f"{name} · {bitmap.width}×{bitmap.height} · scrolls up"
            if not self.window.tall_ok:
                caption += " · needs the custom firmware"
        else:
            caption = f"{name} · {bitmap.width}×{badge.HEIGHT}" + (" · scrolls" if mode == "left" else "")
        self.image_rows.expander.set_subtitle(caption)


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Nick's Badge", default_width=720, default_height=820)
        self.cards = []
        self.active = None
        self.tall_ok = True  # whether the last badge seen runs our tall-image firmware
        self.created_images = set()
        self.state = self.load_state()
        self.profile_key = self.state["current"]
        self.connected = None  # (key, version) of the badge plugged in, if any
        profile = self.profile()

        self.title = Adw.WindowTitle(title="Nick's Badge")
        header = Adw.HeaderBar(title_widget=self.title)
        send = Gtk.Button(label="Send to Badge", css_classes=["suggested-action"],
                          tooltip_text="Send to badge (Ctrl+Enter)")
        send.set_action_name("win.send")
        header.pack_end(send)
        firmware = Gtk.Button(icon_name="software-update-available-symbolic",
                              tooltip_text="Update Firmware")
        firmware.connect("clicked", lambda *_: FirmwareDialog(self).present(self))
        header.pack_start(firmware)

        self.preview = Preview()
        self.preview_caption = Gtk.Label(css_classes=["dim-label", "caption"])
        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                              margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        preview_box.append(self.preview)
        preview_box.append(self.preview_caption)

        self.cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.add_button = Gtk.Button(label="Add Message", halign=Gtk.Align.CENTER,
                                     css_classes=["pill"])
        self.add_button.connect("clicked", lambda *_: self.add_card(dict(DEFAULT_MESSAGE, text="")))

        display = Adw.PreferencesGroup(title="Display")
        self.brightness = Adw.ComboRow(
            title="Brightness", subtitle="Stock firmware only; ours has it in the badge menu",
            model=Gtk.StringList.new([f"{b}%" for b in BRIGHTNESS_LEVELS]))
        self.brightness.connect("notify::selected", self.display_changed)
        self.color = Adw.ComboRow(title="Preview color",
                                  subtitle="Match this badge's LEDs (preview only)",
                                  model=Gtk.StringList.new(list(LED_COLORS)))
        self.color.connect("notify::selected", self.display_changed)
        display.add(self.brightness)
        display.add(self.color)

        self.badge_group = Adw.PreferencesGroup(title="This Badge")
        self.badge_name = Adw.EntryRow(title="Name")
        self.badge_name.connect("changed", self.name_changed)
        self.badge_info = Adw.ActionRow(title="Badge", css_classes=["property"])
        self.badge_group.add(self.badge_name)
        self.badge_group.add(self.badge_info)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                          margin_top=12, margin_bottom=24, margin_start=12, margin_end=12)
        content.append(self.cards_box)
        content.append(self.add_button)
        content.append(display)
        content.append(self.badge_group)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(Adw.Clamp(maximum_size=640, child=content))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(Adw.Clamp(maximum_size=760, child=preview_box))
        body.append(Gtk.Separator())
        body.append(scroller)

        self.toasts = Adw.ToastOverlay(child=body)
        toolbar = Adw.ToolbarView(content=self.toasts)
        toolbar.add_top_bar(header)
        self.set_content(toolbar)

        action = Gio.SimpleAction.new("send", None)
        action.connect("activate", self.send)
        self.add_action(action)
        app.set_accels_for_action("win.send", ["<Ctrl>Return"])

        self.show_profile(profile)
        self.update_device_status()
        GLib.timeout_add_seconds(2, self.update_device_status)
        self.connect("close-request", lambda *_: self.save_state() or False)

    # --- messages ---

    def add_card(self, message):
        card = MessageCard(self, message)
        self.cards.append(card)
        self.cards_box.append(card.group)
        self.renumber()
        self.set_active(card)
        if not message["text"]:
            card.entry.grab_focus()

    def remove_card(self, card):
        self.cards.remove(card)
        self.cards_box.remove(card.group)
        self.renumber()
        if self.active is card:
            self.set_active(self.cards[0])

    def renumber(self):
        for i, card in enumerate(self.cards):
            card.group.set_title(f"Message {i + 1}")
            card.remove_button.set_sensitive(len(self.cards) > 1)
        self.add_button.set_sensitive(len(self.cards) < badge.MAX_MESSAGES)

    def other_bytes(self, card):
        """Bytes the other messages take, which limits how much an image can have."""
        return sum(len(render(c.message(), self.tall_ok).to_bytes())
                   for c in self.cards if c is not card)

    def set_active(self, card):
        self.active = card
        message = card.message()
        self.preview.show(message, tall_ok=self.tall_ok)
        n = self.cards.index(card) + 1 if card in self.cards else 1
        caption = f"Message {n} · {MODE_LABELS[message['mode']]}"
        if self.preview.rows > badge.HEIGHT:
            caption += f" · {self.preview.rows} px tall"
        self.preview_caption.set_label(caption)

    def display_changed(self, *_):
        self.preview.brightness = BRIGHTNESS_LEVELS[self.brightness.get_selected()]
        self.preview.color = LED_COLORS[list(LED_COLORS)[self.color.get_selected()]]

    # --- badges: each has a name and its own messages ---

    def profile(self, key=None):
        key = self.profile_key if key is None else key
        return self.state["badges"].setdefault(key, new_profile())

    def store_profile(self):
        """Put the window's messages and display settings into the current profile."""
        profile = self.profile()
        profile["messages"] = [c.message() for c in self.cards]
        profile["brightness"] = BRIGHTNESS_LEVELS[self.brightness.get_selected()]
        profile["color"] = list(LED_COLORS)[self.color.get_selected()]

    def show_profile(self, profile):
        for card in list(self.cards):
            self.cards_box.remove(card.group)
        self.cards = []
        self.active = None
        self.brightness.set_selected(BRIGHTNESS_LEVELS.index(profile["brightness"]))
        self.color.set_selected(list(LED_COLORS).index(profile["color"]))
        self.display_changed()
        for message in profile["messages"]:
            self.add_card(message)
        self.set_active(self.cards[0])
        self.update_title()

    def switch_profile(self, key):
        self.store_profile()
        known = key in self.state["badges"]
        if not known:
            # A new badge starts with what's in the window, so nothing typed is lost
            self.state["badges"][key] = dict(self.profile(), name="")
        self.profile_key = key
        self.state["current"] = key
        self.show_profile(self.profile())
        if not known:
            self.ask_name(key)
        self.save_state()

    def ask_name(self, key):
        default = "Stock badge" if key == "stock" else f"Badge {len(self.state['badges'])}"
        entry = Gtk.Entry(text=default, activates_default=True)
        body = ("This badge is new to the app. Name it, and the app keeps a separate set "
                "of messages for it.")
        if key == "stock":
            body += " Badges on stock firmware can't be told apart, so they share this name."
        dialog = Adw.AlertDialog(heading="New Badge", body=body, extra_child=entry)
        dialog.add_response("later", "Later")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")

        def response(_dlg, answer):
            name = entry.get_text().strip() if answer == "save" else ""
            self.state["badges"][key]["name"] = name or default
            if key == self.profile_key:
                self.badge_name.set_text(self.state["badges"][key]["name"])
            self.save_state()
        dialog.connect("response", response)
        dialog.present(self)

    def name_changed(self, *_):
        if self.profile_key != UNNAMED:
            self.profile()["name"] = self.badge_name.get_text().strip()
            self.update_title()

    def update_title(self):
        profile = self.profile()
        named = self.profile_key != UNNAMED
        self.badge_group.set_visible(named)
        if named and self.badge_name.get_text().strip() != profile["name"]:
            self.badge_name.set_text(profile["name"])
        self.title.set_title(profile["name"] if named and profile["name"] else "Nick's Badge")
        if self.connected and self.connected[0] == self.profile_key:
            version = self.connected[1]
            self.title.set_subtitle(f"Connected · firmware {version}" if version else "Connected")
        else:
            self.title.set_subtitle("Not plugged in")
        if named:
            key = self.profile_key
            self.badge_info.set_subtitle("Stock firmware" if key == "stock"
                                         else "Chip ID " + key.removeprefix("chip-"))

    # --- device ---

    def update_device_status(self):
        device = badge.find_device()
        self.connected = badge.badge_identity(device) if device else None
        if device and self.connected[0] != self.profile_key:
            self.switch_profile(self.connected[0])
        if device and badge.supports_tall(device) != self.tall_ok:
            self.tall_ok = not self.tall_ok
            if self.active:
                self.set_active(self.active)
        self.update_title()
        return GLib.SOURCE_CONTINUE

    def send(self, *_):
        messages = [c.message() for c in self.cards]
        if not any(m["text"].strip() for m in messages):
            self.toast("Nothing to send — type a message first")
            return
        try:
            device = badge.find_device()
            if not device:
                raise badge.BadgeError("Badge not found — is it plugged in?")
            if badge.badge_identity(device)[0] != self.profile_key:
                raise badge.BadgeError("A different badge is plugged in — wait a moment and try again")
            tall_ok = badge.supports_tall(device)
            bitmaps = [render(m, tall_ok) for m in messages]
            payload = badge.build_packet(
                bitmaps,
                [m["mode"] for m in messages],
                [m["speed"] for m in messages],
                [m["blink"] for m in messages],
                [m["ants"] for m in messages],
                BRIGHTNESS_LEVELS[self.brightness.get_selected()],
                invert_ok=tall_ok,
                borders=[m["border"] for m in messages],
            )
            badge.send(payload, device)
        except badge.BadgeError as e:
            self.toast(str(e))
            return
        self.save_state()
        self.toast(f"Sent {len(messages)} message{'s' if len(messages) > 1 else ''} to the badge ✓")

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    # --- persistence ---

    def load_state(self):
        """Saved badges and their messages. The file used to hold a single message list,
        which becomes the messages used until a badge is plugged in."""
        state = {"badges": {}, "current": UNNAMED}
        try:
            with open(STATE_FILE) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        badges = saved.get("badges") if isinstance(saved.get("badges"), dict) else {UNNAMED: saved}
        for key, profile in badges.items():
            if isinstance(key, str) and isinstance(profile, dict):
                state["badges"][key] = clean_profile(profile)
        if saved.get("current") in state["badges"]:
            state["current"] = saved["current"]
        state["badges"].setdefault(state["current"], new_profile())
        return state

    def save_state(self):
        self.store_profile()
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w") as f:
                json.dump(self.state, f, indent=2)
        except OSError:
            return
        # Pictures converted this session that no message ended up using
        used = {m["text"][1:] for p in self.state["badges"].values() for m in p["messages"]
                if m["text"].startswith("@")}
        for path in self.created_images - used:
            try:
                os.remove(path)
            except OSError:
                pass
        self.created_images &= used


def new_profile():
    return {"name": "", "messages": [dict(DEFAULT_MESSAGE)], "brightness": 100, "color": "Red"}


def clean_profile(saved):
    """A badge profile from saved state, with defaults for anything missing or invalid."""
    profile = new_profile()
    if isinstance(saved.get("name"), str):
        profile["name"] = saved["name"]
    messages = [dict(DEFAULT_MESSAGE, **m) for m in saved.get("messages", [])
                if isinstance(m, dict) and m.get("mode", "left") in badge.MODES]
    if messages:
        profile["messages"] = messages[:badge.MAX_MESSAGES]
    if saved.get("brightness") in BRIGHTNESS_LEVELS:
        profile["brightness"] = saved["brightness"]
    if saved.get("color") in LED_COLORS:
        profile["color"] = saved["color"]
    return profile


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        window = self.get_active_window() or Window(self)
        window.present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
