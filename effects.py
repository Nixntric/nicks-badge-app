"""Frame-by-frame model of the badge's built-in effects, for on-screen previews.

Ported from the preview logic in FOSSASIA's Badge Magic app
(github.com/fossasia/badgemagic-app, lib/badge_animation/), which mimics the firmware.
Everything advances one step per tick; see tick_seconds() for the timing.
"""
from badge import HEIGHT, WIDTH

ANIMATION_FRAME_WIDTH = 48  # mode 5 splits the bitmap into 48px frames, 44 of them visible
ANIMATION_FRAME_TICKS = 5  # ticks per animation frame; measured ~0.65 s per frame at speed 4


def tick_seconds(speed):
    """Badge Magic's timing: 200 ms per step at speed 1, down to ~47 ms at speed 8."""
    return (200_000 - (speed - 1) * (200_000 - 25_000) // 8) / 1_000_000


def frame(mode, columns, tick, height=HEIGHT):
    """Return the 44x11 view as rows of bools (canvas[y][x]) for this tick.

    Bitmaps taller than HEIGHT scroll their whole height in up/down mode, like our
    custom firmware (firmware/badgemagic-firmware, ani_scroll_tall); other modes show
    their top 11 rows.
    """
    w = len(columns)
    canvas = [[False] * WIDTH for _ in range(HEIGHT)]

    if height > HEIGHT and mode in ("up", "down"):
        step = tick % (height + HEIGHT)
        y0 = step - HEIGHT if mode == "up" else height - step
        content = max((x + 1 for x in range(w) if any(columns[x])), default=0)
        offset = (WIDTH - content) // 2 if content < WIDTH else 0
        for y in range(HEIGHT):
            if 0 <= y0 + y < height:
                for x in range(WIDTH):
                    c = x - offset
                    canvas[y][x] = 0 <= c < w and columns[c][y0 + y]
        return canvas

    def src(y, x):
        return 0 <= x < w and 0 <= y < HEIGHT and columns[x][y]

    def paging(page_ticks):
        """(first source column, centering offset) for content that may span several pages."""
        if w > WIDTH:
            pages = -(-w // WIDTH)
            return (tick // page_ticks) % pages * WIDTH, 0
        return 0, (WIDTH - w) // 2

    if mode in ("left", "right"):
        span = w + WIDTH
        step = tick % span
        if mode == "right":
            step = span - 1 - step
        for y in range(HEIGHT):
            for x in range(WIDTH):
                canvas[y][x] = src(y, x + step - WIDTH)

    elif mode in ("up", "down"):
        hold = 15
        page_ticks = 2 * HEIGHT + hold
        start, offset = paging(page_ticks)
        local = tick % page_ticks
        if local < HEIGHT:
            shift = HEIGHT - local
        elif local < HEIGHT + hold:
            shift = 0
        else:
            shift = -(local - HEIGHT - hold)
        if mode == "down":
            shift = -shift
        for y in range(HEIGHT):
            for x in range(WIDTH):
                canvas[y][x] = src(y - shift, start + x - offset)

    elif mode == "still":
        offset = (WIDTH - w) // 2
        for y in range(HEIGHT):
            for x in range(WIDTH):
                canvas[y][x] = src(y, x - offset)

    elif mode == "animation":
        if w <= WIDTH:
            start, offset = 0, (WIDTH - w) // 2
        else:
            frames = -(-w // ANIMATION_FRAME_WIDTH)
            start = (tick // ANIMATION_FRAME_TICKS) % frames * ANIMATION_FRAME_WIDTH
            offset = 0
        for y in range(HEIGHT):
            for x in range(WIDTH):
                canvas[y][x] = src(y, start + x - offset)

    elif mode == "drop":
        # Rows fall in from the top one after another (bottom row first), then fall out.
        page_ticks = HEIGHT * 8
        start, offset = paging(page_ticks)
        local = tick % (page_ticks if w > WIDTH else page_ticks * 2)
        row_cols = lambda row: [src(row, start + x - offset) for x in range(WIDTH)]  # noqa: E731
        if local < HEIGHT * 4:
            for row in range(HEIGHT - 1, -1, -1):
                pos = min(local - (HEIGHT - 1 - row) * 2, row)
                if 0 <= pos < HEIGHT:
                    canvas[pos] = row_cols(row)
        elif local < HEIGHT * 8:
            for row in range(HEIGHT - 1, -1, -1):
                pos = row + (local - HEIGHT * 4 - (HEIGHT - 1 - row) * 2)
                if pos < row:
                    canvas[row] = row_cols(row)
                elif pos < HEIGHT:
                    canvas[row] = [False] * WIDTH
                    canvas[pos] = row_cols(row)

    elif mode == "curtain":
        # Two lit lines open outward from the center revealing the content, then sweep again
        # leaving only what's outside them.
        start, offset = paging(WIDTH)
        step = tick % (WIDTH // 2)
        opening = tick % WIDTH < WIDTH // 2
        left, right = WIDTH // 2 - 1 - step, WIDTH // 2 + step
        for y in range(HEIGHT):
            for x in range(WIDTH):
                inside = left < x < right
                canvas[y][x] = (x in (left, right)
                                or (inside == opening and src(y, start + x - offset)))

    elif mode == "laser":
        # A beam sweeps left to right; each lit pixel in the current column shoots a ray to
        # the right edge, leaving the column drawn behind it. The second sweep erases.
        pages = max(1, -(-w // WIDTH))
        start = (tick // (WIDTH * 2)) % pages * WIDTH
        page_w = min(WIDTH, w - start)
        offset = max(0, WIDTH - w) // 2
        local = tick % (WIDTH * 2)
        drawing, index = local < WIDTH, local % WIDTH
        index = min(index, page_w)  # narrow content: hold once the sweep is past it
        for y in range(HEIGHT):
            for x in range(WIDTH):
                c = start + x - offset
                drawn = src(y, c) if 0 <= c - start < page_w else False
                beam = index < page_w and src(y, start + index)
                if drawing:
                    canvas[y][x] = (drawn and x < index + offset) or (beam and x >= index + offset)
                else:
                    canvas[y][x] = beam if x < index + offset else drawn

    return canvas


def border_place(x, y, rows, cols):
    """Place of pixel (x, y) along a marching border this thick, or None if it's inside.
    Like the firmware (ani_marque): each pixel goes by its edge, top and bottom take the corners."""
    right, bottom = WIDTH - 1, HEIGHT - 1
    if y < rows:
        return x
    if y > bottom - rows:
        return right + bottom + (right - x)
    if x > right - cols:
        return right + y
    if x < cols:
        return 2 * right + bottom + (bottom - y)
    return None


def with_overlays(canvas, tick_time, blink, ants, border=None, invert=False):
    """Apply blink, the marching border (settings as badge.BORDER_DEFAULT) and inversion,
    in the firmware's order: the border is drawn over blink, and inversion flips it all.
    tick_time is seconds since start."""
    if blink and tick_time % 1.0 >= 0.5:
        canvas = [[False] * WIDTH for _ in range(HEIGHT)]
    if ants:
        b = {"on": 1, "gap": 3, "rows": 1, "cols": 1, "cw": False, **(border or {})}
        step = int(tick_time * 10)  # the firmware moves it every 100 ms
        shift = -step if b["cw"] else step
        canvas = [row[:] for row in canvas]
        for y in range(HEIGHT):
            for x in range(WIDTH):
                p = border_place(x, y, b["rows"], b["cols"])
                if p is not None:
                    canvas[y][x] = (p + shift) % (b["on"] + b["gap"]) < b["on"]
    if invert:
        canvas = [[not v for v in row] for row in canvas]
    return canvas
