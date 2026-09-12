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

**Cost, MEASURED 2026-09-12 — and the number this docstring used to carry was wrong by three
orders of magnitude.** It said "a couple of seconds". It was **5318.86 s — 1 h 28 m 38 s — on
Windows**, and ~754 s on Linux CI, where it was **74-82% of the entire suite job** across seven
green runs and swung 336 s between two of them. The job is capped at 20 minutes with 263 s of
headroom, so one test's observed variance already exceeded every second left: the next variance
event is a timeout, and a timeout is a red nobody can read, which is a red people learn to
bypass. The thing being bypassed would have been this file.

**Why it cost that, and why the obvious remedy was the wrong one.** This docstring used to
advise bounding the walk to commits since the last verified-clean tag. Measured, that remedy
buys little and costs a great deal:

- 370 commits reference **106,278** (commit, path) pairs, behind just **1,467 unique blobs**.
  A blob's content is a pure function of its sha, so the old loop fetched and re-scanned the
  same ~1,456 bytestrings **about 73 times each**. The cost was the redundancy, never the
  length of history — bounding attacks the wrong term.
- A bound stops re-examining everything before the mark, and makes the mark load-bearing: move
  it, delete it, or never create it and the guard silently covers less. Worse, whoever can
  write the mark can assert *clean up to here* **without that having been checked** — which is
  a way to skip this check dressed as a way to bound it.

So the redundancy is what went, and the walk did not. **Every commit and every path is still
examined**; only the blob *read* is deduplicated, through one `git cat-file --batch` process
instead of 106,278 `git show` spawns. **MEASURED on the same Windows machine: 34.3 s** — the
walk that builds the map 15.75 s, the batch read 0.34 s, the scan of 1,456 unique blobs
18.24 s. A **156x** reduction with nothing given away and no new thing to trust. Expected CI
cost, **INFERRED from W205's measured 6.9-7.5x Windows spawn penalty and not yet observed
there: roughly 20-25 s**, against ~754 s before.

**The one-spawn shortcut that was deliberately not taken.** `git rev-list --all --objects`
hands you a blob→path map in 0.075 s, and on today's history it misses nothing. It prints each
object **once**, with whichever path the traversal happened to see first, and one blob in this
history already lives at two paths. The day a blob lives at both a `.png` path and a `.py` one,
the skipped spelling can win the print and that blob is dropped without a word. Zero-today is
not zero-by-construction, and a guard that silently covers less is the most expensive shape
this repository has. The per-commit `ls-tree` walk costs 15.75 s and is faithful by
construction; that is the right side of that trade.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.tests import _repo, _scan

ROOT = Path(__file__).resolve().parents[2]
BANLIST = Path(__file__).resolve().parent / "banned_tokens.txt"

#: The digest list itself: hashes only, and hashing them again is circular.
SKIP_PATHS = frozenset({"tools/tests/banned_tokens.txt"})

SKIP_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".exe", ".dll",
    ".so", ".dylib", ".whl", ".pyc", ".woff", ".woff2", ".ttf", ".lock",
})


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout


def all_commits(root: Path = ROOT) -> list[str]:
    return _git(root, "rev-list", "--all").split()


def history_blobs(root: Path) -> tuple[dict[str, set[str]], int]:
    """Every (commit, path) pair in history, collapsed onto the blob shas behind them.

    Returns the scannable map *and the raw pair count*, because the pair count is the only
    evidence that the walk did not shrink. `ls-tree -r` rather than `--name-only`: the sha is
    already in that output, so deduplicating the read costs no extra process.
    """
    by_sha: dict[str, set[str]] = {}
    pairs = 0
    for commit in all_commits(root):
        for line in _git(root, "ls-tree", "-r", commit).splitlines():
            meta, _, rel = line.partition("\t")
            fields = meta.split()
            if len(fields) < 3 or fields[1] != "blob":
                continue
            pairs += 1
            if rel in SKIP_PATHS or Path(rel).suffix.lower() in SKIP_SUFFIXES:
                continue
            by_sha.setdefault(fields[2], set()).add(rel)
    return by_sha, pairs


def read_blobs(root: Path, shas: list[str]) -> dict[str, str]:
    """Read every named blob in ONE `git cat-file --batch` process.

    **Every requested sha must come back, and a short read is an error rather than an empty
    string.** That is half the hazard of batching: if the stream is truncated, or the record
    framing drifts, each unread blob becomes `""`, every `""` scans clean, and the guard
    reports success having read nothing. That is precisely the failure this file exists to
    refuse, so it is asserted rather than tolerated.

    The other half cannot be checked here and is checked in `offenders_in_history`: this
    function can only ask whether it returned what it was *asked for*. Being asked for too few
    shas is invisible from inside, so coverage is counted against the walk, not against this
    argument.

    Decoded `utf-8`/`replace` to match what the per-blob `git show` produced. Line endings are
    *not* newline-translated here and were before; it cannot matter, because `tokenize()`
    splits on every non-alphanumeric character, so `\\r` is a separator either way.
    """
    if not shas:
        return {}
    proc = subprocess.run(["git", "-C", str(root), "cat-file", "--batch", "--buffer"],
                          input=("\n".join(shas) + "\n").encode("ascii"),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = proc.stdout
    contents: dict[str, str] = {}
    pos = 0
    while pos < len(out):
        nl = out.find(b"\n", pos)
        if nl < 0:
            break
        header = out[pos:nl].decode("ascii", "replace").split()
        pos = nl + 1
        if len(header) < 3 or header[1] != "blob":
            break
        sha, size = header[0], int(header[2])
        contents[sha] = out[pos:pos + size].decode("utf-8", "replace")
        pos += size + 1

    missing = [s for s in shas if s not in contents]
    assert not missing, (
        f"`git cat-file --batch` returned {len(contents)} of {len(shas)} blobs; "
        f"{len(missing)} were not read, first {missing[:3]}.\n"
        f"exit {proc.returncode}, stderr: {proc.stderr.decode('utf-8', 'replace')[:400]}\n\n"
        "This is COULD-NOT-LOOK, not a clean result. An unread blob would scan as the empty "
        "string and pass, so the history guard would report success over content it never saw."
    )
    return contents


def offenders_in_history(root: Path, banned: set[str]) -> tuple[dict[str, set[str]], int]:
    """Banned coordinates surviving anywhere in `root`'s history, keyed by path.

    Takes `banned` rather than reading the ban list, so a positive control can prove the walk
    really reaches deleted content using a phrase of its own invention — planting a *real*
    banned coordinate in a fixture to test the guard would create the exact finding the guard
    exists to prevent.

    **The coverage assertion is here, against the WALK, and that is the point of it.**
    `read_blobs` also refuses a short read, but it compares its result against the sha list it
    was *handed* — which proves only that the reader echoed its input, and is blind to anything
    that shortens the list on the way in. Batching created that list; nothing on `main` had one
    to shorten. So the number of blobs actually *scanned* is compared with the number the walk
    found, which is the quantity the guard's coverage claim is made of.
    """
    by_sha, pairs = history_blobs(root)
    offenders: dict[str, set[str]] = {}
    scanned = 0
    for sha, content in read_blobs(root, list(by_sha)).items():
        scanned += 1
        hits = [f.evidence for f in _scan.scan_text(content, banned) if f.kind == "token"]
        if hits:
            for rel in by_sha[sha]:
                offenders.setdefault(rel, set()).update(hits)
    assert scanned == len(by_sha), (
        f"{scanned} of the {len(by_sha)} blobs this walk found were scanned; "
        f"{len(by_sha) - scanned} were never looked at.\n\n"
        "COULD-NOT-LOOK, and `read_blobs`' own short-read check cannot see it: that one asks "
        "whether the reader returned what it was asked for, not whether it was asked for "
        "everything. Counted here against the walk instead, so any narrowing of the read — a "
        "capped batch, a chunked read that loses a chunk — fails rather than scanning fewer "
        "blobs and reporting a clean history."
    )
    return offenders, pairs


def test_there_is_history_to_check():
    """Three ways for the scan below to be meaningless, and none announces itself.

    The shallow case is the one that actually happened: `actions/checkout` defaults to depth
    1, so CI handed this file a single commit containing every file. `all_commits()` then
    returns one entry, the every-blob scan narrows to the working tree that
    `test_no_private_coordinates.py` already covers, and the whole history guard reports
    success while checking nothing new. It is asserted first because it is the specific
    failure, and its message names the specific fix.

    The third is the ban list being empty: `find_banned_tokens()` returns `[]` outright for an
    empty set (`_scan.py:142`), so emptying `banned_tokens.txt` turns every assertion in this
    file green without removing a single line of it.
    """
    assert not _repo.is_shallow(ROOT), _repo.SHALLOW_MESSAGE
    assert len(all_commits()) > 1, "no history found — is this a git checkout?"
    assert _scan.load_banned(BANLIST), (
        f"{BANLIST.name} holds no digests, so every scan in this file matches nothing and "
        "passes. An empty ban list is a disabled guard, not a clean repository."
    )


def test_no_banned_coordinate_survives_anywhere_in_history():
    """Every blob, every commit. Zero findings, or the redaction is not finished.

    The failure message names the file and the offending phrase but no commit, because the
    fix is never "amend that commit" — it is another rewrite pass over everything.
    """
    banned = _scan.load_banned(BANLIST)
    offenders, pairs = offenders_in_history(ROOT, banned)

    assert pairs > len(all_commits()), (
        f"the walk examined only {pairs} (commit, path) pairs across {len(all_commits())} "
        "commits, which is too few to be a walk of this history — `ls-tree -r` output is "
        "probably no longer being parsed, and an unparsed tree scans as zero blobs and passes."
    )
    assert not offenders, (
        "banned coordinates survive in git history:\n  "
        + "\n  ".join(f"{rel}: {sorted(ev)}" for rel, ev in sorted(offenders.items()))
        + "\n\nDeleting the file at HEAD does not help — the content stays reachable. This "
          "needs another `git filter-repo --replace-text` pass over all history, followed by "
          "a force-push AND a fresh clone of the remote to confirm what others can see."
    )


#: An invented phrase, not a real coordinate. The guard's positive control has to plant
#: something the matcher will hit, and planting a genuine banned coordinate in a fixture would
#: manufacture the exact finding this file exists to prevent — permanently, because a fixture
#: committed once stays reachable. So the control supplies its own ban set (one digest of this
#: phrase) and the real list is never involved.
DECOY = "quorbix lanthe"


def _commit_all(repo: Path, message: str) -> None:
    for args in (("add", "-A"),
                 ("-c", "user.name=t", "-c", "user.email=t@example.invalid",
                  "-c", "commit.gpgsign=false", "commit", "-m", message)):
        subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


def _repo_with_deleted_decoy(tmp_path: Path) -> Path:
    """A history where the decoy exists in commit 1 and is gone by HEAD."""
    repo = tmp_path / "hist"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], capture_output=True, check=True)
    (repo / "keep.md").write_text("nothing to see\n", encoding="utf-8")
    (repo / "leak.md").write_text(f"the owner is {DECOY} and it should not be here\n",
                                 encoding="utf-8")
    _commit_all(repo, "the accident")
    (repo / "leak.md").unlink()
    _commit_all(repo, "deleted the file, which is not the same as unreachable")
    return repo


def test_the_walk_reaches_content_that_is_not_at_head(tmp_path: Path):
    """The positive control this guard went its whole life without.

    Until now it had only ever been *observed passing*, which is compatible with it scanning
    nothing at all — the shape this repository pays for most often. This plants an offender,
    deletes it, and requires the walk to find it anyway: `git grep` at HEAD cannot, and that
    difference is the entire reason the file exists.
    """
    repo = _repo_with_deleted_decoy(tmp_path)
    banned = {_scan.digest(DECOY)}

    assert not _git(repo, "grep", "-l", DECOY.split()[0], "HEAD").splitlines(), (
        "the decoy is still at HEAD, so this control would pass even for a guard that only "
        "ever looks at the working tree — the fixture is not testing what it claims."
    )

    offenders, pairs = offenders_in_history(repo, banned)
    assert "leak.md" in offenders, (
        f"the history walk missed a deleted blob: found {offenders!r} over {pairs} pairs"
    )
    assert DECOY in offenders["leak.md"]


def test_the_deduplicated_read_scans_what_a_per_pair_walk_would(tmp_path: Path):
    """The weld. Deduplicating the read must not quietly narrow what is read.

    Run over the same fixture: the batched, sha-keyed reader against a literal
    `git show <commit>:<path>` per pair — the loop this replaced. Same offenders, or the
    optimisation bought speed by looking at less. The fixture keeps one blob alive across both
    commits and one blob at two paths at once, because those are the two cases a sha-keyed map
    can collapse wrongly.
    """
    repo = _repo_with_deleted_decoy(tmp_path)
    (repo / "copy.md").write_text(f"the owner is {DECOY} and it should not be here\n",
                                 encoding="utf-8")
    _commit_all(repo, "the same content under a second name")
    banned = {_scan.digest(DECOY)}

    naive: dict[str, set[str]] = {}
    naive_pairs = 0
    for commit in all_commits(repo):
        for rel in _git(repo, "ls-tree", "-r", "--name-only", commit).splitlines():
            naive_pairs += 1
            if rel in SKIP_PATHS or Path(rel).suffix.lower() in SKIP_SUFFIXES:
                continue
            hits = [f.evidence for f in
                    _scan.scan_text(_git(repo, "show", f"{commit}:{rel}"), banned)
                    if f.kind == "token"]
            if hits:
                naive.setdefault(rel, set()).update(hits)

    offenders, pairs = offenders_in_history(repo, banned)
    assert offenders == naive, f"deduplicated walk found {offenders!r}, per-pair walk {naive!r}"
    assert pairs == naive_pairs, f"deduplicated walk saw {pairs} pairs, per-pair walk {naive_pairs}"
    assert set(naive) == {"leak.md", "copy.md"}, (
        f"the control itself is not exercising both cases: {sorted(naive)}"
    )


def test_a_blob_that_could_not_be_read_is_loud_rather_than_clean(tmp_path: Path):
    """Could-not-look is not a pass — asserted where batching put the risk.

    One `cat-file --batch` process replaced 106,278 `git show` calls, and it brought two new
    ways to fail silently. This is the first: an unreturned blob becomes `""`, and `""` scans
    clean, so a sha the object store cannot produce must raise rather than shorten the result.
    The second — being handed too few shas in the first place — is invisible from in here and
    is the test below.
    """
    repo = _repo_with_deleted_decoy(tmp_path)
    real = next(iter(history_blobs(repo)[0]))
    absent = "0" * 40

    assert read_blobs(repo, [real])[real], "the control read nothing for a blob that exists"

    with pytest.raises(AssertionError) as caught:
        read_blobs(repo, [real, absent])
    assert absent in str(caught.value)
    assert "COULD-NOT-LOOK" in str(caught.value)


def test_a_read_that_covers_less_than_the_walk_is_loud_rather_than_clean(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The hazard the batching *created*, as opposed to the one it inherited.

    The test above proves `read_blobs` refuses to return fewer blobs than it was asked for.
    That check is necessary and it is not sufficient, because it compares the result against
    the list it was *handed*: ask it for less and it answers honestly, completely, and about
    less history than the walk found. Per-pair `git show` had no such list to shorten — this
    one is the price of the batch, and a chunked read added later to bound memory is the most
    likely way to shorten it by accident.

    So the real reader is kept and simply asked for one blob fewer. Its own assertion passes;
    the coverage assertion in `offenders_in_history` must fail, and must report the number the
    *walk* found rather than the number that was requested — the numbers in the message are
    asserted for exactly that reason.
    """
    repo = _repo_with_deleted_decoy(tmp_path)
    banned = {_scan.digest(DECOY)}
    walked = len(history_blobs(repo)[0])
    assert walked > 1, "the fixture needs more than one blob for 'one fewer' to mean anything"
    assert offenders_in_history(repo, banned)[0], "unpatched, the decoy must still be found"

    real = read_blobs
    monkeypatch.setitem(globals(), "read_blobs",
                        lambda root, shas: real(root, shas[:-1]))

    with pytest.raises(AssertionError) as caught:
        offenders_in_history(repo, banned)
    message = str(caught.value)
    assert f"{walked - 1} of the {walked} blobs" in message, message
    assert "never looked at" in message
    assert "COULD-NOT-LOOK" in message


def test_an_empty_ban_list_is_a_disabled_guard_not_a_clean_repository(tmp_path: Path):
    """Why `test_there_is_history_to_check` now asserts the ban list is non-empty.

    `find_banned_tokens()` short-circuits on an empty set (`_scan.py:142`), so emptying
    `banned_tokens.txt` makes the every-blob scan report zero findings — indistinguishable from
    success, and reached by deleting a data file rather than a test. Demonstrated on the decoy
    repo, whose offender is known to be findable, so the zero can only come from the empty set;
    over real history the same zero would prove nothing, and would cost a second full walk.
    """
    repo = _repo_with_deleted_decoy(tmp_path)
    assert offenders_in_history(repo, {_scan.digest(DECOY)})[0], "the decoy must be findable"
    assert offenders_in_history(repo, set())[0] == {}, "an empty ban list finds nothing"
    assert _scan.load_banned(BANLIST), "and this is the assertion that stops that mattering"


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
    hits = _git(ROOT, "grep", "-l", word, "HEAD").splitlines()
    assert hits, (
        f"{word!r} has vanished from the tree — a redaction pass probably ate it as a "
        f"substring of a banned coordinate. Check the lookarounds in the replace-text rules."
    )
