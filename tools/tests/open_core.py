"""The open-core boundary (T689): what this repository must never track, and the checks.

This repository is the free edition. A private repository - the paid layer - is overlaid on
the same folder as `.git-commercial/` and holds files this one never tracks; this repository's
`.gitignore` is the boundary between them. Five checks, each answering a list of problems
(empty when the boundary holds):

* `private_paths_tracked` - a tracked path under a private root.
* `private_paths_unignored` - a private root under which a NEW file would not be ignored, or a
  file under one that is neither tracked nor ignored (a negation rule).
* `barricade_breaches` - a tracked file under `tools/`, `sdlc/` or `sdlc_templates/`, or
  `sdlc-init.py`, that writes `enterprise/` or `enterprise\\` starting a path (any file, prose
  included), or - in a Python file - an `import`/`from` of `enterprise`, an `import_module` or
  `__import__` of a string starting `enterprise`, or `'enterprise'` quoted as a path segment -
  joined with `/`, inside the parentheses of an `os.path.join`, `posixpath.join`,
  `ntpath.join`, `.joinpath` or `Path`-class call (whatever the earlier arguments), or joined to
  `os.sep` with `+`. A parenthesis inside a string literal can mis-span a call: brackets are
  counted plainly. The word used as a value on the same line as a path call
  (`Path(p).read_text()["enterprise"]`) is not a breach. Python prose is held to the
  same rule: the words `import enterprise` anywhere on a line - a comment or a docstring
  included - and `'x' / 'enterprise'` are breaches, and the remedy is rephrasing. What it cannot
  see: a name built at run time (`'enter' + 'prise'`); the paid layer's directory put on
  `sys.path` and its modules imported by their own names; a quoted `'enterprise'` handed to a
  path API other than those (a bare `join` imported from `os.path` included); and `enterprise/`
  written after a slash - `f"{ROOT}/enterprise/app.py"`, `str(ROOT) + "/enterprise"` - which
  the URL exclusion below also lets through. The WORD is not a breach - "an enterprise litellm
  proxy" is a customer's, and so is `'enterprise'` quoted as a value, `", ".join(...)` of tier
  names included - nor is a URL segment, nor is `docs/`, which may describe the split. The
  guard's own two files are exempt. The paid layer imports the free modules; never the
  reverse.
* `twin_overlap` - `None` where no twin is overlaid (a worktree, a fresh clone, CI's), else a
  file both repositories track or a twin file this repository does not ignore. The twin's
  files are its HEAD's and its index's: a twin restored without an index is still read.
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

#: In a Python file: an import statement anywhere on a line (`if x: import enterprise`, and in
#: a string, so `exec('import enterprise')` too), `import_module`/`__import__` with a plain,
#: f-, r- or b-prefixed string starting `enterprise`, and `'enterprise'` joined with `/`
#: (`ROOT / 'enterprise' / 'console'`). A path CALL's arguments are `_in_path_call`'s.
_Q = r"[fFrRbB]{0,2}['\"]enterprise['\"]"
_IMPORT = re.compile(r"(?<![\w.])(?:from|import)\s+enterprise(?![\w])"
                     r"|(?:import_module|__import__)\(\s*[fFrRbB]{0,2}['\"]enterprise(?![\w])"
                     rf"|/\s*{_Q}|{_Q}\s*/")
#: A path call - the quoted word is a path segment when it sits INSIDE the call's own
#: parentheses, whatever its earlier arguments are: `os.path.join(os.path.dirname(__file__),
#: "enterprise")` (B28-25), while `Path(root).read_text()["enterprise"]` is a value (B28-35).
#: `str.join` is not a path API (B28-27).
_PATH_CALL = re.compile(r"\b(?:os\.path|posixpath|ntpath)\.join\(|\.joinpath\("
                        r"|\b(?:Pure|Posix|Windows|PurePosix|PureWindows)?Path\(")
_QUOTED = re.compile(_Q)
#: `os.sep` has no parentheses: the quoted word joined to it with `+`.
_SEP = re.compile(rf"\bos\.sep\s*\+\s*{_Q}|{_Q}\s*\+\s*os\.sep\b")


def _in_path_call(line: str) -> bool:
    """Whether the quoted word lies between a path call's opening parenthesis and its match.
    Brackets are counted plainly; a parenthesis inside a string literal can mis-span a call, a
    limit listed in the module docstring."""
    for m in _PATH_CALL.finditer(line):
        depth, end = 0, len(line)
        for i in range(m.end() - 1, len(line)):
            if line[i] == "(":
                depth += 1
            elif line[i] == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if _QUOTED.search(line, m.end(), end):
            return True
    return bool(_SEP.search(line))
#: In any shipped file: `enterprise/` or `enterprise\` STARTING a path - not after a word
#: character, `.`, `-`, `/`, `\` or `:`, so a URL segment (`https://x/enterprise/`) and
#: `some-enterprise/` are not breaches, while prose that happens to write `enterprise/SMB` is.
_PATH = re.compile(r"(?<![\w.:/\\-])enterprise[/\\]")
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
            if (python and (_IMPORT.search(line) or _in_path_call(line))) \
                    or _PATH.search(line):
                found.append(f"{rel}:{n}: {line.strip()}")
    return found


def twin_overlap(root: Path) -> list[str] | None:
    twin = root / TWIN_DIR
    if not twin.is_dir():
        return None
    base = ["git", f"--git-dir={twin}", f"--work-tree={root}"]

    def listing(*args: str) -> list[str]:
        p = subprocess.run([*base, *args], cwd=root, capture_output=True)
        if p.returncode:
            raise CouldNotLook(f"the private repository at {twin} could not be listed (git "
                               f"{' '.join(args[:2])} exited {p.returncode}): "
                               f"{p.stderr.decode('utf-8', 'replace').strip() or '(no message)'}")
        return [n for n in p.stdout.decode("utf-8", "replace").split("\0") if n]

    # Its files are what its HEAD holds AND what its index holds: a twin restored as a bare
    # clone has a HEAD and no index, and an index alone would read as empty - clean (B28-2).
    has_head = subprocess.run([*base, "rev-parse", "-q", "--verify", "HEAD"], cwd=root,
                              capture_output=True).returncode == 0
    private = sorted(set(listing("ls-files", "-z"))
                     | set(listing("ls-tree", "-r", "-z", "--name-only", "HEAD")
                           if has_head else ()))
    if has_head and not private:
        # Both listings read a tree; nothing is evidence of absence only if that tree IS the
        # empty one (a twin whose only commit is empty - B28-19).
        tree = subprocess.run([*base, "rev-parse", "HEAD^{tree}"], cwd=root,
                              capture_output=True, text=True).stdout.strip()
        empty = subprocess.run([*base, "hash-object", "-t", "tree", "--stdin"], cwd=root,
                               input="", capture_output=True, text=True).stdout.strip()
        if not tree or tree != empty:
            raise CouldNotLook(f"the private repository at {twin} has a commit whose tree is "
                               f"not empty but lists no file")
        return []
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
