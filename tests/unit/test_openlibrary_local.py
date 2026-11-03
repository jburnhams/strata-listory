"""Offline dumps publish atomically and preserve work identity and rating provenance."""
import gzip
import json
from pathlib import Path

import pytest

from app.providers import openlibrary_local as local


def dump(path,rows):
    with gzip.open(path,'wt',encoding='utf8') as stream:
        for key,data in rows:stream.write(f'/type/record\t{key}\t1\t2026-10-31\t{json.dumps(data)}\n')


@pytest.fixture
def dumps(tmp_path):
    dump(tmp_path/'authors.txt.gz',[
        ('/authors/OL1A',dict(name='Cormac McCarthy',alternate_names=['Charles McCarthy'])),
        ('/authors/OL2A',dict(name='Other Writer')),
    ])
    dump(tmp_path/'works.txt.gz',[
        ('/works/OL1W',dict(title='The Road',authors=[dict(author=dict(key='/authors/OL1A'))],first_publish_date='September 26, 2006',subjects=['Fiction'],covers=[-1,123])),
        ('/works/OL2W',dict(title='The Road',authors=[dict(author=dict(key='/authors/OL2A'))])),
        ('/works/OL3W',dict(title='Blood Méridian',authors=[dict(author=dict(key='/authors/OL1A'))])),
    ])
    with gzip.open(tmp_path/'ratings.txt.gz','wt') as stream:
        stream.write('/works/OL1W\t\\N\t5\t2026-01-01\n/works/OL1W\t/books/OL1M\t3\t2026-02-01\n/works/OL2W\t\\N\t1\t2026-03-01\ninvalid\n/works/OL1W\t\\N\t8\t2026-04-01\n')
    return tmp_path


def test_offline_search_uses_author_identity_full_text_and_aggregated_work_ratings(dumps):
    assert local.status()['stage']=='not_installed' and local.search('The Road')==[]
    state=local.build(dumps)
    assert state['ready'] and state['counts']==dict(authors=2,works=3,ratings=3)
    [c]=local.search('The Road','Cormac McCarthy')
    assert (c['id'],c['author'],c['original_year'],c['rating'],c['ratings_count'])==('/works/OL1W','Cormac McCarthy',2006,4,2)
    assert c['cover_url']=='https://covers.openlibrary.org/b/id/123-L.jpg' and c['genres']==['Fiction']
    assert local.search('Road','Cormac McCarthy')[0]['id']=='/works/OL1W'
    assert local.search('The Road','Other Writer')[0]['id']=='/works/OL2W'
    assert local.search('Blood Meridian','Cormac McCarthy')[0]['original_year'] is None
    assert local.search('" OR * --')==[] and local.search('')==[]


@pytest.mark.year_lookup
def test_offline_provider_is_primary_and_online_remains_a_fallback(dumps,fake_urlopen):
    from app.providers.openlibrary import OpenLibrary
    local.build(dumps);p=OpenLibrary()
    assert p.search('The Road','Cormac McCarthy')[0]['rating']==4 and not fake_urlopen.calls
    fake_urlopen.reply(dict(docs=[]))
    assert p.search('Missing title','Writer')==[] and len(fake_urlopen.calls)==1


def test_failed_import_keeps_the_previous_searchable_index(dumps):
    local.build(dumps);previous=(local.folder()/'index.sqlite').stat().st_size
    (dumps/'works.txt.gz').write_bytes(b'broken gzip')
    with pytest.raises(OSError):local.build(dumps)
    assert local.status()['stage']=='failed' and local.status()['ready']
    assert (local.folder()/'index.sqlite').stat().st_size==previous and local.search('The Road','Cormac McCarthy')
    with pytest.raises(ValueError,match='missing Open Library dump'):local.build(dumps/'absent')


def test_offline_cli_import_and_status(dumps,capsys):
    from app.cli import main
    assert main(['openlibrary-import','--directory',str(dumps)])==0
    assert json.loads(capsys.readouterr().out)['ready']
    assert main(['openlibrary-status'])==0
    assert json.loads(capsys.readouterr().out)['counts']['ratings']==3


def test_common_surname_uses_full_author_name_before_bounded_fallback(dumps):
    people=[(f'/authors/OL{i}A',dict(name=f'Person{i} King')) for i in range(3,1105)]
    people += [('/authors/OL9999A',dict(name='Stephen King'))]
    with gzip.open(dumps/'authors.txt.gz','at') as stream:
        for key,data in people:stream.write(f'/type/author\t{key}\t1\t2026-10-31\t{json.dumps(data)}\n')
    with gzip.open(dumps/'works.txt.gz','at') as stream:
        stream.write('/type/work\t/works/OL9999W\t1\t2026-10-31\t'+json.dumps(dict(title='The Shining',authors=[dict(author=dict(key='/authors/OL9999A'))]))+'\n')
    local.build(dumps)
    assert local.search('The Shining','Stephen King')[0]['author']=='Stephen King'


def identity_dumps(dumps):
    dump(dumps/'authors.txt.gz',[(f'/authors/OL{i}A',dict(name=name)) for i,name in enumerate(['Philip Rucker','Carol Leonnig','Philip Rucker Carol Leonnig','Danny Dreyer','Chuck Klosterman'],1)])
    rows=[('/works/OL1W',dict(title='A Very Stable Genius',authors=[dict(author=dict(key='/authors/OL1A')),dict(author=dict(key='/authors/OL2A'))])),('/works/OL2W',dict(title='ChiRunning',authors=[dict(author=dict(key='/authors/OL4A'))]))]
    # Numerous author-prefixed essays must not push the actual numbered volume
    # out of the bounded candidate list when the tags contain only its subtitle.
    rows += [(f'/works/OL{i+10}W',dict(title='Chuck Klosterman on '+name,authors=[dict(author=dict(key='/authors/OL5A'))])) for i,name in enumerate(['Curious People','Dangerous Ideas','Pop Culture','Media','Living','Rock','Film'])]
    rows += [('/works/OL99W',dict(title='Chuck Klosterman IV',authors=[dict(author=dict(key='/authors/OL5A'))]))]
    dump(dumps/'works.txt.gz',rows);local.build(dumps)


def test_local_discovery_handles_multiple_authors_subtitles_and_joined_title_words(dumps):
    identity_dumps(dumps)
    assert local.search("A Very Stable Genius: Donald J. Trump's Testing of America",'Philip Rucker, Carol Leonnig')[0]['id']=='/works/OL1W'
    assert local.search('Chi Running','Danny Dreyer')[0]['id']=='/works/OL2W'
    assert local.search('A Decade of Curious People and Dangerous Ideas','Chuck Klosterman')[0]['id']=='/works/OL99W'


@pytest.mark.year_lookup
def test_subtitle_only_search_uses_attached_editions_and_caches_the_evidence(dumps,monkeypatch):
    from app.providers.openlibrary import OpenLibrary
    from app.providers.base import candidate
    identity_dumps(dumps);p=OpenLibrary();calls=[]
    monkeypatch.setattr(local,'search',lambda *a:[candidate('openlibrary',id='/works/OL99W',title='Chuck Klosterman IV',authors=['Chuck Klosterman'])])
    def request(url):
        calls.append(url)
        if '/editions.json' in url:return dict(entries=[dict(key='/books/OL1M',title='Chuck Klosterman IV',subtitle='Wrong work',works=[dict(key='/works/OL100W')]),dict(key='/books/OL2M',title='Chuck Klosterman IV',subtitle='A Decade of Curious People and Dangerous Ideas',works=[dict(key='/works/OL99W')])])
        return dict(title='Chuck Klosterman IV')
    monkeypatch.setattr(p.http,'request',request)
    [c]=p.search('A Decade of Curious People and Dangerous Ideas','Chuck Klosterman')
    assert (c['id'],c['subtitle_edition'])==('/works/OL99W','/books/OL2M')
    assert p.search('A Decade of Curious People and Dangerous Ideas','Chuck Klosterman')==[c] and len(calls)==2
