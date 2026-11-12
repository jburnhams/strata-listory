"""Where covers come from. Each source takes what the listen is known to be (`Context`) and returns the pictures it can offer, as addresses or bytes, with the highest size the source offers asked for, and a record of what it found or why not. A source never raises: a failure is a status."""

import glob
import os
import re
import shutil
import subprocess
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

from app.covers import imaging, net

IMAGE_NAMES = ("cover", "folder", "front", "albumart", "albumartsmall", "artwork")
MAX_ASINS, MAX_ISBNS, MAX_OL = 12, 6, 6


@dataclass
class Context:
    """What is known about the listen's identified book: its Audible ASINs (every recording of the work), ISBNs (every edition), Open Library work keys, a title and author for text searches, and the folder and files it was made from."""

    title: str = ""
    author: str = ""
    asins: list[str] = field(default_factory=list)
    isbns: list[str] = field(default_factory=list)
    ol_works: list[str] = field(default_factory=list)
    folder: str = ""
    files: list[str] = field(default_factory=list)


@dataclass
class Item:
    source: str
    ref: str  # ASIN, ISBN, Open Library cover id, file name
    label: str = ""  # what it is the cover of
    url: str | None = None
    data: bytes | None = None


def record(source: str, items: list[Item], notes: list[str], *, skipped: str = "") -> dict:
    if skipped:
        return dict(source=source, status="skipped", detail=skipped, count=0)
    if items:
        return dict(source=source, status="found", detail="; ".join(notes[:3]), count=len(items))
    if notes and any(n.startswith("rate limited") for n in notes):
        return dict(source=source, status="limited", detail="; ".join(notes[:4]), count=0)
    errors = [n for n in notes if n.startswith("error")]
    return dict(
        source=source,
        status="error" if errors and len(errors) == len(notes) else "none",
        detail="; ".join(notes[:4]),
        count=0,
    )


def failed_note(ref: str, r) -> str:
    """A note for a failed request: `rate limited` when the host asked us to slow down (the record then says so and the search can be repeated later), else `error`."""
    return f"rate limited: {ref}: {r.error or r.status}" if r.limited else f"error: {ref}: {r.error or r.status}"


# ---- size rewriting: ask each service for its largest picture

AMAZON_SIZE = re.compile(r"\._[A-Za-z0-9,_-]+_(\.[a-z]+)$")


def amazon_original(url: str) -> str:
    """An Amazon/Audible image address without its size suffix (`...I/abc._SL500_.jpg` -> `...I/abc.jpg`), which serves the original."""
    return AMAZON_SIZE.sub(r"\1", url)


def apple_large(url: str) -> str:
    """An iTunes artwork address asked for at 3000 pixels (it serves the original when that is smaller)."""
    return re.sub(r"/\d+x\d+(bb|sr)?\.(jpg|png)$", r"/3000x3000bb.\2", url)


def google_large(links: dict) -> str:
    for k in ("extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail"):
        if links.get(k):
            u = links[k].replace("http://", "https://").replace("&edge=curl", "")
            return re.sub(r"zoom=\d", "zoom=0", u)
    return ""


# ---- sources


def audible(ctx: Context, *, archive=None) -> tuple[list[Item], dict]:
    """The product image of every Audible recording of the work, from the local Audible archive (no request needed), at original size."""
    import sqlite3
    import zlib

    from app.identify import audibleindex

    if not ctx.asins:
        return [], record("audible", [], [], skipped="no ASIN known for this book")
    path = archive or audibleindex.archive_path()
    if not os.path.exists(path):
        return [], record("audible", [], [], skipped="no local Audible archive")
    items, notes = [], []
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            for asin in ctx.asins[:MAX_ASINS]:
                got = None
                for region, value in db.execute(
                    "SELECT region, raw FROM product WHERE asin=? ORDER BY region='uk' DESC", (asin,)
                ):
                    try:
                        p = audibleindex.raw(value)
                        p = p.get("product", p)
                    except (ValueError, zlib.error, TypeError):
                        continue
                    imgs = p.get("product_images") or {}
                    if imgs:
                        got = (
                            region,
                            p.get("title") or "",
                            max(imgs.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0)[1],
                        )
                        break
                if got:
                    items.append(Item("audible", asin, got[1], amazon_original(got[2])))
                else:
                    notes.append(f"{asin}: no image in the archive")
        finally:
            db.close()
    except sqlite3.Error as e:
        notes.append(f"error: archive unreadable ({e})")
    return items, record("audible", items, notes)


def audnexus(ctx: Context) -> tuple[list[Item], dict]:
    """Audnexus's image for each ASIN (Audible's own pictures, also for ASINs missing from the local archive)."""
    if not ctx.asins:
        return [], record("audnexus", [], [], skipped="no ASIN known for this book")
    items, notes = [], []
    for asin in ctx.asins[:MAX_ASINS]:
        r = net.get(f"https://api.audnex.us/books/{urllib.parse.quote(asin)}?region=uk")
        if r.status == 404:
            notes.append(f"{asin}: not on Audnexus")
        elif not r.ok:
            notes.append(failed_note(asin, r))
            if r.limited:
                break
        else:
            try:
                d = r.json()
            except ValueError:
                notes.append(f"error: {asin}: unreadable answer")
                continue
            if d.get("image"):
                items.append(Item("audnexus", asin, d.get("title") or "", amazon_original(d["image"])))
            else:
                notes.append(f"{asin}: no image")
    return items, record("audnexus", items, notes)


def openlibrary(ctx: Context) -> tuple[list[Item], dict]:
    """Open Library covers (large) of each ISBN, and of the covers listed on the work and its editions."""
    if not ctx.isbns and not ctx.ol_works:
        return [], record("openlibrary", [], [], skipped="no ISBN or Open Library work known for this book")
    items, notes = [], []
    for isbn in ctx.isbns[:MAX_ISBNS]:
        items.append(
            Item(
                "openlibrary", isbn, f"ISBN {isbn}", f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
            )
        )
    seen: set[int] = set()
    for key in ctx.ol_works[:3]:
        m = re.search(r"OL\d+W", key)
        if not m:
            continue
        for url, what in (
            (f"https://openlibrary.org/works/{m.group()}.json", "work"),
            (f"https://openlibrary.org/works/{m.group()}/editions.json?limit=30", "edition"),
        ):
            r = net.get(url)
            if not r.ok:
                notes.append(
                    f"{m.group()} {what}: {r.error or r.status}"
                    if r.status == 404
                    else failed_note(f"{m.group()} {what}", r)
                )
                if r.limited:
                    break
                continue
            try:
                d = r.json()
            except ValueError:
                continue
            docs = d.get("entries", [d]) if what == "edition" else [d]
            for doc in docs:
                for cid in doc.get("covers") or []:
                    if isinstance(cid, int) and cid > 0 and cid not in seen and len(seen) < MAX_OL:
                        seen.add(cid)
                        items.append(
                            Item(
                                "openlibrary",
                                str(cid),
                                f"{what} cover {cid}",
                                f"https://covers.openlibrary.org/b/id/{cid}-L.jpg?default=false",
                            )
                        )
    return items, record("openlibrary", items, notes or ["offered; a missing picture is reported when downloaded"])


def apple(ctx: Context) -> tuple[list[Item], dict]:
    """Apple Books' audiobook artwork for results whose title matches, asked for at 3000 pixels."""
    if not ctx.title:
        return [], record("apple", [], [], skipped="no title known for this book")
    from app.identify import textkeys as tk

    term = urllib.parse.quote(f"{ctx.title} {ctx.author}".strip())
    r = net.get(f"https://itunes.apple.com/search?term={term}&media=audiobook&entity=audiobook&limit=8")
    if not r.ok:
        return [], record("apple", [], [failed_note("search", r)])
    try:
        results = r.json().get("results") or []
    except ValueError:
        return [], record("apple", [], ["error: unreadable answer"])
    want = tk.title_key(ctx.title)
    items = []
    for x in results:
        got = x.get("collectionName") or x.get("trackName") or ""
        key = tk.title_key(got)
        if want and key and (want == key or want in key or key in want) and x.get("artworkUrl100"):
            items.append(Item("apple", str(x.get("collectionId") or ""), got, apple_large(x["artworkUrl100"])))
    return items, record("apple", items, [f"{len(results)} results, none with a matching title"] if not items else [])


def google_key() -> str:
    """An optional Google Books API key (a higher request quota): the GOOGLE_BOOKS_API_KEY environment variable or the first line of ~/.config/strata-listory/googlebooks.key. Never logged or stored in the database."""
    key = os.environ.get("GOOGLE_BOOKS_API_KEY", "").strip()
    if not key:
        try:
            with open(os.path.expanduser("~/.config/strata-listory/googlebooks.key"), encoding="utf8") as f:
                key = f.readline().strip()
        except OSError:
            key = ""
    return key


def google(ctx: Context) -> tuple[list[Item], dict]:
    """Google Books' cover of each ISBN, the largest size it lists. Without an API key Google allows few requests, so a 429 stops the lookups for this book and the record says it was rate limited."""
    if not ctx.isbns:
        return [], record("google", [], [], skipped="no ISBN known for this book")
    items, notes = [], []
    key = google_key()
    for isbn in ctx.isbns[:MAX_ISBNS]:
        r = net.get(
            f"https://www.googleapis.com/books/v1/volumes?q=isbn:{urllib.parse.quote(isbn)}&maxResults=2"
            + (f"&key={urllib.parse.quote(key)}" if key else "")
        )
        if not r.ok:
            notes.append(failed_note(isbn, r))
            if r.limited:
                break
            continue
        try:
            vols = r.json().get("items") or []
        except ValueError:
            continue
        got = False
        for v in vols:
            u = google_large((v.get("volumeInfo") or {}).get("imageLinks") or {})
            if u:
                items.append(Item("google", isbn, (v.get("volumeInfo") or {}).get("title") or f"ISBN {isbn}", u))
                got = True
                break
        if not got:
            notes.append(f"{isbn}: no cover")
    return items, record("google", items, notes)


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def local(ctx: Context) -> tuple[list[Item], dict]:
    """Pictures from the listen's own files: an image file in its folder (cover, folder, front...) and art embedded in the first audio file."""
    items, notes = [], []
    if ctx.folder and os.path.isdir(ctx.folder):
        pics = sorted(
            (
                p
                for ext in ("jpg", "jpeg", "png", "webp")
                for p in glob.glob(os.path.join(glob.escape(ctx.folder), f"*.{ext}"))
            ),
            key=lambda p: (
                os.path.splitext(os.path.basename(p))[0].lower() not in IMAGE_NAMES,
                _natural(os.path.basename(p)),
            ),
        )
        for p in pics[:4]:
            try:
                with open(p, "rb") as f:
                    items.append(Item("folder", os.path.basename(p), "image in the folder", data=f.read(40_000_000)))
            except OSError as e:
                notes.append(f"error: {os.path.basename(p)}: {e}")
        if not pics:
            notes.append("no image file in the folder")
    elif ctx.folder:
        notes.append("the folder is not on this machine")
    ffmpeg = shutil.which("ffmpeg")
    first = next((f for f in ctx.files if os.path.isfile(f)), None)
    if ffmpeg and first:
        try:
            r = subprocess.run(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-i",
                    first,
                    "-an",
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-c",
                    "copy",
                    "-f",
                    "image2pipe",
                    "pipe:1",
                ],
                capture_output=True,
                timeout=60,
            )
            if r.returncode == 0 and imaging.kind(r.stdout):
                items.append(Item("embedded", os.path.basename(first), "art embedded in the audio", data=r.stdout))
            else:
                notes.append("no art embedded in the first audio file")
        except (OSError, subprocess.TimeoutExpired):
            notes.append("error: could not read the audio file")
    if not ctx.folder and not first:
        return [], record("local", [], [], skipped="the listen's files are not on this machine")
    return items, record("local", items, notes)


SOURCES: list[tuple[str, Callable[[Context], tuple[list[Item], dict]]]] = [
    ("audible", audible),
    ("audnexus", audnexus),
    ("openlibrary", openlibrary),
    ("apple", apple),
    ("google", google),
    ("local", local),
]
