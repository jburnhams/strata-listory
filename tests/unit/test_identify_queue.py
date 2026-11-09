from identify_dumps import built

from app.identify import candidates as cand
from app.identify import context, dataset, queue


def predict(rows):
    return [0.6 for _ in rows]


def items():
    rows = [(f"/a/Hobbit/The Hobbit CD01 {n:02}.mp3", f"The Hobbit CD01 {n:02}") for n in range(1, 4)] + [
        ("/a/x/Lone.m4b", "Lone")
    ]
    index = context.Index(rows)
    return [index.annotate(dict(title=t, author="J.R.R. Tolkien" if "Hobbit" in t else "", path=p)) for p, t in rows]


def test_groups_collapse_numbered_parts_and_impact_orders():
    g = queue.groups(items())
    assert sorted(v["files"] for v in g.values()) == [1, 3]
    assert (
        queue.impact(10, 0.5) > queue.impact(10, 0.99)
        and queue.impact(10, 0.5) > queue.impact(1, 0.5)
        and queue.impact(5, 0.5, 4) > queue.impact(5, 0.5, 1)
    )


def test_build_decide_and_label_runs(tmp_path):
    db = cand.connect(built(tmp_path))
    q, accepted = queue.build(db, predict, items())
    assert accepted == [] and q and q[0]["files"] == 3 and q[0]["work"] == "/works/OL1W" and q[0]["impact"] > 0
    top = q[0]
    queue.decide(top["item"], "approve", top["work"], top["files"], top["title"], "J. R. R. Tolkien")
    assert top["group"] in queue.decided() and all(
        s["group"] != top["group"] for s in queue.build(db, predict, items())[0]
    )
    queue.decide(
        dict(title="The Hobbit", author="", path="/b/Hobbit.m4b"),
        "reject",
        "/works/OL1W",
        1,
        "The Hobbit",
        "J. R. R. Tolkien",
    )
    runs = queue.label_runs()
    assert {r["target"].get("work") or r["target"].get("reject") for r in runs} == {"/works/OL1W"} and all(
        r["origin"] == "manual" for r in runs
    )
    rows, misses = dataset.build(db, runs, blank_author=False)
    assert rows and any(r["label"] == 1 for r in rows) and any(r["label"] == 0 for r in rows)
    try:
        queue.decide(items()[0], "maybe", "x")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid decision accepted")
