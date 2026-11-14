"""The incremental listen build and identification jobs (Postgres)."""

import pytest
from sqlalchemy import func, select

from app import pipeline_steps as P
from app.store import build
from app.store import filegroups as fg
from app.store import structure as st
from app.store.models import Listen, ListenKind, ListenPart


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres":
        pytest.skip("needs Postgres")
    return db_session


def test_a_listen_with_no_candidate_is_unidentified_until_it_has_one_or_is_marked_none(s, tmp_path):
    for rel in (
        "A/Book Two/01.mp3",
        "A/Book Two/02.mp3",
        "A/Book Two/03.mp3",
        "Radio/Show S01E01.mp3",
        "Radio/Show S01E02.mp3",
        "Radio/Show S01E03.mp3",
    ):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(rel.encode())
    top, _ = st.infer(s, fg.group_by_folder(s, str(tmp_path), fg.scan_files(s, str(tmp_path))))
    build.add_listens(s, top)
    ids = P.unidentified(s)
    assert len(ids) == 2
    s.get(Listen, ids[0]).status = "not_found"  # a person said none of the candidates
    s.flush()
    assert P.unidentified(s) == ids[1:]
    assert (
        s.scalars(select(func.count()).select_from(ListenPart)).one() >= 2 and not s.scalars(select(ListenKind)).all()
    )


def test_the_build_job_makes_listens_with_metadata_and_kinds_and_reruns_cleanly(db_backend, db_env, tmp_path):
    from omarchy_db import connect
    from sqlalchemy.orm import Session

    if db_backend != "postgres":
        pytest.skip("needs Postgres")
    for rel in ("A/Book Two/01.mp3", "A/Book Two/02.mp3", "A/Book Two/03.mp3"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(rel.encode())
    with Session(connect("listory")) as own:  # the job opens its own connection, so the data must be committed
        fg.group_by_folder(own, str(tmp_path), fg.scan_files(own, str(tmp_path)))
        own.commit()
    seen = []
    first = P.build_listens(lambda current, done=None, total=None: seen.append((current, done, total)))
    assert first["made"] == 1 and first["metadata_read"] == 1 and first["classified"] == 1 and seen
    again = P.build_listens(lambda *a, **k: None)
    assert again["made"] == 0 and again["already_had_listens"] == 1 and again["classified"] == 0


def test_the_identify_job_needs_a_model(s, db_env, monkeypatch):
    from app.linkage import service

    monkeypatch.setattr(service, "load_params", lambda: None)
    with pytest.raises(ValueError, match="no model yet"):
        P.identify(lambda *a, **k: None)
