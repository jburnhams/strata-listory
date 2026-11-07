"""Stage 2: model features for one (local item, candidate work) pair.

Every normalisation is a feature, not a rule: the model learns how much each is worth, including that a missing field is different from a mismatching one. No feature is a veto and none encodes an old acceptance gate."""
import difflib
import math
import re

from app.identify import textkeys as tk

KINDS = dict(work=0, work_full=1, edition=2, edition_full=3, full_title=4, other_title=5)
VIA = ('isbn', 'title+author', 'title+surname', 'title', 'author-fuzzy')


def _ratio(a, b): return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.


def _numbers(text): return set(re.findall(r'\d+', text))


def _log(value): return math.log1p(max(value or 0, 0))


def title_features(local_title, c):
    """Match of the local title against all titles the work has ever had."""
    variants = tk.title_variants(local_title); full = variants[0] if variants else ''; compact = full.replace(' ', ''); main = tk.title_main(local_title)
    titles = c['titles']; editions = max(c['editions'], 1)
    def support(keys): return sum(n for (norm, kind), n in titles.items() if norm in keys and kind >= KINDS['edition'])
    exact = {k for (norm, k) in titles if norm == full}; exact_main = {k for (norm, k) in titles if norm == main}
    agree = support({full}); agree_main = support({main})
    names = list({norm for norm, _ in titles}); sims = [(_ratio(full, n), n) for n in names]; best, best_title = max(sims, default=(0., ''))
    main_best = max((_ratio(main, tk.title_main(n)) for n in names), default=0.)
    tokens = set(full.split()); jac = max((len(tokens & set(n.split())) / len(tokens | set(n.split())) for n in names if tokens | set(n.split())), default=0.)
    local_numbers = _numbers(full); other_numbers = _numbers(best_title)
    return dict(
        title_known=int(bool(full)), title_words=len(full.split()), title_has_subtitle=int(main != full),
        title_exact=int(bool(exact)), title_exact_compact=int(any(norm.replace(' ', '') == compact for norm, _ in titles) and bool(compact)), title_exact_main=int(bool(exact_main)),
        **{f'title_exact_kind_{name}': int(code in exact) for name, code in KINDS.items()},
        title_best_sim=best, title_main_best_sim=main_best, title_token_jaccard=jac, title_length_ratio=min(len(full), len(best_title)) / max(len(full), len(best_title), 1),
        title_numbers_agree=int(local_numbers == other_numbers), title_numbers_local_only=len(local_numbers - other_numbers), title_numbers_other_only=len(other_numbers - local_numbers),
        title_edition_agree=_log(agree), title_edition_share=min(agree / editions, 1.), title_main_edition_share=min(agree_main / editions, 1.), title_distinct_aliases=_log(len(names)))


def author_features(local_author, c):
    wanted = tk.people(local_author); have = c['authors']
    have_keys = {tk.person_key(a['name']) for a in have}; have_surnames = {tk.surname(a['name']) for a in have}
    matched = [w for w in wanted if set(tk.person_variants(w)) & have_keys]
    surname_matched = [w for w in wanted if tk.surname(w) in have_surnames]
    sim = max((_ratio(tk.person_key(w), h) for w in wanted for h in have_keys), default=0.)
    given = lambda name: tk.person_key(name).split()[:-1]
    initial = [w for w in surname_matched if given(w) and any(given(a['name']) and given(w)[0][0] == given(a['name'])[0][0] for a in have if tk.surname(a['name']) == tk.surname(w))]
    first = [w for w in surname_matched if given(w) and any(given(a['name']) and given(w)[0] == given(a['name'])[0] for a in have if tk.surname(a['name']) == tk.surname(w))]
    return dict(author_known=int(bool(wanted)), author_count=len(wanted), candidate_author_count=len(have), candidate_author_known=int(bool(have)),
                author_match_share=len(matched) / len(wanted) if wanted else 0., author_surname_share=len(surname_matched) / len(wanted) if wanted else 0.,
                author_exact_all=int(bool(wanted) and len(matched) == len(wanted)), author_single_token=int(bool(wanted) and all(len(tk.person_key(w).split()) == 1 for w in wanted)),
                author_initial_share=len(initial) / len(wanted) if wanted else 0., author_first_name_share=len(first) / len(wanted) if wanted else 0., author_candidate_covered=len(matched) / len(have) if have else 0., author_best_sim=sim)


def popularity_features(c, local_year=None):
    year = c.get('year'); series = c.get('series') or []
    return dict(editions_log=_log(c['editions']), audio_editions_log=_log(c['audio_editions']), has_audio_edition=int(c['audio_editions'] > 0), ratings_log=_log(c['ratings']), rating_known=int(c['rating_average'] is not None),
                rating_average=(c['rating_average'] or 0) / 5, year_known=int(bool(year)), year_scaled=((year - 1800) / 300) if year else 0., isbns_log=_log(len(c['isbns'])),
                year_gap=min(abs(local_year - year), 100) / 100 if year and local_year else 0., year_comparable=int(bool(year and local_year)), subjects_known=int(bool(c['subjects'])), series_known=int(bool(series)))


def item_features(item, c, via):
    """All pair features except the within-query context added by `add_context`."""
    isbns = {k for i in item.get('isbns') or [] for k in tk.isbn_keys(i)}
    out = dict(title_features(item.get('title', ''), c), **author_features(item.get('author', ''), c), **popularity_features(c, item.get('year')))
    out.update({f'reading_{r}': int(item.get('reading', 'raw') == r) for r in ('raw', 'stripped', 'author_title', 'title_author')}, title_homonyms_log=_log(c.get('homonyms', 0)), title_is_person=int(bool(c.get('title_is_person'))), title_generic_no_author=int(len(tk.title_key(item.get('title', '')).split()) <= 2 and not tk.people(item.get('author', ''))), isbn_match=int(bool(isbns & set(c['isbns']))), isbn_known=int(bool(isbns)), **{f'via_{v.replace("+", "_").replace("-", "_")}': int(v in via) for v in VIA})
    local_series = tk.title_key(item.get('series', ''))
    out.update(series_match=int(bool(local_series) and local_series in (c.get('series') or [])), local_series_known=int(bool(local_series)))
    return out


def group_key(c):
    """Exact-duplicate key (same title key and author set); `cluster_ids` is the looser, per-query notion actually used for margins and labels."""
    return (tk.title_key(c['title']), tuple(sorted(a['key'] for a in c['authors'])) or ('',))


def cluster_ids(cands):
    """Duplicate catalogue works of one book: same title key and at least one shared author name. Authorless records are never merged (they could be anything)."""
    parent = list(range(len(cands)))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    seen = {}
    for i, c in enumerate(cands):
        title = tk.title_key(c['title'])
        for a in c['authors']:
            j = seen.setdefault((title, tk.person_key(a['name']) or a['key']), i)      # by name: OL often holds several author records for one person
            if j != i: parent[find(i)] = find(j)
    return [find(i) for i in range(len(cands))]


def add_context(rows):
    """Within-query context: how much better is this candidate than the best *different* book? `rows` = [(candidate, features)]; mutates features."""
    if not rows: return rows
    score = lambda f: f['title_best_sim'] * (.5 + .5 * f['author_surname_share']) + f['title_exact'] * .5 + f['isbn_match']
    ids = cluster_ids([c for c, _ in rows]); groups = {}
    for i, (c, f) in zip(ids, rows): groups.setdefault(i, []).append(score(f))
    best = {k: max(v) for k, v in groups.items()}; top = max(best.values()); max_editions = max(c['editions'] for c, _ in rows) or 1
    exact_groups = len({i for i, (c, f) in zip(ids, rows) if f['title_exact'] and f['author_match_share'] > 0})
    distinct_exact = len({i for i, (c, f) in zip(ids, rows) if f['title_exact']})
    for i, (c, f) in zip(ids, rows):
        mine = best[i]; others = [v for k, v in best.items() if k != i]
        f.update(query_candidates=_log(len(rows)), query_groups=_log(len(groups)), group_size=len(groups[i]), score_margin=mine - max(others, default=0.), is_top_group=int(mine >= top),
                 exact_title_groups=distinct_exact, exact_title_author_candidates=exact_groups, editions_relative=c['editions'] / max_editions)
    return rows


FEATURES = None


def feature_names():
    """Stable column order, derived from a synthetic row so the list never drifts from the code."""
    global FEATURES
    if FEATURES is None:
        c = dict(key='/works/OL0W', title='a b', subtitle='', year=2000, editions=3, audio_editions=1, ratings=2, rating_average=4., authors=[dict(key='/authors/OL0A', name='a b')], titles={('a b', 0): 1}, series=[], isbns=[], subjects=[])
        FEATURES = sorted(add_context([(c, item_features(dict(title='a b', author='a b'), c, {'title'}))])[0][1])
    return FEATURES
