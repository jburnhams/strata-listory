"""Multi-provider candidate collection with raw archives and edition-aware selection."""
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import re
import uuid
from app import book_titles, contributors, matcher, merge, providers, sources
from app.ingest.observe import capture,report

VERSION=18


def names(value): return {contributors.name_key(v) for v in value or [] if v}


CAST_LABELS={'full cast','a full cast','full cast production','ensemble cast','multiple narrators','various narrators'}


def cast_credit(value):
    values=sources._names(value) if isinstance(value,str) else value or []
    return [v for v in values if merge.norm(v) not in CAST_LABELS],any(merge.norm(v) in CAST_LABELS for v in values)


def narrator_names(value,reference,*,strong_title=False):
    """Map only unambiguous name variants onto the reference credits."""
    result=set()
    for name in names(value):
        matching=[other for other in reference if contributors.same_author(name,other,strong_title)]
        result.add(matching[0] if len(matching)==1 else name)
    return result if len(result)==len(names(value)) else names(value)


def local_cast_agreement(local,candidate):
    wanted,ensemble=cast_credit(local);have=names(cast_credit(candidate)[0])
    if not have:return None
    if ensemble and len(have)<2:return 0
    if not wanted:return None
    normal=narrator_names(wanted,have)
    return int(normal==have or ensemble and normal<=have)


def cast_agrees(reference,other,book):
    have=names(cast_credit(reference.get('narrators'))[0]);actual=narrator_names(cast_credit(other.get('narrators'))[0],have)
    if actual==have:return True
    # A partial roster is corroboration only when it contains multiple matching
    # names and the work, runtime, language and release identify the same edition.
    if min(len(have),len(actual))<2 or not (actual<=have or have<=actual) or not compatible_work(book,dict(other,title=other.get('title') or '',subtitle=other.get('subtitle') or '')):return False
    rt=reference.get('runtime_minutes');other_rt=other.get('runtime_minutes')
    if not rt or not other_rt or abs(rt-other_rt)>max(2,rt*.02):return False
    if not language(reference.get('language')) or language(reference.get('language'))!=language(other.get('language')):return False
    same_id=bool(reference.get('asin') and reference['asin']==other.get('asin'))
    return same_id or bool(reference.get('release_date') and other.get('release_date') and reference['release_date'][:10]==other['release_date'][:10])


def authors(book,c):
    strong_title=title_match(book,c)['score']>=.95 or bool(merge.main_title(book['title'])) and merge.main_title(book['title'])==merge.main_title(c.get('title',''))
    reference=names(contributors.authors(book.get('author','')))
    return set().union(*(narrator_names([name],reference,strong_title=strong_title) for name in contributors.authors(c.get('authors') or c.get('author',''),contributors.parse(book.get('author','')))))


def work_title(value):
    """Edition labels identify a version of a work; retain the raw recording title."""
    title=re.sub(r'\b(?:the\s+)?(?:(?:\d+(?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)[\s–—-]+anniversary(?:[\s–—-]+edition)?|anniversary[\s–—-]+edition)\b','',value or '',flags=re.I)
    title=re.sub(r'\b(?:the\s+)?(?:\d+(?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)[\s–—-]+edition\b','',title,flags=re.I)
    title=re.sub(r'\(\s*\)|\[\s*\]','',title)
    title=re.sub(r'(?:\s*[:,]\s*){2,}',': ',title)
    title=re.sub(r'\s*:\s*',': ',title)
    return re.sub(r'\s+',' ',title).strip(' :,–—-()')


def primary_credits(book,record):
    """Keep shared author anchors canonical and retain other credits as evidence."""
    wanted=names(contributors.authors(book.get('author','')))
    if not wanted:return record
    primary=[];extra=[];seen=set()
    for name in record.get('authors',[]):
        matched=authors(book,dict(title=record.get('title') or book['title'],subtitle='',authors=[name])) & wanted
        if matched:
            if not matched<=seen:primary.append(name);seen.update(matched)
        else:extra.append(name)
    return dict(record,authors=primary,additional_contributors=extra)


def language(value):
    text=str(value or '').strip().lower().rsplit('/',1)[-1].replace('_','-')
    return {'en':'english','eng':'english','en-us':'english','en-gb':'english'}.get(text,text)


def family(source):return 'audible' if source in ('audnexus','audible_file') else source


def corroboration(records,field,value,normalise):
    supporting=[s for s,r in records.items() if s in merge.AUDIOBOOK and r.get(field) not in (None,'',[]) and normalise(r[field])==value]
    return supporting if len({family(s) for s in supporting})>=2 else []


def collect(identity,inv,output,provider_names):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);archives=[];results={};book=identity['book'];duration=max(r['duration'] for r in inv['representations']);anchor=book.get('asin') or ''
    book_only=all(name in merge.WORK for name in provider_names)
    discovery=[n for n in identity.get('_work_discovery_providers',[]) if n in ('audible','audnexus','audiobookdb','applebooks')] if book_only else []
    search_book=dict(book,title=work_title(book['title']) if book_only else book['title'],author=', '.join(contributors.authors(book.get('author',''))));recording_anchor=None
    def archive(e):
        e=dict(e,observed_at=datetime.now(timezone.utc).isoformat());p=output/(uuid.uuid4().hex+'.json')
        with p.open('x',encoding='utf8') as f:json.dump(e,f,indent=2,ensure_ascii=False,allow_nan=False)
        archives.append(str(p))
    with capture(archive):
        for name in discovery+list(provider_names):
            if name in discovery and recording_anchor:continue
            p=providers.get(name)
            report(f'Looking up {p.label if hasattr(p, "label") else name}',stage='providers' if book_only else 'recording_providers')
            try:
                reason=p.unavailable()
                if reason:results[name]=dict(status='unavailable',reason=reason,candidates=[]);continue
                found=p.by_asin(anchor) if anchor and name in ('audible','audnexus','audiobookdb','hardcover') else None
                cs=[found] if found else p.search(search_book['title'],search_book['author'])
                if name in discovery:
                    # A recording supplies discovery terms, never a licence to
                    # discard a conflicting local author, volume or performance.
                    accepted=[]
                    for c in cs:
                        people=contributors.authors(c.get('authors'),contributors.parse(book.get('author','')))
                        compatible=(compatible_work(book,c) or distinctive_title_match(book,c)) if contributors.authors(book.get('author','')) else title_match(book,c,cs)['score']>=.95
                        exact=bool(anchor and c.get('asin')==anchor)
                        measured=work_candidate_score(book,c)>=.9 and rank(book,c,duration,identity)[:2]==(1,1) and local_cast_agreement(book.get('narrator',''),c.get('narrators'))==1
                        if people and compatible and (exact or measured):accepted.append(c)
                    # Multiple distinct recordings cannot silently choose the
                    # title/author used to discover the written work.
                    unique={recording_key(c) for c in accepted}
                    results[name]=dict(status='ok',candidates=accepted if len(unique)==1 else [],discovery_candidates=cs)
                    if len(unique)==1:
                        recording_anchor=accepted[0]
                        search_book.update(title=work_title(recording_anchor['title']),author=', '.join(contributors.authors(recording_anchor['authors'],contributors.parse(book.get('author','')))))
                    continue
                # Local tags may use an unfamiliar spelling or nickname. A
                # title-only retry broadens discovery; acceptance still checks
                # the complete author identity and recording evidence below.
                if not found and search_book['author'] and not any(compatible_work(search_book,c) for c in cs):
                    extra=p.search(search_book['title'],'')
                    cs=cs+[c for c in extra if not any(c.get('id')==old.get('id') and c.get('source')==old.get('source') for old in cs)]
                results[name]=dict(status='ok',candidates=cs)
                if book_only:
                    hint=work_lookup_hint(book,dict(providers=results))
                    if hint:search_book.update(hint)
                if name=='audible' and not anchor:
                    ranked=sorted(cs,key=lambda c:rank(book,c,duration,identity),reverse=True)
                    if ranked and matcher.score(book,ranked[0])>=.9:anchor=ranked[0].get('asin','')
                # Use an identifier-confirmed recording's work title for later
                # book searches, rather than a filename with a long subtitle.
                if name in ('audible','audnexus'):
                    confirmed=[c for c in cs if anchor and c.get('asin')==anchor and compatible_work(book,c)]
                    if confirmed:
                        c=max(confirmed,key=lambda c:rank(book,c,duration,identity))
                        recording_anchor=c
                        search_book=dict(book,title=re.split(r'\s*[:(–—]|\s-\s',c['title'],maxsplit=1)[0].strip(),author=', '.join(contributors.authors(c.get('authors'),contributors.parse(book.get('author','')))))
            except (providers.MatchError,ValueError) as e:results[name]=dict(status='error',reason=str(e),candidates=[])
        if book_only:
            inferred_identity,inferred=inferred_work_identity(identity,dict(providers=results))
            if inferred:
                identity=inferred_identity;book=identity['book'];search_book=dict(search_book,author=book['author'])
                known=[c for entry in results.values() for c in entry['candidates'] if compatible_work(book,c)]
                if known:search_book['title']=work_title(max(known,key=lambda c:matcher.score(book,c))['title'])
        # Fetch details only for compatible work candidates; never the first hit blindly.
        for name in ('hardcover','applebooks'):
            if name not in results:continue
            compatible=[c for c in results[name]['candidates'] if compatible_work(search_book,c,results[name]['candidates'])]
            if compatible:
                selected=max(compatible,key=lambda c:work_preference(search_book,c,compatible)) if name=='hardcover' else max(compatible,key=lambda c:(bool((recording_anchor or {}).get('release_date')) and c.get('release_date','')[:10]==recording_anchor['release_date'][:10],matcher.score(book,c)))
                details=getattr(providers.get(name),'details',None)
                if not callable(details):continue
                try:
                    d=details(selected['id'] if name=='hardcover' else selected)
                    if d:results[name]['details']=d
                except (providers.MatchError,ValueError) as e:results[name]['details_error']=str(e)
        if 'openlibrary' in results:
            p=providers.get('openlibrary');lookup=getattr(p,'publication_editions',None)
            compatible=[c for c in results['openlibrary']['candidates'] if compatible_work(search_book,c,results['openlibrary']['candidates'])]
            if callable(lookup) and compatible:
                selected=max(compatible,key=lambda c:work_preference(search_book,c,compatible))
                report('Looking up publishers of the matched book’s editions',stage='providers' if book_only else 'recording_providers')
                try:
                    years=[sources._year(c.get('original_year')) for source,entry in results.items() if source in merge.WORK for c in entry['candidates'] if compatible_work(search_book,c)]
                    year=min((y for y in years if y),default=None)
                    results['openlibrary']['publisher_editions']=lookup(dict(selected,title=search_book['title']),year)
                    results['openlibrary']['edition_work_key']=selected['id']
                except providers.MatchError as error:results['openlibrary']['details_error']=str(error)
    return dict(providers=results,archives=archives),{'metadata.provider_attempts':sum(r['status']!='unavailable' for r in results.values()),'metadata.providers_configured':len(results),'metadata.providers_unavailable':sum(r['status']=='unavailable' for r in results.values()),'metadata.provider_errors':sum(r['status']=='error' for r in results.values()),'metadata.raw_responses':len(archives)}


def title_match(book,c,candidates=()):
    return book_titles.analyse(work_title(book['title']),dict(c,title=work_title(c.get('title','')),subtitle=work_title(c.get('subtitle',''))),book.get('author',''),[dict(other,title=work_title(other.get('title','')),subtitle=work_title(other.get('subtitle',''))) for other in candidates])


def work_candidate_score(book,c,candidates=()):return round(.7*title_match(book,c,candidates)['score']+.3*matcher.author_score(book.get('author',''),c),3)


def work_preference(book,c,candidates=()):
    score=title_match(book,c,candidates)['score'];lang=language(c.get('language'))
    return (score>=.95,1 if lang=='english' else -1 if lang else 0,work_candidate_score(book,c,candidates),c.get('edition_count') or 0,c.get('ratings_count') or 0)


def compatible_work(book,c,candidates=()):
    wanted=names(contributors.authors(book.get('author','')))
    if not wanted or title_match(book,c,candidates)['score']<.85 or not authors(book,c) & wanted:return False
    # A shortened credit cannot bridge two explicitly different initials for
    # the same title. Keep exact credits usable, but withhold ambiguous support.
    for a in wanted:
        for b in contributors.authors(c.get('authors') or c.get('author','')):
            if not contributors.omitted_middle_initial(a,b):continue
            short=min((a,b),key=lambda value:len(contributors.name_key(value).split()))
            expanded={contributors.name_key(name) for other in [c,*candidates] if title_match(book,other)['score']>=.95 for name in contributors.authors(other.get('authors') or other.get('author','')) if contributors.omitted_middle_initial(short,name)}
            if len(contributors.name_key(a).split())==3:expanded.add(contributors.name_key(a))
            if len(expanded)>1:return False
    return True


def rank(book,c,duration,identity):
    runtime=c.get('runtime_minutes');err=abs(runtime*60-duration)/duration if isinstance(runtime,(float,int)) and runtime>0 else 100
    local=book.get('narrator','');narrator,ensemble=cast_credit(local);agreement=local_cast_agreement(local,c.get('narrators'))
    agrees=not narrator and not ensemble or agreement==1 or ensemble and agreement is None and len(names(cast_credit(c.get('narrators'))[0]))>=2
    return (int(agrees),int(err<=.02),matcher.score(dict(book,title=book.get('recording_title') or book['title']),c),-err)


def candidate_score(book,c):
    return max(matcher.score(book,c),matcher.score(dict(book,title=book.get('recording_title') or book['title']),c),work_candidate_score(book,c))


def recording_key(c):
    fallback=hashlib.sha256(json.dumps({k:c.get(k) for k in ('title','narrators','runtime_minutes','language','abridged')},sort_keys=True).encode()).hexdigest()[:20]
    return f"{c['source']}:{c.get('id') or c.get('asin') or c.get('url') or fallback}"


def same_recording(a,b,book):
    have_a=cast_credit(a.get('narrators'))[0];have_b=cast_credit(b.get('narrators'))[0]
    shared_id=bool(a.get('asin') and a['asin']==b.get('asin') or a.get('source')==b.get('source') and a.get('id') and a['id']==b.get('id'))
    if have_a and have_b and not cast_agrees(a,b,book) or not (have_a and have_b) and not shared_id:return False
    lang_a=language(a.get('language'));lang_b=language(b.get('language'))
    if lang_a and lang_b and lang_a!=lang_b or not (lang_a and lang_b) and not shared_id:return False
    if a.get('abridged') is not None and b.get('abridged') is not None and a['abridged']!=b['abridged']:return False
    rt=a.get('runtime_minutes');other=b.get('runtime_minutes')
    return bool(rt and other and abs(rt-other)<=max(2,rt*.02))


def _recording_evidence(identity,inv,collected):
    book=identity['book'];duration=max(r['duration'] for r in inv['representations']);recs={'catalogue':sources.from_catalogue(book)};candidates=[];rejected=[];community={}
    for side in inv['sidecars']:
        from app import details
        recs['audible_file']=sources.from_audible_file(details.audible_summary(side['raw']),contributors.parse(book.get('author','')))
    for name,e in collected['providers'].items():
        cs=e.get('candidates',[])
        if name in ('audible','audnexus','audiobookdb','applebooks'):
            if name=='applebooks' and e.get('details'):cs=[e['details']]+cs
            for c in cs:
                candidates.append(dict(candidate=c,rank=list(rank(book,c,duration,identity)),score=candidate_score(book,c)))
    candidates.sort(key=lambda x:tuple(x['rank']),reverse=True)
    groups=[]
    for entry in candidates:
        c=entry['candidate']
        if not entry['rank'][1] or not compatible_work(book,c):continue
        group=next((group for group in groups if all(same_recording(c,other['candidate'],book) for other in group)),None)
        if group is None:groups.append([entry])
        else:group.append(entry)
    choices=[]
    for group in groups:
        representative=max(group,key=lambda e:(e['candidate'].get('abridged') is not None,tuple(e['rank'])))['candidate']
        choices.append(dict(key=recording_key(representative),candidate=representative,sources=sorted({e['candidate']['source'] for e in group}),runtime_error=abs(representative['runtime_minutes']*60-duration)/duration))
    selected=next((c for c in choices if c['key']==identity.get('_recording_choice')),None)
    best=(selected or (choices[0] if choices else {})).get('candidate') or (candidates[0]['candidate'] if candidates else None)
    # Work catalogues often omit the subtitle. Compare them with the main
    # title of the recording whose full identity was already checked.
    work_book=dict(book,title=best['title']) if best and candidate_score(book,best)>=.9 and compatible_work(book,best) else book
    for name,e in collected['providers'].items():
        if name in ('audible','audnexus','audiobookdb','applebooks'):continue
        cs=e.get('candidates',[]);compatible=[c for c in cs if compatible_work(work_book,c,cs)]
        if compatible:
            chosen=max(compatible,key=lambda c:(c.get('edition_count') or 0,c.get('ratings_count') or 0))
            if name=='hardcover' and e.get('details') and e['details']['id']==chosen['id']:
                recs[name]=sources.from_hardcover(e['details'],contributors.parse(book.get('author','')));community[name]=e['details']
            else:recs[name]=sources.from_candidate(chosen,contributors.parse(book.get('author','')));community[name]=chosen
            for c in cs:
                if c!=chosen:rejected.append(dict(source=name,candidate=c,reason='alternative work/edition; not selected'))
        else:
            for c in cs:rejected.append(dict(source=name,candidate=c,reason='title/complete author list incompatible'))
    score=candidates[0]['score'] if candidates else None;runtime_error=None;narrator_agrees=None;language_agrees=None;abridgement_known=0;ambiguity=1;critical=0
    corroborated={}
    if best:
        rt=best.get('runtime_minutes');runtime_error=abs(rt*60-duration)/duration if isinstance(rt,(int,float)) and rt>0 else None
        wanted=names(cast_credit(book.get('narrator',''))[0]);have=names(cast_credit(best.get('narrators'))[0])
        narrator_agrees=local_cast_agreement(book.get('narrator',''),best.get('narrators'))
        language_agrees=int(language(identity['language'])==language(best['language'])) if identity['language'] and best.get('language') else None
        abridgement_known=int(best.get('abridged') is not None and (identity['abridged'] is None or identity['abridged']==best['abridged']))
        alternatives=[]
        for entry in candidates:
            c=entry['candidate'];same=same_recording(best,c,work_book)
            if c.get('runtime_minutes') and rt:same=same and abs(c['runtime_minutes']-rt)<=max(2,rt*.02)
            # A verified edition can corroborate a storefront's shorter work
            # title only when runtime and recording fields also match.
            linked_work=score>=.9 and compatible_work(work_book,c)
            if not selected and (entry['score']>=.9 or linked_work) and entry['rank'][1] and not same:alternatives.append(c)
            if same and compatible_work(work_book,c) and (entry['score']>=.85 or linked_work and entry['rank'][1]):recs[c['source']]=sources.from_candidate(c,contributors.parse(book.get('author','')))
            else:rejected.append(dict(source=c['source'],candidate=c,reason='different/unverified recording, language, runtime or identity'))
        ambiguity=int(not selected and (len(choices)>1 or bool(alternatives)))
        if not wanted and have and narrator_agrees is None:
            supporting=[s for s,r in recs.items() if s in merge.AUDIOBOOK and cast_credit(r.get('narrators'))[0] and cast_agrees(best,r,work_book)]
            corroborated['narrators']=supporting if len({family(s) for s in supporting})>=2 else []
            narrator_agrees=1 if corroborated['narrators'] else None
        if not identity['language'] and best.get('language'):
            corroborated['language']=corroboration(recs,'language',language(best['language']),language)
            language_agrees=1 if corroborated['language'] else None
        if best.get('asin') and book.get('asin') and best['asin']!=book['asin']:critical+=1
        if narrator_agrees==0:critical+=1
        expected_authors=names(contributors.authors(book.get('author','')))
        if expected_authors and authors(book,best) and not authors(book,best) & expected_authors:critical+=1
        if identity['abridged'] is not None and best.get('abridged') is not None and identity['abridged']!=best['abridged']:critical+=1
        for field in ('asin','narrators','language','abridged'):
            if field=='narrators':
                if any(not cast_agrees(best,r,work_book) for s,r in recs.items() if s in merge.AUDIOBOOK and cast_credit(r.get(field))[0]):critical+=1
                continue
            normalise=language if field=='language' else lambda v:sorted(narrator_names(v,have)) if field=='narrators' else v
            values={json.dumps(normalise(r[field]),sort_keys=True) for s,r in recs.items() if s in merge.AUDIOBOOK and r.get(field) not in (None,'',[])}
            if len(values)>1:critical+=1
    resolved=sources.resolve(book,recs)
    return dict(records=recs,resolved=resolved,recording=best,choices=choices,selected_choice=selected['key'] if selected else None,candidates=candidates,rejected=rejected,community=community,corroboration=corroborated),{'metadata.original_work_link':int(any(s in recs for s in ('openlibrary','hardcover','googlebooks'))),'metadata.work_families':len({family(s) for s in recs if s!='catalogue'}),'metadata.critical_conflicts':critical,'recording.score':score,'recording.runtime_error':runtime_error,'recording.narrator_agrees':narrator_agrees,'recording.language_agrees':language_agrees,'recording.abridgement_known':abridgement_known,'recording.ambiguous':ambiguity}


WORK_FIELDS={'title','subtitle','subtitle_edition','authors','contributors','additional_contributors','original_year','year_kind','publisher_year','publisher_edition','publisher_editions','genres','description','rating','ratings_count','url','series','series_position'}
AUDIO_FIELDS={'narrators','publisher','release_date','release_year','runtime_minutes','asin','language','abridged','isbns'}


def scoped_rows(book,resolved,scope):
    rows=sources.rows(book,resolved)
    allowed=('Title','Author','Year','Publisher') if scope=='work' else ('Narrator','Publisher','ASIN')
    out=[r for r in rows if r['field'] in allowed]
    if scope=='work':
        # Only work-date assertions establish the book's publication year.
        if 'original_year' not in resolved['merged']['fields']:out=[r for r in out if r['field']!='Year']
        for field in allowed:
            if not any(r['field']==field for r in out):out.append(dict(field=field,value='unknown',source='No supporting source',note=''))
    return out


def plan_sections(plan):
    """Split a proposal's frozen assertions without borrowing current run data."""
    meta=plan.get('metadata_evidence') or {}
    if meta.get('work') and meta.get('performance'):return meta['work']['rows'],meta['performance']['rows']
    records=meta.get('records') or {}
    if not records:
        frozen=plan.get('metadata') or []
        if not frozen and (plan.get('title') or plan.get('author')):frozen=[dict(field=field,value=plan.get(key) or 'unknown',source='Saved output plan',note='') for field,key in (('Title','title'),('Author','author'))]
        if not frozen:return [],[]
        work=[dict(r) for r in frozen if r['field'] in ('Title','Author') or r['field']=='Year' and ('first published' in r['source'] or r['source']=='set by hand')]
        audio=[dict(r) for r in frozen if r['field'] in ('Narrator','Publisher','ASIN')]
        for field in ('Narrator','Publisher'):
            if not any(r['field']==field for r in audio):audio.append(dict(field=field,value='unknown',source='Not recorded in this saved plan',note=''))
        for field in ('Publisher','Year'):
            if not any(r['field']==field for r in work):work.append(dict(field=field,value='unknown',source='Not recorded in this saved plan',note=''))
        audio.insert(0,dict(field='Title',value='unknown',source='No separate recording title saved',note=f"Saved output title: {plan.get('title','')}"))
        audio_year=next((r for r in frozen if r['field']=='Year' and 'audiobook release' in r['source']),None)
        audio.append(dict(field='Release date',value=audio_year['value'] if audio_year else 'unknown',source=audio_year['source'] if audio_year else 'Not recorded in this saved plan',note=''))
        return work,audio
    book=dict(title=plan.get('title',''),author=plan.get('author',''),narrator=plan.get('narrator',''),locked=[])
    work={name:{k:v for k,v in record.items() if k in WORK_FIELDS or k=='publisher' and name not in merge.AUDIOBOOK} for name,record in records.items()}
    rows=scoped_rows(book,sources.resolve(book,work,field_sources={'publisher':merge.WORK}),'work')
    frozen_identity={r['field']:r for r in plan.get('metadata',[]) if r['field'] in ('Title','Author')}
    rows=[dict(frozen_identity.get(r['field'],r)) for r in rows]
    audio=[dict(r) for r in plan.get('metadata',[]) if r['field'] in ('Title','Narrator','Publisher','ASIN')]
    recording=meta.get('recording') or {}
    audio.append(dict(field='Release date',value=recording.get('release_date') or 'unknown',source=recording.get('source') or 'No supporting source',note='Audiobook release; separate from original book publication'))
    return rows,audio


def distinctive_title_match(book,c):
    """Full-title evidence only; generic labels and missing subtitles cannot override an author."""
    title=work_title(book['title']);words=merge.norm(title).split()
    informative=[w for w in words if w not in {'a','an','the','of','and','complete','definitive','collection','collected','works','edition','unabridged'}]
    if 'author' in (book.get('locked') or []) or len(words)<4 or len(informative)<2 or len(''.join(words))<24:return False
    target=book_titles.compact(title)
    return any(target==book_titles.compact(work_title(value)) for value in (c.get('title',''),' '.join(v for v in (c.get('title'),c.get('subtitle')) if v)))


def title_author_consensus(book,collected,minimum_families):
    """An observed title collision vetoes inference, even if another author has more votes."""
    matches=[]
    for name,entry in collected['providers'].items():
        if name not in merge.WORK and 'discovery_candidates' not in entry:continue
        for candidate in entry.get('candidates',[]):
            if not distinctive_title_match(book,candidate):continue
            record=entry.get('details') if name=='hardcover' and (entry.get('details') or {}).get('id')==candidate.get('id') else candidate
            people=contributors.authors(record.get('authors'),contributors.parse(book.get('author','')))
            if not people:return None
            matches.append((name,candidate,people))
    if not matches or len({family(name) for name,_,_ in matches})<minimum_families or not any(name in merge.WORK for name,_,_ in matches):return None
    for _,_,reference in matches:
        if any(len(people)!=len(reference) or not all(any(contributors.same_author(a,b,True) for b in people) for a in reference) for _,_,people in matches):return None
    return dict(title=work_title(matches[0][1]['title']),author=', '.join(matches[0][2]))


def inferred_work_identity(identity,collected):
    from copy import deepcopy
    if contributors.authors(identity['book'].get('author','')):
        hint=title_author_consensus(identity['book'],collected,2)
        if not hint:return identity,False
        result=deepcopy(identity);result['book']['author']=hint['author']
        return result,result['book']['author']!=identity['book']['author']
    book=identity['book'];people=[];candidates=[]
    if not re.search(r'[^\W\d_]',book['title']):return identity,False
    # Accepted discovery recordings may corroborate an otherwise unknown writer;
    # Audible and its mirrors still constitute only one independent family.
    discovery={name for name,entry in collected['providers'].items() if 'discovery_candidates' in entry}
    cohort=[c for name,entry in collected['providers'].items() if name in merge.WORK or name in discovery for c in entry.get('candidates',[])]
    for name,entry in collected['providers'].items():
        if name not in merge.WORK and name not in discovery:continue
        for c in entry.get('candidates',[]):
            if title_match(book,c,cohort)['score']<.95:continue
            candidates.append((name,c))
            for person in contributors.authors(c.get('authors') or c.get('author','')):
                found=next((p for p in people if contributors.same_author(person,p['name'],True)),None)
                if found:found['sources'].add(name)
                else:people.append(dict(name=person,sources={name}))
    agreed=[p['name'] for p in people if len({family(s) for s in p['sources']})>=2 and p['sources'].intersection(merge.WORK)]
    if not agreed:return identity,False
    # Distinct books sharing a title must not become invented coauthors.
    families={name for name,c in candidates if all(any(contributors.same_author(w,a,True) for a in contributors.authors(c.get('authors') or c.get('author',''))) for w in agreed)}
    if len(families)<2:return identity,False
    result=deepcopy(identity);result['book']['author']=', '.join(agreed)
    return result,True


def work_lookup_hint(book,collected):
    """Improve discovery with one unambiguous hit; acceptance still needs consensus."""
    if not re.search(r'[^\W\d_]',book['title']):return None
    cohort=[c for name,entry in collected['providers'].items() if name in merge.WORK for c in entry.get('candidates',[])]
    strong=[c for c in cohort if contributors.authors(c.get('authors')) and title_match(book,c,cohort)['score']>=.95 and (not contributors.authors(book.get('author','')) or compatible_work(book,c,cohort))]
    groups=[]
    for c in strong:
        people=contributors.authors(c['authors'])
        if not any(len(people)==len(g) and all(any(contributors.same_author(a,b,True) for b in g) for a in people) for g in groups):groups.append(people)
    if len(groups)!=1:return title_author_consensus(book,collected,1)
    chosen=max(strong,key=lambda c:(title_match(book,c,cohort)['score'],c.get('ratings_count') or 0))
    return dict(title=work_title(chosen['title']),author=', '.join(contributors.authors(chosen['authors'])))


def work_edition_evidence(collected):
    """Use archived attached editions as title evidence, never as new providers."""
    from app.providers.openlibrary import edition_language
    entry=collected['providers'].get('openlibrary',{});aliases=[]
    seen={(c.get('id'),c.get('subtitle_edition')) for c in entry.get('candidates',[]) if c.get('subtitle_edition')}
    for c in entry.get('candidates',[]):
        key=c.get('id') or ''
        if not re.fullmatch(r'/works/OL\d+W',key) or entry.get('edition_work_key')!=key:continue
        main=book_titles.parts(work_title(c.get('title','')))[0]
        for edition in entry.get('publisher_editions',[]):
            if (key,edition.get('key')) in seen:continue
            if edition.get('work_key')!=key or not re.fullmatch(r'/books/OL\d+M',edition.get('key') or ''):continue
            if not book_titles.similar(main,book_titles.parts(work_title(edition.get('title','')))[0]):continue
            aliases.append(dict(c,title=edition['title'],subtitle=edition.get('subtitle') or '',subtitle_edition=edition['key'],language=edition_language(edition)))
            seen.add((key,edition['key']))
    hydrated=[]
    for c in entry.get('candidates',[])+aliases:
        linked=next((e for e in entry.get('publisher_editions',[]) if e.get('work_key')==c.get('id')==entry.get('edition_work_key') and e.get('key')==c.get('subtitle_edition') and re.fullmatch(r'/books/OL\d+M',e.get('key') or '')),None)
        hydrated.append(dict(c,language=edition_language(linked)) if linked and edition_language(linked) else c)
    if hydrated==entry.get('candidates',[]):return collected
    return dict(collected,providers=dict(collected['providers'],openlibrary=dict(entry,candidates=hydrated)))


def full_title_identity(identity,inv,collected):
    """A verified English edition can explain a full title tag beside a short album tag."""
    from copy import deepcopy
    book=identity['book']
    if 'title' in (book.get('locked') or []):return identity
    tags={book_titles.compact(work_title(f['probe']['tags'].get('title',''))) for f in inv['files'] if f['probe']['tags'].get('title')}
    if len(tags)!=1:return identity
    matches={}
    for name,entry in collected['providers'].items():
        if name not in merge.WORK:continue
        for c in entry.get('candidates',[]):
            full=': '.join(v for v in book_titles.parts(c.get('title',''),c.get('subtitle','')) if v)
            if language(c.get('language'))!='english' or book_titles.compact(full) not in tags or not compatible_work(book,c):continue
            matches[book_titles.compact(full)]=full
    if len(matches)!=1:return identity
    result=deepcopy(identity);result['book']['title']=next(iter(matches.values()));return result


def resolve(identity,inv,collected):
    """Identify the written work independently of any recording candidate."""
    collected=work_edition_evidence(collected)
    catalogue_book=identity['book'];identity=full_title_identity(identity,inv,collected);identity,inferred=inferred_work_identity(identity,collected)
    evidence,measure=_recording_evidence(identity,inv,collected);book=identity['book'];known_roles=contributors.parse(book.get('author',''))
    work_scores=[]
    work_records={'catalogue':{k:v for k,v in sources.from_catalogue(catalogue_book).items() if k in WORK_FIELDS}}
    if inferred and contributors.authors(catalogue_book.get('author','')):
        work_records['catalogue']['unverified_authors']=work_records['catalogue']['authors'];work_records['catalogue']['authors']=[]
    work_book=dict(book)
    compatible=[e for e in evidence['candidates'] if compatible_work(book,e['candidate'])]
    if compatible:
        c=max(compatible,key=lambda e:e['score'])['candidate'];work_book['title']=c['title']
    cohort=[c for entry in collected['providers'].values() for c in entry.get('candidates',[])]
    cohort += [entry['details'] for name,entry in collected['providers'].items() if name!='hardcover' and entry.get('details')]
    for name,entry in collected['providers'].items():
        candidates=list(entry.get('candidates',[]))
        if entry.get('details') and name!='hardcover':candidates.insert(0,entry['details'])
        candidates=[c for c in candidates if compatible_work(book,c,cohort) and compatible_work(work_book,c,cohort)]
        if not candidates:continue
        c=max(candidates,key=lambda c:work_preference(book,c,cohort))
        work_scores.append(work_candidate_score(book,c,cohort))
        if name=='hardcover' and entry.get('details') and entry['details']['id']==c['id']:record=sources.from_hardcover(entry['details'],known_roles)
        else:record=sources.from_candidate(c,known_roles)
        record=primary_credits(book,record)
        work_records[name]={k:v for k,v in record.items() if k in WORK_FIELDS or k=='publisher' and name not in merge.AUDIOBOOK}
        work_records[name]['title_match']=title_match(book,c,cohort)['basis']
        if name=='openlibrary' and entry.get('edition_work_key')==c.get('id') and entry.get('publisher_editions'):
            editions=entry['publisher_editions'];work_records[name]['publisher_editions']=editions
            years=[e['year'] for e in editions]+([record['original_year']] if record.get('original_year') else [])
            work_records[name]['original_year']=min(years)
    for side in inv['sidecars']:
        from app import details
        record=sources.from_audible_file(details.audible_summary(side['raw']),known_roles)
        work_records['audible_file']={k:v for k,v in primary_credits(book,record).items() if k in WORK_FIELDS}
    work_resolved=sources.resolve(book,work_records,field_sources={'publisher':merge.WORK})
    original_year=work_resolved['year']
    for name,record in work_records.items():
        if name not in merge.WORK:continue
        if record.get('publisher') and record.get('publisher_year',record.get('original_year'))!=original_year:record['other_edition_publisher']=record.pop('publisher')
        editions=[e for e in record.get('publisher_editions',[]) if e['year']==original_year and e.get('publishers')]
        if editions:
            edition=min(editions,key=lambda e:(e['publish_date'],e['key']))
            record.update(publisher=' / '.join(edition['publishers']),publisher_year=original_year,publisher_edition=edition['key'])
    work_resolved=sources.resolve(book,work_records,field_sources={'publisher':merge.WORK})
    work_conflicts=int(not authors(book,dict(title=work_resolved['title'],subtitle='',authors=work_resolved['authors'])) & names(contributors.authors(book.get('author',''))))
    # Attached editions can bridge a revised/adapted subtitle, but cannot settle
    # conflicting original-publication years accepted by earliest-year selection.
    linked_titles=any('linked editions' in record.get('title_match','') for record in work_records.values())
    work_conflicts+=int(bool(work_resolved['year_note']) and (work_resolved['year_note_requires_review'] or linked_titles))
    work_score=max(work_scores,default=None)
    candidate=evidence['recording'];runtime=measure['recording.runtime_error']
    recording_conflicts=measure['metadata.critical_conflicts']
    # A title/author hit is a work assertion. Recording fields require agreement
    # with the local performance and a close measured runtime.
    matched=bool(candidate and compatible_work(work_book,candidate) and measure['recording.score']>=.9 and runtime is not None and runtime<=.02 and not recording_conflicts and not measure['recording.ambiguous'] and measure['recording.narrator_agrees']!=0 and measure['recording.language_agrees']!=0)
    performance_records={'catalogue':{k:v for k,v in sources.from_catalogue(book).items() if k in AUDIO_FIELDS}}
    if matched:
        performance_records.update({name:{k:v for k,v in record.items() if k in AUDIO_FIELDS} for name,record in evidence['records'].items() if name in merge.AUDIOBOOK})
    elif 'audible_file' in evidence['records']:
        performance_records['audible_file']={k:v for k,v in evidence['records']['audible_file'].items() if k in AUDIO_FIELDS}
    records={name:dict(work_records.get(name,{}),**performance_records.get(name,{})) for name in work_records.keys()|performance_records.keys()}
    resolved=sources.resolve(book,records,field_sources={'narrators':(*merge.AUDIOBOOK,'catalogue')})
    performance_book=dict(book,title=(': '.join(x for x in (candidate.get('title'),candidate.get('subtitle')) if x) if matched else book['title']))
    performance_resolved=sources.resolve(performance_book,performance_records,field_sources={'narrators':(*merge.AUDIOBOOK,'catalogue')})
    performance_rows=scoped_rows(performance_book,performance_resolved,'recording')
    performance_rows.insert(0,dict(field='Title',value=performance_book['title'],source=candidate['source'] if matched else 'Local source tags / catalogue',note=''))
    for field,key in (('Release date','release_date'),('Language','language'),('Runtime','runtime_minutes'),('Abridgement','abridged')):
        value=(candidate or {}).get(key) if matched else None
        if key=='abridged' and value is not None:value='abridged' if value else 'unabridged'
        if key=='runtime_minutes' and value is not None:value=f'{value:g} min'
        performance_rows.append(dict(field=field,value=str(value) if value not in (None,'') else 'unknown',source=candidate['source'] if matched and value not in (None,'') else 'No supporting source',note='Audiobook release; separate from original book publication' if field=='Release date' else ''))
    for field in ('Narrator','Publisher'):
        if not any(r['field']==field for r in performance_rows):performance_rows.append(dict(field=field,value='unknown',source='No supporting source',note=''))

    if inferred:measure['identity.author_known']=1
    elif not contributors.authors(book.get('author','')):measure['identity.author_known']=0
    measure.update({'work.score':work_score,'work.conflicts':work_conflicts,'recording.conflicts':recording_conflicts,'recording.matched':int(matched),'metadata.work_families':len({family(s) for s in work_records if s!='catalogue'})})
    return dict(evidence,version=VERSION,records=records,resolved=resolved,recording=candidate if matched else None,recording_candidate=candidate,work=dict(records=work_records,resolved=work_resolved,rows=scoped_rows(book,work_resolved,'work'),score=work_score,families=len({family(s) for s in work_records if s!='catalogue'})),performance=dict(records=performance_records,rows=performance_rows,matched=matched,choices=evidence['choices'],selected_choice=evidence['selected_choice'],local=dict(title=book['title'],narrators=sources._names(book.get('narrator','')),language=identity['language'],abridged=identity['abridged'],duration_seconds=max(r['duration'] for r in inv['representations']),dates=sorted({str(f['probe']['tags']['date']) for f in inv['files'] if f['probe']['tags'].get('date')})),candidate=candidate,corroboration=evidence['corroboration']),analysis_metrics=measure),measure


def approved_identity(identity,book_metadata):
    from copy import deepcopy
    result=deepcopy(identity);work=book_metadata['work']['resolved']
    result['book'].update(title=work['title'],author=', '.join(work['authors']),recording_title=identity['book'].get('recording_title') or identity['book']['title'])
    return result


def refine_recording(identity,inv,collected,book_metadata):
    """A recording may refine the performance, never replace the approved work."""
    result,metrics=resolve(approved_identity(identity,book_metadata),inv,collected)
    result['work']=book_metadata['work']
    work=result['work']['records'];performance=result['performance']['records']
    result['records']={name:dict(work.get(name,{}),**performance.get(name,{})) for name in work.keys()|performance.keys()}
    result['resolved']=sources.resolve(approved_identity(identity,book_metadata)['book'],result['records'],field_sources={'narrators':(*merge.AUDIOBOOK,'catalogue')})
    metrics={k:v for k,v in metrics.items() if k.startswith('recording.') or k=='metadata.critical_conflicts'}
    result['analysis_metrics']=dict(book_metadata.get('analysis_metrics',{}),**metrics)
    return result,metrics
