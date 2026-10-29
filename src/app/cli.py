"""Command line: `doctor` checks the environment, `add`/`list`/`show`/`edit`/`remove` manage the audiobook catalogue, `set` changes settings, `serve` starts the web server."""
import argparse, importlib, json, sys

from app import __version__, catalogue, settings

CHECKS = (('fastapi', 'fastapi'), ('uvicorn', 'uvicorn'), ('pydantic', 'pydantic'))


def doctor(args):
    ok = True; print(f'app {__version__}, python {sys.version.split()[0]}')
    for label, mod in CHECKS:
        try: m = importlib.import_module(mod); print(f'  ok       {label} {getattr(m, "__version__", "")}'.rstrip())
        except ImportError: ok = False; print(f'  MISSING  {label}: pip install -r requirements.txt')
    return 0 if ok else 1


def _fmt_duration(sec):
    return '' if not sec else f'{sec // 3600}h{sec % 3600 // 60:02d}'


def _line(b):
    series = f" [{b['series']} #{b['series_position']:g}]" if b.get('series') and b.get('series_position') is not None else (f" [{b['series']}]" if b.get('series') else '')
    return f"  #{b['id']:<4} {b['title']}{series} - {b['author'] or 'unknown author'}  {_fmt_duration(b['duration_seconds'])}  ({b['status']})".rstrip()


def _fields(args):
    return {k: v for k in catalogue.EDITABLE if (v := getattr(args, k, None)) is not None}


def _run(fn, *a, **kw):
    try: return fn(*a, **kw)
    except catalogue.CatalogueError as e: sys.exit(str(e))
    except catalogue.NotFound: sys.exit('no such book')


def cmd_add(args):
    b = _run(catalogue.add_book, **_fields(args))
    print(json.dumps(b) if args.json else _line(b)); return 0


def cmd_list(args):
    books = catalogue.list_books(args.query or '', args.status)
    if args.json: print(json.dumps(books, indent=1)); return 0
    if not books: print('  (no books yet: ./app add "Title" --author "Name")'); return 0
    for b in books: print(_line(b))
    return 0


def cmd_show(args):
    print(json.dumps(_run(catalogue.get_book, args.id), indent=1)); return 0


def cmd_edit(args):
    b = _run(catalogue.update_book, args.id, **_fields(args))
    print(json.dumps(b) if args.json else _line(b)); return 0


def cmd_remove(args):
    b = _run(catalogue.remove_book, args.id)
    print(f"removed #{b['id']} {b['title']}"); return 0


def cmd_set(args):
    if args.value is None: print(f'{args.key} = {settings.all_settings().get(args.key)}'); return 0
    try: settings.save({args.key: None if args.value == 'default' else args.value})
    except KeyError as e: sys.exit(str(e))
    print(f'{args.key} = {settings.all_settings()[args.key]}'); return 0


def cmd_serve(args):
    from app.server import app as server_app
    server_app.main(['--host', args.host, '--port', str(args.port)]); return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog='app'); sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('doctor', help='check the environment').set_defaults(fn=doctor)
    def book_fields(sp, edit=False):
        sp.add_argument('--title' if edit else 'title', **(dict(help='new title') if edit else {})); sp.add_argument('--author'); sp.add_argument('--narrator'); sp.add_argument('--series'); sp.add_argument('--series-position', dest='series_position', type=float)
        sp.add_argument('--duration-seconds', dest='duration_seconds', type=int); sp.add_argument('--path', help='where the audio files live'); sp.add_argument('--status', choices=catalogue.STATUSES); sp.add_argument('--json', action='store_true')
    ad = sub.add_parser('add', help='add a book to the catalogue'); book_fields(ad); ad.set_defaults(fn=cmd_add)
    ls = sub.add_parser('list', help='list the catalogue'); ls.add_argument('query', nargs='?', help='match title, author, narrator or series'); ls.add_argument('--status', choices=catalogue.STATUSES); ls.add_argument('--json', action='store_true'); ls.set_defaults(fn=cmd_list)
    sh = sub.add_parser('show', help='show one book as JSON'); sh.add_argument('id', type=int); sh.set_defaults(fn=cmd_show)
    ed = sub.add_parser('edit', help='change fields of a book'); ed.add_argument('id', type=int); book_fields(ed, edit=True); ed.set_defaults(fn=cmd_edit)
    rm = sub.add_parser('remove', help='remove a book from the catalogue (audio files are never touched)'); rm.add_argument('id', type=int); rm.set_defaults(fn=cmd_remove)
    s = sub.add_parser('set', help='show or change a setting (data_dir, library_dir); value "default" resets it'); s.add_argument('key'); s.add_argument('value', nargs='?'); s.set_defaults(fn=cmd_set)
    v = sub.add_parser('serve', help='start the web server'); v.add_argument('--host', default='127.0.0.1'); v.add_argument('--port', type=int, default=8360); v.set_defaults(fn=cmd_serve)
    a = p.parse_args(argv); return a.fn(a)
