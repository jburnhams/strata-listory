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

VERSION=1


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


def build(dumps=None,wait=False):
    root=folder();root.mkdir(parents=True,exist_ok=True);source=Path(dumps) if dumps else root/'dumps'
    paths={kind:source/f'{kind}.txt.gz' for kind in ('authors','works','ratings')}
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
                CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
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
            _progress('indexing',records=0,counts=counts)
            connection.executescript('''CREATE INDEX works_title ON works(normalised);
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
            summary=dict(version=VERSION,counts=counts,dumps=provenance,built=datetime.now(timezone.utc).isoformat())
            connection.execute('INSERT INTO metadata VALUES(?,?)',('snapshot',json.dumps(summary)));connection.commit()
            check=connection.execute('PRAGMA quick_check').fetchone()[0]
            if check!='ok':raise ValueError(f'Open Library index verification failed: {check}')
            connection.close()
            with (root/'index.lock').open('a+b') as publication,oslib.file_lock(publication):target.replace(root/'index.sqlite')
            _progress('ready',ready=True,records=0,error='',**summary,elapsed=round(time.monotonic()-started,1))
            return status()
        except BaseException as error:
            connection.close();_progress('failed',error=str(error));raise


def _flush(connection,kind,batch,links):
    with connection:
        connection.executemany('INSERT OR REPLACE INTO authors VALUES(?,?,?)' if kind=='authors' else 'INSERT OR REPLACE INTO works VALUES(?,?,?,?,?,?,?)',batch)
        if links:connection.executemany('INSERT INTO work_authors VALUES(?,?)',links)


def _ratings(connection,batch):
    with connection:connection.executemany('INSERT INTO ratings VALUES(?,?,?) ON CONFLICT(work) DO UPDATE SET total=total+excluded.total,count=count+excluded.count',batch)


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
