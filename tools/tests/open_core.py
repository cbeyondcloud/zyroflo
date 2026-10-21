"""The open-core boundary (T689): what this repository must never track, and the checks.

This repository is the free edition. A private repository - the paid layer - is overlaid on
the same folder as `.git-commercial/` and holds files this one never tracks; this repository's
`.gitignore` is the boundary between them. Five checks, each answering a list of problems
(empty when the boundary holds):

* `private_paths_tracked` - a tracked path under a private root.
* `private_paths_unignored` - a private root under which a NEW file would not be ignored, or a
  file under one that is neither tracked nor ignored (a negation rule).
* `barricade_breaches` - a shipped file (`tools/`, `sdlc/`, `sdlc_templates/`,
  `sdlc-init.py`) that imports the `enterprise` package or names the path `enterprise/`. The
  WORD is not a breach: "an enterprise litellm proxy" is a customer's, and `docs/` may describe
  the split. The paid layer imports the free modules; never the reverse.
* `twin_overlap` - `None` where no twin is overlaid (a worktree, a fresh clone, CI's), else a
  file both repositories track or a twin file this repository does not ignore.
* `required_public_files` - a public-repository file (licence, README, contributing, security,
  code of conduct) that is missing or untracked.

A listing git could not produce raises `CouldNotLook` (`ban.py`'s): an empty or failed answer
is a tree nobody read, never a clean one.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ban import CouldNotLook

ROOT = Path(__file__).resolve().parents[2]
TWIN_DIR = ".git-commercial"

#: Never tracked here, always ignored here. `.claude/` whole: its agents belong to the private
#: repository, and its worktrees and local settings to nobody.
PRIVATE_ROOTS = ("enterprise/", "internal-docs/", ".claude/", TWIN_DIR + "/")
#: The shipped trees the barricade reads; the guard's own two files name the paid layer by
#: necessity and are not shipped behaviour.
SHIPPED = ("tools/", "sdlc/", "sdlc_templates/")
SHIPPED_FILES = ("sdlc-init.py",)
GUARD = ("tools/tests/open_core.py", "tools/tests/test_open_core_split.py")
REQUIRED = ("LICENSE", "README.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md")

_IMPORT = re.compile(r"^\s*(?:from|import)\s+enterprise(?:\.|\s|$)"
                     r"|import_module\(\s*['\"]enterprise(?:\.|['\"])"
                     r"|__import__\(\s*['\"]enterprise(?:\.|['\"])")
_PATH = re.compile(r"(?<![\w.-])enterprise/")
#: A name no one would give a file, used to ask "would a NEW file under this root be ignored?"
_PROBE = "__open_core_probe__"


def _git(root: Path, *args: str, data: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], input=data, capture_output=True)


def tracked(root: Path) -> list[str]:
    p = _git(root, "ls-files", "-z")
    if p.returncode:
        raise CouldNotLook(f"git ls-files exited {p.returncode} in {root}: "
                           f"{p.stderr.decode('utf-8', 'replace').strip() or '(no message)'}")
    names = [n for n in p.stdout.decode("utf-8", "replace").split("\0") if n]
    if not names:
        raise CouldNotLook(f"git ls-files listed no tracked files in {root}")
    return names


def _private(path: str) -> bool:
    return path.lower().startswith(PRIVATE_ROOTS)


def private_paths_tracked(root: Path) -> list[str]:
    return sorted(p for p in tracked(root) if _private(p))


def private_paths_unignored(root: Path) -> list[str]:
    found = []
    for private_root in PRIVATE_ROOTS:
        p = _git(root, "check-ignore", "-q", "--no-index", private_root + _PROBE)
        if p.returncode not in (0, 1):
            raise CouldNotLook(f"git check-ignore exited {p.returncode} in {root}: "
                               f"{p.stderr.decode('utf-8', 'replace').strip()}")
        if p.returncode == 1:
            found.append(private_root)
    p = _git(root, "ls-files", "-z", "--others", "--exclude-standard", "--",
             *(f":(icase){r}" for r in PRIVATE_ROOTS))
    if p.returncode:
        raise CouldNotLook(f"git ls-files --others exited {p.returncode} in {root}")
    found += sorted(n for n in p.stdout.decode("utf-8", "replace").split("\0") if n)
    return found


def _shipped(path: str) -> bool:
    return (path.startswith(SHIPPED) or path in SHIPPED_FILES) and path not in GUARD


def barricade_breaches(root: Path) -> list[str]:
    found = []
    for rel in tracked(root):
        if not _shipped(rel):
            continue
        try:
            text = (root / rel).read_bytes().decode("utf-8", "replace")
        except OSError as exc:
            raise CouldNotLook(f"{rel} is tracked and could not be read: {exc}")
        python = rel.endswith(".py")
        for n, line in enumerate(text.splitlines(), 1):
            if (python and _IMPORT.search(line)) or _PATH.search(line):
                found.append(f"{rel}:{n}: {line.strip()}")
    return found


def twin_overlap(root: Path) -> list[str] | None:
    twin = root / TWIN_DIR
    if not twin.is_dir():
        return None
    p = subprocess.run(["git", f"--git-dir={twin}", f"--work-tree={root}", "ls-files", "-z"],
                       cwd=root, capture_output=True)
    if p.returncode:
        raise CouldNotLook(f"the private repository at {twin} could not be listed (git exited "
                           f"{p.returncode}): "
                           f"{p.stderr.decode('utf-8', 'replace').strip() or '(no message)'}")
    private = [n for n in p.stdout.decode("utf-8", "replace").split("\0") if n]
    if not private:
        return []
    public = set(tracked(root))
    ignored = _git(root, "check-ignore", "--no-index", "--stdin", "-z",
                   data="\0".join(private).encode("utf-8"))
    if ignored.returncode not in (0, 1):
        raise CouldNotLook(f"git check-ignore exited {ignored.returncode} in {root}")
    ignored_set = {n for n in ignored.stdout.decode("utf-8", "replace").split("\0") if n}
    both = [f"{n}  (tracked by both repositories)" for n in sorted(private) if n in public]
    loose = [f"{n}  (not ignored by this repository)" for n in sorted(private)
             if n not in ignored_set]
    return both + loose


def required_public_files(root: Path) -> list[str]:
    present = set(tracked(root))
    return sorted(r for r in REQUIRED if r not in present)
