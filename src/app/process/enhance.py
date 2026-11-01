"""Prepare user-confirmed medium-quality narration for the ordinary encoder."""
import copy
import json
import os
from pathlib import Path
import subprocess

from app.process import plan as P, probe


def prepare(plan, folder):
    root = Path(__file__).resolve().parents[3]
    code = Path(os.environ.get('STRATA_DIAMOND_CODE', root/'data/diamond-inference'))
    model = Path(os.environ.get('STRATA_DIAMOND_MODEL', code/'data/pretrained/diamond.safetensors'))
    python = Path(os.environ.get('STRATA_DIAMOND_PYTHON', root/'data/torch-rocm-env/run-amd'))
    if not python.is_file() or not model.is_file() or not (code/'diamond/infer.py').is_file():
        raise probe.ToolError('Diamond environment or model missing; configure STRATA_DIAMOND_PYTHON, STRATA_DIAMOND_CODE and STRATA_DIAMOND_MODEL')
    jobs=[]
    for i,ch in enumerate(plan['chapters']):
        inp=Path(folder)/f'diamond-input-{i}.wav'; out=Path(folder)/f'diamond-output-{i}.wav'
        cmd=['ffmpeg','-nostdin','-hide_banner','-v','error','-y',*probe.input_args([tuple(s) for s in ch['segments']],folder),'-map','0:a:0','-ac','1','-ar','24000','-c:a','pcm_f32le',str(inp)]
        result=probe.run(cmd,7200)
        if result.returncode or not inp.is_file():raise probe.ToolError('Diamond input decoding failed: '+result.stderr[-500:])
        jobs.append(dict(input=str(inp),output=str(out)))
    manifest=Path(folder)/'diamond-manifest.json';manifest.write_text(json.dumps(jobs),encoding='utf8')
    env=dict(os.environ,STRATA_DIAMOND_CODE=str(code),STRATA_DIAMOND_MODEL=str(model))
    cache = root/'data/diamond-cache'
    if cache.is_dir():
        env.setdefault('XDG_CACHE_HOME',str(cache)); env.setdefault('HF_HOME',str(cache/'huggingface')); env.setdefault('TORCH_HOME',str(cache/'torch'))
    env.setdefault('OMP_NUM_THREADS','4');env.setdefault('MKL_NUM_THREADS','4');env.setdefault('OPENBLAS_NUM_THREADS','4')
    try:
        result=subprocess.run([str(python),str(root/'scripts/diamond_worker.py'),str(manifest)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=7*24*3600)
    except (OSError,subprocess.TimeoutExpired) as e:raise probe.ToolError(f'Diamond failed: {e}') from e
    if result.returncode:raise probe.ToolError('Diamond failed: '+result.stderr[-1000:])
    if any(not Path(j['output']).is_file() for j in jobs):raise probe.ToolError('Diamond did not produce all chapters')
    enhanced=copy.deepcopy(plan); enhanced['channels']=1
    for ch,job in zip(enhanced['chapters'],jobs):
        ch['segments']=[(job['output'],0,None)];ch['mode']='encode'
    measured=probe.measure_book([(j['output'],0,None) for j in jobs],1)
    if measured['I'] is None or measured['TP'] is None:raise probe.ToolError('Cannot measure Diamond output')
    gain=round(min(P.TARGET_LUFS-measured['I'],P.TP_LIMIT-measured['TP']),2)
    enhanced['loudness'].update(apply_db=gain,gain_steps=0,decision='Diamond and gradual levelling',reencoded=True)
    enhanced['enhancement']['measured_lufs']=measured['I']
    return enhanced
