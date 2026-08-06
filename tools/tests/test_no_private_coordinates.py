"""The genericity guard, whole-tree: nothing that ships may name a repo, an org, or a machine.

`test_core_genericity.py` polices *technology* terms in `tools/graph/core/`. This polices
*workspace coordinates* and *machine paths* across **every git-tracked file**, which is where
they actually were: an audit found 26 behaviour-affecting sites and ~34 prose sites, and only
a handful were in `core/`.

Scope is `git ls-files` rather than a glob or the working tree. That is a direct encoding of
the doctrine — "no shipped file may name a specific workspace" is the same statement as
"nothing git-tracked names one" — and it is also the only version that stays correct as file
types are added. A glob misses a new extension silently; the working tree carries `.env` and
`.venv`, neither of which ships.

Every exemption below is asserted for **exact equality**, so widening one is a visible diff in
a set literal rather than a quiet addition nobody reviews.

## What this guard CANNOT catch — read before trusting it

Stating the limits is the point. A guard believed to be total is worse than one known to be
partial, because the first one stops anybody looking.

* **A coordinate that is also an ordinary English word.** `ban.py` refuses to add one as a
  1-gram, precisely so the guard does not cry wolf and get switched off — the cost is that
  such a word passes when it *is* being used as a name. 2- and 3-grams cover the common
  compounds, not every occurrence.
* **Counts and other identifying facts that contain no name at all.** "a 26-repo estate" says
  something specific about one private workspace while matching nothing here. Several such
  phrases were found and fixed by hand; the guard found none of them.
* **A workspace whose terms nobody added.** The list is generated from a developer's own
  config, so it protects what that config declares and nothing else.
* **Meaning.** It matches strings. A paragraph that describes one organisation's architecture
  precisely, without naming it, is invisible here and is still a leak.

Human review remains load-bearing. This catches the mechanical majority so review can spend
its attention on the rest.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.tests import _scan

ROOT = Path(__file__).resolve().parents[2]
BANLIST = Path(__file__).resolve().parent / "banned_tokens.txt"

#: Not scanned at all. Exactly one file: it is nothing but digests, and hashing it again
#: would be circular.
FULLY_SKIPPED = frozenset({"tools/tests/banned_tokens.txt"})

#: Exempt from the machine-path check ONLY — still fully checked for workspace coordinates.
#:
#: These two files define and prove the path patterns, so they must contain literal drive
#: letters the way `test_core_genericity.py` must name the technologies it bans. The exemption
#: is deliberately narrow: it does not extend to workspace names, and it does not extend to
#: any other file. If a third file ever needs to be here, that is a signal the guard is being
#: worked around rather than satisfied.
PATH_EXEMPT = frozenset({
    "tools/tests/_scan.py",
    "tools/tests/test_scan.py",
})

#: Machine-generated and enormous; the blanket pattern hits every `https://` in it. Checked
#: by `test_the_lockfile_carries_no_local_paths` instead, which targets the only routes a
#: private path could actually take into a lockfile.
LOCKFILE = "uv.lock"

BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".exe", ".dll",
    ".so", ".dylib", ".whl", ".pyc", ".woff", ".woff2", ".ttf",
})


def tracked() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                         capture_output=True, text=True, check=True).stdout
    return sorted(p for p in out.splitlines() if p)


def scannable() -> list[str]:
    return [p for p in tracked()
            if p not in FULLY_SKIPPED
            and p != LOCKFILE
            and Path(p).suffix.lower() not in BINARY_SUFFIXES
            and (ROOT / p).is_file()]


BANNED = _scan.load_banned(BANLIST)


def test_there_is_something_to_scan():
    assert len(scannable()) > 20, "file list looks wrong — is this a git checkout?"


@pytest.mark.parametrize("rel", scannable())
def test_no_workspace_coordinates(rel: str):
    """No tracked file may name a specific organisation, product, or repository."""
    findings = _scan.scan_file(ROOT / rel, BANNED)
    hits = [f for f in findings if f.kind == "token"]
    assert not hits, (
        f"{rel} names a workspace coordinate:\n  "
        + "\n  ".join(f"line {f.line}: {f.evidence!r}" for f in hits)
        + "\n\nKeep the reason, lose the name: a docstring explaining WHY a design is the way "
          "it is stays valuable when its example is generic, and turning rationale into bare "
          "assertion is how the next maintainer ends up undoing the design."
    )


@pytest.mark.parametrize("rel", [p for p in scannable() if p not in PATH_EXEMPT])
def test_no_machine_paths(rel: str):
    """No tracked file may contain an absolute path that exists on one machine."""
    findings = _scan.scan_file(ROOT / rel, BANNED)
    hits = [f for f in findings if f.kind == "path"]
    assert not hits, (
        f"{rel} contains a machine-specific path:\n  "
        + "\n  ".join(f"line {f.line}: {f.evidence!r}" for f in hits)
        + "\n\nPaths arrive as input — a CLI argument, or a value the developer put in their "
          "sdlc-workspace.toml. Never a default: a default that points at one machine reports "
          "an empty workspace everywhere else, and empty reads exactly like clean."
    )


def test_the_lockfile_carries_no_local_paths():
    """`uv.lock` is exempt from the blanket scan (1900+ `https://` false hits) but not from
    the rule. These are the only three ways a private path reaches a lockfile."""
    text = (ROOT / LOCKFILE).read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        assert "file://" not in line, f"{LOCKFILE}:{lineno} has a file:// source"
        assert not _scan._DRIVE.search(line), f"{LOCKFILE}:{lineno} has a drive-letter path"
        assert not line.strip().startswith("path = "), f"{LOCKFILE}:{lineno} has a path source"


# ── the exemptions are pinned, not merely documented ──────────────────────────
def test_the_skip_set_is_exactly_one_file():
    """A guard's exemption list is where guards go to die. Pinning it means widening it is a
    changed literal in a diff, which someone has to read and approve."""
    assert FULLY_SKIPPED == frozenset({"tools/tests/banned_tokens.txt"})


def test_the_path_exemption_covers_only_the_guard_itself():
    assert PATH_EXEMPT == frozenset({"tools/tests/_scan.py", "tools/tests/test_scan.py"})


def test_the_path_exempt_files_are_still_coordinate_clean():
    """The narrow exemption is load-bearing only for drive letters. If either guard file also
    named a real workspace, the exemption would be laundering a violation."""
    for rel in sorted(PATH_EXEMPT):
        hits = [f for f in _scan.scan_file(ROOT / rel, BANNED) if f.kind == "token"]
        assert not hits, f"{rel} names a workspace coordinate despite the path exemption: {hits}"


def test_no_allow_comments_anywhere():
    """The per-line escape hatch exists, and its total count is pinned at zero.

    Not because an exception could never be justified — because an unbounded escape hatch
    turns a gate into a suggestion. Adding the first one should require changing this number
    and saying why in the commit."""
    used = []
    for rel in scannable():
        if rel in PATH_EXEMPT:
            continue  # these two define and test the marker itself
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _scan.ALLOW_MARKER in line:
                used.append(f"{rel}:{lineno}")
    assert used == [], f"allow-comments in use (pinned at zero): {used}"
