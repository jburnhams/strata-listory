"""Identity evidence must fail closed on convincing but contradictory matches."""
import json
import sqlite3
from pathlib import Path

import pytest

from app import catalogue, db, evidence


def records():
    r = dict(title='Dune', authors=['Frank Herbert'], narrators=['Simon Vance'], asin='B000000001', runtime_minutes=60, abridged=False)
    return dict(audible=dict(r), audnexus=dict(r), audiobookdb=dict(r), openlibrary=dict(title='Dune', authors=['Frank Herbert'], original_year=1965))


def files():
    return [dict(path='01.mp3', duration=3600, tags=dict(album='Dune', album_artist='Frank Herbert'))]


def test_candidate_never_approves_processing():
    r = evidence.assess(records(), files())
    assert r['assessment'] == 'candidate'
    assert r['work_identity'] == r['recording_identity'] == 'supported'
    assert r['eligible_for_auto_processing'] is False


@pytest.mark.parametrize('field,value', [('title', 'Dune Messiah'), ('authors', ['Brian Herbert']), ('narrators', ['George Guidall']), ('asin', 'B000000002'), ('abridged', True), ('runtime_minutes', 90)])
def test_material_disagreement_blocks(field, value):
    recs = records(); recs['audiobookdb'][field] = value
    r = evidence.assess(recs, files())
    assert r['assessment'] == 'review'
    assert r['blockers']


def test_audible_mirrors_are_not_independent():
    recs = records(); del recs['audiobookdb']
    r = evidence.assess(recs, files())
    assert any('two source families' in x for x in r['blockers'])


@pytest.mark.parametrize('mutation', ['empty', 'partial', 'wrong_album', 'no_work', 'no_abridgement', 'wrong_catalogue'])
def test_missing_or_local_conflicting_evidence_blocks(mutation):
    recs = records(); fs = files(); errors = []
    if mutation == 'empty': fs = []
    if mutation == 'partial': errors = ['02.mp3 unreadable']
    if mutation == 'wrong_album': fs[0]['tags']['album'] = 'Another book'
    if mutation == 'no_work': recs.pop('openlibrary')
    if mutation == 'no_abridgement':
        for r in recs.values(): r.pop('abridged', None)
    if mutation == 'wrong_catalogue': recs['catalogue'] = dict(title='Dune Messiah')
    assert evidence.assess(recs, fs, errors)['assessment'] == 'review'


def test_collect_preserves_all_probe_and_sidecar_fields(tmp_path, monkeypatch):
    root = tmp_path / 'Dune'; root.mkdir(); (root / '01.mp3').write_bytes(b'audio')
    raw = dict(title='Dune', authors=[dict(name='Frank Herbert')], asin='B000000001', is_abridged=False, unknown_field=['keep me'])
    (root / 'book.metadata.json').write_text(json.dumps(raw))
    probe = dict(format=dict(duration='3600', tags=dict(album='Dune', custom='keep')), streams=[dict(codec_type='audio', codec_name='mp3')], unknown='keep')
    monkeypatch.setattr(evidence.details, '_ffprobe', lambda p: probe)
    b = dict(id=1, title='Dune', author='Frank Herbert', path=str(root), locked=[])
    out = evidence.collect(b, {})
    assert next(e['raw'] for e in out['evidence'] if e['source'] == 'audible_file') == raw
    assert next(e['raw'] for e in out['evidence'] if e['source'] == 'ffprobe') == probe
    assert out['records']['audible_file']['abridged'] is False
    assert out['recording']['abridged']['value'] is False
    assert out['measured_duration_seconds'] == 3600


def test_report_uses_read_only_database_and_no_normal_connection(monkeypatch):
    b = catalogue.add_book(title='Dune', author='Frank Herbert')
    path = db.path()
    monkeypatch.setattr(db, 'connect', lambda: pytest.fail('must not open writable connection'))
    out = evidence.report([b['id']])
    assert out['summary'] == dict(total=1, candidates=0, review=1)
    assert out['books'][0]['evidence'][0]['raw']['title'] == 'Dune'
    connection = sqlite3.connect(path)
    try: assert connection.execute('SELECT count(*) FROM books').fetchone()[0] == 1
    finally: connection.close()
    with pytest.raises(ValueError, match='Unknown'): evidence.report([999])
    with pytest.raises(ValueError, match='positive'): evidence.report(limit=0)


def test_cli_emits_report_and_reports_bad_arguments(monkeypatch, capsys):
    from app import cli
    monkeypatch.setattr(evidence, 'report', lambda ids, limit, stride: dict(ids=ids, limit=limit, stride=stride))
    assert cli.main(['evidence-report', '3', '--limit', '2', '--stride', '4']) == 0
    assert json.loads(capsys.readouterr().out) == dict(ids=[3], limit=2, stride=4)
    def failed(*args): raise ValueError('limit and stride must be positive')
    monkeypatch.setattr(evidence, 'report', failed)
    with pytest.raises(SystemExit, match='positive'): cli.main(['evidence-report', '--limit', '0'])


@pytest.mark.parametrize('outcome', ['linked', 'not_found', 'ambiguous', 'unavailable', 'work_conflict', 'invalid_work_id'])
def test_fresh_openlibrary_archives_before_selection(tmp_path, monkeypatch, outcome):
    from app.providers.base import MatchError
    docs = [dict(key='/works/OL123W', title='Dune', author_name=['Frank Herbert'], first_publish_year=1965, unknown='preserve')]
    if outcome == 'not_found': docs[0]['author_name'] = ['Brian Herbert']
    if outcome == 'ambiguous': docs.append(dict(docs[0], key='/works/OL124W'))
    if outcome == 'invalid_work_id': docs[0]['key'] = '//elsewhere.example/work'
    replies = [dict(docs=docs), dict(title='Different' if outcome == 'work_conflict' else 'Dune', description=dict(value='A desert planet'), subjects=['Science fiction']), dict(summary=dict(average=4.2, count=150))]
    calls = []
    def request(url):
        calls.append(url)
        if outcome == 'unavailable': raise MatchError('offline')
        return replies[len(calls) - 1]
    monkeypatch.setattr(evidence.OPENLIBRARY_HTTP, 'request', request)
    recs = {}; entries = []; book = dict(id=1, title='Dune', author='Frank Herbert')
    result = evidence.fetch_openlibrary(book, recs, entries, tmp_path)
    assert result['status'] == outcome
    assert len(list(tmp_path.glob('*.json'))) == len(calls)
    assert all(json.loads(Path(e['archive']).read_text())['url'] == e['url'] for e in entries)
    if outcome == 'linked':
        assert recs['openlibrary']['original_year'] == 1965
        assert recs['openlibrary']['description'] == 'A desert planet'
        assert recs['openlibrary']['rating'] == 4.2
        assert recs['openlibrary']['ratings_count'] == 150
        assert entries[0]['raw']['docs'][0]['unknown'] == 'preserve'
    else: assert 'openlibrary' not in recs


def test_fresh_lookup_refuses_conflicting_recordings(tmp_path):
    recs = records(); recs['audible']['title'] = 'Another work'
    assert evidence.fetch_openlibrary(dict(id=1), recs, [], tmp_path)['status'] == 'ambiguous_recording'
    assert not list(tmp_path.iterdir())


def test_fresh_report_never_opens_writable_catalogue(monkeypatch, tmp_path):
    b = catalogue.add_book(title='Dune', author='Frank Herbert')
    from app import cli
    monkeypatch.setattr(db, 'connect', lambda: pytest.fail('writable database opened'))
    monkeypatch.setattr(evidence.OPENLIBRARY_HTTP, 'request', lambda url: dict(docs=[]))
    r = evidence.report([b['id']], fresh_openlibrary=True)
    assert r['mode'] == 'fresh-openlibrary'
    assert r['books'][0]['openlibrary_lookup']['status'] == 'not_found'
    assert r['automatic_processing_enabled'] is False


def test_repeat_lookups_never_replace_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence.OPENLIBRARY_HTTP, 'request', lambda url: dict(docs=[]))
    b = dict(id=1, title='Dune', author='Frank Herbert')
    evidence.fetch_openlibrary(b, {}, [], tmp_path)
    first = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    evidence.fetch_openlibrary(b, {}, [], tmp_path)
    assert len(list(tmp_path.iterdir())) == 2
    assert all((tmp_path / name).read_bytes() == body for name, body in first.items())


def test_fetched_work_author_mismatch_does_not_link(tmp_path, monkeypatch):
    replies = iter([dict(docs=[dict(key='/works/OL123W',title='Dune',author_name=['Frank Herbert'],author_key=['OL1A'])]), dict(key='/works/OL123W',title='Dune',authors=[dict(author=dict(key='/authors/OL2A'))])])
    monkeypatch.setattr(evidence.OPENLIBRARY_HTTP, 'request', lambda url: next(replies))
    recs = {}
    r = evidence.fetch_openlibrary(dict(id=1,title='Dune',author='Frank Herbert'), recs, [], tmp_path)
    assert r['status'] == 'work_conflict'
    assert recs == {}


def test_fresh_lookup_missing_author_makes_no_request(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence.OPENLIBRARY_HTTP, 'request', lambda url: pytest.fail('cannot query without an author'))
    r = evidence.fetch_openlibrary(dict(id=1, title='Dune'), {}, [], tmp_path)
    assert r['status'] == 'missing_identity'
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('details', [None, dict(title='Dune', key='/works/OL999W')])
def test_missing_or_changed_work_is_not_attached(tmp_path, monkeypatch, details):
    replies = iter([dict(docs=[dict(key='OL123W', title='Dune', author_name=['Frank Herbert'])]), details])
    monkeypatch.setattr(evidence.OPENLIBRARY_HTTP, 'request', lambda url: next(replies))
    recs = {}
    r = evidence.fetch_openlibrary(dict(id=1, title='Dune', author='Frank Herbert'), recs, [], tmp_path)
    assert r['status'] == 'work_conflict'
    assert recs == {}
    assert len(list(tmp_path.iterdir())) == 2


def test_cli_fresh_flag_is_forwarded(monkeypatch, capsys):
    from app import cli
    def report(ids, limit, stride, fresh_openlibrary=False): return dict(fresh=fresh_openlibrary)
    monkeypatch.setattr(evidence, 'report', report)
    assert cli.main(['evidence-report', '--fresh-openlibrary']) == 0
    assert json.loads(capsys.readouterr().out) == dict(fresh=True)
