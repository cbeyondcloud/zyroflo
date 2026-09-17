"""The ban-list maintainer, which had no test of any kind.

`ban.py` seeds the leak guard: the digests it writes are what
`test_no_private_coordinates.py` matches every tracked file against. It is the one
mechanism between this repository and publishing the coordinates of somebody else's
estate, and until this file existed nobody could watch it fail.

Three things are pinned here, and each is a defect this file was written against:

* **A malformed TOML is refused by name.** The two `--from-*` readers handed
  `tomllib.TOMLDecodeError` straight to the operator as a stack trace, at exit 1 —
  a code `main()` does not otherwise produce, so "your file has a typo" and "this
  tool is broken" arrived looking identical. `policy.py --validate` had exactly this
  shape (item 117) in the one module whose whole job was refusing a document it
  could not read.
* **A ban list it cannot read stops it, and it writes nothing.** ABSENT, EMPTY and
  COULD-NOT-LOOK are three different answers. If the corrupt case were ever folded
  into the absent one the next `--add` would rewrite the file from an empty set and
  silently disarm the guard, so the refusal is tested together with the bytes.
* **The console rule, through the one helper.** `cli.use_utf8()` — see
  `test_cli.py` — not a fourteenth verbatim copy of the block.

Phrases used as ban candidates are BUILT FROM CODE POINTS at runtime. A phrase
spelled literally in this file would be a phrase in the harness's own tracked files
the moment this file is committed, and `vet()` refuses exactly those: the test would
pass while uncommitted and fail once landed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.tests import ban as B          # noqa: E402
from tools.tests import _scan             # noqa: E402

#: The five files a runnable copy of the CLI needs. `cli.py` is stdlib-only by its
#: own docstring, so the sandbox stays a copy of the real thing rather than a stub.
_SANDBOX_FILES = ("tools/__init__.py", "tools/cli.py", "tools/tests/__init__.py",
                  "tools/tests/_scan.py", "tools/tests/ban.py")


def _coined(*points: int) -> str:
    """A token assembled at runtime, so it exists in no file on disk."""
    return "".join(chr(p) for p in points)


#: Two coinages, neither of which appears as text anywhere in the tree.
CANDIDATE = _coined(0x71, 0x76, 0x6F, 0x72, 0x6C, 0x61, 0x78)
OTHER = _coined(0x7A, 0x75, 0x62, 0x72, 0x69, 0x6B)


def _git(root: Path, *args: str) -> None:
    """Identity and signing are passed per command, so the sandbox does not depend on
    whatever `git config --global` happens to say on the machine running the suite."""
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)}: exit {p.returncode}\n{p.stderr}"


def _sandbox(tmp_path: Path) -> Path:
    """A copy of the CLI, in a real git repository, whose ban list is the test's.

    `BANLIST` and `ROOT` are both derived from `__file__`, so pointing the tool at a
    different list means running it from a different tree. Copying is what lets the
    corrupt-list and absent-list cases be spawned at all.

    It is a REPOSITORY because `main` reaches its writer only through
    `tree_phrases()` → `tracked_files()` → `git ls-files` against `ROOT`, with
    `check=True`. In a plain directory that call raised and every `--add` died one
    frame short of `write_list`, so both assertions about what an `--add` leaves on
    disk were true whatever the writer did: a `write_list` that wrote nothing, and a
    `main` that never called it, each survived a mutation pass of this file. A
    fixture that cannot reach the code its test names is not testing it.
    """
    root = tmp_path / "harness"
    (root / "tools" / "tests").mkdir(parents=True)
    for rel in _SANDBOX_FILES:
        (root / rel).write_bytes((ROOT / rel).read_bytes())
    _git(root, "init")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=sdlc harness tests", "-c", "user.email=tests@invalid",
         "-c", "commit.gpgsign=false", "commit", "-m", "sandbox")
    return root


def _sandbox_uncommitted(tmp_path: Path) -> Path:
    """Like `_sandbox`, but git is initialised and NOTHING is added or committed,
    so `git ls-files` answers exit 0 with no output — the exact shape that made
    `tree_phrases()` read a tree it could not see as an empty one."""
    root = tmp_path / "harness"
    (root / "tools" / "tests").mkdir(parents=True)
    for rel in _SANDBOX_FILES:
        (root / rel).write_bytes((ROOT / rel).read_bytes())
    _git(root, "init")
    return root


def _sandbox_no_git(tmp_path: Path) -> Path:
    """Like `_sandbox`, but there is no repository at all: a malformed `.git`
    file stops git's upward search and makes `git ls-files` FAIL (exit 128)
    deterministically, wherever the sandbox happens to live. That is the case
    `check=True` turned into an uncaught `CalledProcessError` at exit 1."""
    root = tmp_path / "harness"
    (root / "tools" / "tests").mkdir(parents=True)
    for rel in _SANDBOX_FILES:
        (root / rel).write_bytes((ROOT / rel).read_bytes())
    (root / ".git").write_text("not a valid gitfile\n", encoding="utf-8")
    return root


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    """Spawn it. A CLI's contract is a process: in-process, a refusal and a
    traceback are the same failed call."""
    return subprocess.run([sys.executable, str(root / "tools" / "tests" / "ban.py"),
                           *args], capture_output=True, text=True)


def test_the_sandbox_runs_the_module_itself_and_not_a_stand_in():
    """Green by design, and the other spawn tests are worthless without it: a
    sandbox that had drifted from the real source would be testing a fossil."""
    for rel in _SANDBOX_FILES:
        assert (ROOT / rel).is_file(), rel
    assert b"def main(" in (ROOT / "tools/tests/ban.py").read_bytes()


# ── a document it cannot read ────────────────────────────────────────────────

@pytest.mark.parametrize("flag", ["--from-workspace", "--from-corpus"])
def test_a_malformed_toml_is_refused_by_name_not_by_traceback(tmp_path, flag):
    """Measured before the fix: exit 1 and a `tomllib.TOMLDecodeError` traceback
    whose frames name the operator's own file. Exit 1 is not in this CLI's
    vocabulary — `main()` returns 0 or 2 — so the operator got a third code and no
    sentence telling them which file was wrong."""
    root = _sandbox(tmp_path)
    bad = tmp_path / "not-really.toml"
    bad.write_text('name = "acme\nroot = [unclosed\n', encoding="utf-8")

    p = _run(root, flag, str(bad))
    out = p.stdout + p.stderr

    assert p.returncode == 2, f"exit {p.returncode}; output: {out}"
    assert "Traceback" not in p.stderr, p.stderr
    assert bad.name in out, out
    assert flag in out, out


def test_a_toml_that_is_not_utf8_is_refused_the_same_way(tmp_path):
    """The other way a file resists being read. `read_text(encoding="utf-8")` sits
    in front of `tomllib` in both readers, so a cp1252 file raised
    UnicodeDecodeError one frame earlier and never reached the TOML branch."""
    root = _sandbox(tmp_path)
    bad = tmp_path / "latin.toml"
    bad.write_bytes(b'name = "' + bytes([0xFF, 0xFE]) + b'"\n')

    p = _run(root, "--from-workspace", str(bad))
    assert p.returncode == 2, p.stdout + p.stderr
    assert "Traceback" not in p.stderr, p.stderr
    assert bad.name in p.stdout + p.stderr


# ── absent vs empty vs could-not-look ────────────────────────────────────────

def test_a_ban_list_it_cannot_read_stops_it_and_writes_nothing(tmp_path):
    """The disarm case. `load_banned` raises on a line that is not a digest; that
    reached the operator as a traceback. The refusal is the first assertion; the
    second is the one the refusal exists for — `--add` rewrites the file from
    whatever `read_list()` returned, so a corrupt list that read as "empty" would be
    silently replaced by a one-entry one and the guard would stop matching everything
    it used to.

    That second assertion says something only because `_sandbox()` is a repository.
    While it was a plain directory the `--add` died in `tracked_files()` before
    reaching the writer, so the bytes were unchanged for a reason that had nothing to
    do with the refusal, and this docstring claimed a coverage the fixture could not
    deliver."""
    root = _sandbox(tmp_path)
    listing = root / "tools" / "tests" / "banned_tokens.txt"
    listing.write_text("# a list\nthis-is-not-a-digest\n", encoding="utf-8")
    before = listing.read_bytes()

    p = _run(root, "--add", CANDIDATE)

    assert p.returncode == 2, f"exit {p.returncode}; output: {p.stdout + p.stderr}"
    assert "Traceback" not in p.stderr, p.stderr
    assert listing.name in p.stdout + p.stderr
    assert listing.read_bytes() == before, "it rewrote a list it could not read"


def test_an_add_persists_the_digest_and_the_guard_then_matches_it(tmp_path):
    """The writer's positive control, and the only test that composes the real writer
    with the real reader. Everything above asserts what an `--add` must NOT write; a
    guard whose seeder silently wrote nothing would satisfy all of it — the run still
    prints `1 added` and exits 0, and the operator learns the term was never guarded
    from the leak that was supposed to catch it. Two mutants proved that gap real: a
    `write_list` with its one statement removed, and a `main` that never calls it,
    both survived this file's mutation pass.

    The last assertion is the weld. `--check` is the consumer of exactly the bytes
    `--add` produced, so a digest spelling that drifted between the two would fail
    here and nowhere else; asserting the file merely EXISTS would not catch it.
    """
    root = _sandbox(tmp_path)
    listing = root / "tools" / "tests" / "banned_tokens.txt"
    assert not listing.exists()
    # Absent is could-not-look now (see the --check absent-vs-empty test above):
    # with no list to consult, `--check` refuses at exit 2 rather than answering
    # the clean "not banned" this line used to assert. The term is genuinely
    # unguarded until `--add` writes the list, which the final assertion proves.
    before = _run(root, "--check", CANDIDATE)
    assert before.returncode == 2, before.stdout + before.stderr
    assert before.stdout.strip() != "banned", before.stdout

    p = _run(root, "--add", CANDIDATE)
    assert p.returncode == 0, f"exit {p.returncode}; output: {p.stdout + p.stderr}"
    assert "1 added" in p.stdout, p.stdout

    assert listing.exists(), "it reported an addition and wrote no list"
    written = listing.read_text(encoding="utf-8")
    assert _scan.digest(CANDIDATE) in written, written
    assert CANDIDATE not in written, "plaintext in a list whose whole point is digests"
    assert _run(root, "--check", CANDIDATE).stdout.strip() == "banned"


def test_an_absent_list_does_not_report_itself_as_zero_banned(tmp_path):
    """ABSENT and MEASURED-ZERO are different facts and `--list` printed the same
    sentence for both. "0 digest(s)" against a file that was never created reads as
    "the guard is armed and nothing is banned"; the truth is that nobody has seeded
    it yet, which is the one state where the guard protects nothing."""
    root = _sandbox(tmp_path)
    listing = root / "tools" / "tests" / "banned_tokens.txt"
    assert not listing.exists()

    absent = _run(root, "--list")
    assert absent.returncode == 0, absent.stdout + absent.stderr
    assert "0 digest(s)" not in absent.stdout, absent.stdout

    listing.write_text("# seeded, then emptied\n", encoding="utf-8")
    empty = _run(root, "--list")
    assert empty.returncode == 0, empty.stdout + empty.stderr
    assert "0 digest(s)" in empty.stdout, empty.stdout
    assert absent.stdout != empty.stdout


def test_check_on_an_absent_list_is_could_not_look_not_not_banned(tmp_path):
    """The same distinction `--list` was taught (above), on the branch that
    still conflated the two. `--check` printed "not banned" for a list that was
    never created AND for one that is armed and genuinely bans nothing — so a
    caller could not tell "nobody seeded the guard" from "the guard cleared this
    term", and the first is the one state where the guard protects nothing.

    Absent is could-not-look: a refusal on stderr and exit 2, the code this CLI
    already returns for an input it could not read, never the clean "not banned"
    on stdout that a caller acts on. Empty is a genuine "not banned" at exit 0."""
    root = _sandbox(tmp_path)
    listing = root / "tools" / "tests" / "banned_tokens.txt"
    assert not listing.exists()

    absent = _run(root, "--check", CANDIDATE)
    assert absent.returncode == 2, f"exit {absent.returncode}; {absent.stdout + absent.stderr}"
    assert absent.stdout.strip() != "not banned", absent.stdout
    assert listing.name in absent.stdout + absent.stderr, absent.stdout + absent.stderr

    listing.write_text("# seeded, then emptied\n", encoding="utf-8")
    empty = _run(root, "--check", CANDIDATE)
    assert empty.returncode == 0, f"exit {empty.returncode}; {empty.stdout + empty.stderr}"
    assert empty.stdout.strip() == "not banned", empty.stdout
    assert absent.stdout != empty.stdout, (absent.stdout, empty.stdout)


# ── the detector, with a positive control ────────────────────────────────────

def test_check_answers_yes_for_a_listed_phrase_and_no_for_an_unlisted_one(tmp_path):
    """THE positive control. A `--check` that had gone blind — a list that failed to
    load, a digest spelling that drifted from the matcher's — would answer "not
    banned" to everything, which is indistinguishable from a clean tree. The first
    assertion is the one that catches that; the second keeps it from being satisfied
    by answering "banned" to everything."""
    root = _sandbox(tmp_path)
    listing = root / "tools" / "tests" / "banned_tokens.txt"
    listing.write_text(f"# digests\n{_scan.digest(CANDIDATE)}\n", encoding="utf-8")

    hit = _run(root, "--check", CANDIDATE)
    miss = _run(root, "--check", OTHER)

    assert hit.returncode == 0 and miss.returncode == 0
    assert hit.stdout.strip() == "banned", hit.stdout
    assert miss.stdout.strip() == "not banned", miss.stdout


def test_a_respelling_of_a_listed_phrase_is_still_caught(tmp_path):
    """Green by design: the reason the stored form is the tokeniser's canonical one.
    A list that stored the phrase verbatim would answer "not banned" here, and a
    coinage is banned precisely so that re-spelling it does not get it past."""
    root = _sandbox(tmp_path)
    listing = root / "tools" / "tests" / "banned_tokens.txt"
    listing.write_text(f"# digests\n{_scan.digest(CANDIDATE)}\n", encoding="utf-8")

    respelled = CANDIDATE[:3].upper() + "_" + CANDIDATE[3:]
    assert respelled != CANDIDATE
    assert _run(root, "--check", respelled).stdout.strip() == "banned"


# ── the guard does not leak what it guards ───────────────────────────────────

def test_tree_phrases_excludes_the_ban_list_itself(tmp_path, monkeypatch):
    """The list is the one tracked file whose contents must not become "phrases the
    harness already uses" — vetting a candidate against them would compare a term to
    the digests guarding it. Both halves are asserted: an ordinary file IS read
    (without which the exclusion is trivially true and the sweep is looking at
    nothing) and the ban list is NOT."""
    ordinary = tmp_path / "ordinary.txt"
    ordinary.write_text(CANDIDATE, encoding="utf-8")
    listing = tmp_path / "banned_tokens.txt"
    marker = _scan.digest(OTHER)
    listing.write_text(f"# digests\n{marker}\n", encoding="utf-8")

    monkeypatch.setattr(B, "BANLIST", listing)
    monkeypatch.setattr(B, "tracked_files", lambda: [ordinary, listing])

    phrases = B.tree_phrases()
    assert CANDIDATE in phrases, "the sweep read nothing"
    assert marker not in phrases, "the ban list's own contents became tree phrases"


def test_the_real_sweep_has_something_to_exclude():
    """Green by design, and it is what keeps the test above honest: the exclusion is
    by path identity, so it means nothing unless the real list really is tracked."""
    tracked = B.tracked_files()
    assert len(tracked) > 100, len(tracked)
    assert B.BANLIST in tracked


# ── the tree it vets against, when git cannot be read ────────────────────────


def test_an_empty_ls_files_is_could_not_look_not_an_empty_tree(tmp_path):
    """CLAUDE.md's headline rule: an external tool returning nothing at exit 0
    is not evidence of absence. `git ls-files` with no output is a tree nobody
    could read, not a tree with nothing in it — and `tree_phrases()` returned
    the empty set for it, so `vet()`'s "already in the tree" check
    (`canonical(toks) in present`) could never be True and EVERY candidate was
    accepted. That silently disarms the one refusal that stops a term the
    product legitimately uses from being banned.

    Measured before the fix: exit 0, `1 added`, and a list written from a tree
    that was never read. The precondition is asserted first so a green here can
    never be a git that quietly listed something."""
    root = _sandbox_uncommitted(tmp_path)
    listing = root / "tools" / "tests" / "banned_tokens.txt"

    ls = subprocess.run(["git", "-C", str(root), "ls-files"],
                        capture_output=True, text=True)
    assert ls.returncode == 0 and ls.stdout.strip() == "", (ls.returncode, ls.stdout)

    p = _run(root, "--add", CANDIDATE)
    assert p.returncode == 2, f"exit {p.returncode}; output: {p.stdout + p.stderr}"
    assert "Traceback" not in p.stderr, p.stderr
    assert not listing.exists(), "it seeded a list from a tree it could not read"


def test_a_git_that_fails_is_refused_by_name_not_by_traceback(tmp_path):
    """The other half of the same unguarded call. `tracked_files()` ran git with
    `check=True` and nothing caught the raise, so a `git ls-files` that FAILED
    reached the operator as a `CalledProcessError` traceback at exit 1 — a code
    `main()` never returns, so "git could not run" and "the tool is broken"
    arrived looking identical, the shape item 117 found in `policy.py
    --validate`.

    The refusal must NAME the failure as a git exit, not as an empty tree: git
    that fails prints nothing on stdout, so the empty-result branch would ALSO
    fire and exit 2 with the wrong sentence ("listed no tracked files"). The
    last assertion is what tells the two apart — without it, deleting the
    returncode check survives, because the empty branch answers for it."""
    root = _sandbox_no_git(tmp_path)
    listing = root / "tools" / "tests" / "banned_tokens.txt"

    ls = subprocess.run(["git", "-C", str(root), "ls-files"],
                        capture_output=True, text=True)
    assert ls.returncode != 0, f"expected git to fail; got {ls.returncode}: {ls.stdout}"

    p = _run(root, "--add", CANDIDATE)
    assert p.returncode == 2, f"exit {p.returncode}; output: {p.stdout + p.stderr}"
    assert "Traceback" not in p.stderr, p.stderr
    assert not listing.exists(), "it seeded a list without being able to read the tree"
    assert "exited" in p.stderr, p.stderr  # a git FAILURE, not "no tracked files"


# ── vetting ──────────────────────────────────────────────────────────────────

def test_a_phrase_the_tree_already_uses_is_refused_unless_forced():
    """Green by design. The refusal is what stops a term the product legitimately
    uses from being banned, and `--force` is a visible flag rather than a silent
    bypass, so both directions are worth pinning."""
    canonical = _scan.canonical(_scan.tokenize(CANDIDATE))
    assert B.vet(CANDIDATE, set())[0] is True
    ok, why = B.vet(CANDIDATE, {canonical})
    assert ok is False and "already use this" in why
    assert B.vet(CANDIDATE, {canonical}, force=True)[0] is True


def test_a_stoplist_word_is_refused_even_with_force():
    """A matcher limit, not a judgement call: `--force` asserts "those occurrences
    are violations I am removing", which cannot be true of the word `core`."""
    word = next(iter(sorted(B.STOPLIST)))
    ok, why = B.vet(word, set(), force=True)
    assert ok is False and "stoplist" in why


def test_an_over_long_phrase_is_refused_rather_than_stored_unmatchable():
    """A 4-gram would hash to something the matcher's 1..3-gram windows can never
    produce: stored, permanently inert, and indistinguishable from a term nobody
    has ever tripped over."""
    phrase = " ".join([CANDIDATE] * (_scan.MAX_NGRAM + 1))
    ok, why = B.vet(phrase, set(), force=True)
    assert ok is False and str(_scan.MAX_NGRAM) in why


# ── the console ──────────────────────────────────────────────────────────────

def test_it_reconfigures_its_console_through_the_one_helper():
    """`test_cli.py`'s two sweeps both derive their roster from `_shipped()`, which
    excludes anything with `tests` in its path — so this CLI, which an operator runs
    and which prints an arbitrary phrase out of an arbitrary TOML, was invisible to
    both and carried no console rule at all. The second assertion is item 115's: a
    fifteenth verbatim copy of the three-line block is the defect, not the fix."""
    src = (ROOT / "tools" / "tests" / "ban.py").read_text(encoding="utf-8")
    assert "cli.use_utf8()" in src, "ban.py does not reconfigure its console"
    assert "reconfigure" not in src, "ban.py hand-rolls a rule cli.use_utf8 owns"
