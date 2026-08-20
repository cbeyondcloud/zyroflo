"""Item 21 — traversal rules: the damping table examined, externalized, and given the
hub rule the examination demanded.

The census that shaped this: hop 3 on a real workspace put ~5,000 nodes above 0.3 from
3-node seeds. Two causes, two fixes: DG_DEPENDS_ON's unexamined along=0.6 built a
"sibling by shared utility" path (fixed: 0.2 — depending on a library means its changes
hit you, not that yours hit it), and transitively-reached hub nodes dumped their whole
worlds (fixed: threshold/fan attenuation, with seeds and wide integration-chain types
exempt). Measured after: 4,785 → 346 and 726 → 113 strong nodes, consumer chains
byte-identical.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.graph.build as B  # noqa: E402
import tools.graph.impact as I  # noqa: E402
from tools.graph.rules import (DEFAULT_RULES, DEFAULT_TABLE, Rules, RulesError,
                               discover_rules, load_rules)  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


# ── the rules object and its file ─────────────────────────────────────────────


def test_the_defaults_carry_the_examined_constants():
    assert DEFAULT_RULES.damping("DG_DEPENDS_ON") == (0.2, 0.9), \
        "the examination's first finding: depending on a library means its changes " \
        "hit you (0.9), not that yours hit it (was an unexamined 0.6)"
    assert DEFAULT_RULES.wide("DG_DEPENDS_ON") and DEFAULT_RULES.wide("DG_CONSUMES")
    assert not DEFAULT_RULES.wide("DG_TESTS") and not DEFAULT_RULES.wide("DG_EXPOSES")
    assert DEFAULT_RULES.hops == 4, "the plan's cap, confirmed by the frontend seeds " \
                                    "that only reach their consumers at hop 4"
    assert DEFAULT_RULES.hub_threshold == 20
    assert DEFAULT_RULES.damping("DG_NEVER_HEARD_OF") == DEFAULT_RULES.default


def test_a_rules_file_overrides_per_key_and_keeps_the_rest(tmp_path):
    f = tmp_path / "traversal_rules.yml"
    f.write_text("hops: 2\nrules:\n  DG_CALLS: {along: 0.5}\n", encoding="utf-8")
    rules = load_rules(f)
    assert rules.hops == 2
    assert rules.damping("DG_CALLS") == (0.5, 0.9), "against inherited, along overridden"
    assert rules.damping("DG_PRODUCES") == DEFAULT_TABLE["DG_PRODUCES"][:2]
    assert rules.noncanonical == 0.5


def test_a_profile_may_mint_its_own_edge_type(tmp_path):
    f = tmp_path / "traversal_rules.yml"
    f.write_text("rules:\n  DG_ORG_INVENTED: {along: 0.4, against: 0.7, wide: true}\n",
                 encoding="utf-8")
    rules = load_rules(f)
    assert rules.damping("DG_ORG_INVENTED") == (0.4, 0.7)
    assert rules.wide("DG_ORG_INVENTED")


@pytest.mark.parametrize("body, expected", [
    ("hops: 0\n", "integer >= 1"),
    ("noncanonical: 1.5\n", r"\[0, 1\]"),
    ("rules:\n  DG_CALLS: {along: 2.0}\n", r"\[0, 1\]"),
    ("rules:\n  DG_CALLS: {alogn: 0.3}\n", "unknown key"),
    ("hpos: 3\n", "unknown key"),
    ("rules:\n  DG_CALLS: {wide: 0.5}\n", "true or false"),
    ("- a\n- b\n", "must be a mapping"),
])
def test_an_untrustworthy_rules_file_is_refused_loudly(tmp_path, body, expected):
    f = tmp_path / "traversal_rules.yml"
    f.write_text(body, encoding="utf-8")
    with pytest.raises(RulesError, match=expected):
        load_rules(f)


def test_discovery_prefers_explicit_then_beside_the_graph_then_defaults(tmp_path):
    graph_dir = tmp_path / "out"
    graph_dir.mkdir()
    rules, source = discover_rules(graph_dir)
    assert rules is DEFAULT_RULES and source == "defaults"
    (graph_dir / "traversal_rules.yml").write_text("hops: 2\n", encoding="utf-8")
    rules, source = discover_rules(graph_dir)
    assert rules.hops == 2 and source.endswith("traversal_rules.yml")
    explicit = tmp_path / "mine.yml"
    explicit.write_text("hops: 5\n", encoding="utf-8")
    rules, source = discover_rules(graph_dir, explicit)
    assert rules.hops == 5 and source == str(explicit)


def test_missing_yaml_with_a_present_file_refuses_rather_than_diverges(tmp_path,
                                                                       monkeypatch):
    from tools.graph.core.lib import props
    f = tmp_path / "traversal_rules.yml"
    f.write_text("hops: 2\n", encoding="utf-8")
    monkeypatch.setattr(props, "yaml_available", lambda: False)
    with pytest.raises(RulesError, match="refusing rather than silently ranking"):
        load_rules(f)


# ── the hub rule, on hand-made scenes ─────────────────────────────────────────


def edge(src, dst, etype, conf=1.0):
    return {"src": src, "dst": dst, "type": etype, "source_file": "f", "line": 1,
            "extractor": "x", "confidence": conf}


def hub_scene(n: int, etype: str):
    """seed -> mid (CALLS along 0.3), mid fans N via `etype` along."""
    edges = [edge("seed", "mid", "DG_CALLS")]
    edges += [edge("mid", f"leaf{i}", etype) for i in range(n)]
    return edges


def test_a_transitive_hub_shares_its_strength():
    rules = Rules()
    best = I.traverse({"seed": 1.0}, hub_scene(40, "DG_TESTS"), hops=2, rules=rules)
    along = DEFAULT_TABLE["DG_TESTS"][0]
    assert best["leaf0"] == pytest.approx(0.3 * along * (20 / 40)), \
        "40 tests through a node the traversal merely reached arrive at half strength"


def test_a_seed_fans_freely():
    rules = Rules()
    edges = [edge("seed", f"leaf{i}", "DG_TESTS") for i in range(40)]
    best = I.traverse({"seed": 1.0}, edges, hops=1, rules=rules)
    assert best["leaf0"] == pytest.approx(DEFAULT_TABLE["DG_TESTS"][0]), \
        "the seed's fan IS the query — and the A/B gate's winning mechanism"


def test_a_wide_type_fans_freely_even_transitively():
    best = I.traverse({"seed": 1.0}, hub_scene(40, "DG_CONSUMES"), hops=2,
                      rules=Rules())
    along = DEFAULT_TABLE["DG_CONSUMES"][0]
    assert best["leaf0"] == pytest.approx(0.3 * along), \
        "a topic reached transitively hands you every consumer — that IS the coupling"


def test_a_small_fan_is_never_attenuated():
    best = I.traverse({"seed": 1.0}, hub_scene(20, "DG_TESTS"), hops=2, rules=Rules())
    assert best["leaf0"] == pytest.approx(0.3 * DEFAULT_TABLE["DG_TESTS"][0])


def test_the_threshold_is_the_rules_files_to_set(tmp_path):
    f = tmp_path / "traversal_rules.yml"
    f.write_text("hub_threshold: 10\n", encoding="utf-8")
    best = I.traverse({"seed": 1.0}, hub_scene(40, "DG_TESTS"), hops=2,
                      rules=load_rules(f))
    assert best["leaf0"] == pytest.approx(0.3 * DEFAULT_TABLE["DG_TESTS"][0] * 0.25)


def test_the_sibling_by_shared_utility_path_is_dead():
    """seed svc -> its utility module -> another consumer of it. The unexamined 0.6
    put the sibling at 0.54; the examined 0.2 leaves it visible and weak."""
    edges = [edge("svc:mine", "mod:util", "DG_DEPENDS_ON"),
             edge("svc:other", "mod:util", "DG_DEPENDS_ON")]
    best = I.traverse({"svc:mine": 1.0}, edges, hops=2, rules=Rules())
    assert best["mod:util"] == pytest.approx(0.2)
    assert best["svc:other"] == pytest.approx(0.2 * 0.9)


def test_the_library_case_still_reaches_every_dependent():
    """Seeded AT the library, every consumer arrives at 0.9 — the plan's demand."""
    edges = [edge(f"svc:c{i}", "mod:lib", "DG_DEPENDS_ON") for i in range(30)]
    best = I.traverse({"mod:lib": 1.0}, edges, hops=1, rules=Rules())
    assert best["svc:c0"] == pytest.approx(0.9)
    assert sum(1 for k in best if k.startswith("svc:")) == 30


# ── hop depth and parents ride the result ─────────────────────────────────────


def test_traverse_paths_records_depth_and_parent():
    edges = [edge("a", "b", "DG_CALLS"), edge("b", "c", "DG_CALLS")]
    best, hop_of, parent = I.traverse_paths({"a": 1.0}, edges, hops=3, rules=Rules())
    assert hop_of == {"a": 0, "b": 1, "c": 2}
    assert parent == {"b": "a", "c": "b"}
    assert best["c"] == pytest.approx(0.3 * 0.3)


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    out = tmp_path_factory.mktemp("rules") / "out"
    assert B.main(["--workspace", str(FIXTURE / "workspace.toml"),
                   "--out", str(out)]) == 0
    return I.load_graph(out), out


def test_every_ranked_row_names_its_hop_distance(graph):
    (nodes, edges), _ = graph
    result = I.impact(nodes, edges, "orders-api",
                      "src/main/java/com/example/orderflow/api/OrderPublisher.java")
    assert all("hops" in f for f in result["files"])
    assert all("hops" in s for s in result["services"])
    by_path = {f["path"]: f["hops"] for f in result["files"]}
    consumer = next(h for p, h in by_path.items() if p.endswith("OrderConsumer.java"))
    mapping = next(h for p, h in by_path.items() if p.endswith("write-mapping.yml"))
    assert consumer < mapping, \
        "the consumer sits nearer the producer seed than the table mapping behind it"


# ── the file reaches the tool ─────────────────────────────────────────────────


def test_the_cli_uses_rules_beside_the_graph_and_says_so(graph, capsys):
    _, out = graph
    (out / "traversal_rules.yml").write_text("hops: 1\n", encoding="utf-8")
    try:
        assert I.main(["--graph-dir", str(out), "--seed",
                       "web-console/src/config/urls.js"]) == 0
        text = capsys.readouterr().out
        assert "rules: traversal_rules.yml" in text
        assert "hops: 1" in text
    finally:
        (out / "traversal_rules.yml").unlink()


def test_the_cli_refuses_a_broken_rules_file_loudly(graph, tmp_path, capsys):
    _, out = graph
    bad = tmp_path / "traversal_rules.yml"
    bad.write_text("hpos: 3\n", encoding="utf-8")
    assert I.main(["--graph-dir", str(out), "--seed",
                   "web-console/src/config/urls.js", "--rules", str(bad)]) == 2
    assert "unknown key" in capsys.readouterr().out


def test_the_build_carries_a_profiles_rules_beside_the_graph(tmp_path):
    """A workspace selecting a profile that ships traversal_rules.yml finds the file
    beside its graph — the graph dir answers alone."""
    ws = tmp_path / "ws"
    (ws / "comp-a").mkdir(parents=True)
    (ws / "comp-a" / "app.properties").write_text("a.key=1\n", encoding="utf-8")
    profile = tmp_path / "my-profile"
    profile.mkdir()
    # A profile is a package even when it only ships rules — the empty EXTRACTORS
    # tuple is its explicit statement that it extracts nothing.
    (profile / "__init__.py").write_text("EXTRACTORS: tuple = ()\n", encoding="utf-8")
    (profile / "traversal_rules.yml").write_text("hops: 2\n", encoding="utf-8")
    (tmp_path / "sdlc-workspace.toml").write_text(
        'schema_version = 1\nroot = "ws"\nprofile = "./my-profile"\n\n'
        '[[component]]\nname = "comp-a"\npath = "comp-a"\n', encoding="utf-8")
    out = tmp_path / "out"
    assert B.main(["--workspace", str(tmp_path / "sdlc-workspace.toml"),
                   "--out", str(out)]) == 0
    assert (out / "traversal_rules.yml").read_text(encoding="utf-8") == "hops: 2\n"
