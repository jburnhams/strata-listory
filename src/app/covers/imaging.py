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
