"""One cross-process lock, shared by everything under `live/` that needs one.

Extracted when `live.health_check` needed the same serialisation
`live.sync_live_signals` had just grown. Copying the helper would have
been two implementations of one thing to get wrong -- which is the
argument that put `flock` in the sync module in the first place, so
duplicating it there would have contradicted its own reasoning within a
day.

`flock` specifically, rather than a lock-free scheme or a PID file,
because `scripts/paper-trading-daily-signal.sh` and
`live.generate_mock_signal` already serialise this way. A second
mechanism for one problem is a second thing to get wrong.
"""

from __future__ import annotations

import contextlib
import fcntl
from pathlib import Path


@contextlib.contextmanager
def exclusive_lock(path: Path | str):
    """Serialise a read-modify-write on `path` across processes.

    Locks a **sidecar** `<name>.lock`, never `path` itself. Both callers
    publish their result with `os.replace`, which swaps the inode -- a
    lock held on the old one would protect nothing the moment the rename
    lands.

    **Blocking, not `LOCK_NB`.** A caller that waits its turn and then
    re-reads is correct; one that gave up on contention would turn "two
    things ran at once" into an error, when waiting a few milliseconds is
    the whole answer.

    The lock file is created if absent and deliberately never removed:
    unlinking it while another process holds a descriptor on it would
    hand the next caller a fresh inode and no mutual exclusion at all.
    """
    path = Path(path)
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        # Released implicitly on close; explicit so the ordering is
        # visible rather than inferred -- unlock, then close.
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
