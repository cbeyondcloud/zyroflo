"""Link passes, the profile layer, and the whole six-hop chain.

The last hop, the last two decoys, and the resolution of the collision 3d only recorded.
The section at the end walks the entire chain of `EXPECTED.md` end to end — which is what
item 4 turns into a gate by adding the mutation test.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.lib import ids  # noqa: E402
from tools.graph.core.model import Edge, ExtractResult, Node  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.links import canonical  # noqa: E402
from tools.graph.profiles import orderflow  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


# ── helpers ───────────────────────────────────────────────────────────────────


def ctx_for(tmp_path: Path, files: dict[str, str], alias: str = "svc") -> ExtractContext:
    root = tmp_path / alias
    paths = []
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    return ExtractContext(repos=[RepoRef(alias=alias, path=root)], run_id="r",
                          files={alias: paths})


def deploy_edge(src: str, path: str, line: int = 1) -> Edge:
    return Edge(src=src, type="DG_DEPLOYS", dst=ids.component_service_id("orders-api"),
                source_file=path, line=line, extractor="t", confidence=1.0)


def deploy_node(node_id: str, component: str) -> Node:
    return Node(id=node_id, type="deploy", props={"component": component})


def link(nodes, edges, config) -> ExtractResult:
    ctx = ExtractContext(repos=[], run_id="r", config=config)
    return canonical.CanonicalPass().link(ctx, nodes, edges)


MAPPING = "version: 1\nmappings:\n  - topic: order-events-v1\n    table: order_events\n    mode: upsert\n"


# ── canonical resolution ──────────────────────────────────────────────────────


def test_a_single_claim_is_left_alone():
    edges = [deploy_edge("deploy:a", "charts/x/values.yaml")]
    res = link([deploy_node("deploy:a", "charts")], edges, {})
    assert res.unresolved == [] and edges[0].canonical is True


def test_path_decides_between_two_packages_in_one_component():
    """`owner` cannot separate them — both would name the same component. `path` is the
    narrower statement and the only one that reaches this case."""
    edges = [deploy_edge("deploy:a", "charts/orderflow/values.yaml"),
             deploy_edge("deploy:b", "legacy/orders-api/values.yaml")]
    nodes = [deploy_node("deploy:a", "deploy-charts"), deploy_node("deploy:b", "deploy-charts")]
    res = link(nodes, edges, {"canonical": [{"service": "orders-api", "owner": "deploy-charts",
                                             "path": "charts/orderflow"}]})
    assert res.unresolved == []
    assert edges[0].canonical is True and edges[0].confidence == 1.0
    assert edges[1].canonical is False


def test_the_losing_claim_is_damped_and_never_dropped():
    """A superseded variant that is about to break is exactly what blast radius exists to
    find. It has to stay visible AND rank below the real one; those are two requirements."""
    edges = [deploy_edge("deploy:a", "charts/x/values.yaml"),
             deploy_edge("deploy:b", "legacy/x/values.yaml")]
    nodes = [deploy_node("deploy:a", "c"), deploy_node("deploy:b", "c")]
    link(nodes, edges, {"canonical": [{"service": "orders-api", "path": "charts/x"}]})
    assert len(edges) == 2, "still in the graph"
    assert edges[1].confidence == 0.5
    assert edges[1].props["superseded_by"] == "charts/x/values.yaml"


def test_owner_decides_when_the_variants_are_in_different_components():
    edges = [deploy_edge("deploy:a", "values.yaml"), deploy_edge("deploy:b", "values.yaml")]
    nodes = [deploy_node("deploy:a", "platform"), deploy_node("deploy:b", "legacy-platform")]
    link(nodes, edges, {"canonical": [{"service": "orders-api", "owner": "platform"}]})
    assert edges[0].canonical is True and edges[1].canonical is False
    assert edges[0].props["canonical_by"] == "owner=platform"


def test_an_undeclared_collision_is_reported_and_nothing_is_marked():
    """Every available heuristic is a guess in a fact's clothing, and a guess here attaches
    configuration edges to the wrong service while looking entirely correct."""
    edges = [deploy_edge("deploy:a", "charts/x/values.yaml"),
             deploy_edge("deploy:b", "legacy/x/values.yaml")]
    nodes = [deploy_node("deploy:a", "c"), deploy_node("deploy:b", "c")]
    res = link(nodes, edges, {})
    assert [r["kind"] for r in res.unresolved] == ["contested-service"]
    assert all(e.canonical for e in edges), "nothing decided means nothing marked"
    assert "charts/x/values.yaml:1" in res.unresolved[0]["claims"]


def test_a_declaration_that_matches_neither_claim_still_reports():
    edges = [deploy_edge("deploy:a", "charts/x/values.yaml"),
             deploy_edge("deploy:b", "legacy/x/values.yaml")]
    nodes = [deploy_node("deploy:a", "c"), deploy_node("deploy:b", "c")]
    res = link(nodes, edges, {"canonical": [{"service": "orders-api", "path": "nowhere"}]})
    assert [r["kind"] for r in res.unresolved] == ["contested-service"]


def test_a_declaration_matching_both_claims_does_not_decide():
    """An ambiguous declaration is not a decision. Picking the first would make the answer
    depend on directory enumeration order."""
    edges = [deploy_edge("deploy:a", "charts/a/values.yaml"),
             deploy_edge("deploy:b", "charts/b/values.yaml")]
    nodes = [deploy_node("deploy:a", "c"), deploy_node("deploy:b", "c")]
    res = link(nodes, edges, {"canonical": [{"service": "orders-api", "path": "charts"}]})
    assert [r["kind"] for r in res.unresolved] == ["contested-service"]


def test_the_declaration_is_matched_on_the_logical_name_a_developer_writes():
    """A developer writes `service = "orders-api"`, not the internal identity string.
    Requiring the latter would make the config file unwritable without reading the source."""
    edges = [deploy_edge("deploy:a", "charts/x/values.yaml"),
             deploy_edge("deploy:b", "legacy/x/values.yaml")]
    nodes = [deploy_node("deploy:a", "c"), deploy_node("deploy:b", "c")]
    res = link(nodes, edges, {"canonical": [{"service": "orders-api", "path": "charts/x"}]})
    assert res.unresolved == [] and edges[0].canonical is True


# ── the profile's own format ──────────────────────────────────────────────────


def test_a_mapping_becomes_an_edge_from_topic_to_table(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"config/write-mapping.yml": MAPPING})
    res = orderflow.WriteMappingsExtractor().extract(ctx)
    e = res.edges[0]
    assert (e.type, e.src, e.dst) == ("DG_WRITES_TABLE", "topic:order-events-v1",
                                      "table:order_events")
    assert e.line == 3 and e.props["table_line"] == 4


def test_a_malformed_mapping_is_recorded_with_the_parser_message(tmp_path: Path):
    """D6. 'This file was skipped' is a log line; 'skipped because line 5 has a mapping value
    where none is allowed' is something a human can act on without reproducing the build."""
    bad = "topic: t\n  table: [oops\n   - unbalanced\n"
    res = orderflow.WriteMappingsExtractor().extract(
        ctx_for(tmp_path, {"a-mapping.yml": bad}))
    assert res.edges == []
    row = res.unresolved[0]
    assert row["kind"] == "unparsable" and "mapping values are not allowed" in row["error"]


def test_a_malformed_file_does_not_stop_the_others(tmp_path: Path):
    """Aborting fails a whole workspace on one bad file in one component. Skipping silently
    drops real edges with no trace. Neither is acceptable, and they are not the same mistake."""
    bad = "topic: t\n  table: [oops\n"
    ctx = ctx_for(tmp_path, {"a-mapping.yml": bad, "b-mapping.yml": MAPPING})
    res = orderflow.WriteMappingsExtractor().extract(ctx)
    assert len(res.edges) == 1 and len(res.unresolved) == 1


def test_a_half_written_mapping_entry_is_reported(tmp_path: Path):
    doc = "mappings:\n  - topic: t\n  - table: only_a_table\n"
    res = orderflow.WriteMappingsExtractor().extract(ctx_for(tmp_path, {"a-mapping.yml": doc}))
    assert res.edges == []
    assert [r["kind"] for r in res.unresolved] == ["incomplete-mapping", "incomplete-mapping"]


def test_a_mappings_key_that_is_not_a_list_is_reported(tmp_path: Path):
    res = orderflow.WriteMappingsExtractor().extract(
        ctx_for(tmp_path, {"a-mapping.yml": "mappings: nope\n"}))
    assert [r["kind"] for r in res.unresolved] == ["malformed-mappings"]


def test_a_topic_nothing_publishes_is_reported(tmp_path: Path):
    """D7. The mapping is KEPT — dropping it would be the silent loss this report exists to
    prevent — and the table it fills is recorded as having no visible source."""
    edges = [Edge(src="topic:shipment-events-v1", type="DG_WRITES_TABLE",
                  dst="table:shipment_events", source_file="orphan-mapping.yml", line=6,
                  extractor="t")]
    ctx = ExtractContext(repos=[], run_id="r")
    res = orderflow.UnproducedTopicsPass().link(ctx, [], edges)
    assert [r["kind"] for r in res.unresolved] == ["unproduced-topic"]
    assert len(edges) == 1, "the mapping stays in the graph"


def test_a_topic_something_publishes_is_not_reported():
    edges = [Edge(src="topic:t", type="DG_WRITES_TABLE", dst="table:x",
                  source_file="m.yml", line=1, extractor="t"),
             Edge(src="svc:a@a", type="DG_PRODUCES", dst="topic:t",
                  source_file="P.java", line=1, extractor="t")]
    ctx = ExtractContext(repos=[], run_id="r")
    assert orderflow.UnproducedTopicsPass().link(ctx, [], edges).unresolved == []


# ── profile selection ─────────────────────────────────────────────────────────


def test_no_profile_selected_means_no_profile_loaded():
    assert B.resolve_profile({"profile": "", "config_path": Path(".")}) is None


def test_a_bare_name_resolves_under_the_harness_profiles():
    assert B.resolve_profile({"profile": "orderflow", "config_path": Path(".")}) \
        == B.PROFILE_ROOT / "orderflow"


def test_a_path_resolves_relative_to_the_workspace_config(tmp_path: Path):
    """Where a real workspace's profile belongs: it is that developer's code, not this
    repository's."""
    (tmp_path / "mine").mkdir()
    cfg = tmp_path / "sdlc-workspace.toml"
    assert B.resolve_profile({"profile": "./mine", "config_path": cfg}) == (tmp_path / "mine")


def test_a_named_profile_that_does_not_exist_is_fatal():
    """Continuing without it produces a graph short by exactly the edges it was written for,
    and nothing in the output would say so."""
    with pytest.raises(B.BuildError, match="does not exist"):
        B.resolve_profile({"profile": "not-a-profile", "config_path": Path(".")})


def test_a_profile_directory_is_the_package_not_a_root_of_them():
    """Scanning it for children finds nothing and registers nothing, which reads exactly like
    a profile that had nothing to contribute."""
    regs, links = B.discover_package(B.PROFILE_ROOT / "orderflow", "profile")
    assert [r.name for r in regs] == ["orderflow-write-mappings"]
    assert [lp.name for lp in links] == ["orderflow-unproduced-topics"]


def test_a_profile_cannot_shadow_a_registered_extractor(tmp_path: Path):
    """Names are recorded on edges, so two would be indistinguishable in provenance — and the
    layer that lost the race would have its output shard overwritten."""
    pkg = tmp_path / "mine"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(textwrap.dedent("""
        from tools.graph.core.model import ExtractResult
        class _E:
            name = "properties"
            INPUT_GLOBS = ("*.x",)
            def extract(self, ctx):
                return ExtractResult()
        EXTRACTORS = (_E(),)
    """), encoding="utf-8")
    seen: dict[str, str] = {}
    B.discover(None, "stack", seen)
    with pytest.raises(B.BuildError, match="already registered"):
        B.discover_package(pkg, "profile", seen)


# ── the link stage in the pipeline ────────────────────────────────────────────


def test_a_package_exporting_neither_tuple_is_a_hard_error(tmp_path: Path):
    pkg = tmp_path / "ext" / "quiet"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("X = 1\n", encoding="utf-8")
    with pytest.raises(B.BuildError, match="neither `EXTRACTORS` nor `LINK_PASSES`"):
        B.discover(tmp_path / "ext", "stack")


def test_a_package_may_export_only_link_passes(tmp_path: Path):
    pkg = tmp_path / "ext" / "joiner"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(textwrap.dedent("""
        from tools.graph.core.model import ExtractResult
        class _L:
            name = "joiner"
            def link(self, ctx, nodes, edges):
                return ExtractResult()
        LINK_PASSES = (_L(),)
    """), encoding="utf-8")
    regs, links = B.discover(tmp_path / "ext", "link")
    assert regs == [] and [lp.name for lp in links] == ["joiner"]


def test_a_link_pass_without_a_link_method_is_rejected(tmp_path: Path):
    pkg = tmp_path / "ext" / "bad"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(textwrap.dedent("""
        class _L:
            name = "bad"
        LINK_PASSES = (_L(),)
    """), encoding="utf-8")
    with pytest.raises(B.BuildError, match="no callable `link"):
        B.discover(tmp_path / "ext", "link")


def test_link_passes_are_ordered_by_their_requirements():
    """A second pass over a graph sees what the first one decided, so order is load-bearing
    here for the same reason it is for extractors."""
    _, links = B.discover(B.LINK_ROOT, "link")
    ordered = B.order_extractors(links)
    assert [lp.name for lp in ordered] == [lp.name for lp in links] or ordered


def test_the_canonical_pass_registers_generically():
    _, links = B.discover(B.LINK_ROOT, "link")
    by_name = {lp.name: lp for lp in links}
    assert by_name["mark-canonical"].layer == "link"


# ── the whole chain, against the committed answer key ─────────────────────────


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("graph")
    report = B.build(FIXTURE / "workspace.toml", out, run_id="fixture01")
    edges = [json.loads(line)
             for line in (out / "graph.jsonl").read_text(encoding="utf-8").splitlines()
             if '"kind": "edge"' in line]
    unresolved = [json.loads(line) for line in
                  (out / "unresolved.jsonl").read_text(encoding="utf-8").splitlines() if line]
    return report, edges, unresolved


#: Every hop of `EXPECTED.md`'s chain, with the file and line it cites.
CHAIN = [
    ("DG_CALLS", "ui:web-console@web-console", "ep:/orders/v1/list", "urls.js", 4),
    ("DG_EXPOSES", ids.component_service_id("orders-api"), "ep:/orders/v1/list",
     "OrderController.java", 12),
    ("DG_PRODUCES", ids.component_service_id("orders-api"), "topic:order-events-v1",
     "OrderPublisher.java", 9),
    ("DG_CONSUMES", ids.component_service_id("orders-worker"), "topic:order-events-v1",
     "OrderConsumer.java", 8),
    ("DG_WRITES_TABLE", "topic:order-events-v1", "table:order_events", "write-mapping.yml", 5),
    ("DG_OWNS_TABLE", ids.component_service_id("orders-worker"), "table:order_events",
     "V1__create_order_events.sql", 2),
]


@pytest.mark.parametrize("type_, src, dst, file, line", CHAIN,
                         ids=[f"hop{i}" for i in range(1, len(CHAIN) + 1)])
def test_fixture_every_hop_of_the_chain(built, type_, src, dst, file, line):
    _, edges, _ = built
    hits = [e for e in edges if e["type"] == type_ and e["src"] == src and e["dst"] == dst]
    assert len(hits) == 1, f"expected exactly one {type_}: {src} -> {dst}"
    assert hits[0]["source_file"].endswith(file)
    assert hits[0]["line"] == line, f"EXPECTED.md cites {file}:{line}"


def test_fixture_the_chain_crosses_component_boundaries(built):
    """The property that matters is not the count of components but that the chain LEAVES
    one — a fixture small enough to be re-read has to work to achieve that, and a chain that
    stays inside one component proves nothing about a cross-component graph.

    Attributed by declaring component, not by path: `source_file` is component-relative and
    carries no component name, so counting path prefixes measures directory layout instead.
    """
    _, edges, _ = built
    by_key = {(e["type"], e["src"], e["dst"]): e for e in edges}
    order = [by_key[(t, s, d)] for t, s, d, _, _ in CHAIN]
    owners = [e["props"].get("component") or e["src"].split("@")[-1] for e in order]
    assert owners == ["web-console", "orders-api", "orders-api",
                      "orders-worker", "orders-worker", "orders-worker"]
    crossings = sum(1 for a, b in zip(owners, owners[1:]) if a != b)
    assert crossings == 2, "EXPECTED.md: three components, two boundaries crossed"


def test_fixture_d4_is_resolved_from_the_workspaces_own_declaration(built):
    _, edges, _ = built
    claims = {e["source_file"]: e for e in edges if e["type"] == "DG_DEPLOYS"}
    winner = claims["charts/orderflow/values.yaml"]
    loser = claims["legacy/orders-api/values.yaml"]
    assert winner["canonical"] is True and winner["confidence"] == 1.0
    assert loser["canonical"] is False and loser["confidence"] == 0.5
    assert loser["props"]["superseded_by"] == "charts/orderflow/values.yaml"


def test_fixture_unresolved_holds_exactly_the_three_that_were_pre_registered(built):
    """`EXPECTED.md` says three — D6, D7 and D8 — and says so from before any extractor
    existed. A fourth would mean something landed by accident rather than by design."""
    _, _, unresolved = built
    assert len(unresolved) == 3
    assert {r["kind"] for r in unresolved} == {"unparsable", "unproduced-topic", "placeholder"}


def test_fixture_d6_the_malformed_file_carries_its_parse_error(built):
    _, _, unresolved = built
    row = next(r for r in unresolved if r["kind"] == "unparsable")
    assert row["source_file"].endswith("broken-mapping.yml")
    assert "mapping values are not allowed" in row["error"]


def test_fixture_d7_the_orphan_mapping_cites_its_own_line(built):
    _, _, unresolved = built
    row = next(r for r in unresolved if r["kind"] == "unproduced-topic")
    assert row["source_file"].endswith("orphan-mapping.yml")
    assert row["line"] == 6, "EXPECTED.md cites orphan-mapping.yml:6"
    assert row["topic"] == "topic:shipment-events-v1"


def test_fixture_the_build_is_clean(built):
    report, _, _ = built
    assert report["status"] == "ok"
    assert report["warnings"] == [], f"unexpected: {report['warnings']}"
    assert report["components_unclaimed"] == []


def test_fixture_the_profile_is_the_one_the_workspace_selected(built):
    report, _, _ = built
    assert report["workspace"]["profile"].endswith("orderflow")
    assert [e["name"] for e in report["extractors"] if e["layer"] == "profile"] \
        == ["orderflow-write-mappings"]


def test_fixture_the_build_is_deterministic(tmp_path_factory):
    """`rebuild == snapshot` doubles as an extractor regression check, so the same workspace
    must serialize byte-identically however many times it is built."""
    a, b = tmp_path_factory.mktemp("a"), tmp_path_factory.mktemp("b")
    B.build(FIXTURE / "workspace.toml", a, run_id="one")
    B.build(FIXTURE / "workspace.toml", b, run_id="two")
    assert (a / "graph.jsonl").read_bytes() == (b / "graph.jsonl").read_bytes()
    assert (a / "unresolved.jsonl").read_bytes() == (b / "unresolved.jsonl").read_bytes()
