"""What a shard row PROMISES its reader, and the one refusal both readers share.

A JSONL row is read back by two modules — `core/lib/emit.read_jsonl` and
`core/store/load.rows_to_model` — and each had its own answer to the same question. An
edge row with **no** `provenance` came back as `Provenance.EXTRACTED`, the highest-trust
value in the taxonomy, at *both*; an edge row with no `line` became `0` at one and a
`TypeError` about a positional argument at the other. Neither is a reading of the row.
`EXTRACTED` says a human read the fact out of a file, and `0` is the model's *meaningful*
"about the file rather than a place in it" — so a field lost between the extractor and
the reader was restored as the strongest claim the graph can make, silently, and a
context pack then cited it.

That is CLAUDE.md's cardinal failure aimed at the graph's own evidence: *a graph that
looks complete but isn't is worse than one that admits its gaps, because a human will act
on it.* The rule is therefore the one `check_unresolved` already applies to unresolved
rows, one layer down: **absent and null are the same defect**, and the row and the field
are both named so a person can go and look.

**One rule, imported twice — not one rule written twice.** Two readers restating a rule
is how these two drifted; a reader that keeps a private copy is a reader that will keep an
old copy. The promise is per KIND because a kind that never carried a field cannot be
missing it: `Node` has three fields and never promised `line`, and refusing a node for
lacking one would be the mirror of the defect it fixes.

Nothing here names a language, framework, or estate.
"""
from __future__ import annotations

from typing import Any, Mapping

#: Per record kind, what a row of that kind owes its reader. These are exactly the fields
#: whose absence a reader would otherwise have to invent a value for — the ones the
#: writer always emits and the model cannot default without asserting something.
#:
#: `provenance` is on the edge list although the dataclass gives it a default, and that
#: default is the whole reason: a *constructor* may reasonably default to EXTRACTED
#: because its caller is an extractor that just read the file, but a *reader* defaulting
#: to it is announcing a reading nobody did. `confidence` and `canonical` are deliberately
#: NOT here — their defaults are also the strongest value, which is a live question, but
#: it is a different one and it has not been ruled on.
PROMISED_BY_KIND: dict[str, tuple[str, ...]] = {
    "node": ("id", "type"),
    "edge": ("src", "type", "dst", "source_file", "line", "extractor", "provenance"),
}


class RowFieldError(ValueError):
    """A row is missing a field its own kind promised.

    A `ValueError`, because that is what both readers already raise for a row the model
    refuses, and a caller that catches the model's refusal must catch this one too — it
    is the same event, found one step earlier.
    """


def check_row(kind: str, row: Mapping[str, Any], *, where: str) -> None:
    """Refuse `row` if its kind promised a field it does not carry. Otherwise return.

    `where` locates the row for a human — a shard path and line where the reader knows
    one, a path and an index where the rows have already been split apart. It is not
    decoration: the reason to refuse rather than default is that somebody has to go and
    look at the row, and a refusal that cannot say which row is a refusal nobody can act on.

    A kind with no declared promise is passed through untouched. That is not laxity; it
    is the same boundary `check_unresolved` draws — core owns the mechanism and refuses to
    learn a vocabulary it was not given, so a new record kind arrives as a row this
    function has no opinion about rather than as a core edit.
    """
    for name in PROMISED_BY_KIND.get(kind, ()):
        # Present-but-empty is a real state and the model judges it (`source_file=""` is
        # refused there, `line=0` is accepted there). Absent, and null, are not states of
        # the estate at all — they are states of the row, and only the reader can see them.
        if name not in row:
            raise RowFieldError(
                f"{where}: {kind} row is missing {name!r} — a row of this kind promises "
                f"it, so reading it means inventing the fact the graph exists to prove. "
                f"Rebuild (tools/graph/build.py) rather than loading a row whose "
                f"provenance is the reader's own default")
        if row[name] is None:
            raise RowFieldError(
                f"{where}: {kind} row has {name!r} set to null — a row of this kind "
                f"promises it, and null is the same defect as absent: neither is a value "
                f"the extractor found. Rebuild (tools/graph/build.py)")
