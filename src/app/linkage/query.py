"""What a listen says about itself, read from the Postgres catalogue: its embedded-tag and folder-name records, its chosen fields and its runtime, turned into several *readings* (title, author) because file and folder names are ambiguous (`Title - Author` and `Author - Title` look alike, tags can be album-level or track-level). The comparison step scores every reading and keeps the best per candidate."""
from __future__ import annotations

import os, re
from dataclasses import dataclass, field

from app import scan
from app.identify import local, textkeys as tk

NOISE = re.compile(r'\((?:un)?abridged[^)]*\)|[_\s]+(?:un)?abridged\b|\b(?:\d+(?:st|nd|rd|th)\s+)?anniversary(?:\s+edition)?\b|\((?:mp3|m4b|aac|audiobook|audio\s*book|retail)\)|\b(?:mp3|m4b)\b|\bunabridged\b', re.I)
ALL_KINDS = ('book', 'radio_series', 'radio_show', 'play', 'other')
GENERIC = re.compile(r'^\s*(track\s*\d*|unknown\b.*|audiobook|disc\s*\d*|chapter\s*\d*|\d+)\s*$', re.I)


@dataclass
class Reading:
    title: str
    author: str = ''
    source: str = 'chosen'        # chosen | tags | folder | swapped | stripped | split


@dataclass
class Query:
    listen_id: int
    kind: str
    label: str = ''
    readings: list[Reading] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    asin: str = ''
    year: int | None = None
    minutes: list[float] = field(default_factory=list)       # one per copy (input part), first = preferred copy
    language: str = ''
    path: str = ''
    hints: list[str] = field(default_factory=list)          # bracketed words in names, often a narrator: 'Great Expectations (Stephen Fry)'
    band: str = ''                                          # the kind classifier's call: broadcast | review | book, '' when it has not run (`store/kinds.py`)
    channels: list[str] = field(default_factory=list)       # BBC services the listen's tags and names point to (`channels_in`), e.g. bbc_radio_four


CHANNELS = (  # (pattern, service id prefix in the BBC programme list)
    (r'radio[\s._-]*4[\s._-]*extra|\br4x\b', 'bbc_radio_four_extra'), (r'radio[\s._-]*4\b|\br4\b|radio four', 'bbc_radio_four'), (r'radio[\s._-]*3\b|\br3\b', 'bbc_radio_three'), (r'radio[\s._-]*2\b|\br2\b', 'bbc_radio_two'), (r'radio[\s._-]*1\b|\br1\b', 'bbc_radio_one'),
    (r'radio[\s._-]*5[\s._-]*live|\br5l\b', 'bbc_radio_five_live'), (r'6[\s._-]*music', 'bbc_6music'), (r'world service', 'bbc_world_service'), (r'radio scotland', 'bbc_radio_scotland'), (r'radio wales|radio cymru', 'bbc_radio_wales'),
)


def channels_in(*texts: str) -> list[str]:
    """The BBC services named in these texts (Radio 4, R3, World Service...), as the ids the programme list uses; a more specific name (Radio 4 Extra) hides the plainer one it contains."""
    t = ' '.join(x for x in texts if x).lower(); found = [svc for pat, svc in CHANNELS if re.search(pat, t)]
    return [svc for svc in found if not any(o != svc and o.startswith(svc + '_') for o in found)]


def clean_title(title: str) -> str:
    """Drop recording labels and file-type words that are never part of a title: '(Unabridged)', '_unabridged', '10th Anniversary Edition', '(MP3)'."""
    t = NOISE.sub(' ', str(title or '')).replace('_', ' ')
    return re.sub(r'\s+', ' ', t).strip(' -.,:')


def author_text(value: str) -> str:
    return ', '.join(tk.people(value))


def make_readings(choices: dict, tags: dict, folder: dict) -> list[Reading]:
    """Readings in order of trust, duplicates (same title and author key) removed. Each yields its own numbered/stripped variants via `identify.local`."""
    def tag(f, minimum=0.5):
        t = tags.get(f) or {}; v = t.get('value') if isinstance(t, dict) and (t.get('share') or 0) >= minimum else None
        return v if v and not GENERIC.match(v) else ''
    raw: list[Reading] = []
    if choices.get('title'): raw.append(Reading(str(choices['title']), str(choices.get('author') or ''), 'chosen'))
    for title_field in ('album', 'title'):
        if (t := tag(title_field)) and (title_field == 'album' or (tags.get('tagged') or 0) <= 3): raw.append(Reading(t, tag('album_artist') or tag('artist'), 'tags'))
    if folder.get('title') and folder['title'].strip().lower() not in scan.GENERIC:
        raw.append(Reading(folder['title'], folder.get('author') or '', 'folder'))
        if folder.get('author'): raw.append(Reading(folder['author'], folder['title'], 'swapped'))       # 'The Secret History - Donna Tartt' parses as author 'The Secret History'
    if folder.get('folder') and folder['folder'].strip().lower() not in scan.GENERIC: raw.append(Reading(folder['folder'], '', 'split'))
    out: list[Reading] = []; seen = set()
    for r in raw:
        author = author_text(r.author); title = clean_title(r.title); bare = re.sub(r'\s*\([^)]*\)\s*', ' ', title).strip()
        variants = local.readings(dict(title=title, author=author)) + (local.readings(dict(title=bare, author=author)) if bare and bare != title else [])
        for v in variants:
            key = (tk.title_key(v['title']), tk.person_key(v['author']))
            if not key[0] or key in seen: continue
            seen.add(key); src = r.source if v['reading'] == 'raw' else ('stripped' if v['reading'] == 'stripped' else 'split')
            out.append(Reading(v['title'], v['author'], src))
    return out[:12]


def from_parts(listen_id: int, kind: str, label: str, choices: dict, tags: dict, folder: dict, inputs: list[dict], band: str = '') -> Query:
    """A Query from the listen's pieces (kept separate from the database read so tests can build one directly)."""
    narr = choices.get('narrator') or (tags.get('narratedby') or {}).get('value') or folder.get('narrator') or ''
    asin = tk.asin(choices.get('asin') or (tags.get('audible_asin') or {}).get('value') or folder.get('asin') or '')
    year = choices.get('year') if isinstance(choices.get('year'), int) else None
    hints = [h.strip() for t in (choices.get('title'), folder.get('folder'), (tags.get('album') or {}).get('value')) if t for h in re.findall(r'\(([^)]{3,60})\)', str(t)) if not NOISE.fullmatch(f'({h})') and not re.fullmatch(r'[\d\s-]+', h)]
    return Query(listen_id, kind, label, make_readings(choices, tags, folder), tk.people(narr), asin, year, [float(i['minutes']) for i in inputs if i.get('minutes')], str(choices.get('language') or ''), (inputs[0].get('path') or '') if inputs else '', list(dict.fromkeys(hints)), band,
                 channels_in(label, str(choices.get('title') or ''), str(choices.get('author') or ''), str(folder.get('folder') or ''), *[str((tags.get(f) or {}).get('value') or '') for f in ('album', 'artist', 'album_artist', 'composer', 'publisher', 'genre')]))


def load(s, kinds=ALL_KINDS, ids=None) -> list[Query]:  # pragma: no cover - Postgres: tests/unit/store/test_linkage_store.py
    """Queries for every live listen of these kinds (all of them by default: a radio programme is a candidate source too), or the given ids."""
    from sqlalchemy import text
    where = 'l.merged_into_id is null and l.retired_at is null and l.kind = any(:kinds)' + (' and l.id = any(:ids)' if ids else '')
    rows = s.execute(text(f"""select l.id, l.kind, l.label,
        (select jsonb_object_agg(c.field, c.value) from listen_choice c where c.listen_id = l.id and c.unchosen_at is null) as choices,
        (select jsonb_object_agg(r.source, r.payload) from listen_part p join part_record r on r.id = p.record_id where p.listen_id = l.id and p.role = 'record' and p.unlinked_at is null) as records,
        (select jsonb_agg(p.detail order by p.ordinal) from listen_part p where p.listen_id = l.id and p.role = 'input' and p.unlinked_at is null) as inputs,
        (select band from listen_kind k where k.listen_id = l.id) as band
        from listen l where {where} order by l.id"""), dict(kinds=list(kinds), ids=list(ids or []))).all()
    out = []
    for lid, kind, label, choices, records, inputs, band in rows:
        records = records or {}
        out.append(from_parts(lid, kind, label or '', choices or {}, records.get('embedded_tags') or {}, records.get('folder_name') or {}, inputs or [], band or ''))
    return out


def relpath(path: str) -> str:
    """The library-relative path (after the audio root), for matching listens to labels made under another mount point."""
    parts = os.path.normpath(path or '').split(os.sep)
    for i, p in enumerate(parts):
        if p in ('audio', 'audio-local'): return '/'.join(parts[i + 1:])
    return '/'.join(parts[-2:])
