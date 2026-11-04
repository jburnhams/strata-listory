"""Trusted approval labels, frozen evidence and independent work-group validation."""
from copy import deepcopy
import json
import sys
from types import SimpleNamespace
import numpy as np
import pytest
from app.ingest import match_model as M
from app.providers.base import candidate


def run(automatic=True):
    accepted=candidate('openlibrary',id='/works/OL1W',title='History',authors=['Alice Writer'],url='https://openlibrary.org/works/OL1W')
    alternative=candidate('hardcover',id='2',title='History',subtitle='Ancient World',authors=['Alice Writer'])
    wrong=candidate('hardcover',id='3',title='History',authors=['Bob Writer'])
    data=[('identity',dict(book=dict(title='History',author='Alice Writer'))),('inventory',dict(members=[dict(id=1)])),
          ('providers',dict(providers=dict(openlibrary=dict(candidates=[accepted]),hardcover=dict(candidates=[alternative,wrong])))),
          ('metadata',dict(work=dict(records={'openlibrary':dict(title='History',authors=['Alice Writer'],url=accepted['url'])},resolved=dict(title='History',authors=['Alice Writer'])))),
          ('metadata_approval',dict(automatic=automatic)),
          ('providers',dict(providers=dict(openlibrary=dict(candidates=[dict(accepted,title='Future metadata')]))))]
    return dict(id='run1',book_id=1,events=[dict(stage=s,status='done',data=d) for s,d in data])


@pytest.mark.parametrize('automatic',[True,False])
def test_trusted_approvals_label_selected_work_and_keep_uncertain_editions_unknown(automatic):
    rows=M.trusted_rows(run(automatic));by_id={r['candidate']['id']:r for r in rows}
    assert by_id['/works/OL1W']['label']==1
    assert by_id['/works/OL1W']['label_origin']==('automatic_approval' if automatic else 'manual_approval')
    assert by_id['2']['label'] is None
    assert by_id['3']['label']==0 and by_id['3']['label_origin']=='contradicts_approved_identity'
    assert all(r['candidate']['title']!='Future metadata' for r in rows)
    assert M.trusted_rows(dict(run(),events=[]))==[]
    missing=run();missing['events'][3]['data']['work']['resolved']={}
    assert M.trusted_rows(missing)==[]


def test_label_fallback_uses_frozen_selected_record_without_inventing_missing_positives():
    r=run();r['events'][3]['data']['work']['records']['openlibrary'].pop('url')
    assert next(x for x in M.trusted_rows(r) if x['candidate']['id']=='/works/OL1W')['label']==1
    r['events'][3]['data']['work']['records']={}
    assert M.trusted_rows(r)==[]


def test_related_editions_and_volume_subtitles_are_not_invented_negative_labels():
    target=dict(title='History: Ancient World',author='Alice Writer')
    assert not M.distinct_work(target,candidate('hardcover',title='History: Ancient Civilisations',authors=['Alice Writer']))
    assert not M.distinct_work(target,candidate('hardcover',title='Histories',authors=['Alice Writer']))
    assert M.distinct_work(target,candidate('hardcover',title='History',authors=['Bob Writer']))
    assert M.distinct_work(target,candidate('hardcover',title='The Moon and the Sea',authors=['Alice Writer']))


def row(rid,bid,cid,title='History',author='Alice Writer',label=1):
    return dict(run_id=rid,book_id=bid,member_ids=[],candidate_key=cid,candidate=candidate('openlibrary',id=cid,title=title,authors=[author]),target=dict(title=title,author=author),local_book=dict(title=title,author=author),features=dict(a=.99 if label else .01,supporting_families=2),label=label,label_origin='automatic_approval' if label else 'contradicts_approved_identity')


def test_work_groups_keep_shared_ids_members_and_canonical_duplicates_together():
    a=row('a',1,'id1');b=row('b',2,'id1',title='Translated Title');c=row('c',3,'id2',title='Translated Title');d=row('d',4,'id3',title='Other Story');d['member_ids']=[3]
    result=M.assign_groups([a,b,c,d]);assert len({r['work_group'] for r in result})==1
    assert len({r['split'] for r in result})==1
    unrelated=row('e',5,'id5',title='A Different Story')
    result=M.assign_groups([*deepcopy(result),unrelated]);assert result[-1]['work_group']!=result[0]['work_group']


def test_cross_work_negatives_use_nearest_observed_works_inside_the_same_split():
    rows=[row('a',1,'id1'),row('b',2,'id2',title='Ancient History',author='Bob Writer'),row('c',3,'id3',title='Hidden History',author='Carol Writer')]
    M.assign_groups(rows)
    for r in rows:r['split']='train'
    rows[2]['split']='test'
    result=M.add_cross_work_negatives(rows,per_query=1);negatives=result[len(rows):]
    assert len(negatives)==2
    assert all(r['label']==0 and r['split']=='train' and r['foreign_group']!=r['work_group'] for r in negatives)
    assert all(r['features']['supporting_families']==2 for r in negatives)


def test_calibration_threshold_rejects_false_positives_and_requires_group_support():
    rows=[dict(label=1,work_group=str(n),label_origin='automatic_approval') for n in range(12)]
    rows.append(dict(label=0,work_group='bad',label_origin='different_approved_work'))
    scores=[.99]*12+[.8]
    threshold=M.choose_threshold(rows,scores)
    assert .8<threshold<=.99
    result=M.metrics(rows,scores,threshold)
    assert result['false_positives']==0 and result['accepted_groups']==12 and result['precision']==1
    assert M.choose_threshold(rows,[.9]*13)>1
    assert M.choose_threshold(rows[:3],scores[:3])>1
    assert M.metrics([],[],1)['precision'] is None


def test_experiment_selects_without_test_data_and_writes_reproducible_artifacts(monkeypatch,tmp_path):
    from app.ingest import match_audit as A
    database=tmp_path/'source.db'
    # Exercise the real read-only loader and preserve source bytes.
    import sqlite3
    with sqlite3.connect(database) as c:
        c.executescript('CREATE TABLE ingest_runs(id,book_id,created,state); CREATE TABLE ingest_members(run_id,book_id); CREATE TABLE ingest_events(run_id,stage,status,data,id);')
        c.execute("INSERT INTO ingest_runs VALUES ('saved',1,'2026','metadata_review')")
        c.execute("INSERT INTO ingest_events VALUES ('saved','identity','done','{}',1)")
    before=database.read_bytes();assert M.load_runs(database)[0]['events'][0]['data']=={}
    labelled=[row(str(n),n,str(n),title=f'Unique work {n}') for n in range(200)]
    labelled+= [dict(r,candidate_key='bad:'+r['candidate_key'],label=0,label_origin='contradicts_approved_identity',candidate=dict(r['candidate'],authors=['Bob Writer']),features=dict(a=.01,supporting_families=2)) for r in labelled]
    M.assign_groups(labelled)
    calls=[];prefer_tree=[False]
    class Model:
        def __init__(self,tree=False,**kw):
            self.tree=tree;self.mean_=np.zeros(2);self.scale_=np.ones(2);self.coef_=np.ones((1,2));self.intercept_=np.zeros(1);self.feature_importances_=np.array([.8,.2]);self.steps=[('scale',self),('reg',self)]
        def fit(self,x,y,**kw):assert len(x)==len(y);calls.append(('fit',self.tree,len(x)));return self
        def predict_proba(self,x):
            calls.append(('predict',self.tree,len(x)));score=x[:,0] if self.tree==prefer_tree[0] else np.full(len(x),.9)
            return np.column_stack((1-score,score))
        def save_model(self,path):path.write_text('model')
    monkeypatch.setitem(sys.modules,'sklearn',SimpleNamespace(__version__='test'))
    monkeypatch.setitem(sys.modules,'sklearn.linear_model',SimpleNamespace(LogisticRegression=Model))
    monkeypatch.setitem(sys.modules,'sklearn.pipeline',SimpleNamespace(make_pipeline=lambda *args:args[-1]))
    monkeypatch.setitem(sys.modules,'sklearn.preprocessing',SimpleNamespace(StandardScaler=Model))
    monkeypatch.setitem(sys.modules,'xgboost',SimpleNamespace(__version__='test',XGBClassifier=lambda **kw:Model(tree=True)))
    monkeypatch.setattr(M,'load_runs',lambda db:[dict(id='trusted',events=[dict(stage='metadata_approval',status='done',data={})]),dict(id='pending',events=[]),dict(id='finished',state='processed',events=[]),dict(id='radio',events=[dict(stage='identity',status='done',data=dict(kind='radio'))]),dict(id='empty',events=[])])
    monkeypatch.setattr(M,'trusted_rows',lambda r:deepcopy(labelled) if r['id']=='trusted' else [])
    monkeypatch.setattr(M,'add_cross_work_negatives',lambda rows:rows)
    pending=dict(row('pending',1000,'p'),features=dict(a=.99,supporting_families=2))
    monkeypatch.setattr(A,'candidate_rows',lambda r:[] if r['id']=='empty' else [pending])
    result=M.experiment(database,tmp_path/'result')
    assert result['selected_model'].startswith('logistic') and result['models'][result['selected_model']]['test']['false_positives']==0
    assert result['pending_above_threshold']==1 and not result['live_approval_enabled']
    assert all('test' not in report for name,report in result['models'].items() if name!=result['selected_model'])
    assert database.read_bytes()==before
    assert json.loads((tmp_path/'result'/'model.json').read_text())['features']==['a','supporting_families']
    assert (tmp_path/'result'/'dataset.jsonl').is_file() and (tmp_path/'result'/'pending.jsonl').is_file()
    prefer_tree[0]=True
    cached=tmp_path/'result'/'dataset.jsonl'
    uncertain=dict(labelled[0],label=0,label_origin='contradicts_approved_identity')
    with cached.open('a') as stream:stream.write(json.dumps(uncertain)+'\n')
    tree_result=M.experiment(database,tmp_path/'tree',dataset=cached)
    assert tree_result['selected_model'].startswith('xgboost')
    assert (tmp_path/'tree'/'model.ubj').read_text()=='model'
    assert tree_result['unknown_candidates']==1
    actual_experiment=M.experiment
    monkeypatch.setattr(M,'experiment',lambda database,output,dataset:dict(selected_model='test',dataset=dataset))
    monkeypatch.setattr(sys,'argv',['match_model',str(database),'--out',str(tmp_path/'cli'),'--dataset',str(cached)])
    M.main()
    monkeypatch.setattr(M,'experiment',actual_experiment)
    with pytest.raises(FileExistsError):M.experiment(database,tmp_path/'result')
    monkeypatch.setattr(M,'trusted_rows',lambda r:[])
    with pytest.raises(ValueError,match='positive and negative'):M.experiment(database,tmp_path/'empty')
