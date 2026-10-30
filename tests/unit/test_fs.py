import errno, os, time
import pytest
from app import backup, catalogue, details, fs, mirror, scan, settings
from app.providers.base import MatchError


@pytest.fixture
def share(tmp_path, monkeypatch):
    """A fake network mount at tmp_path/share whose state the test controls: share.up = True/False, share.hang = seconds."""
    root = tmp_path / 'share'; root.mkdir()
    class S: up = True; hang = 0.0; probes = 0
    s = S()
    def probe(mount):
        s.probes += 1; time.sleep(s.hang)
        return s.up
    monkeypatch.setattr(fs, 'network_mounts', lambda: [str(root)]); monkeypatch.setattr(fs, '_probe', probe); monkeypatch.setattr(fs, 'TIMEOUT', 0.3)
    fs.forget(); s.root = root
    yield s
    fs.forget()


def test_local_paths_are_never_probed(share, tmp_path):
    assert fs.mount_of(str(tmp_path / 'elsewhere')) is None and fs.reachable(str(tmp_path / 'elsewhere')) and share.probes == 0
    assert fs.mount_of(str(share.root / 'a' / 'b')) == str(share.root) and fs.mount_of(str(share.root)) == str(share.root)
    assert fs.mount_of(str(share.root) + '-other') is None                                         # a sibling with the same prefix is not on the share


def test_answers_are_remembered_briefly_and_rechecked_later(share, monkeypatch):
    p = str(share.root / 'x')
    assert fs.reachable(p) and fs.reachable(p) and share.probes == 1
    share.up = False; assert fs.reachable(p) and share.probes == 1                                 # still within UP_FOR
    monkeypatch.setattr(fs, 'UP_FOR', 0); assert not fs.reachable(p) and share.probes == 2
    share.up = True; assert not fs.reachable(p) and share.probes == 2                              # a down answer is kept for DOWN_FOR
    monkeypatch.setattr(fs, 'DOWN_FOR', 0); assert fs.reachable(p) and share.probes == 3
    fs.forget(); assert fs.reachable(p) and share.probes == 4


def test_a_hanging_share_times_out_and_does_not_pile_up_probes(share):
    p = str(share.root / 'x'); share.hang = 1.0
    t = time.monotonic(); assert not fs.reachable(p); assert time.monotonic() - t < 0.8             # gave up after TIMEOUT, not after the hang
    fs.forget(); fs._inflight[str(share.root)] = fs._pool.submit(time.sleep, 1.0)                  # a probe still stuck from before
    t = time.monotonic(); assert not fs.reachable(p); assert time.monotonic() - t < 0.2             # answered at once, no new probe
    time.sleep(1.1)


def test_probe_and_mountinfo_parsing(tmp_path, monkeypatch):
    d = tmp_path / 'm'; d.mkdir(); (d / 'f').write_text('x')
    assert fs._probe(str(d)) is True and fs._probe(str(tmp_path / 'missing')) is False
    info = tmp_path / 'mountinfo'; info.write_text('36 35 0:30 / /mnt/my\\040share rw - cifs //server/Audio rw\n37 35 0:31 / /home rw - ext4 /dev/sda rw\n38 35 0:32 / /mnt/n rw - nfs4 srv:/x rw\n')
    real_open = open
    monkeypatch.setattr('builtins.open', lambda p, *a, **k: real_open(info if p == '/proc/self/mountinfo' else p, *a, **k))
    assert fs.network_mounts() == ['/mnt/my share', '/mnt/n']
    monkeypatch.setattr('builtins.open', lambda p, *a, **k: (_ for _ in ()).throw(OSError('no proc')))
    assert fs.network_mounts() == []


def test_require_raises_a_clear_error(share):
    share.up = False
    with pytest.raises(fs.ShareDown, match='not reachable'): fs.require(str(share.root / 'x'))


def test_book_page_and_covers_survive_a_down_share(share):
    d = share.root / 'Book'; d.mkdir(); (d / '01.mp3').write_bytes(b'x'); (d / 'cover.jpg').write_bytes(b'\xff\xd8\xff\xe0' + b'0' * 10)
    b = catalogue.add_book(title='Book', path=str(d))
    share.up = False; fs.forget()
    assert details.describe(b) == dict(path_exists=False, share_down=True, files=None, probe=None, audible=None)
    assert details.cover(b) is None and not os.path.exists(os.path.join(settings.data_dir(), 'covers', f"{b['id']}.none"))         # no verdict remembered
    share.up = True; fs.forget()
    assert details.describe(b)['files']['count'] == 1 and details.cover(b)                         # and everything works once it is back
    share.up = False; fs.forget()
    assert details.cover(b)                                                                         # a cover already saved in the mirror needs no share


def test_oserrors_while_reading_a_reachable_share_count_as_down(share, monkeypatch):
    d = share.root / 'B'; d.mkdir(); (d / '01.mp3').write_bytes(b'x'); b = catalogue.add_book(title='B', path=str(d))
    monkeypatch.setattr(details, 'audio_files', lambda p: (_ for _ in ()).throw(OSError(errno.EHOSTDOWN, 'Host is down')))
    assert details.describe(b)['share_down'] is True
    assert details.cover(b) is None and not os.path.exists(os.path.join(settings.data_dir(), 'covers', f"{b['id']}.none"))


def test_mirror_keeps_the_audible_summary_while_the_share_is_down(share):
    d = share.root / 'Aud'; d.mkdir(); (d / 'x.metadata.json').write_text('{"asin": "B0X", "title": "Aud"}'); (d / '01.mp3').write_bytes(b'x')
    b = catalogue.add_book(title='Aud', path=str(d))
    assert mirror.read_meta(b)['audible_file']['asin'] == 'B0X'
    share.up = False; fs.forget(); catalogue.update_book(b['id'], status='finished')
    meta = mirror.read_meta(b)
    assert meta['audible_file']['asin'] == 'B0X' and meta['book']['status'] == 'finished'          # not wiped by an unreadable share


def test_a_scan_stops_when_the_share_goes_away_midway(tmp_path, monkeypatch):
    (tmp_path / 'a').mkdir(); (tmp_path / 'a' / '01.mp3').write_bytes(b'x'); (tmp_path / 'b').mkdir(); (tmp_path / 'b' / '01.mp3').write_bytes(b'x')
    real = os.scandir; calls = []
    def flaky(p):
        calls.append(p)
        if len(calls) > 2: raise OSError(errno.EHOSTDOWN, 'Host is down')
        return real(p)
    monkeypatch.setattr(os, 'scandir', flaky)
    with pytest.raises(OSError, match='Host is down'): list(scan.scan(str(tmp_path)))
    monkeypatch.setattr(os, 'scandir', real)
    scan._walk_error(PermissionError(errno.EACCES, 'denied'))                                       # a permission problem on one folder is only skipped
    assert len(list(scan.scan(str(tmp_path)))) == 2


def test_backups_skip_an_unreachable_share(share, tmp_path, monkeypatch):
    monkeypatch.setenv('APP_BACKUP', str(share.root / 'backups')); catalogue.add_book(title='Dune')
    share.up = False; fs.forget()
    p = backup.backup('manual')
    assert p.startswith(os.path.join(settings.data_dir(), 'backups'))                              # straight to the local folder, no waiting on the dead share
    share.up = True; fs.forget()
    assert backup.backup('manual').startswith(str(share.root / 'backups'))
