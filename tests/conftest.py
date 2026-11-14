"""Shared pytest configuration. strata-python's plugin makes every test hermetic (its own home and data folder, no network, no stray override variables, the `integration` marker under tests/integration) and provides `fake_urlopen`, `fake_popen`, `fake_run`, `no_sleep`; configured in pyproject.toml. Only what is specific to this app is here."""

import pytest


@pytest.fixture(autouse=True)
def app_defaults(isolated_env, monkeypatch):
    """strata-python's plugin (`strata_env_prefix` in pyproject.toml) points `~/.app` and the data folder into a tmp dir and removes the tuning variables; the settings module also keeps its own default data folder, which must never reach the real catalogue."""
    from app import settings

    monkeypatch.setattr(settings, "DEFAULT_DATA_DIR", str(isolated_env / "data"))
    return isolated_env


@pytest.fixture(autouse=True)
def no_year_lookup(request, monkeypatch):
    """Planning asks Open Library and Hardcover for the original publication year; in tests it only reads what is already stored. A test of the lookup itself (or of the Open Library provider) uses `@pytest.mark.year_lookup`."""
    from app import catalogue, enrich
    from app.providers.openlibrary import OpenLibrary

    if request.node.get_closest_marker("year_lookup"):
        return
    monkeypatch.setattr(
        OpenLibrary, "search", lambda self, title, author="": []
    )  # Open Library finds nothing unless a test gives it something
    from app import sources

    monkeypatch.setattr(enrich, "ensure_year_sources", lambda book: catalogue.get_extras(book["id"]))
    monkeypatch.setattr(
        sources,
        "gather",
        lambda book, audible=None: sources.local_records(book, audible, catalogue.get_extras(book["id"])),
    )
