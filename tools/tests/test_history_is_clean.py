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
import threading
import tracemalloc
from pathlib import Path
from typing import NamedTuple

import pytest

from tools.tests import _repo, _scan
from tools.tests import test_no_private_coordinates as _tree_guard

ROOT = Path(__file__).resolve().parents[2]
BANLIST = Path(__file__).resolve().parent / "banned_tokens.txt"

#: The path exemption is IMPORTED from the tree guard rather than restated. The two guards
#: police the same rule over different material, and a second copy of the list is how one of
#: them ends up narrower than the other without anybody choosing that.
PATH_EXEMPT = _tree_guard.PATH_EXEMPT

#: Blobs that already carry a machine path and are already published, pinned by CONTENT
#: ADDRESS. **Keyed by blob sha, never by path and never by rule.** A path exemption widens
#: itself the day a file moves or a new file appears under it; a rule exemption switches the
#: matcher off everywhere. A sha names one immutable bytestring and can name nothing else —
#: the `.gitleaksignore` precedent, which is fingerprint-scoped for exactly this reason.
#:
#: **The coordinate is deliberately not written here, and neither is enough of it to
#: reconstruct.** Recording a coordinate in order to exempt it would put it into the tree the
#: sibling guard defends, permanently; the reason carries the shape and the situation only.
#:
#: A pin covers the CONTENT wherever it appears, so copying an offending blob to a new file
#: does not re-arm this guard — `test_no_private_coordinates.py` catches that at HEAD on the
#: commit that does it, which is the earlier and louder place for it to fail.
#:
#: **Every entry must still match something**: `test_no_machine_path_survives_anywhere_in_history`
#: fails on a pin that fired nothing, so a pin cannot outlive the finding it was written for.
PATH_PINNED: dict[str, str] = {
    "330669ed6844cd719239946c66fe4f5f5c894a70":
        "shape drive(D) x2; an early estate-check helper at a path NOT tracked at HEAD, so no "
        "widening of the tree guard could ever reach it; owner-ruled 2026-09-12",
    "7de6fbc8be1dbcf0703e661a55c13ba7dc5b27eb":
        "shape drive(D) x2; a later revision of the same untracked-at-HEAD helper; "
        "owner-ruled 2026-09-12",
    "28d20ffb195b624866ab29b806739d58ce511284":
        "shape drive(C); an exporter test whose path still exists at HEAD and whose HEAD "
        "version is clean, so only history carries it; owner-ruled 2026-09-12",
    "ecc5289ccf5d2aadd5fc2bdd243c24b3eac1b7d1":
        "shape home/<8c user> — the worst of the four, because the matcher only reports it "
        "when the account name is not a placeholder; HEAD version clean; owner-ruled 2026-09-12",
}

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


def reachable_commits(root: Path = ROOT) -> frozenset[str]:
    """Every commit any ref still points at, derived INDEPENDENTLY of the walk's commit list.

    The walk takes its commits from `all_commits()` (`git rev-list --all`). If completeness
    were checked against a second call to that same query it could never disagree: a bound
    added to `all_commits` — this file's own docstring discusses and rejects a since-a-tag
    bound, but nothing stops a future one — would narrow both sides equally and the check would
    stay green while covering less. So the ground truth is built the other way round.
    `git for-each-ref` enumerates the branch, remote and tag TIPS (dereferencing annotated
    tags), and one `git rev-list` seeded by those explicit tips closes them over their parents.
    Two git processes, neither of them `all_commits`.

    **This is why the blocked attempt's root-only check was the wrong remedy.** Checking only
    `--max-parents=0` roots catches a walk that never reached the beginning of history, but a
    walk that drops a commit in the MIDDLE still reaches the root, so `roots - walked` stays
    empty and the drop is invisible. The full ancestor closure names any missing commit
    wherever it sits — root, merge, or middle. And because the tips come from every ref rather
    than from HEAD, it also catches `git rev-list HEAD` substituted for `--all`, which reaches
    the root but silently drops every unmerged branch (MEASURED 2026-09-19 on this clone:
    rev-list --all = 749 commits, rev-list HEAD = 698, so 51 would vanish uncounted).

    The tips are scoped to refs/heads, refs/remotes and refs/tags, the same universe `--all`
    covers, so the closure is always a subset of what the walk visits on a healthy repo (no
    false positive) while still naming anything the walk drops from that universe.
    """
    tips: set[str] = set()
    for line in _git(root, "for-each-ref", "--format=%(objectname) %(*objectname)",
                     "refs/heads", "refs/remotes", "refs/tags").splitlines():
        tips.update(line.split())
    head = _git(root, "rev-parse", "HEAD").strip()
    if head:
        tips.add(head)
    if not tips:
        return frozenset()
    return frozenset(_git(root, "rev-list", *sorted(tips)).split())


def history_blobs(root: Path) -> tuple[dict[str, set[str]], int, frozenset[str]]:
    """Every (commit, path) pair in history, collapsed onto the blob shas behind them.

    Returns the scannable map, the raw pair count, and the SET of commits actually walked. The
    pair count is evidence the walk did not shrink to near-zero; the commit set is evidence
    (checked in `offenders_in_history` against `reachable_commits`) that it did not shrink to a
    partial subset a pair count cannot distinguish from the whole. `ls-tree -r` rather than
    `--name-only`: the sha is already in that output, so deduplicating the read costs no extra
    process.
    """
    by_sha: dict[str, set[str]] = {}
    pairs = 0
    walked: set[str] = set()
    for commit in all_commits(root):
        walked.add(commit)
        for line in _git(root, "ls-tree", "-r", commit).splitlines():
            meta, _, rel = line.partition("\t")
            fields = meta.split()
            if len(fields) < 3 or fields[1] != "blob":
                continue
            pairs += 1
            if rel in SKIP_PATHS or Path(rel).suffix.lower() in SKIP_SUFFIXES:
                continue
            by_sha.setdefault(fields[2], set()).add(rel)
    return by_sha, pairs, frozenset(walked)


#: The size of each raw read pulled from the content stream in `read_blobs`. It is a read
#: granularity, NOT a ceiling on anything: no history-size limit is expressed as a constant
#: here, on purpose. A fixed byte ceiling would redden this gate on ordinary history growth
#: and teach whoever hits it to raise the number rather than look — the failure mode this
#: guard's own docstring warns against. The bound on resident memory is structural (one blob
#: at a time), not numeric.
_READ_CHUNK = 1 << 20


def _batch_check_sizes(root: Path, shas: list[str]) -> dict[str, int]:
    """Every blob's size from `git cat-file --batch-check` — HEADERS ONLY, no content.

    This is the half of the read that used to be paid for in resident memory. The old code
    learned each blob's size only by having already transferred and buffered the blob itself
    — `proc.stdout` held the whole batch — so any size check ran AFTER the allocation it was
    meant to bound: a bound on the decode that copies the bytes, never on the read that
    allocated them. `--batch-check` prints the size straight from the object header and
    transfers no content at all (MEASURED 2026-09-19: 109,894 header bytes stand in for
    68,464,311 content bytes on this history), so the size of the read is known before a byte
    of content is allocated.

    It doubles as the short-read guard: a sha the object store cannot produce prints
    `<sha> missing` and never enters the map, so it is named as COULD-NOT-LOOK here rather
    than surfacing later as an empty string that scans clean.
    """
    proc = subprocess.run(["git", "-C", str(root), "cat-file", "--batch-check", "--buffer"],
                          input=("\n".join(shas) + "\n").encode("ascii"),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sizes: dict[str, int] = {}
    for line in proc.stdout.decode("ascii", "replace").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "blob":
            sizes[parts[0]] = int(parts[2])
    missing = [s for s in shas if s not in sizes]
    assert not missing, (
        f"`git cat-file --batch-check` resolved {len(sizes)} of {len(shas)} blobs to a size; "
        f"{len(missing)} did not, first {missing[:3]}.\n"
        f"exit {proc.returncode}, stderr: {proc.stderr.decode('utf-8', 'replace')[:400]}\n\n"
        "This is COULD-NOT-LOOK, not a clean result. An unread blob would scan as the empty "
        "string and pass, so the history guard would report success over content it never saw."
    )
    return sizes


def read_blobs(root: Path, shas: list[str]) -> dict[str, str]:
    """Read every named blob by STREAMING one `git cat-file --batch` process.

    **The read is bounded, not merely the decode.** The previous implementation buffered the
    whole batch — every unique blob's content — into one `proc.stdout` bytes object and only
    then walked it, so the entirety of history was resident before anything looked at it and
    any size assertion could bound only the decode that followed a copy already made (a factor
    of two, not a bound). Here the sizes come first from `_batch_check_sizes` (headers only, no
    content), and the content is then pulled from the pipe and decoded ONE blob at a time, each
    raw blob discarded before the next is read. Peak raw memory is a single blob, not the
    batch. MEASURED 2026-09-19 on an 8-blob / 8.5 MB fixture: the buffered read peaked at 2.13x
    the content, this streamed read at 1.13x — the whole-history raw copy is gone and only the
    decoded result the caller asked for remains. There is deliberately no fixed byte ceiling.

    **Every requested sha must come back, and a short read is an error rather than an empty
    string.** `_batch_check_sizes` refuses a sha the object store cannot produce before the
    stream even opens; this function additionally refuses a record whose framing drifts or a
    truncated stream, because either would let an unread blob become `""`, and `""` scans clean
    — the failure this file exists to refuse.

    The other half cannot be checked here and is checked in `offenders_in_history`: this
    function can only ask whether it returned what it was *asked for*. Being asked for too few
    shas is invisible from inside, so coverage is counted against the walk, not against this
    argument.

    A writer thread feeds the sha list to stdin while the main thread drains stdout, because
    feeding ~90 KB of shas up front and only then reading a 68 MB reply deadlocks the moment
    git's output fills the pipe while it is still blocked writing to stdin.

    Decoded `utf-8`/`replace` to match what the per-blob `git show` produced. Line endings are
    *not* newline-translated here and were before; it cannot matter, because `tokenize()`
    splits on every non-alphanumeric character, so `\\r` is a separator either way.
    """
    if not shas:
        return {}
    _batch_check_sizes(root, shas)  # sizes/short-read checked from headers, before any content
    proc = subprocess.Popen(["git", "-C", str(root), "cat-file", "--batch"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None

    def _feed() -> None:
        try:
            proc.stdin.write(("\n".join(shas) + "\n").encode("ascii"))
            proc.stdin.close()
        except BrokenPipeError:
            pass

    writer = threading.Thread(target=_feed)
    writer.start()
    contents: dict[str, str] = {}
    stdout = proc.stdout
    stderr = b""
    try:
        for sha in shas:
            header = stdout.readline().decode("ascii", "replace").split()
            if len(header) < 3 or header[1] != "blob" or header[0] != sha:
                break  # framing drift or a missing record — the `missing` assertion names it
            size = int(header[2])
            raw = bytearray()
            while len(raw) < size:
                chunk = stdout.read(min(_READ_CHUNK, size - len(raw)))
                if not chunk:
                    break  # truncated stream — same, caught below
                raw.extend(chunk)
            stdout.read(1)  # the record's trailing newline
            contents[sha] = bytes(raw).decode("utf-8", "replace")
            del raw
    finally:
        writer.join()
        stdout.close()
        stderr = proc.stderr.read()
        proc.wait()

    missing = [s for s in shas if s not in contents]
    assert not missing, (
        f"streaming `git cat-file --batch` returned {len(contents)} of {len(shas)} blobs; "
        f"{len(missing)} were not read, first {missing[:3]}.\n"
        f"exit {proc.returncode}, stderr: {stderr.decode('utf-8', 'replace')[:400]}\n\n"
        "This is COULD-NOT-LOOK, not a clean result. An unread blob would scan as the empty "
        "string and pass, so the history guard would report success over content it never saw."
    )
    return contents


def _path_shape(evidence: str) -> str:
    """A path finding's SHAPE, never its value.

    This guard's findings are reported into a public CI log, and its subject is content that
    should not have been published once. Echoing the coordinate to complain about it publishes
    it a second time, in a place that is indexed. The shape plus the blob sha is what a fix
    actually needs — the remedy for a history finding is never "edit that line", it is a
    rewrite pass addressed by content — so nothing is lost by withholding the value.
    """
    if evidence.startswith("\\\\"):
        return "unc"
    prefix = "/home|Users/"
    if evidence.startswith(prefix):
        return f"home/<{len(evidence) - len(prefix)}c user>"
    return f"drive({evidence[0]})"


class HistoryScan(NamedTuple):
    """One walk's worth of results. A named tuple because there are now four of them and
    `result[2]` at a call site is how the wrong quantity gets asserted."""

    tokens: dict[str, set[str]]          # rel -> banned phrases, quoted from the blob
    paths: dict[tuple[str, str], set[str]]  # (blob sha, rel) -> shapes, never values
    pairs: int
    unused_pins: set[str]


def offenders_in_history(root: Path, banned: set[str], *,
                         pinned: dict[str, str] | None = None) -> HistoryScan:
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
    found, which is the quantity the guard's coverage claim is made of. **Both matchers run
    inside that one pass, against that one counter** — a second breadth quantity would be a
    second thing to keep honest, and this one already covers every blob either matcher sees.

    **Machine paths are scanned here too, and the dispatch is the whole of the difficulty.**
    Until 2026-09-12 this walk filtered its findings to `kind == "token"`, so it looked for
    coordinate phrases only and a machine path committed once and removed later was caught by
    nothing at all: this walk discarded it, the tree guard sees only what is tracked *now*, and
    the CI secret scanner runs its default credential ruleset over credentials. But the naive
    repair — keeping the `path` findings of the same raw-text scan — reports **35 blobs** on
    this history and is wrong about 31 of them, because `_scan` reads a Python file through its
    AST: a drive letter written into a test fixture's source is not a drive letter in any value
    the program holds, which is exactly why those files pass the tree guard today. So a `.py`
    blob is handed to `scan_python` and everything else reuses the raw-text findings already
    computed for the token pass. Four blobs, not thirty-five — and a gate that reports 31
    findings nobody can act on is a gate somebody switches off.

    `scan_text` is still what the TOKEN pass uses for every blob including Python, unchanged:
    raw text is the wider net there, and narrowing it to the AST would quietly weaken the check
    this file was written for.
    """
    pinned = pinned or {}
    by_sha, pairs, walked = history_blobs(root)
    missing_commits = sorted(reachable_commits(root) - walked)
    assert not missing_commits, (
        "the history walk did not reach commit(s) that a ref still points at:\n  "
        + "\n  ".join(missing_commits)
        + "\n\nA COUNT of commits or (commit, path) pairs cannot see this: an ordinary commit "
          "contributes many pairs, so a walk that dropped one — the root, a merge, or one in "
          "the MIDDLE of history — still clears every breadth floor in this file. This checks "
          "the commits the walk actually visited against the commits every ref reaches, by "
          "identity, and names the ones it never looked at.\n\n"
          "COULD-NOT-LOOK: if the walk was deliberately narrowed (a since-tag or a depth "
          "bound, or `git rev-list HEAD` in place of `--all`, which drops every unmerged "
          "branch), that is the hazard this check exists to make loud, not the clean result it "
          "resembles."
    )
    offenders: dict[str, set[str]] = {}
    path_offenders: dict[tuple[str, str], set[str]] = {}
    fired: set[str] = set()
    scanned = 0
    for sha, content in read_blobs(root, list(by_sha)).items():
        scanned += 1
        text_findings = _scan.scan_text(content, banned)
        hits = [f.evidence for f in text_findings if f.kind == "token"]
        if hits:
            for rel in by_sha[sha]:
                offenders.setdefault(rel, set()).update(hits)

        python_rels = sorted(r for r in by_sha[sha]
                             if Path(r).suffix == ".py" and r not in PATH_EXEMPT)
        other_rels = sorted(r for r in by_sha[sha]
                            if Path(r).suffix != ".py" and r not in PATH_EXEMPT)
        for rels, findings in ((python_rels, None), (other_rels, text_findings)):
            if not rels:
                continue  # no path to attribute a hit to, so nothing to parse either
            if findings is None:
                # `set()`, not `banned`: only this pass's `path` findings are read, and the
                # token pass above already scanned this blob's RAW text, which is the wider
                # net. Handing the ban set in as well would hash every 1-, 2- and 3-gram of
                # every AST literal a second time to produce findings that are then dropped.
                # MEASURED 2026-09-12 on Windows: 87.3 s with `banned` here, 68.5 s with
                # `set()`, against 44.8 s for this file before the path pass existed.
                findings = _scan.scan_python(content, set())
            shapes = sorted({_path_shape(f.evidence) for f in findings if f.kind == "path"})
            if not shapes:
                continue
            if sha in pinned:
                fired.add(sha)
                continue
            for rel in rels:
                path_offenders.setdefault((sha, rel), set()).update(shapes)
    assert scanned == len(by_sha), (
        f"{scanned} of the {len(by_sha)} blobs this walk found were scanned; "
        f"{len(by_sha) - scanned} were never looked at.\n\n"
        "COULD-NOT-LOOK, and `read_blobs`' own short-read check cannot see it: that one asks "
        "whether the reader returned what it was asked for, not whether it was asked for "
        "everything. Counted here against the walk instead, so any narrowing of the read — a "
        "capped batch, a chunked read that loses a chunk — fails rather than scanning fewer "
        "blobs and reporting a clean history."
    )
    return HistoryScan(offenders, path_offenders, pairs, set(pinned) - fired)


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


@pytest.fixture(scope="module")
def history_scan() -> HistoryScan:
    """The whole-history walk, run ONCE for every assertion that needs it.

    Module-scoped on purpose: the walk is the expensive half of the most expensive guard in
    the suite, and two tests taking two walks would double it while covering nothing extra.
    Both matchers already run inside the single pass over each blob.
    """
    return offenders_in_history(ROOT, _scan.load_banned(BANLIST), pinned=PATH_PINNED)


def test_no_banned_coordinate_survives_anywhere_in_history(history_scan: HistoryScan):
    """Every blob, every commit. Zero findings, or the redaction is not finished.

    The failure message names the file and the offending phrase but no commit, because the
    fix is never "amend that commit" — it is another rewrite pass over everything.
    """
    pairs = history_scan.pairs

    assert pairs > len(all_commits()), (
        f"the walk examined only {pairs} (commit, path) pairs across {len(all_commits())} "
        "commits, which is too few to be a walk of this history — `ls-tree -r` output is "
        "probably no longer being parsed, and an unparsed tree scans as zero blobs and passes."
    )
    offenders = history_scan.tokens
    assert not offenders, (
        "banned coordinates survive in git history:\n  "
        + "\n  ".join(f"{rel}: {sorted(ev)}" for rel, ev in sorted(offenders.items()))
        + "\n\nDeleting the file at HEAD does not help — the content stays reachable. This "
          "needs another `git filter-repo --replace-text` pass over all history, followed by "
          "a force-push AND a fresh clone of the remote to confirm what others can see."
    )


def test_no_machine_path_survives_anywhere_in_history(history_scan: HistoryScan):
    """The half of this guard that did not exist until 2026-09-12.

    A machine path committed once is published the moment the commit is, and removing the file
    later changes nothing about what a clone can read. Nothing checked for one across history:
    this walk kept only `token` findings, `test_no_private_coordinates.py` reads today's
    tracked tree, and the CI secret scanner runs a credential ruleset. Two of the four blobs
    this found sit at a path that is not tracked at HEAD at all, so no widening of the tree
    guard could ever have reached them.

    Findings are reported by blob sha and SHAPE. The sha is what the remedy is addressed to;
    the value is withheld because this message lands in a public log (see `_path_shape`).

    **The offender is asserted BEFORE the stale pin, and the order is a decision rather than
    an accident.** Only one of two asserts can report, because the first to fail ends the
    test. If a newly committed machine path and a dead pin arrive together, the offender is
    the one a person must read first: it is a live exposure with a clock on it, while a pin
    that stopped matching is bookkeeping about a finding that no longer exists. The reverse
    order was measured to hide exactly that — swapping one pinned sha for another valid blob
    sha reported the dead pin, and the offender the swap had uncovered appeared only in
    pytest's own repr of the namedtuple, one line further down.
    """
    assert not history_scan.paths, (
        "machine paths survive in git history:\n  "
        + "\n  ".join(f"{sha} as {rel}: {sorted(shapes)}"
                      for (sha, rel), shapes in sorted(history_scan.paths.items()))
        + "\n\nThis is a NEW finding and a pin is not its remedy. Deleting the file at HEAD "
          "does not help either — the content stays reachable to every clone. If it has not "
          "been pushed, fix it before it is. If it has, the choice is the owner's between "
          "another `git filter-repo` pass over all history (which gives every later commit a "
          "new sha) and a reviewed entry in PATH_PINNED naming the sha and the reason."
    )
    assert not history_scan.unused_pins, (
        "these pins matched nothing in this history:\n  "
        + "\n  ".join(f"{sha}: {PATH_PINNED[sha]}" for sha in sorted(history_scan.unused_pins))
        + "\n\nA pin that cannot fire is an exemption nobody can watch, which is the shape "
          "this repository has paid for repeatedly. Either the blob is gone — in which case "
          "delete the entry — or the matcher stopped finding what it was written for, which "
          "is a bigger problem than the pin."
    )


# ── the pin is pinned, not merely documented ─────────────────────────────────
#: The pin as the owner ruled it on 2026-09-12. Held as MEMBERSHIP rather than as a count,
#: which is the shape `test_no_private_coordinates.py` already holds `PATH_EXEMPT` to — and
#: the stronger of the two for a reason that was measured rather than argued: substituting one
#: pinned sha for another VALID blob sha of this history leaves the count at four, so a count
#: assertion cannot see it at all. Membership sees it, and so does a pin added or deleted.
PINNED_AT_LANDING = frozenset({
    "330669ed6844cd719239946c66fe4f5f5c894a70",
    "7de6fbc8be1dbcf0703e661a55c13ba7dc5b27eb",
    "28d20ffb195b624866ab29b806739d58ce511284",
    "ecc5289ccf5d2aadd5fc2bdd243c24b3eac1b7d1",
})


def test_the_pin_is_exactly_the_blobs_the_owner_ruled_on():
    """`PATH_PINNED` asserts its own LIVENESS; this asserts its GROWTH.

    `test_no_machine_path_survives_anywhere_in_history` fails on a pin that fired nothing, so
    a pin cannot outlive its finding. Nothing there bounds the list going the other way: a
    fifth entry covering a real new offender fires, counts as used, and the gate goes green —
    on a diff review alone. That is the right standard for a human reading a diff and the
    wrong one for a gate, which is the difference this file exists to make.

    Deliberately a second copy of the four shas. Widening the pin must therefore be TWO edited
    literals and a sentence saying why, which is the same price the sibling guard charges for
    widening `PATH_EXEMPT`, and it is the point rather than duplication to be factored away.
    """
    added = sorted(set(PATH_PINNED) - PINNED_AT_LANDING)
    removed = sorted(PINNED_AT_LANDING - set(PATH_PINNED))
    assert not added, (
        "PATH_PINNED has grown:\n  " + "\n  ".join(added)
        + "\n\nRead this as a FINDING, not as bookkeeping. A new pin means a machine path was "
          "committed to this repository after this guard landed, and that the gate which "
          "caught it was then silenced rather than the path removed before it was pushed. "
          "Deleting the entry is not the fix either — it only restores the red. The fix is "
          "the owner's ruling on that blob, recorded where the pin is, and this literal "
          "updated in the same commit so the next reader sees the list was widened on purpose."
    )
    assert not removed, (
        "PATH_PINNED has shrunk:\n  " + "\n  ".join(removed)
        + "\n\nIf history was rewritten and these blobs are genuinely gone, update this "
          "literal in the same commit and say so. If not, the guard is about to go red on the "
          "blob this entry covered — which is the correct outcome, and this assertion is only "
          "telling you it will happen before the expensive walk does."
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

    offenders, _paths, pairs, _unused = offenders_in_history(repo, banned)
    assert "leak.md" in offenders, (
        f"the history walk missed a deleted blob: found {offenders!r} over {pairs} pairs"
    )
    assert DECOY in offenders["leak.md"]


def _repo_with_middle_decoy(tmp_path: Path) -> tuple[Path, str]:
    """Three commits; the decoy lives ONLY in the middle commit's tree — added in C2, removed
    in C3 — so a walk that skips C2 never sees the blob at all. Returns the repo and C2's sha.
    """
    repo = tmp_path / "middle"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], capture_output=True, check=True)
    # Several files per commit, so the (commit, path) pair count stays comfortably above the
    # commit count even after a commit is dropped — the whole point is that the pairs floor
    # keeps passing while a commit has silently vanished.
    for i in range(5):
        (repo / f"keep{i}.md").write_text(f"nothing to see {i}\n", encoding="utf-8")
    _commit_all(repo, "c1 first")
    (repo / "leak.md").write_text(f"the owner is {DECOY} and it should not be here\n",
                                 encoding="utf-8")
    _commit_all(repo, "c2 the accident, in the middle of history")
    (repo / "leak.md").unlink()
    _commit_all(repo, "c3 removed the file, which is not the same as unreachable")
    commits = all_commits(repo)  # rev-list --all is newest-first: [c3, c2, c1]
    assert len(commits) == 3, f"the fixture must have exactly three commits: {commits!r}"
    return repo, commits[1]


def test_a_dropped_middle_commit_is_found_by_name_not_missed_by_a_count(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """T331: a walk that drops a MIDDLE commit is invisible to every count in this file.

    The blocked attempt checked only that the walk reached the ROOT commit. A dropped middle
    commit still reaches the root, so `roots - walked` stayed empty and the planted offender
    was never found. Proven here the way this file proves its other coverage hazards: shrink
    what the walk visits by exactly the middle commit and require the guard to NAME it.

    The first half of the test demonstrates the hole is real — with the middle commit gone the
    content scan itself finds nothing, so only a completeness check could ever catch it — and
    the second half requires the fixed guard to fire and name the dropped commit.
    """
    repo, middle = _repo_with_middle_decoy(tmp_path)
    banned = {_scan.digest(DECOY)}

    # A complete walk finds the decoy that lives only in the middle commit.
    assert "leak.md" in offenders_in_history(repo, banned).tokens, (
        "the fixture's middle-commit decoy is not findable by a complete walk"
    )

    # Drop exactly the middle commit from what the walk visits.
    real = all_commits
    monkeypatch.setitem(globals(), "all_commits",
                        lambda root=ROOT: [c for c in real(root) if c != middle])

    by_sha, pairs, walked = history_blobs(repo)
    assert pairs > len(walked), (
        "the pairs floor still passes with the middle commit gone — which is exactly why a "
        "count cannot defend completeness"
    )
    # The content scan is blind to the drop: the decoy's only commit was skipped, so nothing
    # is found. A guard WITHOUT the completeness check would return this empty result as clean.
    scanned: dict[str, set[str]] = {}
    for sha, content in read_blobs(repo, list(by_sha)).items():
        hits = [f.evidence for f in _scan.scan_text(content, banned) if f.kind == "token"]
        if hits:
            for rel in by_sha[sha]:
                scanned.setdefault(rel, set()).update(hits)
    assert not scanned, (
        "with the middle commit dropped the decoy is unreachable to the content scan, so only "
        "a completeness check can catch the gap — this is the guard staying green on the "
        "planted defect"
    )

    with pytest.raises(AssertionError) as caught:
        offenders_in_history(repo, banned)
    message = str(caught.value)
    assert middle in message, message
    assert "MIDDLE" in message
    assert "COULD-NOT-LOOK" in message


def _repo_with_unmerged_branch(tmp_path: Path) -> tuple[Path, str]:
    """A repo whose decoy lives on an UNMERGED branch: `git rev-list HEAD` never reaches it,
    `git rev-list --all` and `for-each-ref` do. Returns the repo and the branch tip's sha.
    """
    repo = tmp_path / "unmerged"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], capture_output=True, check=True)
    (repo / "keep.md").write_text("nothing to see\n", encoding="utf-8")
    _commit_all(repo, "c1 on the default branch")
    default = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "feature"],
                   capture_output=True, check=True)
    (repo / "leak.md").write_text(f"the owner is {DECOY} and it should not be here\n",
                                 encoding="utf-8")
    _commit_all(repo, "c2 on the unmerged feature branch")
    tip = _git(repo, "rev-parse", "feature").strip()
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", default],
                   capture_output=True, check=True)
    return repo, tip


def test_a_head_only_walk_misses_unmerged_branches(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """T331: `git rev-list HEAD` in place of `--all` reaches the root but drops every unmerged
    branch. The completeness witness comes from `for-each-ref` (all refs), not from HEAD, so it
    names the branch tip the HEAD-only walk would silently skip.
    """
    repo, feature_tip = _repo_with_unmerged_branch(tmp_path)
    banned = {_scan.digest(DECOY)}

    # `--all` reaches the feature branch; the decoy is found.
    assert "leak.md" in offenders_in_history(repo, banned).tokens, (
        "the fixture's unmerged-branch decoy is not found by a --all walk"
    )

    # Substitute the exact narrowing the review flagged.
    monkeypatch.setitem(globals(), "all_commits",
                        lambda root=ROOT: _git(root, "rev-list", "HEAD").split())

    by_sha = history_blobs(repo)[0]
    scanned = [sha for sha, content in read_blobs(repo, list(by_sha)).items()
               if [f for f in _scan.scan_text(content, banned) if f.kind == "token"]]
    assert not scanned, (
        "the HEAD-only walk must not reach the unmerged decoy, or the fixture is not testing "
        "what it claims"
    )

    with pytest.raises(AssertionError) as caught:
        offenders_in_history(repo, banned)
    message = str(caught.value)
    assert feature_tip in message, message
    assert "unmerged" in message
    assert "COULD-NOT-LOOK" in message


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

    offenders, _paths, pairs, _unused = offenders_in_history(repo, banned)
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


def _repo_with_big_blobs(tmp_path: Path, n: int = 8,
                         size: int = 1 << 20) -> tuple[Path, int]:
    """A one-commit repo of `n` distinct ASCII blobs of ~`size` bytes each, and the total
    content size measured straight from `git cat-file --batch-check` (headers, no content).

    Deliberately several megabytes: 'the whole batch is resident' and 'one blob is resident'
    are only far enough apart in `tracemalloc` to assert on when the batch is large, the way
    the real history's 68 MB is. ASCII so the decoded `str` is one byte per character and the
    ratios below mean what they say.
    """
    repo = tmp_path / "big"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], capture_output=True, check=True)
    for i in range(n):
        (repo / f"f{i}.txt").write_text((chr(65 + i) * 64 + "\n") * (size // 65),
                                        encoding="utf-8")
    _commit_all(repo, "big blobs")
    shas = list(history_blobs(repo)[0])
    bc = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch-check", "--buffer"],
                        input=("\n".join(shas) + "\n").encode("ascii"),
                        stdout=subprocess.PIPE)
    total = sum(int(l.split()[2]) for l in bc.stdout.decode("ascii", "replace").splitlines()
                if len(l.split()) >= 3 and l.split()[1] == "blob")
    return repo, total


def test_the_read_is_bounded_not_the_decode(tmp_path: Path):
    """T322: the guard bounded the decode, not the read.

    The previous `read_blobs` buffered every unique blob's content into one `proc.stdout` bytes
    object and only then walked it, so the whole of history was resident before anything looked
    at it — and any size assertion placed after that (as the blocked attempt's fixed ceiling
    was) could bound only the decode that copied it, a factor of two rather than a bound. A
    mutant that moved such an assert further down, to after the decode loop, would change
    nothing it could catch, because the allocation it was meant to prevent already happened.

    Proven by memory rather than by argument: `tracemalloc` peak against the content size.
    MEASURED 2026-09-19 — buffered read 2.13x the content, streamed read 1.13x — so the 1.5x
    line below is red for the old buffered read and green for the streamed one. The check that
    makes the size knowable before allocation (`--batch-check`, headers only) is asserted too:
    the header stream is a small fraction of the content it describes.
    """
    repo, total = _repo_with_big_blobs(tmp_path)
    shas = list(history_blobs(repo)[0])
    assert total > 4 << 20, "the fixture must be several MB for the ratio to clear noise"

    # remedy (a): the size of the read is knowable from headers, transferring almost nothing.
    bc = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch-check", "--buffer"],
                        input=("\n".join(shas) + "\n").encode("ascii"),
                        stdout=subprocess.PIPE)
    assert 0 < len(bc.stdout) < total // 100, (
        f"batch-check transferred {len(bc.stdout)} bytes to describe {total} bytes of content; "
        "it must report sizes from headers alone, not by moving the content"
    )

    tracemalloc.start()
    try:
        contents = read_blobs(repo, shas)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(contents) == len(shas), "the read lost blobs"
    assert peak < total * 1.5, (
        f"read_blobs peaked at {peak} bytes for {total} bytes of content ({peak / total:.2f}x). "
        "Above ~2x the whole batch is resident as raw bytes AND as decoded text at once, so the "
        "read was never bounded — only the decode that copied an allocation already made. Stream "
        "the content one blob at a time and the raw whole-history copy goes away."
    )


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


#: Built from code points so this file contains no drive letter of its own — the guard next
#: door forbids one in any tracked file, and a fixture that has to be exempted to test the
#: exemption machinery is not a fixture, it is the defect.
_DRIVE_SAMPLE = chr(100) + ":" + chr(92)


def _repo_with_a_removed_machine_path(tmp_path: Path) -> tuple[Path, str]:
    """History where a machine path is committed in a `.md` and removed, plus a `.py` whose
    RAW TEXT looks like one and whose parsed value does not. Returns the repo and the
    offending blob's sha."""
    repo = tmp_path / "paths"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], capture_output=True, check=True)
    (repo / "notes.md").write_text(f"built under {_DRIVE_SAMPLE}work\n", encoding="utf-8")
    # A colon-then-escape in source is a real newline in the value: raw text sees a drive
    # letter there and the AST, correctly, sees none.
    (repo / "decoy.py").write_text(f'print("{_DRIVE_SAMPLE}n")\n', encoding="utf-8")
    _commit_all(repo, "the accident")
    (repo / "notes.md").unlink()
    _commit_all(repo, "removed the file, which is not the same as unreachable")
    sha = _git(repo, "rev-list", "--all", "--objects").split()
    blob = _git(repo, "rev-parse", f"{all_commits(repo)[-1]}:notes.md").strip()
    assert blob and blob in sha, "the fixture could not name its own offending blob"
    return repo, blob


def test_a_machine_path_removed_from_head_is_found_and_only_a_pin_silences_it(tmp_path: Path):
    """Three claims in one fixture, because they only mean anything together.

    The walk reaches a machine path that is not at HEAD — the whole gap this closes. A pin on
    that blob's sha silences exactly it. And **blinding the pin turns the guard red again**,
    which is the property an allow-list is worthless without: an exemption nobody has ever
    watched fail is indistinguishable from a matcher that stopped matching.
    """
    repo, blob = _repo_with_a_removed_machine_path(tmp_path)

    assert not _git(repo, "grep", "-l", _DRIVE_SAMPLE, "HEAD").splitlines(), (
        "the path is still at HEAD, so this control would pass for a guard that only ever "
        "reads the working tree — the fixture is not testing what it claims."
    )

    blind = offenders_in_history(repo, set()).paths
    assert (blob, "notes.md") in blind, f"the walk missed a removed machine path: {blind!r}"
    assert blind[(blob, "notes.md")] == {"drive(d)"}, blind

    pinned = offenders_in_history(repo, set(), pinned={blob: "shape drive, fixture"})
    assert pinned.paths == {}, f"the pin did not silence its own blob: {pinned.paths!r}"
    assert pinned.unused_pins == set(), "the pin fired, so it must not be reported unused"

    stale = offenders_in_history(repo, set(), pinned={"0" * 40: "names no blob here"})
    assert stale.unused_pins == {"0" * 40}, (
        "a pin that matched nothing was not reported — an exemption that can never be seen to "
        "be dead is how the list outlives the findings it was written for"
    )
    assert stale.paths, "and the real finding must still be reported alongside it"


def test_a_drive_letter_in_python_source_is_judged_by_the_ast_not_the_raw_text(tmp_path: Path):
    """The dispatch, as a behaviour rather than as a comment.

    `decoy.py` contains the three characters of a drive letter in its SOURCE and holds none in
    any value it evaluates to. Raw-text scanning reports it; the AST does not, and the AST is
    right — which is why this repository's own synthetic path fixtures pass the tree guard
    today. Asserted both ways round, because the claim is a DIFFERENCE between two scanners
    and half of it would pass for a matcher that simply found nothing.
    """
    repo, _blob = _repo_with_a_removed_machine_path(tmp_path)
    source = (repo / "decoy.py").read_text(encoding="utf-8")

    assert [f for f in _scan.scan_text(source, set()) if f.kind == "path"], (
        "the decoy does not look like a machine path to the raw-text scanner, so this test "
        "proves nothing about the dispatch"
    )
    assert [f for f in _scan.scan_python(source, set()) if f.kind == "path"] == []

    found = offenders_in_history(repo, set()).paths
    assert not [rel for (_sha, rel) in found if rel == "decoy.py"], (
        f"the history walk scanned a .py blob as raw text: {found!r}"
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
    hits = _git(ROOT, "grep", "-l", word, "HEAD").splitlines()
    assert hits, (
        f"{word!r} has vanished from the tree — a redaction pass probably ate it as a "
        f"substring of a banned coordinate. Check the lookarounds in the replace-text rules."
    )
