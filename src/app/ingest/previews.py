"""On-demand listening copies of the selected source; never modify source audio."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory
from threading import Lock
from app import oslib, settings
from app.ingest import pipeline, store
from app.process import plan, probe

VERSION='listening-v1-lufs20'
POOL=ThreadPoolExecutor(max_workers=2,thread_name_prefix='listening-preview')
JOBS={}
LOCK=Lock()


def context(run_id):
    run=store.get(run_id);inv=pipeline.stage_data(run,'inventory');selection=pipeline.stage_data(run,'selection')
    if not inv or not selection:raise ValueError('Selected audio is not ready for listening')
    by_path={f['path']:f for f in inv['files']}
    files=[by_path[p] for p in selection['paths']]
    # Fast freshness check on every request, including cache hits. Cache identity
    # also contains the saved content hashes and exact selected file order.
    for f in files:
        stat=Path(f['path']).stat()
        if stat.st_size!=f['size'] or stat.st_mtime_ns!=f['mtime_ns']:raise ValueError('Source audio changed; re-ingest before listening')
    key=hashlib.sha256(json.dumps([VERSION,run_id,[(f['path'],f['sha256'],f['size'],f['mtime_ns']) for f in files]],sort_keys=True).encode()).hexdigest()
    timeline=plan.timeline([dict(f['probe'],path=f['path']) for f in files]);duration=timeline[-1][1]
    if duration<=0:raise ValueError('Selected audio has no duration')
    folder=Path(settings.data_dir())/'listening-previews'/key
    rng=random.Random(key);length=min(30.,duration/5)
    samples=[dict(index=i,start_seconds=round(max(0,min(duration-length,duration*((i+.5)/5+rng.uniform(-.06,.06))-length/2)),3),duration_seconds=round(length,3)) for i in range(5)]
    return folder,timeline,samples


def status(run_id,ctx=None):
    folder,_,samples=ctx or context(run_id)
    return dict(samples=[dict(s,url=f'/api/ingest/runs/{run_id}/samples/{s["index"]}',status='ready' if (folder/f'{s["index"]}.mp3').is_file() else 'failed' if (folder/f'{s["index"]}.error').exists() else 'generating') for s in samples])


def generate(ctx,index):
    folder,timeline,samples=ctx;s=samples[index];folder.mkdir(parents=True,exist_ok=True);output=folder/f'{index}.mp3'
    with (folder/f'{index}.lock').open('a+b') as lock,oslib.file_lock(lock):
        if output.is_file():return output
        with TemporaryDirectory(dir=folder) as td:
            temp=Path(td)/'sample.mp3';args=probe.input_args(plan.segments(timeline,s['start_seconds'],s['start_seconds']+s['duration_seconds']),td)
            result=probe.run(['ffmpeg','-nostdin','-hide_banner','-v','error']+args+['-map','0:a:0','-vn','-t',str(s['duration_seconds']),'-af','loudnorm=I=-20:TP=-1.5:LRA=11','-ar','44100','-c:a','libmp3lame','-b:a','192k',str(temp)],120)
            if result.returncode or not temp.is_file() or not temp.stat().st_size:raise ValueError('Could not generate listening sample')
            temp.replace(output)
        (folder/f'{index}.error').unlink(missing_ok=True)
    return output


def generate_all(ctx):
    folder,_,samples=ctx
    for s in samples:
        try:generate(ctx,s['index'])
        except (ValueError,OSError,probe.ToolError):
            folder.mkdir(parents=True,exist_ok=True);(folder/f'{s["index"]}.error').write_text('Preview generation failed',encoding='utf8')


def prepare(run_id):
    ctx=context(run_id);folder=ctx[0]
    with LOCK:
        # Completed jobs leave no growing in-memory registry; the disk cache
        # remains the source of truth across restarts and multiple processes.
        for key in list(JOBS):
            if JOBS[key].done():del JOBS[key]
        if folder not in JOBS and any(s['status']!='ready' for s in status(run_id,ctx)['samples']):
            folder.mkdir(parents=True,exist_ok=True)
            for p in folder.glob('*.error'):p.unlink(missing_ok=True)
            JOBS[folder]=POOL.submit(generate_all,ctx)
    return status(run_id,ctx)


def audio(run_id,index):
    if index not in range(5):raise ValueError('No such listening sample')
    ctx=context(run_id);path=ctx[0]/f'{index}.mp3'
    if not path.is_file():raise ValueError('Listening sample is not ready yet')
    return path
