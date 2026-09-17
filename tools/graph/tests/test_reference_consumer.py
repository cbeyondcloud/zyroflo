"""The bundle, read by something outside the harness. Item 54.

`export_okf.py` claims a bundle is usable with no Neo4j, no MCP, no harness and no
network. `reference_consumer.py` is that claim as a program, and this file checks
it against a REAL exported bundle rather than a hand-written one — a consumer
tested only on fixtures its own author wrote is a consumer tested against its own
assumptions.

**What is proven here, and what is not.** That the FORMAT is consumable: the
frontmatter parses, the links resolve, the provenance survives the projection, and
a blast radius computed from markdown alone matches what the graph itself says.
Not that an agent consumed it — there is no model here. A green run is a **green
FORMAT, never a green agent**, the same distinction `RUNNER_CONTRACT` §8 draws for
the reference runner. M6's C1 asks for the second, and this is honestly the first.

**The consumer must not import the harness**, which `test_cli.py` now enforces by
sweeping for the `OUTSIDE_PROCESS` declaration rather than by a roster somebody has
to remember.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.graph import build as B  # noqa: E402
from tools.graph import export_okf as X  # noqa: E402
from tools.graph import reference_consumer as C  # noqa: E402
from tools.graph.impact import load_graph  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONSUMER = ROOT / "tools" / "graph" / "reference_consumer.py"


@pytest.fixture(scope="module")
def exported(tmp_path_factory) -> tuple[Path, Path]:
    """A real build, really exported. `(graph_dir, bundle_dir)`."""
    base = tmp_path_factory.mktemp("consume")
    graph, bundle = base / "graph", base / "bundle"
    B.build(FIXTURES / "pantry" / "workspace.toml", graph, run_id="consume")
    X.export(graph, bundle)
    return graph, bundle


# ── the format is readable without the harness ──────────────────────────────


def test_the_consumer_reads_every_node_the_exporter_wrote(exported):
    graph, bundle = exported
    nodes = C.load(bundle)
    pages = list((bundle / C.NODES_DIR).glob("*.md"))
    assert len(nodes) == len(pages) > 0


def test_what_it_reads_matches_the_graph_it_came_from(exported):
    """**The claim that matters.** A bundle that parsed cleanly but described a
    different graph would pass every structural check and be worthless."""
    graph, bundle = exported
    raw_nodes, _ = load_graph(graph)
    from_graph = {n["id"] for n in raw_nodes}
    from_bundle = set(C.load(bundle))
    # The bundle also carries stubs, which the graph's node rows do not.
    assert from_graph <= from_bundle
    assert from_bundle - from_graph == set(), "the pantry fixture has no stubs"


def test_a_blast_radius_from_markdown_matches_the_graphs_own_edges(exported):
    """Computed by following links in files, checked against the edge rows."""
    graph, bundle = exported
    _, raw_edges = load_graph(graph)
    nodes = C.load(bundle)

    seed = "svc:pantry-api@pantry-api"
    assert seed in nodes, sorted(nodes)[:5]
    one_hop = {r["id"] for r in C.touches(nodes, seed, hops=1)}
    expected = {e["dst"] for e in raw_edges if e["src"] == seed}
    expected |= {e["src"] for e in raw_edges if e["dst"] == seed}
    assert one_hop == expected, f"markdown says {one_hop}, the graph says {expected}"


def test_every_row_carries_the_citation_the_bundle_gave_it(exported):
    """A blast radius a human cannot check is one they should not act on."""
    _, bundle = exported
    nodes = C.load(bundle)
    rows = C.touches(nodes, "svc:pantry-api@pantry-api", hops=2)
    assert rows
    for r in rows:
        assert r["at"], f"{r['id']} reached with no citation"
        assert r["provenance"] in ("EXTRACTED", "INFERRED", "AMBIGUOUS")
        assert isinstance(r["confidence"], float)
        assert r["extractor"]


def test_a_missing_confidence_keeps_the_rest_of_the_citation(tmp_path):
    """**Producer composed with consumer on the shape that dropped evidence.** An
    edge whose confidence is present-but-null was rendered `confidence None` by the
    exporter, and the consumer's citation regex cannot match "None" — so the row
    reached a reader having silently lost its source, provenance and extractor,
    keeping only the arrow. The real fixtures never carry this shape (the model
    defaults confidence to a float), so it is built here by the REAL exporter and
    read back by the REAL consumer, over the filesystem — a fixture that hand-wrote
    the reader's expectation would prove nothing about either end.
    """
    files = X.build_bundle(
        [{"id": "a", "type": "svc", "props": {}},
         {"id": "b", "type": "svc", "props": {}}],
        [{"src": "a", "type": "CALLS", "dst": "b", "source_file": "A.java",
          "line": 7, "extractor": "rest", "confidence": None,
          "provenance": "INFERRED"}])
    X.write_bundle(files, tmp_path)
    rows = C.touches(C.load(tmp_path), "a", hops=1)
    assert rows, "the edge vanished entirely"
    (row,) = rows
    assert row["at"] == "A.java:7", f"source citation dropped: {row}"
    assert row["provenance"] == "INFERRED", f"provenance dropped: {row}"
    assert row["extractor"] == "rest", f"extractor dropped: {row}"
    assert row["confidence"] is None, \
        f"a missing confidence must stay absent, not be read back as a number: {row}"


def test_hops_bound_the_answer(exported):
    _, bundle = exported
    nodes = C.load(bundle)
    near = C.touches(nodes, "svc:pantry-api@pantry-api", hops=1)
    far = C.touches(nodes, "svc:pantry-api@pantry-api", hops=3)
    assert {r["id"] for r in near} <= {r["id"] for r in far}
    assert all(r["hop"] <= 1 for r in near)


# ── it refuses rather than answering over half a graph ──────────────────────


def test_a_directory_that_is_not_a_bundle_is_refused(tmp_path):
    with pytest.raises(SystemExit) as exc:
        C.load(tmp_path)
    assert "not an OKF bundle" in str(exc.value)
    assert "not an empty graph" in str(exc.value)


def test_an_empty_bundle_is_refused_rather_than_answered_over(tmp_path):
    (tmp_path / C.NODES_DIR).mkdir()
    with pytest.raises(SystemExit) as exc:
        C.load(tmp_path)
    assert "no node pages" in str(exc.value)


def test_a_page_with_no_id_is_refused_not_skipped(tmp_path):
    """**A bundle with unreadable pages is not a smaller bundle.** Skipping them
    would answer a blast-radius question over part of the graph and present it as
    the whole one."""
    folder = tmp_path / C.NODES_DIR
    folder.mkdir()
    (folder / "good.md").write_text('---\nid: "a"\ntype: "svc"\n---\n\n# a\n',
                                    encoding="utf-8")
    (folder / "bad.md").write_text("no frontmatter here\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        C.load(tmp_path)
    assert "carry no id" in str(exc.value)


def test_an_unknown_node_is_refused_with_a_count(tmp_path):
    folder = tmp_path / C.NODES_DIR
    folder.mkdir()
    (folder / "a.md").write_text('---\nid: "a"\ntype: "svc"\n---\n\n# a\n',
                                 encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        C.touches(C.load(tmp_path), "nope")
    assert "is not in this bundle" in str(exc.value)


# ── it is genuinely outside ─────────────────────────────────────────────────


def test_it_declares_itself_an_outside_process():
    assert C.OUTSIDE_PROCESS is True


def test_it_runs_as_a_bare_script_with_no_harness_on_the_path(exported, tmp_path):
    """**The strongest form of the claim.** Spawned from a directory that is not
    the harness, with `PYTHONPATH` cleared, so an accidental harness import would
    fail rather than silently succeed from the ambient environment."""
    import os

    _, bundle = exported
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = ""
    proc = subprocess.run(
        [sys.executable, str(CONSUMER), "--bundle", str(bundle),
         "--changed", "svc:pantry-api@pantry-api", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(tmp_path), env=env, timeout=300)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    found = json.loads(proc.stdout)
    assert found["changed"] == "svc:pantry-api@pantry-api"
    assert found["touches"], "it answered with nothing"
    assert all(r["at"] for r in found["touches"])


def test_the_cli_lists_what_it_found(exported, capsys):
    _, bundle = exported
    assert C.main(["--bundle", str(bundle), "--list"]) == 0
    out = capsys.readouterr().out
    assert "node(s) in this bundle" in out and "svc:pantry-api" in out


def test_the_module_says_it_proves_the_format_and_not_an_agent():
    """The distinction `RUNNER_CONTRACT` §8 draws, kept where somebody reading
    this module will meet it — a green here must never be filed as C1."""
    doc = C.__doc__ or ""
    assert "never a green agent" in doc
    assert "C1" in doc and "does not pretend" in doc
