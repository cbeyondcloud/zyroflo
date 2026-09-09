"""Item 149: telling a document's PROSE from the examples it shows.

`adr.change_set_lines` knew headings and list items, `defect.named_tests` knew a
declaring line, and neither knew a code fence — so a fenced sample was read as
if its author had written it as a statement about this run. Measured: a `diff`
sample inside a change set has `-` and `+` lines that both match a markdown list
item, so its backticked tokens became declared paths; a fence merely SHOWING the
P3 SOP's `**Change set:**` template OPENED a section, and a document that
declared nothing yielded two paths; and a reproduction quoting the P4 SOP's own
template obliged on a test nobody wrote. The SOPs teach both shapes inside
fences (`sdlc/stages/P3-design.md`, `sdlc/stages/P4-implementation.md`), so the
readers were refusing the documents their own procedures teach.

**A fenced block is DATA.** It is what a document SHOWS, not what it SAYS, and
neither reader may take a declaration out of one. Item 100 priced the opposite
mistake: the only way past a false obligation is to strip backticks off
legitimate cross-references, which makes documents worse.

**The rule has ONE home.** Item 115: these two readers have drifted apart three
times, and a rule copied into both would be the fourth. Both import this.

Spellings handled, because a document is written by a person: three or more
backticks or tildes, an info string after the opener, a closing run at least as
long as the opener's and of the same character, and an indent — the shipped
SOPs' 158 fences are 118 at column 0 and 40 indented three spaces inside a list
item, so a rule that only knew column 0 would miss a quarter of them.

**A four-space indented code block is not read as code**, deliberately: four
spaces is also how a list item's continuation paragraph is written, so treating
it as code would delete the per-component change set the P3 SOP teaches. The
shipped SOPs use none.
"""
from __future__ import annotations

import re

#: A fence marker: three or more backticks or tildes, optionally indented, with
#: whatever info string follows captured — a CLOSING marker carries none.
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*(\S*)")


def unfenced(text: str) -> list[str]:
    """The document's lines with every fenced block removed, markers included.

    **An unclosed fence is not a fence.** A fence is defined by its pair; a
    marker that is never closed is dropped on its own and the lines after it
    are read as prose. A lone marker is a typo far more often than an intent to
    hide the rest of a document, and the two directions are not symmetric here:
    reading too much produces a finding a human sees and dismisses, while
    reading too little deletes a real change set and returns *a clean bill of
    health from a check that looked at nothing*. No single stray line may
    silence a document — and the scan resumes at the next line, so a real pair
    further down is still read as data.
    """
    lines = text.splitlines()
    kept: list[str] = []
    i = 0
    while i < len(lines):
        opener = _FENCE.match(lines[i])
        if not opener:
            kept.append(lines[i])
            i += 1
            continue
        char, run = opener.group(1)[0], len(opener.group(1))
        close = next(
            (j for j in range(i + 1, len(lines))
             if _closes(lines[j], char, run)), None)
        # The whole span is data; an unclosed marker costs only its own line.
        i = i + 1 if close is None else close + 1
    return kept


def _closes(line: str, char: str, run: int) -> bool:
    """Whether `line` closes a fence opened with `run` of `char`.

    Same character, a run at least as long, and no info string — a tilde never
    closes a backtick fence and a shorter run never closes a longer one.
    """
    marker = _FENCE.match(line)
    return bool(marker
                and marker.group(1)[0] == char
                and len(marker.group(1)) >= run
                and not marker.group(2))
