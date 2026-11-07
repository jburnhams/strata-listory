import json
import sqlite3
from app.identify import audible, audibleindex as ai, context, features as ft


def index(tmp_path):
    c = sqlite3.connect(tmp_path / 'a.sqlite'); c.execute('CREATE TABLE product (asin TEXT, region TEXT, raw BLOB)')
    c.execute('INSERT INTO product VALUES (?,?,?)', ('B000000001', 'us', json.dumps(dict(asin='B000000001', title='Census', authors=[dict(name='Jesse Ball')], narrators=[dict(name='Some Reader')], runtime_length_min=300, language='english', format_type='unabridged', isbn='9780141039596', series=[dict(title='S')]))))
    c.commit(); c.close(); ai.build(tmp_path / 'a.sqlite', tmp_path / 'i.sqlite'); return ai.connect(tmp_path / 'i.sqlite')


def test_lookup_features_and_reading(tmp_path):
    c = index(tmp_path)
    # a title alone still finds the recording, but carries no corroboration and steers nothing
    bare = audible.lookup(dict(title='Census', author='', narrator='', runtime_min=None), c)
    assert bare['grade'] == 'weak' and audible.reading(dict(title='Census'), bare) is None and audible.features(bare)['audible_author_unknown'] == 1 and audible.features(bare)['audible_strong'] == 0
    # one-word title, no author, but narrator and runtime agree: corroborated, so the audiobook's author drives the book search
    census = audible.lookup(dict(title='Census', author='', narrator='Some Reader', runtime_min=301), c)
    r = audible.reading(dict(title='Census', author=''), census)
    assert r['reading'] == 'audible' and r['author'] == 'Jesse Ball' and r['isbns'] == ['9780141039596']
    # a conflicting author or runtime is never corroborated
    assert audible.reading(dict(title='Census'), audible.lookup(dict(title='Census', author='Someone Else', narrator='Some Reader', runtime_min=301), c)) is None
    assert audible.reading(dict(title='Census'), audible.lookup(dict(title='Census', author='', narrator='Some Reader', runtime_min=900), c)) is None
    hit = audible.lookup(dict(title='Census', author='Jesse Ball', narrator='Some Reader', runtime_min=301, language='english'), c)
    assert hit['grade'] == 'strong'
    f = audible.features(hit, dict(language='English'))
    assert f['audible_strong'] == 1 and f['audible_author_match'] == 1 and f['audible_narrator_match'] == 1 and f['audible_runtime_match'] == 1 and f['audible_has_isbn'] == 1 and f['audible_language_match'] == 1 and f['audible_series_known'] == 1
    assert set(audible.features(None)) == set(audible.FEATURES) and not any(audible.features(None).values()) and not audible.corroborated(None)


def test_no_index_means_no_hit_and_features_are_in_the_model(tmp_path, monkeypatch):
    audible.reset(); monkeypatch.setattr(ai, 'connect', lambda *a, **k: None)
    assert audible.lookup(dict(title='x', author='y')) is None
    audible.reset()
    assert 'audible_found' in ft.feature_names() and 'reading_audible' in ft.feature_names()


def test_context_facts():
    assert context.facts(dict(language='english'), dict(representations=[dict(duration=3600), dict(duration=7200)])) == dict(runtime_min=120.0, language='english')
    assert context.facts(None, None) == dict(runtime_min=None, language='')
