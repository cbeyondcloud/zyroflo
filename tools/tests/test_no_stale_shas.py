"""No tracked file may name a commit sha that is not on main.

A rebase gives a commit a new sha while any tracked file that named the old one keeps
the orphan.  Locally the dangling object survives (it is still in the object store),
so ``git cat-file -e`` passes and every local test is green; a fresh CI clone has no
such object and answers ``fatal: bad object``.  This took main red once, on the core
two-commit protocol, and recurred on T323/T352.

The convention that closes the class: **name commits by their subject line, not their
sha.**  A subject survives a rebase; a sha does not.  The only shas that belong in a
tracked file are ones already on main (immovable) or non-commit objects (blob digests
in the history guard, vendored checksums in the manifest).

The guard below walks every tracked file for 7-to-40-character hex tokens at word
boundaries, asks ``git cat-file`` whether each is a real commit, and then asks
``git merge-base --is-ancestor`` whether that commit is on HEAD.  A commit that
exists locally but is not an ancestor of HEAD is a stale sha from a rebased branch.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: 7 to 40 lowercase hex characters at a word boundary.
_SHA_RE = re.compile(r"\b([0-9a-f]{7,40})\b")

#: File extensions that are binary or not worth scanning.
_SKIP_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz",
    ".exe", ".dll", ".so", ".dylib", ".whl", ".pyc", ".woff", ".woff2", ".ttf",
})

#: The hex alphabet, for detecting tokens embedded inside longer hex strings
#: (e.g. sha256 digests in third_party/manifest.json).
_HEX = frozenset("0123456789abcdefABCDEF")


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout
    return sorted(p for p in out.splitlines() if p)


def _sha_tokens_in_file(rel: str) -> list[tuple[int, str, str]]:
    """Return (line_number, token, line_text) for each sha-shaped token in ``rel``.

    A token is skipped when it sits inside a longer hex run (e.g. a 64-character
    sha256 digest), because ``\\b[0-9a-f]{7,40}\\b`` cannot match inside one --
    the word boundary fires at the digest's edge, not in the middle.
    """
    path = ROOT / rel
    if not path.is_file():
        return []
    hits: list[tuple[int, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in _SHA_RE.finditer(line):
            start, end = m.start(), m.end()
            if start > 0 and line[start - 1] in _HEX:
                continue
            if end < len(line) and line[end] in _HEX:
                continue
            hits.append((lineno, m.group(1), line))
    return hits


def _is_stale_commit(token: str) -> bool:
    """True when ``token`` names a commit object that is NOT an ancestor of HEAD."""
    # Is it a real object at all?
    r = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-e", token],
        capture_output=True,
    )
    if r.returncode != 0:
        return False  # not a git object -- a hex literal, a test fixture, etc.

    # Is it a commit?
    r2 = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-t", token],
        capture_output=True, text=True,
    )
    if r2.stdout.strip() != "commit":
        return False  # blob, tree, or tag -- not our concern

    # Is it on main (an ancestor of HEAD)?
    r3 = subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", token, "HEAD"],
        capture_output=True,
    )
    return r3.returncode != 0  # exit 1 = not an ancestor = stale


def stale_shas_in_tracked_files() -> list[tuple[str, int, str]]:
    """Return (file, line, token) for every tracked file that names a stale commit."""
    stale: list[tuple[str, int, str]] = []
    for rel in _tracked_files():
        if Path(rel).suffix.lower() in _SKIP_EXTS:
            continue
        for lineno, token, _line_text in _sha_tokens_in_file(rel):
            if _is_stale_commit(token):
                stale.append((rel, lineno, token))
    return stale


def test_no_tracked_file_names_a_stale_commit_sha():
    """Every sha-shaped token in a tracked file that resolves to a real commit must
    be an ancestor of HEAD.  A commit that exists locally but is not on main is a
    stale leftover from a rebased branch, and a fresh clone will not have it.
    """
    stale = stale_shas_in_tracked_files()
    assert not stale, (
        "tracked file(s) name commit sha(s) that are NOT on main:\n"
        + "\n".join(
            f"  {rel}:{lineno}  {token}"
            for rel, lineno, token in stale
        )
        + "\n\nName the commit by its SUBJECT LINE instead of its sha.  "
        "A subject survives a rebase; a sha does not."
    )


def test_positive_control_fires(tmp_path: Path):
    """Prove the guard can fail: create a repo with a tracked file naming a commit
    sha that is NOT an ancestor of the current HEAD, and verify the scanner finds it.

    The technique: create a commit on a side branch, record its sha in a file on main,
    then rewrite the side branch so the sha is orphaned -- exactly the shape a rebase
    produces.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *args: subprocess.run(
        args, capture_output=True, text=True, cwd=str(repo), check=True,
    )
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "test@test.com")
    run("git", "config", "user.name", "Test")

    # Initial commit on main
    (repo / "readme.txt").write_text("hello\n", encoding="utf-8")
    run("git", "add", "readme.txt")
    run("git", "commit", "-q", "-m", "initial")

    # Side branch with a commit
    run("git", "checkout", "-q", "-b", "feature")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    run("git", "add", "feature.txt")
    run("git", "commit", "-q", "-m", "add feature")
    side_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, cwd=str(repo), check=True,
    ).stdout.strip()

    # Back to main: record the side sha in a tracked file
    run("git", "checkout", "-q", "main")
    note = repo / "notes.txt"
    note.write_text(f"measured at {side_sha[:7]}\n", encoding="utf-8")
    run("git", "add", "notes.txt")
    run("git", "commit", "-q", "-m", "record measurement")

    # Rewrite the side branch (simulate rebase): amend its commit so the old sha
    # is orphaned.
    run("git", "checkout", "-q", "feature")
    (repo / "feature.txt").write_text("feature v2\n", encoding="utf-8")
    run("git", "add", "feature.txt")
    run("git", "commit", "-q", "--amend", "-m", "add feature (amended)")
    run("git", "checkout", "-q", "main")

    # The old side_sha still exists locally as a dangling object, but is NOT an
    # ancestor of HEAD.
    short = side_sha[:7]

    # Verify the old sha is still a valid object (dangling)
    r = subprocess.run(
        ["git", "cat-file", "-e", short],
        capture_output=True, cwd=str(repo),
    )
    assert r.returncode == 0, "the dangling object should still exist locally"

    # Verify it is NOT an ancestor of main
    r = subprocess.run(
        ["git", "merge-base", "--is-ancestor", short, "HEAD"],
        capture_output=True, cwd=str(repo),
    )
    assert r.returncode != 0, "the dangling object should not be on main"

    # Now run our scanner logic against this repo
    stale: list[tuple[str, int, str]] = []
    tracked_out = subprocess.run(
        ["git", "-C", str(repo), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout
    for rel in sorted(tracked_out.splitlines()):
        if not rel:
            continue
        fpath = repo / rel
        if not fpath.is_file():
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for m in _SHA_RE.finditer(line):
                token = m.group(1)
                start, end = m.start(), m.end()
                if start > 0 and line[start - 1] in _HEX:
                    continue
                if end < len(line) and line[end] in _HEX:
                    continue
                # Check if real commit
                r = subprocess.run(
                    ["git", "-C", str(repo), "cat-file", "-t", token],
                    capture_output=True, text=True,
                )
                if r.returncode != 0 or r.stdout.strip() != "commit":
                    continue
                # Check if on HEAD
                r = subprocess.run(
                    ["git", "-C", str(repo), "merge-base", "--is-ancestor", token, "HEAD"],
                    capture_output=True, cwd=str(repo),
                )
                if r.returncode != 0:
                    stale.append((rel, lineno, token))

    assert stale, (
        "the positive control must find the stale sha -- the guard cannot fire"
    )
    assert any(short in t for _, _, t in stale), (
        f"the positive control must find {short!r} specifically"
    )
