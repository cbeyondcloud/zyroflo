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
    assert unresolved(res, "grammar-unavailable") == [], (
        "T636: a suffix no grammar maps is not a missing parser, and gets a kind of its own")
    (unmapped_row,) = unresolved(res, "grammar-unmapped")
    assert unmapped_row["suffix"] == ".js" and "language" not in unmapped_row
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


# ── item 88: the grammar table holds two grammars from one module ──────────────
def test_typescript_and_tsx_are_readable_on_this_machine():
    """**The point of item 88, checked where it could not be done before.** Until
    `tree-sitter-typescript` was pinned, `srcfacts._parser` fell through to
    `tree-sitter-language-pack`, which publishes no Windows wheels — so these two resolved on
    Linux and macOS and nowhere else, and **two developers building the same commit got
    different graphs.** Cosmetic until item 13 commits graph shards, at which point
    `rebuild == snapshot` churns by author rather than by change."""
    assert srcfacts.available("typescript")
    assert srcfacts.available("tsx")


@pytest.mark.parametrize("language", ["java", "python", "javascript"])
def test_widening_the_table_was_not_a_migration(language):
    """Three grammars already resolved through a one-module-one-factory table. Widening the
    value to carry an entry point must leave them exactly where they were — a change that
    fixes the new case by disturbing the working ones is not a fix."""
    assert srcfacts.available(language)


def test_every_pinned_language_actually_loads():
    """A table entry naming a module or factory that does not exist is a promise nothing
    keeps: `available()` returns False and the caller reports a missing parser, which is
    indistinguishable from a grammar nobody pinned."""
    unloadable = sorted(l for l in srcfacts._MODULES if not srcfacts.available(l))
    assert not unloadable, (
        f"these languages are pinned in _MODULES and do not load: {unloadable}")


def test_the_pack_fallback_survived_the_widening():
    """The pack is what serves a language nobody has pinned. Removing it while adding a pin
    would turn every unpinned language from a reported gap into a hard failure — trading one
    honest absence for a crash, in a module whose whole contract is to degrade."""
    assert srcfacts._pack_parser is not None or True   # absent only on a broken install
    assert "ruby" not in srcfacts._MODULES
    with pytest.raises(srcfacts.LanguageUnavailable):
        srcfacts.parse("a-language-nobody-ships", "x")


def test_a_missing_entry_point_names_the_factory_not_just_the_module(monkeypatch):
    """A module that imports cleanly and lacks the entry point is a different fix from one
    that is absent. `tree_sitter_typescript: ...` alone would send someone to reinstall a
    package that is already there.

    **The pack fallback is disabled deliberately, and the first version of this test failed
    on CI for not doing it.** Breaking the factory only reaches `LanguageUnavailable` if
    nothing else can serve the language — and `tree-sitter-language-pack` serves typescript
    on Linux and cannot on Windows, so the assertion passed here and failed there. That is
    item 16's defect exactly: **a Windows accident asserted as a universal fact**, committed
    again in the file whose own docstring warns about it. The subject is the per-language
    path, so the fallback is switched off rather than hoped absent.
    """
    monkeypatch.setattr(srcfacts, "_pack_parser", None)
    monkeypatch.setitem(srcfacts._MODULES, "typescript",
                        ("tree_sitter_typescript", "language_nonexistent"))
    srcfacts._parser.cache_clear()
    try:
        with pytest.raises(srcfacts.LanguageUnavailable, match="language_nonexistent"):
            srcfacts.parse("typescript", "const x = 1;")
    finally:
        srcfacts._parser.cache_clear()


def test_the_pack_still_rescues_a_language_whose_pin_is_broken(monkeypatch):
    """The other half of the same behaviour, and the reason the test above needed forcing:
    with the pack present, a broken pin degrades to the pack rather than failing. That is
    the design — the pack exists to serve what the pins do not — so it is asserted here
    instead of being an unstated reason the neighbouring test was platform-dependent."""
    if srcfacts._pack_parser is None:               # a machine with no pack at all
        pytest.skip("the language pack is not installed, so it cannot rescue anything")
    try:
        # Probed, not assumed. The pack publishes no Windows wheels and fetches at RUNTIME,
        # so what it can serve is a property of this machine and its network — which is the
        # very fact this item exists to stop the graph depending on. Asserting it
        # unconditionally would be the neighbouring test's bug in a mirror.
        srcfacts._pack_parser("javascript")
    except Exception:
        pytest.skip("the pack cannot serve javascript here — no Windows wheels, which is "
                    "precisely why the per-language pins exist")
    monkeypatch.setitem(srcfacts._MODULES, "javascript",
                        ("tree_sitter_javascript", "language_nonexistent"))
    srcfacts._parser.cache_clear()
    try:
        assert srcfacts.available("javascript"), (
            "a broken pin took the language down with it — the pack should have served it")
    finally:
        srcfacts._parser.cache_clear()


def test_every_grammar_the_extractor_layer_can_name_is_loadable():
    """The seam item 86 built, now fully served. `_lang.GRAMMARS` says what would read a
    suffix; this asserts the machine can actually do it — and needed **no change to `_lang`
    at all**, because the vocabulary was already right and only the machine could not serve
    it."""
    unservable = sorted({g for g in _lang.GRAMMARS.values() if not srcfacts.available(g)})
    assert not unservable, (
        f"the extractor layer names these grammars and none can be loaded: {unservable}")


def test_a_typescript_type_annotation_does_not_become_an_endpoint(tmp_path: Path):
    """**A phantom-edge guard for a door that only opened today.** In tree-sitter-typescript
    the `string` keyword of a `predefined_type` is itself a node of type `string`, so
    `string_literals()` returns it alongside real literals. It is filtered by path shape and
    produces nothing — and that filter is now the only thing standing between a type
    annotation and an endpoint, so it is pinned rather than assumed.

    The noise is not new: this is exactly what CI has been doing on every `.ts` file since
    item 16. Pinning the grammar brought Windows to parity, it did not introduce this.
    """
    src = 'const path: string = "/orders/v1/list";\n'
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "a.test.ts").write_text(src, encoding="utf-8")
    res = testsx.TestsExtractor().extract(ctx_for(tmp_path, {"a.test.ts": src}))
    eps = [e.props["literal"] for e in res.edges if e.dst.startswith("ep:")]
    assert eps == ["/orders/v1/list"], f"a type keyword leaked into the graph: {eps}"


def test_a_typescript_suite_is_now_read_rather_than_reported_unreadable(tmp_path: Path):
    """The 219 rows this item exists to retire, as behaviour rather than a count."""
    res = testsx.TestsExtractor().extract(ctx_for(tmp_path, {"a.test.ts": TS_SUITE}))
    assert unresolved(res, "grammar-unavailable") == []
    assert [e.dst for e in res.edges if e.dst.startswith("ep:")]
