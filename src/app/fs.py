"""Is a path on a network share reachable right now? The audio library (and possibly the backup folder) lives on an SMB share that can be down; a stat on a dead mount can block for a long time, and a page of thumbnails would pile up many such calls. So every access to those places first asks `reachable`, which probes the mount point from a helper thread with a short timeout and remembers the answer for a few seconds (a down share is re-checked after DOWN_FOR, an up one after UP_FOR; one probe per mount at a time).

Paths on local disks never need probing: only paths under a mount whose type is a network filesystem are checked (read from /proc/self/mountinfo on Linux; elsewhere nothing is probed and errors are handled where they happen)."""
import os, threading, time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

NETWORK = {'cifs', 'smb3', 'smbfs', 'nfs', 'nfs4', 'fuse.sshfs', 'fuse.rclone', 'davfs', '9p', 'afs', 'ceph', 'glusterfs', 'autofs'}      # autofs: a systemd automount that has not mounted yet (touching it mounts the share, which can take a while)
TIMEOUT, UP_FOR, DOWN_FOR = 4.0, 10.0, 15.0

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='fs-probe')
_state: dict[str, tuple[bool, float]] = {}            # mount -> (reachable, checked at)
_inflight: dict[str, object] = {}
_lock = threading.Lock()


def network_mounts() -> list[str]:
    """Mount points of network filesystems, longest first."""
    try:
        out = []
        with open('/proc/self/mountinfo', encoding='utf8') as f:
            for line in f:
                left, _, right = line.partition(' - ')
                if right.split(' ')[0] in NETWORK: out.append(left.split(' ')[4].replace('\\040', ' '))
        return sorted(out, key=len, reverse=True)
    except OSError: return []


def mount_of(path: str) -> str | None:
    """The network mount that holds `path`, or None for a local path."""
    p = os.path.normcase(os.path.abspath(path))
    return next((m for m in network_mounts() if p == os.path.normcase(m) or p.startswith(os.path.normcase(m).rstrip(os.sep) + os.sep)), None)


def _probe(mount: str) -> bool:
    try: os.stat(mount); os.listdir(mount); return True        # listing, not just stat: a dead share can still answer stat from its cache
    except OSError: return False


def reachable(path: str) -> bool:
    """False when `path` is on a network mount that does not answer within TIMEOUT (or recently did not); True otherwise."""
    mount = mount_of(path)
    if mount is None: return True
    now = time.monotonic()
    with _lock:
        ok, at = _state.get(mount, (False, -1e9))
        if now - at < (UP_FOR if ok else DOWN_FOR): return ok
        fut = _inflight.get(mount)
        if fut is None: fut = _inflight[mount] = _pool.submit(_probe, mount)
        elif not fut.done(): return False                      # an earlier probe is still stuck: do not pile up more
    try: ok = fut.result(timeout=TIMEOUT)
    except FutureTimeout: ok = False
    except Exception: ok = False
    with _lock:
        if fut.done(): _inflight.pop(mount, None)
        _state[mount] = (ok, time.monotonic())
    return ok


def forget():
    """Drop what is remembered about mounts (tests, or after the user says the share is back)."""
    with _lock: _state.clear(); _inflight.clear()


class ShareDown(OSError):
    """Raised when a book's files are on a share that is not reachable."""


def require(path: str):
    if not reachable(path): raise ShareDown(f'{path} is on a network share that is not reachable (is the server up?)')
