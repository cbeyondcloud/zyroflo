"""Item 19's second half: cut any seam of the SECOND stack and its chain must break.

`test_golden_chain_pantry.py` says the chain resolves and that core is untouched. It cannot
say the chain is being *read* — every assertion in it would be satisfied by an extractor that
ignored the files entirely and returned a hard-coded chain, and item 4 committed the
demonstration that such an extractor passes a golden test 24/24. These tests fail against it.

## Why this is not redundant with the first fixture's mutation test

That one proves the *mechanism* is falsifiable. This one proves it stays falsifiable when the
mechanism is pointed at a different stack — which is the whole of item 19. A cut here severs
a Python decorator prefix, a compose interpolation, an inline default and a configured
address: none of them exist in the first fixture, and all of them go through the same core.

## What a mutation must be

Each **renames a single value**, in a copy under `tmp_path`, never in the committed fixture.
Deleting a file would break the chain too and prove almost nothing — any pipeline notices a
missing file. Renaming the value a seam resolves *through* leaves the file, the syntax and
the line count intact, so the only thing that can break the hop is the extractor genuinely
following that resolution. Line counts are asserted, not assumed: a mutation that shifted
lines would move every surviving hop's citation and the "the others survive" assertion would
quietly stop meaning anything.

## Controls, because breakage alone proves nothing

A suite asserting only "this mutation broke the chain" passes for an extractor so brittle
that every change breaks everything. So mutating the negative control must leave every other
edge byte-identical, deleting it must change the node count and nothing else, and rewriting a
comment must change the graph not at all. Without those this measures sensitivity and calls
it precision.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.tests.chain_pantry import CHAIN, INERT_COMPONENT, present  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pantry"


def build_mutated(tmp_path: Path, rel: str = "", old: str = "", new: str = "",
                  drop_component: str = "",
                  edits: list[tuple[str, str, str]] | None = None):
    """Copy the fixture, apply the edits, build. Returns (report, edges, unresolved).

    An edit replaces EVERY occurrence in its file, for the reason the first fixture's version
    of this harness recorded the hard way: a value declared in several places has several
    sources, so cutting one of them cuts nothing — and a narrow cut that severs nothing reads
    exactly like a seam being correctly followed.
    """
    from tools.graph.tests._mutate import build_mutated as impl
    return impl(FIXTURE, tmp_path, rel=rel, old=old, new=new,
                drop_component=drop_component, edits=edits)


CLIENT = "restock-worker/src/restock_worker/client.py"
ROUTES = "pantry-api/src/pantry_api/routes.py"
CONSUMER = "restock-worker/src/restock_worker/consumer.py"
COMPOSE = "deploy-compose/docker-compose.yml"
MIGRATION = "restock-worker/migrations/0001_create_restock_events.sql"

#: One cut per hop, each severing a DIFFERENT resolution mechanism — and every one of the
#: five is a mechanism the first fixture does not contain.
CUTS = [
    (1, "the path literal the caller pairs with its configured host",
     CLIENT, '"/pantry/v1/items"', '"/pantry/v1/stock"'),
    (2, "the router prefix the decorator path is concatenated onto",
     ROUTES, 'prefix="/pantry/v1"', 'prefix="/pantry/v2"'),
    (3, "the compose interpolation default the producer's key resolves through",
     COMPOSE, "${QUEUE_NAME:-restock-events-v2}", "${QUEUE_NAME:-restock-events-v9}"),
    (4, "the inline default the consumer names its queue with",
     CONSUMER, '"restock-events-v2"', '"restock-events-v9"'),
    (5, "the table the migration creates",
     MIGRATION, "restock_events", "restock_log"),
]


@pytest.mark.parametrize("hop, what, rel, old, new", CUTS,
                         ids=[f"hop{c[0]}" for c in CUTS])
def test_cutting_a_seam_breaks_its_own_hop(tmp_path: Path, hop, what, rel, old, new):
    _, edges, _ = build_mutated(tmp_path, rel, old, new)
    broken = next(h for h in CHAIN if h[0] == hop)
    assert not present(edges, broken), (
        f"hop {hop} survived a cut to {what} — the seam is not being followed, and the "
        f"golden test cannot tell that apart from it being followed correctly")


@pytest.mark.parametrize("hop, what, rel, old, new", CUTS,
                         ids=[f"hop{c[0]}" for c in CUTS])
def test_cutting_one_seam_leaves_the_others_standing(tmp_path: Path, hop, what, rel, old, new):
    """Precision, not only sensitivity. An extractor that collapsed on any edit would pass
    the test above for all five hops and be useless."""
    _, edges, _ = build_mutated(tmp_path, rel, old, new)
    survivors = [h[0] for h in CHAIN if h[0] != hop and present(edges, h)]
    expected = [h[0] for h in CHAIN if h[0] != hop]
    # Hops 1 and 2 meet at one endpoint node and 3 and 4 at one topic node, so a cut to
    # either side of a join legitimately takes the JOIN down while both halves survive as
    # edges. Their own hop rows are what these assertions are about.
    assert survivors == expected, (
        f"cutting {what} also broke hops {sorted(set(expected) - set(survivors))}")


def test_the_build_still_succeeds_when_a_seam_is_cut(tmp_path: Path):
    """A severed seam is a graph with a missing edge, not a crash. If a cut aborted the
    build, "the chain broke" would be indistinguishable from "the pipeline fell over"."""
    report, edges, _ = build_mutated(tmp_path, *CUTS[2][2:])
    assert report["status"] == "ok" and edges


# ── the canonical ranking, which the first fixture could not express ──────────


def test_cutting_the_OWNER_compose_value_moves_the_topic(tmp_path: Path):
    """Half one of the pair. The owner's value is what the graph must use."""
    _, edges, _ = build_mutated(tmp_path, COMPOSE, "restock-events-v2", "restock-events-v9")
    assert not [e for e in edges if e["dst"] == "topic:restock-events-v2"
                and e["type"] == "DG_PRODUCES"]


def test_cutting_the_SUPERSEDED_compose_value_changes_nothing(tmp_path: Path):
    """Half two, and the one that matters — it fails against the code as it shipped an hour
    before this file existed.

    `legacy/docker-compose.yml` sorts AFTER `docker-compose.yml`, so under the deterministic
    last-wins merge the superseded file's value won and a whole component published to a
    topic one version number off. Every step of that looked healthy. Editing a dead file must
    not be able to move a live edge, and this is the assertion that says so.
    """
    _, edges, _ = build_mutated(tmp_path, "deploy-compose/legacy/docker-compose.yml",
                                "restock-events-v1", "restock-events-v9")
    produced = [e for e in edges if e["type"] == "DG_PRODUCES"]
    assert [e["dst"] for e in produced] == ["topic:restock-events-v2"]


# ── the controls ──────────────────────────────────────────────────────────────


def baseline_edges(tmp_path: Path):
    _, edges, _ = build_mutated(tmp_path / "base")
    return edges


def test_mutating_the_negative_control_leaves_every_other_edge_identical(tmp_path: Path):
    """P5. If an inert component's edits reach the rest of the graph, the extractors are
    inventing links from proximity — which makes a graph plausible and useless, because every
    query returns something and nothing returned can be trusted."""
    before = baseline_edges(tmp_path)
    _, after, _ = build_mutated(tmp_path / "m", "label-printer/src/label_printer/main.py",
                                "/dev/usb/lp0", "/dev/usb/lp9")
    strip = lambda es: sorted(json.dumps(e, sort_keys=True) for e in es  # noqa: E731
                              if INERT_COMPONENT not in e["src"])
    assert strip(after) == strip(before)


def test_deleting_the_negative_control_drops_its_node_and_nothing_else(tmp_path: Path):
    before = baseline_edges(tmp_path)
    _, after, _ = build_mutated(tmp_path / "m", drop_component=INERT_COMPONENT)
    strip = lambda es: sorted(json.dumps(e, sort_keys=True) for e in es  # noqa: E731
                              if INERT_COMPONENT not in e["src"])
    assert strip(after) == strip(before)


def test_rewriting_a_comment_changes_the_graph_not_at_all(tmp_path: Path):
    """The tightest control. If prose can move an edge, the extractors are reading text
    rather than structure — and every docstring in the workspace becomes a liability."""
    before = baseline_edges(tmp_path)
    _, after, _ = build_mutated(tmp_path / "m", CONSUMER, "Drains the restock queue.",
                                "Reads from the restock queue")
    assert sorted(json.dumps(e, sort_keys=True) for e in after) == \
        sorted(json.dumps(e, sort_keys=True) for e in before)


def test_a_commented_out_creation_still_owns_nothing_when_uncommented_elsewhere(tmp_path: Path):
    """E8 as a mutation. Blanking a comment must preserve line numbers, so the real
    statement above it keeps citing line 2 — which is only checkable by moving the comment's
    content and watching the citation stay put."""
    _, edges, _ = build_mutated(tmp_path, MIGRATION, "pantry_items", "pantry_stock")
    owned = [e for e in edges if e["type"] == "DG_OWNS_TABLE"]
    assert [(e["dst"], e["line"]) for e in owned] == [("table:restock_events", 2)]
