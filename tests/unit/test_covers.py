"""The cover sources and picture helpers: size rewriting, header parsing, hashing distance, source records. No network: `net.get` is replaced."""

import struct

import pytest
from pictures import jpeg, png, webp_x

from app.covers import imaging, net, sources


def test_kind_and_size_read_the_header_of_jpeg_png_and_webp():
    assert (imaging.kind(png(1, 1)), imaging.kind(jpeg(1, 1)), imaging.kind(webp_x(1, 1)), imaging.kind(b"GIF89a")) == (
        ".png",
        ".jpg",
        ".webp",
        "",
    )
    assert imaging.size(png(2400, 3600)) == (2400, 3600)
    assert imaging.size(jpeg(500, 750)) == (500, 750)
    assert imaging.size(webp_x(640, 960)) == (640, 960)
    assert imaging.size(b"not a picture") is None and imaging.size(b"\xff\xd8\xff") is None


def test_distance_counts_bits_and_a_missing_hash_is_as_far_as_possible():
    assert imaging.distance("0000000000000000", "00000000000000ff") == 8
    assert imaging.distance("", "ff") == 64 and imaging.distance("ab", "ab") == 0


def test_size_rewriting_asks_for_the_largest_picture():
    assert (
        sources.amazon_original("https://m.media-amazon.com/images/I/51a5V6IbpjL._SL500_.jpg")
        == "https://m.media-amazon.com/images/I/51a5V6IbpjL.jpg"
    )
    assert (
        sources.amazon_original("https://m.media-amazon.com/images/I/abc._SX342_SY445_CR0,0,342,445_.jpg")
        == "https://m.media-amazon.com/images/I/abc.jpg"
    )
    assert (
        sources.apple_large("https://is1-ssl.mzstatic.com/image/thumb/x/y/100x100bb.jpg")
        == "https://is1-ssl.mzstatic.com/image/thumb/x/y/3000x3000bb.jpg"
    )
    assert (
        sources.google_large(
            {
                "thumbnail": "http://books.google.com/x?id=1&zoom=1&edge=curl",
                "large": "http://books.google.com/y?zoom=3",
            }
        )
        == "https://books.google.com/y?zoom=0"
    )
    assert sources.google_large({}) == ""


def reply(status, body=b"", error=""):
    return net.Reply(status, body, error)


def test_records_say_why_a_source_found_nothing():
    assert sources.record("x", [], [], skipped="no ASIN")["status"] == "skipped"
    assert sources.record("x", [sources.Item("x", "a")], [])["status"] == "found"
    assert sources.record("x", [], ["error: down"])["status"] == "error"
    assert sources.record("x", [], ["a: no image", "error: b"])["status"] == "none"


def test_sources_with_nothing_to_look_up_are_skipped(tmp_path):
    ctx = sources.Context()
    for name, fn in sources.SOURCES:
        items, rec = fn(ctx)
        assert items == [] and rec["status"] == "skipped", name


def test_audnexus_reports_missing_errors_and_images(monkeypatch):
    answers = {
        "A1": reply(200, b'{"title": "One", "image": "https://m.media-amazon.com/images/I/a._SL500_.jpg"}'),
        "A2": reply(404, error="nf"),
        "A3": reply(500, error="boom"),
        "A4": reply(200, b'{"title": "No picture"}'),
        "A5": reply(200, b"<html>"),
    }
    monkeypatch.setattr(net, "get", lambda url, **k: next(v for a, v in answers.items() if f"/{a}?" in url))
    items, rec = sources.audnexus(sources.Context(asins=list(answers)))
    assert [(i.ref, i.url) for i in items] == [("A1", "https://m.media-amazon.com/images/I/a.jpg")]
    assert rec["status"] == "found"
    assert rec["detail"] == "A2: not on Audnexus; error: A3: boom; A4: no image"


def test_openlibrary_offers_isbn_covers_and_those_listed_on_the_work_and_editions(monkeypatch):
    pages = {
        "https://openlibrary.org/works/OL9W.json": reply(200, b'{"covers": [11, -1, 12]}'),
        "https://openlibrary.org/works/OL9W/editions.json?limit=30": reply(
            200, b'{"entries": [{"covers": [12, 13]}, {}]}'
        ),
    }
    monkeypatch.setattr(net, "get", lambda url, **k: pages[url])
    items, rec = sources.openlibrary(sources.Context(isbns=["9780000000002"], ol_works=["/works/OL9W", "nonsense"]))
    assert [i.url for i in items] == [
        "https://covers.openlibrary.org/b/isbn/9780000000002-L.jpg?default=false",
        "https://covers.openlibrary.org/b/id/11-L.jpg?default=false",
        "https://covers.openlibrary.org/b/id/12-L.jpg?default=false",
        "https://covers.openlibrary.org/b/id/13-L.jpg?default=false",
    ]
    assert rec["status"] == "found"
    monkeypatch.setattr(net, "get", lambda url, **k: reply(404, error="nf"))
    items, rec = sources.openlibrary(sources.Context(ol_works=["/works/OL9W"]))
    assert items == [] and rec["status"] == "none" and "OL9W work" in rec["detail"]


def test_apple_keeps_results_whose_title_matches_and_asks_for_3000_pixels(monkeypatch):
    body = b'{"results": [{"collectionName": "Glass Orchard (Unabridged)", "collectionId": 5, "artworkUrl100": "https://i.mzstatic.com/x/100x100bb.jpg"}, {"collectionName": "Something Else", "collectionId": 6, "artworkUrl100": "https://i.mzstatic.com/y/100x100bb.jpg"}]}'
    monkeypatch.setattr(net, "get", lambda url, **k: reply(200, body))
    items, rec = sources.apple(sources.Context(title="Glass Orchard", author="Mara Quill"))
    assert [(i.ref, i.url) for i in items] == [("5", "https://i.mzstatic.com/x/3000x3000bb.jpg")]
    monkeypatch.setattr(net, "get", lambda url, **k: reply(200, b'{"results": []}'))
    assert sources.apple(sources.Context(title="Glass Orchard"))[1]["status"] == "none"
    monkeypatch.setattr(net, "get", lambda url, **k: reply(503, error="down"))
    assert sources.apple(sources.Context(title="Glass Orchard"))[1]["status"] == "error"
    monkeypatch.setattr(net, "get", lambda url, **k: reply(200, b"nope"))
    assert sources.apple(sources.Context(title="Glass Orchard"))[1]["status"] == "error"


def test_google_takes_the_largest_image_link_of_each_isbn(monkeypatch):
    seen = []

    def get(url, **k):
        seen.append(url)
        if "isbn:1" in url:
            return reply(
                200,
                b'{"items": [{"volumeInfo": {"title": "T", "imageLinks": {"thumbnail": "http://books.google.com/a?zoom=1"}}}]}',
            )
        if "isbn:2" in url:
            return reply(200, b'{"items": [{"volumeInfo": {}}]}')
        return reply(429, error="slow")

    monkeypatch.setattr(net, "get", get)
    items, rec = sources.google(sources.Context(isbns=["1", "2", "3"]))
    assert [i.url for i in items] == ["https://books.google.com/a?zoom=0"] and rec["status"] == "found"
    assert "2: no cover" in rec["detail"] and "error: 3" in rec["detail"]


def test_local_reads_images_from_the_folder_cover_names_first(tmp_path):
    (tmp_path / "zzz.jpg").write_bytes(jpeg(600, 600))
    (tmp_path / "Cover.png").write_bytes(png(800, 800))
    items, rec = sources.local(sources.Context(folder=str(tmp_path)))
    assert [i.ref for i in items if i.source == "folder"] == ["Cover.png", "zzz.jpg"]
    assert sources.local(sources.Context(folder=str(tmp_path / "gone")))[1]["detail"].startswith(
        "the folder is not on this machine"
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "no image file in the folder" in sources.local(sources.Context(folder=str(empty)))[1]["detail"]


def test_get_refuses_plain_http_and_reports_unreachable_hosts(monkeypatch):
    assert net.get("http://example.invalid/x").status == 0
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    monkeypatch.setattr(net.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("no route")))
    r = net.get("https://example.invalid/x")
    assert (r.status, r.ok) == (0, False) and "no route" in r.error


class _Resp:
    def __init__(self, status, body=b""):
        self.status, self._b = status, body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self._b


def test_get_returns_the_body_and_retries_a_busy_server_once(monkeypatch):
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise net.urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
        return _Resp(200, b'{"a": 1}')

    monkeypatch.setattr(net.urllib.request, "urlopen", fake)
    r = net.get("https://example.test/x")
    assert r.ok and r.json() == {"a": 1} and len(calls) == 2

    def missing(req, timeout=None):
        raise net.urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

    monkeypatch.setattr(net.urllib.request, "urlopen", missing)
    assert net.get("https://example.test/y").status == 404


@pytest.mark.parametrize("name", [n for n, _ in sources.SOURCES])
def test_every_source_is_listed_once(name):
    assert [n for n, _ in sources.SOURCES].count(name) == 1


def test_webp_lossy_and_lossless_and_a_progressive_jpeg_are_sized():
    lossy = b"RIFF\0\0\0\0WEBPVP8 " + b"\0" * 10 + struct.pack("<HH", 300, 450) + b"\0" * 3000
    assert imaging.size(lossy) == (300, 450)
    lossless = b"RIFF\0\0\0\0WEBPVP8L" + b"\0" * 5 + ((299) | (449 << 14)).to_bytes(4, "little") + b"\0" * 3000
    assert imaging.size(lossless) == (300, 450)
    progressive = b"\xff\xd8\xff\xe0\x00\x04ab" + b"\xff\xc2\x00\x11\x08" + struct.pack(">HH", 900, 600) + b"\0" * 20
    assert imaging.size(progressive) == (600, 900)
    assert imaging.size(b"\xff\xd8\xff\xe0\x00\x04ab\xff\xd8\xff\x01" + b"\0" * 20) is None


class _Done:
    def __init__(self, out=b"", code=0):
        self.stdout, self.returncode = out, code


def test_dhash_compares_neighbouring_pixels_of_a_nine_by_eight_picture(monkeypatch):
    monkeypatch.setattr(imaging.shutil, "which", lambda n: "/usr/bin/ffmpeg")
    ramp = bytes(range(9)) * 8  # every pixel is brighter than the one to its left: all 64 bits are 1
    monkeypatch.setattr(imaging.subprocess, "run", lambda *a, **k: _Done(ramp))
    assert imaging.dhash(b"x") == "f" * 16
    monkeypatch.setattr(imaging.subprocess, "run", lambda *a, **k: _Done(bytes(reversed(range(9))) * 8))
    assert imaging.dhash(b"x") == "0" * 16
    monkeypatch.setattr(imaging.subprocess, "run", lambda *a, **k: _Done(b"short"))
    assert imaging.dhash(b"x") == ""
    monkeypatch.setattr(imaging.subprocess, "run", lambda *a, **k: _Done(ramp, 1))
    assert imaging.dhash(b"x") == ""

    def boom(*a, **k):
        raise OSError("gone")

    monkeypatch.setattr(imaging.subprocess, "run", boom)
    assert imaging.dhash(b"x") == ""
    monkeypatch.setattr(imaging.shutil, "which", lambda n: None)
    assert imaging.dhash(b"x") == ""


def test_audible_reads_the_largest_image_of_each_asin_from_the_local_archive(tmp_path):
    import json
    import sqlite3
    import zlib

    path = tmp_path / "audible.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE product (asin TEXT, region TEXT, raw BLOB)")
    product = {
        "product": {
            "title": "Glass Orchard",
            "product_images": {
                "500": "https://m.media-amazon.com/images/I/a._SL500_.jpg",
                "1024": "https://m.media-amazon.com/images/I/a._SL1024_.jpg",
            },
        }
    }
    db.execute("INSERT INTO product VALUES ('B0A', 'us', ?)", (zlib.compress(json.dumps(product).encode()),))
    db.execute(
        "INSERT INTO product VALUES ('B0B', 'uk', ?)",
        (zlib.compress(json.dumps({"product": {"title": "No images"}}).encode()),),
    )
    db.execute("INSERT INTO product VALUES ('B0C', 'uk', ?)", (b"not zlib",))
    db.commit()
    db.close()
    items, rec = sources.audible(sources.Context(asins=["B0A", "B0B", "B0C", "B0D"]), archive=path)
    assert [(i.ref, i.url, i.label) for i in items] == [
        ("B0A", "https://m.media-amazon.com/images/I/a.jpg", "Glass Orchard")
    ]
    assert rec["status"] == "found" and "B0B: no image in the archive" in rec["detail"]
    assert (
        sources.audible(sources.Context(asins=["B0A"]), archive=tmp_path / "missing.sqlite")[1]["detail"]
        == "no local Audible archive"
    )
    (tmp_path / "broken.sqlite").write_bytes(b"this is not a database" * 50)
    assert sources.audible(sources.Context(asins=["B0A"]), archive=tmp_path / "broken.sqlite")[1]["status"] == "error"


def test_local_reads_art_embedded_in_the_first_audio_file(tmp_path, monkeypatch):
    track = tmp_path / "01.mp3"
    track.write_bytes(b"audio")
    ctx = sources.Context(files=[str(tmp_path / "gone.mp3"), str(track)])
    monkeypatch.setattr(sources.shutil, "which", lambda n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(sources.subprocess, "run", lambda *a, **k: _Done(png(600, 600)))
    items, rec = sources.local(ctx)
    assert [(i.source, i.ref) for i in items] == [("embedded", "01.mp3")] and rec["status"] == "found"
    monkeypatch.setattr(sources.subprocess, "run", lambda *a, **k: _Done(b"", 1))
    assert "no art embedded" in sources.local(ctx)[1]["detail"]

    def boom(*a, **k):
        raise OSError("x")

    monkeypatch.setattr(sources.subprocess, "run", boom)
    assert "error: could not read the audio file" in sources.local(ctx)[1]["detail"]
    assert sources.local(sources.Context(files=["/nowhere/a.mp3"]))[1]["status"] == "skipped"
