"""Source summaries show filenames without invoking audio probes."""
from app import catalogue, source_files
from app.ingest import store


def test_source_summary_samples_directory_and_single_file_paths(tmp_path,monkeypatch):
    folder=tmp_path/'Book';folder.mkdir()
    for n in range(5):(folder/f'{n+1:03}.mp3').write_bytes(b'audio')
    b=catalogue.add_book(title='Book',path=str(folder))
    monkeypatch.setattr(source_files.details,'_ffprobe',lambda *a:(_ for _ in ()).throw(AssertionError('must not probe')))
    assert source_files.describe(b)==dict(folder=str(folder),files=['001.mp3','002.mp3','003.mp3'],count=5,available=True)
    b=catalogue.add_book(title='Single',path=str(folder/'001.mp3'))
    assert source_files.describe(b)==dict(folder=str(folder),files=['001.mp3'],count=1,available=True)
    assert source_files.describe(catalogue.add_book(title='No path'))['count']==0


def test_source_summary_reuses_inventory_and_handles_unavailable_sources(tmp_path,monkeypatch):
    b=catalogue.add_book(title='Book',path=str(tmp_path/'Book'),file_count=10)
    monkeypatch.setattr(source_files.fs,'reachable',lambda *a:False)
    assert source_files.describe(b)==dict(folder=b['path'],files=[],count=10,available=False)
    monkeypatch.setattr(source_files.fs,'reachable',lambda *a:True)
    monkeypatch.setattr(source_files.details,'audio_files',lambda *a:(_ for _ in ()).throw(OSError('offline')))
    assert not source_files.describe(b)['available']
    rid=store.create(b['id'],{})
    store.event(rid,'inventory','done',data=dict(root=b['path'],files=[dict(path=str(tmp_path/'Book'/'001.mp3'))]))
    assert source_files.describe(b)['files']==['001.mp3']


def test_source_summary_exposes_grouping_evidence_from_the_saved_inventory(tmp_path):
    b=catalogue.add_book(title='Stories',path=str(tmp_path/'Stories'))
    rid=store.create(b['id'],{})
    store.event(rid,'inventory','done',data=dict(root=b['path'],files=[dict(path=str(tmp_path/'Stories'/'01.mp3'))],work_hint=dict(grouping_notes=['Shared album and numbered discs','Publisher contents confirm the disc with missing tags'])))
    assert source_files.describe(b)['grouping']==['Shared album and numbered discs','Publisher contents confirm the disc with missing tags']
