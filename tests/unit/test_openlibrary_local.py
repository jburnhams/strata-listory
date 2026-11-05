"""Offline dumps publish atomically and preserve work identity and rating provenance."""
import gzip
import json
import sqlite3
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


def test_editions_import_retains_isbn_publisher_dates_and_work_links_without_inventing_original_years(dumps):
    dump(dumps/'editions.txt.gz',[
        ('/books/OL1M',dict(title='The Road',works=[dict(key='/works/OL1W')],isbn_10=['0307387895'],isbn_13=['978-0-307-38789-9'],publish_date='March 2007',publishers=['Vintage'],languages=[dict(key='/languages/eng')],physical_format='Paperback')),
        ('/books/OL2M',dict(title='The Road',works=[dict(key='/works/OL1W')],publish_date='2006',publishers=['Original Publisher'],languages=[dict(key='/languages/eng')],physical_format='Hardcover')),
        ('/books/OL3M',dict(title='The Road',works=[dict(key='/works/OL1W')],publish_date='2005',publishers=['Audio Publisher'],physical_format='Audio CD')),
        ('/books/OL4M',dict(title='Blood Meridian',works=[dict(key='/works/OL3W')],isbn_13=['9780679728757'],publish_date='1992',publishers=['Reprint Publisher'])),
        ('/books/OL5M',dict(title='Combined volume',works=[dict(key='/works/OL1W'),dict(key='/works/OL3W')],isbn_10=['012345678X'])),
        ('bad-key',dict(title='Bad record')),
    ])
    state=local.build(dumps,editions=True)
    assert state['editions'] and state['counts']['editions']==5
    rows=local.editions('/works/OL1W')
    assert [r['key'] for r in rows]==['/books/OL3M','/books/OL2M','/books/OL1M','/books/OL5M']
    [edition]=local.editions(isbn='978-0-307-38789-9')
    assert edition['work_key']=='/works/OL1W' and edition['year']==2007
    assert edition['publishers']==['Vintage'] and edition['isbns']==['0307387895','9780307387899']
    assert edition['languages']==[dict(key='/languages/eng')]
    assert local.search('The Road','Cormac McCarthy')[0]['original_year']==2006
    assert local.search('Blood Meridian','Cormac McCarthy')[0]['original_year'] is None
    assert local.editions(isbn='012345678X')[0]['work_key'] is None
    assert local.editions(isbn='012345678X')[0]['works']==['/works/OL1W','/works/OL3W']
    assert local.editions('/works/OL999W')==[] and local.editions(isbn='invalid')==[]
    assert local.editions('unsafe')==[] and local.editions()==[]
    from app.providers.openlibrary import OpenLibrary
    published=OpenLibrary().edition_publishers('/works/OL1W')
    assert [r['year'] for r in published]==[2006,2007]
    assert published[-1]['isbns']==['0307387895','9780307387899']


def test_editions_are_optional_and_failed_edition_import_preserves_published_index(dumps):
    assert local.editions('/works/OL1W') is None
    local.build(dumps)
    assert local.editions('/works/OL1W') is None
    with pytest.raises(ValueError,match='missing Open Library dump'):local.build(dumps,editions=True)
    (dumps/'editions.txt.gz').write_bytes(b'corrupt gzip')
    with pytest.raises(OSError):local.build(dumps,editions=True)
    assert local.search('The Road','Cormac McCarthy')[0]['original_year']==2006
    assert local.editions('/works/OL1W') is None


def test_editions_cli_flag_imports_the_snapshot(dumps,capsys):
    from app.cli import main
    dump(dumps/'editions.txt.gz',[('/books/OL1M',dict(title='The Road',works=[dict(key='/works/OL1W')],publish_date='2006'))])
    assert main(['openlibrary-import','--directory',str(dumps),'--editions'])==0
    assert json.loads(capsys.readouterr().out)['counts']['editions']==1
    assert local.editions('/works/OL1W')[0]['year']==2006


@pytest.mark.parametrize('languages,keep', [([],True),(['/languages/eng'],True),(['/languages/fre'],False),(['/languages/ger','/languages/eng'],True),([''],True),(['/languages/not-a-code'],True),(['/languages/und'],True),(['/languages/mul'],True)])
def test_edition_language_filter_keeps_english_and_unknown(languages,keep):
    assert local.keep_edition(languages) is keep


def test_import_excludes_explicit_foreign_editions_and_their_links(dumps):
    dump(dumps/'editions.txt.gz',[(f'/books/OL{n}M',dict(title='The Road',works=[dict(key='/works/OL1W')],isbn_10=[f'{n:010}'],languages=languages)) for n,languages in enumerate([
        [dict(key='/languages/fre')],[dict(key='/languages/eng')],[],[dict(key='/languages/ger'),dict(key='/languages/eng')],[{}],
    ],1)])
    state=local.build(dumps,editions=True)
    assert state['counts']['editions']==4 and state['edition_languages']=='english_or_unknown'
    assert {r['key'] for r in local.editions('/works/OL1W')}=={'/books/OL2M','/books/OL3M','/books/OL4M','/books/OL5M'}
    assert local.editions(isbn='0000000001')==[]


def old_editions_index(dumps):
    dump(dumps/'editions.txt.gz',[(f'/books/OL{n}M',dict(title='The Road',works=[dict(key='/works/OL1W')],isbn_10=[f'{n:010}'])) for n in (1,2)])
    local.build(dumps,editions=True)
    c=sqlite3.connect(local.folder()/'index.sqlite')
    with c:
        snapshot=json.loads(c.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0]);snapshot.pop('edition_languages')
        c.execute("UPDATE metadata SET value=? WHERE key='snapshot'",(json.dumps(snapshot),))
        c.execute("UPDATE editions SET languages=? WHERE key='/books/OL1M'",(json.dumps(['/languages/fre']),))
        c.execute("UPDATE editions SET languages=? WHERE key='/books/OL2M'",(json.dumps(['/languages/und']),))
    c.close()


def test_compaction_removes_foreign_editions_links_and_reclaims_pages_atomically(dumps,capsys):
    from app.cli import main
    old_editions_index(dumps)
    assert main(['openlibrary-compact'])==0
    summary=json.loads(capsys.readouterr().out)
    assert summary['counts']['editions']==1 and summary['editions_excluded']==1
    assert local.status()['ready'] and local.status()['edition_languages']=='english_or_unknown'
    assert [r['key'] for r in local.editions('/works/OL1W')]==['/books/OL2M']
    assert local.editions(isbn='0000000001')==[]
    assert local.search('The Road','Cormac McCarthy')[0]['original_year']==2006
    assert local.compact_editions()==summary
    assert not (local.folder()/'index.sqlite.compacting').exists()


def test_failed_compaction_preserves_published_index(dumps,monkeypatch):
    old_editions_index(dumps)
    target=local.folder()/'index.sqlite';before=target.read_bytes();replace=Path.replace
    def fail_publication(self,other):
        if self.name=='index.sqlite.compacting':raise OSError('publication failed')
        return replace(self,other)
    monkeypatch.setattr(Path,'replace',fail_publication)
    with pytest.raises(OSError,match='publication failed'):local.compact_editions()
    assert target.read_bytes()==before and local.status()['ready']
    assert len(local.editions('/works/OL1W'))==2
    assert not (local.folder()/'index.sqlite.compacting').exists()


def test_compaction_requires_a_published_editions_snapshot(dumps):
    local.folder().mkdir(parents=True,exist_ok=True)
    with pytest.raises(ValueError,match='no published'):local.compact_editions()
    local.build(dumps)
    with pytest.raises(ValueError,match='no editions'):local.compact_editions()
