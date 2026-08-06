"""Proves the leak-guard matcher works, independently of the tree's current state.

Everything here runs against **synthetic** inputs and an **invented** ban list. That is not
squeamishness: a test that hardcoded the real banned coordinates in order to check they match
would publish them in plaintext, defeating the whole reason the ban list is hashed.

The real list is validated two ways that need no plaintext at all — its shape (well-formed
digests, non-empty), and `test_the_guard_fires_on_the_pre_cleanup_blobs`, which replays the
actual pre-remediation file contents out of git history and asserts the scanner catches them.
That is the strongest available evidence that this guard catches rather than merely asserts,
and it names nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.tests import _scan

ROOT = Path(__file__).resolve().parents[2]
BANLIST = Path(__file__).resolve().parent / "banned_tokens.txt"

#: An invented workspace, so no real coordinate appears in this file.
FAKE = {_scan.digest(p) for p in ["zorbcorp", "quux", "widget core"]}


# ── tokenisation ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    # The two false-positive words that motivated tokenising instead of regexing. Both must
    # stay single tokens, or a short banned coinage would match inside them forever.
    ("ripgrep", ["ripgrep"]),
    ("upgrade", ["upgrade"]),
    # Underscores split. `\b` would not do this, which is how SCREAMING_SNAKE names escape.
    ("ZORB_SERVICE", ["zorb", "service"]),
    ("eg_zorb_service_v2", ["eg", "zorb", "service", "v2"]),
    # camelCase splits, including the acronym-then-word boundary.
    ("ZorbQuuxHost", ["zorb", "quux", "host"]),
    ("HTTPServer", ["http", "server"]),
    ("zorb.quux.host", ["zorb", "quux", "host"]),
    ("zorb-corp", ["zorb", "corp"]),
    # A word that merely *contains* a coinage stays whole.
    ("rizorbep", ["rizorbep"]),
    ("", []),
])
def test_tokenise(text: str, expected: list[str]):
    assert _scan.tokenize(text) == expected


def test_digest_normalises_across_spellings():
    """The ban list cannot drift from the matcher: one concept, one digest, however written.

    The concatenated spelling belongs in this set. `AcmeCorp` splits into two tokens while
    `acmecorp` is one, so a space-joined canonical form would hash them differently and a ban
    on either spelling would silently miss the other — which is a re-spelling evasion, not a
    cosmetic difference.
    """
    forms = ["Widget-Core", "widget_core", "WidgetCore", "widget core", "WIDGET.CORE", "widgetcore"]
    assert len({_scan.digest(f) for f in forms}) == 1


def test_a_coined_compound_is_caught_however_it_is_spelled():
    """Regression for the evasion above: one ban entry, every spelling."""
    banned = {_scan.digest("zorbcorp")}
    for spelling in ["zorbcorp", "ZorbCorp", "zorb-corp", "ZORB_CORP", "zorb.corp"]:
        assert _scan.find_banned_tokens(spelling, banned), f"evaded via {spelling!r}"


def test_ngrams_are_bounded():
    grams = _scan.ngrams(["a", "b", "c", "d"])
    assert "a" in grams and "ab" in grams and "abc" in grams
    assert "abcd" not in grams, "4-grams would make the list unmaintainable"


# ── machine paths: the true positives ──────────────────────────────────────────
@pytest.mark.parametrize("text", [
    r"ZORB_ROOT=D:\Zorbland",
    "python sdlc-init.py --target D:/some-workspace",
    r'ap.add_argument("--estate", default="D:\\Zorbland")',
    r"c:\temp\thing",
    "E:/builds/out",
    r"\\fileserver\share",
    "/home/alice/projects",
    "/Users/alice/projects",
])
def test_machine_paths_are_caught(text: str):
    assert _scan.find_machine_paths(text), f"missed a machine path in {text!r}"


# ── machine paths: the false positives that made a naive regex unusable ────────
@pytest.mark.parametrize("text", [
    # Regex source where a drive letter is really the tail of a longer word.
    r"r'\s*lint_command:\s*\"(.*)\"'",
    r'f"installer failed:\n{proc.stdout}"',
    r'{"Makefile": "test:\n\techo"}',
    "note:\\next line",
    # URLs. `s:/` inside `https:/` is preceded by a word character.
    "https://github.com/example/repo.git",
    "see http://localhost:7474 for the browser",
    "bolt://localhost:7687",
    # Times, ratios, YAML.
    "a:1, b:2",
    "runs at 12:30/13:00",
    "key: value",
    # Placeholder home paths are the template doing its job.
    "/home/you/workspace",
    "/Users/username/repos",
])
def test_known_false_positives_stay_clean(text: str):
    assert not _scan.find_machine_paths(text), f"false positive on {text!r}"


# ── banned tokens ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "zorbcorp", "ZorbCorp", "zorb-corp-services", "ZORBCORP_HOST",
    "quux", "eg_quux_service_v2", "QUUX_SERVICE", "quux.master.json",
    "widget core", "Widget-Core", "the WidgetCore module",
])
def test_banned_phrases_are_caught(text: str):
    assert _scan.find_banned_tokens(text, FAKE), f"missed a banned phrase in {text!r}"


@pytest.mark.parametrize("text", [
    # Contains a banned coinage as a substring but is a different word.
    "rizorbepping", "aquuxa", "sequux",
    # `widget` alone is not banned — only the 2-gram is. This is the rule that keeps an
    # ordinary English word from firing across the tree.
    "a widget factory", "widgets are cheap", "core dump", "the core module",
    "unrelated prose entirely",
])
def test_near_misses_stay_clean(text: str):
    assert not _scan.find_banned_tokens(text, FAKE), f"false positive on {text!r}"


def test_a_bare_english_word_needs_a_multigram():
    """`widget` is legal; `widget core` is not. Without this distinction the list would fire
    on ordinary prose, get muted, and protect nothing."""
    assert not _scan.find_banned_tokens("widget", FAKE)
    assert _scan.find_banned_tokens("widget core", FAKE)


# ── Python is scanned via the AST, which removes the last FP class ─────────────
def test_python_literals_are_scanned_post_escape():
    """`"x:\\n"` is a real newline by the time it reaches the matcher and cannot look like a
    drive letter, while `"D:\\\\Zorb"` correctly arrives as a path. Scanning raw source text
    would get exactly one of these two wrong."""
    clean = 'MSG = "done:\\n"\n'
    dirty = 'ROOT = "D:\\\\Zorbland"\n'
    assert not _scan.scan_python(clean, FAKE)
    assert _scan.scan_python(dirty, FAKE)


def test_python_identifiers_and_comments_are_scanned():
    """A path in a comment ships to every reader, and a name is code. Neither is a literal,
    so `ast.Constant` alone would miss both."""
    assert _scan.scan_python("ZORBCORP_ROOT = 1\n", FAKE), "identifier not scanned"
    assert _scan.scan_python("x = 1  # see D:/zorbland\n", FAKE), "comment not scanned"
    assert _scan.scan_python("def load_quux_map():\n    pass\n", FAKE), "function name not scanned"


def test_unparseable_python_falls_back_to_raw_text():
    """A file that will not parse is exactly the one nobody has reviewed — it must not be
    silently skipped."""
    assert _scan.scan_python("def (((: \nZORB = 'D:/zorbland'\n", FAKE)


# ── the escape hatch ───────────────────────────────────────────────────────────
def test_allow_marker_suppresses_one_line_only():
    text = ("a = 'D:/zorbland'  # harness-allow: documented exception\n"
            "b = 'D:/zorbland'\n")
    findings = _scan.scan_python(text, FAKE)
    assert findings and all(f.line == 2 for f in findings), \
        "the marker must exempt its own line and nothing else"


def test_allow_marker_works_in_plain_text_too():
    text = "D:/zorbland  # harness-allow: example\nD:/zorbland\n"
    findings = _scan.scan_text(text, FAKE)
    assert [f.line for f in findings] == [2]


# ── the ban list file itself ───────────────────────────────────────────────────
def test_banlist_is_digests_only():
    """Plaintext here would be a disclosure AND a permanently non-matching entry, so it is a
    hard parse error rather than a lint."""
    banned = _scan.load_banned(BANLIST)
    assert len(banned) >= 10, "ban list looks unseeded"
    assert all(len(d) == 64 for d in banned)


def test_banlist_rejects_plaintext(tmp_path: Path):
    bad = tmp_path / "banned.txt"
    bad.write_text("# comment ok\nnot-a-digest\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sha256"):
        _scan.load_banned(bad)


def test_banlist_never_contains_the_words_it_guards():
    """Self-reference check: the file must be readable by anyone without telling them
    anything. Every non-comment line is a hash, so this is true by construction — asserted
    so a future 'helpful' plaintext comment cannot quietly undo it."""
    for line in BANLIST.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        assert not stripped or _scan._HEX64.match(stripped)


# ── the proof that the guard catches, rather than merely asserts ───────────────
def test_the_validation_record_is_present_and_says_the_guard_fired():
    """The one-time validation against real pre-remediation content lives in
    `docs/m0/guard-validation.md`, not in a test.

    It used to be a test: it replayed the actual pre-Rev-7 file contents out of git history
    and required the scanner to fire on each. That evidence could not survive item 73's
    history rewrite, which deletes those objects — after which the test would `pytest.skip`
    and still print green. **A skipping test is worse than no test, because it looks like
    coverage.** So the result was recorded, dated, and the history-dependent test removed
    rather than left as a shell that quietly stops checking anything.

    What is asserted here is only that the record exists and has not been hollowed out. The
    ongoing regression evidence is every other test in this file — synthetic, permanent, and
    independent of what git history happens to contain.
    """
    record = ROOT / "docs" / "m0" / "guard-validation.md"
    assert record.is_file(), "the guard's validation record is missing"
    text = record.read_text(encoding="utf-8")
    assert "d36b467" in text, "the record must name the commit it was validated against"
    assert "fired on every file" in text, "the record must state the outcome, not just the method"
    assert "What this does NOT establish" in text, \
        "a validation record without its limits overstates itself"
