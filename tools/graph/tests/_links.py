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


def loop(at: Path, scratch: Path) -> list[Path]:
    """A directory link at `at` that never ends, and every link made for it, in the order to
    remove them: on POSIX a symlink to itself; on Windows, where a junction refuses a target that
    does not exist and an unprivileged symlink is refused, a chain of junctions longer than the
    system resolves (63 resolve, 64 answer ERROR_CANT_RESOLVE_FILENAME - measured, unprivileged),
    each made onto one that exists, the chain kept in `scratch`."""
    if os.name != "nt":
        os.symlink(at, at, target_is_directory=True)
        return [at]
    target = scratch / "real"
    target.mkdir(parents=True)
    made = []
    for i in range(70):
        made.append(scratch / f"j{i:03d}")
        link(target, made[-1])
        target = made[-1]
    link(target, at)
    return [at, *reversed(made)]


def remove(links: list[Path]) -> None:
    for at in links:
        unlink(at)
