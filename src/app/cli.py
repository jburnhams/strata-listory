"""Command line: `doctor` checks the environment, `add`/`list`/`show`/`edit`/`remove` manage the audiobook catalogue, `set` changes settings, `serve` starts the web server."""
import argparse, importlib, json, os, sys

from app import __version__, backup, catalogue, db, matcher, mirror, providers, scan, settings

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
    flag = '' if b['review'] == 'confirmed' else f" [{b['review']}" + (f" -> {b['suggestion']['title']} / {b['suggestion']['author']}, {b['match_score']:.2f}" if b.get('suggestion') else '') + ']'
    return f"  #{b['id']:<4} {b['title']}{series} - {b['author'] or 'unknown author'}  {_fmt_duration(b['duration_seconds'])}  ({b['status']}){flag}".rstrip()


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
    books = catalogue.list_books(args.query or '', args.status, args.review, args.kind)
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


def cmd_backup(args):
    p = backup.backup('manual'); print(p or 'no database yet, nothing to back up'); return 0


def cmd_backups(args):
    bs = backup.list_backups()
    if not bs: print('  (no backups yet: ./app backup)'); return 0
    for b in bs: print(f"  {b['taken']:%Y-%m-%d %H:%M:%S}  {b['reason'] or 'daily':12} {b['path']}")
    return 0


def cmd_restore(args):
    path = args.backup or (next(iter(backup.list_backups()), None) or sys.exit('no backups to restore'))['path']
    try: safety = backup.restore(path)
    except ValueError as e: sys.exit(str(e))
    print(f'restored {path}' + (f'; the previous database is kept as {safety}' if safety else '')); return 0


def cmd_scan(args):
    root = args.folder or settings.library_dir() or sys.exit('no audiobook folder: ./app scan /path/to/audio, or ./app set library_dir /path/to/audio')
    if not os.path.isdir(root): sys.exit(f'not a folder: {root}')
    found = list(scan.scan(root))
    kinds = {k: sum(f['kind'] == k for f in found) for k in catalogue.KINDS}
    if args.dry_run: print(f"{len(found)} candidates ({kinds['book']} books, {kinds['radio']} radio); nothing stored (dry run)"); return 0
    res = catalogue.add_scanned(found)
    print(f"{len(found)} candidates ({kinds['book']} books, {kinds['radio']} radio): {res['added']} added, {res['existing']} already known" + (f", {res['replaced']} fragments replaced" if res['replaced'] else '')); return 0


def _chosen(args):
    try: return providers.enabled([n.strip() for n in args.providers.split(',')] if args.providers else None)
    except KeyError as e: sys.exit(str(e.args[0]))


def cmd_match(args):
    try: res = matcher.match_pending(args.limit, _chosen(args))
    except matcher.MatchError as e: sys.exit(str(e))
    print(f"tried {res['tried']}: {res['suggested']} suggestions, {res['none']} without a match; {res['remaining']} still to try (providers: {', '.join(res['providers'])})"); return 0


def cmd_mirror(args):
    books = catalogue.list_books(review='confirmed') if not args.all else catalogue.list_books()
    n = mirror.write_all(books)
    print(f'wrote metadata.json for {n} of {len(books)} books under {mirror.root()}'); return 0


def cmd_providers(args):
    for p in providers.status():
        print(f"  {p['name']:12} {p['label']:18} {'selected #' + str(p['order']) if p['selected'] else 'not selected':14} {'ready' if p['available'] else 'UNAVAILABLE: ' + p['reason']}  (1 request per {p['min_interval']:g}s, cache {p['cache_days']:g} days)")
    return 0


def cmd_match_test(args):
    plist = _chosen(args)
    books = [b for b in catalogue.list_books(kind='book') if b['review'] == 'new'][::max(1, args.stride)][:args.limit]
    if not books: print('no scanned books to test: ./app scan first'); return 0
    try: rows = matcher.evaluate(books, plist)
    except matcher.MatchError as e: sys.exit(str(e))
    print(f'{len(books)} scanned books; nothing is recorded')
    for name, r in rows.items(): print(f"  {name:12} good (>= {matcher.GOOD_SCORE:.2f}) {r['good']:>4} ({r['good'] / len(books):4.0%})   any (>= {matcher.MIN_SCORE:.2f}) {r['some']:>4} ({r['some'] / len(books):4.0%})   errors {r['errors']}")
    return 0


def cmd_confirm(args):
    print(_line(_run(catalogue.confirm, args.id))); return 0


def cmd_ignore(args):
    print(_line(_run(catalogue.ignore, args.id))); return 0


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
    ls = sub.add_parser('list', help='list the catalogue'); ls.add_argument('query', nargs='?', help='match title, author, narrator or series'); ls.add_argument('--status', choices=catalogue.STATUSES); ls.add_argument('--review', choices=catalogue.REVIEWS); ls.add_argument('--kind', choices=catalogue.KINDS); ls.add_argument('--json', action='store_true'); ls.set_defaults(fn=cmd_list)
    sh = sub.add_parser('show', help='show one book as JSON'); sh.add_argument('id', type=int); sh.set_defaults(fn=cmd_show)
    ed = sub.add_parser('edit', help='change fields of a book'); ed.add_argument('id', type=int); book_fields(ed, edit=True); ed.set_defaults(fn=cmd_edit)
    rm = sub.add_parser('remove', help='remove a book from the catalogue (audio files are never touched)'); rm.add_argument('id', type=int); rm.set_defaults(fn=cmd_remove)
    sc = sub.add_parser('scan', help='find audiobooks in a folder (default: the library_dir setting) and add them as new, unreviewed books; never writes to the folder'); sc.add_argument('folder', nargs='?'); sc.add_argument('--dry-run', action='store_true'); sc.set_defaults(fn=cmd_scan)
    mt = sub.add_parser('match', help='look scanned books up with the metadata providers and record suggestions'); mt.add_argument('--limit', type=int, default=25); mt.add_argument('--providers', help='comma separated, in order (default: the match_providers setting)'); mt.set_defaults(fn=cmd_match)
    mr = sub.add_parser('mirror', help='rewrite metadata.json in the data mirror (<data_dir>/library) for confirmed books'); mr.add_argument('--all', action='store_true', help='every book, not only confirmed ones'); mr.set_defaults(fn=cmd_mirror)
    sub.add_parser('providers', help='list the metadata providers and whether each can run').set_defaults(fn=cmd_providers)
    mx = sub.add_parser('match-test', help='compare the providers on a sample of scanned books without recording anything'); mx.add_argument('--limit', type=int, default=50); mx.add_argument('--stride', type=int, default=1, help='take every Nth book, to spread the sample'); mx.add_argument('--providers'); mx.set_defaults(fn=cmd_match_test)
    cf = sub.add_parser('confirm', help='accept a book, applying its suggestion if it has one'); cf.add_argument('id', type=int); cf.set_defaults(fn=cmd_confirm)
    ig = sub.add_parser('ignore', help='mark a scanned item as not wanted'); ig.add_argument('id', type=int); ig.set_defaults(fn=cmd_ignore)
    sub.add_parser('backup', help='back up the catalogue database now').set_defaults(fn=cmd_backup)
    sub.add_parser('backups', help='list the automatic backups').set_defaults(fn=cmd_backups)
    rs = sub.add_parser('restore', help='replace the catalogue with a backup (default: the newest); the current one is kept'); rs.add_argument('backup', nargs='?', help='path from `backups`'); rs.set_defaults(fn=cmd_restore)
    s = sub.add_parser('set', help='show or change a setting (data_dir, library_dir, backup_dir, match_providers, audible_region); value "default" resets it'); s.add_argument('key'); s.add_argument('value', nargs='?'); s.set_defaults(fn=cmd_set)
    v = sub.add_parser('serve', help='start the web server'); v.add_argument('--host', default='127.0.0.1'); v.add_argument('--port', type=int, default=8360); v.set_defaults(fn=cmd_serve)
    a = p.parse_args(argv)
    try: return a.fn(a)
    except db.CorruptDatabase as e: sys.exit(str(e))
