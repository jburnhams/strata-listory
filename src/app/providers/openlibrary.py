"""Open Library (https://openlibrary.org): an open, free book catalogue with a documented API and public bulk dumps (personal use; identify yourself in the User-Agent, which the shared client does). It is about books, not audiobooks, so it has no narrators or runtimes, but it knows when a work was first published, which the audiobook sources do not, plus reader ratings and subjects. Used to find the original publication year (for the folder name) and as extra metadata; it is not part of the default title matching."""
import re
import urllib.parse

from app.providers.base import Http, MatchError, Provider, candidate
from app import book_titles

FIELDS = 'key,title,author_name,first_publish_year,ratings_average,ratings_count,subject,edition_count,cover_i'


def edition_language(entry):
    codes={v.get('key','').rsplit('/',1)[-1] if isinstance(v,dict) else str(v).rsplit('/',1)[-1] for v in entry.get('languages') or []}
    return next(iter(codes)) if len(codes)==1 else ''


def to_candidate(d: dict) -> dict:
    cover = f"https://covers.openlibrary.org/b/id/{d['cover_i']}-L.jpg" if d.get('cover_i') else ''
    c = candidate('openlibrary', id=d.get('key', ''), title=d.get('title', ''), authors=d.get('author_name') or [], cover_url=cover, rating=d.get('ratings_average'), ratings_count=d.get('ratings_count'), genres=(d.get('subject') or [])[:8],
                  url=f"https://openlibrary.org{d['key']}" if d.get('key') else '')
    c['original_year'] = d.get('first_publish_year')
    c['subjects'] = (d.get('subject') or [])[:25]; c['edition_count'] = d.get('edition_count')
    return c


class OpenLibrary(Provider):
    name = 'openlibrary'; label = 'Open Library'; site = 'https://openlibrary.org'
    min_interval = 0.5; cache_days = 30; matching = False          # a work's first publication year does not change: a month is plenty

    def __init__(self): self.http = Http('Open Library', self.min_interval)

    def search(self, title: str, author: str = '') -> list[dict]:
        t = title.strip()[:120]
        if len(t) < 2: return []
        from app.providers import openlibrary_local
        from app import matcher
        try:local=openlibrary_local.search(t,author)
        except (OSError,openlibrary_local.sqlite3.Error):local=[]
        def accepted(c):
            return book_titles.analyse(t,c,author,local)['score']>=.85 and (not author or matcher.author_score(author,c)>=.9)
        # The first index snapshot stored main titles only. Hydrate subtitles only
        # for ambiguous or author-named works, and cache the small work response.
        variants=[]
        for c in local:
            main=book_titles.parts(c['title'])[0]
            competing=sum(book_titles.compact(book_titles.parts(other['title'])[0])==book_titles.compact(main) for other in local)>1
            if re.fullmatch(r'/works/OL\d+W',c['id']) and (competing or not accepted(c)):
                try:raw=self.cache('work-title:'+c['id'],lambda c=c:self.http.request('https://openlibrary.org'+c['id']+'.json'))
                except MatchError:continue
                if raw and raw.get('subtitle'):c['subtitle']=raw['subtitle']
                if not c.get('subtitle'):
                    try:editions=self.cache(f"publisher-editions:{c['id']}:0",lambda c=c:self.http.request(f"https://openlibrary.org{c['id']}/editions.json?limit=100&offset=0"))
                    except MatchError:continue
                    titles=[]
                    for edition in sorted((editions or {}).get('entries',[]),key=lambda e:edition_language(e) in ('eng','en','en-gb','en-us'),reverse=True):
                        if c['id'] not in [work.get('key') for work in edition.get('works',[]) if isinstance(work,dict)]:continue
                        em,es=book_titles.parts(edition.get('title',''),edition.get('subtitle',''))
                        if not es or not book_titles.similar(main,em) or any(book_titles.similar(es,old['subtitle']) for old in titles):continue
                        titles.append(dict(c,subtitle=es,subtitle_edition=edition.get('key',''),language=edition_language(edition)))
                    if titles:c.update(titles[0]);variants.extend(titles[1:])
        local.extend(variants)
        if local and any(accepted(c) for c in local):return local
        q = dict(title=t, limit=5, fields=FIELDS, **({'author': author.strip()[:60]} if author.strip() else {}))
        raw = self.cache(f'search:{t.lower()}:{author.strip().lower()}', lambda: self.http.request('https://openlibrary.org/search.json?' + urllib.parse.urlencode(q)))
        return [to_candidate(d) for d in (raw or {}).get('docs', [])]


    def edition_publishers(self,work_key):
        """Small cached edition lookups for a known work; no editions dump is required."""
        if not re.fullmatch(r'/works/OL\d+W',work_key or ''):return []
        from app.providers import openlibrary_local
        local=openlibrary_local.editions(work_key)
        if local:
            return [e for e in local if e['year'] and not re.search(r'audio|mp3|cassette',e['physical_format'],re.I)]
        from app.providers.openlibrary_local import year
        result=[]
        for offset in (0,100,200):
            raw=self.cache(f'publisher-editions:{work_key}:{offset}',lambda offset=offset:self.http.request(f'https://openlibrary.org{work_key}/editions.json?limit=100&offset={offset}'))
            entries=(raw or {}).get('entries',[])
            for entry in entries:
                if re.search(r'audio|mp3|cassette',str(entry.get('physical_format','')),re.I):continue
                if work_key not in [work.get('key') for work in entry.get('works',[]) if isinstance(work,dict)]:continue
                published=year(entry.get('publish_date'))
                if published:result.append(dict(key=entry.get('key',''),title=entry.get('title',''),subtitle=entry.get('subtitle',''),work_key=work_key,year=published,publish_date=entry.get('publish_date',''),publishers=[str(p) for p in entry.get('publishers',[]) if p],languages=[lang.get('key') for lang in entry.get('languages',[]) if isinstance(lang,dict)],isbns=list(dict.fromkeys(str(v) for field in ('isbn_10','isbn_13') for v in entry.get(field) or []))))
            if len(entries)<100:break
        return result


    def publication_editions(self,c,original_year=None):
        """Resolve empty duplicate works through small, identity-checked edition searches."""
        editions=self.edition_publishers(c['id'])
        if editions and (original_year is None or any(e['year']==original_year and e['publishers'] for e in editions)):return editions
        from app import matcher
        title=c['title'].split(':',1)[0].strip();author=c.get('author') or ', '.join(c.get('authors',[]))
        query=dict(title=title,author=author,limit=5,fields=FIELDS)
        if original_year:query['first_publish_year']=original_year
        raw=self.cache(f'publisher-work:{title}:{author}:{original_year}',lambda:self.http.request('https://openlibrary.org/search.json?'+urllib.parse.urlencode(query)))
        for doc in (raw or {}).get('docs',[]):
            alternate=to_candidate(doc)
            if alternate['id']==c['id'] or matcher.author_score(author,alternate)<.9:continue
            found=self.edition_publishers(alternate['id'])
            matching=[e for e in found if matcher.title_score(c['title'],dict(title=e['title'],subtitle=e['subtitle']))>=.9]
            if matching:editions.extend(matching)
        return editions
