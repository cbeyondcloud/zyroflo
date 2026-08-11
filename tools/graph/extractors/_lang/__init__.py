"""One suffix-to-grammar map, shared by every extractor that parses source.

Underscore-prefixed, so the extractor registry skips it — the same arrangement as `_java/`
and `_yaml/`.

## Why this is not in `core/lib/srcfacts.py`, where the grammars already live

`srcfacts` owns the grammar-to-module table, so a suffix-to-grammar table looks like it
belongs on the shelf beside it. It does not. Putting it there would mean every new file
extension forces an edit to core, which is the exact boundary the M1 kill gate exists to
catch, and the same reasoning that keeps the per-kind `UNRESOLVED_FIELDS` out of core:
**core owns the mechanism, the extractor layer owns the vocabulary.**

## Why it is shared rather than copied

Two readers were each keeping their own map and they disagreed. `tests` mapped `.java` and
`.py` and nothing else, so every Jest suite fell to its "no parser is installed" branch and
said exactly that — while `frontend`, three directories away, mapped `.js` to `javascript`
and parsed the same syntax happily using a grammar this repository pins and ships. The row
was **false on every platform**, and the harness was declining to read files it could
already read. Nothing detected it, because a row that reports a loss looks the same whether
the loss is real or invented.

They also spelled one language two ways — `js` from one reader, `javascript` from the other,
for a single grammar. That is cosmetic right up until something groups these rows by
pattern, at which point one pattern becomes two and the report's own summary is wrong.

## A suffix is claimed here whether or not its grammar is installed

`srcfacts.available()` answers "can this be parsed on this machine", which varies by platform
and by what has been fetched. This answers "what would read it", which does not vary at all.
Keeping the two apart is what lets an unreadable file be reported as a **named language**
rather than as a bare file extension — and a reader who is told `typescript` can go install
a grammar, where a reader who is told `ts` cannot tell whether that is a language, a
mistake, or a gap in the harness.
"""
from __future__ import annotations

#: Suffix to the grammar that reads it. `.tsx` is its own grammar rather than an alias for
#: `typescript`: tree-sitter publishes them as two parsers, and the one that cannot read JSX
#: fails on a `.tsx` file in a way that reads as a broken file rather than a wrong parser.
GRAMMARS: dict[str, str] = {
    ".java": "java",
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
}


def grammar_for(suffix: str) -> str | None:
    """The grammar that reads this suffix, or None if this harness maps none.

    None is a fact about the harness — "nothing here claims to read this" — and is a
    different answer from `available()` returning False, which is a fact about the machine.
    Callers that report a loss owe their reader the distinction; conflating them is how a
    row comes to say a parser is missing when the truth is that nobody ever asked for one.
    """
    return GRAMMARS.get(suffix.lower())
