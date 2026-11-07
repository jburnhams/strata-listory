import gzip
import json


def row(kind, key, data): return f'{kind}\t{key}\t1\t2026-01-01\t{json.dumps(data)}\n'


def dumps(tmp_path):
    d = tmp_path / 'dumps'; d.mkdir()
    def write(name, rows):
        with gzip.open(d / f'{name}.txt.gz', 'wt', encoding='utf8') as f: f.writelines(rows)
    write('authors', [row('/type/author', '/authors/OL1A', dict(name='J. R. R. Tolkien', alternate_names=['Tolkien, J.R.R.', 'John Ronald Reuel Tolkien']))])
    write('works', [row('/type/work', '/works/OL1W', dict(title='The Hobbit', subtitle='or There and Back Again', authors=[dict(author=dict(key='/authors/OL1A'))], first_publish_date='1937', subjects=['Fantasy'])),
                    row('/type/redirect', '/works/OL2W', dict(location='/works/OL1W')), 'bad line\n', row('/type/work', '/works/OL3W', dict(title=''))])
    write('editions', [row('/type/edition', '/books/OL1M', dict(title='The Hobbit', works=[dict(key='/works/OL1W')], isbn_10=['0261102214'], isbn_13=['9780261102217'], publish_date='1999', languages=[dict(key='/languages/eng')], series=['Middle-earth'], identifiers=dict(amazon=['B002V0QK4C']), other_titles=['Der Hobbit'])),
                       row('/type/edition', '/books/OL2M', dict(title='Le Hobbit', works=[dict(key='/works/OL1W')], languages=[dict(key='/languages/fre')])),
                       row('/type/edition', '/books/OL3M', dict(title='The Hobbit', subtitle='Audio', works=[dict(key='/works/OL1W')], physical_format='Audio CD', publish_date='1991')),
                       row('/type/edition', '/books/OL4M', dict(title='Orphan'))])
    write('ratings', ['/works/OL1W\t/books/OL1M\t5\t2020\n', '/works/OL1W\t/books/OL1M\t4\t2020\n', '/works/OL1W\t\t9\t2020\n'])
    return d


def built(tmp_path):
    """A tiny published identification index (one work, The Hobbit) for candidate/feature tests."""
    from app.identify import olindex
    out = tmp_path / 'identify.sqlite'
    olindex.build(dumps(tmp_path), workers=0, output=out)
    return out
