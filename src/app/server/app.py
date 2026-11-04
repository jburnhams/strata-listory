"""The application server: `./app serve` starts the FastAPI backend and serves the built React app.

    ./app serve [--host 127.0.0.1] [--port 8360]

API (JSON): GET /api/health; GET /api/settings; PUT /api/settings {data_dir?, library_dir?, backup_dir?};
GET /api/books?q=&status=&review=&kind=; POST /api/scan {folder?}; POST /api/match?limit=&providers=; GET /api/providers; GET /api/process (the approval queue); POST /api/process/propose?limit=, /approve, /reject, /repropose {ids}, /run; POST /api/enrich?limit=; POST /api/books/{id}/enrich (Hardcover ratings and reviews); POST /api/books/{id}/confirm|ignore; POST /api/books {title, author?, narrator?, series?, series_position?, duration_seconds?, path?, status?};
GET|PATCH|DELETE /api/books/{id}; GET /api/books/{id}/details (files, embedded tags, Audible metadata file); GET /api/books/{id}/cover (image).
The page at / serves `src/app/server/static/index.html` when built (`cd web && npm run build`), else the plain stand-in `landing.html`."""
import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import __version__, catalogue, db, details, providers, settings, read_models
from app.process import queue as pq
from app.jobs import JobQueue
from app.server import tasks

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
LANDING = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'landing.html')


class IngestIn(BaseModel):
    ids: list[int] | None = None
    limit: int = Field(default=5,ge=1)
    online: bool = True


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
    @asynccontextmanager
    async def lifespan(app):
        jobs.start()
        jobs.submit('system','bootstrap')
        reconcile_identification_reviews()
        try:yield
        finally:jobs.stop()
    app = FastAPI(title='strata-listory', docs_url='/api/docs', openapi_url='/api/openapi.json',lifespan=lifespan)

    @app.middleware('http')
    async def saved_reads(request,call_next):
        from app.ingest.pipeline import saved_evidence_only
        with catalogue.deferred_mirrors(lambda bid:jobs.submit('system','mirror',book_id=bid)):
            if request.method in ('GET','HEAD'):
                with saved_evidence_only():return await call_next(request)
            return await call_next(request)

    initial_settings=settings.all_settings()
    jobs=JobQueue(lambda domain,kind,options:tasks.execute(jobs,domain,kind,options))
    app.state.jobs=jobs

    def queued(domain,kind,**options):
        ident=jobs.submit(domain,kind,**options)
        return JSONResponse(dict(queued=True,job_id=ident,url=f'/api/jobs/{ident}'),status_code=202)

    @app.get('/api/jobs/{job_id}')
    def get_job(job_id:str):
        try:return jobs.get(job_id)
        except ValueError as e:raise HTTPException(404,str(e))

    @app.post('/api/jobs/{job_id}/retry')
    def retry_job(job_id:str):
        try:ident=jobs.retry(job_id)
        except ValueError as e:raise HTTPException(400,str(e))
        return JSONResponse(dict(queued=True,job_id=ident,url=f'/api/jobs/{ident}'),status_code=202)

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
    def get_settings():return read_models.get('settings','current',initial_settings)

    @app.put('/api/settings')
    def put_settings(s: SettingsIn):
        for label, v in (('data', s.data_dir), ('library', s.library_dir), ('backup', s.backup_dir)):
            if v and not os.path.isabs(os.path.expanduser(v)): raise HTTPException(400, f'the {label} folder must be an absolute path')
        changes = {k: v for k, v in (('data_dir', s.data_dir), ('library_dir', s.library_dir), ('backup_dir', s.backup_dir)) if v is not None}
        with jobs.configuration_lock:
            target=os.environ.get('APP_DATA') or changes.get('data_dir') or settings.DEFAULT_DATA_DIR
            normalize=lambda path:os.path.normcase(os.path.abspath(os.path.expanduser(path)))
            if 'data_dir' in changes and normalize(target)!=normalize(settings.data_dir()) and jobs.pending():
                raise HTTPException(409,'wait for queued and running jobs to finish before changing the data folder')
            if changes: settings.save({k: v or None for k, v in changes.items()})
            value=settings.all_settings();read_models.save('settings','current',value)
            return value

    @app.get('/api/books')
    def get_books(q: str = '', status: str | None = None, review: str | None = None, kind: str | None = None): return guard(catalogue.list_books, q, status, review, kind)

    @app.get('/api/books/{book_id}/source')
    def book_source(book_id:int):
        from app import source_files
        return guard(source_files.describe,guard(catalogue.get_book,book_id))

    @app.post('/api/books')
    def post_book(b: BookIn): return guard(catalogue.add_book, **b.model_dump())

    @app.post('/api/scan')
    def post_scan(b: ScanIn):
        folder=b.folder or settings.library_dir()
        if not folder:raise HTTPException(400,'set the audiobook folder in Settings first')
        return queued('system','scan',folder=folder)

    @app.post('/api/match')
    def post_match(limit:int=25,providers_:str|None=Query(None,alias='providers')):
        names=providers_.split(',') if providers_ else None
        if names and any(n not in {p.name for p in providers.ALL} for n in names):raise HTTPException(400,'unknown provider')
        return queued('system','match',limit=max(1,min(limit,200)),providers=names)

    @app.post('/api/enrich')
    def post_enrich(limit:int=25):return queued('system','enrich',limit=max(1,min(limit,100)))

    @app.post('/api/books/{book_id}/enrich')
    def enrich_one(book_id:int):
        guard(catalogue.get_book,book_id)
        return queued('system','enrich_book',book_id=book_id)

    @app.post('/api/books/{book_id}/inspect')
    def inspect_book(book_id:int):
        guard(catalogue.get_book,book_id)
        return queued('system','inspect',book_id=book_id)

    worker = pq.Worker();worker.jobs=jobs

    def queue_view():return tasks.process_view(jobs)

    @app.get('/api/process')
    def get_process(): return queue_view()

    @app.get('/api/finished')
    def finished(): return catalogue.list_processed()

    def output_metadata(book_id):
        guard(catalogue.get_book, book_id)
        result = catalogue.get_processed(book_id)
        if not result or result.get('status') != 'done' or not result.get('out_dir'): raise HTTPException(404, 'no finished output')
        folder=result['out_dir']
        metadata=read_models.get('output',book_id)
        if metadata is None:raise HTTPException(404,'finished metadata unavailable')
        return folder, metadata

    @app.get('/api/books/{book_id}/output')
    def book_output(book_id: int):
        _, metadata = output_metadata(book_id)
        return metadata

    @app.get('/api/books/{book_id}/audio/{chapter}')
    def book_audio(book_id: int, chapter: int):
        folder, metadata = output_metadata(book_id);folder=os.path.realpath(folder)
        chapters = metadata.get('chapters', [])
        if chapter < 0 or chapter >= len(chapters): raise HTTPException(404, 'no such chapter')
        name = chapters[chapter].get('file', '')
        path = os.path.realpath(os.path.join(folder, name))
        if not name or os.path.dirname(path) != folder or not name.lower().endswith('.mp3') or not os.path.isfile(path): raise HTTPException(404, 'chapter unavailable')
        return FileResponse(path, media_type='audio/mpeg')

    @app.post('/api/process/propose')
    def post_propose(limit: int = 5):
        return queued('process','propose',limit=max(1,min(limit,50)))

    @app.post('/api/process/approve')
    def post_approve(b: IdsIn):return queued('process','approve',ids=b.ids)

    @app.post('/api/process/reject')
    def post_reject(b: IdsIn):return queued('process','reject',ids=b.ids)

    @app.post('/api/process/repropose')
    def post_repropose(b: IdsIn):
        return queued('process','repropose',ids=b.ids)

    @app.post('/api/process/run')
    def post_run():
        if not catalogue.plan_counts().get('approved'):raise HTTPException(400,'nothing is approved yet')
        return queued('process','run')

    from app.ingest import pipeline as ingest, policy as ingest_policy, store as ingest_store
    from app.ingest.worker import Worker as IngestWorker
    ingest_worker=IngestWorker();ingest_worker.jobs=jobs

    def reconcile_identification_reviews():
        # Recovery is processing too: do it on the worker, one current run at a time.
        ingest_worker.submit('reconcile')

    def ingest_guard(fn,*args,**kwargs):
        try:return guard(fn,*args,**kwargs)
        except (ValueError,OSError) as e:raise HTTPException(400,str(e))

    @app.get('/api/ingest')
    def get_ingest(offset:int=Query(default=0,ge=0),phase:Literal['metadata','audiobook','quality']|None=None,sort:Literal['strongest','metadata','newest','oldest','title']|None=None):return dict(ingest.dashboard(offset=offset,phase=phase,sort=sort),worker=ingest_worker.status())

    @app.get('/api/operations')
    def get_operations(completed:bool=False):
        from app.ingest import operations
        view=operations.view(ingest_worker.tasks()+jobs.tasks('system'),worker.tasks(),completed)
        for job in jobs.failures():
            label=job['kind'].replace('_',' ')
            view['rows'].append(dict(id='job-'+job['id'],job_id=job['id'],book_id=job['book_id'],run_id=None,title=f"{label}: {job['title']}" if job['title'] else label,stage=label,state='failed',phase=None,error=job['error']))
        return view

    @app.get('/api/library/covers')
    def library_covers():return dict(ids=details.available_cover_ids())

    @app.get('/api/library/workflow')
    def library_workflow():
        from app.ingest import operations
        return guard(operations.library,ingest_worker.tasks(),worker.tasks())

    @app.post('/api/ingest/run')
    def run_ingest(b:IngestIn):
        if b.ids is not None:
            if not b.ids:raise HTTPException(400,'select at least one book ID')
            for bid in b.ids:guard(catalogue.get_book,bid)
        if b.limit>max(200,len(b.ids or [])):raise HTTPException(422,'limit must be 1..200, or at most the number of explicitly selected book IDs')
        return queued('ingest','run',ids=b.ids,limit=b.limit,online=b.online)

    @app.get('/api/ingest/runs/{run_id}')
    def get_ingest_run(run_id:str):return ingest_guard(ingest_store.run_summary,run_id)

    from app.ingest import previews

    @app.post('/api/ingest/runs/{run_id}/samples')
    def prepare_ingest_samples(run_id:str):
        ingest_guard(ingest_store.run_book_id,run_id)
        return queued('system','samples',run_id=run_id)

    @app.get('/api/ingest/runs/{run_id}/samples')
    def get_ingest_samples(run_id:str):return ingest_guard(previews.status,run_id)

    @app.get('/api/ingest/runs/{run_id}/samples/{index}')
    def ingest_sample_audio(run_id:str,index:int):
        return FileResponse(ingest_guard(previews.audio,run_id,index),media_type='audio/mpeg',headers={'Cache-Control':'private, no-cache'})

    @app.post('/api/ingest/runs/{run_id}/resume')
    def resume_ingest(run_id:str):
        ingest_guard(ingest_store.run_book_id,run_id)
        return queued('ingest','resume',run_id=run_id)

    @app.post('/api/ingest/runs/{run_id}/evaluate')
    def evaluate_ingest(run_id:str):
        ingest_guard(ingest_store.run_book_id,run_id)
        return queued('ingest','evaluate',run_id=run_id)

    @app.post('/api/ingest/runs/{run_id}/approve/{phase}')
    def approve_ingest_review(run_id:str,phase:str):
        ingest_guard(ingest_store.run_book_id,run_id)
        if phase not in ingest.REVIEW_STAGES:raise HTTPException(400,'invalid review stage')
        return queued('ingest','approve',run_id=run_id,phase=phase)

    @app.put('/api/ingest/runs/{run_id}/recording')
    def choose_ingest_recording(run_id:str,b:dict):
        ingest_guard(ingest_store.run_book_id,run_id)
        if not isinstance(b.get('key'),str):raise HTTPException(400,'recording key is required')
        return queued('ingest','recording',run_id=run_id,key=b['key'])

    @app.put('/api/ingest/runs/{run_id}/quality')
    def rate_ingest_quality(run_id:str,b:dict):
        ingest_guard(ingest_store.run_book_id,run_id)
        if b.get('rating') not in ('good','medium','poor','unrated'):raise HTTPException(400,'invalid quality rating')
        return queued('ingest','quality',run_id=run_id,rating=b['rating'])

    @app.post('/api/ingest/runs/{run_id}/enqueue')
    def enqueue_ingest(run_id:str):
        ingest_guard(ingest_store.run_book_id,run_id)
        return queued('ingest','enqueue',run_id=run_id)

    @app.put('/api/ingest/policy')
    def put_ingest_policy(b:dict):
        value=ingest_guard(ingest_policy.save,b)
        jobs.submit('system','views')
        if value['auto_approve_metadata'] or value['auto_approve_audiobook']:ingest_worker.submit('auto_metadata')
        return value

    @app.get('/api/providers')
    def get_providers():return read_models.get('providers','configured',[])

    @app.post('/api/books/{book_id}/confirm')
    def confirm_book(book_id: int): return guard(catalogue.confirm, book_id)

    @app.post('/api/books/{book_id}/ignore')
    def ignore_book(book_id: int): return guard(catalogue.ignore, book_id)

    @app.get('/api/books/{book_id}/details')
    def book_details(book_id: int):
        b = guard(catalogue.get_book, book_id); d = guard(details.saved_description, b)
        return dict(book=b, extras=catalogue.get_extras(book_id), processed=catalogue.get_processed(book_id), **d)

    @app.get('/api/books/{book_id}/cover')
    def book_cover(book_id: int):
        p = guard(lambda: details.saved_cover(catalogue.get_book(book_id)))
        if not p or not os.path.isfile(p):raise HTTPException(404,'no cover')
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
