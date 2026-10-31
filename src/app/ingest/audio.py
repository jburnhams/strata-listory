"""Representation alignment and quality metrics; no automatic performer inference."""
from array import array
import math
from pathlib import Path
import re
from app import audio_compare as A
from app.ingest import inventory
from app.process import plan as P, probe


def alignment(inv,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);by_path={f['path']:f for f in inv['files']};representations=[];features={};parts={}
    for rep in inv['representations']:
        stream=array('f');messages=[];parts[rep['id']]=[]
        for n,path in enumerate(rep['files']):
            values,info=A.extract(path,output/(rep['id']+f'-{n}.f32'))
            if info['sha256']!=by_path[path]['sha256']:raise ValueError('audio changed since inventory')
            parts[rep['id']].append((path,values));stream.extend(values)
            if info['decode_messages']:messages.append(info['decode_messages'])
        features[rep['id']]=stream;representations.append(dict(rep,decode_messages=messages))
    reference=max(representations,key=lambda r:r['duration']);index=A.Index(features[reference['id']]);duration=len(features[reference['id']])*A.STEP
    for rep in representations:
        if rep['id']==reference['id']:
            rep.update(coverage=1.0,matched_fraction=1.0,max_gap_seconds=0,min_correlation=1.0,windows=[])
        else:
            windows=[];base=0.0
            for path,values in parts[rep['id']]:
                for w in A.align(index,values):
                    windows.append(dict(w,source_file=path,source_start=w['query_start'],query_start=w['query_start']+base,query_end=w['query_end']+base))
                base+=len(values)*A.STEP
            matches=[w for w in windows if w['status']=='matched'];intervals=[(w['reference_start'],w['reference_start']+w['query_end']-w['query_start']) for w in matches];cov=A.coverage(intervals,duration)
            rep.update(coverage=cov['coverage_fraction'],matched_fraction=len(matches)/len(windows) if windows else 0,max_gap_seconds=max((b-a for a,b in cov['gaps']),default=0),min_correlation=min((m['correlation'] for m in matches),default=0),windows=windows,gaps=cov['gaps'])
    missing=sum(r['missing_tracks'] or 0 for r in representations)+sum(r['duplicate_tracks'] for r in representations)
    # Only one reference cannot establish that every source represents the same performance.
    unresolved=sum(r['matched_fraction']<.98 for r in representations)
    return dict(method=A.VERSION+'/per-file',reference_id=reference['id'],representations=representations),{'alignment.coverage':min(r['coverage'] for r in representations),'alignment.max_gap_seconds':max(r['max_gap_seconds'] for r in representations),'alignment.unmatched_fraction':max(1-r['matched_fraction'] for r in representations),'alignment.missing_tracks':missing,'alignment.unresolved_representations':unresolved}


def band_sample(segments):
    with __import__('tempfile').TemporaryDirectory() as td:
        args=probe.input_args(segments,td);levels=[]
        for high in (False,True):
            filt='aresample=22050,'+('highpass=f=4000,'*4 if high else '')+'astats=metadata=0:reset=0'
            r=probe.run(['ffmpeg','-nostdin','-hide_banner','-nostats','-v','info']+args+['-map','0:a:0','-af',filt,'-f','null','-'],120)
            values=re.findall(r'RMS level dB:\s*(-?[\d.]+)',r.stderr)
            if r.returncode or not values:return None
            levels.append(float(values[-1]))
        return levels[1]-levels[0] if all(math.isfinite(v) for v in levels) else None


def quality(inv,aligned):
    by_path={f['path']:f['probe'] for f in inv['files']};results=[]
    for rep in aligned['representations']:
        infos=[by_path[p] for p in rep['files']];segments=[(p,0,None) for p in rep['files']];m=probe.measure_book(segments,max(f['channels'] for f in infos));tl=P.timeline(infos)
        m={k:(None if isinstance(v,float) and not math.isfinite(v) else v) for k,v in m.items()}
        sample=[]
        # Comparable fractions only for globally matching, full representations.
        for fraction in (.1,.5,.9):
            at=max(0,rep['duration']*fraction-15);sample.append(band_sample(P.segments(tl,at,min(at+30,rep['duration']))))
        valid=[v for v in sample if v is not None];phase=max(0,m['I']-m['mono_I']) if m.get('mono_I') is not None and m.get('I') is not None else 0
        results.append(dict(id=rep['id'],measurements=m,band4k_samples=sample,band4k_relative_db=sum(valid)/len(valid) if valid else None,phase_loss_db=phase,decode_errors=len(rep['decode_messages'])))
    return dict(version=1,representations=results),{'quality.complete':int(all(r['band4k_relative_db'] is not None and all(r['measurements'].get(k) is not None and math.isfinite(r['measurements'][k]) for k in ('I','TP','LRA')) for r in results)),'quality.decode_errors':sum(r['decode_errors'] for r in results),'quality.true_peak':max((r['measurements']['TP'] for r in results if r['measurements']['TP'] is not None),default=None),'quality.phase_loss_db':max(r['phase_loss_db'] for r in results)}


def select(inv,aligned,quality_data):
    qs={r['id']:r for r in quality_data['representations']};ranked=[]
    for rep in aligned['representations']:
        q=qs[rep['id']];complete=rep['coverage']>=.995 and rep['matched_fraction']>=.995 and not rep['missing_tracks'] and not rep['duplicate_tracks']
        score=(int(complete),-q['decode_errors'],int(q['measurements']['TP'] is not None and q['measurements']['TP']<=0),q['band4k_relative_db'] if q['band4k_relative_db'] is not None else -999)
        ranked.append((score,rep,q))
    ranked.sort(key=lambda t:t[0],reverse=True);score,chosen,q=ranked[0];ambiguous=not score[0] or q['decode_errors']>0 or q['measurements']['TP'] is None
    if len(ranked)>1:
        other=ranked[1]
        if score[:3]==other[0][:3] and abs(score[3]-other[0][3])<3:ambiguous=True
    paths=chosen['files'];manifest=[{k:f[k] for k in ('path','sha256','size','mtime_ns')} for f in inv['files']]
    return dict(selected_id=chosen['id'],paths=paths,manifest=manifest,measurements=q['measurements'],ranking=[dict(id=r['id'],score=list(s),reasons=dict(coverage=r['coverage'],matched_fraction=r['matched_fraction'],true_peak=v['measurements']['TP'],band4k_relative_db=v['band4k_relative_db'],decode_errors=v['decode_errors'])) for s,r,v in ranked],reason='Completeness, decode success, peak safety, then sampled spectral retention; spectral energy is not a perceptual/noise score.'),{'alignment.coverage':chosen['coverage'],'alignment.max_gap_seconds':chosen['max_gap_seconds'],'alignment.unmatched_fraction':1-chosen['matched_fraction'],'alignment.missing_tracks':(chosen['missing_tracks'] or 0)+chosen['duplicate_tracks'],'selection.ambiguous':int(ambiguous),'quality.complete':int(q['band4k_relative_db'] is not None and all(q['measurements'].get(k) is not None and math.isfinite(q['measurements'][k]) for k in ('I','TP','LRA'))),'quality.decode_errors':q['decode_errors'],'quality.true_peak':q['measurements']['TP'],'quality.phase_loss_db':q['phase_loss_db']}
