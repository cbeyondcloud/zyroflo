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
#:
#: `^\s*` accepts ANY indent, including the eight spaces CommonMark would read
#: as an indented code block containing a literal marker. That is deliberate
#: and it is the same decision as the module docstring's: four-space blocks are
#: out of scope here, so there is no indented-code state to be inside, and a
#: marker at any depth is read as a marker. The shipped SOPs indent fences
#: three spaces inside list items and never four; nothing measured needs the
#: stricter rule, and it does not get written until a shipped document does.
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*(\S*)")


def unfenced(text: str) -> list[str]:
    """The document's lines with every fenced block removed, markers included.

    **An unclosed fence runs to the end of the document**, as CommonMark says.
    This reverses item 149's first ruling, which dropped a lone marker and read
    on; the reviewer measured what that cost. Every one of the 158 fences these
    documents ship is a backtick run, so a stray marker is the same character
    as every real one and pairs with the NEXT REAL OPENER — which INVERTS the
    reading: `adr.named_paths` returned the paths from inside the example and
    lost the real ones, `defect.named_tests` returned `only_an_example.py` and
    lost `a_real_one.py`. One stray backtick produced a false obligation AND a
    missed one, and the answer looked plausible.

    Running to the end is the LOUD direction. A stray opener makes each reader
    report LESS, and neither reader is silent when it finds nothing: `adr`
    treats a design naming no file as a finding in its own right (item 64, *a
    clean bill of health from a check that looked at nothing*) and `defect`
    still refuses with *names no regression test*. So the document gets fixed.
    Under the old rule both readers answered something plausible and wrong,
    which this project ranks as the worst outcome there is. It also agrees with
    every markdown renderer the author is looking at, so what this reader calls
    code and what the human sees as code are the same thing.
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
        # The whole span is data — to the closer, or to the end of the document
        # when there is none.
        i = len(lines) if close is None else close + 1
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
