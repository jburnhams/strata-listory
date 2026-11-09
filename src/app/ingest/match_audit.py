"""Read-only candidate feature export for match experiments.

Run: python -m app.ingest.match_audit DATA/catalogue.db --out candidates.jsonl
The default export leaves labels unknown; match_model uses trusted approvals.
"""

import argparse
import difflib
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from app import book_titles, contributors, matcher, merge
from app.ingest import metadata

VERSION = 2
RATING_FEATURES = {
    "ratings_count_known",
    "ratings_count_log",
    "ratings_popularity_relative",
    "rating_known",
    "rating_average",
}


def edit_similarity(a, b):
    """Normalised Levenshtein similarity; retain missingness separately."""
    if not a or not b:
        return 0.0
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        following = [i]
        for j, y in enumerate(b, 1):
            following.append(min(following[-1] + 1, row[j] + 1, row[j - 1] + (x != y)))
        row = following
    return 1 - row[-1] / max(len(a), len(b))


def numeric(value):
    try:
        number = float(value)
        return number if not isinstance(value, bool) and math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def evidence_features(book, candidate, cohort, editions):
    """Observed evidence only: missingness and popularity never assert a match."""
    count = numeric(candidate.get("ratings_count"))
    rating = numeric(candidate.get("rating"))
    rating = rating if rating is not None and rating <= 5 else None
    source_counts = [numeric(c.get("ratings_count")) for c in cohort if c.get("source") == candidate.get("source")]
    maximum = max((v for v in source_counts if v is not None), default=0)
    title = " ".join((candidate.get("title") or "", candidate.get("subtitle") or ""))
    local = book.get("title") or ""
    biography = lambda text: bool(re.search(r"\b(?:biography|autobiography|biographical|memoir)\b", text, re.I))
    annotation = lambda text: bool(re.search(r"\b(?:annotated|annotation|study guide|summary)\b", text, re.I))
    adaptation = lambda text: bool(
        re.search(r"\b(?:adaptation|adapted|dramatization|dramatisation|retelling)\b", text, re.I)
    )
    original = numeric(candidate.get("original_year"))
    linked = [
        e
        for e in editions
        if e.get("work_key") == candidate.get("id") and e.get("key") == candidate.get("subtitle_edition")
    ]
    published = numeric(linked[0].get("year")) if linked else None
    publication = published if published is not None and 1000 <= published <= 2100 else None
    original = original if original is not None and 1000 <= original <= 2100 else None
    local_sub = book_titles.parts(metadata.work_title(local))[1]
    other_sub = book_titles.parts(metadata.work_title(candidate.get("title", "")), candidate.get("subtitle", ""))[1]
    return dict(
        ratings_count_known=int(count is not None),
        ratings_count_log=math.log1p(count) if count is not None else 0.0,
        ratings_popularity_relative=math.log1p(count) / math.log1p(maximum)
        if count is not None and maximum > 0
        else 0.0,
        rating_known=int(rating is not None),
        rating_average=rating / 5 if rating is not None else 0.0,
        candidate_biography=int(biography(title)),
        local_biography=int(biography(local)),
        biography_added=int(biography(title) and not biography(local)),
        candidate_annotation=int(annotation(title)),
        local_annotation=int(annotation(local)),
        annotation_added=int(annotation(title) and not annotation(local)),
        candidate_adaptation=int(adaptation(title)),
        adaptation_added=int(adaptation(title) and not adaptation(local)),
        subtitle_edit=edit_similarity(merge.norm(local_sub), merge.norm(other_sub)),
        candidate_year_known=int(original is not None),
        candidate_year_scaled=(original - 1800) / 300 if original is not None else 0.0,
        candidate_isbn_known=int(bool(candidate.get("isbns"))),
        candidate_publisher_known=int(bool(candidate.get("publisher"))),
        edition_evidence_known=int(bool(linked)),
        edition_year_known=int(publication is not None),
        edition_work_year_gap=min(abs(publication - original), 300) / 300
        if publication is not None and original is not None
        else 0.0,
        edition_work_year_comparable=int(publication is not None and original is not None),
        edition_isbn_known=int(any(e.get("isbns") for e in linked)),
        edition_publisher_known=int(any(e.get("publishers") for e in linked)),
    )


EXTRA_FEATURES = set(evidence_features({}, {}, [], []))


def features(book, candidate, cohort=(), editions=()):
    title = metadata.work_title(book.get("title", ""))
    found = metadata.work_title(candidate.get("title", ""))
    a = merge.norm(title)
    b = merge.norm(found)
    ta = set(a.split())
    tb = set(b.split())
    wanted = contributors.authors(book.get("author", ""))
    people = contributors.authors(candidate.get("authors") or candidate.get("author", ""))
    aa = {contributors.name_key(n) for n in wanted}
    bb = {contributors.name_key(n) for n in people}
    analysis = metadata.title_match(dict(book, title=title), candidate, cohort)
    numbers = lambda text: set(re.findall(r"\d+", text))
    return dict(
        title_edit=edit_similarity(a, b),
        title_sequence=difflib.SequenceMatcher(None, a, b).ratio(),
        title_exact=int(bool(a) and a == b),
        title_token_jaccard=len(ta & tb) / len(ta | tb) if ta | tb else 0.0,
        title_length_ratio=min(len(a), len(b)) / max(len(a), len(b), 1),
        title_rule_score=analysis["score"],
        title_rule_conflict=int(bool(analysis["reason"])),
        title_numbers_agree=int(numbers(a) == numbers(b)),
        local_subtitle_known=int(bool(book_titles.parts(title)[1])),
        candidate_subtitle_known=int(bool(book_titles.parts(found, candidate.get("subtitle", ""))[1])),
        author_edit=max((edit_similarity(x, y) for x in aa for y in bb), default=0.0),
        author_score=matcher.author_score(book.get("author", ""), candidate),
        author_exact=int(bool(aa) and aa == bb),
        author_overlap=len(aa & bb) / len(aa | bb) if aa | bb else 0.0,
        local_author_known=int(bool(aa)),
        candidate_author_known=int(bool(bb)),
        local_author_count=len(aa),
        candidate_author_count=len(bb),
        compatible=int(metadata.compatible_work(book, candidate, cohort)),
        work_score=metadata.work_candidate_score(book, candidate, cohort),
        exact_asin=int(bool(book.get("asin")) and book["asin"] == candidate.get("asin")),
        attached_edition=int(bool(candidate.get("subtitle_edition"))),
        **evidence_features(book, candidate, cohort, editions),
    )


def candidate_inputs(run):
    stages = {event["stage"]: event["data"] for event in run["events"] if event["status"] == "done"}
    book = (stages.get("identity") or {}).get("book", {})
    if not book.get("title"):
        return book, {}, {}, stages
    collected = metadata.work_edition_evidence(stages.get("providers") or dict(providers={}))
    cohort = [
        dict(c, source=name)
        for name, entry in collected["providers"].items()
        for c in entry.get("candidates", [])
        if name in merge.WORK
    ]
    edition_evidence = {
        name: entry.get("publisher_editions", []) + entry.get("isbn_editions", [])
        for name, entry in collected["providers"].items()
    }
    unique = {
        json.dumps([c.get("source"), c.get("id"), c.get("title"), c.get("subtitle"), c.get("subtitle_edition")]): c
        for c in cohort
    }
    return book, unique, edition_evidence, stages


def candidate_rows(run):
    book, unique, edition_evidence, stages = candidate_inputs(run)
    if not book.get("title"):
        return []
    cohort = list(unique.values())
    rows = []
    support = {}
    vectors = {}

    def identity(c):
        return (
            book_titles.compact(metadata.work_title(c["title"])),
            tuple(
                sorted(contributors.name_key(n) for n in contributors.authors(c.get("authors") or c.get("author", "")))
            ),
        )

    for key, c in unique.items():
        support.setdefault(identity(c), set()).add(metadata.family(c["source"]))
        vectors[key] = features(book, c, cohort, edition_evidence.get(c["source"], []))
    for key, c in unique.items():
        vector = vectors[key]
        vector["supporting_families"] = len(support[identity(c)])
        others = [
            vectors[other_key]["work_score"]
            for other_key, other in unique.items()
            if other is not c and other["source"] == c["source"] and other.get("id") != c.get("id")
        ]
        vector["source_margin"] = vector["work_score"] - max(others, default=0.0)
        rows.append(
            dict(
                version=VERSION,
                run_id=run["id"],
                book_id=run["book_id"],
                candidate_key=hashlib.sha256(key.encode()).hexdigest(),
                title=book["title"],
                author=book.get("author", ""),
                candidate=c,
                edition_evidence=[
                    e
                    for e in edition_evidence.get(c["source"], [])
                    if e.get("work_key") == c.get("id") and e.get("key") == c.get("subtitle_edition")
                ],
                features=vector,
                label=None,
                work_group=None,
                approval=stages.get("metadata_approval"),
                label_note="Assign 1/0 only after independent review; group editions/fragments of the same work together.",
            )
        )
    return rows


def export(database, output):
    """No app connection, migrations, projection writes, provider calls or audio IO."""
    counts = Counter()
    rows = []
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        # A single read transaction keeps current membership and evidence consistent.
        connection.execute("BEGIN")
        runs = connection.execute("""SELECT r.* FROM ingest_runs r WHERE NOT EXISTS (
            SELECT 1 FROM ingest_members mine JOIN ingest_members later ON later.book_id=mine.book_id
            JOIN ingest_runs newer ON newer.id=later.run_id WHERE mine.run_id=r.id AND newer.created>r.created) ORDER BY r.id""").fetchall()
        for run in runs:
            events = [
                dict(e, data=json.loads(e["data"]))
                for e in connection.execute("SELECT * FROM ingest_events WHERE run_id=? ORDER BY id", (run["id"],))
            ]
            approval = next(
                (e["data"] for e in reversed(events) if e["stage"] == "metadata_approval" and e["status"] == "done"),
                None,
            )
            counts[
                "manual_approvals"
                if approval and not approval.get("automatic")
                else "automatic_approvals"
                if approval
                else "unapproved"
            ] += 1
            rows.extend(candidate_rows(dict(run, events=events)))
    with Path(output).open("x", encoding="utf8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    return dict(counts, candidates=len(rows), labels=0)


def precision_bound(correct, total, confidence=0.95):
    """Exact one-sided bound for an independently audited, zero-error batch."""
    if not 0 < confidence < 1 or type(total) is not int or total < 0 or correct != total:
        raise ValueError("requires zero errors, a nonnegative sample count and confidence between zero and one")
    return math.pow(1 - confidence, 1 / total) if total else 0.0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.database, args.out)))


if __name__ == "__main__":
    main()
