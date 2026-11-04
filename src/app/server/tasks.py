"""Handlers for jobs; filesystem inspection, providers and audio work stay here."""
import json
import os
from pathlib import Path
from app import catalogue, details, enrich, fs, matcher, mirror, providers, read_models, scan, settings
from app.ingest import pipeline, previews
from app.ingest.worker import Worker as IngestWorker
from app.process import queue as pq


def execute(jobs,domain,kind,options):
    if domain=='ingest':
        options=dict(options);recovered=options.pop('_recovered',False)
        if kind=='approve':
            result=pipeline.approve_review(options['run_id'],options['phase'])
            continue_needed=result['changed']
            if recovered and not continue_needed:
                from app.ingest import store
                run=store.get(options['run_id'])
                if pipeline.approval_valid(run,options['phase']):store.finish(run['id'],'running');continue_needed=True
            if continue_needed:jobs.submit('ingest','continue',run_id=options['run_id'])
            pipeline.publish_run(options['run_id'])
            return dict(result,queued=result['changed'])
        functions={'evaluate':pipeline.evaluate,'recording':pipeline.choose_recording,'quality':pipeline.rate_quality,'enqueue':pipeline.enqueue}
        if kind in functions:
            result=functions[kind](**options);pipeline.publish_run(options['run_id']);return result
        worker=IngestWorker();worker.jobs=jobs;worker._do(kind,options)
        result=worker.last.get(kind)
        if isinstance(result,dict) and result.get('error'):raise ValueError(result['error'])
        for rid in result if isinstance(result,list) else []:pipeline.publish_run(rid)
        return result
    if domain=='process':
        if kind=='propose':return pq.propose(**options,jobs=1)
        if kind=='run':return pq.run_approved(jobs=1)
        if kind in ('approve','reject'):return dict(changed=pq.decide(options['ids'],kind=='approve'))
        if kind=='repropose':
            changed=pq.repropose(options['ids'])
            if changed:jobs.submit('process','propose',ids=changed,limit=len(changed))
            return dict(changed=changed)
    if domain=='system':
        if kind=='bootstrap':return bootstrap()
        if kind=='views':
            from app.ingest import store
            headers=store.review_headers()
            for header in headers:pipeline.publish_run(header['id'])
            return dict(updated=len(headers))
        if kind=='samples':return previews.prepare(options['run_id'])
        if kind=='mirror':
            try:book=catalogue.get_book(options['book_id'])
            except catalogue.NotFound:return None
            mirror.forget_missing_cover(book);mirror.write(book);return None
        if kind=='scan':
            folder=options['folder']
            if not fs.reachable(folder):raise ValueError('audiobook folder is not reachable')
            if not os.path.isdir(folder):raise ValueError(f'not a folder: {folder}')
            found=list(scan.scan(folder))
            return dict(found=len(found),**catalogue.add_scanned(found))
        if kind=='match':return matcher.match_pending(options['limit'],providers.enabled(options.get('providers')))
        if kind=='enrich':return enrich.enrich_pending(options['limit'])
        if kind=='enrich_book':
            book=catalogue.get_book(options['book_id']);found=False
            try:enrich.enrich_openlibrary(book);found=True
            except matcher.MatchError:pass
            try:found=enrich.enrich_book(book) is not None or found
            except matcher.MatchError:
                if not found:raise
            return dict(found=found,extras=catalogue.get_extras(book['id']))
        if kind=='inspect':
            book=catalogue.get_book(options['book_id']);result=details.describe(book);cover=details.cover(book)
            read_models.save('details',book['id'],result)
            if cover:read_models.save('cover',book['id'],dict(path=cover))
            probe=result.get('probe') or {};seconds=probe.get('duration_seconds') if probe.get('complete') else (result.get('audible') or {}).get('runtime_minutes',0)*60
            if book['duration_seconds'] is None and seconds:catalogue.update_book(book['id'],duration_seconds=int(seconds),lock=False)
            return result
    raise ValueError('unknown job kind')


def process_view(jobs):
    from app import db
    c=db.connect()
    try:
        rows=[]
        for row in c.execute("SELECT p.book_id,b.title,b.author,p.state,p.error,p.updated,v.data FROM process_plan p JOIN books b ON b.id=p.book_id LEFT JOIN read_models v ON v.kind='plan' AND v.owner=CAST(p.book_id AS TEXT) ORDER BY p.updated DESC"):
            row=dict(row);row['summary']=json.loads(row.pop('data') or 'null');rows.append(row)
    finally:c.close()
    return dict(rows=rows,worker=dict(jobs.status('process'),counts=catalogue.plan_counts()),result={str(r['book_id']):catalogue.get_processed(r['book_id']) for r in rows if r['state'] in ('done','failed')})


def bootstrap():
    """Index legacy local artifacts once in a job; reads never scan the mirror tree."""
    count=0
    for book in catalogue.list_books():
        folder=Path(mirror.dir_for(book,probe_source=False))
        for ext in details.IMAGES:
            candidates=(folder/('cover'+ext),Path(settings.data_dir())/'covers'/f"{book['id']}{ext}")
            path=next((p for p in candidates if p.is_file()),None)
            if path:read_models.save('cover',book['id'],dict(path=str(path)));break
        try:
            with (folder/'metadata.json').open(encoding='utf8') as f:meta=json.load(f)
            read_models.save('audible',book['id'],meta.get('audible_file'))
        except (OSError,ValueError):pass
        processed=catalogue.get_processed(book['id']) or {}
        if processed.get('status')=='done' and processed.get('out_dir'):
            try:
                with open(os.path.join(processed['out_dir'],'metadata.json'),encoding='utf8') as f:read_models.save('output',book['id'],json.load(f))
            except (OSError,ValueError):pass
        plan=catalogue.get_plan(book['id'])
        if plan and plan.get('plan'):
            try:read_models.save('plan',book['id'],pq.summary(plan['plan']))
            except KeyError:pass
        count+=1
    from app.ingest import store
    snapshot=store.workflow_snapshot()
    for rid in snapshot['runs']:pipeline.publish_run(rid)
    read_models.save('providers','configured',providers.status())
    read_models.save('settings','current',settings.all_settings())
    return dict(indexed=count)
