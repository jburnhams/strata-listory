"""User settings, declared once with strata-python's `Settings`: one JSON file, `~/.app/settings.json` (the folder moves with APP_HOME), validated on save and overridden by the environment variables named below.

  data_dir   where application data files live. Default: `data/` in this repository (gitignored) until configured. APP_DATA.
  library_dir  the folder the audiobook files live in (optional). Default: unset. APP_LIBRARY.
  library_dirs  more folders to scan alongside library_dir (a list; the Files page edits it). `library_roots()` is library_dir then these, without duplicates or folders inside another.
  backup_dir   where the catalogue database backups go (default: `backups/` inside data_dir). May be a network share (SMB): backups are built on the local disk and copied there. APP_BACKUP.
  match_providers  comma separated metadata providers in the order to try them (default: audnexus,audible,audiobookdb). APP_PROVIDERS.
  audible_region   which Audible store to search: uk (default), us, de, fr, ca or au. APP_AUDIBLE_REGION.
  final_dir        where processed books are written (default: ~/audiobooks). APP_FINAL.

The functions below read the declaration live (an environment variable beats the file beats the default), so a settings change reaches a running server at once."""

import os

from strata_python.settings import ListSetting, Setting, Settings, absolute_path

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DATA_DIR = os.path.join(REPO, "data")  # tests point this into a tmp folder (tests/conftest.py)


def _optional_path(value: str) -> str:
    """A folder that may be left unset: blank stays blank, anything else must be an absolute path (`~` expanded)."""
    return absolute_path(value) if value.strip() else ""


def _names(value: str) -> str:
    """A comma separated list of names, trimmed and without blanks."""
    return ",".join(n.strip() for n in value.split(",") if n.strip())


SETTINGS = Settings(
    "app",  # ~/.app/settings.json, APP_HOME, APP_<KEY>
    [
        Setting("data_dir", default=lambda: DEFAULT_DATA_DIR, normalise=absolute_path, env="APP_DATA"),
        Setting("library_dir", default="", normalise=_optional_path, env="APP_LIBRARY"),
        ListSetting("library_dirs", item=absolute_path),
        Setting(
            "backup_dir",
            default=lambda: os.path.join(SETTINGS.get("data_dir"), "backups"),
            normalise=absolute_path,
            env="APP_BACKUP",
        ),
        Setting("match_providers", default="", normalise=_names, env="APP_PROVIDERS"),
        Setting("audible_region", default="uk", choices=["uk", "us", "de", "fr", "ca", "au"], env="APP_AUDIBLE_REGION"),
        Setting(
            "final_dir",
            default=lambda: os.path.join(os.path.expanduser("~"), "audiobooks"),
            normalise=absolute_path,
            env="APP_FINAL",
        ),
    ],
)
KNOWN = SETTINGS.keys
home, load, save = SETTINGS.home, SETTINGS.load, SETTINGS.save


def data_dir():
    """The folder that holds application data (created when asked for)."""
    return SETTINGS.folder("data_dir")


def library_dir():
    """The configured audiobook folder, or '' when none is set."""
    return SETTINGS.get("library_dir")


def library_roots():
    """Every folder that holds audiobooks: library_dir first, then the extra library_dirs. Duplicates and folders inside another root are left out (a file would be in two roots)."""
    out: list[str] = []
    for p in [library_dir(), *SETTINGS.get_list("library_dirs")]:
        if p and not any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for r in out):
            out = [r for r in out if not r.startswith(p.rstrip(os.sep) + os.sep)] + [p]
    return out


def backup_dir():
    """The folder for database backups (not created here; backup.py makes it when it writes)."""
    return SETTINGS.get("backup_dir")


def final_dir():
    return SETTINGS.get("final_dir")


def audible_region():
    return SETTINGS.get("audible_region")


def all_settings():
    """Every setting as the API shows it; `library_dirs` is the roots (library_dir first, overlaps removed)."""
    data_dir()
    return {**SETTINGS.all(), "library_dirs": library_roots()}
