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


#: What a Go-template span becomes so the YAML around it can parse. Distinctive enough
#: that no real configuration value contains it, so a reader can refuse to believe any
#: value it appears in.
TOKEN = "__TPL__"

_SPAN = re.compile(r"\{\{.*?\}\}")


def detemplate(text: str) -> str:
    """Go-templated YAML → parseable YAML, with the LINE COUNT untouched.

    A chart's `templates/*.yaml` are YAML skeletons carrying `{{ ... }}` spans in scalar
    positions and whole-line control constructs between mappings. Rendering them needs a
    template engine, a values context and a release name — none of which a static reader
    has — but parsing them only needs the spans out of the way:

    * an inline span becomes `TOKEN`, so `name: {{ template "x" . }}` parses and the
      reader can see the value is not a literal;
    * a line that is ONLY template machinery (`{{- if }}`, `{{- end }}`, a span left
      dangling across lines) becomes a comment of itself;

    and every substitution happens WITHIN its line, never across one — the whole point
    is that `key_line` citations against the original text stay true.
    """
    out = []
    in_span = False
    for line in text.splitlines():
        if in_span:
            out.append("# " + line)
            in_span = "}}" not in line
            continue
        replaced = _SPAN.sub(TOKEN, line)
        if "{{" in replaced:                      # a span opened and never closed here
            out.append("# " + line)
            in_span = True
        elif replaced.strip().startswith(TOKEN) and not replaced.strip()[len(TOKEN):].strip(" -"):
            out.append("# " + line)               # the line IS the construct
        else:
            out.append(replaced)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")
