"""Bounded tag-aware group discovery, immutable manifests and representations."""

import hashlib
import json
import math
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from app import catalogue, contributors, details, fs, matcher, merge, scan, sources
from app.ingest import grouping
from app.ingest.observe import report
from app.process import probe


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def signature(book):
    return hashlib.sha256(
        json.dumps(
            {
                k: book.get(k)
                for k in (
                    "title",
                    "author",
                    "narrator",
                    "series",
                    "series_position",
                    "asin",
                    "year",
                    "locked",
                    "path",
                    "review",
                )
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def track(tags):
    m = re.match(r"\s*(\d+)(?:/(\d+))?", str(tags.get("track") or tags.get("tracknumber") or ""))
    return (int(m[1]), int(m[2]) if m[2] else None) if m else (None, None)


def is_part(path, tags):
    a, b = track(tags)
    return bool((b and b > 1) or re.search(r"(?:chapter|part|track|\s-)[ _-]*\d+\s*$", Path(path).stem, re.I))


def numbered_parts(paths):
    """Infer chapter prefixes only from siblings sharing a title and starting at 1."""
    paths = list(paths)
    groups = {}
    parts = {path: value["number"] for path, value in grouping.coded_parts(paths).items()}
    parts.update(
        {path: value["number"] for path, value in grouping.filename_parts(paths).items() if value["kind"] != "bytes"}
    )
    for path in paths:
        p = Path(path)
        m = re.fullmatch(r"(\d{1,4})(?:[ ._-]+(.*))?", p.stem)
        if m:
            groups.setdefault((str(p.parent), p.suffix.lower(), merge.norm(m[2] or "")), []).append(
                (str(path), int(m[1]))
            )
    for items in groups.values():
        numbers = [n for _, n in items]
        if len(items) > 1 and min(numbers) == 1 and len(set(numbers)) == len(numbers):
            parts.update(items)
    return parts


def embedded_asins(tags):
    found = set()
    for key, value in tags.items():
        if not isinstance(value, str):
            continue
        if key.lower() in ("audible_asin", "asin") and sources.ASIN.fullmatch(value.strip().upper()):
            found.add(value.strip().upper())
        # A product link is identifier evidence, not a URL to fetch. Restrict
        # extraction to actual Audible hosts and product path segments.
        if key.lower() not in ("woas", "woaf", "www", "url", "website", "purl"):
            continue
        for url in re.findall(r'https?://[^\s<>"\']+', value):
            try:
                parsed = urlparse(url)
                host = (parsed.hostname or "").lower()
            except ValueError:
                continue
            if not re.fullmatch(r"(?:www\.)?audible\.(?:com|co\.uk|com\.au|ca|de|fr|it|es|co\.jp|in)", host):
                continue
            match = re.search(r"/(?:pd|dp)/(?:[^/]+/)?([A-Z0-9]{10})(?:/|$)", parsed.path, re.I)
            if match:
                found.add(match[1].upper())
    return found


def discover(book):
    path = Path(book["path"]).resolve()
    if not fs.reachable(str(path)) or not path.exists():
        raise ValueError("audio path unavailable")
    root = path
    sibling_paths = None
    coded_run = False
    if path.is_file():
        try:
            first = probe.probe_file(str(path))
        except (OSError, ValueError, probe.ToolError):
            first = dict(tags={})
        siblings = [p for p in path.parent.iterdir() if p.is_file() and scan.is_audio(p.name)]
        filename_parts = grouping.filename_parts(siblings)
        selected_part = filename_parts.get(str(path))
        coded = grouping.coded_parts(siblings)
        selected_code = coded.get(str(path))
        coded_run = bool(
            selected_code and all((coded.get(str(p)) or {}).get("prefix") == selected_code["prefix"] for p in siblings)
        )
        hint = grouping.title_hint(scan.parse_name(path.name)["title"])

        def related(p):
            if coded_run:
                return True
            if selected_part and (filename_parts.get(str(p)) or {}).get("key") == selected_part["key"]:
                return True
            other = grouping.title_hint(scan.parse_name(p.name)["title"])
            a, b = matcher.norm(hint), matcher.norm(other)
            return (
                not other
                or matcher.title_score(hint, dict(title=other, subtitle="")) >= 0.9
                or (min(len(a), len(b)) >= 6 and (a.endswith(b) or b.endswith(a)))
            )

        other_formats = bool(hint) and any(p.suffix.lower() != path.suffix.lower() and related(p) for p in siblings)
        # A stale track total on a loose whole-book file is not permission to
        # inventory a library directory. File expansion is a sibling operation;
        # only explicit directory selections recursively discover collections.
        if (
            is_part(path, first["tags"]) or str(path) in numbered_parts(siblings) or selected_part or other_formats
        ) and all(related(p) for p in siblings):
            root = path.parent
            sibling_paths = siblings
    paths = (
        [root]
        if root.is_file()
        else sorted(
            sibling_paths
            if sibling_paths is not None
            else (
                p
                for p in root.rglob("*")
                if p.is_file()
                and scan.is_audio(p.name)
                and not any(x.startswith((".", "@")) for x in p.relative_to(root).parts)
            ),
            key=lambda p: probe.natural_key(str(p)),
        )
    )
    if not paths:
        raise ValueError("no audio files")
    extras = catalogue.get_extras(book["id"])
    scope = (extras.get("source_group") or {}).get("data")
    reference = (extras.get("source_group_ref") or {}).get("data")
    if reference and reference["primary_id"] != book["id"]:
        scope = (catalogue.get_extras(reference["primary_id"]).get("source_group") or {}).get("data") or scope
    overrides = (scope or {}).get("layout_overrides") or (extras.get("source_layout") or {}).get("data", {}).get(
        "files", {}
    )
    scope_root = root
    if scope:
        scope_root = Path(scope["root"])
        all_paths = sorted(
            (
                p
                for p in scope_root.rglob("*")
                if p.is_file()
                and scan.is_audio(p.name)
                and not any(x.startswith((".", "@")) for x in p.relative_to(scope_root).parts)
            ),
            key=lambda p: probe.natural_key(str(p)),
        )
        paths = (
            [Path(p) for p in scope["paths"]]
            if scope.get("version", 1) == grouping.VERSION and {str(p) for p in all_paths} == set(scope["scope_paths"])
            else all_paths
        )
        root = scope_root
    files = []
    errors = []
    for position, p in enumerate(paths, 1):
        report(f"Inspecting tags and hashing {p.name}", stage="inventory", current=position, total=len(paths))
        try:
            before = p.stat()
            raw = details._ffprobe(str(p))
            info = probe.probe_file(str(p))
            sha = digest(p)
            after = p.stat()
            if not raw or not math.isfinite(info["duration"]) or info["duration"] <= 0:
                raise ValueError("invalid duration or probe")
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("input changed during inventory")
            files.append(
                dict(
                    path=str(p), sha256=sha, size=before.st_size, mtime_ns=before.st_mtime_ns, probe=info, raw_probe=raw
                )
            )
            correction = overrides.get(str(p))
            if correction and correction.get("sha256") == sha:
                files[-1]["grouping_hint"] = correction
        except (OSError, ValueError, probe.ToolError) as e:
            errors.append(dict(path=str(p), error=str(e)))
    for file_path, part in grouping.filename_layout(files).items():
        if part["kind"] != "bytes":
            continue
        f = next(f for f in files if f["path"] == file_path)
        if f["probe"].get("duration_method") == "decoded audio clock":
            continue
        report(f"Measuring decoded duration of split MP3 {Path(file_path).name}", stage="inventory")
        try:
            measured = probe.decoded_duration(file_path)
            stat = Path(file_path).stat()
            if (stat.st_size, stat.st_mtime_ns) != (f["size"], f["mtime_ns"]):
                raise ValueError("input changed during duration measurement")
            f["probe"] = probe.apply_decoded_duration(f["probe"], measured)
        except (OSError, ValueError, probe.ToolError) as error:
            errors.append(dict(path=file_path, error=str(error)))
    sidecars = []
    names = (
        set(root.glob("*.metadata.json") if sibling_paths is not None else root.rglob("*.metadata.json"))
        if root.is_dir()
        else {root.with_suffix(".metadata.json")}
    )
    for p in sorted(names):
        if p.exists():
            try:
                sidecars.append(dict(path=str(p), sha256=digest(p), raw=json.loads(p.read_text(encoding="utf8"))))
            except (OSError, ValueError) as e:
                errors.append(dict(path=str(p), error=str(e)))
    members = [
        b
        for b in catalogue.list_books()
        if b["path"] and (Path(b["path"]).resolve() == root or Path(b["path"]).resolve() in paths)
    ]
    if book["id"] not in [b["id"] for b in members]:
        members.append(book)
    representations = represent(files)
    group = hashlib.sha256(str(root).encode()).hexdigest()
    result = dict(
        root=str(root),
        group=group,
        primary_id=min(b["id"] for b in members),
        members=members,
        files=files,
        sidecars=sidecars,
        representations=representations,
        errors=errors,
    )
    groups = grouping.partition(files, root)
    if scope:
        result["scope_root"] = scope["root"]
        result["scope_paths"] = [str(p) for p in all_paths]
        matching = [g for g in groups if set(g["paths"]) & set(scope["paths"])]
        if len(matching) != 1:
            raise ValueError("book grouping changed; ingest the source collection to regroup it")
        matching[0]["anchor_members"] = scope.get("anchor_members", [])
        result = scoped(result, matching[0], members)
        result["scope_root"] = scope["root"]
        result["scope_paths"] = [str(p) for p in all_paths]
    elif path.is_file() and len(groups) > 1:
        result = scoped(result, next(g for g in groups if str(path) in g["paths"]), members)
    elif len(groups) > 1:
        result["work_groups"] = groups
    elif groups:
        if path.is_file() and root != path and not coded_run:

            def stem_key(p):
                return scan._norm(re.sub(r"(?:chapter|part|track|disc|cd)[ _-]*\d+", "", p.stem, flags=re.I))

            base = stem_key(path)
            if base and not all(
                not stem_key(p) or stem_key(p).endswith(base) or base.endswith(stem_key(p)) for p in paths
            ):
                result = scoped(result, dict(groups[0], paths=[str(path)]), members)
        result["work_hint"] = groups[0] if len(result["files"]) == len(files) else dict(groups[0], paths=[str(path)])
    report("Source inventory complete", stage="inventory", current=len(paths), total=len(paths))
    return result, {"inventory.files": len(result["files"]), "inventory.probe_errors": len(result["errors"])}


def scoped(inv, work, members):
    """Select one evidenced work while retaining the containing source snapshot."""
    paths = set(work["paths"])
    files = [f for f in inv["files"] if f["path"] in paths]
    all_paths = set(inv.get("scope_paths") or [f["path"] for f in inv["files"]])

    def belongs(member):
        path = Path(member.get("path") or "").resolve()
        beneath = {p for p in all_paths if Path(p).is_relative_to(path)}
        return (
            member["id"] in work.get("anchor_members", []) or str(path) in paths or (bool(beneath) and beneath <= paths)
        )

    members = [m for m in members if belongs(m)]
    if not members:
        raise ValueError("source group has no catalogue entry")
    root = Path(os.path.commonpath([str(Path(p).parent) for p in paths]))
    if len(paths) == 1:
        root = Path(next(iter(paths)))
    result = dict(
        inv,
        root=str(root),
        scope_root=inv.get("scope_root", inv["root"]),
        scope_paths=inv.get("scope_paths", [f["path"] for f in inv["files"]]),
        work_hint=work,
        files=files,
        members=members,
        primary_id=min(m["id"] for m in members),
        group=hashlib.sha256(("\n".join(sorted(paths))).encode()).hexdigest(),
        sidecars=[
            s
            for s in inv["sidecars"]
            if (
                bool(related := {p for p in all_paths if Path(p).is_relative_to(Path(s["path"]).parent)})
                and related <= paths
            )
            or Path(s["path"]).with_suffix("").with_suffix("") in {Path(p).with_suffix("") for p in paths}
        ],
        representations=[],
        errors=[e for e in inv["errors"] if e["path"] in paths],
    )
    result.pop("work_groups", None)
    result["scope_sidecars"] = inv.get("scope_sidecars", [s["path"] for s in inv.get("sidecars", [])])
    result["representations"] = represent(files)
    return result


def layout_overrides(inv):
    return {f["path"]: f["grouping_hint"] for f in inv["files"] if f.get("grouping_hint")}


def disc_number(file):
    tags = file["probe"]["tags"]
    value = str(tags.get("disc") or tags.get("discnumber") or "")
    match = re.match(r"(\d+)(?:/(\d+))?", value)
    if match:
        return int(match[1]), int(match[2]) if match[2] else None
    _, disc, total = grouping.album_parts(tags.get("album", ""))
    if disc:
        return disc, total
    match = re.search(r"(?:cd|disc|disk)[ _-]*(\d+)", Path(file["path"]).parent.name, re.I)
    return (int(match[1]), None) if match else (0, None)


def represent(files):
    reps = {}
    numbered = numbered_parts(f["path"] for f in files)
    coded = grouping.coded_parts(f["path"] for f in files)
    layout = grouping.sequence_layout(files)
    named = grouping.filename_layout(files)

    def disc_info(f):
        info = layout.get(f["path"])
        return (info["disc"], info["total"]) if info else disc_number(f)

    for f in files:
        path = Path(f["path"])
        tags = f["probe"]["tags"]
        disc, _ = disc_info(f)
        sequence = layout.get(f["path"])
        parent = path.parent.parent if disc and scan.DISC.search(path.parent.name) else path.parent
        part = is_part(path, tags) or f["path"] in numbered or disc
        key = str(parent) + "|" + path.suffix.lower() + "|" + merge.norm(tags.get("album", "")) if part else str(path)
        if disc:
            key = (
                str(parent)
                + "|"
                + path.suffix.lower()
                + "|"
                + matcher.norm(grouping.album_parts(tags.get("album", ""))[0])
            )
        if f["path"] in coded:
            key = str(parent) + "|" + path.suffix.lower() + "|code:" + coded[f["path"]]["prefix"]
        elif f["path"] in numbered and not disc:
            key += "|" + merge.norm(re.sub(r"^\d+[ ._-]*", "", path.stem))
        if f["path"] in named:
            key = "filename:" + named[f["path"]]["key"]
        if sequence:
            key = (
                str(path.parent.parent if sequence["shared"] else path.parent)
                + "|"
                + path.suffix.lower()
                + "|"
                + sequence["key"]
            )
        reps.setdefault(key, []).append(f)
    result = []
    for key, items in sorted(reps.items()):
        items.sort(key=lambda f: (disc_info(f)[0], probe.natural_key(f["path"])))
        discs = {}
        missing = 0
        duplicates = 0
        known = False
        for f in items:
            discs.setdefault(disc_info(f)[0], []).append(f)
        disc_total = max((disc_info(f)[1] or 0 for f in items), default=0)
        if discs and all(discs):
            disc_total = max(disc_total, max(discs))
        # Untrusted tags sometimes contain uint32 sentinel totals. Count the
        # complement arithmetically instead of materialising billions of IDs.
        missing += disc_total - len({n for n in discs if 1 <= n <= disc_total}) if disc_total else 0
        for values in discs.values():
            numbers = []
            total = 0
            for f in values:
                number, expected = track(f["probe"]["tags"])
                suffix = re.search(r"(?:chapter|part|track)[ _-]*(\d+)\s*$", Path(f["path"]).stem, re.I)
                number = (
                    number
                    or (named.get(f["path"]) or {}).get("number")
                    or numbered.get(f["path"])
                    or (int(suffix[1]) if suffix else None)
                )
                expected = expected or (named.get(f["path"]) or {}).get("total") or 0
                numbers.append(number)
                total = max(total, expected or 0)
            if not total and numbers and all(n is not None for n in numbers) and min(numbers) == 1:
                total = max(numbers)
            if total:
                known = True
                missing += total - len({n for n in numbers if n is not None and 1 <= n <= total})
            if all(n is not None for n in numbers):
                duplicates += len(numbers) - len(set(numbers))
        result.append(
            dict(
                id=hashlib.sha256(key.encode()).hexdigest()[:16],
                files=[f["path"] for f in items],
                duration=sum(f["probe"]["duration"] for f in items),
                bytes=sum(f["size"] for f in items),
                missing_tracks=missing if known or disc_total else None,
                duplicate_tracks=duplicates,
            )
        )
    return result


def recover(book, inv):
    locked = set(book.get("locked") or [])
    votes = {}
    authors = {}
    narrators = {}
    asins = {book["asin"]} if sources.ASIN.fullmatch(book.get("asin") or "") else set()
    languages = set()
    formats = set()
    evidence = []

    def add(target, value):
        if value:
            target[value] = target.get(value, 0) + 1

    for f in inv["files"]:
        t = f["probe"]["tags"]
        hint = inv.get("work_hint") or {}
        title = (
            hint.get("title") if hint.get("use_title") else t.get("album") or hint.get("title") or t.get("title") or ""
        )
        if not grouping.title_hint(title):
            title = hint.get("title") or t.get("title") or ""
        title = re.sub(r"\s*\((?:un)?abridged\)\s*", "", title, flags=re.I).strip()
        add(votes, title)
        who = "" if hint.get("anthology") else grouping.author_credit(t)
        if "/" in who and len(set(who.split("/"))) == 1:
            who = who.split("/")[0]
        if hint and who and merge.norm(who) in merge.norm(hint.get("title", "")):
            who = ""
        reader = t.get("narratedby") or t.get("narrator") or t.get("composer") or ""
        credits = contributors.parse(reader or t.get("album_artist", ""))
        if credits and all(c["role"] in ("narrator", "reader") for c in credits):
            reader = ", ".join(c["name"] for c in credits)
        add(authors, who)
        add(narrators, reader)
        asins.update(embedded_asins(t))
        if t.get("language"):
            languages.add(t["language"].lower())
        if re.search(r"\bunabridged\b", t.get("album", ""), re.I):
            formats.add(False)
        elif re.search(r"\babridged\b", t.get("album", ""), re.I):
            formats.add(True)
        evidence.append(dict(source="embedded", file=f["path"], tags=t))
    for side in inv["sidecars"]:
        a = details.audible_summary(side["raw"])
        add(votes, a["title"])
        add(authors, ", ".join(a["authors"]))
        add(narrators, ", ".join(a["narrators"]))
        if a.get("asin"):
            asins.add(a["asin"])
        fmt = a.get("format")
        if fmt in ("abridged", "unabridged"):
            formats.add(fmt == "abridged")
        evidence.append(dict(source="audible_file", data=a))
    out = dict(book)
    hint = inv.get("work_hint") or {}
    if hint and not authors and "author" not in locked:
        # An absent grouping credit must not erase an existing same-work author.
        if (
            hint.get("author")
            or hint.get("anthology")
            or matcher.norm(hint.get("title", "")) != matcher.norm(book["title"])
        ):
            out["author"] = hint.get("author", "")
    folder_authors = {}
    for f in inv["files"]:
        folder = grouping.folder_identity(f["path"])
        if (
            not hint.get("anthology")
            and folder["author"]
            and matcher.norm(grouping.title_hint(folder["title"])) == matcher.norm(hint.get("title") or out["title"])
        ):
            folder_authors[folder["path"]] = folder
    for folder in folder_authors.values():
        evidence.append(dict(source="folder", path=folder["path"], title=folder["title"], author=folder["author"]))
        if not authors and "author" not in locked:
            out["author"] = hint.get("author") or folder["author"]
    for field, target in [("title", votes), ("author", authors), ("narrator", narrators)]:
        if field not in locked and target:
            out[field] = max(target, key=target.get)
    if "asin" not in locked and len(asins) == 1:
        out["asin"] = next(iter(asins))
    text = " ".join(
        [out["title"], inv["root"]]
        + [f["path"] for f in inv["files"]]
        + [str(f["probe"]["tags"].get(k, "")) for f in inv["files"] for k in f["probe"]["tags"]]
    ).lower()
    kind = (
        "radio"
        if scan.BBC_RADIO.search(text)
        or "radio comedy" in text
        or re.search(r"\bbbc\s+(?:radio|r[1-6]x?)\b", text)
        or re.search(r"\bs\d+\s*-?\s*e\d+", text)
        else "episode"
        if re.search(r"\bep(?:isode)?\.?\s*\d+", text)
        else "standup"
        if "stand-up" in text or "standup" in text
        else "story"
        if re.search(r"\ba story from\b|\bshort story\b", text)
        else "book"
    )
    if (inv.get("work_hint") or {}).get("anthology"):
        kind = "book"
    conflicts = (
        int(bool((inv.get("work_hint") or {}).get("conflicting_album")))
        + int(len(asins) > 1)
        + int(len(languages) > 1)
        + int(len(formats) > 1)
    )
    # Multi-work/narrator folders must not turn into a single book by popularity vote.
    author_lists = [contributors.authors(value) for value in authors]
    shared = [
        name
        for name in (author_lists[0] if author_lists else [])
        if all(any(contributors.same_author(name, other, True) for other in values) for values in author_lists)
    ]
    local_people = contributors.authors(out.get("author", ""))
    conflicts += int(
        any(
            local_people
            and not any(
                contributors.same_author(a, b, True)
                for a in local_people
                for b in contributors.authors(folder["author"])
            )
            for folder in folder_authors.values()
        )
    )
    conflicts += int(len(author_lists) > 1 and not shared) + int(len({matcher.norm(x) for x in votes}) > 1)
    return dict(
        book=out,
        contributors=contributors.parse(out.get("author", "")),
        kind=kind,
        language=next(iter(languages)) if len(languages) == 1 else "",
        abridged=next(iter(formats)) if len(formats) == 1 else None,
        evidence=evidence,
    ), {
        "identity.title_known": int(bool(out.get("title"))),
        "identity.author_known": int(bool(out.get("author"))),
        "identity.conflicts": conflicts,
    }


def unchanged(manifest):
    for f in manifest:
        p = Path(f["path"])
        if not p.is_file() or p.stat().st_size != f["size"] or digest(p) != f["sha256"]:
            return False
    return True


def snapshot_unchanged(inv):
    if not unchanged(inv["files"]):
        return False
    for side in inv.get("sidecars", []):
        if not Path(side["path"]).is_file() or digest(side["path"]) != side["sha256"]:
            return False
    root = Path(inv.get("scope_root") or inv["root"])
    if root.is_dir():
        paths = {
            str(p)
            for p in root.rglob("*")
            if p.is_file()
            and scan.is_audio(p.name)
            and not any(x.startswith((".", "@")) for x in p.relative_to(root).parts)
        }
        if paths != set(inv.get("scope_paths") or [f["path"] for f in inv["files"]]):
            return False
        if {str(p) for p in root.rglob("*.metadata.json")} != set(
            inv.get("scope_sidecars") or [s["path"] for s in inv.get("sidecars", [])]
        ):
            return False
    elif root.with_suffix(".metadata.json").exists() and str(root.with_suffix(".metadata.json")) not in {
        s["path"] for s in inv.get("sidecars", [])
    }:
        return False
    return True
