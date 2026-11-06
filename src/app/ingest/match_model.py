"""Train offline work-match classifiers from trusted existing approvals.

python -m app.ingest.match_model DATA/catalogue.db --out NEW_DIRECTORY
Research dependencies: pip install -r requirements-model.txt
"""
from collections import Counter
from contextlib import closing
import difflib
import hashlib
import json
from pathlib import Path
import sqlite3
from app import book_titles, contributors, sources
from app.ingest import match_audit as audit, metadata

# Rule outputs are observations for the model, not vetoes or target labels.
RULE_FEATURES=set()
LEGACY_FEATURES={'attached_edition','author_edit','author_exact','author_overlap','author_score','candidate_author_count','candidate_author_known','candidate_subtitle_known','exact_asin','local_author_count','local_author_known','local_subtitle_known','supporting_families','title_edit','title_exact','title_length_ratio','title_numbers_agree','title_sequence','title_token_jaccard'}
VERSION=2


def stages(run):return {e['stage']:e['data'] for e in run['events'] if e['status']=='done'}


def trusted_rows(run,candidate_loader=None):
    """Freeze features and the chosen identity at consent, before later refreshes."""
    index=next((i for i in range(len(run['events'])-1,-1,-1) if run['events'][i]['stage']=='metadata_approval' and run['events'][i]['status']=='done'),None)
    if index is None or any(e['status']=='done' and (e['stage']=='metadata_revocation' or e['stage']=='match_review_decision' and e['data'].get('action')=='reject') for e in run['events'][index+1:]):return []
    frozen=dict(run,events=run['events'][:index+1]);data=stages(frozen)
    approved=data.get('metadata') or {};work=approved.get('work') or {}
    records=work.get('records') or approved.get('records') or {}
    resolved=work.get('resolved') or approved.get('resolved') or {}
    target=dict(title=resolved.get('title',''),author=', '.join(resolved.get('authors') or []))
    if not target['title'] or not target['author']:return []
    rows=(candidate_loader or audit.candidate_rows)(frozen);roles=contributors.parse((data.get('identity') or {}).get('book',{}).get('author',''))
    for row in rows:
        c=row['candidate'];record=records.get(c['source']) or {};people=contributors.authors(c.get('authors') or c.get('author',''),roles)
        converted=metadata.primary_credits(target,sources.from_candidate(c,roles))
        credit=lambda names:{contributors.name_key(n) for n in names}
        url=c.get('url');saved_url=record.get('url')
        same_url=bool(url and saved_url and url.rstrip('/')==saved_url.rstrip('/'))
        exact_record=bool(record and book_titles.compact(c['title'])==book_titles.compact(record.get('title','')) and credit(converted.get('authors',[]))==credit(record.get('authors',[])) and credit(record.get('authors',[])))
        if same_url or exact_record:
            row.update(label=1,label_origin='automatic_approval' if data['metadata_approval'].get('automatic') else 'manual_approval')
        elif distinct_work(target,c):row.update(label=0,label_origin='contradicts_approved_identity')
        row.update(target=target,member_ids=[m['id'] for m in (data.get('inventory') or {}).get('members',[])],local_book=(data.get('identity') or {}).get('book',{}))
    return rows if any(r['label']==1 for r in rows) else []


def distinct_work(book,candidate):
    """Different known authors or clearly different main titles, not subtitle variants."""
    people=contributors.authors(book.get('author',''));other=contributors.authors(candidate.get('authors') or candidate.get('author',''))
    if people and other and not any(contributors.same_author(a,b,True) for a in people for b in other):return True
    main=book_titles.parts(metadata.work_title(book.get('title','')))[0];found=book_titles.parts(metadata.work_title(candidate.get('title','')))[0]
    # A missing subtitle, edition label or spelling variant is not a negative.
    return bool(main and found and not book_titles.similar(main,found) and difflib.SequenceMatcher(None,book_titles.compact(main),book_titles.compact(found)).ratio()<.6)


def assign_groups(rows):
    """Union queries sharing a member, an accepted work ID, or canonical identity."""
    parents={r['run_id']:r['run_id'] for r in rows};tokens={}
    def root(key):
        while parents[key]!=key:parents[key]=parents[parents[key]];key=parents[key]
        return key
    for row in rows:
        target=row['target'];c=row['candidate'];keys=[('member',bid) for bid in [row['book_id'],*row.get('member_ids',[])]]
        keys.append(('identity',book_titles.compact(metadata.work_title(target['title'])),tuple(sorted(contributors.name_key(n) for n in contributors.authors(target['author'])))))
        if row['label']==1 and c.get('id'):keys.append(('work',c['source'],c['id']))
        for key in keys:
            if key in tokens:
                a,b=root(row['run_id']),root(tokens[key]);parents[max(a,b)]=min(a,b)
            else:tokens[key]=row['run_id']
    members={}
    for row in rows:members.setdefault(root(row['run_id']),set()).add(row['run_id'])
    for row in rows:
        group=hashlib.sha256('|'.join(sorted(members[root(row['run_id'])])).encode()).hexdigest()
        row['work_group']=group;bucket=int(group[:8],16)%5
        row['split']='test' if bucket==0 else 'calibration' if bucket==1 else 'train'
    return rows


def add_cross_work_negatives(rows,per_query=3,query_ids=None):
    """Mine nearest real wrong works within each split; never cross holdout boundaries."""
    positives={};queries={}
    for row in rows:
        queries.setdefault(row['run_id'],row)
        if row['label']==1:positives.setdefault((row['split'],row['work_group'],row['candidate_key']),row)
    negatives=[];cohorts={}
    for row in rows:cohorts.setdefault(row['run_id'],[]).append(row['candidate'])
    for query in queries.values():
        if query_ids is not None and query['run_id'] not in query_ids:continue
        options=[r for r in positives.values() if r['split']==query['split'] and r['work_group']!=query['work_group'] and distinct_work(query['target'],r['candidate'])]
        wanted=contributors.authors(query['target']['author'])
        def difficulty(row):
            same_author=any(contributors.same_author(a,b,True) for a in wanted for b in contributors.authors(row['candidate'].get('authors') or row['candidate'].get('author','')))
            return same_author,difflib.SequenceMatcher(None,book_titles.compact(query['local_book']['title']),book_titles.compact(row['candidate']['title'])).ratio()
        options.sort(key=difficulty,reverse=True)
        seen=set()
        for other in options:
            key=other['work_group']
            if key in seen:continue
            seen.add(key)
            negatives.append(dict(query,candidate=other['candidate'],candidate_key='cross:'+other['candidate_key'],label=0,label_origin='different_approved_work',foreign_group=other['work_group'],foreign_run_id=other['run_id'],edition_evidence=other.get('edition_evidence',[]),features=dict(audit.features(query['local_book'],other['candidate'],cohorts[query['run_id']]+[other['candidate']],other.get('edition_evidence',[])),supporting_families=other['features']['supporting_families'],source_margin=0)))
            if len(seen)>=per_query:break
    return rows+negatives


def load_runs(database):
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row;c.execute('BEGIN')
        headers=c.execute('''SELECT r.* FROM ingest_runs r WHERE NOT EXISTS (
            SELECT 1 FROM ingest_members mine JOIN ingest_members later ON later.book_id=mine.book_id
            JOIN ingest_runs newer ON newer.id=later.run_id WHERE mine.run_id=r.id AND newer.created>r.created) ORDER BY r.id''').fetchall()
        return [dict(r,events=[dict(e,data=json.loads(e['data'])) for e in c.execute('SELECT * FROM ingest_events WHERE run_id=? ORDER BY id',(r['id'],))]) for r in headers]


def metrics(rows,scores,threshold):
    accepted=[(r,s) for r,s in zip(rows,scores) if s>=threshold]
    tp=sum(r['label']==1 for r,s in accepted);fp=sum(r['label']==0 for r,s in accepted);positives=sum(r['label']==1 for r in rows)
    groups={r['work_group'] for r,s in accepted};bad={r['work_group'] for r,s in accepted if r['label']==0}
    return dict(threshold=threshold,accepted=len(accepted),true_positives=tp,false_positives=fp,precision=tp/(tp+fp) if tp+fp else None,recall=tp/positives if positives else None,accepted_groups=len(groups),false_positive_groups=len(bad),zero_error_precision_lower_95=audit.precision_bound(len(groups),len(groups)) if groups and not bad else None,origins=dict(Counter(r['label_origin'] for r,s in accepted)))


def choose_threshold(rows,scores,minimum_groups=10):
    choices=[metrics(rows,scores,t) for t in (.5,.7,.8,.9,.95,.98,.99,.995,.999)]
    valid=[c for c in choices if not c['false_positives'] and c['accepted_groups']>=minimum_groups and c['true_positives']]
    return max(valid,key=lambda c:(c['accepted_groups'],c['true_positives'],c['threshold']))['threshold'] if valid else 1.000001


def refresh_features(rows):
    """Add missing feature columns from the retained inputs without rebuilding old ones."""
    from app.ingest.training_cache import backfill
    backfill(rows)
    return rows


def experiment(database,output,dataset=None,feature_set='expanded',cache=None):
    """No approval writes or network calls; optional ML dependencies stay offline."""
    import numpy as np
    import sklearn
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    import xgboost
    from xgboost import XGBClassifier
    output=Path(output);output.mkdir(exist_ok=False,parents=True)
    from app.ingest.training_cache import FeatureCache,input_key
    evidence_cache=FeatureCache(cache or Path(database).parent/'training'/'features.sqlite')
    runs=load_runs(database)
    if dataset:
        original=[json.loads(line) for line in Path(dataset).read_text().splitlines() if line.strip()]
        original=[r for r in original if r.get('label_origin')!='different_approved_work']
        for row in original:
            if row['label']==0 and not distinct_work(row['target'],row['candidate']):row.update(label=None,label_origin='unresolved_alternative')
    else:original=[row for run in runs for row in evidence_cache.trusted(run)]
    if dataset:original=refresh_features(original)
    rows=evidence_cache.negatives(assign_groups(original),add_cross_work_negatives)
    with (output/'dataset.jsonl').open('w') as stream:
        for row in rows:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    labelled=[r for r in rows if r['label'] in (0,1)];names=sorted((set(labelled[0]['features']) & LEGACY_FEATURES) if feature_set=='legacy' else set(labelled[0]['features'])-RULE_FEATURES) if labelled else []
    if feature_set=='evidence':names=[name for name in names if name not in audit.RATING_FEATURES]
    splits={s:[r for r in labelled if r['split']==s] for s in ('train','calibration','test')}
    if any({r['label'] for r in part}!={0,1} for part in splits.values()):raise ValueError('each work-group split needs positive and negative labels')
    matrix=lambda part:np.array([[r['features'][f] for f in names] for r in part])
    train=splits['train'];counts=Counter((r['work_group'],r['label']) for r in train)
    weights=np.array([1/counts[r['work_group'],r['label']] for r in train]);x=matrix(train);y=np.array([r['label'] for r in train]);cal=matrix(splits['calibration']);test=matrix(splits['test'])
    models={f'logistic_C{c}':make_pipeline(StandardScaler(),LogisticRegression(C=c,max_iter=2000,random_state=42)) for c in (.1,1,10)}
    models.update({f'xgboost_depth{depth}':XGBClassifier(n_estimators=150,max_depth=depth,learning_rate=.04,subsample=.9,colsample_bytree=.9,reg_lambda=5,min_child_weight=2,n_jobs=2,tree_method='hist',eval_metric='logloss',random_state=42) for depth in (2,3,4)})
    evaluations={};fitted={}
    for name,model in models.items():
        model.fit(x,y,**({'logisticregression__sample_weight':weights} if name.startswith('logistic') else {'sample_weight':weights}))
        scores=model.predict_proba(cal)[:,1];threshold=choose_threshold(splits['calibration'],scores)
        evaluations[name]=dict(calibration=metrics(splits['calibration'],scores,threshold));fitted[name]=(model,threshold)
    # Model and threshold selection never inspect final test predictions.
    best=max(evaluations,key=lambda n:(evaluations[n]['calibration']['accepted_groups'] if not evaluations[n]['calibration']['false_positives'] else -1,evaluations[n]['calibration']['true_positives'],n.startswith('logistic')))
    model,threshold=fitted[best];evaluations[best]['test']=metrics(splits['test'],model.predict_proba(test)[:,1],threshold)
    report=dict(version=VERSION,feature_set=feature_set,feature_version=audit.VERSION,teacher='user-trusted existing automatic and manual book approvals',features=names,excluded_rule_features=sorted(RULE_FEATURES),split_summary={s:dict(rows=len(part),groups=len({r['work_group'] for r in part}),labels=dict(Counter(r['label'] for r in part))) for s,part in splits.items()},label_origins=dict(Counter(r['label_origin'] for r in labelled)),unknown_candidates=sum(r['label'] is None for r in rows),selected_model=best,models=evaluations,versions=dict(numpy=np.__version__,sklearn=sklearn.__version__,xgboost=xgboost.__version__),threshold=threshold,live_approval_enabled=False)
    if best.startswith('logistic'):
        scaler,reg=model.steps[0][1],model.steps[1][1];artifact=dict(features=names,mean=scaler.mean_.tolist(),scale=scaler.scale_.tolist(),coefficients=reg.coef_[0].tolist(),intercept=float(reg.intercept_[0]),threshold=threshold)
        (output/'model.json').write_text(json.dumps(artifact,indent=2));report['feature_importance']=dict(sorted(zip(names,reg.coef_[0].tolist()),key=lambda item:abs(item[1]),reverse=True))
    else:
        model.save_model(output/'model.ubj');report['feature_importance']=dict(sorted(zip(names,model.feature_importances_.tolist()),key=lambda item:item[1],reverse=True))
    predictions=[]
    for run in runs:
        if run.get('state','metadata_review') not in ('metadata_review','review','eligible'):continue
        if (stages(run).get('identity') or {}).get('kind','book')!='book':continue
        if any(e['stage']=='metadata_approval' and e['status']=='done' for e in run['events']):continue
        candidates=evidence_cache.candidates(run)
        for row in candidates:row['input_key']=input_key(run)
        if not candidates:continue
        scores=model.predict_proba(matrix(candidates))[:,1]
        for row,score in zip(candidates,scores):row.update(model_score=float(score),above_threshold=bool(score>=threshold))
        candidates.sort(key=lambda r:r['model_score'],reverse=True);predictions.extend(candidates)
    with (output/'pending.jsonl').open('w') as stream:
        for row in predictions:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    report.update(pending_candidates=len(predictions),pending_above_threshold=sum(r['above_threshold'] for r in predictions),pending_queries=len({r['run_id'] for r in predictions}),pending_queries_above_threshold=len({r['run_id'] for r in predictions if r['above_threshold']}))
    (output/'report.json').write_text(json.dumps(report,indent=2));return report


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('database');parser.add_argument('--out',required=True);parser.add_argument('--dataset',help='reuse a previous frozen feature dataset');parser.add_argument('--cache',help='persistent training evidence database');parser.add_argument('--feature-set',choices=('expanded','evidence','legacy'),default='expanded');args=parser.parse_args()
    print(json.dumps(experiment(args.database,args.out,args.dataset,feature_set=args.feature_set,cache=args.cache),indent=2))


if __name__=='__main__':main()
