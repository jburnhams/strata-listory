"""The application server: `./app serve` starts the FastAPI backend and serves the built React app.

    ./app serve [--host 127.0.0.1] [--port 8360]

API (JSON): GET /api/health; GET /api/settings; PUT /api/settings {data_dir?, library_dir?, backup_dir?};
GET /api/books?q=&status=&review=&kind=; POST /api/scan {folder?}; POST /api/match?limit=&providers=; GET /api/providers; GET /api/process (the approval queue); POST /api/process/propose?limit=, /approve, /reject, /repropose {ids}, /run; POST /api/enrich?limit=; POST /api/books/{id}/enrich (Hardcover ratings and reviews); POST /api/books/{id}/confirm|ignore; POST /api/books {title, author?, narrator?, series?, series_position?, duration_seconds?, path?, status?};
GET|PATCH|DELETE /api/books/{id}; GET /api/books/{id}/details (files, embedded tags, Audible metadata file); GET /api/books/{id}/cover (image).
The page at / serves `src/app/server/static/index.html` when built (`cd web && npm run build`), else the plain stand-in `landing.html`."""
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import __version__, catalogue, db, details, enrich, fs, matcher, providers, scan, settings
from app.process import queue as pq

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
LANDING = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'landing.html')


class SettingsIn(BaseModel):
    data_dir: str | None = None
    library_dir: str | None = None
    backup_dir: str | None = None


class BookIn(BaseModel):
    title: str = ''
    author: str = ''
    narrator: str = ''
    series: str = ''
    series_position: float | None = None
    duration_seconds: int | None = None
    path: str = ''
    asin: str = ''
    status: str = 'unlistened'


class IdsIn(BaseModel):
    ids: list[int]


class ScanIn(BaseModel):
    folder: str | None = None


class BookPatch(BaseModel):
    title: str | None = None
    author: str | None = None
    narrator: str | None = None
    series: str | None = None
    series_position: float | None = None
    duration_seconds: int | None = None
    path: str | None = None
    status: str | None = None


def create_app():
    app = FastAPI(title='strata-listory', docs_url='/api/docs', openapi_url='/api/openapi.json')

    def guard(fn, *a, **kw):
        try: return fn(*a, **kw)
        except catalogue.CatalogueError as e: raise HTTPException(400, str(e))
        except db.CorruptDatabase as e: raise HTTPException(503, str(e))
        except catalogue.NotFound: raise HTTPException(404, 'no such book')

    @app.get('/', response_class=HTMLResponse)
    def index():
        built = os.path.join(STATIC, 'index.html')          # the React app when it has been built (cd web && npm run build), else the plain stand-in page
        with open(built if os.path.exists(built) else LANDING, encoding='utf8') as f: return f.read()

    @app.get('/api/health')
    def get_health(): return dict(ok=True, version=__version__)

    @app.get('/api/settings')
    def get_settings(): return settings.all_settings()

    @app.put('/api/settings')
    def put_settings(s: SettingsIn):
        for label, v in (('data', s.data_dir), ('library', s.library_dir), ('backup', s.backup_dir)):
            if v and not os.path.isabs(os.path.expanduser(v)): raise HTTPException(400, f'the {label} folder must be an absolute path')
        changes = {k: v for k, v in (('data_dir', s.data_dir), ('library_dir', s.library_dir), ('backup_dir', s.backup_dir)) if v is not None}
        if changes: settings.save({k: v or None for k, v in changes.items()})      # an empty value resets the setting
        return settings.all_settings()

    @app.get('/api/books')
    def get_books(q: str = '', status: str | None = None, review: str | None = None, kind: str | None = None): return guard(catalogue.list_books, q, status, review, kind)

    @app.post('/api/books')
    def post_book(b: BookIn): return guard(catalogue.add_book, **b.model_dump())

    @app.post('/api/scan')
    def post_scan(b: ScanIn):
        folder = b.folder or settings.library_dir()
        if not folder: raise HTTPException(400, 'set the audiobook folder in Settings first')
        if not fs.reachable(folder): raise HTTPException(503, f'{folder} is on a network share that is not reachable (is the server up?)')
        if not os.path.isdir(folder): raise HTTPException(400, f'not a folder: {folder}')
        try: found = list(scan.scan(folder))
        except OSError as e: raise HTTPException(503, f'the scan was interrupted ({e.strerror or e}); nothing was changed')
        return guard(lambda: dict(found=len(found), **catalogue.add_scanned(found)))

    @app.post('/api/match')
    def post_match(limit: int = 25, providers_: str | None = Query(None, alias='providers')):
        try: return guard(matcher.match_pending, max(1, min(limit, 200)), providers.enabled(providers_.split(',') if providers_ else None))
        except KeyError as e: raise HTTPException(400, str(e.args[0]))
        except matcher.MatchError as e: raise HTTPException(502, str(e))

    @app.post('/api/enrich')
    def post_enrich(limit: int = 25):
        try: return guard(enrich.enrich_pending, max(1, min(limit, 100)))
        except matcher.MatchError as e: raise HTTPException(502, str(e))

    @app.post('/api/books/{book_id}/enrich')
    def enrich_one(book_id: int):
        b = guard(catalogue.get_book, book_id)
        found = False
        try: guard(enrich.enrich_openlibrary, b); found = True                          # Open Library needs no key
        except matcher.MatchError: pass
        try: found = guard(enrich.enrich_book, b) is not None or found
        except matcher.MatchError as e:
            if not found: raise HTTPException(502, str(e))
        return dict(found=found, extras=catalogue.get_extras(book_id))

    worker = pq.Worker()

    def queue_view():
        rows = [dict(book_id=r['book_id'], title=r['title'], author=r['author'], state=r['state'], error=r['error'], updated=r['updated'], summary=pq.summary(r['plan']) if r['plan'] else None) for r in catalogue.list_plans()]
        return dict(rows=rows, worker=worker.status(), result={k: catalogue.get_processed(r['book_id']) for k, r in ((str(r['book_id']), r) for r in rows) if r['state'] in ('done', 'failed')})

    @app.get('/api/process')
    def get_process(): return queue_view()

    @app.post('/api/process/propose')
    def post_propose(limit: int = 5):
        worker.submit('propose', limit=max(1, min(limit, 50))); return queue_view()

    @app.post('/api/process/approve')
    def post_approve(b: IdsIn): return dict(changed=pq.decide(b.ids, True))

    @app.post('/api/process/reject')
    def post_reject(b: IdsIn): return dict(changed=pq.decide(b.ids, False))

    @app.post('/api/process/repropose')
    def post_repropose(b: IdsIn): return dict(changed=pq.repropose(b.ids))

    @app.post('/api/process/run')
    def post_run():
        if not catalogue.list_plans(('approved',)): raise HTTPException(400, 'nothing is approved yet')
        worker.submit('run'); return queue_view()

    @app.get('/api/providers')
    def get_providers(): return providers.status()

    @app.post('/api/books/{book_id}/confirm')
    def confirm_book(book_id: int): return guard(catalogue.confirm, book_id)

    @app.post('/api/books/{book_id}/ignore')
    def ignore_book(book_id: int): return guard(catalogue.ignore, book_id)

    @app.get('/api/books/{book_id}/details')
    def book_details(book_id: int):
        b = guard(catalogue.get_book, book_id); d = guard(details.describe, b)
        probe = d['probe'] or {}; secs = probe.get('duration_seconds') if probe.get('complete') else (d['audible'] or {}).get('runtime_minutes', 0) * 60 or None      # a full probe is exact; otherwise Audible's runtime
        if b['duration_seconds'] is None and secs: b = guard(catalogue.update_book, book_id, duration_seconds=int(secs))
        return dict(book=b, extras=catalogue.get_extras(book_id), processed=catalogue.get_processed(book_id), **d)

    @app.get('/api/books/{book_id}/cover')
    def book_cover(book_id: int):
        p = guard(lambda: details.cover(catalogue.get_book(book_id)))
        if not p: raise HTTPException(404, 'no cover')
        return FileResponse(p, headers={'Cache-Control': 'private, max-age=3600'})

    @app.get('/api/books/{book_id}')
    def get_book(book_id: int): return guard(catalogue.get_book, book_id)

    @app.patch('/api/books/{book_id}')
    def patch_book(book_id: int, b: BookPatch): return guard(catalogue.update_book, book_id, **b.model_dump(exclude_unset=True))

    @app.delete('/api/books/{book_id}')
    def delete_book(book_id: int): return guard(catalogue.remove_book, book_id)

    if os.path.isdir(os.path.join(STATIC, 'assets')): app.mount('/assets', StaticFiles(directory=os.path.join(STATIC, 'assets')), name='assets')
    return app


def main(argv=None):
    import argparse, uvicorn
    a = argparse.ArgumentParser(); a.add_argument('--host', default='127.0.0.1'); a.add_argument('--port', type=int, default=8360); a = a.parse_args(argv)
    print(f'data folder: {settings.data_dir()}\nhttp://{a.host}:{a.port}/'); uvicorn.run(create_app(), host=a.host, port=a.port, log_level='warning')
