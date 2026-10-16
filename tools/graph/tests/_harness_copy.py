"""A copy of the harness's tracked tree, for tests that change the harness's own code - the
extractor version's file set (batch 22, T617) - and spawn the COPY's `build.py` by path, as an
adopter runs it. The live checkout is never touched: its repo-wide guards read every file.

`copy_harness` holds the tracked `tools/**` and `sdlc-init.py` as the working tree has them,
mtimes kept (`shutil.copy2`, so a copied `.pyc` stays valid), never `.venv`, `third_party` or
`.git`; `sys.executable` supplies the site-packages."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def tracked(paths: tuple[str, ...] = ("tools", "sdlc-init.py")) -> list[str]:
    """The tracked files under `paths`, as git lists them - a listing that fails or comes back
    empty raises, never a copy of nothing."""
    proc = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", *paths],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    names = [name for name in proc.stdout.split("\0") if name]
    if proc.returncode != 0 or not names:
        raise RuntimeError(f"git ls-files could not list the harness (exit {proc.returncode}): "
                           f"{proc.stderr.strip()}")
    return names


def copy_harness(dst: Path) -> Path:
    """The harness's tracked tree under `dst`, as the working tree holds it."""
    for name in tracked():
        src = ROOT / name
        if src.is_file():   # a tracked file this working tree deleted is not copied
            target = dst / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    return dst


def spawn_copy(harness: Path, where: Path, *flags: str,
               out: str = "out") -> subprocess.CompletedProcess:
    """The copy's build of `where`'s workspace, spawned by path - `_estate.spawn` with another
    harness."""
    return subprocess.run(
        [sys.executable, str(harness / "tools" / "graph" / "build.py"),
         "--workspace", str(where / "ws" / "workspace.toml"), "--out", str(where / out),
         "--run-id", "fixed", "--empty-ok", *flags],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(harness))
