"""Convert images (including animated GIFs) into badge bitmaps."""
import bisect
import hashlib
import os

from PIL import Image, ImageOps

from badge import HEIGHT, MAX_PAYLOAD, TALL_MAX_HEIGHT, WIDTH, Bitmap, image_to_bitmap
from effects import ANIMATION_FRAME_WIDTH

FITS = {
    "height": "Fit height (scrolls if wide)",
    "inside": "Fit inside 44×11",
    "fill": "Fill 44×11 (crop)",
    "stretch": "Stretch to 44×11",
    "pan": "Pan down (animation)",
    "tall": "Fit width, full height (scrolls up/down)",
}
FRAME_BYTES = ANIMATION_FRAME_WIDTH // 8 * HEIGHT  # 66 bytes per animation frame
MAX_WIDTH = 600  # 8KB holds ~700 columns total, so leave room for other messages
STILL_BOX = (MAX_WIDTH * 4, TALL_MAX_HEIGHT * 4)  # keep enough resolution for a good downscale
FRAME_BOX = (WIDTH * 8, HEIGHT * 8)


def frame_budget(other_bytes=0):
    """How many animation frames fit alongside other_bytes of other messages."""
    return max(1, (MAX_PAYLOAD - 64 - other_bytes) // FRAME_BYTES)


class Source:
    """An opened image. Animation frames are decoded on demand and cached."""

    def __init__(self, path):
        self.path = path
        with Image.open(path) as img:
            self.n_frames = getattr(img, "n_frames", 1)
            self.first = _flatten(img, STILL_BOX)
            durations = []
            for i in range(self.n_frames):
                img.seek(i)
                durations.append(img.info.get("duration") or 100)
        # Frame i starts at starts[i] ms.
        self.starts = [0]
        for d in durations[:-1]:
            self.starts.append(self.starts[-1] + d)
        self.duration = sum(durations) / 1000
        self._cache = {}

    @property
    def animated(self):
        return self.n_frames > 1

    def frame_at(self, seconds):
        return max(0, bisect.bisect_right(self.starts, seconds * 1000) - 1)

    def frames(self, indices):
        missing = sorted(set(indices) - self._cache.keys())
        if missing:
            with Image.open(self.path) as img:
                for i in missing:  # sorted, so GIF seeking only moves forward
                    img.seek(i)
                    self._cache[i] = _flatten(img, FRAME_BOX)
        return [self._cache[i] for i in indices]

    def sample(self, start, end, count):
        """Frame indices for `count` frames evenly spaced in time over [start, end] seconds."""
        span = max(0.0, end - start)
        return [self.frame_at(start + (k + 0.5) * span / count) for k in range(count)]


def _flatten(frame, box):
    """Grayscale, shrunk to fit box, with transparency as white (unlit by default)."""
    rgba = frame.convert("RGBA")
    rgba.thumbnail(box, Image.LANCZOS)
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, rgba).convert("L")


def _prepare(gray, autocontrast, light_on):
    if autocontrast:
        gray = ImageOps.autocontrast(gray)
    if light_on:
        gray = ImageOps.invert(gray)
    return gray  # from here on, dark = lit


def _binarize(gray, threshold, dither):
    """Return an image of mode "1" where 1 = lit."""
    if dither:
        # Shift brightness so the threshold slider still biases the dither.
        shifted = gray.point(lambda p: max(0, min(255, p + 128 - threshold)))
        return ImageOps.invert(shifted.convert("1", dither=Image.FLOYDSTEINBERG).convert("L")).convert("1")
    return gray.point(lambda p: 255 if p < threshold else 0).convert("1")


def _resize(gray, fit):
    w, h = gray.size
    if fit == "height":
        return gray.resize((max(1, min(MAX_WIDTH, round(w * HEIGHT / h))), HEIGHT), Image.LANCZOS)
    if fit == "inside":
        scale = min(WIDTH / w, HEIGHT / h)
        small = gray.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        out = Image.new("L", (WIDTH, HEIGHT), 255)
        out.paste(small, ((WIDTH - small.width) // 2, (HEIGHT - small.height) // 2))
        return out
    if fit == "tall":
        return gray.resize((WIDTH, max(HEIGHT, min(TALL_MAX_HEIGHT, round(h * WIDTH / w)))),
                           Image.LANCZOS)
    if fit == "fill":
        return ImageOps.fit(gray, (WIDTH, HEIGHT), Image.LANCZOS)
    return gray.resize((WIDTH, HEIGHT), Image.LANCZOS)


def _frames_to_bitmap(images):
    """Pack 44x11 frames into 48px slots for the Animation effect."""
    out = Bitmap()
    for img in images:
        out += image_to_bitmap(img) + Bitmap.blank(ANIMATION_FRAME_WIDTH - WIDTH)
    return out


def pan_positions(source):
    """Number of distinct vertical positions when panning the image at full badge width."""
    w, h = source.first.size
    return max(1, round(h * WIDTH / w) - HEIGHT + 1)


def convert(source, fit, threshold=128, dither=False, light_on=False, autocontrast=True,
            animate=False, start=0.0, end=None, count=None):
    """Convert to a Bitmap. Animations (GIF frames or a pan) pack frames into 48px slots."""
    prep = lambda g: _prepare(g, autocontrast, light_on)  # noqa: E731
    binarize = lambda g: _binarize(g, threshold, dither)  # noqa: E731

    if fit == "pan":
        # Scale to badge width, then slide an 11-row window from top to bottom.
        w, h = source.first.size
        tall = prep(source.first).resize((WIDTH, max(HEIGHT, round(h * WIDTH / w))), Image.LANCZOS)
        whole = binarize(tall)
        positions = tall.height - HEIGHT + 1
        count = min(count or positions, positions)
        tops = [round(k * (positions - 1) / max(1, count - 1)) for k in range(count)]
        return _frames_to_bitmap([whole.crop((0, t, WIDTH, t + HEIGHT)) for t in tops])

    if fit == "tall" or not (animate and source.animated):
        return image_to_bitmap(binarize(_resize(prep(source.first), fit)))

    end = source.duration if end is None else end
    indices = source.sample(start, end, count or frame_budget())
    frame_fit = "inside" if fit == "height" else fit
    return _frames_to_bitmap([binarize(_resize(prep(g), frame_fit)) for g in source.frames(indices)])


def save(bitmap, directory, stem):
    """Save as a black-on-white PNG (black = lit) that badge.py's '@file' loads as-is."""
    img = Image.new("1", (max(1, bitmap.width), bitmap.height), 1)
    px = img.load()
    for x, col in enumerate(bitmap.columns):
        for y, lit in enumerate(col):
            if lit:
                px[x, y] = 0
    digest = hashlib.sha1(img.tobytes()).hexdigest()[:8]
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{stem}-{digest}.png")
    img.save(path)
    return path
