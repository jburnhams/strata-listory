"""Book title assertions: distinguish missing subtitles from conflicting ones."""
import difflib
import re
from app import contributors,merge


def normal(value):
    text=re.sub(r'\bvol(?:ume)?\s+(?=\d+\b)','',merge.norm(value))
    return re.sub(r'\b(?:a|an|the|unabridged|abridged)\b','',text).strip()


def compact(value):return normal(value).replace(' ','')


def parts(title,subtitle=''):
    split=re.split(r'\s*:\s*',title or '',maxsplit=1)
    return split[0],split[1] if len(split)>1 else subtitle or ''


def similar(a,b):
    if _volume(a) and _volume(b) and _volume(a)!=_volume(b):return False
    if compact(a)==compact(b):return True
    if set(map(int,re.findall(r'\d+',a)))!=set(map(int,re.findall(r'\d+',b))):return False
    return difflib.SequenceMatcher(None,normal(a),normal(b)).ratio()>=.93


def _shape(title,subtitle,people):
    main,sub=parts(title,subtitle);removed=False
    for person in sorted(people,key=len,reverse=True):
        prefix=merge.norm(person);value=merge.norm(main)
        if prefix and (value==prefix or value.startswith(prefix+' ')):
            main=value[len(prefix):].strip();removed=True;break
    return main,sub,removed


def _volume(main):
    # Parse before normal() removes volume labels for spelling comparisons.
    value=merge.norm(main)
    if re.fullmatch(r'[ivxlcdm]+|\d+',value):return value
    found=re.search(r'\b(?:book|volume|vol|part)\s+(\d+|[ivxlcdm]+)\b',value)
    return found[1] if found else ''


def subtitle_groups(entries):
    """Explicit attached editions can supply alternate subtitles of one work."""
    groups=[];linked={}
    for subtitle,record in entries:
        if not normal(subtitle):continue
        group=next((g for g in groups if any(similar(subtitle,s) for s in g)),None)
        if group is None:group=[];groups.append(group)
        group.append(subtitle)
        if record.get('source')=='openlibrary' and re.fullmatch(r'/works/OL\d+W',record.get('id','')) and re.fullmatch(r'/books/OL\d+M',record.get('subtitle_edition','')):
            linked.setdefault(record['id'],[]).append(subtitle)
    for subtitles in linked.values():
        # A work catalogue may contain bad links: never use it to erase explicit
        # volume distinctions, including a volume present on only one side.
        for volume in {_volume(s) for s in subtitles}:
            variants=[s for s in subtitles if _volume(s)==volume]
            matching=[g for g in groups if any(s in g for s in variants)]
            if len(matching)>1:
                groups=[g for g in groups if not any(g is old for old in matching)]+[sum(matching,[])]
    return groups


def analyse(wanted,candidate,author='',candidates=()):
    def result(score,basis='',reason=''):return dict(score=score,basis=basis,reason=reason)
    authors=contributors.authors(author)
    people=list(dict.fromkeys(authors+contributors.authors(candidate.get('authors') or candidate.get('author',''))))
    wm,ws,wp=_shape(wanted,'',people);cm,cs,cp=_shape(candidate.get('title',''),candidate.get('subtitle',''),people)
    # Tags often concatenate a known main title and subtitle without a colon.
    if not ws and cs and compact(wm)==compact(cm+' '+cs):wm,ws=cm,cs
    if _volume(wm) and _volume(cm) and _volume(wm)!=_volume(cm):return result(0,reason='Different book or volume numbers')
    relevant=[];records=[]
    for other in candidates or [candidate]:
        names=contributors.authors(other.get('authors') or other.get('author',''))
        if authors and not any(contributors.same_author(a,b,True) for a in authors for b in names):continue
        om,os,op=_shape(other.get('title',''),other.get('subtitle',''),list(dict.fromkeys(authors+names)))
        relevant.append((om,os,op))
        records.append(other)
    if not ws:
        for om,os,_ in relevant:
            if os and compact(wm)==compact(om+' '+os):wm,ws=om,os;break
    if ws and not cs and compact(cm)==compact(wm+' '+ws):cm,cs=wm,ws
    same_main=bool(normal(wm)) and similar(wm,cm)
    # Recompute after discovering a subtitle concatenated into the local tag.
    subtitle_evidence=[(os,record) for (om,os,_),record in zip(relevant,records) if similar(wm,om)]
    groups=subtitle_groups(subtitle_evidence)
    def equivalent(a,b):return similar(a,b) or any(any(similar(a,s) for s in g) and any(similar(b,s) for s in g) for g in groups)
    linked_variants=any(any(not similar(g[0],s) for s in g[1:]) for g in groups)
    if same_main:
        if normal(ws) and normal(cs):
            if not equivalent(ws,cs):return result(0,reason=f'Same main title, different subtitle: {cs}')
            if not similar(ws,cs):return result(.96,'linked editions of the same original work')
            return result(1 if compact(wanted) in (compact(candidate.get('title','')),compact(candidate.get('title','')+' '+candidate.get('subtitle',''))) else .99,'full title and subtitle')
        if not normal(ws) and len(groups)>1:return result(0,reason='Multiple books share this main title with different subtitles')
        if normal(ws) and not normal(cs) and any(not any(equivalent(ws,s) for s in g) for g in groups):return result(0,reason='Available subtitles conflict with the requested subtitle')
        if not normal(ws) and not normal(cs):return result(1,'main title; linked editions of original work' if linked_variants else 'main title')
        return result(.96,'main title; linked editions of original work' if linked_variants else 'main title; subtitle omitted')
    # A title entered under its distinctive subtitle can identify an author-named
    # collection, but cannot erase a known IV/V or other volume disagreement.
    if (cp or _volume(cm)) and normal(cs) and similar(wanted,cs) or wp and normal(ws) and (similar(ws,cm) or normal(cs) and similar(ws,cs)):
        matches={compact(om) for om,os,op in relevant if normal(os) and (similar(wanted,os) or normal(ws) and similar(ws,os))}
        if len(matches)>1:return result(0,reason='This subtitle belongs to multiple distinct titles')
        return result(.96,'distinctive subtitle with author prefix')
    if normal(ws) or normal(cs):return result(0,reason='Main titles do not agree')
    if set(map(int,re.findall(r'\d+',wm)))!=set(map(int,re.findall(r'\d+',cm))):return result(0,reason='Different or missing title numbers')
    similarity=difflib.SequenceMatcher(None,compact(wm),compact(cm)).ratio()
    return result(similarity,'fuzzy main title' if similarity>=.85 else '',reason='' if similarity>=.85 else 'Main titles do not agree')
