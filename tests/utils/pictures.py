"""Made-up pictures: a valid header (so kind and size read) and padding. Not decodable, so the perceptual hash is faked where it matters."""

import struct


def png(w, h, pad=3000):
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", w, h)
        + b"\x08\x02\x00\x00\x00"
        + b"\0" * pad
    )


def jpeg(w, h, pad=3000):
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        + b"\xff\xc0\x00\x11\x08"
        + struct.pack(">HH", h, w)
        + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
        + b"\0" * pad
    )


def webp_x(w, h):
    return (
        b"RIFF\0\0\0\0WEBPVP8X"
        + b"\0" * 8
        + (w - 1).to_bytes(3, "little")
        + (h - 1).to_bytes(3, "little")
        + b"\0" * 3000
    )
