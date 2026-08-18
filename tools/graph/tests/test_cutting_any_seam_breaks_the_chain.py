"""M0 KILL GATE, half one continued: cut any seam and the chain must break.

`test_golden_chain.py` says the chain resolves. It cannot say the chain is being *read* — a
golden test that still passes when the fixture is wrong is not reading the fixture, and
nothing in a passing golden run distinguishes the two. This file is what makes that claim
falsifiable.

## The honest problem this answers

The fixture was written by the same author as the extractors, so by default it proves only
that an extractor can parse its author's idea of the input. Every assertion in the golden
test could be satisfied by an extractor that ignored the files entirely and returned a
hard-coded chain. **These tests fail against exactly that extractor**, which is the property
the golden test cannot have on its own.

## What a mutation must be

Each one **renames a single value on a single line**, in a copy under `tmp_path` — never in
the committed fixture. Deleting a file would break the chain too, and prove almost nothing:
any pipeline notices a missing file. Renaming the value a seam resolves *through* leaves the
file, the syntax and the line count intact, so the only thing that can break the hop is the
extractor genuinely following that resolution.

Line counts are checked, not assumed. A mutation that shifted lines would move every
surviving hop's citation, and the "five others survive" assertion would silently stop
meaning anything.

## Two controls, because breakage alone proves nothing

A test asserting only "this mutation broke the chain" passes for an extractor so brittle that
*every* change breaks *everything*. So:

  * mutating the negative control must leave every other edge byte-identical;
  * rewriting a comment must change the graph not at all.

Without those, the suite would be measuring sensitivity and calling it precision.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.tests.chain import CHAIN, INERT_COMPONENT, present  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


# ── the harness ───────────────────────────────────────────────────────────────


def build_mutated(tmp_path: Path, rel: str = "", old: str = "", new: str = "",
                  drop_component: str = "", edits: list[tuple[str, str, str]] | None = None):
    """Copy the fixture, apply the edits, build. Returns (report, edges, unresolved).

    One shared implementation, in `_mutate`, where the honesty rules — the copy, the
    replace-EVERY-occurrence semantics, the line-count guard — live once with their
    reasons. Three suites cut fixtures now, and a private copy that quietly lost one of
    those rules would keep passing while measuring less.
    """
    from tools.graph.tests._mutate import build_mutated as impl
    return impl(FIXTURE, tmp_path, rel=rel, old=old, new=new,
                drop_component=drop_component, edits=edits)


API_PROPS = "orders-api/src/main/resources/application.properties"
WORKER_PROPS = "orders-worker/src/main/resources/application.properties"

#: One cut per hop, each severing a DIFFERENT resolution mechanism. Every entry renames a
#: value in place — never deletes a file, never changes a line count.
CUTS = [
    (1, "the central URL module's path literal",
     "web-console/src/config/urls.js", "'/orders/v1/list'", "'/orders/v1/listing'"),
    (2, "the context path the mapping is concatenated onto",
     API_PROPS, "server.servlet.context-path=/orders", "server.servlet.context-path=/orders2"),
    # Every declaration of the topic, not just the placeholder's default. D2 declares this
    # key three ways in this file, so it has three sources and cutting one cuts nothing —
    # which is precisely what the first version of this cut did, and what it took a failing
    # mutation test to notice. See `test_cutting_only_the_placeholder_default_is_not_a_cut`.
    (3, "every declaration of the topic the producer publishes to",
     API_PROPS, "order-events-v1", "order-events-v9"),
    (4, "the value the consumer's own property resolves to",
     WORKER_PROPS, "orders.consume.topic=order-events-v1", "orders.consume.topic=order-ev-v9"),
    (5, "the topic the write mapping is keyed on",
     "orders-worker/config/write-mapping.yml", "topic: order-events-v1",
     "topic: order-events-v9"),
    (6, "the table the migration creates",
     "orders-worker/src/main/resources/db/migration/V1__create_order_events.sql",
     "CREATE TABLE order_events (", "CREATE TABLE order_events_v9 ("),
]


# ── cut any seam, and the chain must break AT THAT SEAM ───────────────────────


@pytest.mark.parametrize("hop, what, rel, old, new", CUTS, ids=[f"hop{c[0]}" for c in CUTS])
def test_cutting_a_seam_breaks_its_own_hop(tmp_path: Path, hop, what, rel, old, new):
    _, edges, _ = build_mutated(tmp_path, rel, old, new)
    cut = next(h for h in CHAIN if h[0] == hop)
    assert not present(edges, cut), (
        f"hop {hop} survived a mutation of {what} in {rel}. The chain is not being read "
        f"through that seam — an extractor returning it from anywhere else would pass the "
        f"golden test identically")


@pytest.mark.parametrize("hop, what, rel, old, new", CUTS, ids=[f"hop{c[0]}" for c in CUTS])
def test_cutting_a_seam_breaks_only_its_own_hop(tmp_path: Path, hop, what, rel, old, new):
    """Breakage alone would be satisfied by an extractor so brittle that any edit destroys
    everything. Five of the six must survive, which is what makes the cut *specific*."""
    _, edges, _ = build_mutated(tmp_path, rel, old, new)
    survivors = [h[0] for h in CHAIN if present(edges, h)]
    assert survivors == [h[0] for h in CHAIN if h[0] != hop], (
        f"cutting hop {hop} disturbed other hops: survivors were {survivors}")


@pytest.mark.parametrize("hop, what, rel, old, new", CUTS, ids=[f"hop{c[0]}" for c in CUTS])
def test_a_cut_chain_still_builds(tmp_path: Path, hop, what, rel, old, new):
    """A cut seam is a *finding*, not a crash. The graph a developer gets from a broken
    workspace is the one they need most, and a pipeline that aborted would hand them
    nothing."""
    report, edges, _ = build_mutated(tmp_path, rel, old, new)
    assert report["status"] == "ok" and edges


def test_cutting_the_producer_leaves_a_topic_with_a_consumer_and_no_producer(tmp_path: Path):
    """What the graph SHOULD say after this cut, which is more than "an edge is missing": a
    topic that something reads and nothing writes. That is a real architectural finding, and
    the pipeline reports it rather than quietly renaming the topic away."""
    hop = next(c for c in CUTS if c[0] == 3)
    _, edges, unresolved = build_mutated(tmp_path, hop[2], hop[3], hop[4])
    assert not [e for e in edges
                if e["type"] == "DG_PRODUCES" and e["dst"] == "topic:order-events-v1"]
    assert [e for e in edges
            if e["type"] == "DG_CONSUMES" and e["dst"] == "topic:order-events-v1"]
    assert any(r["kind"] == "unproduced-topic" and r["topic"] == "topic:order-events-v1"
               for r in unresolved), "the orphaned mapping must be reported, not dropped"


def test_cutting_the_context_path_leaves_the_interface_calling_nothing(tmp_path: Path):
    """The failure this pair of hops exists to make visible. Move the prefix and the service
    serves `/orders2/v1/list` while the interface still calls `/orders/v1/list` — a call with
    no server, which is exactly what a graph is for and exactly what an extractor reading
    only the annotation would hide."""
    hop = next(c for c in CUTS if c[0] == 2)
    _, edges, _ = build_mutated(tmp_path, hop[2], hop[3], hop[4])
    assert [e for e in edges if e["type"] == "DG_CALLS" and e["dst"] == "ep:/orders/v1/list"]
    assert not [e for e in edges
                if e["type"] == "DG_EXPOSES" and e["dst"] == "ep:/orders/v1/list"]
    assert [e for e in edges
            if e["type"] == "DG_EXPOSES" and e["dst"] == "ep:/orders2/v1/list"]


def test_cutting_only_the_placeholder_default_is_not_a_cut(tmp_path: Path):
    """A surprise, pinned rather than smoothed over — and the reason hop 3's cut is broad.

    D2 declares this key three ways in one file, so its value has three independent sources.
    Changing the placeholder's default leaves the two literals, the key still resolves, and
    the hop stands. **Nothing is wrong with the extractor**: the graph is correct and its
    provenance still points at the requested spelling's line, which is what hop 3 cites.

    What is wrong is the *inference a narrow cut invites*. The first version of this file cut
    only line 5, saw the hop survive, and would have been read as "the extractor is not
    following the placeholder" — when the real fact is that the placeholder is not the only
    thing supplying the value. A mutation test is worth exactly as much as its cuts being
    load-bearing, and that is not self-evident from looking at them.
    """
    _, edges, _ = build_mutated(tmp_path, API_PROPS,
                                "${ORDER_TOPIC:order-events-v1}", "${ORDER_TOPIC:other-v9}")
    assert present(edges, next(h for h in CHAIN if h[0] == 3)), (
        "if this ever starts breaking, the fixture's D2 decoy has changed and hop 3's cut "
        "should be re-narrowed to the placeholder alone")


def test_dropping_every_default_makes_the_hop_unresolvable_not_wrong(tmp_path: Path):
    """A different failure mode from renaming, and it must be distinguishable: with nothing
    supplying a value the key cannot be resolved from the repository at all. The hop must
    vanish AND be reported — never resolved to the literal `${...}`, which would name a topic
    after a placeholder and look exactly like a real one."""
    _, edges, unresolved = build_mutated(tmp_path, edits=[
        (API_PROPS, "${ORDER_TOPIC:order-events-v1}", "${NOTHING_SUPPLIES_THIS}"),
        (API_PROPS, "ORDERS_PUBLISH_TOPIC=order-events-v1",
         "ORDERS_PUBLISH_TOPIC=${NOTHING_SUPPLIES_THIS}"),
        (API_PROPS, "orders-publish-topic=order-events-v1",
         "orders-publish-topic=${NOTHING_SUPPLIES_THIS}"),
    ])
    assert not present(edges, next(h for h in CHAIN if h[0] == 3))
    assert not [e for e in edges if "NOTHING_SUPPLIES_THIS" in e["dst"]], \
        "a placeholder must never become an entity name"
    assert any("NOTHING_SUPPLIES_THIS" in str(r.get("missing", "")) for r in unresolved)


def identity(edges: list[dict]) -> list[tuple[str, str, str]]:
    """What an edge IS, independent of how it was written: type, source, target."""
    return sorted((e["type"], e["src"], e["dst"]) for e in edges)


def test_renaming_a_path_variable_changes_no_edge_identity(tmp_path: Path):
    """The counterpart to D1, stated as a mutation: `{orderId}` and `{id}` are one endpoint,
    so renaming the variable must not change what any edge connects. An extractor keying on
    the literal would split the endpoint here and the golden test would never notice.

    Compared on identity rather than on the whole record, deliberately: the literal that was
    written DOES change, and recording it is correct provenance. Demanding byte-identical
    edges would fail on the extractor being more honest, not less.
    """
    before = build_mutated(tmp_path / "a")[1]
    after = build_mutated(tmp_path / "b", "web-console/src/config/urls.js",
                          "{orderId}", "{customerOrderId}")[1]
    assert identity(after) == identity(before)


# ── the controls: breakage must be specific, not universal ────────────────────


def test_mutating_the_negative_control_changes_nothing_else(tmp_path: Path):
    """P5. If editing an inert component perturbs anything between the others, the extractor
    is inventing links from proximity — the failure that makes a graph plausible and useless,
    because every query returns something and nothing returned is trustworthy."""
    before = build_mutated(tmp_path / "a")[1]
    after = build_mutated(tmp_path / "b",
                          f"{INERT_COMPONENT}/src/main/resources/application.properties",
                          "reports.retention.days=30", "reports.retention.days=90")[1]
    unrelated = [e for e in after if INERT_COMPONENT not in e["src"]
                 and INERT_COMPONENT not in e["dst"]]
    baseline = [e for e in before if INERT_COMPONENT not in e["src"]
                and INERT_COMPONENT not in e["dst"]]
    assert unrelated == baseline


def test_deleting_the_negative_control_leaves_every_other_edge_byte_identical(tmp_path: Path):
    """`EXPECTED.md` P5 in its own words: deleting it must change the node count and leave
    every edge between other components byte-identical."""
    before_report, before, _ = build_mutated(tmp_path / "a")
    after_report, after, _ = build_mutated(tmp_path / "b", drop_component=INERT_COMPONENT)

    assert after_report["totals"]["nodes"] < before_report["totals"]["nodes"]
    kept = [e for e in before
            if INERT_COMPONENT not in e["src"] and INERT_COMPONENT not in e["dst"]]
    assert after == kept


def test_rewriting_a_comment_changes_the_graph_not_at_all(tmp_path: Path):
    """Only literals are read. A path in a comment is not a claim about behaviour, and the
    fixture names one deliberately — read as text, the test source mentions it twice."""
    before = build_mutated(tmp_path / "a")[1]
    after = build_mutated(
        tmp_path / "b",
        "orders-api/src/test/java/com/example/orderflow/api/OrderControllerTest.java",
        "in a mock stub. Neither is a real caller.",
        "in a mock stub. Neither of them calls it.")[1]
    assert after == before


def test_the_unmutated_copy_reproduces_the_committed_build(tmp_path: Path):
    """The harness's own control. If copying the fixture changed the graph, every result in
    this file would be measuring the copy rather than the mutation."""
    from tools.graph.tests.chain import CHAIN as C

    _, edges, unresolved = build_mutated(tmp_path)
    assert [h[0] for h in C if present(edges, h)] == [h[0] for h in C]
    assert len(unresolved) == 4
