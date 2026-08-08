"""Shared YAML reading helpers for the stack extractors that need them.

Underscore-prefixed, so the extractor registry skips it — the same arrangement as `_java/`.
This is shared *code*, not a shared cache entry: there is no state here and nothing to keep
in step, only one implementation of a thing two readers were each getting wrong separately.

## Why a YAML-aware line finder exists at all

`props.find_line` is a substring search. That is right for a properties file, where a key is
the start of a line, and **wrong for YAML**, where a key's name routinely appears inside an
earlier value:

    services:
      api:
        environment:
          DSN: postgresql://db:5432/app     <- `db:` matches HERE
      db:                                   <- and the service is HERE
        image: postgres:16

The second fixture caught this in the compose reader, which reported a service eleven lines
before it was declared. The helm reader had the identical defect in its configmap keys and
kept it for another item, because a defect fixed outside the item that found it arrives
without a regression test.

**This is the shape of wrongness that survives review.** The number is plausible, the edge is
otherwise correct, and only someone opening the file and counting would ever know. Nothing
downstream can detect it, which is why the answer keys assert line numbers at all.
"""
from __future__ import annotations

import re


def key_line(text: str, key: str, start: int = 0) -> tuple[int, int]:
    """The 1-based line where `key:` is *declared*, plus an offset to resume scanning from.

    Anchored to the start of a line, after indentation and an optional list dash — which is
    what makes it a key rather than a coincidence inside a value.

    The returned offset lets a caller keep searching **forward**, so a key repeated under two
    blocks resolves to its own occurrence rather than to the first one for both. A caller that
    ignores it gets the first match, which is correct for a single lookup and wrong in a loop.

    Returns `(0, start)` when the key is not found: line 0 is the "no line" convention already
    used across the extractors, and the unchanged offset keeps a scanning loop from advancing
    on a miss.
    """
    m = re.compile(rf"^[ \t]*-?[ \t]*{re.escape(key)}[ \t]*:", re.M).search(text, start)
    if m is None:
        return 0, start
    return text.count("\n", 0, m.start()) + 1, m.end()
