"""An atomically published SQLite/FTS index of Open Library's public monthly dumps."""
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re
import sqlite3
import time
import unicodedata

from app import book_titles, contributors, oslib, settings
from app.providers.base import candidate
from app.ingest.observe import emit

VERSION=4


def folder():return Path(settings.data_dir())/'openlibrary'


def normalise(value):
    text=''.join(c for c in unicodedata.normalize('NFKD',value.casefold()) if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[^\W_]+',text))


def status():
    root=folder()
    try:return json.loads((root/'status.json').read_text(encoding='utf8'))
    except (OSError,ValueError):return dict(ready=False,stage='not_installed')


def _progress(stage,**values):
    root=folder();root.mkdir(parents=True,exist_ok=True)
    data=dict(status(),stage=stage,updated=datetime.now(timezone.utc).isoformat(),**values)
    temp=root/'status.json.tmp';temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf8');temp.replace(root/'status.json')


def records(path,sizes=None):
    """The fifth TSV column is JSON; gzip validates the entire stream before publication."""
    with gzip.open(path,'rt',encoding='utf8') as stream:
        for line in stream:
            fields=line.rstrip('\n').split('\t',4)
            if len(fields)!=5:continue
            yield fields[1],json.loads(fields[4])
        if sizes is not None:sizes[path.name]=stream.tell()


def year(value):
    found=re.search(r'\b(1\d{3}|20\d{2})\b',str(value or ''))
    return int(found[1]) if found else None


def keep_edition(languages):
    known={value for value in languages if re.fullmatch(r'/languages/[a-z]{3}',value) and value not in ('/languages/und','/languages/mul','/languages/zxx')}
    return not known or '/languages/eng' in known


def build(dumps=None,wait=False,editions=False):
    root=folder();root.mkdir(parents=True,exist_ok=True);source=Path(dumps) if dumps else root/'dumps'
    paths={kind:source/f'{kind}.txt.gz' for kind in (('authors','works','ratings','editions') if editions else ('authors','works','ratings'))}
    for path in paths.values():
        if not path.is_file() and not wait:raise ValueError(f'missing Open Library dump: {path}')
    with (root/'import.lock').open('a+b') as lock,oslib.file_lock(lock):
        target=root/'index.sqlite.building';target.unlink(missing_ok=True)
        connection=sqlite3.connect(target)
        try:
            connection.executescript('''PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-65536; PRAGMA temp_store=FILE;
                CREATE TABLE authors(key TEXT PRIMARY KEY,name TEXT NOT NULL,aliases TEXT NOT NULL);
                CREATE TABLE works(key TEXT PRIMARY KEY,title TEXT NOT NULL,normalised TEXT NOT NULL,authors TEXT NOT NULL,year INTEGER,subjects TEXT NOT NULL,cover INTEGER);
                CREATE TABLE work_authors(work TEXT NOT NULL,author TEXT NOT NULL);
                CREATE TABLE ratings(work TEXT PRIMARY KEY,total INTEGER NOT NULL,count INTEGER NOT NULL);
                CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE editions(key TEXT PRIMARY KEY,title TEXT NOT NULL,subtitle TEXT NOT NULL,year INTEGER,publish_date TEXT NOT NULL,publishers TEXT NOT NULL,languages TEXT NOT NULL,format TEXT NOT NULL,isbns TEXT NOT NULL);
                CREATE TABLE edition_works(edition TEXT NOT NULL,work TEXT NOT NULL);
                CREATE TABLE edition_isbns(isbn TEXT NOT NULL,edition TEXT NOT NULL);''')
            counts={};sizes={};started=time.monotonic()
            for kind in ('authors','works'):
                while not paths[kind].is_file():_progress('waiting_'+kind);time.sleep(5)
                count=0;batch=[];links=[];_progress(kind,records=0)
                for key,data in records(paths[kind],sizes):
                    if kind=='authors':
                        if not isinstance(data.get('name'),str) or not data['name']:continue
                        batch.append((key,data['name'],' '.join(str(n) for n in (data.get('alternate_names') or [])[:30])))
                    else:
                        if not isinstance(data.get('title'),str) or not data['title']:continue
                        people=[(a.get('author') or a).get('key') for a in (data.get('authors') or []) if isinstance(a,dict) and isinstance(a.get('author') or a,dict)]
                        people=list(dict.fromkeys(p for p in people if p))
                        covers=data.get('covers') or []
                        batch.append((key,data['title'],normalise(data['title']),json.dumps(people),year(data.get('first_publish_date')),json.dumps((data.get('subjects') or [])[:8]),next((n for n in covers if isinstance(n,int) and n>0),None)))
                        links.extend((key,person) for person in people)
                    count+=1
                    if len(batch)>=5000:
                        _flush(connection,kind,batch,links);batch=[];links=[]
                        if count%100000==0:_progress(kind,records=count)
                _flush(connection,kind,batch,links);counts[kind]=count
            while not paths['ratings'].is_file():_progress('waiting_ratings');time.sleep(5)
            _progress('ratings',records=0);batch=[];count=0
            with gzip.open(paths['ratings'],'rt',encoding='utf8') as stream:
                for line in stream:
                    fields=line.rstrip('\n').split('\t')
                    if len(fields)!=4 or not fields[0].startswith('/works/') or fields[2] not in ('1','2','3','4','5'):continue
                    batch.append((fields[0],int(fields[2]),1));count+=1
                    if len(batch)>=5000:
                        _ratings(connection,batch);batch=[]
                        if count%100000==0:_progress('ratings',records=count)
                sizes['ratings.txt.gz']=stream.tell()
            _ratings(connection,batch);counts['ratings']=count
            if editions:
                while not paths['editions'].is_file():_progress('waiting_editions');time.sleep(5)
                _progress('editions',records=0);batch=[];links=[];identifiers=[];count=0
                for key,data in records(paths['editions'],sizes):
                    if not re.fullmatch(r'/books/OL\d+M',key) or not isinstance(data.get('title'),str):continue
                    work_keys=list(dict.fromkeys(w['key'] for w in data.get('works') or [] if isinstance(w,dict) and re.fullmatch(r'/works/OL\d+W',w.get('key') or '')))
                    isbns=list(dict.fromkeys(re.sub(r'[-\s]','',str(v)).upper() for field in ('isbn_10','isbn_13') for v in data.get(field) or []))
                    isbns=[v for v in isbns if re.fullmatch(r'\d{9}[\dX]|\d{13}',v)]
                    languages=[v.get('key','') for v in data.get('languages') or [] if isinstance(v,dict) and isinstance(v.get('key',''),str)]
                    if not keep_edition(languages):continue
                    publishers=[str(v) for v in data.get('publishers') or [] if v]
                    date=str(data.get('publish_date') or '')
                    batch.append((key,data['title'],str(data.get('subtitle') or ''),year(date),date,json.dumps(publishers),json.dumps(languages),str(data.get('physical_format') or ''),json.dumps(isbns)))
                    links.extend((key,w) for w in work_keys);identifiers.extend((isbn,key) for isbn in isbns);count+=1
                    if len(batch)>=5000:
                        _editions(connection,batch,links,identifiers);batch=[];links=[];identifiers=[]
                        if count%100000==0:_progress('editions',records=count)
                _editions(connection,batch,links,identifiers);counts['editions']=count
            _progress('indexing',records=0,counts=counts)
            connection.executescript('''CREATE INDEX editions_work ON edition_works(work,edition);
                CREATE INDEX editions_isbn ON edition_isbns(isbn,edition);
                CREATE INDEX editions_edition ON edition_works(edition,work);
                CREATE INDEX works_title ON works(normalised);
                CREATE INDEX work_authors_author ON work_authors(author,work);
                CREATE VIRTUAL TABLE titles USING fts5(title,content='works',content_rowid='rowid',tokenize='unicode61 remove_diacritics 2');
                INSERT INTO titles(titles) VALUES('rebuild');
                CREATE VIRTUAL TABLE names USING fts5(name,aliases,content='authors',content_rowid='rowid',tokenize='unicode61 remove_diacritics 2');
                INSERT INTO names(names) VALUES('rebuild');''')
            provenance={}
            for kind,path in paths.items():
                try:info=json.loads((source/f'{kind}.download.json').read_text())
                except (OSError,ValueError):info={}
                provenance[kind]=dict(uncompressed_bytes=sizes[path.name],bytes=path.stat().st_size,url=info.get('url_effective',''),file=path.name)
            summary=dict(version=VERSION,counts=counts,editions=editions,edition_languages='english_or_unknown' if editions else None,dumps=provenance,built=datetime.now(timezone.utc).isoformat())
            connection.execute('INSERT INTO metadata VALUES(?,?)',('snapshot',json.dumps(summary)));connection.commit()
            check=connection.execute('PRAGMA quick_check').fetchone()[0]
            if check!='ok':raise ValueError(f'Open Library index verification failed: {check}')
            connection.close()
            with (root/'index.lock').open('a+b') as publication,oslib.file_lock(publication):target.replace(root/'index.sqlite')
            _progress('ready',ready=True,records=0,error='',**summary,elapsed=round(time.monotonic()-started,1))
            return status()
        except BaseException as error:
            connection.close();_progress('failed',error=str(error));raise


def compact_editions():
    """Filter an existing snapshot without a new import; publish only a verified compact copy."""
    root=folder()
    with (root/'import.lock').open('a+b') as lock,oslib.file_lock(lock):
        source=root/'index.sqlite';target=root/'index.sqlite.compacting'
        if not source.is_file():raise ValueError('no published Open Library index')
        original=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)
        try:summary=json.loads(original.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0])
        except BaseException:original.close();raise
        if not summary.get('editions'):
            original.close();raise ValueError('published index has no editions')
        if summary.get('edition_languages')=='english_or_unknown':
            original.close();return summary
        target.unlink(missing_ok=True);connection=sqlite3.connect(target)
        try:
            _progress('filtering_editions',ready=True)
            original.backup(connection);original.close()
            connection.executescript("""PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=FILE;
                CREATE TEMP TABLE excluded(key TEXT PRIMARY KEY);
                INSERT INTO excluded SELECT e.key FROM editions e
                WHERE EXISTS (SELECT 1 FROM json_each(e.languages) WHERE value GLOB '/languages/[a-z][a-z][a-z]' AND value NOT IN ('/languages/und','/languages/mul','/languages/zxx'))
                  AND NOT EXISTS (SELECT 1 FROM json_each(e.languages) WHERE value='/languages/eng');""")
            removed=connection.execute('SELECT count(*) FROM excluded').fetchone()[0]
            with connection:
                for table in ('edition_works','edition_isbns'):connection.execute(f'DELETE FROM {table} WHERE edition IN (SELECT key FROM excluded)')
                connection.execute('DELETE FROM editions WHERE key IN (SELECT key FROM excluded)')
                summary.update(version=VERSION,edition_languages='english_or_unknown',editions_excluded=removed,compacted=datetime.now(timezone.utc).isoformat())
                summary['counts']['editions']=connection.execute('SELECT count(*) FROM editions').fetchone()[0]
                connection.execute("UPDATE metadata SET value=? WHERE key='snapshot'",(json.dumps(summary),))
            _progress('compacting_editions',ready=True,editions_excluded=removed)
            connection.execute('VACUUM')
            if connection.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('compacted index verification failed')
            connection.close()
            with (root/'index.lock').open('a+b') as publication,oslib.file_lock(publication):target.replace(source)
            _progress('ready',ready=True,error='',**summary)
            return summary
        except BaseException:
            original.close();connection.close();target.unlink(missing_ok=True)
            _progress('ready',ready=True,error='Edition compaction failed; published index preserved')
            raise


def _flush(connection,kind,batch,links):
    with connection:
        connection.executemany('INSERT OR REPLACE INTO authors VALUES(?,?,?)' if kind=='authors' else 'INSERT OR REPLACE INTO works VALUES(?,?,?,?,?,?,?)',batch)
        if links:connection.executemany('INSERT INTO work_authors VALUES(?,?)',links)


def _ratings(connection,batch):
    with connection:connection.executemany('INSERT INTO ratings VALUES(?,?,?) ON CONFLICT(work) DO UPDATE SET total=total+excluded.total,count=count+excluded.count',batch)


def _editions(connection,batch,links,identifiers):
    with connection:
        connection.executemany('INSERT OR REPLACE INTO editions VALUES(?,?,?,?,?,?,?,?,?)',batch)
        connection.executemany('INSERT INTO edition_works VALUES(?,?)',links)
        connection.executemany('INSERT INTO edition_isbns VALUES(?,?)',identifiers)


def isbn_keys(value):
    """Validated, equivalent ISBN-10/13 keys; never infer other format identifiers."""
    key=re.sub(r'[-\s]','',str(value or '')).upper()
    if re.fullmatch(r'\d{9}[\dX]',key):
        if sum((10-i)*(10 if c=='X' else int(c)) for i,c in enumerate(key))%11:return []
        base='978'+key[:9];check=(-sum(int(c)*(1 if i%2==0 else 3) for i,c in enumerate(base)))%10
        return [key,base+str(check)]
    if not re.fullmatch(r'97[89]\d{10}',key) or sum(int(c)*(1 if i%2==0 else 3) for i,c in enumerate(key))%10:return []
    if key.startswith('979'):return [key]
    base=key[3:12];check=(-sum((10-i)*int(c) for i,c in enumerate(base)))%11
    return [key,base+('X' if check==10 else str(check))]


def work_candidates(keys):
    """Exact linked work lookups; ISBN evidence must not depend on title search ranking."""
    root=folder();path=root/'index.sqlite'
    keys=list(dict.fromkeys(k for k in keys if re.fullmatch(r'/works/OL\d+W',k or '')))[:300]
    if not keys or not path.is_file():return []
    with (root/'index.lock').open('a+b') as reader,oslib.file_lock(reader):
        c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
        try:
            result=[]
            for key in keys:
                row=c.execute('SELECT * FROM works WHERE key=?',(key,)).fetchone()
                if row is None:continue
                people=[name[0] for author in json.loads(row['authors']) if (name:=c.execute('SELECT name FROM authors WHERE key=?',(author,)).fetchone())]
                rating=c.execute('SELECT total,count FROM ratings WHERE work=?',(key,)).fetchone()
                result.append(candidate('openlibrary',id=key,title=row['title'],authors=people,original_year=row['year'],genres=json.loads(row['subjects']),rating=rating[0]/rating[1] if rating else None,ratings_count=rating[1] if rating else None,url='https://openlibrary.org'+key))
            snapshot=json.loads(c.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0])
            emit(source='Open Library offline linked works',query=dict(works=keys),cached=True,raw=dict(candidates=result,snapshot=snapshot))
            return result
        finally:c.close()


def editions(work_key=None,*,isbn=None,limit=300):
    """Bounded edition facts, with work links and snapshot provenance. None means unavailable."""
    root=folder();path=root/'index.sqlite'
    if not path.is_file():return None
    if work_key is not None and not re.fullmatch(r'/works/OL\d+W',work_key):return []
    if isbn is not None:
        isbn=re.sub(r'[-\s]','',str(isbn)).upper()
        if not re.fullmatch(r'\d{9}[\dX]|\d{13}',isbn):return []
    if work_key is None and isbn is None:return []
    with (root/'index.lock').open('a+b') as reader,oslib.file_lock(reader):
        connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);connection.row_factory=sqlite3.Row
        try:
            snapshot=json.loads(connection.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0])
            if not snapshot.get('editions'):return None
            join='JOIN edition_works l ON l.edition=e.key' if work_key is not None else 'JOIN edition_isbns l ON l.edition=e.key'
            field='l.work' if work_key is not None else 'l.isbn'
            keys=[work_key] if work_key is not None else isbn_keys(isbn) or [isbn]
            rows=connection.execute(f"SELECT DISTINCT e.* FROM editions e {join} WHERE {field} IN ({','.join('?' for _ in keys)}) ORDER BY e.year IS NULL,e.year,e.key LIMIT ?",keys+[max(1,min(limit,300))])
            result=[]
            for row in rows:
                works=[v[0] for v in connection.execute('SELECT work FROM edition_works WHERE edition=?',(row['key'],))]
                result.append(dict(key=row['key'],title=row['title'],subtitle=row['subtitle'],work_key=work_key or (works[0] if len(works)==1 else None),works=works,year=row['year'],publish_date=row['publish_date'],publishers=json.loads(row['publishers']),languages=[dict(key=v) for v in json.loads(row['languages'])],physical_format=row['format'],isbns=json.loads(row['isbns'])))
            emit(source='Open Library offline editions',query=dict(work=work_key,isbn=isbn),cached=True,raw=dict(editions=result,snapshot=snapshot))
            return result
        finally:connection.close()


def search(title,author=''):
    root=folder()
    if not (root/'index.sqlite').is_file():return []
    # Windows cannot replace an open SQLite file; readers hold this brief cross-process lock.
    with (root/'index.lock').open('a+b') as reader,oslib.file_lock(reader):return _search(title,author)


def _search(title,author=''):
    path=folder()/'index.sqlite'
    if not path.is_file():return []
    normal=normalise(title)
    if not normal:return []
    connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);connection.row_factory=sqlite3.Row
    try:
        people=[]
        if author and normalise(author):
            sql='SELECT a.key FROM names JOIN authors a ON a.rowid=names.rowid WHERE names MATCH ? ORDER BY rank LIMIT 1000'
            for person in contributors.authors(author):
                tokens=normalise(person).split()
                if not tokens:continue
                query=' AND '.join('"'+token+'"' for token in tokens[:16])
                matches=[r[0] for r in connection.execute(sql,(query,))]
                if not matches:matches=[r[0] for r in connection.execute(sql,('"'+tokens[-1]+'"',))]
                people.extend(matches)
            people=list(dict.fromkeys(people))
        where='';parameters=[]
        if people:
            where=f" AND w.key IN (SELECT work FROM work_authors WHERE author IN ({','.join('?' for _ in people)}))";parameters=people
        main=normalise(book_titles.parts(title)[0]);variants=list(dict.fromkeys([normal,normal.replace(' ',''),main,main.replace(' ','')]))
        rows=list(connection.execute(f"SELECT w.* FROM works w WHERE w.normalised IN ({','.join('?' for _ in variants)})"+where+' LIMIT 100',variants+parameters))
        if not rows:
            query=' AND '.join('"'+part+'"' for part in normal.split()[:16])
            rows=list(connection.execute('SELECT w.* FROM titles JOIN works w ON w.rowid=titles.rowid WHERE titles MATCH ?'+where+' ORDER BY rank LIMIT 100',[query]+parameters))
        if not rows and people:
            # Some collections are catalogued under the writer's name and a volume,
            # while the local tags contain only their distinctive subtitle.
            possible=list(connection.execute(f"SELECT w.* FROM works w WHERE w.key IN (SELECT work FROM work_authors WHERE author IN ({','.join('?' for _ in people)})) LIMIT 200",people))
            for row in possible:
                names=[connection.execute('SELECT name FROM authors WHERE key=?',(key,)).fetchone() for key in json.loads(row['authors'])]
                if any(person and normalise(row['title']).startswith(normalise(person[0])+' ') for person in names):rows.append(row)
        results=[]
        for row in rows:
            keys=json.loads(row['authors']);authors=[]
            for key in keys:
                person=connection.execute('SELECT name FROM authors WHERE key=?',(key,)).fetchone()
                if person:authors.append(person[0])
            rating=connection.execute('SELECT total,count FROM ratings WHERE work=?',(row['key'],)).fetchone()
            results.append(candidate('openlibrary',id=row['key'],title=row['title'],authors=authors,original_year=row['year'],genres=json.loads(row['subjects']),cover_url=f"https://covers.openlibrary.org/b/id/{row['cover']}-L.jpg" if row['cover'] else '',rating=rating[0]/rating[1] if rating else None,ratings_count=rating[1] if rating else None,url='https://openlibrary.org'+row['key']))
        # Provider acceptance still uses the app's full title/author identity checks.
        from app import matcher
        book=dict(title=title,author=author)
        def author_volume(c):
            title=normalise(c['title'])
            return any(re.fullmatch(re.escape(normalise(person))+r'\s+(?:[ivxlcdm]+|\d+)',title) for person in c['authors'])
        results=sorted(results,key=lambda c:(normalise(c['title'])==normal,normalise(c['title']) in variants,author_volume(c),matcher.score(book,c),c.get('ratings_count') or 0,bool(c.get('original_year'))),reverse=True)[:5]
        snapshot=json.loads(connection.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0])
        emit(source='Open Library offline',query=dict(title=title,author=author),cached=True,raw=dict(candidates=results,snapshot=snapshot))
        return results
    finally:connection.close()
