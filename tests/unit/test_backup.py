import os, sqlite3
from datetime import datetime, timedelta, timezone
import pytest
from app import backup, catalogue, db


def make(name, age):
    """An (empty) backup file that looks `age` old."""
    os.makedirs(backup.folder(), exist_ok=True)
    taken = datetime.now(timezone.utc) - age
    p = os.path.join(backup.folder(), f"catalogue-{taken:%Y%m%d-%H%M%S}{'-' + name if name else ''}.db"); open(p, 'w').close(); return p


def test_backup_is_taken_on_first_open_once_a_day_and_verified(monkeypatch):
    assert backup.backup() is None and backup.list_backups() == []          # nothing to back up yet
    catalogue.add_book(title='Dune')                                         # creates the database
    monkeypatch.setattr(db, '_checked', set())
    catalogue.list_books()                                                   # first open in a "new process" -> routine backup
    [b] = backup.list_backups()
    assert b['reason'] == '' and backup.check(b['path'])
    assert backup.maybe_backup() is None                                     # one a day is enough
    c = sqlite3.connect(b['path']); assert c.execute('select title from books').fetchone()[0] == 'Dune'; c.close()


def test_maybe_backup_runs_when_the_last_is_old():
    catalogue.add_book(title='a'); make('', timedelta(hours=25))
    assert backup.maybe_backup() is not None


def test_manual_backup_restore_and_safety_copy():
    catalogue.add_book(title='Before')
    snap = backup.backup('manual')
    catalogue.add_book(title='After')
    safety = backup.restore(snap)
    assert [b['title'] for b in catalogue.list_books()] == ['Before']
    assert safety and 'pre-restore' in safety
    assert [b['reason'] for b in backup.list_backups() if b['path'] == safety] == ['pre-restore']
    with pytest.raises(ValueError, match='not a sound'): backup.restore(os.path.join(backup.folder(), 'missing.db'))
    bad = os.path.join(backup.folder(), 'bad.db'); open(bad, 'wb').write(b'x' * 500)
    with pytest.raises(ValueError, match='not a sound'): backup.restore(bad)


def test_migration_backs_up_an_existing_database(monkeypatch):
    catalogue.add_book(title='Old')
    monkeypatch.setattr(db, 'SCHEMA', db.SCHEMA + ('ALTER TABLE books ADD COLUMN isbn TEXT',))
    catalogue.list_books()
    assert [b['reason'] for b in backup.list_backups() if b['reason']] == ['pre-migration']


def test_prune_keeps_recent_days_weeks_and_pre_backups():
    keep = [make('', timedelta(hours=1)), make('', timedelta(days=3)), make('pre-restore', timedelta(days=20)), make('', timedelta(days=15))]
    keep.append(make('manual', timedelta(days=5)))
    gone = [make('', timedelta(hours=2)), make('', timedelta(days=60)), make('pre-migration', timedelta(days=40))]
    backup.prune()
    left = {b['path'] for b in backup.list_backups()}
    assert all(p in left for p in keep) and not any(p in left for p in gone)


def test_failed_integrity_check_discards_the_new_backup(monkeypatch):
    catalogue.add_book(title='a')
    monkeypatch.setattr(backup, 'check', lambda p: False)
    with pytest.raises(RuntimeError, match='integrity'): backup.backup()
    assert backup.list_backups() == [] and not [n for n in os.listdir(backup.folder()) if n.endswith('.tmp')]


def test_restore_works_even_if_the_backup_is_old_and_would_be_pruned():
    catalogue.add_book(title='Before'); snap = backup.backup('manual')
    catalogue.add_book(title='After')
    os.replace(snap, snap2 := os.path.join(backup.folder(), 'catalogue-20200101-000000.db'))      # an old, unnamed-reason file
    backup.restore(snap2)
    assert [b['title'] for b in catalogue.list_books()] == ['Before']
