"""Source-fact extraction over tree-sitter, parameterized by language.

Why this is core and language-neutral: the plan's shared lib originally named a Java-only
`javasrc`, but the Rev-3 genericity rule forbids language types in core signatures — and
the M1 second-estate profile needs the identical machinery for Python. So core owns the
tree-sitter plumbing (parse, query, literal and line recovery) and each profile supplies
its own queries, which are inherently language-specific.

This is what retires the regex-miss class the plan calls out: nested classes, multi-line
annotations and text blocks are structure to a parser and accidents to a regex.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterator

try:  # pragma: no cover - import guard exercised only on a broken install
    from tree_sitter_language_pack import get_parser as _pack_parser
except ImportError:  # pragma: no cover
    _pack_parser = None

#: Per-language grammar packages, tried before the aggregate pack. Each entry names the
#: module **and the factory to call inside it**, because one module can publish more than
#: one grammar.
#:
#: The aggregate `tree-sitter-language-pack` publishes no Windows wheels (verified at M0:
#: 1.14.1 offers macos/linux only), so on Windows it fails at *runtime* with a download
#: error rather than at install time. The individual grammar packages do ship Windows
#: wheels, so they are the primary source and the pack is the fallback for languages we
#: have not pinned individually. Add an entry here to support a new language.
#:
#: **Why the factory is named rather than assumed.** Every entry here used to be a bare
#: module name and `language()` was called on it. `tree_sitter_typescript` publishes
#: `language_typescript()` and `language_tsx()` and **no `language()` at all**, so the
#: one-module-one-grammar assumption could not express it — it was not an inconvenience to
#: work around but a shape the table could not hold.
#:
#: **What pinning typescript buys, and it is not the row count.** Without it the pack served
#: these two on Linux and macOS and nothing on Windows, so **two developers building the same
#: commit got different graphs** — one missing every `.ts`/`.tsx` fact. That is cosmetic until
#: item 13 commits graph shards, at which point `rebuild == snapshot` churns by *author*
#: rather than by *change*, and a regression check that churns gets switched off. It also
#: retires a second axis: a grammar the pack fetches at runtime makes extraction coverage
#: depend on **network access** as well as platform.
_MODULES: dict[str, tuple[str, str]] = {
    "java": ("tree_sitter_java", "language"),
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
}


class LanguageUnavailable(RuntimeError):
    """Raised when a grammar is not installed — callers degrade, they do not crash."""


@dataclass(frozen=True, slots=True)
class Capture:
    """One query hit: its text, its 1-based line, and the capture name it matched."""

    name: str
    text: str
    line: int
    start_byte: int
    end_byte: int


@lru_cache(maxsize=16)
def _parser(language: str) -> Any:
    """Build a parser, preferring the pinned per-language package.

    Returns the parser with its Language attached as `.language`, which queries need.
    """
    errors: list[str] = []

    entry = _MODULES.get(language)
    if entry:
        module_name, factory = entry
        try:
            import importlib

            from tree_sitter import Language, Parser

            grammar = importlib.import_module(module_name)
            lang = Language(getattr(grammar, factory)())
            parser = Parser(lang)
            parser.language = lang  # keep the Language reachable for query compilation
            return parser
        except Exception as exc:
            # The factory is named in the error, not just the module: a module that imports
            # cleanly and lacks the entry point is a different fix from one that is absent,
            # and "tree_sitter_typescript: ..." alone would send someone to reinstall a
            # package that is already there.
            errors.append(f"{module_name}.{factory}: {exc}")

    if _pack_parser is not None:
        try:
            return _pack_parser(language)
        except Exception as exc:
            errors.append(f"language-pack: {exc}")

    raise LanguageUnavailable(
        f"no tree-sitter grammar for {language!r}"
        + (f" ({'; '.join(errors)})" if errors else "")
        + ". Install the per-language package (e.g. tree-sitter-<lang>) and register it in "
        "srcfacts._MODULES."
    )


def available(language: str) -> bool:
    """Whether this language can be parsed here — lets callers fall back deliberately."""
    try:
        _parser(language)
        return True
    except LanguageUnavailable:
        return False


class SourceFile:
    """A parsed source file. Byte offsets are authoritative; lines are derived from them."""

    __slots__ = ("language", "source", "_tree", "_lang")

    def __init__(self, language: str, source: str) -> None:
        self.language = language
        self.source = source
        parser = _parser(language)
        self._tree = parser.parse(source.encode("utf-8", errors="replace"))
        self._lang = getattr(parser, "language", None)

    @property
    def root(self) -> Any:
        return self._tree.root_node

    def line_of(self, byte_offset: int) -> int:
        """1-based line for a byte offset."""
        return self.source.encode("utf-8", errors="replace").count(b"\n", 0, byte_offset) + 1

    def text_of(self, node: Any) -> str:
        return node.text.decode("utf-8", errors="replace")

    def query(self, query_source: str) -> list[Capture]:
        """Run a tree-sitter query and return captures in document order.

        Query syntax is language-specific and therefore supplied by the profile; core only
        runs it. Returns [] rather than raising when the query does not compile against this
        grammar, so a grammar upgrade degrades coverage instead of breaking the build — the
        validation report is where that loss becomes visible.
        """
        if self._lang is None:
            return []
        try:
            from tree_sitter import Query, QueryCursor

            cursor = QueryCursor(Query(self._lang, query_source))
            raw = cursor.captures(self.root)
        except Exception:
            return []

        out: list[Capture] = []
        for name, nodes in raw.items():
            for n in nodes:
                out.append(
                    Capture(
                        name=name,
                        text=self.text_of(n),
                        line=n.start_point[0] + 1,
                        start_byte=n.start_byte,
                        end_byte=n.end_byte,
                    )
                )
        out.sort(key=lambda c: c.start_byte)
        return out

    def walk(self, *node_types: str) -> Iterator[Any]:
        """Depth-first walk yielding nodes of the given types (all nodes when none given)."""
        want = set(node_types)
        stack = [self.root]
        while stack:
            node = stack.pop()
            if not want or node.type in want:
                yield node
            stack.extend(reversed(node.children))

    def string_literals(self, *literal_types: str) -> list[Capture]:
        """Every string literal, with its line — the raw material for URL/topic mining."""
        types = literal_types or ("string_literal", "string", "interpreted_string_literal")
        return [
            Capture("literal", unquote(self.text_of(n)), n.start_point[0] + 1, n.start_byte, n.end_byte)
            for n in self.walk(*types)
        ]


def unquote(text: str) -> str:
    """Strip one layer of quotes, including Java/Python triple-quoted text blocks."""
    t = text.strip()
    for q in ('"""', "'''"):
        if t.startswith(q) and t.endswith(q) and len(t) >= 2 * len(q):
            return t[len(q) : -len(q)].strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        return t[1:-1]
    return t


def parse(language: str, source: str) -> SourceFile:
    """Parse source text. Raises LanguageUnavailable if the grammar is missing."""
    return SourceFile(language, source)
