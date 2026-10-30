"""The application server: `./app serve` starts the FastAPI backend and serves the built React app.

    ./app serve [--host 127.0.0.1] [--port 8360]

API (JSON): GET /api/health; GET /api/settings; PUT /api/settings {data_dir?, library_dir?, backup_dir?};
GET /api/books?q=&status=&review=&kind=; POST /api/scan {folder?}; POST /api/match?limit=; POST /api/books/{id}/confirm|ignore; POST /api/books {title, author?, narrator?, series?, series_position?, duration_seconds?, path?, status?};
GET|PATCH|DELETE /api/books/{id}.
The page at / serves `src/app/server/static/index.html` when built (`cd web && npm run build`), else the plain stand-in `landing.html`."""
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import __version__, catalogue, db, matcher, scan, settings

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
        if not folder or not os.path.isdir(folder): raise HTTPException(400, 'set the audiobook folder in Settings first' if not folder else f'not a folder: {folder}')
        found = list(scan.scan(folder))
        return guard(lambda: dict(found=len(found), **catalogue.add_scanned(found)))

    @app.post('/api/match')
    def post_match(limit: int = 25):
        try: return guard(matcher.match_pending, max(1, min(limit, 200)))
        except matcher.MatchError as e: raise HTTPException(502, str(e))

    @app.post('/api/books/{book_id}/confirm')
    def confirm_book(book_id: int): return guard(catalogue.confirm, book_id)

    @app.post('/api/books/{book_id}/ignore')
    def ignore_book(book_id: int): return guard(catalogue.ignore, book_id)

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
