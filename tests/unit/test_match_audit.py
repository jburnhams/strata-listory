"""Candidate features preserve negative evidence and never manufacture labels."""
import json
import sqlite3
import pytest
from app.ingest import match_audit as A
from app.providers.base import candidate


def test_edit_distance_and_features_keep_missingness_and_conflicts():
    assert A.edit_similarity('kitten','sitting')==pytest.approx(4/7)
    assert A.edit_similarity('','')==0
    book=dict(title='History Volume 1',author='Alice Writer')
    other=candidate('hardcover',title='History Volume 2',authors=['Bob Writer'])
    f=A.features(book,other,[other])
    assert f['title_numbers_agree']==0 and f['title_rule_conflict']==1 and f['compatible']==0
    assert f['author_exact']==0 and f['candidate_author_known']==1
    f=A.features(dict(title='History',author=''),candidate('openlibrary',title='History'))
    assert f['local_author_known']==0 and f['candidate_author_known']==0 and f['author_overlap']==0


def test_export_rows_do_not_treat_approval_or_rejected_candidates_as_labels():
    c=candidate('hardcover',id='1',title='History',authors=['Alice Writer'])
    approval=dict(automatic=True,scope='book')
    run=dict(id='run1',book_id=1,events=[dict(stage=s,status='done',data=d) for s,d in (
        ('identity',dict(book=dict(title='History',author='Alice Writer'))),
        ('providers',dict(providers=dict(hardcover=dict(candidates=[c,c]),openlibrary=dict(candidates=[dict(c,source='openlibrary',id='2')])))),
        ('metadata_approval',approval))])
    rows=A.candidate_rows(run)
    assert len(rows)==2 and all(r['features']['supporting_families']==2 for r in rows)
    assert all(r['label'] is None and r['work_group'] is None and r['approval']==approval for r in rows)
    assert len({r['candidate_key'] for r in rows})==2
    assert A.candidate_rows(dict(run,events=[]))==[]


def test_readonly_export_never_changes_database_and_refuses_overwrite(tmp_path):
    database=tmp_path/'catalogue.db';output=tmp_path/'candidates.jsonl'
    with sqlite3.connect(database) as c:
        c.executescript('CREATE TABLE ingest_runs(id,book_id,created,state); CREATE TABLE ingest_members(run_id,book_id); CREATE TABLE ingest_events(run_id,stage,status,data,id);')
        c.execute("INSERT INTO ingest_runs VALUES ('run1',1,'2026','metadata_review')")
        identity=dict(book=dict(title='History',author='Writer'))
        providers=dict(providers=dict(hardcover=dict(candidates=[candidate('hardcover',id='1',title='History',authors=['Writer'])])))
        c.executemany("INSERT INTO ingest_events VALUES ('run1',?,'done',?,?)",[(s,json.dumps(d),i) for i,(s,d) in enumerate((('identity',identity),('providers',providers)))])
    before=database.read_bytes()
    assert A.export(database,output)==dict(unapproved=1,candidates=1,labels=0)
    assert json.loads(output.read_text())['label'] is None and database.read_bytes()==before
    with pytest.raises(FileExistsError):A.export(database,output)


def test_zero_errors_is_not_a_guarantee_of_zero_false_positives():
    assert A.precision_bound(0,0)==0
    assert A.precision_bound(100,100)==pytest.approx(.9704869504)
    assert A.precision_bound(2995,2995)>=.999
    with pytest.raises(ValueError):A.precision_bound(99,100)
    with pytest.raises(ValueError):A.precision_bound(1,1,confidence=1)
