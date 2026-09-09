"""Item 149: a fenced block is an EXAMPLE, not prose that declares anything.

`adr.change_set_lines` knew headings and list items, and `defect.named_tests`
knew a declaring line; neither knew a code fence. So the SOPs' own teaching
examples were read as declarations by the very readers those SOPs teach:
`sdlc/stages/P3-design.md` shows a change set inside a fence and
`sdlc/stages/P4-implementation.md` shows `Regression test: ...` inside one.
Item 123's rule — a procedure that teaches a shape its own reader refuses fails
by name — so the SOP files themselves are the fixtures here.

Item 115: the two readers have drifted apart three times, so the fence rule has
ONE home (`tools/sdlc/prose.py`) and the table below is run through BOTH — a
rule applied in one reader only turns this file red.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[2]
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from tools.sdlc import adr, defect  # noqa: E402

P3 = HARNESS / "sdlc" / "stages" / "P3-design.md"
P4 = HARNESS / "sdlc" / "stages" / "P4-implementation.md"


# ── the SOPs' own text, read by the readers those SOPs teach ────────────────


def test_the_p3_sops_own_fenced_example_is_not_a_change_set():
    """P3 shows `## Change set - orders-api` and two list items inside a fence
    to teach the shape. Read as a declaration, the SOP declares files nobody
    intends to change."""
    text = P3.read_text(encoding="utf-8")
    assert "```" in text, "the fixture must actually contain a fence"
    paths = adr.named_paths(text)
    inside = [p for p in paths if "ShortenController" in p]
    assert inside == [], (
        f"the SOP's teaching example was read as a declaration: {inside}")


def test_the_p4_sops_own_fenced_example_obliges_nobody():
    """P4 shows `Regression test: `testGetObjectMapper`` inside a fence."""
    text = P4.read_text(encoding="utf-8")
    assert "```" in text, "the fixture must actually contain a fence"
    assert "testGetObjectMapper" not in defect.named_tests(text), (
        "the SOP's teaching example was read as an obligation")


# ── the three shapes W63 measured, spelled as a design or a repro would ─────


DIFF_SAMPLE = (
    "# Design\n\n"
    "## Change set\n\n"
    "- `src/main/App.java`\n\n"
    "The migration looked like this in the spike:\n\n"
    "```diff\n"
    "- `legacy/OldThing.java`\n"
    "+ `spike/scratch/NewThing.java`\n"
    "```\n\n"
    "Prose after the fence.\n"
)

QUOTED_TEMPLATE = (
    "# Design\n\n"
    "The P3 SOP asks every design to write:\n\n"
    "```markdown\n"
    "**Change set:** `path/to/file.py`\n"
    "```\n\n"
    "- `docs/only/prose.md`\n"
)

QUOTED_REPRO_TEMPLATE = (
    "## Reproduction\n\n"
    "The SOP template is:\n\n"
    "```markdown\n"
    "Regression test: `test_example_from_the_sop.py`\n"
    "```\n\n"
    "We have not written ours yet.\n"
)


def test_a_diff_sample_inside_a_change_set_declares_no_paths():
    """A `diff` sample's `-` and `+` lines both match `_LIST_ITEM`, so every
    backticked token in it became a declared path."""
    assert adr.named_paths(DIFF_SAMPLE) == ["src/main/App.java"]


def test_a_fenced_template_opens_no_change_set():
    """The fenced `**Change set:**` line opened a section, and the section then
    swallowed the ordinary list item after the fence."""
    assert adr.change_set_lines(QUOTED_TEMPLATE) == []
    assert adr.named_paths(QUOTED_TEMPLATE) == []


def test_a_fenced_repro_template_obliges_nothing():
    assert defect.named_tests(QUOTED_REPRO_TEMPLATE) == []


# ── the controls: outside a fence, nothing whatever has changed ─────────────


def test_the_same_tokens_outside_a_fence_are_read_exactly_as_before():
    """Item 123's and item 141's narrowings are untouched: the list items of a
    declared change set are still the change set."""
    prose = (
        "# Design\n\n"
        "## Change set\n\n"
        "- `src/main/App.java`\n"
        "- `legacy/OldThing.java`\n\n"
        "Prose in the section is prose, and cites `10-requirements.md`.\n\n"
        "### orders-api\n\n"
        "- `spike/scratch/NewThing.java`\n"
    )
    assert adr.named_paths(prose) == [
        "src/main/App.java", "legacy/OldThing.java",
        "spike/scratch/NewThing.java"]
    repro = "## Reproduction\n\nRegression test: `test_example_from_the_sop.py`\n"
    assert defect.named_tests(repro) == ["test_example_from_the_sop.py"]


# ── the fence spellings that actually occur ─────────────────────────────────
#
# Measured over the 19 shipped SOP/template markdown files: 158 fences, every
# one a three-backtick run, 118 at column 0 and 40 indented three spaces inside
# a list item - which is why an indent is allowed. No four-space indented code
# blocks are used as code in those documents, and they stay out of scope: four
# spaces is also how a list item's continuation paragraph is written, so
# reading it as code would delete the per-component change set the P3 SOP
# teaches. Tildes, longer runs and info strings are handled because a document
# is written by a person, not by this repository.

SPELLINGS = [
    pytest.param("```", "```", id="three-backticks"),
    pytest.param("~~~", "~~~", id="three-tildes"),
    pytest.param("````", "````", id="four-backticks"),
    pytest.param("```yaml", "```", id="info-string"),
    pytest.param("~~~text", "~~~", id="tilde-info-string"),
    pytest.param("```", "``````", id="longer-close-than-open"),
    pytest.param("   ```", "   ```", id="indented-three-as-the-sops-write-it"),
]


def _design(open_marker: str, close_marker: str) -> str:
    return (
        "## Change set\n\n"
        "- `src/main/App.java`\n\n"
        f"{open_marker}\n"
        "- `inside/TheFence.java`\n"
        f"{close_marker}\n"
    )


def _repro(open_marker: str, close_marker: str) -> str:
    return (
        "## Reproduction\n\n"
        f"{open_marker}\n"
        "Regression test: `inside_the_fence.py`\n"
        f"{close_marker}\n"
    )


@pytest.mark.parametrize("open_marker,close_marker", SPELLINGS)
def test_adr_ignores_every_fence_spelling(open_marker, close_marker):
    assert adr.named_paths(_design(open_marker, close_marker)) == [
        "src/main/App.java"]


@pytest.mark.parametrize("open_marker,close_marker", SPELLINGS)
def test_defect_ignores_every_fence_spelling(open_marker, close_marker):
    assert defect.named_tests(_repro(open_marker, close_marker)) == []


def test_a_tilde_does_not_close_a_backtick_fence():
    text = ("## Change set\n\n```\n~~~\n- `inside/TheFence.java`\n```\n"
            "- `after/TheFence.java`\n")
    assert adr.named_paths(text) == ["after/TheFence.java"]


def test_a_shorter_run_does_not_close_a_longer_fence():
    text = ("## Change set\n\n````\n```\n- `inside/TheFence.java`\n````\n"
            "- `after/TheFence.java`\n")
    assert adr.named_paths(text) == ["after/TheFence.java"]


# ── an UNCLOSED fence runs to the end of the document ───────────────────────
#
# THE RULING, REVERSED. Item 149 first ruled the other way: a lone marker was
# dropped and the lines after it read on as prose, on the argument that no
# single stray line may silence a document. The reviewer then MEASURED what
# that costs, and the cases below are that measurement, kept and inverted.
#
# Every one of the 158 fences these documents ship is a backtick run, so a
# stray marker is the same character as every real one and pairs with the NEXT
# REAL OPENER. Under the old rule that INVERTED the reading: `adr.named_paths`
# returned the paths from inside the example and lost the real ones, and
# `defect.named_tests` returned `only_an_example.py` while losing the real
# `a_real_one.py`. One stray backtick made a false obligation AND a missed one,
# and the answer was plausible.
#
# Running to the end is the LOUD direction: each reader then reports LESS, and
# neither is silent when it finds nothing - see the two loudness tests in
# `test_adr.py` and `test_defect_flow.py`, which drive the real obligations
# through a swallowed document. It also agrees with every markdown renderer the
# author is looking at.


def test_an_unclosed_fence_swallows_the_rest_of_the_document():
    """Was `..._does_not_swallow_...` and asserted both paths. It reversed
    because the reviewer measured the cost of reading on: what follows a stray
    marker is exactly what a renderer paints as code, and the declaration
    LOST here is loud - an empty change set is item 64's own finding."""
    text = (
        "## Change set\n\n"
        "- `src/main/App.java`\n\n"
        "```\n"
        "- `after/TheStrayMarker.java`\n"
    )
    assert adr.named_paths(text) == ["src/main/App.java"]


def test_an_unclosed_fence_hides_the_declaration_after_it():
    """Was `..._leaves_a_declaration_readable`. It reversed for the same
    measurement: a declaration a renderer shows as code is an example, and a
    reproduction that ends up naming no test is refused by name, not passed."""
    text = "## Reproduction\n\n~~~\nRegression test: `after_the_marker.py`\n"
    assert defect.named_tests(text) == []


def test_a_stray_marker_shifts_every_pairing_after_it():
    """Was `..._does_not_consume_the_pair_that_follows_it`, and it used a TILDE
    stray so the backtick pair below could not close it - a spelling that never
    occurs in these documents. The reviewer used the spelling that does, and
    measured what it costs: a stray backtick pairs with the next REAL opener,
    so the author's example becomes prose and the author's prose becomes code.
    That shift happens under either ruling and this reader cannot undo it.
    What the reversal changes is the TAIL: the trailing marker now runs to the
    end, so `after/ThePair.java` is no longer named. The answer left is exactly
    what a markdown renderer paints as prose for this document, which is the
    second reason for the reversal - the author can SEE what the reader read.
    Under the old rule it was `['inside/APair.java', 'after/ThePair.java']`."""
    text = (
        "## Change set\n\n"
        "```\n"
        "- `after/TheStrayMarker.java`\n"
        "```\n"
        "- `inside/APair.java`\n"
        "```\n"
        "- `after/ThePair.java`\n"
    )
    assert adr.named_paths(text) == ["inside/APair.java"]


def test_a_stray_marker_that_has_no_partner_at_all_names_nothing():
    """The pure case, and the one the reversal is FOR: a single stray marker
    with no other marker anywhere. Under the old rule the reader read straight
    past it and named the example; now it runs to the end and names nothing,
    which item 64's finding then reports. The test above is the mixed case,
    where a stray has real markers after it to pair with."""
    text = (
        "## Change set\n\n"
        "```\n"
        "- `only/AnExample.java`\n"
        "- `also/OnlyAnExample.java`\n"
    )
    assert adr.named_paths(text) == []


def test_the_reproduction_the_reviewer_measured_reads_as_the_renderer_does():
    """The reviewer's second measurement, kept verbatim as the record of it.
    `only_an_example.py` is named and the real `a_real_one.py` is lost - and
    that is TRUE of the rendered document too, because the author's stray
    marker really did move where the code block starts. The reversal does not
    claim to repair a document like this one; it makes the reader agree with
    what the author sees, so the typo is visible instead of being a
    disagreement between two readings nobody can compare."""
    text = (
        "## Reproduction\n\n"
        "```\n"
        "Regression test: `a_real_one.py`\n"
        "```\n"
        "Regression test: `only_an_example.py`\n"
        "```\n"
        "tail\n"
    )
    assert defect.named_tests(text) == ["only_an_example.py"]
    closed = text.replace("```\ntail\n", "```\ntail\n```\n")
    assert defect.named_tests(closed) == ["only_an_example.py"], (
        "the control: closing the trailing marker changes nothing here, so "
        "this row is about the SHIFTED pairing and not about the tail rule")


def test_a_closed_fence_is_still_read_as_data_after_the_stray_rule():
    """The positive control for the three above: with the stray marker gone,
    the very same document reads exactly as it always did, so the tests above
    are pinning the stray rule and not an empty reader."""
    text = (
        "## Change set\n\n"
        "- `after/TheStrayMarker.java`\n"
        "```\n"
        "- `inside/APair.java`\n"
        "```\n"
        "- `after/ThePair.java`\n"
    )
    assert adr.named_paths(text) == [
        "after/TheStrayMarker.java", "after/ThePair.java"]


# ── the weld (item 115) ─────────────────────────────────────────────────────
#
# These two readers have drifted apart three times. The rule lives in one
# module and BOTH call it; a change applied to one reader only turns the
# parametrised pair above red, and the source check below turns red if either
# module grows a fence rule of its own.


READERS = [
    pytest.param(
        lambda body: adr.named_paths("## Change set\n\n" + body),
        "- `x/Y.java`", id="adr.named_paths"),
    pytest.param(
        lambda body: defect.named_tests("## Reproduction\n\n" + body),
        "Regression test: `x_y.py`", id="defect.named_tests"),
]


@pytest.mark.parametrize("read,declaration", READERS)
def test_both_readers_answer_a_fence_the_same_way(read, declaration):
    """One table, both readers: whatever a fence means, it means it twice."""
    assert read(f"```\n{declaration}\n```\n") == []
    assert read(f"~~~\n{declaration}\n~~~\n") == []
    assert read(declaration + "\n") != []
    assert read(f"```\n{declaration}\n") == [], (
        "an unclosed fence runs to the end - and it must run to the end in "
        "BOTH readers, or a stray marker means two different things")


@pytest.mark.parametrize("module", [adr, defect], ids=["adr", "defect"])
def test_neither_reader_carries_its_own_copy_of_the_fence_rule(module):
    """Item 115's weld at the source: one home, not two copies that drift."""
    source = Path(module.__file__).read_text(encoding="utf-8")
    body = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert "{3,}" not in body, (
        f"{Path(module.__file__).name} spells a fence run of its own - the rule "
        "belongs in tools/sdlc/prose.py")
    assert "from tools.sdlc import prose" in body, (
        f"{Path(module.__file__).name} must read its prose through the shared "
        "module, or the two readers drift apart a fourth time")
