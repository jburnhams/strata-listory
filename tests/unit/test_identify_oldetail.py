import json
import sqlite3

import pytest

from app.identify import oldetail

try:
    from compression import zstd

    def train(samples):
        return zstd.train_dict(samples, 4096).dict_content

    def compress(data, raw):
        return zstd.compress(data, zstd_dict=zstd.ZstdDict(raw))
except ImportError:  # Python < 3.14
    import zstandard

    def train(samples):
        return zstandard.train_dictionary(4096, samples).as_bytes()

    def compress(data, raw):
        return zstandard.ZstdCompressor(dict_data=zstandard.ZstdCompressionDict(raw)).compress(data)


def store(path):
    """A detail.sqlite as scripts/build_ol_detail.py writes it: a dictionary per kind, one compressed record per key."""
    records = {
        "works": [
            {"key": f"/works/OL{i}W", "title": f"Title number {i}", "subjects": ["Fiction", "History"] * 3}
            for i in range(1, 400)
        ]
    }
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE dict(id INTEGER PRIMARY KEY, kind TEXT, data BLOB)")
    c.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    raw = train([json.dumps(r).encode() for r in records["works"]])
    c.execute('INSERT INTO dict VALUES(2, "works", ?)', (raw,))
    for kind in ("authors", "works", "editions"):
        c.execute(f"CREATE TABLE {kind}(key TEXT NOT NULL, dict INTEGER NOT NULL, blob BLOB NOT NULL)")
    c.executemany(
        "INSERT INTO works VALUES(?,2,?)", [(r["key"], compress(json.dumps(r).encode(), raw)) for r in records["works"]]
    )
    c.execute('INSERT INTO meta VALUES("snapshot", "s")')
    c.commit()
    c.close()


def test_get_returns_the_full_record_by_key_or_bare_id_and_none_when_absent(tmp_path):
    store(tmp_path / "d.sqlite")
    d = oldetail.connect(tmp_path / "d.sqlite")
    assert d.get("/works/OL7W")["title"] == "Title number 7" and d.get("OL7W")["key"] == "/works/OL7W"
    assert d.get("OL99999W") is None and d.get("OL1M") is None and d.snapshot() == {"snapshot": "s"}
    d.close()


def test_unknown_key_kind_is_an_error_and_a_missing_store_is_none(tmp_path):
    with pytest.raises(ValueError):
        oldetail.table("/x/OL1Z")
    assert oldetail.connect(tmp_path / "nope.sqlite") is None
