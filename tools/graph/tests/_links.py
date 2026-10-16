"""A directory link for a test, and its removal: a junction on Windows, which refuses an
unprivileged symlink; a symlink on POSIX, which has no junction. A link is removed by its own
path, never walked into."""
from __future__ import annotations

import os
from pathlib import Path


def link(target: Path, at: Path) -> None:
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(at))
    else:
        os.symlink(target, at, target_is_directory=True)


def unlink(at: Path) -> None:
    os.rmdir(at) if os.name == "nt" else os.unlink(at)
