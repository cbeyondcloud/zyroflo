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
from tools.graph.extractors import (  # noqa: E402
    compose, env_keys, frontend, gradle, helm, http_calls, http_routes, kafka_python,
    maven, npm, openapi, properties, sqlmigrations)
from tools.graph.profiles import orderflow  # noqa: E402

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


def test_an_unreadable_migration_is_recorded_not_silently_skipped(tmp_path: Path):
    """could-not-read a source file must surface as an unresolved row, not vanish into
    "this file creates no tables".

    A .sql migration whose bytes cannot be read used to fold to "" (repo_walk.read_text
    swallowed the OSError) and then parse to zero tables -- indistinguishable from a migration
    that genuinely creates nothing, so the table it owns went missing from the graph with no
    record of the loss. read_text_reporting now NAMES the failure and the extractor records a
    row of kind "unreadable" the reader can act on. The input is a DIRECTORY at the .sql path,
    which makes open() raise OSError on both Windows and POSIX without depending on chmod.
    """
    root = tmp_path / "svc"
    root.mkdir()
    unreadable = root / "V1__orders.sql"
    unreadable.mkdir()
    ctx = ExtractContext(repos=[RepoRef(alias="svc", path=root)], run_id="r",
                         files={"svc": [unreadable]})

    res = sqlmigrations.MigrationsExtractor().extract(ctx)

    rows = [r for r in res.unresolved if r.get("kind") == "unreadable-file"]
    assert len(rows) == 1, f"an unreadable migration must be recorded once, got {res.unresolved}"
    assert rows[0]["source_file"].endswith("V1__orders.sql")
    assert rows[0]["extractor"] and rows[0]["component"] == "svc" and rows[0]["why"]


@pytest.mark.parametrize("make_extractor, fname, name", [
    (sqlmigrations.MigrationsExtractor, "V1__x.sql", "sql-migrations"),
    (http_calls.HttpCallsExtractor, "client.py", "http-calls"),
    (kafka_python.PyConsumersExtractor, "consumer.py", "kafka-consumers-py"),
    (gradle.GradleModulesExtractor, "build.gradle", "gradle-modules"),
    (maven.ModulesExtractor, "pom.xml", "maven-modules"),
    (npm.NpmModulesExtractor, "package.json", "npm-modules"),
    (env_keys.EnvKeysExtractor, "app.py", "env-keys"),
    (http_routes.HttpRoutesExtractor, "routes.py", "http-routes"),
    (openapi.OpenApiExtractor, "api.yaml", "openapi-specs"),
    (compose.ComposeExtractor, "compose.yml", "compose-services"),
    (properties.PropertiesExtractor, "app.properties", "properties"),
    (helm.DeploymentsExtractor, "values.yaml", "helm-deployments"),
    (helm.ConfigMapsExtractor, "templates/cm.yaml", "helm-configmaps"),
    (orderflow.WriteMappingsExtractor, "write-mapping.yml", "orderflow-write-mappings"),
], ids=["sql-migrations", "http-calls", "kafka-python", "gradle", "maven", "npm", "env-keys",
        "http-routes", "openapi", "compose", "properties", "helm-deployments",
        "helm-configmaps", "orderflow"])
def test_every_migrated_read_site_records_unreadable_file(tmp_path: Path, make_extractor, fname,
                                                          name):
    """Each read site that folded a could-not-read to "" now records an unreadable-file row,
    and the row NAMES ITS OWN EXTRACTOR.

    One directory placed at each extractor's own input path makes open() raise OSError on both
    OSes; without the fix the empty read parses/scans to nothing and the file's facts vanish
    silently. This is the reader the row exists for -- a person, not the graph -- so it is
    asserted per site rather than trusted to one. The second assertion welds the row's
    `extractor` field to the emitting module's own `name`: provenance is a load-bearing
    convention here, and a row attributed to the wrong extractor is a wrong answer a later
    reader acts on.
    """
    root = tmp_path / "svc"
    root.mkdir()
    unreadable = root / fname
    unreadable.parent.mkdir(parents=True, exist_ok=True)
    unreadable.mkdir()
    ctx = ExtractContext(repos=[RepoRef(alias="svc", path=root)], run_id="r",
                         files={"svc": [unreadable]})

    res = make_extractor().extract(ctx)

    rows = [r for r in res.unresolved
            if r.get("kind") == "unreadable-file" and r["source_file"].endswith(fname)]
    assert rows, f"{fname} read failure not recorded: {res.unresolved}"
    # Pinned to the LITERAL name, not to `ext.name`: a mutant that rebinds the extractor's name
    # would move both the row and `ext.name` together and go unnoticed, so the expectation must
    # be a fixed string the rebind cannot follow.
    assert all(r["extractor"] == name for r in rows), \
        f"an unreadable-file row must name its own extractor ({name!r}), got " \
        f"{[r['extractor'] for r in rows]}"


def test_a_broad_scan_unreadable_row_says_the_scope_is_unknown(tmp_path: Path):
    """A broad-scan extractor reads many files and decides scope by CONTENT, so when it cannot
    read one it must not claim the file WAS a specification -- only that it could not look, and
    that whether the file was in scope is therefore UNKNOWN. The sentence is asserted here
    because a fixture corpus of readable files never exercises it, which is also why the volume
    of these rows measured zero: the wording could drift and no readable-corpus test would notice.
    """
    root = tmp_path / "svc"
    root.mkdir()
    unreadable = root / "api.yaml"
    unreadable.mkdir()
    ctx = ExtractContext(repos=[RepoRef(alias="svc", path=root)], run_id="r",
                         files={"svc": [unreadable]})

    rows = [r for r in openapi.OpenApiExtractor().extract(ctx).unresolved
            if r["kind"] == "unreadable-file"]

    assert len(rows) == 1, rows
    why = rows[0]["why"]
    assert "could not be read" in why, why
    assert "unknown" in why and "if it was" in why, \
        "the row must say the scope is UNKNOWN, not assert the file was a specification"


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


AGGREGATOR = """<project>
      <groupId>com.example</groupId>
      <artifactId>agg</artifactId>
      <modules><module>core</module><module>web</module></modules>
      <dependencies>
        <dependency><groupId>com.example</groupId><artifactId>core</artifactId></dependency>
      </dependencies>
    </project>"""


def test_an_aggregators_own_modules_are_not_dependencies(tmp_path: Path):
    """A build that depends on a module it also builds depends on itself, and that is not an
    edge — the shared-library traversal would return the component it started from.

    This used to be pinned with NO child manifest, which pinned the DIRECTORY NAME as the
    module's artifact name; the audit of 2026-09-09 showed that guess deleting a real
    dependency on a third-party artifact that merely happened to be spelled like one of the
    directories. So the evidence that makes `core/` the module `com.example:core` is now
    present: its own manifest, read below. The half where it is absent is the next test.
    """
    child = "<project><groupId>com.example</groupId><artifactId>core</artifactId></project>"
    res = maven.ModulesExtractor().extract(
        ctx_for(tmp_path, {"pom.xml": AGGREGATOR, "core/pom.xml": child}))
    assert edges(res, "DG_DEPENDS_ON") == []


def test_a_module_whose_manifest_cannot_be_read_keeps_the_edge_and_leaves_a_row(
        tmp_path: Path):
    """The same aggregator with no `core/pom.xml`: what that directory publishes is now
    unknown, so `com.example:core` MIGHT be the module and might be somebody else's.

    Keeping it is the cheap direction. A phantom self-dependency inside one component is
    noise the impact engine survives; a real cross-component dependency deleted for sharing
    a directory's spelling is a missed impact nobody sees. The row is what stops that from
    being a silent guess either way.
    """
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": AGGREGATOR}))
    assert [e.dst for e in edges(res, "DG_DEPENDS_ON")] == [
        ids.module_id("com.example", "core")]
    unread = [r for r in res.unresolved if r["kind"] == "module-unresolved"]
    assert [(r["module"], r["line"]) for r in unread] == [("core", 4), ("web", 4)], (
        f"both modules are unreadable and both sit on line 4: {res.unresolved}")


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
        # A blank version must SAY it is inherited (9b's spring-kafka edge) — what can
        # never happen is a blank that claims to be declared, or a self-loop.
        assert e["props"]["version"] or e["props"]["version_source"] == "inherited"
        assert e["src"] != e["dst"]
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
    act = B.activate(regs, comps, ws["root"], workspace=comps)
    claimed = act.by_extractor["helm-deployments"]
    ctx = ExtractContext(repos=B.repo_refs(comps, ws["root"], claimed), run_id="t",
                         files={c: act.files.get(("helm-deployments", c), []) for c in claimed})

    claims = [e for e in regs[0].extractor.extract(ctx).edges if e.type == "DG_DEPLOYS"]
    assert len(claims) == 2
    assert {e.confidence for e in claims} == {1.0}
    assert {e.canonical for e in claims} == {True}


def test_fixture_there_are_exactly_seven_endpoints(fixture_edges):
    """Three served endpoints and no phantoms — plus item 9a's specified-and-unserved
    one, which arrives ONLY as a spec's promise (DG_SPECIFIES) and must never grow a
    DG_EXPOSES from proximity, and item 92's two CALLED-not-served pairs."""
    seen = {e["dst"] for e in fixture_edges if e["dst"].startswith("ep:")}
    assert seen == {"ep:/orders/v1/list", "ep:/orders/v1/detail/{}",
                    "ep:/inventory/v1/reserve", "ep:/orders/v1/{}/history",
                    "ep:/billing/v1/_search", "ep:/reports/v1/list",
                    "ep:/v1/detail/{}"}
    assert {e["type"] for e in fixture_edges
            if e["dst"] == "ep:/orders/v1/{}/history"} == {"DG_SPECIFIES"}


def test_fixture_the_inert_component_still_reaches_nothing(fixture_edges):
    """P5 again, now that build manifests are read: legacy-reports shares a group with
    every other module, and a group is not a dependency. Amended at item 79: the
    control OWNS one shared-namespace entity (its cache table, a real table from a
    real migration), and only the DECLARED alias may touch it — an extractor's edge
    landing there would be a link invented from proximity."""
    inert = ids.component_service_id("legacy-reports")
    owned = {e["dst"] for e in fixture_edges
             if e["src"] == inert and "legacy-reports" not in e["dst"]}
    assert owned == {"table:order_reports_cache"}, \
        f"the control's outward surface must be exactly its own table, not {owned}"
    for e in fixture_edges:
        if e["src"] == inert or e.get("props", {}).get("declared"):
            continue
        assert e["src"] not in owned and e["dst"] not in owned, \
            f"extraction touched the inert component's entity via {e['type']}"


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


# ── item 82: a template that cannot be parsed must not read as a chart with no addresses ──

#: A config map as deployment packages actually ship them: a templating language wrapping
#: and interpolating into the document. Transcribed in shape from real charts rather than
#: invented, per the fixture doctrine — the templating is the whole point, and a sample
#: without it would test a file no chart contains.
TEMPLATED_CONFIGMAP = """\
{{- if .Values.config -}}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ .Values.name }}
data:
  ORDERS_API_HOST: {{ .Values.orders.host }}
{{- end }}
"""

TEMPLATED_WORKLOAD = TEMPLATED_CONFIGMAP.replace(
    "apiVersion: v1\nkind: ConfigMap", "apiVersion: apps/v1\nkind: Deployment")


def test_a_templated_config_map_is_reported_rather_than_silently_skipped(tmp_path: Path):
    """Item 76's headline finding, superseded by item 87 in the right direction: the
    file is now READ by de-templating, and the loss is recorded PER VALUE rather than
    per file. Every value here is a template span, so the file yields exactly its rows
    and no edges — never silence, and never an invented address."""
    res = helm.ConfigMapsExtractor().extract(
        ctx_for(tmp_path, {"templates/cm.yaml": TEMPLATED_CONFIGMAP}))
    assert [r["kind"] for r in res.unresolved] == ["templated-value"]
    assert res.unresolved[0]["line"] == 7 and res.unresolved[0]["raw"]
    assert res.edges == [], "a templated value must never become an address"
    # An extractor that reported the gap AND emitted addresses from a file it never parsed
    # would satisfy the assertion above while inventing the facts the row says are missing.
    assert res.edges == []


def test_an_unreadable_template_that_is_not_a_config_map_is_not_reported(tmp_path: Path):
    """The narrowing is deliberate and is the difference between a report and a flood: a
    template directory is mostly workloads, services and ingresses this extractor would
    never read even if they parsed perfectly. Announcing that it failed to read files it
    does not read would bury the handful that cost real edges."""
    res = helm.ConfigMapsExtractor().extract(
        ctx_for(tmp_path, {"templates/deploy.yaml": TEMPLATED_WORKLOAD}))
    assert res.unresolved == []
    assert res.edges == []


def test_the_multi_document_loader_tells_unreadable_from_empty(tmp_path: Path):
    """The core half, and the reason fixing only the extractor would have been worthless:
    every other YAML reader in the tree calls this and inherits whichever answer it gives."""
    from tools.graph.core.lib import props as _props
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    broken = tmp_path / "broken.yaml"
    broken.write_text(TEMPLATED_CONFIGMAP, encoding="utf-8")

    assert _props.load_yaml_all_reporting(empty) == ([], "")
    docs, error = _props.load_yaml_all_reporting(broken)
    assert docs == [] and error, "unreadable must be distinguishable from having no content"

    # And the plain spelling still cannot tell them apart, which is why it is not the one a
    # reporting caller may use. Pinned rather than assumed: this equality IS the defect, and
    # a future refactor that quietly fixed it here would leave the docstring lying.
    assert _props.load_yaml_all(empty) == _props.load_yaml_all(broken) == []


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


def test_a_claim_naming_values_yaml_covers_the_chart_it_belongs_to(tmp_path: Path):
    """The semantics this item had to decide rather than assume. A chart declares its service
    in values.yaml while publishing its host map from templates/, so a claim naming the
    values file has to reach the chart's templates or it reaches nothing at all. T529 moved
    that reach from the file to the PACKAGE: the helm reader asks whether the claim names the
    chart a template belongs to, and a claim no longer reaches past the file it names."""
    from tools.graph.extractors import _canonical
    c = CLAIM[0]

    def designates(rel: str) -> bool:
        return _canonical.covers_package(c, "svc", rel, helm.chart_roots(rel),
                                         helm.claimed_chart)

    assert _canonical.covers(c, "svc", "charts/orders/values.yaml")
    assert designates("charts/orders/templates/cm.yaml")
    assert not designates("legacy/orders/templates/cm.yaml")
    # T529: a chart nested below the named file's directory is another package.
    assert not designates("charts/orders/v1/templates/cm.yaml")


@pytest.mark.parametrize("claimed, off_case", [
    ("charts/orders/Chart.yaml", False),
    ("charts/orders/values.yaml", False),
    ("charts/orders/values-prod.yaml", False),
    ("charts/orders", False),                      # a directory claims by no file name
    ("charts/orders/chart.yaml", True),
    ("charts/orders/VALUES.yaml", True),
    ("charts/orders/Templates/cm.yaml", True),
])
def test_a_claim_naming_a_chart_file_only_by_folding_case_is_named(
        tmp_path: Path, claimed: str, off_case: bool):
    """T576 (the owner's ruling A, 2026-10-02). `claimed_chart` matches a chart's file
    names case-sensitively, as the reader has read them since T531, so a claim naming
    `chart.yaml` - perhaps as it is spelled on disk - designated no chart, in silence. The
    match stays case-sensitive, and such a claim is named in a row; an exact one is not."""
    ctx = ctx_charts(tmp_path, {"charts/orders/templates/cm.yaml": CM % "orders-v2:8080"},
                     [{"service": "orders", "owner": "svc", "path": claimed}])
    res = helm.ConfigMapsExtractor().extract(ctx)
    rows = [r for r in res.unresolved if r["kind"] == "off-case-claim"]
    assert [r["claim"] for r in rows] == ([claimed] if off_case else []), rows


@pytest.mark.parametrize("owner, charted, want", [
    ("a", ("a",), ["a"]),
    ("", ("a",), ["a"]),
    ("", ("a", "c"), ["a", "c"]),
    ("b", ("a",), []),
], ids=["owned", "ownerless", "ownerless-two-charted", "owned-by-a-chartless-one"])
def test_an_off_case_claim_is_named_once_by_each_component_whose_charts_it_misses(
        tmp_path: Path, owner: str, charted: tuple, want: list):
    """Review B14-6, as B14-13 corrected it. Two `[[canonical]]` blocks naming one path -
    for two services, a shape the workspace loader accepts - made two rows in a real build;
    the row is made once per claimed path. The pipeline calls this extractor with one
    claimed component, which has inputs (T534), and its loader refuses a block with no
    owner, so the other cells are hand-built contexts: `claims_for` honours an ownerless
    claim, and there the row is made only by a component the reader has charts in."""
    files = {}
    for alias in charted:
        cm = tmp_path / alias / "charts" / "orders" / "templates" / "cm.yaml"
        cm.parent.mkdir(parents=True)
        cm.write_text(CM % "orders-v2:8080", encoding="utf-8")
        files[alias] = [cm]
    repos = [RepoRef(alias=a, path=tmp_path / a) for a in ("a", "b", "c")]
    ctx = ExtractContext(repos=repos, run_id="r", files=files)
    ctx.config = {"canonical": [
        {"service": service, "path": "charts/orders/chart.yaml",
         **({"owner": owner} if owner else {})} for service in ("orders", "billing")]}
    res = helm.ConfigMapsExtractor().extract(ctx)
    rows = [r for r in res.unresolved if r["kind"] == "off-case-claim"]
    assert [r["component"] for r in rows] == want, rows
    assert all(r["claim"] == "charts/orders/chart.yaml" for r in rows)


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
    rel = "anything/at/all.yaml"
    assert _canonical.owns_package({}, "svc", rel, helm.chart_roots(rel), helm.claimed_chart)


def test_a_nested_chart_below_the_claimed_values_file_is_not_the_owner(tmp_path: Path):
    """T529. A claim covered everything under its file's PARENT, so a superseded chart kept
    inside the live one's directory was canonical too - and, sorting after it, won the host
    map in silence. It is another package, so its value is the loser, and said."""
    ctx = ctx_charts(tmp_path, {
        "charts/orders/templates/cm.yaml": CM % "orders-v2:8080",
        "charts/orders/v1/templates/cm.yaml": CM % "orders-v1:8080"}, CLAIM)
    res = helm.ConfigMapsExtractor().extract(ctx)
    assert ctx.cache[helm.CACHE_KEY]["svc"]["orders.host"]["value"] == "orders-v2:8080"
    rows = [r for r in res.unresolved if r["kind"] == "superseded-declaration"]
    assert [r["value"] for r in rows] == ["orders-v1:8080"]


def test_a_directory_claim_does_not_cover_its_siblings(tmp_path: Path):
    """T529, the orderflow shape. `charts/orderflow` names a directory; its parent rule made
    it cover `charts/orders-api/` too - any chart beside it."""
    from tools.graph.extractors import _canonical
    c = {"service": "orders-api", "owner": "svc", "path": "charts/orderflow"}
    assert _canonical.covers(c, "svc", "charts/orderflow/values.yaml")
    assert not _canonical.covers(c, "svc", "charts/orders-api/values.yaml")


def _owner_and_legacy(tmp_path: Path, owner_rel: str, path: str):
    """The owner's chart publishing v2 and a superseded tree sorting after it publishing v1,
    with one claim naming `path`: which value the host map holds, and the rows."""
    ctx = ctx_charts(tmp_path, {
        owner_rel: CM % "orders-v2:8080",
        "legacy/orders/templates/cm.yaml": CM % "orders-v1:8080"},
        [{"service": "orders", "owner": "svc", "path": path}])
    res = helm.ConfigMapsExtractor().extract(ctx)
    held = ctx.cache[helm.CACHE_KEY]["svc"]["orders.host"]["value"]
    return held, [r["value"] for r in res.unresolved if r["kind"] == "superseded-declaration"]


def test_a_claim_on_an_umbrella_chart_covers_its_subcharts(tmp_path: Path):
    """B8-1. One release deploys an umbrella chart and every chart vendored under its
    `charts/`, so a claim naming the umbrella's `values.yaml` is a claim on its subcharts.
    Asked of the nearest chart alone, the subchart was nobody's, and the superseded tree
    sorting after it supplied its live address - with no row."""
    # Batch 15b (D15b-43): the umbrella's own `Chart.yaml` is in the fixture - a listed pin
    # change. A directory counts as an umbrella over its `charts/` only when it is itself a
    # package, so the fixture as it stood (no umbrella files) is now a `canonical-unmatched`
    # cell, and this pin keeps asserting what it was written for.
    assert _owner_and_legacy_with(
        tmp_path, "charts/app/charts/orders/templates/cm.yaml", "charts/app/values.yaml",
        {"charts/app/Chart.yaml": CHART_META},
    ) == ("orders-v2:8080", ["orders-v1:8080"])


@pytest.mark.parametrize("path", ["charts/orders/templates/cm.yaml",
                                  "charts/orders/values-prod.yaml"])
def test_a_claim_naming_one_of_a_charts_own_files_names_the_chart(tmp_path: Path, path: str):
    """B8-2. A claim may name a chart by any of its own files - one of its templates, or the
    values overlay beside `values.yaml` - and both did before T529. Narrowed to the entry
    files alone, each designated nothing, and the superseded tree won in silence."""
    assert _owner_and_legacy(tmp_path, "charts/orders/templates/cm.yaml", path) == (
        "orders-v2:8080", ["orders-v1:8080"])


def test_a_claim_naming_a_file_beside_the_charts_names_no_chart():
    """The edge of the umbrella rule. A chart under a top-level `charts/` has the
    component's root as its umbrella, so a root file that is one of a chart's own - a
    `values.yaml` - reaches it, and a root file that is not - a compose file - names no
    chart at all. Reaching everything under the root was the parent rule T529 removed."""
    from tools.graph.extractors import _canonical
    rel = "charts/orders/templates/cm.yaml"
    assert helm.chart_roots(rel) == ["charts/orders", ""]

    def designates(path: str) -> bool:
        return _canonical.covers_package({"service": "orders", "owner": "svc", "path": path},
                                         "svc", rel, helm.chart_roots(rel), helm.claimed_chart)

    assert designates("values.yaml")
    assert not designates("docker-compose.yml")
    assert not designates("charts/payments/values.yaml")


@pytest.mark.parametrize("path", ["charts\\orders", "./charts/orders", "charts/orders/",
                                  "charts//orders", ".\\charts\\orders\\values.yaml"])
def test_a_claim_path_spelled_another_way_is_the_same_path(tmp_path: Path, path: str):
    """T575. Windows is a first-class target, and a claim written with its separator, or
    with a leading `./`, was compared as written: it designated nothing, and the superseded
    tree won in silence."""
    assert _owner_and_legacy(tmp_path, "charts/orders/templates/cm.yaml", path) == (
        "orders-v2:8080", ["orders-v1:8080"])


@pytest.mark.parametrize("path", [".", "/", "./"])
def test_a_claim_on_the_components_root_covers_all_of_it(path: str):
    """T575: `.` names the component's root, as `/` already did. It matched nothing."""
    from tools.graph.extractors import _canonical
    assert _canonical.covers({"service": "orders", "owner": "svc", "path": path}, "svc",
                             "charts/orders/values.yaml")


def test_a_values_file_holding_only_comments_is_a_chart_taking_its_defaults(tmp_path: Path):
    """T537: YAML reads a comment-only file as nothing, and `doc is None` was taken for a
    parse failure - an `unparsable` row with an empty error, and the chart's `deploy` node
    lost. A chart that takes every default is a real package that names no service, and
    `unnamed-deployment` is its honest row."""
    res = helm.DeploymentsExtractor().extract(ctx_for(tmp_path, {
        "charts/x/values.yaml": "# every value is the chart's default\n"}))
    kinds = [r["kind"] for r in res.unresolved]
    assert "unparsable" not in kinds and kinds == ["unnamed-deployment"], res.unresolved
    assert [n.type for n in res.nodes] == ["deploy"]


# ── batch 15b: what the helm readers publish - kind, ships_in, packages, losses ──

CHART_META = "apiVersion: v2\nname: web\nversion: 0.1.0\n"
WORKLOAD = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n"
UNNAMED_CHART = ("this chart has no `values.yaml`, so nothing the graph reads names the "
                 "service its templates deploy; add one naming it (`fullnameOverride: "
                 "<name>`)")


def deploy_node(res, rel: str, alias: str = "svc"):
    found = [n for n in res.nodes if n.id == ids.deployment_id(alias, rel)]
    assert len(found) == 1, (rel, [n.id for n in res.nodes])
    return found[0]


def deployments(files: dict[str, str], tmp_path: Path, config: dict | None = None):
    ctx = ctx_for(tmp_path, files)
    ctx.config = config or {}
    return helm.DeploymentsExtractor().extract(ctx)


def test_a_values_files_node_carries_its_readers_facts(tmp_path: Path):
    """`kind` and `ships_in` - the chart roots the file ships in, its own first - are facts
    about the reader's own files, config-free and safe in a shard; the link pass reads a
    block by them (`_canonical.designates`)."""
    res = deployments({"charts/web/values.yaml": "fullnameOverride: web\n"}, tmp_path)
    node = deploy_node(res, "charts/web/values.yaml")
    assert (node.props["kind"], node.props["ships_in"]) == ("helm", ["charts/web"])
    assert "unread" not in node.props


def test_a_subchart_ships_in_its_umbrella_only_where_the_umbrella_is_a_package(tmp_path: Path):
    sub = {"charts/umb/charts/web/values.yaml": "fullnameOverride: web\n"}
    lone = deploy_node(deployments(sub, tmp_path / "1"), "charts/umb/charts/web/values.yaml")
    assert lone.props["ships_in"] == ["charts/umb/charts/web"]
    vendored = deploy_node(deployments({**sub, "charts/umb/Chart.yaml": CHART_META},
                                       tmp_path / "2"), "charts/umb/charts/web/values.yaml")
    assert vendored.props["ships_in"] == ["charts/umb/charts/web", "charts/umb"]


def test_a_host_map_file_carries_its_chart_roots(tmp_path: Path):
    res = helm.ConfigMapsExtractor().extract(
        ctx_charts(tmp_path, {"charts/web/templates/cm.yaml": CM % "web:80"}, []))
    (cfg,) = [n for n in res.nodes if n.type == "cfg"]
    assert cfg.props["ships_in"] == ["charts/web"]


def test_package_dirs_names_a_chart_or_a_values_file_naming_a_service(tmp_path: Path):
    """D15b-35: both kinds count - a root values file naming a service is a deployment the
    reader reads - and a values file naming nothing, or not parsing, makes no package."""
    ctx = ctx_for(tmp_path, {"a/Chart.yaml": CHART_META,
                             "b/values.yaml": "fullnameOverride: b\n",
                             "c/values.yaml": "replicaCount: 2\n",
                             "d/values.yaml": "fullnameOverride: [\n",
                             "e/templates/cm.yaml": CM % "x",
                             "values.yaml": "fullnameOverride: root\n"})
    repo = ctx.repos[0]
    assert helm.package_dirs(repo, ctx.inputs(repo)) == frozenset({"a", "b", ""})


def test_the_umbrella_walk_stops_at_the_first_non_package():
    rel = "charts/umb/charts/web/templates/cm.yaml"
    assert helm.chart_roots(rel) == ["charts/umb/charts/web", "charts/umb", ""], "main's walk"
    assert helm.chart_roots(rel, {"charts/umb", ""}) == ["charts/umb/charts/web",
                                                         "charts/umb", ""]
    assert helm.chart_roots(rel, {"charts/umb"}) == ["charts/umb/charts/web", "charts/umb"]
    # Three levels with no package in the middle: the walk stops, never skips a level.
    assert helm.chart_roots(rel, {""}) == ["charts/umb/charts/web"]


def _root_claim(tmp_path: Path, extra: dict[str, str]):
    """The owner's chart under a top-level `charts/`, a superseded tree beside it, and a
    claim on the root `values.yaml` - which reaches `charts/` only where the root is
    itself a package (D15b-12, D15b-35)."""
    return _owner_and_legacy_with(tmp_path, "charts/orders/templates/cm.yaml",
                                  "values.yaml", extra)


def _owner_and_legacy_with(tmp_path: Path, owner_rel: str, path: str, extra: dict[str, str]):
    ctx = ctx_charts(tmp_path, {owner_rel: CM % "orders-v2:8080",
                                "legacy/orders/templates/cm.yaml": CM % "orders-v1:8080",
                                **extra},
                     [{"service": "orders", "owner": "svc", "path": path}])
    res = helm.ConfigMapsExtractor().extract(ctx)
    held = ctx.cache[helm.CACHE_KEY]["svc"]["orders.host"]["value"]
    return held, [r["value"] for r in res.unresolved if r["kind"] == "superseded-declaration"]


def test_a_root_values_file_naming_a_service_ranks_the_charts_below(tmp_path: Path):
    assert _root_claim(tmp_path, {"values.yaml": "fullnameOverride: orders\n"}) == (
        "orders-v2:8080", ["orders-v1:8080"])


def test_a_root_chart_yaml_is_an_umbrella_over_the_charts_below(tmp_path: Path):
    assert _root_claim(tmp_path, {"Chart.yaml": CHART_META}) == (
        "orders-v2:8080", ["orders-v1:8080"])


def test_a_root_values_file_naming_nothing_ranks_nothing(tmp_path: Path):
    """The root is no package, so a claim naming its values file stops reaching `charts/`
    - and `canonical-unmatched` names the block (the link pass's half)."""
    held, superseded = _root_claim(tmp_path, {"values.yaml": "replicaCount: 2\n"})
    assert superseded == [], "nothing was ranked, so nothing was superseded"


# ── ruling 20: a chart with only a Chart.yaml ────────────────────────────────


def test_a_chart_yaml_only_chart_with_a_template_is_an_unread_deployment(tmp_path: Path):
    """Ruling 20 (the owner: "a permanent solution"): the chart deploys something nothing
    the graph reads names - an `unread` node and its row, where `main` was silent."""
    res = deployments({"charts/web/Chart.yaml": CHART_META,
                       "charts/web/templates/deploy.yaml": WORKLOAD}, tmp_path)
    node = deploy_node(res, "charts/web/Chart.yaml")
    assert (node.props["unread"], node.props["kind"], node.props["ships_in"]) == (
        "unnamed-deployment", "helm", ["charts/web"])
    (row,) = [r for r in res.unresolved if r["kind"] == "unnamed-deployment"]
    assert (row["source_file"], row["why"]) == ("charts/web/Chart.yaml", UNNAMED_CHART)


@pytest.mark.parametrize("files", [
    {"charts/web/Chart.yaml": CHART_META + "type: library\n",
     "charts/web/templates/_helpers.yaml": WORKLOAD},
    {"charts/web/Chart.yaml": CHART_META},
    {"charts/web/Chart.yaml": CHART_META, "charts/web/templates/NOTES.txt": "hi\n"},
    {"charts/umb/Chart.yaml": CHART_META,
     "charts/umb/charts/web/values.yaml": "fullnameOverride: web\n",
     "charts/umb/charts/web/templates/deploy.yaml": WORKLOAD},
], ids=["library", "no-templates", "notes-only", "umbrella-of-subcharts"])
def test_a_chart_that_deploys_nothing_of_its_own_stays_skipped(tmp_path: Path, files):
    res = deployments(files, tmp_path)
    chart_yaml = next(rel for rel in files if rel.endswith("Chart.yaml"))
    assert not [n for n in res.nodes if n.id == ids.deployment_id("svc", chart_yaml)]
    assert not [r for r in res.unresolved if r["source_file"] == chart_yaml]


@pytest.mark.parametrize("values", ["global:\n  x: 1\n", "fullnameOverride: web\n"],
                         ids=["naming-nothing", "naming-a-service"])
def test_a_library_charts_values_file_is_no_deployment_package(tmp_path: Path, values):
    """Review B15b-13: helm never installs a library chart, so its values file deploys
    nothing - no node, no claim, no row, as ruling 20 already treats a library chart with no
    values file. Its `Chart.yaml` is one of this reader's inputs, so its digest covers it."""
    res = deployments({"charts/lib/Chart.yaml": CHART_META + "type: library\n",
                       "charts/lib/values.yaml": values}, tmp_path)
    assert not [n for n in res.nodes if n.type == "deploy"]
    assert not res.edges and not res.unresolved


@pytest.mark.parametrize("values, kind", [("fullnameOverride: [\n", "unparsable")],
                         ids=["unparsable"])
def test_a_library_charts_values_file_that_cannot_be_read_keeps_its_row(tmp_path: Path,
                                                                        values, kind):
    """Review B15b-17: the library rule withholds a claim, never a read. A values file that
    does not parse is lost, whatever its chart: its row and its `unread` node stay, as on
    `main` and as decision 6 gives every deployment file the graph could not read."""
    res = deployments({"charts/lib/Chart.yaml": CHART_META + "type: library\n",
                       "charts/lib/values.yaml": values}, tmp_path)
    assert [r["kind"] for r in res.unresolved] == [kind]
    assert deploy_node(res, "charts/lib/values.yaml").props["unread"] == kind
    assert not res.edges


def test_a_library_charts_values_file_that_cannot_be_opened_keeps_its_row(tmp_path: Path):
    """Review B15b-24: the read half of B15b-17's rule. A directory where the values file
    should be cannot be read, and in a library chart too it keeps its `unreadable-file` row
    and its `unread` node."""
    root = tmp_path / "u" / "svc"
    (root / "charts" / "lib" / "values.yaml").mkdir(parents=True)
    (root / "charts" / "lib" / "Chart.yaml").write_text(CHART_META + "type: library\n",
                                                        encoding="utf-8")
    ctx = ExtractContext(repos=[RepoRef(alias="svc", path=root)], run_id="r",
                         files={"svc": [root / "charts" / "lib" / "Chart.yaml",
                                        root / "charts" / "lib" / "values.yaml"]})
    res = helm.DeploymentsExtractor().extract(ctx)
    assert [r["kind"] for r in res.unresolved] == ["unreadable-file"]
    assert deploy_node(res, "charts/lib/values.yaml").props["unread"] == "unreadable-file"
    assert not res.edges


def test_a_values_file_beside_a_chart_yaml_that_does_not_parse_claims_nothing(
        tmp_path: Path):
    """B15b-13's control, reversed by T624: helm's loader refuses a `Chart.yaml` it cannot
    parse ("cannot load Chart.yaml"), so the chart deploys nothing - not a guess about its
    type - and its values file claims nothing, a `chart-refused` row quoting the parser."""
    res = deployments({"charts/lib/Chart.yaml": "apiVersion: [\n",
                       "charts/lib/values.yaml": "fullnameOverride: web\n"}, tmp_path)
    assert res.edges == []
    (row,) = [r for r in res.unresolved if r["kind"] == "chart-refused"]
    assert row["error"].startswith("cannot load Chart.yaml: ")


def test_the_chart_file_names_are_one_fact():
    """Review B15b-15: the helm reader's globs for a chart's values and metadata files ARE
    `_canonical`'s names, which its directory listing and the accounting rows read - one
    fact, so the three cannot drift."""
    from tools.graph.extractors import _canonical
    assert helm.VALUES_GLOBS is _canonical.VALUES_FILES
    assert helm.CHART_GLOBS is _canonical.CHART_FILES


def test_a_chart_yaml_that_does_not_parse_is_unparsable(tmp_path: Path):
    res = deployments({"charts/web/Chart.yaml": "apiVersion: [\n",
                       "charts/web/templates/deploy.yaml": WORKLOAD}, tmp_path)
    assert deploy_node(res, "charts/web/Chart.yaml").props["unread"] == "unparsable"
    assert [r["kind"] for r in res.unresolved
            if r["source_file"] == "charts/web/Chart.yaml"] == ["unparsable"]


def test_an_overlay_is_no_values_file(tmp_path: Path):
    """The reader reads `values.yaml`/`values.yml` alone; `values-prod.yaml` names nothing
    it reads, so the chart is reported - and the row says "no `values.yaml`"."""
    res = deployments({"charts/web/Chart.yaml": CHART_META,
                       "charts/web/values-prod.yaml": "fullnameOverride: web\n",
                       "charts/web/templates/deploy.yaml": WORKLOAD}, tmp_path)
    (row,) = [r for r in res.unresolved if r["kind"] == "unnamed-deployment"]
    assert "has no `values.yaml`" in row["why"]


def test_a_nested_template_is_the_charts_own(tmp_path: Path):
    """D15b-118: `templates/sub/cm.yaml` is the chart's by `chart_root`; a subchart's
    template is not."""
    res = deployments({"charts/web/Chart.yaml": CHART_META,
                       "charts/web/templates/sub/deploy.yaml": WORKLOAD}, tmp_path)
    assert deploy_node(res, "charts/web/Chart.yaml").props["unread"] == "unnamed-deployment"


def test_a_directory_named_values_yaml_is_no_values_file(tmp_path: Path):
    """D15b-126: the case-folded listing counts regular files only."""
    root = tmp_path / "svc"
    (root / "charts" / "web" / "values.yaml").mkdir(parents=True)
    res = deployments({"charts/web/Chart.yaml": CHART_META,
                       "charts/web/templates/deploy.yaml": WORKLOAD}, tmp_path)
    assert deploy_node(res, "charts/web/Chart.yaml").props["unread"] == "unnamed-deployment"


def test_a_mis_cased_values_file_carries_the_chart(tmp_path: Path):
    """D15b-100, D15b-107: a values file in another spelling means the chart is not
    `Chart.yaml`-only - its rename is the remedy (the case row's), never "add one", which on
    Windows would write INTO the mis-cased file. Found by a case-folded listing, never a
    path probe."""
    res = deployments({"charts/web/Chart.yaml": CHART_META,
                       "charts/web/Values.yaml": "fullnameOverride: web\n",
                       "charts/web/templates/deploy.yaml": WORKLOAD}, tmp_path)
    assert not [n for n in res.nodes if n.id == ids.deployment_id("svc", "charts/web/Chart.yaml")]
    assert not [r for r in res.unresolved if r["kind"] == "unnamed-deployment"]


def test_a_template_lost_to_case_still_counts_as_the_charts_own(tmp_path: Path):
    """D15b-120, D15b-131: the question reads the case record as well as the inputs, and
    for a case-record entry ONLY matches the `templates/` segment case-folded - asked
    case-sensitively, `chart_root("charts/web/TEMPLATES/cm.yaml")` answers
    `charts/web/TEMPLATES`, and the chart was silently gone."""
    lost = {"deployment_off_case": {"svc": ["charts/web/TEMPLATES/deploy.yaml"]}}
    files = {"charts/web/Chart.yaml": CHART_META}
    (tmp_path / "svc" / "charts" / "web" / "TEMPLATES").mkdir(parents=True)
    (tmp_path / "svc" / "charts" / "web" / "TEMPLATES" / "deploy.yaml").write_text(
        WORKLOAD, encoding="utf-8")
    res = deployments(files, tmp_path, lost)
    assert deploy_node(res, "charts/web/Chart.yaml").props["unread"] == "unnamed-deployment"


def test_a_values_file_under_templates_is_a_claim_and_a_template(tmp_path: Path):
    """D15b-110: a file's role is decided by which of the reader's patterns match it, never
    by where it sits - `templates/values.yaml` is a claim (a values pattern) and a template
    of its chart (a template pattern), as on `main` for the claim."""
    res = deployments({"charts/web/values.yaml": "fullnameOverride: web\n",
                       "charts/web/templates/values.yaml": "fullnameOverride: inner\n"},
                      tmp_path)
    assert {deploy_node(res, rel).props["service"] for rel in (
        "charts/web/values.yaml", "charts/web/templates/values.yaml")} == {"web", "inner"}


def test_an_unreadable_or_unparsable_values_file_is_an_unread_node(tmp_path: Path):
    res = deployments({"charts/web/values.yaml": "fullnameOverride: [\n"}, tmp_path)
    assert deploy_node(res, "charts/web/values.yaml").props["unread"] == "unparsable"
    root = tmp_path / "u" / "svc"
    (root / "charts" / "web" / "values.yaml").mkdir(parents=True)
    ctx = ExtractContext(repos=[RepoRef(alias="svc", path=root)], run_id="r",
                         files={"svc": [root / "charts" / "web" / "values.yaml"]})
    res = helm.DeploymentsExtractor().extract(ctx)
    assert deploy_node(res, "charts/web/values.yaml").props["unread"] == "unreadable-file"


def test_a_values_file_naming_no_service_is_marked_unread(tmp_path: Path):
    """The node `main` already publishes for it now says why it carries no claim - and is
    left out of the orphan metric (ruling 21)."""
    res = deployments({"charts/web/values.yaml": "replicaCount: 2\n"}, tmp_path)
    assert deploy_node(res, "charts/web/values.yaml").props["unread"] == "unnamed-deployment"


def test_with_no_parser_the_deployment_reader_counts_only_its_claim_files(tmp_path: Path,
                                                                          monkeypatch):
    """D15b-117, D15b-127: the deployment reader reads templates only to count them; its
    `grammar-unavailable` row counts its claim files alone, and is withheld where it holds
    none - a web page's YAML template is no lost deployment."""
    from tools.graph.core.lib import props
    monkeypatch.setattr(props, "yaml_available", lambda: False)
    res = deployments({"src/main/resources/templates/page.yaml": "title: x\n"}, tmp_path / "1")
    assert res.unresolved == []
    res = deployments({"charts/web/values.yaml": "fullnameOverride: web\n",
                       "charts/web/templates/deploy.yaml": WORKLOAD}, tmp_path / "2")
    (row,) = res.unresolved
    assert (row["kind"], row["files"]) == ("grammar-unavailable", 1)
    assert "Every deployment and address they declare is missing" in row["why"]


def test_with_no_parser_the_host_map_reader_says_what_it_lost(tmp_path: Path, monkeypatch):
    from tools.graph.core.lib import props
    monkeypatch.setattr(props, "yaml_available", lambda: False)
    res = helm.ConfigMapsExtractor().extract(ctx_charts(tmp_path, {
        "charts/web/values.yaml": "fullnameOverride: web\n",
        "charts/web/templates/cm.yaml": CM % "x"}, []))
    (row,) = res.unresolved
    assert row["files"] == 2
    assert "Every address they publish, and which charts they make, are missing" in row["why"]


def test_a_values_file_is_never_read_as_a_host_map(tmp_path: Path):
    """The host-map reader now reads values and `Chart` files - only to know which
    directories are packages (decision 4). A file's role is the pattern it matches
    (D15b-110), so a values file holding a ConfigMap document publishes no address, as on
    `main`, where the reader never saw it."""
    res = helm.ConfigMapsExtractor().extract(ctx_charts(tmp_path, {
        "charts/web/values.yaml": CM % "web:80"}, []))
    assert not [n for n in res.nodes if n.type == "cfg"]
