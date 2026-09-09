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


# ── an UNCLOSED fence is not a fence ────────────────────────────────────────
#
# THE RULING: a fence is defined by its pair. A marker that is never closed is
# dropped - it is a marker, never prose that declares anything - and the lines
# after it are read as prose. A lone marker is a typo far more often than an
# intent to hide the rest of a document, and the two directions are not
# symmetric: reading too much produces a finding a human sees and dismisses,
# while reading too little deletes a real change set and returns "a clean bill
# of health from a check that looked at nothing" (`adr.py`'s own words). No
# single stray line may silence a document.


def test_an_unclosed_fence_does_not_swallow_the_rest_of_the_document():
    text = (
        "## Change set\n\n"
        "- `src/main/App.java`\n\n"
        "```\n"
        "- `after/TheStrayMarker.java`\n"
    )
    assert adr.named_paths(text) == [
        "src/main/App.java", "after/TheStrayMarker.java"]


def test_an_unclosed_fence_leaves_a_declaration_readable():
    text = "## Reproduction\n\n~~~\nRegression test: `after_the_marker.py`\n"
    assert defect.named_tests(text) == ["after_the_marker.py"]


def test_a_stray_marker_does_not_consume_the_pair_that_follows_it():
    """The scan resumes at the stray marker's next line, so a real pair later
    in the document is still read as data. The stray marker is a tilde, which
    the backtick pair below it cannot close."""
    text = (
        "## Change set\n\n"
        "~~~\n"
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
    assert read(f"```\n{declaration}\n") != [], "an unclosed fence is not a fence"


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
