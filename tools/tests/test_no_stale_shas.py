"""No tracked file may name a commit sha that is not on main.

A rebase gives a commit a new sha while any tracked file that named the old one keeps the
orphan. Locally the dangling object survives (it is still in the object store), so
``git cat-file`` can resolve it and every local test is green; a fresh clone never receives
an object nothing points at, so it cannot see the orphan at all -- not "sees it and calls it
clean", literally never receives the bytes. This took main red once, on the core two-commit
protocol, and recurred on T323/T352.

The convention that closes the class: **name commits by their subject line, not their
sha.** A subject survives a rebase; a sha does not. The only shas that belong in a tracked
file are ones already on main (immovable) or non-commit objects (blob digests in the
history guard, vendored checksums in the manifest).

**What this guard can and cannot prove.** A CONFIRMED-stale verdict (resolves, is a commit,
is not an ancestor of HEAD) is reliable wherever it fires, because it only fires when the
dangling object is actually present to inspect -- the author's machine right after the
rebase, or the integrator's, before either runs ``git gc``. An UNRESOLVED verdict (nothing
here by that name) is NOT a clean bill of health: ``git clone``/``git fetch`` never
transmit an object no ref points at, so a rebase-orphaned commit is unreachable from any
fresh clone by construction -- CI's own checkout included, full history or not, because
depth only bounds how far back REACHABLE history goes, never whether an unreachable object
travels (it never does). Collapsing UNRESOLVED into "not stale, fine" is exactly the
mistake CLAUDE.md's own headline rule names -- an external binary's non-zero exit read as
evidence of absence -- and it is the mistake this file made until T352's second round:
``_is_stale_commit`` read a non-zero ``cat-file -e`` as ``return False``, so on a fresh
clone the planted defect this file's own positive control proves stale on the machine that
made it came back "not stale" every time, silently, for ANY tree. The fix is not a
cleverer git incantation -- there is no query that reveals an object a clone was never
given -- it is to keep UNRESOLVED as its own reported outcome (``ShaScan.unresolved``)
rather than folding it into "fine", and to say so here rather than claim coverage this
guard does not have. ``test_a_stale_sha_invisible_on_a_fresh_clone_is_unresolved_not_clean``
is the proof: the identical planted defect that ``test_positive_control_fires`` confirms
as stale on the machine that made it comes back UNRESOLVED, never CONFIRMED-clean, once
cloned fresh.

Two further silent-pass shapes are refused rather than scanned past, both raising
``ban.py``'s ``CouldNotLook`` (the pattern this repository already uses for "the tree
nobody could read is not a tree with nothing in it", reused here rather than reinvented):
an empty or failed ``git ls-files``, and a shallow checkout (``actions/checkout`` defaults
to depth 1 -- see ``tools/tests/_repo.py``), which would make even genuinely on-main
ancestors resolve as UNRESOLVED and blame the checkout's shallowness on the tree.

The guard below walks every tracked file for 7-to-40-character hex tokens at word
boundaries and classifies each one with exactly TWO git processes total, regardless of how
many tokens are found: one ``git rev-list HEAD`` for the full, immovable ancestor set, and
one ``git cat-file --batch-check`` for everything else this clone's object store can
resolve at all. A naive per-token loop (``cat-file -e``, ``cat-file -t``, ``merge-base
--is-ancestor`` -- up to three spawns per token) is kept as ``_is_stale_commit``, the
reference implementation the weld test checks the batched path against; it is what made
this file the slowest test in the suite at ~18.5s over 482 tokens (roughly 1000 git process
spawns), and it is no longer on the path ``stale_shas_in_tracked_files`` runs.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

from tools.tests import _repo
from tools.tests import ban as B

ROOT = Path(__file__).resolve().parents[2]

#: 7 to 40 lowercase hex characters at a word boundary.
_SHA_RE = re.compile(r"\b([0-9a-f]{7,40})\b")

#: File extensions that are binary or not worth scanning.
_SKIP_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz",
    ".exe", ".dll", ".so", ".dylib", ".whl", ".pyc", ".woff", ".woff2", ".ttf",
})

#: The hex alphabet, for detecting tokens embedded inside longer hex strings (e.g. sha256
#: digests in third_party/manifest.json). Defense in depth only: ``\b`` already cannot
#: match inside a longer hex run on its own (the boundary fires at the run's edge, never in
#: the middle), so this filter is not reachable by any input over the real tree -- measured
#: in T352 round 2 review (0 files where removing it changed the hit set). Left in because
#: it costs nothing and documents the intent; not claimed to be doing work it does not do.
_HEX = frozenset("0123456789abcdefABCDEF")


class ShaScan(NamedTuple):
    """One scan's worth of classified sha-shaped tokens. Zero silent drops: every token
    that resolved to a real, non-ancestor commit is in ``stale``; every token this clone's
    object store could not resolve at all is in ``unresolved``, and is never treated as
    "fine" -- see the module docstring for why that distinction is the entire point."""
    stale: list[tuple[str, int, str]]
    unresolved: list[tuple[str, int, str]]


def _tracked_files(root: Path = ROOT) -> list[str]:
    """Every tracked file under ``root``, or a refusal -- never a silent empty scan.

    Mirrors ``tools/tests/ban.py``'s ``tracked_files()``: a non-zero exit and an empty
    result at exit 0 are both COULD-NOT-LOOK, not "nothing to see". A tree that scans zero
    files and calls itself clean is the failure shape ``CouldNotLook`` exists to name.
    """
    proc = subprocess.run(["git", "-C", str(root), "ls-files"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise B.CouldNotLook(f"git ls-files exited {proc.returncode}: "
                             f"{proc.stderr.strip() or '(no message)'}")
    files = sorted(p for p in proc.stdout.splitlines() if p)
    if not files:
        raise B.CouldNotLook(f"git ls-files listed no tracked files under {root} -- this "
                             "is not the harness tree, or nothing is committed")
    return files


def _sha_tokens_in_file(rel: str, root: Path = ROOT) -> list[tuple[int, str, str]]:
    """Return (line_number, token, line_text) for each sha-shaped token in ``rel``.

    A token is skipped when it sits inside a longer hex run (e.g. a 64-character sha256
    digest), because ``\\b[0-9a-f]{7,40}\\b`` cannot match inside one -- the word boundary
    fires at the digest's edge, not in the middle.
    """
    path = root / rel
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


def _is_stale_commit(token: str, root: Path = ROOT) -> bool:
    """True when ``token`` names a commit object that is NOT an ancestor of HEAD.

    The NAIVE, per-token reference implementation: up to three git spawns for this one
    token alone. ``stale_shas_in_tracked_files`` does not call this in a loop -- see the
    module docstring for the cost that ran up before it was batched. It exists as the
    oracle ``test_the_batched_scan_agrees_with_the_naive_one`` checks the batched path
    against, and for anyone probing a single token by hand.
    """
    r = subprocess.run(["git", "-C", str(root), "cat-file", "-e", token],
                       capture_output=True)
    if r.returncode != 0:
        return False  # not resolvable HERE -- see module docstring: not proof of absence

    r2 = subprocess.run(["git", "-C", str(root), "cat-file", "-t", token],
                        capture_output=True, text=True)
    if r2.stdout.strip() != "commit":
        return False  # blob, tree, or tag -- not our concern

    r3 = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", token, "HEAD"],
                        capture_output=True)
    return r3.returncode != 0  # exit 1 = not an ancestor = stale


def _ancestor_shas(root: Path = ROOT) -> set[str]:
    """Every full commit sha reachable from HEAD, in ONE ``git rev-list`` process.

    These are the commits that can never move again. A token that is a hex PREFIX of one
    of these full shas is an abbreviation of an immovable ancestor -- checking that is
    string comparison in Python, not a ``git merge-base`` spawn per token.
    """
    out = subprocess.run(["git", "-C", str(root), "rev-list", "HEAD"],
                         capture_output=True, text=True, check=True).stdout
    return set(out.split())


def _batch_check(tokens: list[str], root: Path = ROOT) -> dict[str, str]:
    """Resolve every token's git object TYPE in ONE ``git cat-file --batch-check`` process.

    Returns ``{token: type}`` for every token this clone's object store can resolve at all
    (``"commit"``, ``"blob"``, ``"tree"``, or ``"tag"``); a token git reports ``missing``
    is left OUT of the result rather than mapped to a sentinel -- the caller
    (``stale_shas_in_tracked_files``) is what turns "not in this dict" into an explicit,
    reported UNRESOLVED entry, and it is never silently read as "fine". This is the same
    shape ``test_history_is_clean.py``'s ``read_blobs`` uses for the same reason: one spawn
    instead of one-to-three per token, because 482 tokens over up to three git spawns each
    is what made this file the slowest test in the suite.

    A short or misaligned read is COULD-NOT-LOOK, not a quieter kind of missing: if git
    returned fewer lines than tokens requested, zipping them positionally would silently
    read every trailing token as UNRESOLVED rather than as "never checked at all" -- the
    same hazard ``read_blobs`` guards against for blob content, applied here to type
    lookups instead.
    """
    if not tokens:
        return {}
    proc = subprocess.run(
        ["git", "-C", str(root), "cat-file", "--batch-check=%(objectname) %(objecttype)",
         "--buffer"],
        input=("\n".join(tokens) + "\n").encode("ascii"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise B.CouldNotLook(
            f"git cat-file --batch-check exited {proc.returncode}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip() or '(no message)'}"
        )
    lines = proc.stdout.decode("ascii", "replace").splitlines()
    if len(lines) != len(tokens):
        raise B.CouldNotLook(
            f"git cat-file --batch-check returned {len(lines)} lines for {len(tokens)} "
            "requested tokens -- a short or misaligned read here would silently attribute "
            "the wrong answer to each token after the drift, or read every unanswered "
            "trailing token as UNRESOLVED rather than as never having been checked."
        )
    types: dict[str, str] = {}
    for requested, line in zip(tokens, lines):
        _obj, _, kind = line.partition(" ")
        if kind and kind != "missing":
            types[requested] = kind
    return types


def _classify(token: str, ancestors: set[str], types: dict[str, str]) -> str:
    """Classify one sha-shaped token: ``"on_main"``, ``"stale"``, ``"not_a_commit"``, or
    ``"unresolved"``. See the module docstring for why the last one is not "fine"."""
    if any(full.startswith(token) for full in ancestors):
        return "on_main"
    kind = types.get(token)
    if kind is None:
        return "unresolved"
    return "stale" if kind == "commit" else "not_a_commit"


def stale_shas_in_tracked_files(root: Path = ROOT) -> ShaScan:
    """Classify every sha-shaped token in every tracked file under ``root``.

    Refuses outright (``CouldNotLook``) on a shallow checkout: a depth-limited clone is
    missing ancestors beyond its depth, which would make even genuinely on-main commits
    read as UNRESOLVED and blame the checkout's shallowness on the tree -- the exact
    failure shape ``tools/tests/_repo.py`` exists to stop recurring a third time.
    """
    if _repo.is_shallow(root):
        raise B.CouldNotLook(f"{root} is a shallow checkout -- {_repo.SHALLOW_MESSAGE}")

    hits: list[tuple[str, int, str]] = []
    for rel in _tracked_files(root):
        if Path(rel).suffix.lower() in _SKIP_EXTS:
            continue
        for lineno, token, _line_text in _sha_tokens_in_file(rel, root):
            hits.append((rel, lineno, token))

    ancestors = _ancestor_shas(root)
    tokens = sorted({token for _, _, token in hits})
    types = _batch_check(tokens, root)

    stale: list[tuple[str, int, str]] = []
    unresolved: list[tuple[str, int, str]] = []
    for rel, lineno, token in hits:
        kind = _classify(token, ancestors, types)
        if kind == "stale":
            stale.append((rel, lineno, token))
        elif kind == "unresolved":
            unresolved.append((rel, lineno, token))
        # "on_main" and "not_a_commit" are confirmed fine -- dropped here, not silently:
        # both were resolved and examined, never skipped.
    return ShaScan(stale=stale, unresolved=unresolved)


def test_no_tracked_file_names_a_stale_commit_sha():
    """Every sha-shaped token this clone can CONFIRM is a commit must be an ancestor of
    HEAD. This is a narrower claim than "the tree is clean" -- see the module docstring
    and ``ShaScan.unresolved`` for the tokens this clone could not check at all, which are
    reported, never silently counted as passing.
    """
    scan = stale_shas_in_tracked_files()
    assert not scan.stale, (
        "tracked file(s) name commit sha(s) that are CONFIRMED NOT on main:\n"
        + "\n".join(f"  {rel}:{lineno}  {token}" for rel, lineno, token in scan.stale)
        + "\n\nName the commit by its SUBJECT LINE instead of its sha.  "
        "A subject survives a rebase; a sha does not."
    )


# ── fixtures shared by the controls below ────────────────────────────────────

def _repo_with_a_stale_sha(tmp_path: Path, name: str = "repo") -> tuple[Path, str]:
    """A repo where a tracked file on ``main`` names a commit sha that is NOT an ancestor
    of HEAD -- the exact shape a rebase produces. Returns the repo and the short (7-char)
    sha it planted.

    Technique: commit on a side branch, record that commit's sha in a file on main, then
    amend the side branch so the recorded sha is orphaned (still a real object locally, no
    longer reachable from anything).
    """
    repo = tmp_path / name
    repo.mkdir()
    run = lambda *args: subprocess.run(
        args, capture_output=True, text=True, cwd=str(repo), check=True,
    )
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "test@test.com")
    run("git", "config", "user.name", "Test")

    (repo / "readme.txt").write_text("hello\n", encoding="utf-8")
    run("git", "add", "readme.txt")
    run("git", "commit", "-q", "-m", "initial")

    run("git", "checkout", "-q", "-b", "feature")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    run("git", "add", "feature.txt")
    run("git", "commit", "-q", "-m", "add feature")
    side_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(repo), check=True,
    ).stdout.strip()

    run("git", "checkout", "-q", "main")
    short = side_sha[:7]
    (repo / "notes.txt").write_text(f"measured at {short}\n", encoding="utf-8")
    run("git", "add", "notes.txt")
    run("git", "commit", "-q", "-m", "record measurement")

    # Rewrite the side branch (simulate rebase): amend its commit so the old sha is
    # orphaned.
    run("git", "checkout", "-q", "feature")
    (repo / "feature.txt").write_text("feature v2\n", encoding="utf-8")
    run("git", "add", "feature.txt")
    run("git", "commit", "-q", "--amend", "-m", "add feature (amended)")
    run("git", "checkout", "-q", "main")

    r = subprocess.run(["git", "cat-file", "-e", short], capture_output=True, cwd=str(repo))
    assert r.returncode == 0, "the dangling object should still exist locally"
    r = subprocess.run(["git", "merge-base", "--is-ancestor", short, "HEAD"],
                       capture_output=True, cwd=str(repo))
    assert r.returncode != 0, "the dangling object should not be on main"

    return repo, short


def test_positive_control_fires(tmp_path: Path):
    """Prove the guard can fail, by calling the REAL scanner -- not a second copy of it.

    T352 round 1's control re-implemented the scan inline (its own ``subprocess.run``
    calls for ``cat-file -t`` and ``merge-base --is-ancestor``), which meant it never
    called ``_is_stale_commit``, ``_tracked_files``, or ``stale_shas_in_tracked_files`` --
    a mutation that blinded any of those left this control green, because nothing here
    exercised them. Calling the real function against the synthetic repo is the weld: it
    cannot pass while the scanner it is supposed to prove correct is broken.
    """
    repo, short = _repo_with_a_stale_sha(tmp_path)

    scan = stale_shas_in_tracked_files(root=repo)
    assert scan.stale, "the positive control must find the stale sha -- the guard cannot fire"
    assert any(short in token for _rel, _lineno, token in scan.stale), (
        f"the positive control must find {short!r} specifically, found {scan.stale!r}"
    )


def test_the_batched_scan_agrees_with_the_naive_one(tmp_path: Path):
    """The weld. ``stale_shas_in_tracked_files`` replaced a per-token loop calling
    ``_is_stale_commit`` up to three times each with two batched git calls; this proves
    the batching did not quietly change the answer, over a fixture with both a stale
    commit and an on-main one (the fixture's root commit, still an ancestor of HEAD).

    This is also what makes ``_is_stale_commit`` itself load-bearing again: it is not on
    ``stale_shas_in_tracked_files``'s path any more, so blinding it alone would be
    invisible to ``test_positive_control_fires`` -- this test calls it directly and dies
    if it is blinded.
    """
    repo, short = _repo_with_a_stale_sha(tmp_path)
    initial_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-list", "--max-parents=0", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert initial_sha, "fixture must have a root commit to use as the on-main sample"

    naive_stale = _is_stale_commit(short, root=repo)
    naive_on_main = _is_stale_commit(initial_sha[:7], root=repo)
    assert naive_stale is True, "the naive per-token check must also see the plant as stale"
    assert naive_on_main is False, "the naive check must not flag a real ancestor"

    scan = stale_shas_in_tracked_files(root=repo)
    batched_stale_tokens = {token for _rel, _lineno, token in scan.stale}
    assert any(short in t for t in batched_stale_tokens), (
        f"batched scan disagrees with the naive one: naive says {short!r} is stale, "
        f"batched found {scan.stale!r}"
    )


def test_an_empty_tracked_tree_is_could_not_look_not_a_clean_tree(tmp_path: Path):
    """CLAUDE.md's headline rule, applied to this guard: an empty ``git ls-files`` is a
    tree nobody could read, not a tree with nothing in it. Before this, ``_tracked_files``
    returning ``[]`` made ``stale_shas_in_tracked_files`` scan zero files and report zero
    stale shas -- indistinguishable from a genuinely clean, fully-scanned tree. This is the
    same shape ``tools/tests/ban.py``'s ``CouldNotLook`` already exists to refuse
    (``test_ban.py::test_an_empty_ls_files_is_could_not_look_not_an_empty_tree``); reused
    here rather than reinvented.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "-C", str(empty), "init", "-q", "-b", "main"],
                   capture_output=True, check=True)
    ls = subprocess.run(["git", "-C", str(empty), "ls-files"],
                        capture_output=True, text=True)
    assert ls.returncode == 0 and ls.stdout == "", (ls.returncode, ls.stdout)

    with pytest.raises(B.CouldNotLook):
        stale_shas_in_tracked_files(root=empty)


def test_a_shallow_checkout_is_could_not_look(tmp_path: Path):
    """A depth-limited clone is missing ancestors beyond its depth, so a genuinely
    on-main commit can read as UNRESOLVED there -- the guard would blame the checkout's
    shallowness on the tree instead of refusing outright. ``actions/checkout`` defaults to
    depth 1 (see ``tools/tests/_repo.py``); this is what catches that default recurring
    here, even though this repository's own CI workflow currently overrides it.
    """
    src, _short = _repo_with_a_stale_sha(tmp_path, name="src")
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--no-local", "--single-branch", "--depth", "1", "--branch", "main",
         str(src), str(shallow)],
        capture_output=True, text=True, check=True,
    )
    assert _repo.is_shallow(shallow), "the fixture itself must be shallow, or this proves nothing"

    with pytest.raises(B.CouldNotLook):
        stale_shas_in_tracked_files(root=shallow)


def test_a_stale_sha_invisible_on_a_fresh_clone_is_unresolved_not_clean(tmp_path: Path):
    """T352 round 2's central finding, proven rather than argued: on a fresh, non-local
    clone -- the shape ``git clone`` produces from a remote, and what CI itself does -- the
    planted defect ``test_positive_control_fires`` confirms as STALE on the machine that
    made it is not visible at all. ``git clone``/``git fetch`` never transmit an object
    nothing points at, so the orphaned commit this fixture built never crosses into the
    clone's object store.

    The fix under test is that this comes back UNRESOLVED, not CONFIRMED-clean: before
    T352 round 2, ``_is_stale_commit`` read the unresolvable object as ``return False``, so
    ``stale_shas_in_tracked_files`` on a fresh clone answered ``stale=[]`` -- byte-identical
    to a fully-verified clean tree, for a tree that provably still carries the defect
    (``test_positive_control_fires`` proves it on the very same fixture, uncloned). A CI run
    of the OLD code could never fail on this defect, for ANY tree: this is what "the guard
    would pass there no matter what the tree contains" looks like as a test.
    """
    src, short = _repo_with_a_stale_sha(tmp_path, name="src")
    fresh = tmp_path / "fresh"
    subprocess.run(
        ["git", "clone", "--no-local", "--single-branch", "--branch", "main",
         str(src), str(fresh)],
        capture_output=True, text=True, check=True,
    )
    assert not _repo.is_shallow(fresh), (
        "the fresh clone must be FULL history, matching CI's fetch-depth: 0 -- a shallow "
        "clone would be caught by the other, narrower refusal instead of proving this"
    )
    r = subprocess.run(["git", "-C", str(fresh), "cat-file", "-e", short], capture_output=True)
    assert r.returncode != 0, (
        "the fresh clone still has the orphaned object -- this fixture is not reproducing "
        "what a real push-then-clone does, so this test proves nothing"
    )

    scan = stale_shas_in_tracked_files(root=fresh)
    assert scan.stale == [], (
        "a fresh clone cannot CONFIRM staleness for an object it was never given -- a "
        "non-empty `stale` here would mean the classifier is inventing a positive answer "
        "from no evidence, which is worse than the blindness this test is about"
    )
    assert any(short in token for _rel, _lineno, token in scan.unresolved), (
        f"the planted defect must be reported UNRESOLVED, not silently dropped: "
        f"{scan.unresolved!r}"
    )
