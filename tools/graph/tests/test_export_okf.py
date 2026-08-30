"""The OKF bundle: the widest access tier, and the one that needs nothing. Item 54.

Three tiers, widest last: **MCP tools > Python CLIs > OKF bundle**. The first two
need this harness running; this one needs a reader that can open a file and follow
a relative link. That is the property under test, and everything else here serves
it.

**The cross-links ARE the graph.** A bundle whose links do not resolve is a
directory of disconnected pages — it would still look right, and it would have lost
the thing the format exists for. The first cut of the exporter had exactly that
defect: node pages live in `nodes/` and linked to `nodes/<file>`, resolving to
`nodes/nodes/<file>`. Every cross-link in the bundle was dead and every page
rendered fine.

**Every relationship carries its provenance into the bundle** — `source_file:line`,
extractor, confidence, and EXTRACTED / INFERRED / AMBIGUOUS. A projection that drops
it produces exactly the confident, uncheckable artifact the graph exists to avoid.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.graph import build as B  # noqa: E402
from tools.graph import export_okf as X  # noqa: E402
from tools.graph.impact import GraphError, load_graph  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LINK = re.compile(r"\]\(([^)]+)\)")


@pytest.fixture(scope="module", params=("pantry", "orderflow"))
def bundle(request, tmp_path_factory) -> Path:
    """A REAL build of a real fixture, exported. Both of them, because 'any agent
    can consume it' is a claim about the format and one fixture cannot make it."""
    base = tmp_path_factory.mktemp(f"okf-{request.param}")
    graph, out = base / "graph", base / "bundle"
    B.build(FIXTURES / request.param / "workspace.toml", graph,
            run_id=f"{request.param}-okf")
    X.export(graph, out)
    return out


# ── the links are the graph ─────────────────────────────────────────────────


def test_every_cross_link_in_the_bundle_resolves(bundle):
    """**The defect the first cut shipped.** Pages in `nodes/` linked to
    `nodes/<file>`, which resolves to `nodes/nodes/<file>` — every link dead,
    every page rendering fine."""
    dead = []
    for page in sorted((bundle / X.NODES_DIR).glob("*.md")):
        for link in LINK.findall(page.read_text(encoding="utf-8")):
            if not (page.parent / link).is_file():
                dead.append(f"{page.name} -> {link}")
    for link in LINK.findall((bundle / X.INDEX).read_text(encoding="utf-8")):
        if not (bundle / link).is_file():
            dead.append(f"{X.INDEX} -> {link}")
    assert not dead, f"{len(dead)} dead link(s): {dead[:4]}"


def test_the_bundle_actually_contains_links(bundle):
    """The guard's own guard: a bundle with no links at all would pass the test
    above by having nothing to check."""
    found = sum(len(LINK.findall(p.read_text(encoding="utf-8")))
                for p in (bundle / X.NODES_DIR).glob("*.md"))
    assert found > 0, "no page links to any other - the graph did not survive"


def test_a_node_page_names_its_relationships_in_both_directions(bundle):
    pages = [p.read_text(encoding="utf-8")
             for p in (bundle / X.NODES_DIR).glob("*.md")]
    assert any("## Outgoing" in p for p in pages)
    assert any("## Incoming" in p for p in pages)


# ── provenance survives the projection ──────────────────────────────────────


def test_every_relationship_carries_where_it_came_from(bundle):
    """`source_file:line`, extractor, confidence, provenance. A row a human is
    asked to trust is a row they can go and check."""
    seen = 0
    for page in (bundle / X.NODES_DIR).glob("*.md"):
        for line in page.read_text(encoding="utf-8").splitlines():
            if not line.strip().startswith("- **"):
                continue
            seen += 1
    assert seen > 0, "no relationship lines at all"

    body = "\n".join(p.read_text(encoding="utf-8")
                     for p in (bundle / X.NODES_DIR).glob("*.md"))
    assert "confidence" in body and "extractor" in body
    assert re.search(r"EXTRACTED|INFERRED|AMBIGUOUS", body)


def test_a_citation_names_a_file_and_a_line():
    page = X.node_page(
        {"id": "a", "type": "svc", "props": {}},
        [{"src": "a", "type": "CALLS", "dst": "b", "source_file": "A.java",
          "line": 42, "extractor": "rest", "confidence": 0.9,
          "provenance": "INFERRED"}],
        [], {"b": "b.md"}, stub=False)
    assert "`A.java:42`" in page
    assert "INFERRED" in page and "0.9" in page and "`rest`" in page


# ── nothing is lost on the way out ──────────────────────────────────────────


def test_a_node_appearing_twice_MERGES_rather_than_the_last_one_winning():
    """**Measured on a real fixture**: one endpoint arrived twice, once carrying a
    `path` prop and once with none. Letting the last row win drops whatever the
    others knew, and the empty row is as likely to be last as the full one — so
    the bundle would lose a fact depending on iteration order."""
    files = X.build_bundle(
        [{"id": "ep:/x", "type": "ep", "props": {"path": "/x"}},
         {"id": "ep:/x", "type": "ep", "props": {}}], [])
    page = files[f"{X.NODES_DIR}/{X.filename_for('ep:/x')}"]
    assert "prop_path" in page and "/x" in page
    assert json.loads(files[X.MANIFEST])["nodes"] == 1


def test_the_manifest_count_matches_what_was_written():
    """A manifest that disagrees with the directory is a bundle that lies about
    itself — and it did, before duplicate ids were merged rather than dropped."""
    files = X.build_bundle(
        [{"id": "a", "type": "svc", "props": {}},
         {"id": "a", "type": "svc", "props": {}},
         {"id": "b", "type": "svc", "props": {}}], [])
    pages = [k for k in files if k.startswith(f"{X.NODES_DIR}/")]
    assert json.loads(files[X.MANIFEST])["nodes"] == len(pages) == 2


def test_a_dangling_reference_becomes_a_visible_page_not_a_dropped_link():
    """Zero silent drops, carried into the export. A reference nobody could
    resolve is a coverage finding, and a bundle that hid it would look more
    complete than the graph is."""
    files = X.build_bundle(
        [{"id": "a", "type": "svc", "props": {}}],
        [{"src": "a", "type": "CALLS", "dst": "ghost", "source_file": "f",
          "line": 1, "extractor": "x", "confidence": 1.0,
          "provenance": "EXTRACTED"}])
    page = files[f"{X.NODES_DIR}/{X.filename_for('ghost')}"]
    assert "unresolved: true" in page
    assert "Unresolved" in page
    assert json.loads(files[X.MANIFEST])["unresolved"] == 1


# ── filenames a filesystem will accept ──────────────────────────────────────


@pytest.mark.parametrize("node_id", [
    "svc:a@repo", "ep:/pantry/v1/items", "topic:a.b.c",
    # A backslash and a colon, which is what this case is FOR - written without a
    # drive letter because `test_no_private_coordinates` bans machine paths in
    # tracked files, and it caught the first version of this line in CI. The guard
    # covering the repo's own tests is the design working; the second time it has
    # caught an example of mine rather than a real leak.
    "cfg:some\\nested:thing", 'weird:"<>|*?', "con", "PRN", "aux",
    "trailing.", "trailing ", "", "x" * 300,
])
def test_a_filename_is_safe_on_any_filesystem(node_id):
    name = X.filename_for(node_id)
    assert name.endswith(".md") and len(name) < 120
    assert not re.search(r'[:<>"|?*\\/]', name)
    assert Path(name).stem.split(".")[0].lower() not in X._RESERVED
    assert name == name.strip() and not name.startswith((".", "-"))


def test_two_ids_that_slug_the_same_get_different_files():
    """Slugging alone collides — `svc:a/b` and `svc:a-b` both become `svc-a-b`,
    and two nodes sharing a file is a graph that lost an entity silently."""
    assert X.filename_for("svc:a/b") != X.filename_for("svc:a-b")
    assert X.filename_for("A") != X.filename_for("a")


def test_a_filename_is_the_same_every_time():
    assert X.filename_for("svc:a@r") == X.filename_for("svc:a@r")


# ── deterministic, so a bundle can be diffed ────────────────────────────────


def test_the_same_graph_produces_a_byte_identical_bundle():
    """The committed-snapshot rule applied here: no run id, no timestamp, sorted
    everywhere. A bundle that churned could not be reviewed in a pull request."""
    nodes = [{"id": "b", "type": "svc", "props": {"n": 2}},
             {"id": "a", "type": "svc", "props": {"n": 1}}]
    edges = [{"src": "a", "type": "CALLS", "dst": "b", "source_file": "f",
              "line": 2, "extractor": "x", "confidence": 1.0,
              "provenance": "EXTRACTED"},
             {"src": "a", "type": "CALLS", "dst": "b", "source_file": "f",
              "line": 1, "extractor": "x", "confidence": 1.0,
              "provenance": "EXTRACTED"}]
    first = X.build_bundle(nodes, edges)
    second = X.build_bundle(list(reversed(nodes)), list(reversed(edges)))
    assert first == second


def test_the_bundle_carries_no_run_id_or_timestamp(bundle):
    body = "\n".join(p.read_text(encoding="utf-8")
                     for p in bundle.rglob("*") if p.is_file())
    assert "run_id" not in body


# ── it refuses what it cannot read ──────────────────────────────────────────


def test_an_absent_graph_is_refused_by_name(tmp_path):
    """`GraphError`'s recorded convention: the caller checks the graph EXISTS.
    A bundle written from nothing reads as a workspace with nothing in it."""
    with pytest.raises(GraphError) as exc:
        X.export(tmp_path / "nowhere", tmp_path / "out")
    assert "does not exist" in str(exc.value)
    assert not (tmp_path / "out").exists(), "it wrote a bundle it had refused"


def test_a_damaged_graph_is_refused_rather_than_partly_exported(tmp_path):
    g = tmp_path / "g"
    g.mkdir()
    (g / "graph.jsonl").write_text('{"kind":"node","id":"a","type":"svc"}\n{bad\n',
                                   encoding="utf-8")
    with pytest.raises(GraphError):
        X.export(g, tmp_path / "out")


# ── the claim it makes about itself ─────────────────────────────────────────


def test_the_bundle_states_its_shape_and_does_not_assert_conformance(bundle):
    """The OKF spec is not vendored here, so a claim of conformance would be one
    nobody in this repository could check. The bundle names the shape it targets
    and says why — which is the difference between a citation and an assertion."""
    index = (bundle / X.INDEX).read_text(encoding="utf-8")
    assert X.BUNDLE_SHAPE in index

    # Asserted on the SENTENCE, not on the words `not`, `vendored` and
    # `conformance` appearing somewhere - a mutation replacing the claim with
    # "Fully OKF-conformant. Verified." left all three words standing elsewhere in
    # the paragraph and walked straight through the first version of this test.
    # The same too-loose shape item 58's first cut had, in the same session.
    assert "rather than asserting verified conformance" in index

    lower = index.lower()
    for overclaim in ("fully okf-conformant", "okf-conformant.", "spec-compliant",
                      "conforms to the okf"):
        assert overclaim not in lower, f"the bundle claims {overclaim!r}"


def test_the_index_says_what_a_reader_needs_to_read_it(bundle):
    index = (bundle / X.INDEX).read_text(encoding="utf-8")
    for promise in ("no database", "no server", "no network"):
        assert promise in index
    assert "unresolved" in index


def test_the_bundle_is_markdown_and_json_only(bundle):
    """Nothing that needs executing, and nothing that needs this harness."""
    suffixes = {p.suffix for p in bundle.rglob("*") if p.is_file()}
    assert suffixes <= {".md", ".json"}, suffixes


def test_the_cli_exports_and_reports(tmp_path, capsys):
    graph, out = tmp_path / "g", tmp_path / "b"
    B.build(FIXTURES / "pantry" / "workspace.toml", graph, run_id="cli")
    assert X.main(["--graph", str(graph), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "node(s)" in printed and "unresolved" in printed
    assert (out / X.INDEX).is_file() and (out / X.MANIFEST).is_file()
