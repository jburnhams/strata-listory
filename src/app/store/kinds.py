"""Is a listen a book, or a radio programme / play / series? A scored classification with its reasons, not a set of hard rules.

Every piece of evidence is a *signal* with a weight in log-odds (positive: broadcast, negative: book); the weights add to a prior and a logistic turns the sum into `p_broadcast`. The result is a `Verdict`: the kind, the probability, a band (`broadcast`, `book`, `review`) and the signals that fired, so a card can say why. Weights are hand-set from reading the real library (see docs/listen-kinds.md), not fitted: there are few labelled examples and the point is to be explainable.

The key distinction is **reading versus broadcast**. An unabridged reading narrated by a BBC actor, or published by BBC Audio, is a book; a dramatisation, a series of episodes or a recording of a transmission is not. So "BBC" as a publisher or narrator's employer counts for nothing by itself; a *channel* (Radio 4, R3, World Service, iPlayer), a programme strand (Drama on 3, Afternoon Play), episode/series wording, a radio genre tag, broadcast-sized parts and an Audible content type of "Radio/TV Program" count for broadcast, while "unabridged", "read by", an Audible "Audiobook" recording of the same length and a long runtime count for book.

Kinds: `book`; `radio_series` (numbered series/episodes, several broadcast-sized parts); `radio_show` (one programme: a documentary, a stand-up, a one-off); `play` (a drama or dramatisation: radio play, stage play, full-cast production). Pure functions over a `Features` record, so the database and the tests feed the same code."""
from __future__ import annotations

import math, re
from dataclasses import dataclass, field

VERSION = 1
PRIOR = -1.6                  # most listens are books (about 87% of this library)
BROADCAST_AT = 0.85           # p_broadcast at or above: a programme, play or series
BOOK_AT = 0.25                # at or below: a book; in between: needs a person's look
SLOT_MINUTES = (14, 15, 28, 29, 30, 43, 44, 45, 58, 59, 60)      # the lengths of broadcast slots: a part this long is a programme, books are cut by size not by schedule
LONG_BOOK_MINUTES = 360       # nobody broadcasts six hours as one listen; omnibus editions of a serial are the exception, so only a nudge
PLAY_MINUTES = 140            # one or two files and under this long: play length

CHANNEL = re.compile(r'\bbbc[\s._-]*(?:radio[\s._-]*(?:[1-7]\b|four|three|two|one|scotland|wales|ulster|cymru|nan|wm|london)|r[1-7]x?\b|world service|iplayer|sounds)|\bradio[\s._-]*4[\s._-]*extra\b|\bradio[\s._-]*[1-7]\b|\bworld service\b|\b(?:bbc\s+)?r[1-7]x?\s+(?:drama|comedy|extra)\b|\bget_iplayer\b|\bbbc\s+iplayer\b|\brte\s+radio\b|\bradio\s+times\b', re.I)
BARE_RADIO = re.compile(r'^\s*(?:bbc\s+)?radio\s*$', re.I)               # an author or artist that is just "BBC Radio"
BARE_BBC = re.compile(r'^\s*bbc(?:\s+(?:audio|digital audio|radio\s*collection))?\s*$', re.I)
STRAND = re.compile(r'\b(?:drama on (?:3|four|three)|afternoon (?:drama|play|reading)|saturday (?:play|night theatre)|sunday (?:play|feature)|the monday play|the wednesday play|woman.?s hour|book (?:at bedtime|of the week)|friday play|classic serial|the archers|feature on 4|start the week|desert island discs)\b', re.I)
RADIO_ADAPTATION = re.compile(r'\bradio\s+(?:adaptations?|dramas?|plays?|dramati[sz]ations?|serials?|series|comedy|shows?|collection|programmes?|specials?|versions?)\b|\bbbc\s+audio\s+adaptations?\b|\bbroadcast\b', re.I)
DRAMATISED = re.compile(r'\b(?:dramati[sz]ed|dramati[sz]ations?|dramatis(?:e|ation)|full[- ]cast (?:production|dramati[sz]ation|drama|recording|audio|performance)|adapted (?:by|for)|adaptation by|a play (?:by|in)|radio play|stage play|audio drama|an? (?:\w+ )?audio original)\b', re.I)
FULL_CAST = re.compile(r'\bfull[- ]cast\b', re.I)
SERIES_N = re.compile(r'(?:\bseries|\bseason)\s*\d+|\bs\d{1,2}\s*e\d{1,3}\b|\bs\d{1,2}\b(?!\w)', re.I)
EPISODE_N = re.compile(r'\bep(?:isode)?\.?\s*\d+\b|\bepisodes?\s+\d+\s*(?:-|to|&|and)\s*\d+', re.I)
DATED = re.compile(r'\b(?:19|20)\d\d[-._]\d\d[-._]\d\d\b|\b\d{1,2}(?:st|nd|rd|th)?[\s._-]+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s._-]+(?:19|20)\d\d\b|\(\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(?:19|20)\d\d\s*\)', re.I)
UNABRIDGED = re.compile(r'\b(?:un)?abridged\b', re.I)
READING = re.compile(r'\b(?:read|narrated|performed) by\b|\bclassic readings?\b|\bunabridged reading\b|\bthe reading\b|\baudio ?books?\b|\bhörbuch\b', re.I)
RADIO_GENRES = {'radio drama', 'radio comedy', 'radio show', 'radio', 'wireless', 'podcast', 'bbc r4 drama', 'bbc radio', 'get_iplayer', 'radio play', 'radio documentary', 'radio 4', 'drama on 3', 'radio feature', 'radio series', 'spoken word radio'}
DRAMA_GENRES = {'drama', 'comedy', 'factual', 'documentary', 'standup', 'stand-up', 'humour', 'comedy/drama', 'theatre', 'play', 'plays', 'news', 'current affairs', 'music', 'features', 'abstract'}
BOOK_GENRES = re.compile(r'audio ?books?|books? ?& ?spoken|hörbuch|literature|fiction|biograph|nonfiction|non-fiction|memoir|speech$', re.I)
PLAY_WORDS = re.compile(r'\bplays?\b|\bdrama\b|\btheatre\b|\bdramati[sz]', re.I)
BBC_PUBLISHERS = re.compile(r'^bbc(?: audio| digital audio| audiobooks(?: america)?| worldwide.*| children.s books| active| digital)?$', re.I)


@dataclass
class Features:
    """What is known about one listen. Text fields are free text from tags or folder names; every field may be empty."""
    structure_kind: str = 'book'                  # what the structure step decided: book | programme
    structure_reason: str = ''
    label: str = ''                               # the listen's title
    author: str = ''
    narrator: str = ''
    publisher: str = ''
    folder: str = ''                              # folder name (and folder-derived title)
    album: str = ''
    track_title: str = ''
    artist: str = ''
    album_artist: str = ''
    composer: str = ''
    genre: str = ''
    comment: str = ''
    tag_publisher: str = ''
    asin: str = ''
    minutes: float = 0.0                          # total length of the largest copy
    files: int = 0
    part_minutes: list[float] = field(default_factory=list)      # per-file lengths in minutes, when known
    audible: list[dict] = field(default_factory=list)            # matched Audible recordings: content_type, format, publisher, keywords, minutes, diff (relative runtime difference to this listen)
    radio_hits: list[dict] = field(default_factory=list)         # titles found in the radio programme index: source, title, service


@dataclass
class Signal:
    name: str
    weight: float
    detail: str = ''
    def as_dict(self) -> dict: return dict(name=self.name, weight=round(self.weight, 2), detail=self.detail)


@dataclass
class Verdict:
    kind: str                 # book | radio_series | radio_show | play
    p_broadcast: float
    confidence: float         # how sure of `kind` (for a book, 1 - p_broadcast)
    band: str                 # broadcast | book | review
    signals: list[Signal]
    def as_dict(self) -> dict: return dict(kind=self.kind, p_broadcast=round(self.p_broadcast, 4), confidence=round(self.confidence, 4), band=self.band, version=VERSION, signals=[s.as_dict() for s in self.signals])


def _texts(f: Features) -> dict[str, str]:
    return {'title': f.label, 'author': f.author, 'narrator': f.narrator, 'publisher': f.publisher or f.tag_publisher, 'folder': f.folder, 'album': f.album, 'track': f.track_title, 'artist': f.artist, 'album artist': f.album_artist, 'composer': f.composer, 'comment': f.comment}


def _first(pattern: re.Pattern, texts: dict[str, str], where: tuple[str, ...] | None = None) -> tuple[str, str] | None:
    for k, v in texts.items():
        if where and k not in where: continue
        m = pattern.search(v or '')
        if m: return k, m.group(0)
    return None


CONTENT_WEIGHT = {'broadcast': 2.5, 'show': 2.0, 'performance': 1.5}


def _content_class(ct: str) -> str:
    """Audible's content type as book / broadcast / show / performance ('' when it says nothing useful). BBC Audio files readings under "Radio/TV Program" too, which is why this is evidence, not a rule."""
    c = ct.strip().lower()
    if c in ('product', 'audiobook', 'audiobook/fiction', 'audiobook/nonfiction', 'lecture'): return 'book'
    if re.search(r'radio|tv|television|program', c): return 'broadcast'
    return c if c in ('show', 'performance') else ''


def _slot_share(parts: list[float]) -> float:
    if len(parts) < 2: return 0.
    return sum(any(abs(p - s) <= 1.6 for s in SLOT_MINUTES) for p in parts) / len(parts)


def signals(f: Features) -> list[Signal]:
    out: list[Signal] = []
    add = lambda name, w, detail='': out.append(Signal(name, w, detail))
    t = _texts(f); minutes = f.minutes or 0.

    if f.structure_kind == 'programme': add('structure', 2.0, f.structure_reason or 'the file structure looks like numbered episodes or dated broadcasts')

    # --- where it was broadcast
    hit = _first(CHANNEL, t)
    if hit: add('channel', 3.0, f'{hit[0]}: "{hit[1]}"')
    elif _first(BARE_RADIO, t, ('author', 'artist', 'album artist', 'composer', 'narrator')): add('radio credit', 2.2, 'credited to "Radio"')
    else:
        strand = _first(STRAND, t)
        if strand: add('strand', 2.2 if 'bedtime' not in strand[1].lower() else 1.0, f'{strand[0]}: "{strand[1]}"')
    if not hit and (b := _first(BARE_BBC, t, ('author', 'artist', 'album artist', 'composer'))): add('bbc credit', 1.0, f'{b[0]}: "{b[1]}"')
    ra = _first(RADIO_ADAPTATION, t)
    if ra: add('radio wording', 1.6 if re.search(r'collection|shows?|programmes?|specials?|versions?|series|broadcast', ra[1], re.I) else 3.3, f'{ra[0]}: "{ra[1]}"')
    dr = _first(DRAMATISED, t)
    if dr: add('dramatised', 1.8, f'{dr[0]}: "{dr[1]}"')
    elif _first(FULL_CAST, t, ('narrator', 'title', 'artist', 'comment')): add('full cast', 0.4, 'full cast named')

    # --- episodes
    s = _first(SERIES_N, t, ('title', 'folder', 'album', 'track', 'comment'))
    if s: add('series number', 1.8, f'{s[0]}: "{s[1]}"')
    e = _first(EPISODE_N, t, ('title', 'folder', 'album', 'track'))
    if e: add('episode number', 1.1, f'{e[0]}: "{e[1]}"')
    d = _first(DATED, t, ('title', 'folder', 'album', 'track'))
    if d: add('broadcast date', 1.3, f'{d[0]}: "{d[1]}"')

    # --- tags
    g = (f.genre or '').strip().lower()
    if g in RADIO_GENRES: add('radio genre', 2.5, f'genre "{f.genre}"')
    elif g in DRAMA_GENRES: add('programme genre', 0.8, f'genre "{f.genre}"')
    elif g and BOOK_GENRES.search(g): add('book genre', -0.8, f'genre "{f.genre}"')

    # --- reading, not broadcast
    ub = _first(UNABRIDGED, t, ('title', 'album', 'folder', 'track'))
    if ub: add('unabridged', -2.2, f'{ub[0]}: "{ub[1]}"')
    rd = _first(READING, t, ('title', 'album', 'folder', 'narrator', 'comment', 'author', 'artist', 'track'))
    if rd and not ub: add('reading', -2.0, f'{rd[0]}: "{rd[1]}"')
    if f.asin: add('audible id', -1.2, 'the tags carry an Audible ASIN')

    # --- length and shape
    if f.files >= 3 and (share := _slot_share(f.part_minutes)) >= 0.8: add('slot-length parts', 1.2, f'{share:.0%} of {len(f.part_minutes)} parts are about 14, 28, 44 or 58 minutes long')
    if 0 < minutes <= PLAY_MINUTES and 0 < f.files <= 3 and f.structure_kind != 'programme': add('play length', 0.6, f'{minutes:.0f} minutes in {f.files} file{"s" if f.files != 1 else ""}')
    names = [n for n in re.split(r'\s*(?:,|&|/| and )\s*', f.narrator or '') if n.strip()]
    if len(names) >= 3: add('cast list', 0.8, f'{len(names)} names credited as narrator')

    if not any(x.name in ('channel', 'strand', 'radio genre', 'radio wording', 'slot-length parts') for x in out):       # a long listen is a book unless it is plainly an archive of broadcasts
        if minutes >= 600: add('long', -3.0, f'{minutes / 60:.0f} hours')
        elif minutes >= LONG_BOOK_MINUTES: add('long', -1.8, f'{minutes / 60:.1f} hours')

    # --- catalogues
    near = sorted((a for a in f.audible if a.get('diff') is not None and a['diff'] <= 0.06 and (a.get('p') or 0) >= 0.5), key=lambda a: a['diff'])
    if near:
        tied = [a for a in near if a['diff'] <= near[0]['diff'] + 0.01]; kinds_ = {_content_class(a.get('content_type') or '') for a in tied} - {''}
        if len(kinds_) > 1: add('audible disagrees', 0.0, 'Audible lists recordings of this length both as an audiobook and as a programme')
        elif kinds_ == {'book'}: add('audible audiobook', -3.0, f'the matching Audible recording ({tied[0].get("asin", "")}) is an audiobook')
        elif kinds_: ct = tied[0]['content_type']; add('audible content type', CONTENT_WEIGHT[next(iter(kinds_))], f'Audible lists the matching recording ({tied[0].get("asin", "")}) as "{ct}"')
    for a in f.audible:
        kw = ' '.join(a.get('keywords') or []).lower()
        if a.get('diff') is not None and a['diff'] <= 0.06 and re.search(r'dramati[sz]|radio|full cast', kw): add('audible keywords', 1.4, f'Audible keywords: {kw[:60]}'); break
    if f.radio_hits:
        drama = any(h['source'] == 'wikidata_drama' for h in f.radio_hits); bbc = any(h['source'] == 'bbc_radio' for h in f.radio_hits)
        add('programme catalogue', 1.0 + 0.6 * (bbc and (drama or len(f.radio_hits) > 1)), '; '.join(f'{h["source"].replace("_", " ")}: {h["title"]}' for h in f.radio_hits[:2]))
    return out


def _kind(f: Features, sigs: list[Signal]) -> str:
    names = {s.name for s in sigs}; t = _texts(f)
    seriesish = bool(names & {'series number', 'episode number', 'slot-length parts'}) or f.structure_kind == 'programme' and f.files >= 3 and not names & {'dramatised'}
    playish = 'dramatised' in names or bool(PLAY_WORDS.search(' '.join([f.label, f.genre, f.folder, f.album]))) or 'strand' in names and bool(re.search(r'drama|play|theatre|classic serial', ' '.join(s.detail for s in sigs if s.name == 'strand'), re.I))
    if seriesish and not playish: return 'radio_series'
    if playish and not (seriesish and f.files >= 4 and 'series number' in names): return 'play'
    return 'radio_series' if seriesish else 'radio_show'


def classify(f: Features) -> Verdict:
    sigs = signals(f); total = PRIOR + sum(s.weight for s in sigs)
    p = 1 / (1 + math.exp(-total))
    band = 'broadcast' if p >= BROADCAST_AT else 'book' if p <= BOOK_AT else 'review'
    if band == 'book': return Verdict('book', p, 1 - p, band, sigs)
    kind = _kind(f, sigs)
    if band == 'review' and f.structure_kind != 'programme': return Verdict('book', p, 1 - p, band, sigs)          # not sure: leave the listen as it was, flagged for a look; the guess is in the signals
    return Verdict(kind, p, p if band == 'broadcast' else max(p, 1 - p), band, sigs)


def label_of(kind: str) -> str:
    return {'book': 'book', 'radio_series': 'radio series', 'radio_show': 'radio programme', 'play': 'play'}.get(kind, kind)
