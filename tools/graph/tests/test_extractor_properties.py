"""The property substrate: relaxed binding, interpolation, and the honest absence.

Three of the fixture's eight decoys are answered here, and the section at the bottom checks
them against the committed fixture itself — not because the unit tests are insufficient, but
because `EXPECTED.md` cites specific line numbers and a resolver that returns the right value
from the wrong line is wrong in a way nothing notices unless someone asserts the number.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import properties as P  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


def ctx_for(tmp_path: Path, files: dict[str, str], alias: str = "svc") -> ExtractContext:
    """A context holding one component with the given files, as the pipeline would build it."""
    root = tmp_path / alias
    paths = []
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    return ExtractContext(repos=[RepoRef(alias=alias, path=root)], run_id="r",
                          files={alias: paths})


def view_of(tmp_path: Path, files: dict[str, str], alias: str = "svc") -> dict:
    ctx = ctx_for(tmp_path, files, alias)
    P.PropertiesExtractor().extract(ctx)
    return ctx.cache[P.CACHE_KEY][alias]


def run(tmp_path: Path, files: dict[str, str], alias: str = "svc"):
    ctx = ctx_for(tmp_path, files, alias)
    res = P.PropertiesExtractor().extract(ctx)
    return ctx.cache[P.CACHE_KEY][alias], res


# ── D2: one setting, several spellings ────────────────────────────────────────


def test_three_spellings_collapse_to_one_key(tmp_path: Path):
    """The decoy's own words: three spellings must not become three keys, or the graph grows
    two phantom entities beside the real one — and phantoms look exactly like the real one."""
    view = view_of(tmp_path, {"a.properties":
                              "orders.publish.topic=t\n"
                              "ORDERS_PUBLISH_TOPIC=t\n"
                              "orders-publish-topic=t\n"})
    assert list(view["values"]) == ["orders.publish.topic"]
    assert view["values"]["orders.publish.topic"]["value"] == "t"


def test_every_spelling_is_recorded_with_its_own_line(tmp_path: Path):
    view = view_of(tmp_path, {"a.properties":
                              "orders.publish.topic=t\n"
                              "ORDERS_PUBLISH_TOPIC=t\n"
                              "orders-publish-topic=t\n"})
    spellings = view["values"]["orders.publish.topic"]["spellings"]
    assert {k: v["line"] for k, v in spellings.items()} == {
        "orders.publish.topic": 1, "ORDERS_PUBLISH_TOPIC": 2, "orders-publish-topic": 3}


def test_lookup_cites_the_line_of_the_spelling_that_was_asked_for(tmp_path: Path):
    """A consumer reading `${ORDERS_PUBLISH_TOPIC}` should be pointed at the line declaring
    THAT spelling. Citing whichever declaration won the value would be provenance that is
    quietly, unfalsifiably wrong."""
    view = view_of(tmp_path, {"a.properties":
                              "orders.publish.topic=t\n"
                              "ORDERS_PUBLISH_TOPIC=t\n"})
    assert P.lookup(view, "ORDERS_PUBLISH_TOPIC")["line"] == 2
    assert P.lookup(view, "orders.publish.topic")["line"] == 1


def test_lookup_falls_back_to_the_first_declaration_for_an_unseen_spelling(tmp_path: Path):
    view = view_of(tmp_path, {"a.properties": "orders.publish.topic=t\nORDERS_PUBLISH_TOPIC=t\n"})
    hit = P.lookup(view, "orders_publish_topic")      # a spelling nobody wrote
    assert hit["line"] == 1 and hit["value"] == "t"


def test_lookup_of_an_undeclared_key_is_none(tmp_path: Path):
    view = view_of(tmp_path, {"a.properties": "a.b=1\n"})
    assert P.lookup(view, "nothing.here") is None


# ── D3: a placeholder is not a value ──────────────────────────────────────────


def test_a_placeholder_with_a_default_resolves_to_the_default(tmp_path: Path):
    view = view_of(tmp_path, {"a.properties": "orders.publish.topic=${ORDER_TOPIC:order-events-v1}\n"})
    assert view["values"]["orders.publish.topic"]["value"] == "order-events-v1"


def test_the_dash_default_form_also_resolves(tmp_path: Path):
    view = view_of(tmp_path, {"a.properties": "k=${VAR:-fallback}\n"})
    assert view["values"]["k"]["value"] == "fallback"


def test_a_placeholder_referencing_another_property_resolves(tmp_path: Path):
    view = view_of(tmp_path, {"a.properties": "base.host=example.internal\nurl=http://${base.host}/x\n"})
    assert view["values"]["url"]["value"] == "http://example.internal/x"


def test_a_placeholder_chain_resolves(tmp_path: Path):
    view = view_of(tmp_path, {"a.properties": "a=${b}\nb=${c:deep}\n"})
    assert view["values"]["a"]["value"] == "deep"


def test_a_placeholder_resolves_across_spellings(tmp_path: Path):
    """`${ORDERS_DB_HOST}` naming a property written `orders.db.host` is the ordinary case,
    not the exotic one: code and deployment routinely spell the same setting differently."""
    view = view_of(tmp_path, {"a.properties": "orders.db.host=db01\nurl=${ORDERS_DB_HOST}:5432\n"})
    assert view["values"]["url"]["value"] == "db01:5432"


def test_the_raw_form_is_kept_beside_the_resolved_one(tmp_path: Path):
    view = view_of(tmp_path, {"a.properties": "k=${V:x}\n"})
    assert view["values"]["k"]["raw"] == "${V:x}"
    assert view["values"]["k"]["value"] == "x"


# ── D8: a placeholder with no default, and the honest absence ─────────────────


def test_a_placeholder_with_no_default_is_kept_out_of_the_resolved_view(tmp_path: Path):
    """Absence is the safe failure. A view that returned the literal `${AUDIT_TOPIC}` would
    hand every consumer a plausible string, and an entity would end up named after it."""
    view, _ = run(tmp_path, {"a.properties": "orders.audit.topic=${AUDIT_TOPIC}\n"})
    assert "orders.audit.topic" not in view["values"]
    assert P.lookup(view, "orders.audit.topic") is None


def test_a_placeholder_with_no_default_is_reported_unresolved(tmp_path: Path):
    view, res = run(tmp_path, {"a.properties": "x=1\norders.audit.topic=${AUDIT_TOPIC}\n"})
    assert view["unresolved"]["orders.audit.topic"]["missing"] == ["AUDIT_TOPIC"]
    assert view["unresolved"]["orders.audit.topic"]["line"] == 2

    row = next(r for r in res.unresolved if r["kind"] == "placeholder")
    assert row["key"] == "orders.audit.topic"
    assert row["line"] == 2
    assert "AUDIT_TOPIC" in row["why"]


def test_the_literal_placeholder_never_becomes_a_value(tmp_path: Path):
    view = view_of(tmp_path, {"a.properties": "k=${NOPE}\n"})
    assert "${NOPE}" not in {v["value"] for v in view["values"].values()}


def test_a_partially_resolvable_value_is_still_unresolved(tmp_path: Path):
    """Half a placeholder expanded is not a resolved value, and treating it as one produces a
    string that looks almost right — the hardest kind of wrong to notice."""
    view = view_of(tmp_path, {"a.properties": "k=${A:one}-${B}\n"})
    assert "k" not in view["values"]
    assert view["unresolved"]["k"]["missing"] == ["B"]


# ── several files in one component ────────────────────────────────────────────


def test_files_merge_with_the_later_path_winning_the_value(tmp_path: Path):
    view = view_of(tmp_path, {"a.properties": "k=first\n", "b.properties": "k=second\n"})
    assert view["values"]["k"]["value"] == "second"


def test_provenance_defaults_to_where_the_setting_was_introduced(tmp_path: Path):
    """The first declaration is where the setting is defined; a later duplicate is usually an
    override. A consumer that asked for a specific spelling gets that spelling instead."""
    view = view_of(tmp_path, {"a.properties": "k=first\n", "b.properties": "k=second\n"})
    assert view["values"]["k"]["file"] == "a.properties"


def test_the_view_does_not_depend_on_the_order_files_were_walked(tmp_path: Path):
    files = {"a.properties": "k=1\n", "b.properties": "k=2\n"}
    forward = view_of(tmp_path / "f", files)

    ctx = ctx_for(tmp_path / "r", files)
    ctx.files["svc"].reverse()
    P.PropertiesExtractor().extract(ctx)
    assert ctx.cache[P.CACHE_KEY]["svc"] == forward


# ── conflicts are surfaced, never silently resolved ───────────────────────────


def test_identical_values_across_spellings_are_not_a_conflict(tmp_path: Path):
    """The fixture's D2 case. Three spellings agreeing is exactly what relaxed binding is
    for; reporting it would train a reader to ignore the report."""
    _, res = run(tmp_path, {"a.properties": "orders.publish.topic=t\nORDERS_PUBLISH_TOPIC=t\n"})
    assert [r for r in res.unresolved if r["kind"] == "key-conflict"] == []


def test_spellings_that_disagree_are_reported(tmp_path: Path):
    view, res = run(tmp_path, {"a.properties": "orders.publish.topic=one\n"
                                               "ORDERS_PUBLISH_TOPIC=two\n"})
    row = next(r for r in res.unresolved if r["kind"] == "key-conflict")
    assert row["values"] == ["one", "two"]
    assert "deployment fact" in row["why"]


def test_a_disagreeing_key_is_still_resolved_rather_than_dropped(tmp_path: Path):
    """One of them IS what the running system uses. Dropping the key loses a real coupling to
    avoid admitting an ambiguity."""
    view, _ = run(tmp_path, {"a.properties": "k=one\nK=two\n"})
    assert view["values"]["k"]["value"] == "two"


# ── nodes, edges, and the cache ───────────────────────────────────────────────


def test_one_config_node_per_file_with_a_containment_edge(tmp_path: Path):
    _, res = run(tmp_path, {"a.properties": "k=1\n", "sub/b.properties": "j=2\n"})
    kinds = {n.props["kind"] for n in res.nodes}
    assert kinds == {"properties"}
    assert sorted(n.props["path"] for n in res.nodes) == ["a.properties", "sub/b.properties"]
    assert {e.type for e in res.edges} == {"DG_IN_REPO"}
    assert {e.dst for e in res.edges} == {"repo:svc"}


def test_the_view_lands_in_the_shared_cache_under_the_component_alias(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"a.properties": "k=1\n"}, alias="orders-api")
    P.PropertiesExtractor().extract(ctx)
    assert set(ctx.cache) == {P.CACHE_KEY}
    assert set(ctx.cache[P.CACHE_KEY]) == {"orders-api"}


def test_the_extractor_reads_exactly_the_files_that_activated_it(tmp_path: Path):
    """`ctx.inputs` is the activation set. An extractor that re-globbed with a slightly
    different pattern would activate on one set of files and read another, and the gap would
    never be reported."""
    ctx = ctx_for(tmp_path, {"a.properties": "k=1\n"})
    (tmp_path / "svc" / "unlisted.properties").write_text("j=2\n", encoding="utf-8")
    P.PropertiesExtractor().extract(ctx)
    assert "j" not in ctx.cache[P.CACHE_KEY]["svc"]["values"]


def test_several_components_each_get_their_own_view(tmp_path: Path):
    a = ctx_for(tmp_path / "a", {"x.properties": "k=1\n"}, alias="one")
    b_root = tmp_path / "b" / "two"
    (b_root).mkdir(parents=True)
    (b_root / "x.properties").write_text("k=2\n", encoding="utf-8")
    a.repos.append(RepoRef(alias="two", path=b_root))
    a.files["two"] = [b_root / "x.properties"]

    P.PropertiesExtractor().extract(a)
    cache = a.cache[P.CACHE_KEY]
    assert cache["one"]["values"]["k"]["value"] == "1"
    assert cache["two"]["values"]["k"]["value"] == "2"


def test_a_component_with_no_declarations_still_gets_an_entry(tmp_path: Path):
    """An empty view and a missing view are different facts: one says 'read, nothing there',
    the other says 'never read'. A consumer must be able to tell them apart."""
    view = view_of(tmp_path, {"a.properties": "# only a comment\n"})
    assert view == {"values": {}, "unresolved": {}}


# ── registration ──────────────────────────────────────────────────────────────


def test_the_package_registers_through_the_real_pipeline():
    regs = {r.name: r for r in B.discover_extractors()}
    assert "properties" in regs
    assert regs["properties"].layer == "stack"
    assert regs["properties"].globs == ("*.properties",)


# ── against the committed fixture, whose answer key cites line numbers ────────


@pytest.fixture(scope="module")
def fixture_cache():
    si = B.load_installer()
    ws = si.read_workspace(FIXTURE / "workspace.toml")
    comps = si.discover_components(ws)["components"]
    regs = [r for r in B.discover_extractors() if r.name == "properties"]
    act = B.activate(regs, comps, ws["root"])
    ctx = ExtractContext(
        repos=B.repo_refs(comps, ws["root"], act.by_extractor["properties"]), run_id="t",
        files={c: act.files.get(("properties", c), []) for c in act.by_extractor["properties"]})
    res = regs[0].extractor.extract(ctx)
    return ctx.cache[P.CACHE_KEY], res


def test_fixture_the_published_topic_resolves_at_the_line_the_answer_key_cites(fixture_cache):
    cache, _ = fixture_cache
    hit = P.lookup(cache["orders-api"], "orders.publish.topic")
    assert hit["value"] == "order-events-v1"
    assert hit["file"].endswith("application.properties")
    assert hit["line"] == 5, "EXPECTED.md hop 3 cites application.properties:5"


def test_fixture_the_consumed_topic_resolves_to_the_same_value(fixture_cache):
    cache, _ = fixture_cache
    hit = P.lookup(cache["orders-worker"], "orders.consume.topic")
    assert hit["value"] == "order-events-v1"
    assert hit["line"] == 5, "EXPECTED.md hop 4 cites application.properties:5"


def test_fixture_the_producer_and_consumer_key_names_deliberately_differ(fixture_cache):
    """The answer key's own point: an extractor pairing producers to consumers BY KEY NAME
    finds nothing here. Pairing must be on the resolved value."""
    cache, _ = fixture_cache
    assert "orders.publish.topic" not in cache["orders-worker"]["values"]
    assert "orders.consume.topic" not in cache["orders-api"]["values"]


def test_fixture_d2_three_spellings_are_one_key(fixture_cache):
    cache, _ = fixture_cache
    entry = cache["orders-api"]["values"]["orders.publish.topic"]
    assert sorted(entry["spellings"]) == [
        "ORDERS_PUBLISH_TOPIC", "orders-publish-topic", "orders.publish.topic"]
    assert [entry["spellings"][k]["line"] for k in
            ("orders.publish.topic", "ORDERS_PUBLISH_TOPIC", "orders-publish-topic")] == [5, 9, 10]


def test_fixture_d8_is_the_only_thing_this_extractor_cannot_resolve(fixture_cache):
    """`EXPECTED.md` expects three entries in `unresolved` overall — D6, D7 and D8 — and
    exactly one of them is this extractor's. A fourth here would mean a decoy landed by
    accident rather than by design."""
    _, res = fixture_cache
    assert len(res.unresolved) == 1
    row = res.unresolved[0]
    assert (row["component"], row["key"], row["line"]) == (
        "orders-api", "orders.audit.topic", 14)
    assert row["missing"] == ["AUDIT_TOPIC"]


def test_fixture_every_component_declares_its_context_path(fixture_cache):
    """Hop 2 concatenates the context path onto a controller mapping, so its absence would
    silently shorten every endpoint id rather than fail."""
    cache, _ = fixture_cache
    assert P.lookup(cache["orders-api"], "server.servlet.context-path")["value"] == "/orders"
    assert P.lookup(cache["orders-worker"], "server.servlet.context-path")["value"] \
        == "/orders-worker"


def test_fixture_the_inert_component_resolves_and_shares_nothing(fixture_cache):
    """P5, at this layer: the negative control has real configuration, and none of its values
    coincide with anything another component declares."""
    cache, _ = fixture_cache
    inert = {v["value"] for v in cache["legacy-reports"]["values"].values()}
    others = {v["value"] for a, view in cache.items() if a != "legacy-reports"
              for v in view["values"].values()}
    assert inert & others == set()
