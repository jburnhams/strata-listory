"""Pictures: what kind a file is, its pixel size (read from the header), and a perceptual hash so the same cover at another size or recompression counts once."""

import shutil
import struct
import subprocess

MIN_BYTES = 2000  # a 1x1 placeholder is not a cover
MIN_SIDE = 100


def kind(b: bytes) -> str:
    """The file extension for a JPEG, PNG or WebP picture, else ''."""
    return (
        ".png"
        if b[:8] == b"\x89PNG\r\n\x1a\n"
        else ".jpg"
        if b[:3] == b"\xff\xd8\xff"
        else ".webp"
        if b[:4] == b"RIFF" and b[8:12] == b"WEBP"
        else ""
    )


def size(b: bytes) -> tuple[int, int] | None:
    """(width, height) from the header of a JPEG, PNG or WebP picture, or None if it cannot be read."""
    k = kind(b)
    try:
        if k == ".png":
            return struct.unpack(">II", b[16:24])
        if k == ".jpg":
            i = 2
            while i + 9 < len(b):
                if b[i] != 0xFF:
                    i += 1
                    continue
                m = b[i + 1]
                if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                    i += 2
                    continue
                n = struct.unpack(">H", b[i + 2 : i + 4])[0]
                if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", b[i + 5 : i + 9])
                    return w, h
                i += 2 + n
        if k == ".webp":
            c = b[12:16]
            if c == b"VP8X":
                return 1 + int.from_bytes(b[24:27], "little"), 1 + int.from_bytes(b[27:30], "little")
            if c == b"VP8L":
                v = int.from_bytes(b[21:25], "little")
                return 1 + (v & 0x3FFF), 1 + ((v >> 14) & 0x3FFF)
            if c == b"VP8 ":
                w, h = struct.unpack("<HH", b[26:30])
                return w & 0x3FFF, h & 0x3FFF
    except (struct.error, ValueError):
        return None
    return None


def dhash(b: bytes) -> str:
    """A 64-bit difference hash (hex) of the picture, drawn 9x8 in grey by ffmpeg: '' when ffmpeg is missing or cannot read it."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return ""
    try:
        r = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                "pipe:0",
                "-vf",
                "scale=9:8:flags=area,format=gray",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "pipe:1",
            ],
            input=b,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    px = r.stdout
    if r.returncode or len(px) != 72:
        return ""
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] < px[row * 9 + col + 1])
    return f"{bits:016x}"


def distance(a: str, b: str) -> int:
    """Number of differing bits between two hashes (64 when either is missing)."""
    return (int(a, 16) ^ int(b, 16)).bit_count() if a and b else 64


# Plain bands round a picture (a book cover centred on a square canvas, or letterboxed): found on a small copy, then cut off the original.
GRID = 256  # the copy is drawn GRID x GRID whatever the shape, so a fraction of it is the same fraction of the picture
TOLERANCE = (
    14  # largest difference in any channel for a pixel to count as the band's colour (compression noise at the edge)
)
MIN_BAND = 0.03  # padding smaller than this share of the side is left alone
KEEP = 0.4  # a crop that would leave less than this share of the side is not padding


def padding(px: bytes, n: int = GRID) -> tuple[float, float, float, float] | None:
    """(left, top, right, bottom) shares of the picture that are one plain colour, from raw RGB pixels of an n x n copy; None when there is no padding to cut. Only a band round two opposite sides counts: all four corners are one colour, one axis only is padded, the two bands are of similar width (padding is centred), and what is left is at least `KEEP` of the side. A picture that is plain at its edges all round (a white cover, a logo) is not cut."""
    if len(px) != n * n * 3:
        return None

    def at(x: int, y: int) -> tuple[int, int, int]:
        i = (y * n + x) * 3
        return px[i], px[i + 1], px[i + 2]

    ref = at(0, 0)

    def near(p: tuple[int, int, int]) -> bool:
        return max(abs(p[0] - ref[0]), abs(p[1] - ref[1]), abs(p[2] - ref[2])) <= TOLERANCE

    if not all(near(at(x, y)) for x, y in ((n - 1, 0), (0, n - 1), (n - 1, n - 1))):
        return None
    col = [all(near(at(x, y)) for y in range(n)) for x in range(n)]
    row = [all(near(at(x, y)) for x in range(n)) for y in range(n)]

    def run(plain: list[bool], rev: bool) -> int:
        k = 0
        for v in reversed(plain) if rev else plain:
            if not v:
                break
            k += 1
        return k

    left, right, top, bottom = run(col, False), run(col, True), run(row, False), run(row, True)
    if left + right >= n or top + bottom >= n:
        return None  # nothing but the band's colour

    def axis(a: int, b: int) -> bool:
        return (a + b) / n >= MIN_BAND and (a + b) / n <= 1 - KEEP and abs(a - b) <= max(2, 0.3 * max(a, b))

    horizontal, vertical = axis(left, right), axis(top, bottom)
    if horizontal == vertical or (top + bottom if horizontal else left + right) / n >= MIN_BAND:
        return None  # both axes padded or the other axis has a band too: not the canvas pattern
    return (left / n, 0.0, right / n, 0.0) if horizontal else (0.0, top / n, 0.0, bottom / n)


def trim_padding(b: bytes) -> bytes | None:
    """The picture with its plain bands cut off (same format), or None when it has none, ffmpeg is missing or anything fails: the original then stands."""
    ffmpeg = shutil.which("ffmpeg")
    dims = size(b)
    k = kind(b)
    if not ffmpeg or not dims or not k:
        return None
    try:
        r = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                "pipe:0",
                "-vf",
                f"scale={GRID}:{GRID}:flags=area,format=rgb24",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "pipe:1",
            ],
            input=b,
            capture_output=True,
            timeout=60,
        )
        found = None if r.returncode else padding(r.stdout)
        if found is None:
            return None
        w, h = dims
        # one grid cell more than the band: the cell at its edge is part band, part picture, and an anti-aliased fringe goes with it
        cell_x, cell_y = max(1, round(w / GRID)), max(1, round(h / GRID))
        x0, y0 = round(found[0] * w) + (cell_x if found[0] else 0), round(found[1] * h) + (cell_y if found[1] else 0)
        x1, y1 = (
            w - round(found[2] * w) - (cell_x if found[2] else 0),
            h - round(found[3] * h) - (cell_y if found[3] else 0),
        )
        cw, ch = x1 - x0, y1 - y0
        if cw < MIN_SIDE or ch < MIN_SIDE:
            return None
        codec = (
            ["-c:v", "mjpeg", "-q:v", "2"]
            if k == ".jpg"
            else ["-c:v", "png"]
            if k == ".png"
            else ["-c:v", "libwebp", "-quality", "95"]
        )
        out = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                "pipe:0",
                "-vf",
                f"crop={cw}:{ch}:{x0}:{y0}",
                "-frames:v",
                "1",
                *codec,
                "-f",
                "image2pipe",
                "pipe:1",
            ],
            input=b,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if not out.returncode and kind(out.stdout) == k and size(out.stdout) == (cw, ch) else None
