"""Per-job raw-response observer; authentication headers are never observed."""
from contextvars import ContextVar
from contextlib import contextmanager

observer = ContextVar('ingest_observer', default=None)


def emit(**entry):
    fn = observer.get()
    if fn: fn(entry)


@contextmanager
def capture(callback):
    token = observer.set(callback)
    try: yield
    finally: observer.reset(token)


progress_observer=ContextVar('ingest_progress_observer',default=None)


def report(message,**values):
    fn=progress_observer.get()
    if fn:fn(dict(message=message,**values))


@contextmanager
def capture_progress(callback):
    token=progress_observer.set(callback)
    try:yield
    finally:progress_observer.reset(token)
