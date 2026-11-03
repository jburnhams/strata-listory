"""Materialize independent work inventories without moving or rewriting source audio."""
from pathlib import Path
from app import catalogue,scan
from app.ingest import grouping,inventory,store


def split(run_id,inv,policy,online):
    books=catalogue.list_books();all_paths={f['path'] for f in inv['files']};children=[]
    if inv['errors']:raise ValueError('resolve unreadable source files before splitting this collection')
    beneath_by_path={}
    for path in all_paths:
        for parent in Path(path).parents:beneath_by_path.setdefault(str(parent),set()).add(path)
    candidates=[]
    for book in books:
        if book.get('path'):
            path=Path(book['path']).resolve();beneath=beneath_by_path.get(str(path),set())
            if str(path) in all_paths or beneath:candidates.append((book,path,beneath))
    previous={}
    for child in store.all_runs():
        if child['options'].get('collection_run')==run_id:
            saved=next((e['data'] for e in reversed(child['events']) if e['stage']=='inventory' and e['status']=='done'),{})
            if saved.get('group') and store.latest_id(child['book_id'])==child['id']:previous[saved['group']]=child
    for work in inv['work_groups']:
        paths=set(work['paths']);members=[];anchors=[]
        for book,path,beneath in candidates:
            anchor=bool(beneath and path!=Path(inv['root']) and len(beneath)>=2 and len(beneath & paths)/len(beneath)>=.8)
            if str(path) in paths or beneath and beneath<=paths or anchor:members.append(book)
            if anchor and not beneath<=paths:anchors.append(book['id'])
        if not members:
            path=work['paths'][0]
            catalogue.add_scanned([dict(path=path,title=work['title'],author=work['author'],narrator='',asin='',kind=scan.kind_of(work['title'],*(Path(p).name for p in paths)),file_count=len(paths),series_position=None)])
            book=next(b for b in catalogue.list_books() if b['path']==path);members=[book];books.append(book)
        if anchors:work=dict(work,anchor_members=anchors)
        primary=min(members,key=lambda b:b['id'])
        selected=inventory.scoped(inv,work,members);selected['primary_id']=primary['id']
        prior=previous.get(selected['group'])
        if prior:
            children.append(dict(run_id=prior['id'],book_id=prior['book_id'],title=work['title'],files=len(paths),existing=prior['state']=='split_existing'))
            continue
        descriptor=dict(version=grouping.VERSION,root=inv['root'],paths=work['paths'],scope_paths=sorted(all_paths),anchor_members=anchors,layout_overrides=inventory.layout_overrides(inv))
        catalogue.set_extra(primary['id'],'source_group',descriptor)
        catalogue.set_extra(primary['id'],'source_group_ref',dict(primary_id=primary['id']))
        for member in members:
            if member['id']!=primary['id']:catalogue.set_extra(member['id'],'source_group_ref',dict(primary_id=primary['id']))
        child=store.create(primary['id'],dict(policy=policy,online=online,book_signature=inventory.signature(primary),collection_run=run_id))
        store.link(child,[b['id'] for b in members])
        store.event(child,'inventory','done',{'inventory.files':len(selected['files']),'inventory.probe_errors':len(selected['errors'])},dict(selected,split_from=run_id))
        existing=bool(catalogue.get_plan(primary['id']) or (catalogue.get_processed(primary['id']) or {}).get('status')=='done' or primary['review']=='ignored')
        store.finish(child,'split_existing' if existing else 'queued')
        children.append(dict(run_id=child,book_id=primary['id'],title=work['title'],files=len(paths),existing=existing))
    store.event(run_id,'split','done',data=dict(children=children,reason='Distinct works identified from embedded tags and filenames; source audio unchanged'))
    store.finish(run_id,'split')
    return children
