"""The guard, applied to git history rather than the working tree.

`test_no_private_coordinates.py` checks what ships *today*. This checks what shipped *ever* —
because anything committed to a repo with a remote is reachable long after the commit that
added it is gone, and "I deleted the file" is a narrower claim than "the content is
unreachable".

That distinction is not theoretical here. The Rev 7 history rewrite (item 73) took **four
passes**, and every one of the first three looked complete:

1. removing the offending files by path missed content inside files that legitimately
   *continued to exist* across the same commits;
2. redacting those literals missed a **differently-named earlier copy** of the same report,
   found only by cloning the pushed remote and grepping it;
3. removing both filename conventions missed coordinates still sitting in historical test
   fixtures and docstrings.

This test is the stopping condition that pass 4 finally satisfied. It exists so the next
rewrite — or the next accidental commit — is judged mechanically instead of by whoever last
remembered to look.

**Cost.** It reads every blob in every commit, so it is the slowest test in the suite. On a
repo this size that is a couple of seconds. If it ever becomes the reason people stop running
the suite, bound it to commits since the last verified-clean tag rather than deleting it —
a slow check that runs beats a fast one that does not exist.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.tests import _scan

ROOT = Path(__file__).resolve().parents[2]
BANLIST = Path(__file__).resolve().parent / "banned_tokens.txt"

#: The digest list itself: hashes only, and hashing them again is circular.
SKIP_PATHS = frozenset({"tools/tests/banned_tokens.txt"})

SKIP_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".exe", ".dll",
    ".so", ".dylib", ".whl", ".pyc", ".woff", ".woff2", ".ttf", ".lock",
})


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout


def all_commits() -> list[str]:
    return _git("rev-list", "--all").split()


def test_there_is_history_to_check():
    assert len(all_commits()) > 1, "no history found — is this a git checkout?"


def test_no_banned_coordinate_survives_anywhere_in_history():
    """Every blob, every commit. Zero findings, or the redaction is not finished.

    The failure message names the file and the offending phrase but no commit, because the
    fix is never "amend that commit" — it is another rewrite pass over everything.
    """
    banned = _scan.load_banned(BANLIST)
    offenders: dict[str, set[str]] = {}

    for commit in all_commits():
        for rel in _git("ls-tree", "-r", "--name-only", commit).splitlines():
            if rel in SKIP_PATHS or Path(rel).suffix.lower() in SKIP_SUFFIXES:
                continue
            content = _git("show", f"{commit}:{rel}")
            hits = [f for f in _scan.scan_text(content, banned) if f.kind == "token"]
            if hits:
                offenders.setdefault(rel, set()).update(f.evidence for f in hits)

    assert not offenders, (
        "banned coordinates survive in git history:\n  "
        + "\n  ".join(f"{rel}: {sorted(ev)}" for rel, ev in sorted(offenders.items()))
        + "\n\nDeleting the file at HEAD does not help — the content stays reachable. This "
          "needs another `git filter-repo --replace-text` pass over all history, followed by "
          "a force-push AND a fresh clone of the remote to confirm what others can see."
    )


@pytest.mark.parametrize("word", ["ripgrep", "upgrade"])
def test_the_redaction_did_not_damage_legitimate_words(word: str):
    """A history-wide search-and-replace is a blunt instrument.

    Redacting a short coined token risks mangling ordinary words that merely contain its
    letters. `ripgrep` and `upgrade` both do, and a naive substitution would have quietly
    corrupted the vendored-binary manifest, a tree-sitter docstring, and the very tests that
    prove the matcher ignores them. The redaction used lookarounds to exclude both; this
    proves it worked and would catch a future pass that forgot.

    Deliberately not pinned to a file — where these words live is incidental, that they
    survived intact anywhere is the claim.
    """
    hits = _git("grep", "-l", word, "HEAD").splitlines()
    assert hits, (
        f"{word!r} has vanished from the tree — a redaction pass probably ate it as a "
        f"substring of a banned coordinate. Check the lookarounds in the replace-text rules."
    )
