"""User settings: one JSON file, `~/.app/settings.json` (the folder can be moved with the APP_HOME environment variable).

  data_dir   where application data files live. Default: `data/` in this repository (gitignored) until configured.
  library_dir  the folder the audiobook files live in (optional; not used yet, reserved for scanning). Default: unset.
  backup_dir   where the catalogue database backups go (default: `backups/` inside data_dir). May be a network share (SMB): backups are built on the local disk and copied there.
  match_providers  comma separated metadata providers in the order to try them (default: audnexus,audible,audiobookdb).
  audible_region   which Audible store to search: uk (default), us, de, fr, ca or au.
  final_dir        where processed books are written (default: ~/audiobooks).

The environment variables APP_DATA, APP_LIBRARY, APP_BACKUP, APP_PROVIDERS, APP_AUDIBLE_REGION and APP_FINAL override the file (handy for tests and one-off runs)."""
import json, os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DATA_DIR = os.path.join(REPO, 'data')
KNOWN = ('data_dir', 'library_dir', 'backup_dir', 'match_providers', 'audible_region', 'final_dir')


def home():
    return os.environ.get('APP_HOME') or os.path.join(os.path.expanduser('~'), '.app')


def _path(): return os.path.join(home(), 'settings.json')


def load():
    try:
        with open(_path(), encoding='utf8') as f: d = json.load(f)
    except (OSError, ValueError): d = {}
    return d if isinstance(d, dict) else {}


def save(changes):
    """Merge `changes` (known keys only) into the settings file; returns the saved settings. A None value removes the setting (back to its default)."""
    d = load()
    for k, v in changes.items():
        if k not in KNOWN: raise KeyError(f'unknown setting {k!r}')
        if v is None: d.pop(k, None)
        else: d[k] = str(v)
    os.makedirs(home(), exist_ok=True); tmp = _path() + '.tmp'
    with open(tmp, 'w', encoding='utf8') as f: json.dump(d, f, indent=1)
    os.replace(tmp, _path()); return d


def data_dir():
    """The folder that holds application data (created when asked for)."""
    p = os.environ.get('APP_DATA') or load().get('data_dir') or DEFAULT_DATA_DIR
    p = os.path.abspath(os.path.expanduser(p)); os.makedirs(p, exist_ok=True); return p


def library_dir():
    """The configured audiobook folder, or '' when none is set."""
    p = (os.environ.get('APP_LIBRARY') or load().get('library_dir') or '').strip()
    return os.path.abspath(os.path.expanduser(p)) if p else ''


def backup_dir():
    """The folder for database backups (not created here; backup.py makes it when it writes)."""
    p = (os.environ.get('APP_BACKUP') or load().get('backup_dir') or '').strip()
    return os.path.abspath(os.path.expanduser(p)) if p else os.path.join(data_dir(), 'backups')


def final_dir():
    return os.path.abspath(os.path.expanduser(os.environ.get('APP_FINAL') or load().get('final_dir') or os.path.join('~', 'audiobooks')))


def audible_region():
    r = (os.environ.get('APP_AUDIBLE_REGION') or load().get('audible_region') or 'uk').strip().lower()
    return r or 'uk'


def all_settings(): return dict(data_dir=data_dir(), library_dir=library_dir(), backup_dir=backup_dir())
