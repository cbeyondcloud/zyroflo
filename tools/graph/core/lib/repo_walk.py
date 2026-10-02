r"""File discovery and content search, rg/fd-accelerated with a pure-Python fallback.

Two properties matter more than speed here:
  * `rg --json` reports byte offsets, so every edge gets exact `source_file:line` provenance
    rather than a re-scan guess;
  * the pure-Python path produces identical results, so a developer without the vendored
    binaries gets the same graph, only slower.

Windows specifics are handled deliberately: long paths via the `\\?\` prefix, and decoding
with errors='replace' so one cp1252 file cannot abort an estate-wide build.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator

THIRD_PARTY_BIN = Path(__file__).resolve().parents[4] / "third_party" / "bin"


def _platform_key() -> str:
    """`<os>-<arch>`, matching the directory names `third_party.py` installs into.

    Duplicated from `third_party.current_platform()` rather than imported: this module sits
    in `core/`, which may not reach up into `tools/`, and the whole point of the vendored
    binaries is that they are optional. Kept to a few lines and covered by a test that pins
    the two implementations together.
    """
    import platform

    osname = platform.system().lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "x64": "x86_64",
            "arm64": "aarch64", "aarch64": "aarch64"}.get(platform.machine().lower(), "")
    return f"{osname}-{arch}"

#: Directories never worth walking. Generated output is excluded because a generated file
#: yields edges that no human can act on — it is noise with provenance.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git", ".svn", ".hg", "node_modules", "target", "build", "dist", "out",
    ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".idea", ".vscode", "vendor", "coverage", ".next", ".nuxt", "bin", "obj",
    ".dart_tool", ".sdlc-worktrees",
)


@dataclass(frozen=True, slots=True)
class Match:
    """One content hit with exact provenance."""

    path: Path
    line: int
    text: str


@lru_cache(maxsize=4)
def _tool(name: str) -> str | None:
    """Locate a vendored binary, falling back to PATH; None means use the Python path.

    This used to look only for `third_party/bin/{name}.exe`, which meant that even a
    correctly downloaded Linux binary could never be found — the resolver was the second
    half of the single-OS lock-in, and the more insidious half, because it looks like a
    missing tool rather than a wrong path.
    """
    from shutil import which

    exe = ".exe" if os.name == "nt" else ""
    candidate = THIRD_PARTY_BIN / _platform_key() / f"{name}{exe}"
    if candidate.exists():
        return str(candidate)
    return which(name)


def long_path(p: Path) -> str:
    r"""Windows MAX_PATH escape. Real workspaces routinely have directory names containing
    spaces and nesting several levels deep; without the \\?\ prefix those paths fail to open
    at ~260 characters, and the failure surfaces as a file that simply is not there."""
    s = str(p)
    if os.name == "nt" and len(s) > 240 and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + os.path.abspath(s)
    return s


def read_text_reporting(path: Path) -> tuple[str, str]:
    """Read source text, returning (text, error). Exactly one of the two is meaningful.

    could-not-read is its OWN answer: on OSError the text is "" and `error` names the reason,
    so a caller can record an `unresolved` row instead of treating a file it could not open as
    an empty one -- the same distinction `props.load_yaml_all_reporting` draws for a file it
    could not parse. This is the single place the decode happens; `read_text` is a thin
    collapsing wrapper over it, so the tolerant spelling and the honest one can never disagree
    about what a file contained. A blank file reads as ("", "") -- no error -- which is exactly
    how it stays distinct from ("", <reason>) for one that would not open.

    A byte-order mark at the start is read past (`utf-8-sig`), here, once, for every reader:
    editors on one platform write it by default, and left in it stayed glued to the file's
    first token - a key declared on line 1 read as a different key (T532). A mark anywhere
    else is content and is kept.
    """
    try:
        with open(long_path(path), "rb") as fh:
            return fh.read().decode("utf-8-sig", errors="replace"), ""
    except OSError as exc:
        return "", " ".join(str(exc).split())


def read_text(path: Path) -> str:
    """Decode tolerantly, folding could-not-read to "" -- never let one mis-encoded or
    unreadable file fail a build. A caller that must tell "unreadable" from "empty" and record
    the loss uses `read_text_reporting`; this spelling is for the ones that genuinely cannot act
    on it (a content search skips a blank and an unreadable file alike)."""
    return read_text_reporting(path)[0]


def find_files(
    root: Path,
    patterns: Iterable[str],
    *,
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
    use_fd: bool = True,
    near_misses: list[tuple[Path, tuple[str, ...]]] | None = None,
) -> list[Path]:
    """Find files under `root` matching glob patterns, excluding noisy directories.

    Matching is case-sensitive on every platform (see `glob_match`), so a file spelled
    `dockerfile` is not `Dockerfile*` on Windows either. Given a `near_misses` list, the SAME
    walk also appends `(path, patterns)` for every file that no pattern matches but that
    patterns match once case is ignored - EVERY such pattern, so a caller can name each
    reader that lost the file, not only the one whose pattern sorts first - in an order that
    is the same on every platform (T531).
    """
    patterns = list(patterns)
    if not root.is_dir():
        return []
    fd = _tool("fd") if use_fd else None
    if fd:
        # Collected apart and handed over only on success: a walk the fallback has to
        # redo must not leave half its near misses behind.
        misses: list[tuple[Path, tuple[str, ...]]] | None = (
            [] if near_misses is not None else None)
        found = _find_with_fd(fd, root, patterns, excludes, misses)
        if found is not None:
            if near_misses is not None:
                near_misses.extend(misses or [])
            return found
    return _find_with_python(root, patterns, excludes, near_misses)


def _near_miss(rel: str, name: str, patterns: list[str]) -> tuple[str, ...]:
    """Every pattern this file matches only when case is ignored - empty when none does.

    All of them, not the first: `patterns` arrive sorted, so `*.yaml` is tried before
    `values.yaml`, and naming only the first sent a reader to the extractor that sorts
    first rather than to the one that lost the file.

    Both sides are lowercased explicitly. `fnmatch.fnmatch` folds case only through
    `os.path.normcase`, which does nothing on POSIX, so leaning on it would report the near
    miss on Windows alone - the platform split this exists to close.
    """
    return tuple(pat for pat in patterns
                 if glob_match(rel.lower(), name.lower(), pat.lower()))


def _find_with_fd(fd: str, root: Path, patterns: list[str], excludes: Iterable[str],
                  near_misses: list[tuple[Path, tuple[str, ...]]] | None = None
                  ) -> list[Path] | None:
    """ONE traversal, with membership decided in Python.

    fd is used for what it is good at — walking a tree quickly and honouring excludes — and
    `glob_match` decides which files count, exactly as the pure-Python backend does. The two
    then agree by construction rather than by coincidence, which is the parity contract this
    has always claimed.

    **It was previously one fd invocation per pattern**, and with a handful of patterns that
    was merely wasteful. Once the build pipeline started passing the union of every registered
    extractor's globs it became ~30 process spawns per component: a six-component fixture took
    **27 seconds through the accelerator and 1 second through the fallback**. An optimisation
    that is 28× slower than the thing it optimises is not a tuning problem, it is a defect,
    and it hid because nothing called this with a large pattern set until then.

    `--glob` is not used at all now, which also retires two traps recorded here: it is a
    boolean mode switch rather than a repeatable option (passing it twice made fd read the
    second pattern as a search *path*, matching nothing at exit 0), and `--full-path` matches
    a backslash-separated path on Windows, so any glob containing `/` matched nothing —
    silently, which the returncode guard below cannot catch.

    **A RELATIVE `root` used to return zero files here, at exit 0** — the third instance of
    this module's own cardinal failure, found when the POSIX gate (`docs/m0/posix-gate-record.md`)
    compared the two backends deliberately. fd is asked for `--absolute-path`, so every line
    it emits is absolute; `p.relative_to(root)` against a relative root then raised
    `ValueError` for *every* line, and a per-line `except: continue` swallowed each one. The
    caller received an empty list meaning "this tree has no matching files".

    Two changes, and the second matters more than the first. Paths are made relative against
    the **resolved** root, so the comparison is between like and like. And a line that still
    cannot be placed under it **abandons the accelerator** rather than skipping that file:
    every line fd emits is under the search path it was given, so one that is not means an
    assumption here is wrong, and the honest response to a broken assumption is to let the
    fallback answer — the same rule the returncode guard above already follows.

    Results are rebuilt as `root / rel` rather than returned as fd printed them, so both
    backends answer in the caller's own frame of reference: a relative root yields relative
    paths, an absolute root absolute ones. Agreeing on *which files* while disagreeing on how
    to spell them is not parity — it just moves the discrepancy somewhere harder to see.
    """
    # `--no-ignore`, NOT `--no-ignore-vcs`. `--no-ignore-vcs` disables only the VCS ignore
    # file (`.gitignore`); fd ALSO honours its own `.ignore` and `.fdignore`, so a repo
    # carrying either made fd silently emit nothing at exit 0 where the Python fallback found
    # the file — this module's cardinal failure ("an empty result at exit 0 is not evidence of
    # absence"), third instance. `--no-ignore` subsumes `--no-ignore-vcs` and disables every
    # ignore-file mechanism, so the accelerator and the fallback see the same tree; membership
    # is still decided in Python below, and DEFAULT_EXCLUDES is what prunes noise, not any
    # ignore file the adopter's repo happens to ship.
    cmd = [fd, "--type", "f", "--absolute-path", "--no-ignore", "--hidden"]
    for e in excludes:
        cmd += ["--exclude", e]
    cmd += ["--", ".", str(root)]          # `.` as a regex: every file, one walk
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None  # never interpret an argument error as an empty result set

    base = Path(root).resolve()
    found: set[Path] = set()
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        p = Path(line)
        try:
            rel = p.relative_to(base).as_posix()
        except ValueError:
            return None  # an assumption broke; the fallback answers rather than "nothing"
        if any(glob_match(rel, p.name, pat) for pat in patterns):
            found.add(root / rel)
        elif near_misses is not None and (pats := _near_miss(rel, p.name, patterns)):
            near_misses.append((root / rel, pats))
    if near_misses is not None:
        # By the POSIX spelling: a `Path` compares case-folded on Windows alone.
        near_misses.sort(key=lambda miss: miss[0].as_posix())
    return sorted(found)


def _find_with_python(root: Path, patterns: list[str], excludes: Iterable[str],
                      near_misses: list[tuple[Path, tuple[str, ...]]] | None = None
                      ) -> list[Path]:
    ex = set(excludes)
    out: set[Path] = set()
    misses: list[tuple[Path, tuple[str, ...]]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # T592: a directory that is a link is not entered, as fd does not enter it.
        # `os.walk` leaves a symlink alone but takes a Windows junction for a directory,
        # and one pointing back at the root recursed `self/self/...` to the path limit.
        dirnames[:] = [d for d in dirnames
                       if d not in ex and not _is_dir_link(os.path.join(dirpath, d))]
        here = Path(dirpath)
        for fn in filenames:
            p = here / fn
            rel = p.relative_to(root).as_posix()
            if any(_glob_match(rel, fn, pat) for pat in patterns):
                out.add(p)
            elif near_misses is not None and (pats := _near_miss(rel, fn, patterns)):
                misses.append((p, pats))
    if near_misses is not None:
        near_misses.extend(sorted(misses, key=lambda miss: miss[0].as_posix()))
    return sorted(out)


#: The reparse tags of the two Windows links a walk must not enter - a symlink and a
#: junction (mount point). Only these: a cloud-files placeholder is a reparse point too,
#: and a real directory, which fd walks as well.
_LINK_TAGS = (stat.IO_REPARSE_TAG_SYMLINK, stat.IO_REPARSE_TAG_MOUNT_POINT)


def _is_dir_link(path: str) -> bool:
    """Whether `path` is a symlink or a Windows junction, read without following it."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or getattr(st, "st_reparse_tag", 0) in _LINK_TAGS


#: Directory names that conventionally mean "this is test code", matched case-folded.
#: `it` is not among them: as a bare directory it is as often a package or a locale (T531).
_TEST_DIRS = {"test", "tests", "testing", "spec", "specs", "e2e"}
#: A source set's name under `src/`, for the layout that has them: these sets hold tests,
#: and `main` holds production code.
_TEST_SOURCE_SETS = {"test", "it"}
_PRODUCTION_SOURCE_SET = "main"
#: Test file names whose suffix carries its own boundary (`.`, `_`), matched case-folded.
_TEST_SUFFIXES = (".test.js", ".spec.js", ".test.jsx", ".spec.jsx",
                  ".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx",
                  "_test.py", "_test.go", "_spec.rb")
_TEST_PREFIX = "test_"
#: Test file names whose boundary is a capital letter, matched CASE-SENSITIVELY: folded,
#: `Credit.java` ends in `it.java` and `Latest.java` in `test.java` (T531).
_TEST_CAMEL = ("*Test.java", "*Tests.java", "*IT.java", "*TestCase.java")


def looks_like_test(rel: str) -> bool:
    """Whether a repo-relative path is test code, by convention.

    A convention, and it is stated as one: a project that names its tests differently is
    missed here, which is why the extractors that use this report their coverage rather than
    implying it. The direction of the error is deliberate — treating production code as a
    test loses real edges, while treating a test as production code INVENTS them, and an
    invented edge then appears in every blast radius forever.

    The rule, in order (T531):

    * **A test file name counts anywhere.** A `test_` prefix and a suffix that carries its
      own boundary (`.spec.ts`, `_test.go`) are matched with case folded, as they always
      were. A suffix whose boundary is a capital letter (`*Test.java`, `*IT.java`) is matched
      case-sensitively, because folded it swallowed `Credit.java`, `Audit.java` and
      `Latest.java` - production classes dropped by every gated extractor, with no row.
    * **Below a `src/main/` source set, a directory name is a package name.** A `spec` or an
      `it` package there is production code. A test file NAME still counts there: a web
      front end kept under `src/main/webapp/` has `*.spec.ts` suites, and reading one as
      production turned a test's mock URL into a real call.
    * **`src/test/` and `src/it/` are test source sets**, whatever their files are called.
    * **A source set kept under a test directory is a fixture**, `src/main/` included: a
      project under `tests/fixtures/` is test code, or its endpoints enter the graph as the
      component's own.
    * **Elsewhere, a conventional test directory** (`test`, `tests`, `spec`, ...) makes a
      file test code. A bare `it` directory does not, anywhere: it is as often a package, a
      locale or a country's module (`sites/it/src/main/`) as a test module.
    """
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    base = parts[-1] if parts else ""
    low = base.lower()
    if (low.startswith(_TEST_PREFIX) or low.endswith(_TEST_SUFFIXES)
            or any(fnmatchcase(base, pat) for pat in _TEST_CAMEL)):
        return True
    dirs = [p.lower() for p in parts[:-1]]
    for i in range(len(dirs) - 1):
        if dirs[i] == "src":
            if any(d in _TEST_DIRS for d in dirs[:i]):
                return True
            if dirs[i + 1] == _PRODUCTION_SOURCE_SET:
                return False
            if dirs[i + 1] in _TEST_SOURCE_SETS:
                return True
    return any(d in _TEST_DIRS for d in dirs)


def glob_match(rel: str, name: str, pattern: str) -> bool:
    """Whether a file matches one pattern, given its root-relative path and its base name.

    Public because a caller that needs to match many pattern SETS against one tree — the
    build pipeline, deciding which extractors claim which files — must walk once and filter
    in memory rather than walk once per set. Filtering the union that way is equivalent to
    calling `find_files` per set: `_find_with_python` is exactly `any(glob_match(...))`, and
    the fd backend is asserted equal to it by the parity contract test.

    **Case-sensitive on every platform** (T531). `fnmatch.fnmatch` folds case on Windows
    alone, so one commit built a different graph there: `Credit.java` matched `*IT.java` and
    became a test suite on Windows and nothing at all on Linux. A file that now misses only
    by case is reported by the caller, through `find_files`' `near_misses`, never dropped.
    """
    if fnmatchcase(rel, pattern) or fnmatchcase(name, pattern):
        return True
    # Treat a bare pattern as matching at any depth, mirroring fd's default behavior.
    return not pattern.startswith("**/") and fnmatchcase(rel, f"**/{pattern}")


#: Retained so the private spelling used elsewhere in this module keeps working.
_glob_match = glob_match


def search(
    root: Path,
    pattern: str,
    *,
    globs: Iterable[str] = (),
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
    use_rg: bool = True,
    fixed_string: bool = False,
) -> list[Match]:
    """Search file contents, returning matches with exact line provenance."""
    if not root.is_dir():
        return []
    rg = _tool("rg") if use_rg else None
    if rg:
        found = _search_with_rg(rg, root, pattern, globs, excludes, fixed_string)
        if found is not None:
            return found
    return _search_with_python(root, pattern, globs, excludes, fixed_string)


def _search_with_rg(rg, root, pattern, globs, excludes, fixed_string) -> list[Match] | None:
    # `--no-ignore`, NOT `--no-ignore-vcs` (same failure class as `_find_with_fd`): rg honours
    # `.gitignore`, `.ignore` AND `.rgignore`, and `--no-ignore-vcs` disables only the first.
    # A repo shipping a `.ignore`/`.rgignore` therefore hid matching files from rg — silently,
    # at exit 0 (or 1 for "no match") — while `_search_with_python` found them, since a whole-
    # tree search passes no include glob to override the ignore rule. `--no-ignore` disables all
    # three, so rg and the Python fallback search the same files; DEFAULT_EXCLUDES still prunes.
    cmd = [rg, "--json", "--no-ignore", "--hidden"]
    if fixed_string:
        cmd.append("--fixed-strings")
    for ex in excludes:
        # A ripgrep glob containing '/' is anchored to the search root, so `!node_modules/**`
        # only skips the TOP-LEVEL one — every nested node_modules/.git/target still leaked
        # into results. The Python fallback prunes these directories at any depth, so match
        # that: exclude the directory itself and its contents, wherever it sits.
        cmd += ["--glob", f"!**/{ex}", "--glob", f"!**/{ex}/**"]
    for g in globs:
        cmd += ["--glob", g]
    cmd += ["--", pattern, str(root)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode not in (0, 1):
        return None
    out: list[Match] = []
    for line in proc.stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") != "match":
            continue
        d = rec["data"]
        path = d.get("path", {}).get("text")
        if not path:
            continue
        out.append(Match(Path(path), d["line_number"], d.get("lines", {}).get("text", "").rstrip("\n")))
    return out


def _search_with_python(root, pattern, globs, excludes, fixed_string) -> list[Match]:
    import re

    rx = re.compile(re.escape(pattern) if fixed_string else pattern)
    files = find_files(root, list(globs) or ["*"], excludes=excludes, use_fd=False)
    out: list[Match] = []
    for f in files:
        text = read_text(f)
        if not text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                out.append(Match(f, i, line))
    return out


def iter_lines(path: Path) -> Iterator[tuple[int, str]]:
    """1-based (line_number, text) over a file's contents."""
    for i, line in enumerate(read_text(path).splitlines(), 1):
        yield i, line
