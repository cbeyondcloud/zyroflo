"""Build manifests, migrations, deployment packages and the user interface.

Two more chain hops (1 and 6), the cross-component dependency edges, the collision D4 sets
up, and D1 — whose whole claim is that two spellings of one path are ONE node.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.lib import ids, srcfacts  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402  (used by fixture tests)
from tools.graph.extractors import frontend, helm, maven, sqlmigrations  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


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


def edges(res, type_):
    return [e for e in res.edges if e.type == type_]


POM = """<project>
  <groupId>com.example.orderflow</groupId>
  <artifactId>orders-api</artifactId>
  <version>2.1.0</version>
  <dependencies>
    <dependency>
      <groupId>com.example.orderflow</groupId>
      <artifactId>orders-shared</artifactId>
      <version>2.1.0</version>
    </dependency>
  </dependencies>
</project>
"""


# ── build manifests ───────────────────────────────────────────────────────────


def test_a_dependency_becomes_an_edge_carrying_its_version(tmp_path: Path):
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": POM}))
    e = edges(res, "DG_DEPENDS_ON")[0]
    assert e.dst == ids.module_id("com.example.orderflow", "orders-shared")
    assert e.props["version"] == "2.1.0"
    assert e.line == 6, "the line the <dependency> block opens on"


def test_a_manifest_never_depends_on_its_own_coordinates(tmp_path: Path):
    """The trap this extractor exists to avoid, and it has been paid for once already: match
    artifact names anywhere in the file and every component reports itself as its own
    dependency, so the shared-library question returns the component you started from.

    The declaration below is the real shape — a manifest listing its own artifact, which
    happens whenever a module is both published and consumed within one build. A test using a
    manifest that never mentions itself passes with the exclusion removed, and proves nothing.
    """
    pom = POM.replace("<artifactId>orders-shared</artifactId>",
                      "<artifactId>orders-api</artifactId>")
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    assert edges(res, "DG_DEPENDS_ON") == []


def test_a_parents_coordinates_are_not_a_dependency(tmp_path: Path):
    pom = """<project>
      <parent>
        <groupId>com.example</groupId>
        <artifactId>platform-parent</artifactId>
        <version>3.0.0</version>
      </parent>
      <artifactId>orders-api</artifactId>
      <dependencies>
        <dependency>
          <groupId>com.example</groupId>
          <artifactId>platform-parent</artifactId>
        </dependency>
      </dependencies>
    </project>"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    assert edges(res, "DG_DEPENDS_ON") == []


def test_an_aggregators_own_modules_are_not_dependencies(tmp_path: Path):
    """A parent names its children before their manifests are read. On a repository whose
    child manifests fall outside a scan, those names are the ONLY evidence that a
    'dependency' is really a sibling of the same build."""
    pom = """<project>
      <groupId>com.example</groupId>
      <artifactId>agg</artifactId>
      <modules><module>core</module><module>web</module></modules>
      <dependencies>
        <dependency><groupId>com.example</groupId><artifactId>core</artifactId></dependency>
      </dependencies>
    </project>"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    assert edges(res, "DG_DEPENDS_ON") == []


def test_a_component_records_what_it_publishes(tmp_path: Path):
    """Without it, a dependency edge points at a module node belonging to nobody, and 'who
    rebuilds when this library changes' stops one hop short of the answer."""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": POM}))
    e = edges(res, "DG_BUILT_FROM")[0]
    assert e.src == ids.component_service_id("svc")
    assert e.dst == ids.module_id("com.example.orderflow", "orders-api")


def test_an_inherited_version_is_labelled_not_reported(tmp_path: Path):
    """A dependency with no version is managed by a parent or a bill of materials. Normal —
    and reporting every one of them would bury the findings that matter."""
    pom = ("<project><groupId>g</groupId><artifactId>a</artifactId><dependencies>"
           "<dependency><groupId>g2</groupId><artifactId>b</artifactId></dependency>"
           "</dependencies></project>")
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    assert edges(res, "DG_DEPENDS_ON")[0].props["version_source"] == "inherited"
    assert res.unresolved == []


def test_scope_and_optionality_are_carried(tmp_path: Path):
    pom = ("<project><groupId>g</groupId><artifactId>a</artifactId><dependencies><dependency>"
           "<groupId>g2</groupId><artifactId>b</artifactId><scope>test</scope>"
           "<optional>true</optional></dependency></dependencies></project>")
    e = edges(maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom})),
              "DG_DEPENDS_ON")[0]
    assert (e.props["scope"], e.props["optional"]) == ("test", True)


def test_a_namespaced_manifest_is_read_the_same(tmp_path: Path):
    """A declared namespace is what a strict reader must be taught about and a tag pattern
    does not care about. Real manifests carry one; the fixture's does not."""
    pom = POM.replace("<project>", '<project xmlns="http://maven.apache.org/POM/4.0.0">')
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    assert len(edges(res, "DG_DEPENDS_ON")) == 1


# ── migrations: ownership is creation ─────────────────────────────────────────


def test_a_created_table_is_owned_by_the_component_that_creates_it(tmp_path: Path):
    sql = "-- header\nCREATE TABLE order_events (\n  id BIGSERIAL\n);\n"
    res = sqlmigrations.MigrationsExtractor().extract(ctx_for(tmp_path, {"V1__x.sql": sql}))
    e = edges(res, "DG_OWNS_TABLE")[0]
    assert e.dst == ids.table_id("order_events")
    assert e.line == 2


@pytest.mark.parametrize("sql, expected", [
    ("CREATE TABLE t (a int);", ["table:t"]),
    ("create table if not exists t (a int);", ["table:t"]),
    ("CREATE TABLE public.t (a int);", ["table:public.t"]),
    ('CREATE TABLE "t" (a int);', ["table:t"]),
    ("CREATE UNLOGGED TABLE t (a int);", ["table:t"]),
    ("CREATE TABLE a (x int); CREATE TABLE b (y int);", ["table:a", "table:b"]),
])
def test_the_shapes_a_creation_can_take(tmp_path: Path, sql, expected):
    res = sqlmigrations.MigrationsExtractor().extract(ctx_for(tmp_path, {"V1__x.sql": sql}))
    assert sorted(e.dst for e in edges(res, "DG_OWNS_TABLE")) == expected


def test_a_commented_out_creation_is_not_a_creation(tmp_path: Path):
    sql = "-- CREATE TABLE ghost (a int);\nCREATE TABLE real_one (a int);\n"
    res = sqlmigrations.MigrationsExtractor().extract(ctx_for(tmp_path, {"V1__x.sql": sql}))
    assert [e.dst for e in edges(res, "DG_OWNS_TABLE")] == ["table:real_one"]


def test_removing_comments_does_not_shift_line_numbers(tmp_path: Path):
    """Line numbers are part of every edge's claim, so comment bodies are blanked rather than
    deleted — deleting them would move every statement after the first one."""
    sql = "/* a\n   multi-line\n   comment */\nCREATE TABLE t (a int);\n"
    res = sqlmigrations.MigrationsExtractor().extract(ctx_for(tmp_path, {"V1__x.sql": sql}))
    assert edges(res, "DG_OWNS_TABLE")[0].line == 4


def test_a_construct_this_does_not_model_is_skipped(tmp_path: Path):
    """`unresolved` means 'a reference I could not follow'. Widening it to 'a statement I do
    not model' would drown the references in noise."""
    sql = "CREATE INDEX idx ON t (a);\nCREATE VIEW v AS SELECT 1;\n"
    res = sqlmigrations.MigrationsExtractor().extract(ctx_for(tmp_path, {"V1__x.sql": sql}))
    assert res.edges == [] and res.unresolved == []


def test_using_a_table_is_not_owning_it(tmp_path: Path):
    """Treat a mention as ownership and every reader becomes an owner, so the blast radius of
    a schema change names everyone and distinguishes nobody."""
    sql = "INSERT INTO order_events VALUES (1);\nALTER TABLE order_events ADD c int;\n"
    res = sqlmigrations.MigrationsExtractor().extract(ctx_for(tmp_path, {"V1__x.sql": sql}))
    assert res.edges == []


# ── deployment packages ───────────────────────────────────────────────────────


VALUES = "image:\n  repository: reg/example/orders-api\n  tag: 2.1.0\nservice:\n  name: orders-api\n  port: 8080\n"


def test_a_package_declares_the_service_it_deploys(tmp_path: Path):
    res = helm.DeploymentsExtractor().extract(ctx_for(tmp_path, {"charts/x/values.yaml": VALUES}))
    e = edges(res, "DG_DEPLOYS")[0]
    assert e.dst == ids.component_service_id("orders-api")
    assert e.line == 5, "the line that NAMES the service, not the block containing it"


def test_the_image_path_names_the_service_when_nothing_else_does(tmp_path: Path):
    values = "image:\n  repository: reg/example/orders-api\n  tag: 2.1.0\n"
    res = helm.DeploymentsExtractor().extract(ctx_for(tmp_path, {"values.yaml": values}))
    e = edges(res, "DG_DEPLOYS")[0]
    assert e.dst == ids.component_service_id("orders-api")
    assert e.props["declared_by"] == "image.repository"


def test_an_explicit_name_beats_an_image_path(tmp_path: Path):
    """The image is a publishing detail; the name is what other services address."""
    values = "image:\n  repository: reg/team/legacy-name\nservice:\n  name: orders-api\n"
    res = helm.DeploymentsExtractor().extract(ctx_for(tmp_path, {"values.yaml": values}))
    assert edges(res, "DG_DEPLOYS")[0].props["service"] == "orders-api"


def test_a_package_naming_no_service_is_reported_never_invented(tmp_path: Path):
    """Naming it after its directory would fabricate identity, and nothing downstream could
    tell a fabricated service from a declared one."""
    res = helm.DeploymentsExtractor().extract(ctx_for(tmp_path, {"values.yaml": "replicas: 2\n"}))
    assert res.edges == []
    assert [r["kind"] for r in res.unresolved] == ["unnamed-deployment"]


def test_an_unparsable_package_is_reported(tmp_path: Path):
    bad = "image:\n  repository: [unclosed\n"
    res = helm.DeploymentsExtractor().extract(ctx_for(tmp_path, {"values.yaml": bad}))
    assert [r["kind"] for r in res.unresolved] == ["unparsable"]


def test_two_packages_claiming_one_service_are_recorded_identically(tmp_path: Path):
    """D4's setup. Both trees are equally real, and which one configuration and UI links
    should attach to is a statement about intent that neither file contains. Preferring one
    here would attach real edges to the wrong service while looking entirely correct."""
    second = VALUES.replace("reg/example", "reg/legacy")
    ctx = ctx_for(tmp_path, {"charts/x/values.yaml": VALUES, "legacy/x/values.yaml": second})
    res = helm.DeploymentsExtractor().extract(ctx)
    claims = edges(res, "DG_DEPLOYS")
    assert len(claims) == 2
    assert {e.dst for e in claims} == {ids.component_service_id("orders-api")}
    assert {e.confidence for e in claims} == {1.0}, "no preference is expressed here"
    assert len({e.src for e in claims}) == 2, "and each claim keeps its own identity"


CONFIGMAP = """apiVersion: v1
kind: ConfigMap
metadata:
  name: orderflow-config
data:
  ORDERS_API_HOST: http://orders-api:8080
  INVENTORY_SERVICE_HOST: http://inventory-api:8080
"""


def test_a_config_map_publishes_its_keys_and_their_lines(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"templates/cm.yaml": CONFIGMAP})
    res = helm.ConfigMapsExtractor().extract(ctx)
    by_dst = {e.dst: e for e in edges(res, "DG_CONFIGURES")}
    assert set(by_dst) == {ids.env_key_id("ORDERS_API_HOST"),
                           ids.env_key_id("INVENTORY_SERVICE_HOST")}
    assert by_dst[ids.env_key_id("ORDERS_API_HOST")].line == 6


def test_the_host_map_reaches_the_shared_cache(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"templates/cm.yaml": CONFIGMAP})
    helm.ConfigMapsExtractor().extract(ctx)
    entries = ctx.cache[helm.CACHE_KEY]["svc"]
    assert entries["orders.api.host"]["value"] == "http://orders-api:8080"


def test_a_template_that_is_not_a_config_map_is_ignored(tmp_path: Path):
    doc = "apiVersion: apps/v1\nkind: Deployment\ndata:\n  X: y\n"
    res = helm.ConfigMapsExtractor().extract(ctx_for(tmp_path, {"templates/d.yaml": doc}))
    assert res.edges == []


# ── the user interface ────────────────────────────────────────────────────────


URLS_JS = ("export const URLS = {\n  ORDERS_LIST: '/orders/v1/list',\n"
           "  ORDERS_DETAIL: '/orders/v1/detail/{orderId}',\n};\n")
INLINE_JSX = ("import { x } from '../config/urls';\n"
              "const detailPath = '/orders/v1/detail/{id}';\n")

pytest_js = pytest.mark.skipif(not srcfacts.available("javascript"),
                               reason="no parser installed for this language")


@pytest_js
def test_the_url_table_is_the_highest_confidence_source(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"src/config/urls.js": URLS_JS})
    e = edges(frontend.UrlsExtractor().extract(ctx), "DG_CALLS")[0]
    assert (e.confidence, e.props["via"]) == (0.95, "urls-central")


@pytest_js
def test_a_path_written_inline_is_real_and_ranked_lower(tmp_path: Path):
    """Still a dependency, and also where typos, stale copies and half-finished migrations
    live. Collapsing the difference into found/not-found throws the useful half away."""
    ctx = ctx_for(tmp_path, {"src/components/D.jsx": INLINE_JSX})
    e = edges(frontend.UrlsExtractor().extract(ctx), "DG_CALLS")[0]
    assert (e.confidence, e.props["via"]) == (0.8, "url-inline")


@pytest_js
def test_d1_two_spellings_of_one_path_are_one_endpoint(tmp_path: Path):
    """The decoy's own words: two nodes would split every impact query on this endpoint,
    while looking from outside like a component calling two different endpoints."""
    ctx = ctx_for(tmp_path, {"src/config/urls.js": URLS_JS,
                             "src/components/D.jsx": INLINE_JSX})
    res = frontend.UrlsExtractor().extract(ctx)
    detail = [e for e in edges(res, "DG_CALLS") if "detail" in e.dst]
    assert {e.dst for e in detail} == {"ep:/orders/v1/detail/{}"}
    assert len(detail) == 2, "one node, and both declarations still cited"


@pytest_js
def test_an_import_specifier_is_not_an_endpoint(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"src/components/D.jsx": "import { x } from '../config/urls';\n"})
    assert frontend.UrlsExtractor().extract(ctx).edges == []


@pytest_js
def test_a_replacement_token_is_not_an_endpoint(tmp_path: Path):
    src = "const p = '/a/b'.replace('{id}', id);\n"
    res = frontend.UrlsExtractor().extract(ctx_for(tmp_path, {"src/x.js": src}))
    assert [e.dst for e in edges(res, "DG_CALLS")] == ["ep:/a/b"]


@pytest_js
def test_the_interface_is_named_by_its_package(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"package.json": '{"name": "web-console"}',
                             "src/config/urls.js": URLS_JS})
    res = frontend.UrlsExtractor().extract(ctx)
    assert edges(res, "DG_CALLS")[0].src == ids.ui_id("web-console", "svc")


@pytest_js
def test_an_unnamed_package_falls_back_to_the_component(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"src/config/urls.js": URLS_JS})
    assert edges(frontend.UrlsExtractor().extract(ctx), "DG_CALLS")[0].src \
        == ids.ui_id("svc", "svc")


@pytest_js
def test_an_interface_node_appears_only_when_something_was_read(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"src/x.js": "const a = 1;\n"})
    res = frontend.UrlsExtractor().extract(ctx)
    assert [n for n in res.nodes if n.type == "ui"] == []


@pytest_js
def test_a_test_source_is_left_to_the_tests_extractor(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"src/x.test.js": URLS_JS})
    assert frontend.UrlsExtractor().extract(ctx).edges == []


def test_a_language_with_no_grammar_is_reported_per_component(tmp_path: Path, monkeypatch):
    """A user interface that silently produces no edges is indistinguishable from one that
    calls nothing — which is the failure this whole project keeps rediscovering.

    **The missing grammar is forced, not found, and the first version's was found.** That one
    handed the extractor a `.ts` file and passed here — because `tree-sitter-language-pack`
    downloads prebuilt parsers and publishes none for `windows-x86_64`, so typescript is
    genuinely absent on this developer's machine. On the Linux runner the pack supplies it,
    the file parsed correctly, `unresolved` was empty, and the test failed *for the harness
    doing the right thing*.

    The subject here is the reporting path, and it must not depend on which optional grammars
    the host happens to have — that is a fact about the machine, and asserting it through a
    real language makes this test mean different things on different platforms. Forcing the
    condition also widens it: it now covers every language rather than whichever one this
    week's wheels omit.
    """
    monkeypatch.setattr(srcfacts, "available", lambda language: False)
    ctx = ctx_for(tmp_path, {"src/config/urls.js": URLS_JS})
    res = frontend.UrlsExtractor().extract(ctx)
    assert [r["kind"] for r in res.unresolved] == ["grammar-unavailable"]
    # The point of the row is that the loss is visible. An extractor that reported the gap
    # AND emitted edges from a file it never parsed would satisfy the assertion above while
    # inventing the very facts the row says are missing.
    assert res.edges == []


@pytest.mark.parametrize("rel, central", [
    ("src/config/urls.js", True),
    ("src/urls.js", True),
    ("src/api.js", True),
    ("src/constants/x.js", True),
    ("src/components/OrderDetail.jsx", False),
])
def test_what_counts_as_the_url_table(rel, central):
    assert frontend.is_central(rel) is central


# ── registration ──────────────────────────────────────────────────────────────


def test_the_new_extractors_register():
    names = {r.name for r in B.discover_extractors()}
    assert {"maven-modules", "sql-migrations", "helm-deployments",
            "helm-configmaps", "frontend-urls"} <= names


# ── against the committed fixture ─────────────────────────────────────────────


@pytest.fixture(scope="module")
def fixture_edges(tmp_path_factory):
    import json

    out = tmp_path_factory.mktemp("graph")
    B.build(FIXTURE / "workspace.toml", out, run_id="fixture01")
    return [json.loads(line)
            for line in (out / "graph.jsonl").read_text(encoding="utf-8").splitlines()
            if '"kind": "edge"' in line]


def of_type(fixture_edges, t):
    return [e for e in fixture_edges if e["type"] == t]


@pytest_js
def test_fixture_hop1_the_url_table_calls_the_endpoint(fixture_edges):
    hits = [e for e in of_type(fixture_edges, "DG_CALLS")
            if e["dst"] == "ep:/orders/v1/list" and e["src"].startswith("ui:")]
    assert len(hits) == 1
    assert hits[0]["source_file"].endswith("config/urls.js")
    assert hits[0]["line"] == 4, "EXPECTED.md hop 1 cites urls.js:4"
    assert hits[0]["confidence"] == 0.95


@pytest_js
def test_fixture_d1_both_declarations_reach_one_endpoint_node(fixture_edges):
    hits = sorted((e["source_file"].rsplit("/", 1)[-1], e["line"])
                  for e in of_type(fixture_edges, "DG_CALLS")
                  if e["dst"] == "ep:/orders/v1/detail/{}" and e["src"].startswith("ui:"))
    assert hits == [("OrderDetail.jsx", 4), ("urls.js", 5)], \
        "EXPECTED.md cites urls.js:5 AND OrderDetail.jsx:4"


def test_fixture_hop6_the_migration_owns_the_table(fixture_edges):
    e = next(e for e in of_type(fixture_edges, "DG_OWNS_TABLE"))
    assert e["src"] == ids.component_service_id("orders-worker")
    assert e["dst"] == "table:order_events"
    assert e["source_file"].endswith("V1__create_order_events.sql")
    assert e["line"] == 2, "EXPECTED.md hop 6 cites V1__create_order_events.sql:2"


def test_fixture_both_services_depend_on_the_shared_library(fixture_edges):
    deps = {e["src"]: e for e in of_type(fixture_edges, "DG_DEPENDS_ON")}
    shared = ids.module_id("com.example.orderflow", "orders-shared")
    for alias in ("orders-api", "orders-worker"):
        e = deps[ids.component_service_id(alias)]
        assert e["dst"] == shared and e["props"]["version"] == "2.1.0"
        assert e["line"] == 6, "EXPECTED.md cites pom.xml:6-9"


def test_fixture_no_component_depends_on_itself(fixture_edges):
    for e in of_type(fixture_edges, "DG_DEPENDS_ON"):
        assert e["props"]["version"] and e["src"] != e["dst"]
        assert e["dst"].rsplit(":", 1)[-1] not in e["src"]


def test_fixture_d4_two_packages_claim_one_service_at_the_lines_cited(fixture_edges):
    claims = of_type(fixture_edges, "DG_DEPLOYS")
    assert {e["dst"] for e in claims} == {ids.component_service_id("orders-api")}
    assert sorted((e["source_file"], e["line"]) for e in claims) == [
        ("charts/orderflow/values.yaml", 6),
        ("legacy/orders-api/values.yaml", 9),
    ]


def test_fixture_extraction_alone_expresses_no_preference(tmp_path: Path):
    """The resolution comes from the link pass reading the workspace's own declaration, never
    from extraction — so run the extractor by itself and it must still be undecided.

    Asserting this on the BUILT graph would only pin whichever ran last. Asserting it here
    pins the thing that matters: that this extractor cannot decide, and does not try.
    """
    si = B.load_installer()
    ws = si.read_workspace(FIXTURE / "workspace.toml")
    comps = si.discover_components(ws)["components"]
    regs = [r for r in B.discover_extractors() if r.name == "helm-deployments"]
    act = B.activate(regs, comps, ws["root"])
    claimed = act.by_extractor["helm-deployments"]
    ctx = ExtractContext(repos=B.repo_refs(comps, ws["root"], claimed), run_id="t",
                         files={c: act.files.get(("helm-deployments", c), []) for c in claimed})

    claims = [e for e in regs[0].extractor.extract(ctx).edges if e.type == "DG_DEPLOYS"]
    assert len(claims) == 2
    assert {e.confidence for e in claims} == {1.0}
    assert {e.canonical for e in claims} == {True}


def test_fixture_there_are_exactly_three_endpoints(fixture_edges):
    """Three real endpoints and no phantoms. Every extra one would be an entity nothing
    serves, arriving in blast radiuses as a dependency nobody can find."""
    seen = {e["dst"] for e in fixture_edges if e["dst"].startswith("ep:")}
    assert seen == {"ep:/orders/v1/list", "ep:/orders/v1/detail/{}",
                    "ep:/inventory/v1/reserve"}


def test_fixture_the_inert_component_still_reaches_nothing(fixture_edges):
    """P5 again, now that build manifests are read: legacy-reports shares a group with every
    other module, and a group is not a dependency."""
    inert = ids.component_service_id("legacy-reports")
    for e in fixture_edges:
        if e["src"] != inert:
            continue
        assert "legacy-reports" in e["dst"], f"inert component reached {e['dst']}"


# ── item 81: the line-citation defect the second fixture found in compose ─────


def test_a_configmap_key_is_not_matched_inside_an_earlier_value(tmp_path: Path):
    """REGRESSION. `props.find_line` is a substring search — right for a properties file,
    where a key starts its line by construction, and wrong for YAML, where a key's name
    routinely appears inside a value above it.

    The second fixture caught this in the compose reader (a service `db` reported eleven
    lines early, because `postgresql://db:5432/...` sits above it). This reader had the
    identical defect and kept it for an item, because a defect fixed outside the item that
    found it arrives without a regression test. Here is the test.
    """
    doc = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: hosts
data:
  DSN: postgresql://db:5432/app
  db: postgres-svc
"""
    res = helm.ConfigMapsExtractor().extract(ctx_for(tmp_path, {"templates/cm.yaml": doc}))
    lines = {e.props["declared_as"]: e.line for e in res.edges if e.type == "DG_CONFIGURES"}
    assert lines["db"] == 7, (
        "`db:` appears INSIDE the value on line 6 (`postgresql://db:5432/...`) before it is "
        "declared on line 7 — plausible, otherwise-correct, invisible to anyone not counting")
    assert lines["DSN"] == 6


def test_two_configmaps_declaring_one_key_each_get_their_own_line(tmp_path: Path):
    """Scanning forward per key rather than from the top each time. Without the cursor both
    resolve to the first occurrence, and one of the two citations is silently wrong."""
    doc = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: a
data:
  SHARED: one
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: b
data:
  SHARED: two
"""
    res = helm.ConfigMapsExtractor().extract(ctx_for(tmp_path, {"templates/cm.yaml": doc}))
    assert sorted(e.line for e in res.edges if e.type == "DG_CONFIGURES") == [6, 13]


def test_a_key_that_is_absent_cites_no_line_rather_than_guessing(tmp_path: Path):
    """Line 0 is the "no line" convention across the extractors. A wrong number would be
    worse than none, because a reader can check nothing against it."""
    from tools.graph.extractors import _yaml
    assert _yaml.key_line("a: 1\nb: 2\n", "zzz") == (0, 0)


def test_both_yaml_readers_share_one_line_finder(tmp_path: Path):
    """Two implementations of "where is this key declared" is how one of them silently
    regresses while the other stays right, and nothing compares them."""
    from tools.graph.extractors import _yaml, compose
    assert compose.key_line is _yaml.key_line


# ── item 81b: the host map must respect [[canonical]], as compose now does ────

CM = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: hosts
data:
  ORDERS_HOST: %s
"""


def ctx_charts(tmp_path: Path, files: dict[str, str], canonical: list[dict]):
    ctx = ctx_for(tmp_path, files)
    ctx.config = {"canonical": canonical}
    return ctx


CLAIM = [{"service": "orders", "owner": "svc", "path": "charts/orders/values.yaml"}]


def test_a_superseded_chart_tree_cannot_overwrite_the_owners_host(tmp_path: Path):
    """REGRESSION. The host map was a plain last-wins assignment, so `legacy/` sorting after
    `charts/` supplied live addresses from a tree the workspace had already superseded — the
    same defect the second fixture found in the compose reader, one deployment tool over."""
    ctx = ctx_charts(tmp_path, {
        "charts/orders/templates/cm.yaml": CM % "orders-v2:8080",
        "legacy/orders/templates/cm.yaml": CM % "orders-v1:8080"}, CLAIM)
    helm.ConfigMapsExtractor().extract(ctx)
    assert ctx.cache[helm.CACHE_KEY]["svc"]["orders.host"]["value"] == "orders-v2:8080"


def test_the_superseded_host_is_reported_rather_than_dropped(tmp_path: Path):
    ctx = ctx_charts(tmp_path, {
        "charts/orders/templates/cm.yaml": CM % "orders-v2:8080",
        "legacy/orders/templates/cm.yaml": CM % "orders-v1:8080"}, CLAIM)
    res = helm.ConfigMapsExtractor().extract(ctx)
    rows = [r for r in res.unresolved if r["kind"] == "superseded-declaration"]
    assert [r["value"] for r in rows] == ["orders-v1:8080"]


def test_a_claim_naming_values_yaml_covers_the_templates_beside_it(tmp_path: Path):
    """The semantics this item had to decide rather than assume. A chart declares its service
    in values.yaml while publishing its host map from templates/, so a claim naming the
    values file has to reach its siblings or it reaches nothing at all."""
    from tools.graph.extractors import _canonical
    c = CLAIM[0]
    assert _canonical.covers(c, "svc", "charts/orders/templates/cm.yaml")
    assert _canonical.covers(c, "svc", "charts/orders/values.yaml")
    assert not _canonical.covers(c, "svc", "legacy/orders/templates/cm.yaml")


def test_a_bare_filename_claim_does_not_swallow_its_siblings(tmp_path: Path):
    """The asymmetry that keeps the compose reader intact. `docker-compose.yml` has no
    directory part, and treating its empty parent as a prefix would match EVERY path —
    making the `legacy/` file canonical too and silently inverting the decision recorded."""
    from tools.graph.extractors import _canonical
    c = {"service": "api", "owner": "svc", "path": "docker-compose.yml"}
    assert _canonical.covers(c, "svc", "docker-compose.yml")
    assert not _canonical.covers(c, "svc", "legacy/docker-compose.yml")


def test_with_no_claim_nothing_is_ranked(tmp_path: Path):
    """A workspace fills in [[canonical]] only for collisions the build reports. Until then
    the existing merge order stands, unchanged."""
    from tools.graph.extractors import _canonical
    assert _canonical.is_owner({}, "svc", "anything/at/all.yaml")
