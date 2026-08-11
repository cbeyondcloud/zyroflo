"""One suffix-to-grammar map, and what it cost to have had two.

`tests` mapped `.java` and `.py` and nothing else, so every Jest suite fell through to its
"no parser is installed" branch and said exactly that — while `frontend`, three directories
away, mapped `.js` to `javascript` and read the same syntax happily with a grammar this
repository pins and ships. **The row was false on every platform**, and the harness was
declining to read files it could already read. Nothing detected it, because a row reporting
a loss looks identical whether the loss is real or invented; only comparing the two readers
against each other showed it.

The second half is the spelling. `tests` reported the file extension (`js`, `ts`) where
`frontend` reported the grammar (`javascript`, `typescript`), so one cause arrived under two
names. That is cosmetic exactly until something groups these rows by pattern — item 84 —
at which point the report's own summary is wrong, and wrong in the direction of looking
more thorough than it is.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.lib import srcfacts  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import _lang, frontend  # noqa: E402
from tools.graph.extractors import tests as testsx  # noqa: E402

JS_SUITE = """\
// exercises /orders/v1/list, mentioned here in prose.
describe('orders', () => {
  it('lists', () => {
    const mockPath = '/orders/v1/list';
    expect(mockPath).toBeTruthy();
  });
});
"""

TS_SUITE = """\
describe('orders', () => {
  it('lists', () => {
    const mockPath: string = '/orders/v1/list';
  });
});
"""


def ctx_for(tmp_path: Path, files: dict[str, str], alias: str = "svc") -> ExtractContext:
    root = tmp_path / alias
    paths = []
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    return ExtractContext(repos=[RepoRef(alias=alias, path=root)], run_id="r",
                          cache={}, files={alias: paths})


def unresolved(res, kind: str) -> list[dict]:
    return [r for r in res.unresolved if r["kind"] == kind]


# ── the reported defect ────────────────────────────────────────────────────────
def test_a_javascript_suite_is_read_rather_than_reported_unreadable(tmp_path: Path):
    """**The defect, stated as behaviour.** `tree-sitter-javascript` is a pinned dependency
    and `available("javascript")` is true here and on CI, so a row saying otherwise was
    false wherever it was read."""
    assert srcfacts.available("javascript"), (
        "this test is meaningless without the javascript grammar — it is pinned in "
        "pyproject.toml, so its absence is a broken environment, not a skip")
    res = testsx.TestsExtractor().extract(ctx_for(tmp_path, {"a.test.js": JS_SUITE}))
    assert unresolved(res, "grammar-unavailable") == [], (
        "a Jest suite still reports that no parser is installed for it")
    assert [e.dst for e in res.edges if e.dst.startswith("ep:")], (
        "the suite parsed but named nothing — the row is gone and so is the fact it hid")


def test_a_javascript_suite_still_never_becomes_a_caller(tmp_path: Path):
    """The property the whole extractor exists to hold, re-proved for a language that only
    started parsing today. Turning on a parse path is exactly how a phantom caller gets in:
    the endpoint is now visible where before it was invisible, and calling it a dependency
    would put a consumer nobody can find into every blast radius that endpoint appears in.
    """
    res = testsx.TestsExtractor().extract(ctx_for(tmp_path, {"a.test.js": JS_SUITE}))
    assert not [e for e in res.edges if e.type == "DG_CALLS"]
    to_ep = [e for e in res.edges if e.dst.startswith("ep:")]
    assert len(to_ep) == 1 and to_ep[0].type == "DG_TESTS"
    assert to_ep[0].confidence == 0.8


def test_a_path_in_a_javascript_comment_is_not_a_mention(tmp_path: Path):
    """Read as text this suite names the path twice; read as a parse tree it is one literal
    and one comment. That distinction is the entire reason for parsing rather than grepping,
    and it had never been exercised on a JavaScript source."""
    res = testsx.TestsExtractor().extract(ctx_for(tmp_path, {"a.test.js": JS_SUITE}))
    literal_line = JS_SUITE.splitlines().index("    const mockPath = '/orders/v1/list';") + 1
    assert [e.line for e in res.edges if e.dst.startswith("ep:")] == [literal_line]


# ── the spelling ───────────────────────────────────────────────────────────────
def test_an_unreadable_suite_is_reported_by_grammar_not_by_file_extension(tmp_path: Path):
    """`ts` is not a language. A reader told `typescript` can go and install a grammar; a
    reader told `ts` cannot tell whether that is a language, a typo, or a hole in the
    harness — and `frontend` reports the same grammar under its full name, so one cause
    would arrive in the report under two names."""
    res = testsx.TestsExtractor().extract(ctx_for(tmp_path, {"a.test.ts": TS_SUITE}))
    rows = unresolved(res, "grammar-unavailable")
    if srcfacts.available("typescript"):        # pinned later; the row then should not exist
        assert rows == []
        return
    assert [r["language"] for r in rows] == ["typescript"]


def test_the_two_readers_spell_a_shared_grammar_the_same_way():
    """Item 84 groups unresolved rows by pattern using their own fields. Two spellings of
    one grammar is two patterns for one cause — a grouped report that is wrong in the
    direction of looking more thorough than it is."""
    assert frontend.LANGUAGES is _lang.GRAMMARS, (
        "the frontend reader has taken its own copy of the map again; the two disagreed "
        "last time and nothing noticed for as long as both were only ever read separately")


def test_every_suite_extension_has_a_grammar_mapped():
    """The drift that caused this. `KINDS` gained `.js`/`.ts` and the grammar map did not,
    so those suites silently took the unreadable branch — visible only as a row that looked
    like an honest report of a missing parser."""
    unmapped = sorted(s for s in testsx.KINDS if _lang.grammar_for(s) is None)
    assert not unmapped, (
        f"these are labelled as test suites but no grammar reads them: {unmapped}. Either "
        f"map them in `_lang.GRAMMARS` or stop claiming them in `KINDS` — the state in "
        f"between reports a missing parser for a parser that was never asked for.")


@pytest.mark.parametrize("suffix, grammar", [
    (".js", "javascript"), (".jsx", "javascript"), (".mjs", "javascript"),
    (".cjs", "javascript"), (".ts", "typescript"), (".tsx", "tsx"),
    (".java", "java"), (".py", "python"),
])
def test_the_map_answers_what_would_read_a_suffix(suffix, grammar):
    assert _lang.grammar_for(suffix) == grammar
    assert _lang.grammar_for(suffix.upper()) == grammar, "a suffix arrives cased as it is"


def test_an_unmapped_suffix_is_a_different_answer_from_an_uninstalled_grammar():
    """Two facts that had been reported as one. None means *this harness maps nothing to
    that suffix* — nobody asked for a parser; False from `available()` means one was asked
    for and is not here. A reader who cannot tell them apart goes and installs a grammar
    that was never missing."""
    assert _lang.grammar_for(".rb") is None
    assert srcfacts.available("java") is True


# ── the two causes are said differently ────────────────────────────────────────
def test_an_uninstalled_grammar_and_an_unmapped_suffix_give_different_reasons(
        tmp_path: Path, monkeypatch):
    """Forced rather than found, on the lesson item 16 paid for: routing this assertion
    through a real language made it mean different things on different machines."""
    monkeypatch.setattr(srcfacts, "available", lambda language: False)
    res = testsx.TestsExtractor().extract(ctx_for(tmp_path, {"a.test.js": JS_SUITE}))
    (installed_row,) = unresolved(res, "grammar-unavailable")
    assert installed_row["language"] == "javascript"
    assert "no parser is installed for javascript on this machine" in installed_row["why"]

    monkeypatch.setattr(_lang, "GRAMMARS", {})
    res = testsx.TestsExtractor().extract(ctx_for(tmp_path, {"a.test.js": JS_SUITE}))
    (unmapped_row,) = unresolved(res, "grammar-unavailable")
    assert "maps no grammar to .js sources" in unmapped_row["why"]
    assert "never asked for" in unmapped_row["why"]


def test_a_suite_that_fails_to_parse_reports_the_parser_error(tmp_path: Path, monkeypatch):
    """`unparsable` promises `error` in this module's own UNRESOLVED_FIELDS and carried none.
    Dormant, not harmless: the branch runs only when a suite that HAS a grammar fails to
    parse, so it never ran here or on CI and would first have surfaced on an adopter's
    build — as a contract violation exiting 2 and calling the harness defective."""
    def boom(language, source):
        raise RuntimeError("grammar exploded")
    monkeypatch.setattr(srcfacts, "parse", boom)
    res = testsx.TestsExtractor().extract(ctx_for(tmp_path, {"a.test.js": JS_SUITE}))
    (row,) = unresolved(res, "unparsable")
    assert row["error"] == "grammar exploded"
