"""A file made really unreadable for a door spawned against it, the premise asserted (batch 22):
an exclusive byte-range lock held by a child process on Windows, mode 000 on Linux - and where
the suite runs as root, which mode 000 does not stop, the door runs as `nobody` in a directory
outside pytest's `0o700` temp root, handed over to it. Branched and asserted, never skipped."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

#: The unprivileged user a door runs as where the suite runs as root.
NOBODY = 65534
#: Holds an exclusive lock on every byte of the file it is handed until its stdin closes.
LOCKER = ("import msvcrt, os, sys\n"
          "f = open(sys.argv[1], 'r+b')\n"
          "msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, os.path.getsize(sys.argv[1]))\n"
          "print('locked', flush=True)\n"
          "sys.stdin.read()\n")


def as_root() -> bool:
    return os.name != "nt" and os.geteuid() == 0


def door_kwargs() -> dict:
    """What a spawned door needs to meet the file as unreadable: `user=` where we are root."""
    return {"user": NOBODY, "group": NOBODY} if as_root() else {}


@contextmanager
def site(tmp_path: Path):
    """Where a test lays its tree: pytest's temp dir, or - as root, the door running as
    `nobody` - a directory outside pytest's `0o700` temp root."""
    if not as_root():
        yield tmp_path
        return
    base = Path(tempfile.mkdtemp(prefix="b22_unreadable_", dir="/tmp"))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def hand_over(where: Path) -> None:
    """As root, the tree becomes `nobody`'s, so the door can read and write it."""
    if as_root():
        for path in [where, *where.rglob("*")]:
            os.chown(path, NOBODY, NOBODY, follow_symlinks=False)


@contextmanager
def unreadable(path: Path):
    """`path` really unreadable while the block runs, the premise asserted as the door's user:
    the read fails."""
    if os.name == "nt":
        locker = subprocess.Popen([sys.executable, "-c", LOCKER, str(path)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            assert locker.stdout.readline().strip() == "locked"
            with pytest.raises(PermissionError):
                path.read_bytes()
            yield
        finally:
            locker.stdin.close()
            locker.wait(timeout=60)
        return
    mode = path.stat().st_mode
    os.chmod(path, 0)
    try:
        probe = subprocess.run([sys.executable, "-c", f"open({str(path)!r}, 'rb').read()"],
                               capture_output=True, text=True, **door_kwargs())
        assert probe.returncode != 0 and "PermissionError" in probe.stderr, (
            probe.returncode, probe.stderr)
        yield
    finally:
        os.chmod(path, mode)
