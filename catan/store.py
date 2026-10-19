"""A save file shared by several processes.

The web UI (`catan serve`) and one or more agents (`catan do`, `catan wait`)
can all act on the same game. Every read-modify-write happens under an
exclusive lock, and saves are atomic, so moves never interleave or get lost.
"""

from __future__ import annotations

import contextlib
import os
import threading

from .game import Game

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        msvcrt = None

_in_process = threading.RLock()


class GameStore:
    def __init__(self, path: str):
        self.path = path
        folder, base = os.path.split(os.path.abspath(path))
        self.lock_path = os.path.join(folder, f".{base}.lock")

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def version(self) -> str | None:
        """Changes on every save (each save is a fresh file via os.replace)."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        return f"{st.st_ino}-{st.st_mtime_ns}-{st.st_size}"

    def load(self) -> Game:
        return Game.load(self.path)

    def save(self, game: Game):
        game.save(self.path)

    @contextlib.contextmanager
    def lock(self):
        """Exclusive access across threads and processes."""
        with _in_process, open(self.lock_path, "a+") as fh:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_EX)
            elif msvcrt:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                if fcntl:
                    fcntl.flock(fh, fcntl.LOCK_UN)
                elif msvcrt:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
