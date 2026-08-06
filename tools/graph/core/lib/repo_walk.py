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
import subprocess
from dataclasses import dataclass
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
    ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".gradle",
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


def read_text(path: Path) -> str:
    """Decode tolerantly — never let one mis-encoded file fail a build."""
    try:
        with open(long_path(path), "rb") as fh:
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def find_files(
    root: Path,
    patterns: Iterable[str],
    *,
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
    use_fd: bool = True,
) -> list[Path]:
    """Find files under `root` matching glob patterns, excluding noisy directories."""
    patterns = list(patterns)
    if not root.is_dir():
        return []
    fd = _tool("fd") if use_fd else None
    if fd:
        found = _find_with_fd(fd, root, patterns, excludes)
        if found is not None:
            return found
    return _find_with_python(root, patterns, excludes)


def _find_with_fd(fd: str, root: Path, patterns: list[str], excludes: Iterable[str]) -> list[Path] | None:
    """One fd invocation per pattern.

    `--glob` is a boolean mode switch, not a repeatable option that takes a value: passing
    it twice made fd read the second pattern as a *search path*, which silently matched
    nothing and returned an empty list. That produced a confidently wrong estate report, so
    this now runs one pattern per call and treats any non-zero exit as "fall back to Python"
    rather than "no results".
    """
    ex = list(excludes)
    found: set[Path] = set()
    for pat in patterns:
        # `--full-path` is deliberately NOT used. On Windows it matches against a
        # backslash-separated path, so a glob containing `/` matches nothing and exits 0 —
        # a silent empty result the returncode guard below cannot catch, and the same class
        # of bug that once produced a confidently wrong estate report. Escaping to
        # backslashes does not help either: `\` is the glob escape character.
        # So fd does what it is good at (fast traversal, name matching) and the path
        # semantics are decided by the same `_glob_match` the pure-Python path uses. The
        # two backends then agree by construction rather than by coincidence.
        name_part = pat.rsplit("/", 1)[-1] or "*"
        cmd = [fd, "--type", "f", "--absolute-path", "--no-ignore-vcs", "--hidden", "--glob"]
        for e in ex:
            cmd += ["--exclude", e]
        cmd += ["--", name_part, str(root)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=300)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None  # never interpret an argument error as an empty result set
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            p = Path(line)
            if "/" in pat:
                try:
                    rel = p.relative_to(root).as_posix()
                except ValueError:
                    continue
                if not _glob_match(rel, p.name, pat):
                    continue
            found.add(p)
    return sorted(found)


def _find_with_python(root: Path, patterns: list[str], excludes: Iterable[str]) -> list[Path]:
    ex = set(excludes)
    out: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ex]
        here = Path(dirpath)
        for fn in filenames:
            p = here / fn
            rel = p.relative_to(root).as_posix()
            if any(_glob_match(rel, fn, pat) for pat in patterns):
                out.add(p)
    return sorted(out)


def _glob_match(rel: str, name: str, pattern: str) -> bool:
    from fnmatch import fnmatch

    if fnmatch(rel, pattern) or fnmatch(name, pattern):
        return True
    # Treat a bare pattern as matching at any depth, mirroring fd's default behavior.
    return not pattern.startswith("**/") and fnmatch(rel, f"**/{pattern}")


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
    cmd = [rg, "--json", "--no-ignore-vcs", "--hidden"]
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
