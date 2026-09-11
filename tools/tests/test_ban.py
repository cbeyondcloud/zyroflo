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


def _sandbox(tmp_path: Path) -> Path:
    """A copy of the CLI whose ban list is the test's, not the repository's.

    `BANLIST` and `ROOT` are both derived from `__file__`, so pointing the tool at a
    different list means running it from a different tree. Copying is what lets the
    corrupt-list and absent-list cases be spawned at all.
    """
    root = tmp_path / "harness"
    (root / "tools" / "tests").mkdir(parents=True)
    for rel in _SANDBOX_FILES:
        (root / rel).write_bytes((ROOT / rel).read_bytes())
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
    reached the operator as a traceback. The refusal matters less than the second
    assertion: `--add` rewrites the file from whatever `read_list()` returned, so a
    corrupt list that read as "empty" would be silently replaced by a one-entry one
    and the guard would stop matching everything it used to."""
    root = _sandbox(tmp_path)
    listing = root / "tools" / "tests" / "banned_tokens.txt"
    listing.write_text("# a list\nthis-is-not-a-digest\n", encoding="utf-8")
    before = listing.read_bytes()

    p = _run(root, "--add", CANDIDATE)

    assert p.returncode == 2, f"exit {p.returncode}; output: {p.stdout + p.stderr}"
    assert "Traceback" not in p.stderr, p.stderr
    assert listing.name in p.stdout + p.stderr
    assert listing.read_bytes() == before, "it rewrote a list it could not read"


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
