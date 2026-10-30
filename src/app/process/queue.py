"""The processing queue, with a person in the loop: plans are *proposed* for confirmed books, a person *approves* or *rejects* each (the Processing page or the CLI), and only approved plans are run. Nothing is processed automatically.

States (catalogue.PLAN_STATES): planning -> proposed -> approved -> running -> done | failed; a proposal can also be rejected (and is then not proposed again). A plan is the exact thing that was shown: before it runs, the inbox files are compared with what was planned and a changed book fails with a request to propose it again."""
import os, queue as _queue, threading
from concurrent.futures import ThreadPoolExecutor

from app import catalogue, details, fs, settings
from app.process import plan as P, probe, run as R


def summary(plan: dict) -> dict:
    """What the Processing page shows for a plan (the full chapter list is shown on demand)."""
    tiers: dict[str, int] = {}
    for c in plan['chapters']: tiers[c['tier']] = tiers.get(c['tier'], 0) + 1
    l = plan['loudness']
    return dict(hours=round(plan['duration'] / 3600, 2), files=plan['in_files'], chapters=len(plan['chapters']), chapter_source=plan['chapter_source'], tiers=tiers, in_bytes=plan['in_bytes'], est_bytes=plan['est_bytes'], out_dir=plan['out_dir'],
                loudness_before=l['I'], loudness_after_est=round(l['I'] + l['apply_db'], 1), decision=l['decision'], reencoded=l['reencoded'], warnings=plan['warnings'], downmix=plan['downmix'],
                metadata=plan.get('metadata', []), year=plan.get('year'),
                chapter_list=[dict(file=c['file'], title=c['title'], minutes=round(c['duration'] / 60, 1), mode=c['mode'], tier=c['tier']) for c in plan['chapters']])


def _plan_one(book: dict, final_dir: str) -> dict:
    side = details.sidecar(book['path']) if os.path.exists(book['path']) else None
    return P.make_plan(book, final_dir, details.audible_summary(side) if side else None)


def propose(limit: int = 5, jobs: int = 2) -> dict:
    """Plan up to `limit` eligible books that have no proposal yet; each becomes `proposed` (or `failed` with the reason)."""
    final = settings.final_dir(); books = catalogue.pending_propose(limit)
    for b in books: catalogue.set_plan(b['id'], 'planning')
    def one(b):
        try:
            if not fs.reachable(b['path']) or not os.path.exists(b['path']): raise probe.ToolError(f"its files are not available at {b['path']}")
            catalogue.set_plan(b['id'], 'proposed', _plan_one(b, final)); return True
        except (probe.ToolError, OSError) as e:
            catalogue.set_plan(b['id'], 'failed', error=str(e)); return False
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex: ok = list(ex.map(one, books))
    return dict(planned=sum(ok), failed=len(ok) - sum(ok), remaining=len(catalogue.pending_propose(100000)))


def decide(ids: list[int], approve: bool) -> list[int]:
    """Approve (proposed or previously rejected plans) or reject (proposed or approved ones, not yet running); returns the ids that changed."""
    allowed = ('proposed', 'rejected') if approve else ('proposed', 'approved'); changed = []
    for i in ids:
        row = catalogue.get_plan(i)
        if row and row['state'] in allowed and row['plan']: catalogue.set_plan(i, 'approved' if approve else 'rejected', keep_plan=True); changed.append(i)
    return changed


def repropose(ids: list[int]) -> list[int]:
    """Throw away the proposals (not running or done ones) so that the books are planned again, for example after fixing metadata."""
    out = []
    for i in ids:
        row = catalogue.get_plan(i)
        if row and row['state'] in ('proposed', 'approved', 'rejected', 'failed'): catalogue.delete_plan(i); out.append(i)
    return out


def _unchanged(book: dict, plan: dict) -> bool:
    files = details.audio_files(book['path'])
    return len(files) == plan['in_files'] and sum(os.path.getsize(f) for f in files) == plan['in_bytes']


def run_approved(jobs: int = 2) -> dict:
    """Process every approved plan; returns {done, failed}."""
    rows = [r for r in catalogue.list_plans(('approved',)) if r['plan']]
    for r in rows: catalogue.set_plan(r['book_id'], 'running', keep_plan=True)
    def one(r):
        book = catalogue.get_book(r['book_id'])
        try:
            if not fs.reachable(book['path']) or not os.path.exists(book['path']): raise probe.ToolError(f"its files are not available at {book['path']}")
            if not _unchanged(book, r['plan']): raise probe.ToolError('the files changed since this plan was proposed: propose it again')
            res = R.execute(book, r['plan'], force=os.path.exists(r['plan']['out_dir']) and (catalogue.get_processed(book['id']) or {}).get('status') == 'failed')
            catalogue.set_processed(book['id'], res); catalogue.set_plan(book['id'], 'done', keep_plan=True); return True
        except (probe.ToolError, OSError, KeyError) as e:
            from datetime import datetime, timezone
            catalogue.set_processed(book['id'], dict(status='failed', error=str(e), finished=datetime.now(timezone.utc).isoformat())); catalogue.set_plan(book['id'], 'failed', error=str(e), keep_plan=True); return False
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex: ok = list(ex.map(one, rows))
    return dict(done=sum(ok), failed=len(ok) - sum(ok))


class Worker:
    """One background thread for the server: runs proposals and approved plans one task at a time so long jobs never block a request. `inline` (tests) runs tasks on the spot."""
    inline = False

    def __init__(self):
        self.q: _queue.Queue = _queue.Queue(); self.busy: str | None = None; self.last: dict = {}; self._thread: threading.Thread | None = None; self._lock = threading.Lock()

    def submit(self, kind: str, **kw):
        if self.inline: self._do(kind, kw); return
        self.q.put((kind, kw))
        with self._lock:
            if not self._thread or not self._thread.is_alive(): self._thread = threading.Thread(target=self._loop, daemon=True, name='process-worker'); self._thread.start()

    def _do(self, kind: str, kw: dict):
        self.busy = kind
        try: self.last = {kind: (propose if kind == 'propose' else run_approved)(**kw)}
        except Exception as e: self.last = {kind: dict(error=str(e))}      # a task must never kill the worker
        finally: self.busy = None

    def _loop(self):
        while True:
            try: kind, kw = self.q.get(timeout=30)
            except _queue.Empty: return
            self._do(kind, kw)

    def status(self) -> dict:
        return dict(busy=self.busy, queued=self.q.qsize(), last=self.last, counts=catalogue.plan_counts())
