"""Command line: `doctor` checks the environment, `add`/`list`/`show`/`edit`/`remove` manage the audiobook catalogue, `set` changes settings, `serve` starts the web server."""
import argparse, importlib, json, os, sys

from app import __version__, backup, catalogue, db, details, enrich, fs, matcher, mirror, providers, scan, settings

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
    if not fs.reachable(root): sys.exit(f'{root} is on a network share that is not reachable (is the server up?)')
    if not os.path.isdir(root): sys.exit(f'not a folder: {root}')
    try: found = list(scan.scan(root))
    except OSError as e: sys.exit(f'the scan was interrupted ({e.strerror or e}); nothing was changed')
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


def cmd_use_library(args):
    new = os.path.abspath(os.path.expanduser(args.folder)); old = os.path.abspath(os.path.expanduser(args.old)) if args.old else settings.library_dir()
    if not old: sys.exit('no current audiobook folder: give --from')
    if not os.path.isdir(new) or not os.listdir(new): sys.exit(f'{new} is not a folder with files in it')
    paths = catalogue.paths_under(old); missing = [p for p in paths if not os.path.exists(new + p[len(old.rstrip(os.sep)):])]
    print(f'{len(paths)} books live under {old}; {len(paths) - len(missing)} of them exist under {new}')
    if missing and not args.force:
        for p in missing[:5]: print('  missing:', p[len(old.rstrip(os.sep)):])
        sys.exit(f'{len(missing)} are missing in the new folder (has the copy finished? audio-sync status); use --force to switch anyway')
    backup.backup('pre-rebase')
    n = catalogue.rebase_paths(old, new); settings.save(dict(library_dir=new))
    print(f'{n} book paths moved to {new}; library_dir is now {new}. Restart the app (systemctl --user restart strata-listory) and check APP_LIBRARY in its unit.'); return 0


def _mb(n): return f'{n / 1e6:,.0f} MB' if n < 1e9 else f'{n / 1e9:,.2f} GB'


def _plan_line(p):
    tiers = {}
    for c in p['chapters']: tiers[c['tier']] = tiers.get(c['tier'], 0) + 1
    t = ' '.join(f'{k}x{v}' for k, v in sorted(tiers.items()))
    return f"#{p['book_id']:<5} {p['title'][:38]:38} {p['duration'] / 3600:5.1f} h  {len(p['chapters']):3} ch ({p['chapter_source']})  {t:14} {p['loudness']['I']:6.1f} -> {p['loudness']['I'] + p['loudness']['apply_db']:6.1f} LUFS  {_mb(p['in_bytes'])} -> ~{_mb(p['est_bytes'])}" + (f"  !! {'; '.join(p['warnings'])}" if p['warnings'] else '')


def _books_for(args):
    if args.ids:
        books = [_run(catalogue.get_book, i) for i in args.ids]
        for b in books:
            if b['review'] != 'confirmed': print(f"note: #{b['id']} {b['title']} is not confirmed ({b['review']}); processing it because you named it")
        return books
    return []


def _one_plan(b, final):
    from app.process import plan as P
    side = details.sidecar(b['path']) if b['path'] and os.path.exists(b['path']) else None
    return P.make_plan(b, final, details.audible_summary(side) if side else None)


def cmd_process(args):
    from app.process import probe, run as R
    final = os.path.abspath(os.path.expanduser(args.out)) if args.out else settings.final_dir()
    books = _books_for(args)
    if not books: print('nothing named: propose plans with `./app process-propose`, approve them in the app or with `./app process-approve`, then `./app process-run`. (`./app process ID...` processes named books directly; --plan only shows plans.)'); return 0
    for b in books:
        if not b['path'] or not os.path.exists(b['path']) or not fs.reachable(b['path']): sys.exit(f"#{b['id']} {b['title']}: its files are not available at {b['path'] or '(no path)'}")
    plans, failed = {}, 0
    def plan_one(b):
        try: return b['id'], _one_plan(b, final)
        except (probe.ToolError, OSError) as e: return b['id'], str(e)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        for bid, p in ex.map(plan_one, books):
            if isinstance(p, str): failed += 1; print(f'#{bid}: cannot plan: {p}')
            else: plans[bid] = p; print(_plan_line(p)) if args.plan or not args.json else None
    if args.json: print(json.dumps(plans, indent=1)); return 1 if failed else 0
    tot_i = sum(p['in_bytes'] for p in plans.values()); tot_o = sum(p['est_bytes'] for p in plans.values())
    print(f"{len(plans)} books, {_mb(tot_i)} -> about {_mb(tot_o)} ({tot_o / tot_i:.0%})" if tot_i else 'nothing planned')
    if args.plan: return 1 if failed else 0
    def go(b):
        try:
            res = R.execute(b, plans[b['id']], args.force); catalogue.set_processed(b['id'], res)
            return b['id'], res, None
        except (probe.ToolError, OSError) as e:
            catalogue.set_processed(b['id'], dict(status='failed', error=str(e), finished=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat())); return b['id'], None, str(e)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        for bid, res, err in ex.map(go, [b for b in books if b['id'] in plans]):
            if err: failed += 1; print(f'#{bid}: FAILED: {err}')
            else: print(f"#{bid}: done in {res['seconds']:.0f}s: {_mb(res['in_bytes'])} -> {_mb(res['out_bytes'])}, loudness {res['loudness_before']:.1f} -> {res['loudness_after']:.1f} LUFS, {res['out_dir']}" + (f"  !! {'; '.join(res['warnings'])}" if res['warnings'] else ''))
    return 1 if failed else 0


def cmd_process_propose(args):
    from app.process import queue as pq
    res = pq.propose(args.limit, args.jobs); print(f"proposed {res['planned']} plans, {res['failed']} could not be planned; {res['remaining']} more confirmed books could be proposed. Review with `./app process-queue`, approve in the app (Processing page) or with `./app process-approve ID...`"); return 0


def cmd_process_queue(args):
    from app.process import queue as pq
    rows = catalogue.list_plans([args.state] if args.state else None)
    if not rows: print('  (the queue is empty: ./app process-propose)'); return 0
    for r in rows:
        if not r['plan']: print(f"  #{r['book_id']:<5} {r['state']:9} {r['title'][:40]}" + (f"  {r['error']}" if r['error'] else '')); continue
        sm = pq.summary(r['plan']); t = ' '.join(f'{k}x{v}' for k, v in sorted(sm['tiers'].items()))
        print(f"  #{r['book_id']:<5} {r['state']:9} {r['title'][:36]:36} {sm['hours']:5.1f} h {sm['chapters']:3} ch {t:12} {sm['loudness_before']:6.1f} -> {sm['loudness_after_est']:6.1f} LUFS  {_mb(sm['in_bytes'])} -> ~{_mb(sm['est_bytes'])}" + (f"  !! {'; '.join(sm['warnings'])}" if sm['warnings'] else '') + (f"  {r['error']}" if r['error'] else ''))
    return 0


def cmd_process_decide(args):
    from app.process import queue as pq
    changed = pq.decide(args.ids, args.approve); print(f"{'approved' if args.approve else 'rejected'}: {', '.join('#%d' % i for i in changed) or 'none'}" + (f" (not changed: {', '.join('#%d' % i for i in args.ids if i not in changed)}; only proposed plans can be decided)" if len(changed) < len(args.ids) else '')); return 0


def cmd_process_run(args):
    from app.process import queue as pq
    if not catalogue.list_plans(('approved',)): print('nothing is approved yet: `./app process-queue`, then `./app process-approve ID...`'); return 0
    res = pq.run_approved(args.jobs); print(f"processed {res['done']}, failed {res['failed']}; see `./app process-queue`"); return 1 if res['failed'] else 0


def cmd_process_status(args):
    s = catalogue.processed_summary()
    print(f"{s['done']} books processed ({s['seconds'] / 3600:.0f} h of audio): {_mb(s['in_bytes'])} -> {_mb(s['out_bytes'])}" + (f" ({s['out_bytes'] / s['in_bytes']:.0%})" if s['in_bytes'] else '') + f"; {s['failed']} failed; {len(catalogue.pending_process(100000))} confirmed books still to do; final library {settings.final_dir()}"); return 0


def cmd_mirror(args):
    books = catalogue.list_books(review='confirmed') if not args.all else catalogue.list_books()
    n = mirror.write_all(books)
    print(f'wrote metadata.json for {n} of {len(books)} books under {mirror.root()}'); return 0


def cmd_enrich(args):
    try:
        if args.id is not None:
            data = enrich.enrich_book(_run(catalogue.get_book, args.id))
            print(f"#{args.id}: " + (f"Hardcover {data['rating'] or 0:.2f} from {data['ratings_count']} ratings, {data['reviews_count']} reviews, {len(data['reviews'])} stored" if data else 'not found on Hardcover')); return 0
        res = enrich.enrich_pending(args.limit)
    except matcher.MatchError as e: sys.exit(str(e))
    print(f"tried {res['tried']}: {res['enriched']} enriched, {res['not_found']} not found on Hardcover; {res['remaining']} confirmed books still to do"); return 0


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
    ul = sub.add_parser('use-library', help='switch the audiobook folder (for example to the local copy), moving every book path with it'); ul.add_argument('folder'); ul.add_argument('--from', dest='old', help='the folder the paths currently live under (default: library_dir)'); ul.add_argument('--force', action='store_true', help='switch even if some books are missing in the new folder'); ul.set_defaults(fn=cmd_use_library)
    pr = sub.add_parser('process', help='turn books into the final library: mono mp3 chapters, normalised loudness (--plan shows what would happen)'); pr.add_argument('ids', nargs='*', type=int, help='book ids (default: confirmed books not yet processed)'); pr.add_argument('--plan', action='store_true', help='measure and print the plan, change nothing'); pr.add_argument('--json', action='store_true'); pr.add_argument('--limit', type=int, default=5); pr.add_argument('--jobs', type=int, default=3); pr.add_argument('--force', action='store_true', help='replace an existing output folder'); pr.add_argument('--out', help='final library folder (default: the final_dir setting, ~/audiobooks)'); pr.set_defaults(fn=cmd_process)
    pp = sub.add_parser('process-propose', help='plan eligible confirmed books and put the plans in the approval queue (nothing is processed)'); pp.add_argument('--limit', type=int, default=5); pp.add_argument('--jobs', type=int, default=2); pp.set_defaults(fn=cmd_process_propose)
    pq_ = sub.add_parser('process-queue', help='show the approval queue'); pq_.add_argument('--state'); pq_.set_defaults(fn=cmd_process_queue)
    pa = sub.add_parser('process-approve', help='approve proposed plans'); pa.add_argument('ids', nargs='+', type=int); pa.set_defaults(fn=cmd_process_decide, approve=True)
    pj = sub.add_parser('process-reject', help='reject proposed plans (they are not proposed again)'); pj.add_argument('ids', nargs='+', type=int); pj.set_defaults(fn=cmd_process_decide, approve=False)
    prn = sub.add_parser('process-run', help='process the approved plans'); prn.add_argument('--jobs', type=int, default=2); prn.set_defaults(fn=cmd_process_run)
    sub.add_parser('process-status', help='how much of the library is processed').set_defaults(fn=cmd_process_status)
    mr = sub.add_parser('mirror', help='rewrite metadata.json in the data mirror (<data_dir>/library) for confirmed books'); mr.add_argument('--all', action='store_true', help='every book, not only confirmed ones'); mr.set_defaults(fn=cmd_mirror)
    en = sub.add_parser('enrich', help='fetch Hardcover ratings, reviews and other community data for confirmed books'); en.add_argument('id', nargs='?', type=int, help='one book (refetched even if recent); default: confirmed books without recent data'); en.add_argument('--limit', type=int, default=25); en.set_defaults(fn=cmd_enrich)
    sub.add_parser('providers', help='list the metadata providers and whether each can run').set_defaults(fn=cmd_providers)
    mx = sub.add_parser('match-test', help='compare the providers on a sample of scanned books without recording anything'); mx.add_argument('--limit', type=int, default=50); mx.add_argument('--stride', type=int, default=1, help='take every Nth book, to spread the sample'); mx.add_argument('--providers'); mx.set_defaults(fn=cmd_match_test)
    cf = sub.add_parser('confirm', help='accept a book, applying its suggestion if it has one'); cf.add_argument('id', type=int); cf.set_defaults(fn=cmd_confirm)
    ig = sub.add_parser('ignore', help='mark a scanned item as not wanted'); ig.add_argument('id', type=int); ig.set_defaults(fn=cmd_ignore)
    sub.add_parser('backup', help='back up the catalogue database now').set_defaults(fn=cmd_backup)
    sub.add_parser('backups', help='list the automatic backups').set_defaults(fn=cmd_backups)
    rs = sub.add_parser('restore', help='replace the catalogue with a backup (default: the newest); the current one is kept'); rs.add_argument('backup', nargs='?', help='path from `backups`'); rs.set_defaults(fn=cmd_restore)
    s = sub.add_parser('set', help='show or change a setting (data_dir, library_dir, backup_dir, match_providers, audible_region, final_dir); value "default" resets it'); s.add_argument('key'); s.add_argument('value', nargs='?'); s.set_defaults(fn=cmd_set)
    v = sub.add_parser('serve', help='start the web server'); v.add_argument('--host', default='127.0.0.1'); v.add_argument('--port', type=int, default=8360); v.set_defaults(fn=cmd_serve)
    a = p.parse_args(argv)
    try: return a.fn(a)
    except db.CorruptDatabase as e: sys.exit(str(e))
