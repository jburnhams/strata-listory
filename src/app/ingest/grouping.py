"""Work-level source grouping from tags and filenames; folders are fallback clues."""
from collections import Counter
from pathlib import Path
import re
from app import contributors, matcher, merge, scan

VERSION=6
GENERIC={'','audio','audiobook','audiobooks','book','books','unknown','unknown album','untitled'}

DISC_LABEL=re.compile(r'\b(?:cd|disc|disk)\s*[._-]*\s*(\d+)(?:\s*(?:of|/)\s*(\d+))?\b',re.I)
NUMBERS={word:str(n) for n,word in enumerate(('zero','one','two','three','four','five','six','seven','eight','nine','ten','eleven','twelve'))}


def author_credit(tags):
    """A labelled reader/auxiliary credit is not a work-author assertion."""
    return next((value for key in ('album_artist','artist') if (value:=tags.get(key)) and contributors.authors(value)),'')


def album_parts(value):
    """Disc labels are packaging; retain the distinct numbered volume identity."""
    disc=DISC_LABEL.search(value or '')
    text=DISC_LABEL.sub('',value or '').strip(' []() ._-') if not disc else (value or '')[:disc.start()].strip(' []() ._-')
    text=re.sub(r'\b(?:vol(?:ume)?)[.\s_-]*(\d+|'+'|'.join(NUMBERS)+r')\b',lambda m:' '+NUMBERS.get(m[1].lower(),str(int(m[1])) if m[1].isdigit() else m[1]),text,flags=re.I)
    text=re.sub(r'\b0+(\d+)\b',r'\1',text)
    text=re.sub(r'\s+',' ',text).strip()
    return (text if merge.norm(text) not in GENERIC else '',int(disc[1]) if disc else 0,int(disc[2]) if disc and disc[2] else None)


def sequence_layout(files):
    """Recognise ordered chapter runs before treating story names as book titles.

    Every directory/format/album/disc run must have unique numbered tracks.
    A reset within a folder, unrelated album or whole-book file is not a run.
    """
    runs={};result={}
    for f in files:
        path=Path(f['path']);tags=f['probe']['tags'];hint=f.get('grouping_hint') or {}
        title,disc,total=album_parts(hint.get('album') or tags.get('album',''))
        tagged=re.match(r'(\d+)(?:/(\d+))?',str(tags.get('disc') or tags.get('discnumber') or ''))
        if tagged:disc,total=int(tagged[1]),int(tagged[2]) if tagged[2] else total
        folder=DISC_LABEL.search(path.parent.name)
        if not disc and folder:disc=int(folder[1])
        disc=hint.get('disc') or disc
        track=re.match(r'\s*(\d+)(?:/(\d+))?',str(tags.get('track') or tags.get('tracknumber') or ''))
        leading=re.match(r'^(\d{1,3})[ ._-]+',path.stem)
        number=int(track[1]) if track else int(leading[1]) if leading else None
        key=(str(path.parent),path.suffix.lower(),matcher.norm(title),disc)
        runs.setdefault(key,[]).append((f,title,number,total))
    coherent=[]
    for key,items in runs.items():
        title=items[0][1];numbers=[n for _,_,n,_ in items]
        if not title or merge.norm(title) in {'collection','mixed collection','unknown collection'} or re.search(r'\b(?:collected works|complete works|audiobooks)\b',title,re.I):continue
        if key[1] in scan.WHOLE_BOOK or len(items)<2 and not key[3] or any(n is None for n in numbers) or len(set(numbers))!=len(numbers):continue
        if min(numbers)!=1 and not key[3] and not any('/' in str(f['probe']['tags'].get('track','')) for f,_,_,_ in items):continue
        # Two tracks need an explicit total or disc; larger ordered runs can
        # establish the structure without a rigid directory naming convention.
        if len(items)==2 and not key[3] and not any('/' in str(f['probe']['tags'].get('track','')) for f,_,_,_ in items):continue
        coherent.append((key,items))
    for key,items in coherent:
        directory=Path(key[0]);title=items[0][1]
        related=[(k,vs) for k,vs in coherent if k[2]==key[2] and (Path(k[0]).parent==directory.parent or k[0]==key[0])]
        # Disc labels or a matching numbered sibling series establish ordering;
        # unrelated copy folders remain separate representations.
        folders={k[0]:re.fullmatch(r'(.+?)\s+(\d+)',Path(k[0]).name) for k,_ in related}
        base=re.sub(r'\s+\d+$','',matcher.norm(title))
        ordered=len(folders)>1 and all(m and matcher.norm(m[1])==base for m in folders.values())
        shared=any(k[3] for k,_ in related) or ordered
        offsets={k[3]-int(folders[k[0]][2]) for k,_ in related if k[3] and ordered}
        offset=next(iter(offsets)) if len(offsets)==1 else 1-min(int(m[2]) for m in folders.values()) if ordered and not offsets else None
        disc=key[3] or (int(folders[key[0]][2])+offset if ordered and offset is not None else 0)
        for f,_,_,total in items:
            result[f['path']]=dict(title=title,key=key[2],disc=disc,total=total,shared=shared,folder=key[0],reason=(f.get('grouping_hint') or {}).get('reason','Album identity and ordered track sequence'))
    return result


def title_hint(value):
    text=re.sub(r'\s*\((?:un)?abridged\)\s*',' ',value or '',flags=re.I)
    text=re.sub(r'^(?:\d{1,3}[ ._-]+)','',text)
    text=re.sub(r'\s*[-_ ]*(?:chapter|chap|part|track|disc|cd|episode|ep)[ _-]*\d+(?:\s*(?:of|/)\s*\d+)?\s*$','',text,flags=re.I)
    text=re.sub(r'\b(?:19|20)\d\d[-.]\d\d[-.]\d\d\b','',text)
    text=re.sub(r'\s+-\s*\d{1,3}\s*$','',text)
    text=re.sub(r'[_]+',' ',text).strip(' ._-,')
    if merge.norm(text) in GENERIC or re.fullmatch(r'(?:\d{1,3}(?:/\d+)?|0+\d+|(?:chapter|chap|ch|part|track|disc|cd|unabridged|abridged)\s*\d+(?:/\d+)?)',text,re.I):return ''
    return text


def folder_hint(path,root):
    parent=Path(path).parent;root=Path(root)
    while parent!=parent.parent and (scan.DISC.search(parent.name) or merge.norm(parent.name) in GENERIC):parent=parent.parent
    return title_hint(scan.parse_name(parent.name)['title'])


CODED=re.compile(r'^([A-Za-z]{2,12})[._ -]?(\d{2,5})[._ -]+.+$')


def coded_parts(paths):
    families={};result={}
    for value in paths:
        path=Path(value);code_match=CODED.match(path.stem)
        if code_match:families.setdefault((str(path.parent),path.suffix.lower(),code_match[1].upper()),[]).append((str(value),int(code_match[2])))
    for key,items in families.items():
        numbers=sorted({n for _,n in items});positive=[n for n in numbers if n>0]
        if len(numbers)<3 or len(numbers)!=len(items) or not positive:continue
        differences=[b-a for a,b in zip(positive,positive[1:])]
        if not differences or sum(n in (1,2) for n in differences)/len(differences)<.5:continue
        start=min(positive);intro=int(0 in numbers)
        for path,n in items:result[path]=dict(prefix=key[2],number=1 if n==0 else n-start+1+intro)
    return result


def filename_parts(paths):
    """Candidate filename sequences; bare suffixes require byte-split proof later."""
    families={};result={}
    for value in paths:
        path=Path(value);stem=path.stem;kind='';total=None
        if m:=re.fullmatch(r'(.+?)\s+(\d{1,4})\s+of\s+(\d{1,4})',stem,re.I):
            base=m[1];number=int(m[2]);total=int(m[3]);kind='total'
            if not 1<=number<=total or total<2:continue
            if re.search(r'\b(?:book|vol(?:ume)?|series|season|episode|ep)\s*$',base,re.I):continue
            base=re.sub(r'\s+(?:chapter|chap|part|track)\s*$','',base,flags=re.I)
        elif m:=re.fullmatch(r'(\d+)\s*\((\d{1,4})\)',stem):base=m[1];number=int(m[2]);kind='counter'
        elif m:=re.fullmatch(r'(.+?)\s+(\d{1,3})',stem):
            base=m[1];number=int(m[2]);kind='bytes'
            if re.search(r'\b(?:book|vol(?:ume)?|part|disc|cd|series|season|episode|ep)\s*$',base,re.I):continue
        else:continue
        key=(str(path.parent),path.suffix.lower(),kind,matcher.norm(base))
        families.setdefault(key,[]).append((str(value),base,number,total))
    for key,items in families.items():
        numbers=[v[2] for v in items];totals={v[3] for v in items};kind=key[2]
        if kind=='counter' and key[1] in scan.WHOLE_BOOK:continue
        if len(set(numbers))!=len(numbers) or len(totals)!=1:continue
        if len(items)<(2 if kind=='total' else 3 if kind=='counter' else 4) or kind!='total' and min(numbers)!=1:continue
        folder=title_hint(scan.parse_name(Path(key[0]).name)['title'])
        if kind=='counter':
            if not folder or re.search(r'\b(?:collection|collected works|complete works)\b',folder,re.I):continue
            title=folder.replace('.',' ');author=''
        else:
            parsed=scan.parse_name(items[0][1]);title=title_hint(parsed['title']);author=parsed['author']
            if not title or kind=='bytes' and (key[1]!='.mp3' or matcher.norm(folder)!=matcher.norm(title)):continue
        for path,_,number,total in items:result[path]=dict(key='|'.join(key),kind=kind,title=title,author=author,number=number,total=total,reason='Ordered filename parts of one work')
    return result


def filename_layout(files):
    """Accept explicit part runs, numeric copy counters, and fixed-size MP3 chunks."""
    candidates=filename_parts(f['path'] for f in files);families={};result={}
    for f in files:
        if f['path'] in candidates:families.setdefault(candidates[f['path']]['key'],[]).append(f)
    for items in families.values():
        info=candidates[items[0]['path']];title=info['title']
        if any((album:=title_hint(f['probe']['tags'].get('album',''))) and matcher.norm(album)!=matcher.norm(title) for f in items):continue
        hashes=[f['sha256'] for f in items if f.get('sha256')]
        if info['kind']=='counter' and len(set(hashes))!=len(hashes):continue
        if info['kind']=='bytes':
            ordered=sorted(items,key=lambda f:candidates[f['path']]['number']);sizes=[f['size'] for f in ordered];middle=sizes[1:-1]
            # Series volumes do not become chapters merely because names end in
            # 1,2,3. Equal byte chunks plus a shorter tail establish this layout.
            if len(set(middle))!=1 or middle[0]<4096 or not .98*middle[0]<=sizes[0]<=middle[0] or not 0<sizes[-1]<middle[0]:continue
        for f in items:result[f['path']]=dict(candidates[f['path']])
    return result


def anthology_layout(files,root,layout):
    """A named, ordered album inside its matching book folder can contain many authors.

    Require at least three tracks and a complete unique sequence. Generic collection
    labels, competing album identities, numbering resets and whole-book files are
    intentionally not evidence of a single anthology.
    """
    runs={};result={}
    for f in files:
        if sequence:=layout.get(f['path']):runs.setdefault((sequence['folder'],sequence['key']),[]).append(f)
    for (directory,key),items in runs.items():
        siblings=[f for f in files if str(Path(f['path']).parent)==directory]
        if len(items)<3 or len(items)!=len(siblings):continue
        numbers=[]
        for f in items:
            tags=f['probe']['tags']
            match=re.match(r'\s*(\d+)',str(tags.get('track') or tags.get('tracknumber') or Path(f['path']).name))
            if match:numbers.append(int(match[1]))
        if sorted(numbers)!=list(range(1,len(items)+1)):continue
        folder=scan.parse_name(Path(directory).name)
        if matcher.norm(title_hint(folder['title']))!=key:continue
        people={contributors.name_key(author_credit(f['probe']['tags'])) for f in items if author_credit(f['probe']['tags'])}
        if len(people)<2:continue
        editor=re.search(r'^(.*?)\s*\((?:ed\.?|eds\.?|editor|edited by)\)\s*$',folder.get('author') or '',re.I)
        credit=editor[1].strip() if editor else ''
        if credit.count(',')==1:
            surname,given=credit.split(',',1);credit=given.strip()+' '+surname.strip()
        for f in items:result[f['path']]=dict(author=credit,reason='Shared album, matching container and complete ordered anthology contents')
    return result


def partition(files,root):
    hints=[];coded=coded_parts(f['path'] for f in files);layout=sequence_layout(files);named=filename_layout(files)
    anthologies=anthology_layout(files,root,layout)
    albums={}
    for f in files:
        album=title_hint(f['probe']['tags'].get('album',''))
        if album:albums.setdefault(str(Path(f['path']).parent),set()).add(album)
    prefixes={}
    for path,code in coded.items():prefixes.setdefault(str(Path(path).parent),set()).add(code['prefix'])
    for f in files:
        path=Path(f['path']);tags=f['probe']['tags'];album=title_hint(tags.get('album',''))
        filename='' if re.match(r'^(?:chapter|chap|ch|track)\b',path.stem,re.I) else title_hint(path.stem)
        fallback=folder_hint(path,root)
        if f['path'] in coded:
            anchors=albums.get(str(path.parent),set())
            filename=''
            fallback=coded[f['path']]['prefix'] if len(prefixes[str(path.parent)])>1 else next(iter(anchors)) if len(anchors)==1 else fallback
        author=author_credit(tags)
        hints.append(dict(file=f,album=album,filename=filename,folder=fallback,author=author))
    repeated=Counter(merge.norm(h['filename']) for h in hints if h['filename'])
    sequences={name for name,count in repeated.items() if name and count>1}
    groups=[]
    for h in hints:
        album=h['album'];filename=h['filename'];path=Path(h['file']['path']);tags=h['file']['probe']['tags']
        generic_album=not album or bool(re.search(r'\b(?:collection|collected works|complete works|audiobooks)\b',album,re.I))
        title=album;use_title=False;conflict=False
        sequence=layout.get(h['file']['path'])
        if sequence:title=sequence['title'];use_title=True
        part=named.get(h['file']['path'])
        if part:title=part['title'];use_title=True
        if not sequence and not part and len(sequences)>1 and filename and album and matcher.title_score(album,dict(title=filename,subtitle=''))<.85:
            title=filename;use_title=True;conflict=True
        if generic_album and not sequence and not part:
            # A numbered book stem repeated across files is stronger than a
            # collection label. Bare chapter titles still use the folder clue.
            title=filename if filename and (repeated[merge.norm(filename)]>1 or path.suffix.lower() in scan.WHOLE_BOOK or not tags.get('track')) else ''
            title=title or (h['folder'] if h['file']['path'] in coded else title_hint(tags.get('title',''))) or h['folder'] or filename
            # A descriptive/sample file can accompany a numeric chapter run.
            # Its parsed work title must match that run; it remains a separate
            # representation because it has no sequence number.
            parsed=scan.parse_name(path.name)['title']
            related=[p for value,p in named.items() if p['kind']=='counter' and Path(value).parent==path.parent and matcher.norm(p['title'])==matcher.norm(parsed)]
            if related:title=related[0]['title']
            use_title=True;conflict=False
        anthology=anthologies.get(h['file']['path'])
        author=anthology['author'] if anthology else h['author'] or (part or {}).get('author','')
        if author and merge.norm(author) in merge.norm(title):author=''
        key=matcher.norm(title);who=contributors.name_key(author)
        matching=[g for g in groups if g['key']==key and (sequence and sequence['shared'] and g.get('shared_parent')==str(path.parent.parent) or not who or not g['author'] or any(contributors.same_author(a,b,True) for a in contributors.authors(author) for b in contributors.authors(g['author'])))]
        if len(matching)==1:group=matching[0]
        else:
            group=dict(key=key,title=title or path.stem,author=author,paths=[],use_title=use_title,conflicting_album=conflict);groups.append(group)
            if sequence and sequence['shared']:group['shared_parent']=str(path.parent.parent)
        if anthology:
            group['anthology']=True
            group.setdefault('grouping_notes',[])
            if anthology['reason'] not in group['grouping_notes']:group['grouping_notes'].append(anthology['reason'])
        if not group['author'] and author:group['author']=author
        if sequence:
            group.setdefault('grouping_notes',[])
            if sequence['reason'] not in group['grouping_notes']:group['grouping_notes'].append(sequence['reason'])
        if part:
            group.setdefault('grouping_notes',[])
            if part['reason'] not in group['grouping_notes']:group['grouping_notes'].append(part['reason'])
        group['use_title']=group['use_title'] or use_title;group['conflicting_album']=group['conflicting_album'] or conflict
        group['paths'].append(h['file']['path'])
    return [{k:g[k] for k in ('title','author','paths','use_title','conflicting_album','grouping_notes','anthology') if k in g} for g in groups]
