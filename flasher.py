"""Firmware updater dialog: pick a .bin, arm, and it flashes the next badge in flash mode.

Does what firmware/recover.sh does: waits for a WCH chip in flash mode (USB 4348:55e0),
lets it settle, and flashes with wchisp, retrying. Before flashing it checks the chip
is a CH582, so nothing else that turns up in flash mode gets this firmware.
"""
import os
import re
import shutil

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# wchisp (github.com/ch32-rs/wchisp): a copy downloaded next to the app, else from PATH
BUNDLED_WCHISP = os.path.join(HERE, "firmware", "tools", "wchisp-dl", "wchisp-linux-x64", "wchisp")
WCHISP = BUNDLED_WCHISP if os.access(BUNDLED_WCHISP, os.X_OK) else shutil.which("wchisp")
IMAGES_DIR = os.path.join(HERE, "firmware", "images")
ISP_ID = ("4348", "55e0")  # WCH bootloader
CHIP = "CH582"
MAX_IMAGE = 448 * 1024  # code flash size
POLL_MS = 200
SETTLE_MS = 1000  # flashing the instant it appears is when the erase failed
TRIES = 5
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def isp_present():
    """True if a WCH chip in flash mode is on USB."""
    root = "/sys/bus/usb/devices"
    for name in os.listdir(root):
        try:
            with open(os.path.join(root, name, "idVendor")) as v, \
                 open(os.path.join(root, name, "idProduct")) as p:
                if (v.read().strip(), p.read().strip()) == ISP_ID:
                    return True
        except OSError:
            pass
    return False


def check_image(path):
    """Why this file can't be flashed, or None if it looks fine."""
    if not path.lower().endswith(".bin"):
        return "Firmware files end in .bin"
    try:
        size = os.path.getsize(path)
    except OSError:
        return "Can't read that file"
    if not 1024 <= size <= MAX_IMAGE:
        return f"{size} bytes doesn't look like badge firmware"
    return None


class FirmwareDialog(Adw.Dialog):
    def __init__(self, window):
        super().__init__(title="Update Firmware", content_width=520, content_height=600)
        self.window = window
        self.image = None
        self.armed = False
        self.flashing = False
        self.poll = None

        # Drop zone, which also shows the chosen file
        self.file_label = Gtk.Label(label="Drop a firmware .bin here", wrap=True,
                                    css_classes=["title-4"])
        self.file_detail = Gtk.Label(label="or open one from the firmware folder",
                                     wrap=True, css_classes=["dim-label"])
        icon = Gtk.Image(icon_name="folder-download-symbolic", pixel_size=48,
                         css_classes=["dim-label"])
        open_button = Gtk.Button(label="Open Firmware Folder…", halign=Gtk.Align.CENTER,
                                 css_classes=["pill"])
        open_button.connect("clicked", self.choose_file)
        drop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                           css_classes=["card"], margin_top=6, margin_bottom=6)
        for widget in (icon, self.file_label, self.file_detail, open_button):
            widget.set_margin_start(18)
            widget.set_margin_end(18)
            drop_box.append(widget)
        icon.set_margin_top(18)
        open_button.set_margin_bottom(18)
        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _t, file, *_: self.set_image(file.get_path()) or True)
        drop_box.add_controller(drop)

        self.arm_button = Gtk.Button(label="Arm Firmware Launcher", sensitive=False,
                                     halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        self.arm_button.connect("clicked", self.toggle_armed)
        self.spinner = Gtk.Spinner(visible=False)
        self.status = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        status_box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        status_box.append(self.spinner)
        status_box.append(self.status)

        self.log = Gtk.TextView(editable=False, monospace=True, cursor_visible=False,
                                wrap_mode=Gtk.WrapMode.WORD_CHAR)
        log_scroll = Gtk.ScrolledWindow(child=self.log, min_content_height=160)
        self.details = Gtk.Expander(label="Details", child=log_scroll)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=24, margin_start=18, margin_end=18)
        for widget in (drop_box, self.arm_button, status_box, self.details):
            box.append(widget)
        toolbar = Adw.ToolbarView(content=Gtk.ScrolledWindow(
            child=box, hscrollbar_policy=Gtk.PolicyType.NEVER))
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_child(toolbar)
        self.connect("closed", lambda *_: self.disarm())
        self.set_status("Pick the firmware, then arm the launcher")

    # --- choosing the file ---

    def choose_file(self, *_):
        bins = Gtk.FileFilter(name="Firmware (.bin)")
        bins.add_pattern("*.bin")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(bins)
        dialog = Gtk.FileDialog(title="Choose firmware", filters=filters,
                                initial_folder=Gio.File.new_for_path(
                                    IMAGES_DIR if os.path.isdir(IMAGES_DIR) else os.path.expanduser("~")))

        def done(dlg, result):
            try:
                self.set_image(dlg.open_finish(result).get_path())
            except GLib.Error:
                pass  # cancelled

        dialog.open(self.get_root(), None, done)

    def set_image(self, path):
        if self.flashing:
            return
        problem = check_image(path)
        if problem:
            self.set_status(problem)
            return
        self.image = path
        self.file_label.set_label(os.path.basename(path))
        self.file_detail.set_label(f"{os.path.getsize(path) // 1024} KB · {os.path.dirname(path)}")
        self.arm_button.set_sensitive(True)
        if not self.armed:
            self.set_status("Ready: arm the launcher, then put the badge in flash mode")

    # --- arming and flashing ---

    def toggle_armed(self, *_):
        if self.armed:
            self.disarm()
            self.set_status("Disarmed")
        else:
            self.arm()

    def arm(self):
        if not WCHISP:
            self.set_status("wchisp isn't installed: get it from github.com/ch32-rs/wchisp "
                            "and put it on your PATH")
            return
        if isp_present():
            # Already waiting in flash mode: flash it, but say so
            self.append_log("A badge is already in flash mode.\n")
        self.armed = True
        self.arm_button.set_label("Disarm")
        self.arm_button.remove_css_class("suggested-action")
        self.spinner.set_visible(True)
        self.spinner.start()
        self.set_status("Armed. Hold the badge's lower key for 5 seconds "
                        "(2 on firmware before v1.0.0) to enter flash mode.")
        self.poll = GLib.timeout_add(POLL_MS, self.check_for_badge)

    def disarm(self):
        if self.poll:
            GLib.source_remove(self.poll)
            self.poll = None
        self.armed = False
        self.arm_button.set_label("Arm Firmware Launcher")
        self.arm_button.add_css_class("suggested-action")
        self.arm_button.set_sensitive(self.image is not None)
        self.spinner.stop()
        self.spinner.set_visible(False)

    def check_for_badge(self):
        if not isp_present():
            return GLib.SOURCE_CONTINUE
        self.poll = None
        self.flashing = True
        self.set_can_close(False)
        self.arm_button.set_sensitive(False)
        self.set_status("Badge in flash mode, checking the chip…")
        GLib.timeout_add(SETTLE_MS, self.check_chip)
        return GLib.SOURCE_REMOVE

    def check_chip(self):
        def done(ok, output):
            if f"Chip: {CHIP}" in output:
                self.flash(1)
            elif "Chip:" in output:
                self.finish(False, f"That chip isn't a {CHIP}, so it wasn't flashed. "
                                   "See Details.")
            else:
                self.finish(False, "Couldn't read the chip. It stays in flash mode: "
                                   "arm again to retry. See Details.")
        self.run([WCHISP, "info"], done)
        return GLib.SOURCE_REMOVE

    def flash(self, attempt):
        self.set_status(f"Flashing {os.path.basename(self.image)}…"
                        + (f" (try {attempt} of {TRIES})" if attempt > 1 else ""))

        def done(ok, output):
            if ok:
                self.finish(True, f"Flashed {os.path.basename(self.image)} ✓ The badge restarts on it.")
            elif attempt < TRIES:
                GLib.timeout_add(1000, lambda: self.flash(attempt + 1) and False)
            else:
                self.finish(False, f"Flashing failed {TRIES} times. The badge stays in flash "
                                   "mode: arm again to retry. See Details.")
        self.run([WCHISP, "flash", self.image], done)

    def finish(self, ok, message):
        self.flashing = False
        self.set_can_close(True)
        self.disarm()
        self.set_status(message)
        if ok:
            self.window.toast(message)
        else:
            self.details.set_expanded(True)

    def run(self, argv, callback):
        """Run a command, logging its output, then callback(succeeded, output)."""
        self.append_log("$ " + " ".join(os.path.basename(a) if a == WCHISP else a for a in argv) + "\n")
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE
                                      | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error as e:
            self.append_log(f"{e.message}\n")
            callback(False, "")
            return

        def done(p, result):
            try:
                _ok, out, _err = p.communicate_utf8_finish(result)
            except GLib.Error as e:
                out = e.message
            out = ANSI.sub("", out or "")
            self.append_log(out)
            callback(p.get_successful(), out)
        proc.communicate_utf8_async(None, None, done)

    # --- output ---

    def set_status(self, text):
        self.status.set_label(text)

    def append_log(self, text):
        buf = self.log.get_buffer()
        buf.insert(buf.get_end_iter(), text)
        self.log.scroll_to_iter(buf.get_end_iter(), 0, False, 0, 0)
