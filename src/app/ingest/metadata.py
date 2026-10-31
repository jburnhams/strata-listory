"""Multi-provider candidate collection with raw archives and edition-aware selection."""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
from app import matcher, merge, providers, sources
from app.ingest.observe import capture


def names(value): return {merge.norm(v) for v in value or [] if v}


def collect(identity,inv,output,provider_names):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);archives=[];results={};book=identity['book'];duration=max(r['duration'] for r in inv['representations']);anchor=book.get('asin') or ''
    def archive(e):
        e=dict(e,observed_at=datetime.now(timezone.utc).isoformat());p=output/(uuid.uuid4().hex+'.json')
        with p.open('x',encoding='utf8') as f:json.dump(e,f,indent=2,ensure_ascii=False,allow_nan=False)
        archives.append(str(p))
    with capture(archive):
        for name in provider_names:
            p=providers.get(name)
            try:
                reason=p.unavailable()
                if reason:results[name]=dict(status='unavailable',reason=reason,candidates=[]);continue
                found=p.by_asin(anchor) if anchor and name in ('audible','audnexus','audiobookdb','hardcover') else None
                cs=[found] if found else p.search(book['title'],book.get('author',''))
                results[name]=dict(status='ok',candidates=cs)
                if name=='audible' and not anchor:
                    ranked=sorted(cs,key=lambda c:rank(book,c,duration,identity),reverse=True)
                    if ranked and matcher.score(book,ranked[0])>=.9:anchor=ranked[0].get('asin','')
            except (providers.MatchError,ValueError) as e:results[name]=dict(status='error',reason=str(e),candidates=[])
        # Fetch details only for compatible work candidates; never the first hit blindly.
        for name in ('hardcover','applebooks'):
            if name not in results:continue
            compatible=[c for c in results[name]['candidates'] if compatible_work(book,c)]
            if compatible:
                selected=max(compatible,key=lambda c:c.get('ratings_count') or 0) if name=='hardcover' else max(compatible,key=lambda c:matcher.score(book,c))
                try:
                    d=providers.get(name).details(selected['id'] if name=='hardcover' else selected)
                    if d:results[name]['details']=d
                except (providers.MatchError,ValueError) as e:results[name]['details_error']=str(e)
    return dict(providers=results,archives=archives),{'metadata.provider_attempts':sum(r['status']!='unavailable' for r in results.values()),'metadata.providers_configured':len(results),'metadata.providers_unavailable':sum(r['status']=='unavailable' for r in results.values()),'metadata.provider_errors':sum(r['status']=='error' for r in results.values()),'metadata.raw_responses':len(archives)}


def compatible_work(book,c):
    return matcher.score(book,c)>=.85 and names(c.get('authors'))==names(sources._names(book.get('author','')))


def rank(book,c,duration,identity):
    runtime=c.get('runtime_minutes');err=abs(runtime*60-duration)/duration if isinstance(runtime,(float,int)) and runtime>0 else 100
    narrator=names(sources._names(book.get('narrator','')));agrees=not narrator or narrator==names(c.get('narrators'))
    return (int(agrees),int(err<=.02),matcher.score(book,c),-err)


def resolve(identity,inv,collected):
    book=identity['book'];duration=max(r['duration'] for r in inv['representations']);recs={'catalogue':sources.from_catalogue(book)};candidates=[];rejected=[];community={}
    for side in inv['sidecars']:
        from app import details
        recs['audible_file']=sources.from_audible_file(details.audible_summary(side['raw']))
    for name,e in collected['providers'].items():
        cs=e.get('candidates',[])
        if name in ('audible','audnexus','audiobookdb','applebooks'):
            if name=='applebooks' and e.get('details'):cs=[e['details']]+cs
            for c in cs:
                candidates.append(dict(candidate=c,rank=list(rank(book,c,duration,identity)),score=matcher.score(book,c)))
        else:
            compatible=[c for c in cs if compatible_work(book,c)]
            if compatible:
                chosen=max(compatible,key=lambda c:(c.get('edition_count') or 0,c.get('ratings_count') or 0))
                if name=='hardcover' and e.get('details') and e['details']['id']==chosen['id']:
                    recs[name]=sources.from_hardcover(e['details']);community[name]=e['details']
                else:recs[name]=sources.from_candidate(chosen);community[name]=chosen
                for c in cs:
                    if c!=chosen:rejected.append(dict(source=name,candidate=c,reason='alternative work/edition; not selected'))
            else:
                for c in cs:rejected.append(dict(source=name,candidate=c,reason='title/complete author list incompatible'))
    candidates.sort(key=lambda x:tuple(x['rank']),reverse=True);best=candidates[0]['candidate'] if candidates else None
    score=candidates[0]['score'] if candidates else None;runtime_error=None;narrator_agrees=None;language_agrees=None;abridgement_known=0;ambiguity=1;critical=0
    if best:
        rt=best.get('runtime_minutes');runtime_error=abs(rt*60-duration)/duration if isinstance(rt,(int,float)) and rt>0 else None
        wanted=names(sources._names(book.get('narrator','')));have=names(best.get('narrators'))
        narrator_agrees=int(wanted==have) if wanted and have else None
        language=lambda x:{'en':'english','eng':'english'}.get(str(x).lower(),str(x).lower())
        language_agrees=int(language(identity['language'])==language(best['language'])) if identity['language'] and best.get('language') else None
        abridgement_known=int(best.get('abridged') is not None and (identity['abridged'] is None or identity['abridged']==best['abridged']))
        alternatives=[]
        for entry in candidates:
            c=entry['candidate'];same=names(c.get('narrators'))==have and language(c.get('language',''))==language(best.get('language',''))
            if c.get('runtime_minutes') and rt:same=same and abs(c['runtime_minutes']-rt)<=max(2,rt*.02)
            if entry['score']>=.9 and entry['rank'][1] and not same:alternatives.append(c)
            if same and entry['score']>=.85:recs[c['source']]=sources.from_candidate(c)
            else:rejected.append(dict(source=c['source'],candidate=c,reason='different/unverified recording, language, runtime or identity'))
        ambiguity=int(bool(alternatives))
        if best.get('asin') and book.get('asin') and best['asin']!=book['asin']:critical+=1
        if wanted and have!=wanted:critical+=1
        if names(best.get('authors'))!=names(sources._names(book.get('author',''))):critical+=1
        if identity['abridged'] is not None and best.get('abridged') is not None and identity['abridged']!=best['abridged']:critical+=1
        for field in ('asin','narrators','language','abridged'):
            values={json.dumps(r[field],sort_keys=True) for s,r in recs.items() if s in merge.AUDIOBOOK and r.get(field) not in (None,'',[])}
            if len(values)>1:critical+=1
    resolved=sources.resolve(book,recs)
    return dict(records=recs,resolved=resolved,recording=best,candidates=candidates,rejected=rejected,community=community),{'metadata.original_work_link':int(any(s in recs for s in ('openlibrary','hardcover','googlebooks'))),'metadata.work_families':len({('audible' if s in ('audnexus','audible_file') else s) for s in recs if s!='catalogue'}),'metadata.critical_conflicts':critical,'recording.score':score,'recording.runtime_error':runtime_error,'recording.narrator_agrees':narrator_agrees,'recording.language_agrees':language_agrees,'recording.abridgement_known':abridgement_known,'recording.ambiguous':ambiguity}
