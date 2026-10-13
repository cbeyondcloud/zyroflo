"""A directory that can be entered but not listed - the state T611 and review B14-1 are about.

Shared by the walk's tests, the build's and the workspace report's. Whether a refusal binds is
measured, never assumed: root lists through the mode bits (the Linux gate runs unprivileged),
so a test branches on `lists()` rather than skipping."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def deny_listing(directory: Path) -> None:
    """Refuse to list `directory` while it can still be entered: an ACL on Windows, the
    mode bits on POSIX (`--x` for all but the owner's write)."""
    if os.name == "nt":
        done = subprocess.run(["icacls", str(directory), "/deny", "*S-1-1-0:(RD)"],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr
    else:
        directory.chmod(0o311)


def allow_listing(directory: Path) -> None:
    if os.name == "nt":
        subprocess.run(["icacls", str(directory), "/remove:d", "*S-1-1-0"],
                       capture_output=True, text=True)
    else:
        directory.chmod(0o755)


def lists(directory: Path) -> bool:
    try:
        os.listdir(directory)
    except PermissionError:
        return False
    return True
