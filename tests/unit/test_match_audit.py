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


def test_popularity_features_compress_rating_counts_and_keep_missing_data_separate():
    import math
    book=dict(title='A Book',author='A Writer')
    little=candidate('openlibrary',title='A Book',ratings_count=10,rating=4)
    popular=candidate('openlibrary',title='A Book',ratings_count=1000000,rating=4.5)
    other=candidate('hardcover',title='A Book',ratings_count=100000000)
    a=A.features(book,little,[little,popular,other]);b=A.features(book,popular,[little,popular,other])
    assert a['ratings_count_log']==pytest.approx(math.log1p(10))
    assert a['ratings_popularity_relative']==pytest.approx(math.log1p(10)/math.log1p(1000000))
    assert b['ratings_popularity_relative']==1 and b['ratings_count_log']<14
    assert a['rating_average']==.8 and a['rating_known']==1 and a['ratings_count_known']==1
    missing=A.features(book,candidate('openlibrary',title='A Book'))
    zero=A.features(book,candidate('openlibrary',title='A Book',ratings_count=0,rating=0))
    assert missing['ratings_count_known']==0 and missing['rating_known']==0
    assert zero['ratings_count_known']==1 and zero['ratings_count_log']==0 and zero['rating_known']==1
    for value in (True,-1,float('nan'),float('inf'),10**500,'bad'):
        invalid=A.features(book,candidate('openlibrary',title='A Book',ratings_count=value,rating=value))
        assert invalid['ratings_count_known']==0 and invalid['rating_known']==0
        json.dumps(invalid,allow_nan=False)
    assert A.features(book,candidate('openlibrary',title='A Book',rating=6))['rating_known']==0


def test_edition_and_subtitle_features_record_conflicts_without_making_labels():
    c=candidate('openlibrary',id='/works/OL1W',title='Great Expectations',subtitle='Annotated Biography',authors=['Charles Dickens'],original_year=1950)
    c['subtitle_edition']='/books/OL1M'
    edition=dict(work_key='/works/OL1W',key='/books/OL1M',year=2020,publishers=['A Publisher'],isbns=['9780307387899'])
    f=A.features(dict(title='Great Expectations',author='Charles Dickens'),c,[c],[edition])
    assert f['candidate_biography']==1 and f['biography_added']==1 and f['annotation_added']==1
    assert f['candidate_year_known']==1 and f['edition_year_known']==1 and f['edition_work_year_gap']==pytest.approx(70/300)
    assert f['edition_evidence_known']==1 and f['edition_isbn_known']==1 and f['edition_publisher_known']==1
    spoof=A.features(dict(title='Great Expectations'),c,[c],[dict(edition,work_key='/works/OL2W')])
    assert spoof['edition_evidence_known']==0 and spoof['edition_work_year_comparable']==0
    original=A.features(dict(title='A Memoir Annotated',author='Writer'),candidate('openlibrary',title='A Memoir',subtitle='Annotated',original_year='unknown'))
    assert original['local_biography']==1 and original['biography_added']==0 and original['annotation_added']==0
    assert original['candidate_year_known']==0
    adapted=A.features(dict(title='A Novel'),candidate('openlibrary',title='A Novel',subtitle='A dramatization'))
    assert adapted['adaptation_added']==1
