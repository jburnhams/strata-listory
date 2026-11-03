"""Work-level source grouping from tags and filenames; folders are fallback clues."""
from collections import Counter
from pathlib import Path
import re
from app import contributors, matcher, merge, scan

VERSION=3
GENERIC={'','audio','audiobook','audiobooks','book','books','unknown','unknown album','untitled'}

DISC_LABEL=re.compile(r'\b(?:cd|disc|disk)\s*[._-]*\s*(\d+)(?:\s*(?:of|/)\s*(\d+))?\b',re.I)
NUMBERS={word:str(n) for n,word in enumerate(('zero','one','two','three','four','five','six','seven','eight','nine','ten','eleven','twelve'))}


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


def partition(files,root):
    hints=[];coded=coded_parts(f['path'] for f in files);layout=sequence_layout(files)
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
        author=tags.get('album_artist') or tags.get('artist') or ''
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
        if not sequence and len(sequences)>1 and filename and album and matcher.title_score(album,dict(title=filename,subtitle=''))<.85:
            title=filename;use_title=True;conflict=True
        if generic_album and not sequence:
            # A numbered book stem repeated across files is stronger than a
            # collection label. Bare chapter titles still use the folder clue.
            title=filename if filename and (repeated[merge.norm(filename)]>1 or path.suffix.lower() in scan.WHOLE_BOOK or not tags.get('track')) else ''
            title=title or (h['folder'] if h['file']['path'] in coded else title_hint(tags.get('title',''))) or h['folder'] or filename
            use_title=True;conflict=False
        author=h['author']
        if author and merge.norm(author) in merge.norm(title):author=''
        key=matcher.norm(title);who=contributors.name_key(author)
        matching=[g for g in groups if g['key']==key and (sequence and sequence['shared'] and g.get('shared_parent')==str(path.parent.parent) or not who or not g['author'] or any(contributors.same_author(a,b,True) for a in contributors.authors(author) for b in contributors.authors(g['author'])))]
        if len(matching)==1:group=matching[0]
        else:
            group=dict(key=key,title=title or path.stem,author=author,paths=[],use_title=use_title,conflicting_album=conflict);groups.append(group)
            if sequence and sequence['shared']:group['shared_parent']=str(path.parent.parent)
        if not group['author'] and author:group['author']=author
        if sequence:
            group.setdefault('grouping_notes',[])
            if sequence['reason'] not in group['grouping_notes']:group['grouping_notes'].append(sequence['reason'])
        group['use_title']=group['use_title'] or use_title;group['conflicting_album']=group['conflicting_album'] or conflict
        group['paths'].append(h['file']['path'])
    return [{k:g[k] for k in ('title','author','paths','use_title','conflicting_album','grouping_notes') if k in g} for g in groups]
